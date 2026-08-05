from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, close_old_connections, connection
from django.db.models.deletion import ProtectedError
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from pacientes.models import Paciente
from turnos.forms import (
    ConfirmacionTurnoForm,
    SolicitudTurnoPublicaForm,
    TurnoForm,
    TurnoReprogramacionAccesoPublicoForm,
)
from turnos.integrations.google_calendar import construir_evento_desde_turno
from turnos.models import (
    AccionPublicaTurno,
    ConfiguracionAgendaInteligente,
    DisponibilidadOdontologo,
    ExcepcionAgenda,
    Odontologo,
    TipoTurno,
    TipoTurnoOdontologo,
    Turno,
)
from turnos.notifications import _renderizar_email_turno
from turnos.public_access.services import reprogramar_turno_publico_seguro
from turnos.services import cancelar_turno, reprogramar_turno
from turnos.smart_scheduling import (
    IntervaloLibre,
    calcular_horarios_inteligentes,
    evaluar_aprovechamiento_hueco,
    generar_resultado_horarios_inteligentes,
)
from turnos.smart_scheduling_cache import obtener_horarios_inteligentes_cacheados
from turnos.solicitudes_publicas.services import crear_solicitud_publica_de_turno


def crear_usuario_odontologo(sufijo, *, activo=True):
    usuario = get_user_model().objects.create_user(
        username=f"odontologo.smart.{sufijo}",
        password="clave-pruebas",
        first_name="Paula",
        last_name=f"Smart {sufijo}",
        email=f"odontologo.{sufijo}@example.test",
    )
    odontologo = Odontologo.objects.create(
        usuario=usuario,
        matricula=f"SMART-{sufijo}",
        activo=activo,
    )
    return usuario, odontologo


def crear_paciente(sufijo):
    return Paciente.objects.create(
        nombre="Paciente",
        apellido=f"Smart {sufijo}",
        documento=f"77{sufijo:06d}",
        telefono="2604000000",
        email=f"paciente.{sufijo}@example.test",
    )


def crear_tipo(nombre="Control", slug="control-smart"):
    return TipoTurno.objects.create(
        nombre=nombre,
        slug=slug,
        descripcion_publica="Servicio programado.",
        icono=TipoTurno.Icono.CONTROL,
        activo=True,
        visible_publicamente=True,
    )


class AgendaInteligenteMotorPuroTests(SimpleTestCase):
    fecha = datetime(2026, 7, 20)

    def configuracion(self, **cambios):
        valores = {
            "odontologo_id": 1,
            "intervalo_inicio_minutos": 15,
            "hueco_minimo_util_minutos": 30,
            "cantidad_horarios_recomendados": 4,
            "cantidad_horarios_alternativos": 20,
            "preservar_bloques_largos": True,
            "bloque_largo_minutos": 90,
            "modo_compactacion": ConfiguracionAgendaInteligente.ModoCompactacion.EQUILIBRADO,
        }
        valores.update(cambios)
        return ConfiguracionAgendaInteligente(**valores)

    def generar(self, inicio, fin, duracion, *, margen=0, configuracion=None):
        return generar_resultado_horarios_inteligentes(
            intervalos_libres=(
                IntervaloLibre(
                    self.fecha.replace(hour=inicio[0], minute=inicio[1]),
                    self.fecha.replace(hour=fin[0], minute=fin[1]),
                    limitado_por_ocupacion_inicio=True,
                    limitado_por_ocupacion_fin=True,
                ),
            ),
            duracion_atencion_minutos=duracion,
            margen_posterior_minutos=margen,
            configuracion=configuracion or self.configuracion(),
            duraciones_publicas=(20, 30, 45, 60, 90),
        )

    def test_escenario_a_prioriza_extremos_y_descarta_fragmentos_de_15(self):
        resultado = self.generar((10, 0), (12, 0), 45)
        recomendados = {item.hora_inicio for item in resultado.recomendados}
        todos = {item.hora_inicio for item in resultado.todos}

        self.assertIn(time(10, 0), recomendados)
        self.assertIn(time(11, 15), recomendados)
        self.assertNotIn(time(10, 15), todos)
        self.assertIn(time(10, 30), todos)
        self.assertGreater(resultado.descartados_por_fragmentacion, 0)

    def test_escenario_b_hueco_exacto_es_el_maximo(self):
        resultado = self.generar((14, 45), (15, 15), 30)
        self.assertEqual(len(resultado.todos), 1)
        self.assertEqual(resultado.recomendados[0].hora_inicio, time(14, 45))
        self.assertIn("ocupa_intervalo_exacto", resultado.recomendados[0].razones_tecnicas)

    def test_escenario_c_preserva_bloque_largo(self):
        resultado = self.generar((9, 0), (11, 0), 30)
        puntajes = {item.hora_inicio: item.puntaje for item in resultado.todos}
        self.assertGreater(puntajes[time(9, 0)], puntajes[time(9, 45)])
        self.assertGreater(puntajes[time(10, 30)], puntajes[time(9, 45)])

    def test_escenario_d_diversifica_manana_y_tarde(self):
        resultado = generar_resultado_horarios_inteligentes(
            intervalos_libres=(
                IntervaloLibre(
                    self.fecha.replace(hour=9),
                    self.fecha.replace(hour=13),
                ),
                IntervaloLibre(
                    self.fecha.replace(hour=14),
                    self.fecha.replace(hour=19),
                ),
            ),
            duracion_atencion_minutos=30,
            margen_posterior_minutos=0,
            configuracion=self.configuracion(cantidad_horarios_recomendados=5),
            duraciones_publicas=(30, 45, 60),
        )
        horas = [item.hora_inicio for item in resultado.recomendados]
        self.assertTrue(any(hora.hour < 13 for hora in horas))
        self.assertTrue(any(hora.hour >= 13 for hora in horas))
        self.assertTrue(
            any(
                abs((primero.hour * 60 + primero.minute) - (segundo.hour * 60 + segundo.minute))
                >= 60
                for indice, primero in enumerate(horas)
                for segundo in horas[indice + 1 :]
            )
        )

    def test_margen_posterior_forma_parte_del_bloque(self):
        resultado = self.generar((9, 0), (10, 0), 45, margen=15)
        candidato = resultado.recomendados[0]
        self.assertEqual(candidato.hora_fin_atencion, time(9, 45))
        self.assertEqual(candidato.hora_fin_bloqueada, time(10, 0))

    def test_modos_inicio_y_final_agregan_bonificacion_correcta(self):
        inicio = self.generar(
            (9, 0),
            (11, 0),
            30,
            configuracion=self.configuracion(modo_compactacion="inicio"),
        )
        final = self.generar(
            (9, 0),
            (11, 0),
            30,
            configuracion=self.configuracion(modo_compactacion="final"),
        )
        puntajes_inicio = {item.hora_inicio: item.puntaje for item in inicio.todos}
        puntajes_final = {item.hora_inicio: item.puntaje for item in final.todos}
        self.assertGreater(puntajes_inicio[time(9, 0)], puntajes_final[time(9, 0)])
        self.assertGreater(puntajes_final[time(10, 30)], puntajes_inicio[time(10, 30)])

    def test_duraciones_y_grilla_generan_resultados_deterministas(self):
        for duracion in (20, 30, 45, 60, 90):
            with self.subTest(duracion=duracion):
                primero = self.generar((9, 0), (13, 0), duracion)
                segundo = self.generar((9, 0), (13, 0), duracion)
                self.assertEqual(primero, segundo)
                self.assertTrue(all(item.hora_inicio.minute % 15 == 0 for item in primero.todos))

    def test_evaluacion_de_huecos_usa_duraciones_reales(self):
        self.assertEqual(
            evaluar_aprovechamiento_hueco(45, (30, 45, 60), 30),
            "exacto_para_servicio",
        )
        self.assertEqual(
            evaluar_aprovechamiento_hueco(50, (30, 45, 60), 30),
            "admite_servicio",
        )
        self.assertEqual(evaluar_aprovechamiento_hueco(15, (30, 45), 30), "inutil")


@override_settings(TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED=True)
class AgendaInteligenteModelosYFlujoTests(TestCase):
    def setUp(self):
        self.usuario, self.odontologo = crear_usuario_odontologo("principal")
        self.otro_usuario, self.otro_odontologo = crear_usuario_odontologo("otro")
        self.fecha = timezone.localdate() + timedelta(days=7)
        self.disponibilidad = DisponibilidadOdontologo.objects.create(
            odontologo=self.odontologo,
            dia_semana=self.fecha.weekday(),
            hora_inicio=time(9, 0),
            hora_fin=time(13, 0),
        )
        DisponibilidadOdontologo.objects.create(
            odontologo=self.otro_odontologo,
            dia_semana=self.fecha.weekday(),
            hora_inicio=time(9, 0),
            hora_fin=time(13, 0),
        )
        self.tipo = crear_tipo()
        self.configuracion_tipo = TipoTurnoOdontologo.objects.create(
            odontologo=self.odontologo,
            tipo_turno=self.tipo,
            duracion_atencion_minutos=45,
            margen_posterior_minutos=15,
            reserva_publica=True,
        )
        self.configuracion_agenda = ConfiguracionAgendaInteligente.objects.create(
            odontologo=self.odontologo,
            intervalo_inicio_minutos=15,
            hueco_minimo_util_minutos=30,
            cantidad_horarios_recomendados=4,
            cantidad_horarios_alternativos=8,
        )

    def datos_publicos(self, **cambios):
        datos = {
            "nombre": "Lucía",
            "apellido": "Paciente",
            "telefono": "2604000000",
            "documento": "88111222",
            "email": "lucia.smart@example.test",
            "odontologo": self.odontologo,
            "tipo_turno": self.tipo,
            "fecha": self.fecha,
            "hora_inicio": time(9, 0),
            "motivo": "Comentario opcional",
        }
        datos.update(cambios)
        return datos

    def test_validaciones_de_modelos(self):
        with self.assertRaises(ValidationError):
            TipoTurno(
                nombre="<b>Control</b>",
                slug="html",
                visible_publicamente=True,
            ).full_clean()
        for duracion, margen in ((9, 0), (22, 0), (240, 5), (30, 7)):
            with self.subTest(duracion=duracion, margen=margen):
                with self.assertRaises(ValidationError):
                    TipoTurnoOdontologo(
                        odontologo=self.odontologo,
                        tipo_turno=self.tipo,
                        duracion_atencion_minutos=duracion,
                        margen_posterior_minutos=margen,
                    ).full_clean()
        with self.assertRaises(ValidationError):
            ConfiguracionAgendaInteligente(
                odontologo=self.otro_odontologo,
                intervalo_inicio_minutos=17,
            ).full_clean()

    def test_tipo_usado_no_se_elimina_y_configuracion_es_unica(self):
        with self.assertRaises(ProtectedError):
            self.tipo.delete()
        with self.assertRaises((ValidationError, IntegrityError)):
            TipoTurnoOdontologo.objects.create(
                odontologo=self.odontologo,
                tipo_turno=self.tipo,
                duracion_atencion_minutos=30,
            )

    def test_endpoint_tipos_solo_expone_configuracion_publica(self):
        oculto = crear_tipo("Interno", "interno-smart")
        oculto.visible_publicamente = False
        oculto.save()
        TipoTurnoOdontologo.objects.create(
            odontologo=self.odontologo,
            tipo_turno=oculto,
            duracion_atencion_minutos=30,
        )
        response = self.client.get(
            reverse("turnos:solicitud_publica_tipos"),
            {"odontologo": self.odontologo.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["nombre"] for item in response.json()["tipos"]], ["Control"])

    def test_profesional_sin_servicios_recibe_estado_vacio(self):
        response = self.client.get(
            reverse("turnos:solicitud_publica_tipos"),
            {"odontologo": self.otro_odontologo.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tipos"], [])
        self.assertIn("no tiene turnos disponibles", response.json()["mensaje"])

    def test_tipo_o_configuracion_desactivados_no_se_publican(self):
        self.configuracion_tipo.reserva_publica = False
        self.configuracion_tipo.activo = False
        self.configuracion_tipo.save()
        response = self.client.get(
            reverse("turnos:solicitud_publica_tipos"),
            {"odontologo": self.odontologo.pk},
        )
        self.assertEqual(response.json()["tipos"], [])
        horarios = self.client.get(
            reverse("turnos:solicitud_publica_horarios"),
            {
                "odontologo": self.odontologo.pk,
                "tipo_turno": self.tipo.pk,
                "fecha": self.fecha.isoformat(),
            },
        )
        self.assertEqual(horarios.json()["codigo"], "tipo_turno_invalido")

    def test_endpoint_horarios_separa_grupos_y_no_expone_puntajes(self):
        response = self.client.get(
            reverse("turnos:solicitud_publica_horarios"),
            {
                "odontologo": self.odontologo.pk,
                "tipo_turno": self.tipo.pk,
                "fecha": self.fecha.isoformat(),
            },
        )
        self.assertEqual(response.status_code, 200)
        contenido = response.json()
        self.assertIn("horarios_recomendados", contenido)
        self.assertIn("horarios_alternativos", contenido)
        self.assertNotIn("puntaje", str(contenido).lower())
        self.assertNotIn("razones", str(contenido).lower())

    def test_formulario_rechaza_tipo_de_otro_odontologo(self):
        tipo_otro = crear_tipo("Consulta", "consulta-otro-smart")
        TipoTurnoOdontologo.objects.create(
            odontologo=self.otro_odontologo,
            tipo_turno=tipo_otro,
            duracion_atencion_minutos=30,
            reserva_publica=True,
        )
        datos = self.datos_publicos(tipo_turno=tipo_otro.pk)
        datos["odontologo"] = self.odontologo.pk
        datos["fecha"] = self.fecha.isoformat()
        datos["hora_inicio"] = "09:00"
        form = SolicitudTurnoPublicaForm(data=datos)
        self.assertFalse(form.is_valid())
        self.assertIn("tipo_turno", form.errors)

    def test_formulario_exige_tipo_con_feature_activo(self):
        datos = self.datos_publicos()
        datos.pop("tipo_turno")
        datos["odontologo"] = self.odontologo.pk
        datos["fecha"] = self.fecha.isoformat()
        datos["hora_inicio"] = "09:00"
        form = SolicitudTurnoPublicaForm(data=datos)
        self.assertFalse(form.is_valid())
        self.assertIn("tipo_turno", form.errors)

    def test_creacion_deriva_duracion_y_guarda_snapshots(self):
        datos = self.datos_publicos(duracion_minutos=5, margen_posterior=0, puntaje=99999)
        with self.captureOnCommitCallbacks(execute=False):
            resultado = crear_solicitud_publica_de_turno(datos)
        turno = resultado.turno
        solicitud = resultado.solicitud
        self.assertEqual(turno.duracion_atencion_minutos, 45)
        self.assertEqual(turno.margen_posterior_minutos_snapshot, 15)
        self.assertEqual(turno.duracion_minutos, 60)
        self.assertEqual(turno.tipo_turno, self.tipo)
        self.assertEqual(turno.tipo_turno_nombre_snapshot, "Control")
        self.assertIn(turno.clasificacion_horario, {"recomendado", "alternativo"})
        self.assertEqual(solicitud.duracion_bloqueada_snapshot, 60)
        self.assertEqual(solicitud.tipo_turno_nombre_snapshot, "Control")
        self.assertEqual(solicitud.horario_puntaje, turno.puntaje_horario)

    def test_cambiar_configuracion_no_modifica_snapshots_existentes(self):
        with self.captureOnCommitCallbacks(execute=False):
            turno = crear_solicitud_publica_de_turno(self.datos_publicos()).turno
        self.configuracion_tipo.duracion_atencion_minutos = 90
        self.configuracion_tipo.margen_posterior_minutos = 0
        self.configuracion_tipo.save()
        turno.refresh_from_db()
        self.assertEqual(turno.duracion_atencion_minutos, 45)
        self.assertEqual(turno.margen_posterior_minutos_snapshot, 15)
        self.assertEqual(turno.duracion_minutos, 60)

    def test_mismo_tipo_global_usa_duracion_de_cada_odontologo(self):
        self.configuracion_tipo.margen_posterior_minutos = 0
        self.configuracion_tipo.save()
        configuracion_otro = TipoTurnoOdontologo.objects.create(
            odontologo=self.otro_odontologo,
            tipo_turno=self.tipo,
            duracion_atencion_minutos=60,
            reserva_publica=True,
        )
        ConfiguracionAgendaInteligente.objects.create(odontologo=self.otro_odontologo)
        resultado_45 = calcular_horarios_inteligentes(
            odontologo=self.odontologo,
            fecha=self.fecha,
            duracion_atencion_minutos=45,
        )
        resultado_60 = calcular_horarios_inteligentes(
            odontologo=self.otro_odontologo,
            fecha=self.fecha,
            duracion_atencion_minutos=60,
        )
        self.assertNotEqual(
            {(item.hora_inicio, item.hora_fin_bloqueada) for item in resultado_45.todos},
            {(item.hora_inicio, item.hora_fin_bloqueada) for item in resultado_60.todos},
        )
        with self.captureOnCommitCallbacks(execute=False):
            turno_45 = crear_solicitud_publica_de_turno(self.datos_publicos()).turno
            turno_60 = crear_solicitud_publica_de_turno(
                self.datos_publicos(
                    documento="88111223",
                    email="otro.smart@example.test",
                    odontologo=self.otro_odontologo,
                    tipo_turno=configuracion_otro.tipo_turno,
                )
            ).turno
        self.assertEqual(turno_45.tipo_turno_id, turno_60.tipo_turno_id)
        self.assertEqual(turno_45.duracion_atencion_minutos, 45)
        self.assertEqual(turno_60.duracion_atencion_minutos, 60)

    def test_edicion_interna_conserva_snapshot_vigente_y_desactivado(self):
        paciente = crear_paciente(6)
        turno = Turno.objects.create(
            paciente=paciente,
            odontologo=self.odontologo,
            tipo_turno=self.tipo,
            tipo_turno_nombre_snapshot="Control",
            fecha=self.fecha,
            hora_inicio=time(9, 0),
            duracion_minutos=60,
            duracion_atencion_minutos=45,
            margen_posterior_minutos_snapshot=15,
            clasificacion_horario=Turno.ClasificacionHorario.RECOMENDADO,
            estado=Turno.Estado.CONFIRMADO,
        )
        self.configuracion_tipo.duracion_atencion_minutos = 90
        self.configuracion_tipo.margen_posterior_minutos = 0
        self.configuracion_tipo.save()

        def editar(notas):
            form = TurnoForm(
                data={
                    "paciente": paciente.pk,
                    "odontologo": self.odontologo.pk,
                    "tipo_turno": self.tipo.pk,
                    "fecha": self.fecha.isoformat(),
                    "hora_inicio": "09:00",
                    "duracion_minutos": 60,
                    "motivo": "Control",
                    "estado": Turno.Estado.CONFIRMADO,
                    "notas": notas,
                },
                instance=turno,
            )
            self.assertTrue(form.is_valid(), form.errors)
            return form.save()

        turno = editar("Configuración vigente distinta")
        self.configuracion_tipo.reserva_publica = False
        self.configuracion_tipo.activo = False
        self.configuracion_tipo.save()
        turno = editar("Servicio desactivado")
        self.assertEqual(turno.duracion_minutos, 60)
        self.assertEqual(turno.duracion_atencion_minutos, 45)
        self.assertEqual(turno.margen_posterior_minutos_snapshot, 15)
        self.assertEqual(turno.tipo_turno_nombre_snapshot, "Control")

    def test_alta_interna_admite_tipo_configurado_o_duracion_manual(self):
        paciente = crear_paciente(7)
        form_tipo = TurnoForm(
            data={
                "paciente": paciente.pk,
                "odontologo": self.odontologo.pk,
                "tipo_turno": self.tipo.pk,
                "fecha": self.fecha.isoformat(),
                "hora_inicio": "09:00",
                "duracion_minutos": 5,
                "motivo": "Control interno",
                "estado": Turno.Estado.CONFIRMADO,
                "notas": "",
            }
        )
        self.assertTrue(form_tipo.is_valid(), form_tipo.errors)
        turno_tipo = form_tipo.save()
        self.assertEqual(turno_tipo.duracion_minutos, 60)
        self.assertEqual(turno_tipo.duracion_atencion_minutos, 45)
        self.assertEqual(turno_tipo.margen_posterior_minutos_snapshot, 15)
        self.assertEqual(turno_tipo.clasificacion_horario, Turno.ClasificacionHorario.INTERNO)

        form_manual = TurnoForm(
            data={
                "paciente": paciente.pk,
                "odontologo": self.odontologo.pk,
                "tipo_turno": "",
                "fecha": self.fecha.isoformat(),
                "hora_inicio": "11:00",
                "duracion_minutos": 35,
                "motivo": "Tratamiento interno",
                "estado": Turno.Estado.CONFIRMADO,
                "notas": "",
            }
        )
        self.assertTrue(form_manual.is_valid(), form_manual.errors)
        turno_manual = form_manual.save()
        self.assertIsNone(turno_manual.tipo_turno)
        self.assertIsNone(turno_manual.duracion_atencion_minutos)
        self.assertEqual(turno_manual.duracion_minutos, 35)
        self.assertEqual(turno_manual.clasificacion_horario, Turno.ClasificacionHorario.INTERNO)

    def test_cancelacion_libera_hueco_exacto(self):
        with self.captureOnCommitCallbacks(execute=False):
            turno = crear_solicitud_publica_de_turno(self.datos_publicos()).turno
        antes = calcular_horarios_inteligentes(
            odontologo=self.odontologo,
            fecha=self.fecha,
            duracion_atencion_minutos=45,
            margen_posterior_minutos=15,
        )
        self.assertNotIn(time(9, 0), {item.hora_inicio for item in antes.todos})
        cancelar_turno(turno)
        despues = calcular_horarios_inteligentes(
            odontologo=self.odontologo,
            fecha=self.fecha,
            duracion_atencion_minutos=45,
            margen_posterior_minutos=15,
        )
        self.assertIn(time(9, 0), {item.hora_inicio for item in despues.todos})

    def test_excepciones_y_estados_se_respetan(self):
        paciente = crear_paciente(1)
        Turno.objects.create(
            paciente=paciente,
            odontologo=self.odontologo,
            fecha=self.fecha,
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.PENDIENTE,
        )
        ExcepcionAgenda.objects.create(
            tipo=ExcepcionAgenda.Tipo.BLOQUEO_PARCIAL,
            odontologo=self.odontologo,
            fecha_desde=self.fecha,
            fecha_hasta=self.fecha,
            todo_el_dia=False,
            hora_inicio=time(11, 0),
            hora_fin=time(11, 30),
            motivo="Bloqueo operativo",
        )
        resultado = calcular_horarios_inteligentes(
            odontologo=self.odontologo,
            fecha=self.fecha,
            duracion_atencion_minutos=30,
        )
        horas = {item.hora_inicio for item in resultado.todos}
        self.assertNotIn(time(10, 0), horas)
        self.assertNotIn(time(11, 0), horas)

    def test_pendiente_y_confirmado_ocupan_pero_cancelado_no(self):
        for indice, (hora, estado) in enumerate(
            (
                (time(9, 0), Turno.Estado.PENDIENTE),
                (time(10, 0), Turno.Estado.CONFIRMADO),
                (time(11, 0), Turno.Estado.CANCELADO),
            ),
            start=10,
        ):
            Turno.objects.create(
                paciente=crear_paciente(indice),
                odontologo=self.odontologo,
                fecha=self.fecha,
                hora_inicio=hora,
                duracion_minutos=30,
                estado=estado,
            )
        resultado = calcular_horarios_inteligentes(
            odontologo=self.odontologo,
            fecha=self.fecha,
            duracion_atencion_minutos=30,
        )
        horas = {item.hora_inicio for item in resultado.todos}
        self.assertNotIn(time(9, 0), horas)
        self.assertNotIn(time(10, 0), horas)
        self.assertIn(time(11, 0), horas)

    def test_excepcion_total_y_agenda_inactiva_no_ofrecen_horarios(self):
        self.configuracion_agenda.activa = False
        self.configuracion_agenda.save()
        desactivada = calcular_horarios_inteligentes(
            odontologo=self.odontologo,
            fecha=self.fecha,
            duracion_atencion_minutos=30,
        )
        self.assertEqual(desactivada.todos, ())

        self.configuracion_agenda.activa = True
        self.configuracion_agenda.save()
        ExcepcionAgenda.objects.create(
            tipo=ExcepcionAgenda.Tipo.FERIADO,
            fecha_desde=self.fecha,
            fecha_hasta=self.fecha,
            todo_el_dia=True,
            motivo="Cierre completo",
        )
        bloqueada = calcular_horarios_inteligentes(
            odontologo=self.odontologo,
            fecha=self.fecha,
            duracion_atencion_minutos=30,
        )
        self.assertEqual(bloqueada.todos, ())

    def test_reprogramacion_form_usa_snapshot_y_no_configuracion_nueva(self):
        paciente = crear_paciente(2)
        turno = Turno.objects.create(
            paciente=paciente,
            odontologo=self.odontologo,
            tipo_turno=self.tipo,
            tipo_turno_nombre_snapshot="Control",
            fecha=self.fecha,
            hora_inicio=time(9, 0),
            duracion_minutos=60,
            duracion_atencion_minutos=45,
            margen_posterior_minutos_snapshot=15,
            estado=Turno.Estado.PENDIENTE,
        )
        self.configuracion_tipo.duracion_atencion_minutos = 90
        self.configuracion_tipo.margen_posterior_minutos = 0
        self.configuracion_tipo.save()
        form = TurnoReprogramacionAccesoPublicoForm(
            instance=turno,
            initial={"fecha": self.fecha},
            accion_token="token-prueba",
        )
        self.assertIn("09:00", {value for value, _label in form.fields["hora_inicio"].choices})
        reprogramar_turno(
            turno,
            {"fecha": self.fecha, "hora_inicio": time(11, 0), "duracion_minutos": 60},
        )
        turno.refresh_from_db()
        self.assertEqual(turno.duracion_atencion_minutos, 45)
        self.assertEqual(turno.margen_posterior_minutos_snapshot, 15)

    def test_reprogramacion_interna_explicita_actualiza_snapshot_consistente(self):
        paciente = crear_paciente(30)
        turno = Turno.objects.create(
            paciente=paciente,
            odontologo=self.odontologo,
            tipo_turno=self.tipo,
            tipo_turno_nombre_snapshot="Control",
            fecha=self.fecha,
            hora_inicio=time(9, 0),
            duracion_minutos=60,
            duracion_atencion_minutos=45,
            margen_posterior_minutos_snapshot=15,
            clasificacion_horario=Turno.ClasificacionHorario.RECOMENDADO,
            estado=Turno.Estado.CONFIRMADO,
        )
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            reprogramar_turno(
                turno,
                {"fecha": self.fecha, "hora_inicio": time(10, 30), "duracion_minutos": 75},
            )
        self.assertEqual(len(callbacks), 1)
        turno.refresh_from_db()
        self.assertEqual(turno.duracion_minutos, 75)
        self.assertEqual(turno.duracion_atencion_minutos, 60)
        self.assertEqual(turno.margen_posterior_minutos_snapshot, 15)
        self.assertEqual(turno.clasificacion_horario, Turno.ClasificacionHorario.INTERNO)

    def test_reprogramacion_publica_segura_conserva_snapshots(self):
        with self.captureOnCommitCallbacks(execute=False):
            turno = crear_solicitud_publica_de_turno(self.datos_publicos()).turno
        token = "token-reprogramacion-smart"
        accion = AccionPublicaTurno.objects.create(
            turno=turno,
            paciente=turno.paciente,
            tipo_accion=AccionPublicaTurno.TipoAccion.REPROGRAMAR,
            token_hash=make_password(token),
            version_turno=turno.version_publica,
            expira_en=timezone.now() + timedelta(minutes=10),
        )
        self.configuracion_tipo.duracion_atencion_minutos = 90
        self.configuracion_tipo.margen_posterior_minutos = 0
        self.configuracion_tipo.save()
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            valida, reprogramado = reprogramar_turno_publico_seguro(
                accion.id,
                token,
                turno.paciente_id,
                {"fecha": self.fecha, "hora_inicio": time(11, 0)},
            )
        self.assertEqual(len(callbacks), 1)
        self.assertTrue(valida)
        self.assertEqual(reprogramado.duracion_minutos, 60)
        self.assertEqual(reprogramado.duracion_atencion_minutos, 45)
        self.assertEqual(reprogramado.margen_posterior_minutos_snapshot, 15)
        self.assertEqual(reprogramado.tipo_turno_nombre_snapshot, "Control")

    @override_settings(TURNOS_PUBLIC_BOOKING_HORARIOS_CACHE_SECONDS=60)
    def test_cache_distingue_configuracion_y_tolera_falla(self):
        cache.clear()
        with patch(
            "turnos.smart_scheduling_cache.calcular_horarios_inteligentes",
            wraps=calcular_horarios_inteligentes,
        ) as calcular:
            _primero, hit_1 = obtener_horarios_inteligentes_cacheados(
                configuracion_tipo=self.configuracion_tipo,
                fecha=self.fecha,
            )
            _segundo, hit_2 = obtener_horarios_inteligentes_cacheados(
                configuracion_tipo=self.configuracion_tipo,
                fecha=self.fecha,
            )
            self.assertFalse(hit_1)
            self.assertTrue(hit_2)
            self.assertEqual(calcular.call_count, 1)
        with patch("turnos.smart_scheduling_cache.cache.get", side_effect=OSError("fallo")):
            resultado, hit = obtener_horarios_inteligentes_cacheados(
                configuracion_tipo=self.configuracion_tipo,
                fecha=self.fecha,
            )
        self.assertFalse(hit)
        self.assertTrue(resultado.todos)
        with patch("turnos.smart_scheduling_cache.cache.get", return_value={"invalido": True}):
            resultado_corrupto, hit_corrupto = obtener_horarios_inteligentes_cacheados(
                configuracion_tipo=self.configuracion_tipo,
                fecha=self.fecha,
            )
        self.assertFalse(hit_corrupto)
        self.assertTrue(resultado_corrupto.todos)

    def test_confirmacion_exige_aceptar_cambio_de_duracion(self):
        sin_confirmar = ConfirmacionTurnoForm(
            data={"duracion_personalizada": 75},
            duracion_original=60,
            requiere_confirmacion_cambio=True,
        )
        self.assertFalse(sin_confirmar.is_valid())
        self.assertIn("confirmar_cambio_duracion", sin_confirmar.errors)
        confirmada = ConfirmacionTurnoForm(
            data={"duracion_personalizada": 75, "confirmar_cambio_duracion": "on"},
            duracion_original=60,
            requiere_confirmacion_cambio=True,
        )
        self.assertTrue(confirmada.is_valid(), confirmada.errors)

    def test_email_y_calendar_separan_duracion_visible_y_bloqueada(self):
        with self.captureOnCommitCallbacks(execute=False):
            turno = crear_solicitud_publica_de_turno(self.datos_publicos()).turno
        email = _renderizar_email_turno(turno, "turnos/emails/turno_confirmado.txt")
        evento = construir_evento_desde_turno(turno)
        self.assertIn("Motivo: Control", email)
        self.assertIn("Duración aproximada: 45 minutos", email)
        self.assertNotIn("Margen operativo", email)
        inicio = datetime.fromisoformat(evento.inicio)
        fin = datetime.fromisoformat(evento.fin)
        self.assertEqual(int((fin - inicio).total_seconds() // 60), 60)

    @override_settings(TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED=False)
    def test_flag_apagado_conserva_duracion_publica_de_30(self):
        datos = self.datos_publicos()
        datos.pop("tipo_turno")
        datos["hora_inicio"] = time(9, 0)
        with self.captureOnCommitCallbacks(execute=False):
            turno = crear_solicitud_publica_de_turno(datos).turno
        self.assertEqual(turno.duracion_minutos, 30)
        self.assertIsNone(turno.tipo_turno)


class ConfiguracionServiciosPermisosTests(TestCase):
    def setUp(self):
        self.usuario, self.odontologo = crear_usuario_odontologo("permisos")
        self.otro_usuario, self.otro_odontologo = crear_usuario_odontologo("ajeno")
        self.tipo = crear_tipo("Consulta", "consulta-permisos")
        self.configuracion = TipoTurnoOdontologo.objects.create(
            odontologo=self.odontologo,
            tipo_turno=self.tipo,
            duracion_atencion_minutos=30,
        )
        odontologos, _ = Group.objects.get_or_create(name="Odontologo")
        self.usuario.groups.add(odontologos)
        self.otro_usuario.groups.add(odontologos)

    def test_odontologo_edita_solo_sus_servicios(self):
        self.client.force_login(self.usuario)
        self.assertEqual(
            self.client.get(reverse("turnos:configuracion_servicios")).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                reverse("turnos:servicio_odontologo_editar", args=[self.configuracion.pk])
            ).status_code,
            200,
        )
        ajena = TipoTurnoOdontologo.objects.create(
            odontologo=self.otro_odontologo,
            tipo_turno=self.tipo,
            duracion_atencion_minutos=30,
        )
        self.assertEqual(
            self.client.get(
                reverse("turnos:servicio_odontologo_editar", args=[ajena.pk])
            ).status_code,
            403,
        )

    def test_recepcion_ve_pero_no_modifica(self):
        recepcion = get_user_model().objects.create_user("recepcion.smart", password="clave")
        grupo, _ = Group.objects.get_or_create(name="Recepcionista")
        recepcion.groups.add(grupo)
        self.client.force_login(recepcion)
        self.assertEqual(
            self.client.get(reverse("turnos:configuracion_servicios")).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                reverse("turnos:servicio_odontologo_editar", args=[self.configuracion.pk])
            ).status_code,
            403,
        )

    def test_administrador_gestiona_catalogo(self):
        administrador = get_user_model().objects.create_user("admin.smart", password="clave")
        grupo, _ = Group.objects.get_or_create(name="Administrador")
        administrador.groups.add(grupo)
        self.client.force_login(administrador)
        response = self.client.post(
            reverse("turnos:tipo_turno_crear"),
            {
                "nombre": "Primera revisión",
                "slug": "primera-revision",
                "descripcion_publica": "Evaluación inicial.",
                "icono": "info",
                "orden_publico": 40,
                "activo": "on",
                "visible_publicamente": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(TipoTurno.objects.filter(slug="primera-revision").exists())


class CrearTiposTurnoInicialesCommandTests(TestCase):
    def test_comando_inicial_es_idempotente_y_dry_run_no_escribe(self):
        call_command("crear_tipos_turno_iniciales", "--dry-run", verbosity=0)
        self.assertEqual(TipoTurno.objects.count(), 0)
        call_command("crear_tipos_turno_iniciales", verbosity=0)
        call_command("crear_tipos_turno_iniciales", verbosity=0)
        self.assertEqual(TipoTurno.objects.count(), 3)
        self.assertFalse(TipoTurno.objects.filter(visible_publicamente=True).exists())


@skipUnless(
    connection.vendor == "postgresql",
    "La reserva concurrente se valida con bloqueos reales de PostgreSQL.",
)
@override_settings(TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED=True)
class AgendaInteligentePostgreSQLTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        _usuario, self.odontologo = crear_usuario_odontologo("postgres")
        self.fecha = timezone.localdate() + timedelta(days=7)
        DisponibilidadOdontologo.objects.create(
            odontologo=self.odontologo,
            dia_semana=self.fecha.weekday(),
            hora_inicio=time(9, 0),
            hora_fin=time(11, 0),
        )
        self.tipo = crear_tipo("Control", "control-postgres")
        TipoTurnoOdontologo.objects.create(
            odontologo=self.odontologo,
            tipo_turno=self.tipo,
            duracion_atencion_minutos=30,
            reserva_publica=True,
        )
        ConfiguracionAgendaInteligente.objects.create(odontologo=self.odontologo)

    def test_dos_reservas_simultaneas_no_superponen_el_intervalo(self):
        barrera = Barrier(2)

        def reservar(indice):
            close_old_connections()
            odontologo = Odontologo.objects.get(pk=self.odontologo.pk)
            tipo = TipoTurno.objects.get(pk=self.tipo.pk)
            barrera.wait(timeout=5)
            try:
                crear_solicitud_publica_de_turno(
                    {
                        "nombre": "Paciente",
                        "apellido": f"Concurrente {indice}",
                        "telefono": "2604000000",
                        "documento": f"9911122{indice}",
                        "email": f"concurrente.{indice}@example.test",
                        "odontologo": odontologo,
                        "tipo_turno": tipo,
                        "fecha": self.fecha,
                        "hora_inicio": time(9, 0),
                        "motivo": "",
                    }
                )
                return "creada"
            except ValidationError:
                return "rechazada"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            resultados = list(executor.map(reservar, (1, 2)))

        self.assertEqual(resultados.count("creada"), 1)
        self.assertEqual(resultados.count("rechazada"), 1)
        self.assertEqual(
            Turno.objects.filter(
                odontologo=self.odontologo,
                fecha=self.fecha,
                hora_inicio=time(9, 0),
            ).count(),
            1,
        )
