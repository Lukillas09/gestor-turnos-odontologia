import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from urllib.parse import urlencode

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, close_old_connections, connection
from django.db.models.deletion import ProtectedError
from django.test import (
    RequestFactory,
    SimpleTestCase,
    TestCase,
    TransactionTestCase,
    override_settings,
)
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from consultorio.models import ConfiguracionConsultorio
from pacientes.models import Paciente
from turnos.admin import ConfiguracionAgendaInteligenteAdmin
from turnos.excepciones import obtener_rango_reserva_publica
from turnos.forms import (
    ConfiguracionAgendaInteligenteForm,
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
    SolicitudTurnoPublica,
    TipoTurno,
    TipoTurnoOdontologo,
    Turno,
)
from turnos.notifications import _renderizar_email_turno
from turnos.public_access.services import reprogramar_turno_publico_seguro
from turnos.services import cancelar_turno, reprogramar_turno
from turnos.smart_scheduling import (
    IntervaloLibre,
    buscar_proximo_horario_disponible,
    calcular_horarios_del_dia,
    calcular_horarios_inteligentes,
    cargar_contexto_agenda,
    evaluar_aprovechamiento_hueco,
    generar_resultado_horarios_inteligentes,
)
from turnos.smart_scheduling_cache import (
    _construir_clave,
    obtener_horarios_inteligentes_cacheados,
)
from turnos.solicitudes_publicas.services import crear_solicitud_publica_de_turno
from turnos.tests import crear_idempotency_token_para_cliente


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

    def generar(
        self,
        inicio,
        fin,
        duracion,
        *,
        margen=0,
        configuracion=None,
        limitado_inicio=True,
        limitado_fin=True,
        hora_inicio_minima=None,
    ):
        return generar_resultado_horarios_inteligentes(
            intervalos_libres=(
                IntervaloLibre(
                    self.fecha.replace(hour=inicio[0], minute=inicio[1]),
                    self.fecha.replace(hour=fin[0], minute=fin[1]),
                    limitado_por_ocupacion_inicio=limitado_inicio,
                    limitado_por_ocupacion_fin=limitado_fin,
                ),
            ),
            duracion_atencion_minutos=duracion,
            margen_posterior_minutos=margen,
            configuracion=configuracion or self.configuracion(),
            duraciones_publicas=(20, 30, 45, 60, 90),
            hora_inicio_minima=hora_inicio_minima,
        )

    def configuracion_por_defecto(self):
        """Valores por defecto del modelo: grilla 15, hueco mínimo 30, 4 + 8 horarios."""
        return self.configuracion(cantidad_horarios_alternativos=8)

    def assertContadoresCoherentes(self, resultado):
        self.assertEqual(resultado.total_candidatos_validos, len(resultado.todos))
        horas = [item.hora_inicio for item in resultado.todos]
        self.assertEqual(len(horas), len(set(horas)))

    def test_hueco_de_60_con_servicio_de_45_ofrece_el_borde_en_lugar_de_dejarlo_vacio(self):
        # Regresión: los dos candidatos (10:00 y 10:15) dejaban 15 minutos y quedaban en 0.
        resultado = self.generar(
            (10, 0), (11, 0), 45, configuracion=self.configuracion_por_defecto()
        )

        self.assertEqual([item.hora_inicio for item in resultado.todos], [time(10, 0)])
        self.assertIn("fallback_fragmentacion", resultado.todos[0].razones_tecnicas)
        self.assertEqual(resultado.descartados_por_fragmentacion, 1)
        self.assertContadoresCoherentes(resultado)

    def test_fallback_prefiere_quedar_pegado_a_un_turno_existente(self):
        # Disponibilidad en el inicio (borde) y turno al final: 10:15 queda pegado al turno.
        al_final = self.generar(
            (10, 0),
            (11, 0),
            45,
            configuracion=self.configuracion_por_defecto(),
            limitado_inicio=False,
            limitado_fin=True,
        )
        self.assertEqual([item.hora_inicio for item in al_final.todos], [time(10, 15)])
        self.assertIn("pegado_a_turno", al_final.todos[0].razones_tecnicas)

        al_inicio = self.generar(
            (10, 0),
            (11, 0),
            45,
            configuracion=self.configuracion_por_defecto(),
            limitado_inicio=True,
            limitado_fin=False,
        )
        self.assertEqual([item.hora_inicio for item in al_inicio.todos], [time(10, 0)])
        self.assertContadoresCoherentes(al_final)
        self.assertContadoresCoherentes(al_inicio)

    def test_hueco_de_50_con_servicio_de_30_ofrece_un_horario(self):
        # Regresión: 10:00, 10:15 y 10:20 dejaban un fragmento menor a 30 minutos.
        resultado = self.generar(
            (10, 0), (10, 50), 30, configuracion=self.configuracion_por_defecto()
        )

        self.assertEqual([item.hora_inicio for item in resultado.todos], [time(10, 0)])
        self.assertEqual(resultado.descartados_por_fragmentacion, 2)
        self.assertContadoresCoherentes(resultado)

    def test_fallback_no_se_activa_si_el_servicio_no_entra(self):
        resultado = self.generar(
            (10, 0), (10, 40), 45, configuracion=self.configuracion_por_defecto()
        )

        self.assertEqual(resultado.todos, ())
        self.assertEqual(resultado.descartados_por_fragmentacion, 0)

    def test_fallback_respeta_la_anticipacion_minima(self):
        # 10:00 queda fuera por anticipación (no cuenta como descartado por fragmentación);
        # el único candidato pegado a un borde que sobrevive es el ancla de fin.
        resultado = self.generar(
            (10, 0),
            (11, 0),
            45,
            configuracion=self.configuracion_por_defecto(),
            hora_inicio_minima=time(10, 5),
        )

        self.assertEqual([item.hora_inicio for item in resultado.todos], [time(10, 15)])
        self.assertEqual(resultado.descartados_por_fragmentacion, 0)
        self.assertContadoresCoherentes(resultado)

    def test_dia_vacio_ofrece_todos_los_candidatos_validos(self):
        # Regresión: 26 candidatos válidos pero solo 12 visibles con el tope de alternativos.
        resultado = generar_resultado_horarios_inteligentes(
            intervalos_libres=(
                IntervaloLibre(self.fecha.replace(hour=9), self.fecha.replace(hour=13)),
                IntervaloLibre(self.fecha.replace(hour=16), self.fecha.replace(hour=20)),
            ),
            duracion_atencion_minutos=30,
            margen_posterior_minutos=0,
            configuracion=self.configuracion_por_defecto(),
            duraciones_publicas=(30,),
        )

        esperados = {
            (self.fecha.replace(hour=base) + timedelta(minutes=15 * paso)).time()
            for base in (9, 16)
            for paso in range(15)
        } - {time(9, 15), time(12, 15), time(16, 15), time(19, 15)}
        self.assertEqual(resultado.total_candidatos_validos, 26)
        self.assertEqual({item.hora_inicio for item in resultado.todos}, esperados)
        self.assertEqual(len(resultado.recomendados), 4)
        self.assertEqual(len(resultado.alternativos), 22)
        self.assertEqual(
            resultado.total_candidatos_validos,
            len(resultado.recomendados) + len(resultado.alternativos),
        )
        self.assertEqual(resultado.descartados_por_fragmentacion, 4)
        horas_alternativas = [item.hora_inicio for item in resultado.alternativos]
        self.assertEqual(horas_alternativas, sorted(horas_alternativas))
        # La tarde completa es visible, incluidos los horarios que antes quedaban ocultos.
        horas_ofrecidas = {item.hora_inicio for item in resultado.todos}
        self.assertTrue({time(17, 0), time(18, 0), time(19, 0), time(19, 30)} <= horas_ofrecidas)
        self.assertContadoresCoherentes(resultado)

    def test_cantidad_de_alternativos_configurada_no_limita_el_resultado(self):
        sin_alternativos = self.generar(
            (9, 0), (13, 0), 30, configuracion=self.configuracion(cantidad_horarios_alternativos=0)
        )
        con_tope = self.generar(
            (9, 0), (13, 0), 30, configuracion=self.configuracion(cantidad_horarios_alternativos=1)
        )

        self.assertEqual(sin_alternativos, con_tope)
        self.assertGreater(len(sin_alternativos.alternativos), 1)

    def test_ancla_de_inicio_ofrece_el_borde_de_un_turno_fuera_de_grilla(self):
        # Un turno interno termina 10:20: el horario pegado a ese borde se ofrece y es el primero.
        resultado = self.generar(
            (10, 20), (13, 0), 30, configuracion=self.configuracion_por_defecto()
        )

        self.assertIn(time(10, 20), {item.hora_inicio for item in resultado.todos})
        self.assertEqual(resultado.recomendados[0].hora_inicio, time(10, 20))
        self.assertIn("pegado_inicio", resultado.recomendados[0].razones_tecnicas)
        self.assertContadoresCoherentes(resultado)

    def test_ancla_de_fin_ofrece_el_ultimo_inicio_que_entra(self):
        resultado = self.generar(
            (10, 0), (12, 50), 30, configuracion=self.configuracion_por_defecto()
        )

        candidato = next(item for item in resultado.todos if item.hora_inicio == time(12, 20))
        self.assertEqual(candidato.hora_fin_bloqueada, time(12, 50))
        self.assertEqual(candidato.hueco_posterior_minutos, 0)
        self.assertContadoresCoherentes(resultado)

    def test_anclas_redondean_bordes_con_segundos_a_minutos_enteros(self):
        resultado = generar_resultado_horarios_inteligentes(
            intervalos_libres=(
                IntervaloLibre(
                    self.fecha.replace(hour=10, minute=20, second=30),
                    self.fecha.replace(hour=12, minute=50, second=30),
                    limitado_por_ocupacion_inicio=True,
                    limitado_por_ocupacion_fin=True,
                ),
            ),
            duracion_atencion_minutos=30,
            margen_posterior_minutos=0,
            configuracion=self.configuracion_por_defecto(),
            duraciones_publicas=(30,),
        )

        horas = {item.hora_inicio for item in resultado.todos}
        # Inicio redondeado hacia arriba y fin hacia abajo: nada ofrecido puede tener segundos,
        # porque la URL usa HH:MM y la revalidación compara por igualdad exacta.
        self.assertIn(time(10, 21), horas)
        self.assertIn(time(12, 20), horas)
        self.assertTrue(all(hora.second == 0 and hora.microsecond == 0 for hora in horas))
        self.assertContadoresCoherentes(resultado)

    def test_orden_lexicografico_prefiere_turno_sobre_borde_de_disponibilidad(self):
        # Intervalo 10:00-12:00, servicio de 45: 10:00 queda pegado a un turno y 11:15 al final
        # de la disponibilidad. Solo cambia el criterio (c), por eso gana 10:00.
        resultado = self.generar(
            (10, 0),
            (12, 0),
            45,
            configuracion=self.configuracion_por_defecto(),
            limitado_inicio=True,
            limitado_fin=False,
        )

        puntajes = {item.hora_inicio: item.puntaje for item in resultado.todos}
        # Dígitos: exacto, sin hueco inútil, pegado, no rompe bloque largo, resto, compactación.
        self.assertEqual(puntajes[time(10, 0)], 12010)
        self.assertEqual(puntajes[time(11, 15)], 11010)
        self.assertEqual(resultado.recomendados[0].hora_inicio, time(10, 0))
        self.assertIn("pegado_a_turno", resultado.recomendados[0].razones_tecnicas)

    def test_orden_lexicografico_codifica_cada_criterio_en_un_digito(self):
        exacto = self.generar((14, 45), (15, 15), 30)
        self.assertEqual(exacto.todos[0].puntaje, 112100)

        fallback = self.generar(
            (10, 0), (11, 0), 45, configuracion=self.configuracion_por_defecto()
        )
        # b = 0 porque el candidato deja un resto inútil y solo existe por el fallback.
        self.assertEqual(fallback.todos[0].puntaje, 2100)

        # Un candidato que no deja resto inútil siempre supera a uno de fallback.
        valido = self.generar((9, 0), (13, 0), 30, configuracion=self.configuracion_por_defecto())
        self.assertGreater(min(item.puntaje for item in valido.todos), fallback.todos[0].puntaje)

    def test_sin_organizar_la_agenda_ofrece_solo_la_grilla_y_nada_destacado(self):
        configuracion = self.configuracion_por_defecto()
        configuracion.evitar_huecos = False

        # 10:00 y 10:15 serían fragmentadores (y 10:00 el fallback) con la organización activa.
        hueco = self.generar((10, 0), (11, 0), 45, configuracion=configuracion)
        self.assertEqual([item.hora_inicio for item in hueco.todos], [time(10, 0), time(10, 15)])
        self.assertEqual(hueco.recomendados, ())
        self.assertEqual(hueco.descartados_por_fragmentacion, 0)
        self.assertTrue(
            all(
                item.clasificacion == Turno.ClasificacionHorario.ALTERNATIVO for item in hueco.todos
            )
        )

        # Sin anclas: un turno que termina 10:20 no genera el horario 10:20.
        borde = self.generar((10, 20), (13, 0), 30, configuracion=configuracion)
        self.assertNotIn(time(10, 20), {item.hora_inicio for item in borde.todos})
        self.assertEqual(borde.todos[0].hora_inicio, time(10, 30))
        self.assertContadoresCoherentes(borde)

    def test_cronologicos_ordena_por_hora_sin_separar_recomendados(self):
        resultado = self.generar(
            (9, 0), (13, 0), 30, configuracion=self.configuracion_por_defecto()
        )

        horas = [item.hora_inicio for item in resultado.cronologicos]
        self.assertEqual(horas, sorted(horas))
        self.assertEqual(len(horas), resultado.total_candidatos_validos)
        self.assertTrue(resultado.recomendados)
        self.assertNotEqual(horas, [item.hora_inicio for item in resultado.todos])

    def test_resultado_con_fallback_y_anclas_es_determinista(self):
        for inicio, fin, duracion in (((10, 0), (11, 0), 45), ((10, 20), (13, 0), 30)):
            with self.subTest(inicio=inicio, fin=fin, duracion=duracion):
                primero = self.generar(
                    inicio, fin, duracion, configuracion=self.configuracion_por_defecto()
                )
                segundo = self.generar(
                    inicio, fin, duracion, configuracion=self.configuracion_por_defecto()
                )
                self.assertEqual(primero, segundo)

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
                # smart-v2: además de la grilla se ofrece el ancla de fin (13:00 - duración),
                # que con 20 minutos es 12:40 y no cae en la grilla de 15.
                ancla_fin = (self.fecha.replace(hour=13) - timedelta(minutes=duracion)).time()
                self.assertTrue(
                    all(
                        item.hora_inicio.minute % 15 == 0 or item.hora_inicio == ancla_fin
                        for item in primero.todos
                    )
                )

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

    def test_endpoint_horarios_agrupa_por_franja_con_sugeridos_y_no_expone_puntajes(self):
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
        # Una lista cronológica por franja; "recomendados" y "alternativos" ya no existen.
        self.assertIn("horarios_manana", contenido)
        self.assertIn("horarios_tarde", contenido)
        self.assertNotIn("horarios_recomendados", contenido)
        self.assertNotIn("horarios_alternativos", contenido)
        etiquetas = [opcion["label"] for opcion in contenido["horarios_manana"]]
        self.assertEqual(etiquetas, sorted(etiquetas))
        self.assertTrue(contenido["hay_sugeridos"])
        self.assertTrue(any(opcion["sugerido"] for opcion in contenido["horarios_manana"]))
        self.assertTrue(any(not opcion["sugerido"] for opcion in contenido["horarios_manana"]))
        for opcion in contenido["horarios_manana"] + contenido["horarios_tarde"]:
            self.assertEqual(set(opcion), {"label", "url", "sugerido"})
        self.assertNotIn("puntaje", str(contenido).lower())
        self.assertNotIn("razones", str(contenido).lower())

    def test_seleccion_preselecciona_motivo_publico_sin_errores_de_busqueda(self):
        response = self.client.get(
            reverse("turnos:solicitud_publica"), {"tipo_turno": self.tipo.pk}
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["busqueda_form"].is_bound)
        self.assertEqual(response.context["tipo_turno_preseleccionado"], self.tipo)
        self.assertContains(response, f'data-preselected-type="{self.tipo.pk}"')
        self.assertContains(response, "Elegí un profesional para reservar control.")
        self.assertNotContains(response, "field-error")

        landing = self.client.get(reverse("landing_publica"))
        self.assertContains(
            landing, f'{reverse("turnos:solicitud_publica")}?tipo_turno={self.tipo.pk}'
        )

    def test_seleccion_ignora_motivo_preseleccionado_invalido_u_oculto(self):
        tipo_oculto = crear_tipo("Blanqueamiento", "blanqueamiento-presel")
        TipoTurno.objects.filter(pk=tipo_oculto.pk).update(visible_publicamente=False)

        for valor in ("abc", "999999", str(tipo_oculto.pk)):
            with self.subTest(valor=valor):
                response = self.client.get(
                    reverse("turnos:solicitud_publica"), {"tipo_turno": valor}
                )
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.context["tipo_turno_preseleccionado"])
                self.assertNotContains(response, "data-preselected-type")
                self.assertNotContains(response, "field-error")

        with override_settings(TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED=False):
            response = self.client.get(
                reverse("turnos:solicitud_publica"), {"tipo_turno": self.tipo.pk}
            )
        self.assertIsNone(response.context["tipo_turno_preseleccionado"])

    def test_tipo_preseleccionado_no_habilitado_se_ignora_y_el_servidor_lo_valida(self):
        tipo_otro = crear_tipo("Ortodoncia", "ortodoncia-presel")
        TipoTurnoOdontologo.objects.create(
            odontologo=self.otro_odontologo,
            tipo_turno=tipo_otro,
            duracion_atencion_minutos=30,
            reserva_publica=True,
        )

        # La preselección no es una búsqueda: renderiza sin errores.
        response = self.client.get(
            reverse("turnos:solicitud_publica"), {"tipo_turno": tipo_otro.pk}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["busqueda_form"].is_bound)
        self.assertNotContains(response, "field-error")

        # El profesional elegido no lo ofrece: el endpoint de motivos no lo devuelve,
        # así que el cliente no tiene qué preseleccionar.
        tipos = self.client.get(
            reverse("turnos:solicitud_publica_tipos"), {"odontologo": self.odontologo.pk}
        ).json()["tipos"]
        self.assertNotIn(tipo_otro.pk, [tipo["tipo_turno_id"] for tipo in tipos])

        # Si igual llega como búsqueda completa, el formulario del servidor lo rechaza.
        response = self.client.get(
            reverse("turnos:solicitud_publica"),
            {
                "odontologo": self.odontologo.pk,
                "tipo_turno": tipo_otro.pk,
                "fecha": self.fecha.isoformat(),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("tipo_turno", response.context["busqueda_form"].errors)
        self.assertNotContains(response, "data-public-slot=")

        reserva = {
            "odontologo": self.odontologo.pk,
            "tipo_turno": tipo_otro.pk,
            "fecha": self.fecha.isoformat(),
            "hora_inicio": "09:00",
        }
        response = self.client.get(reverse("turnos:solicitud_publica_datos"), reserva)
        self.assertRedirects(response, reverse("turnos:solicitud_publica"))

        # Y al enviar, la validación final tampoco lo acepta ni crea el turno.
        datos = {
            **reserva,
            "nombre": "Lucía",
            "apellido": "Paciente",
            "telefono": "2604000000",
            "documento": "88111333",
            "email": "lucia.presel@example.test",
            "motivo": "",
            "idempotency_token": crear_idempotency_token_para_cliente(self.client),
        }
        response = self.client.post(reverse("turnos:solicitud_publica_datos"), datos)
        self.assertEqual(response.status_code, 200)
        self.assertIn("tipo_turno", response.context["form"].errors)
        self.assertFalse(Turno.objects.filter(paciente__documento="88111333").exists())

    def test_seleccion_renderiza_grupos_de_horarios_sin_javascript(self):
        response = self.client.get(
            reverse("turnos:solicitud_publica"),
            {
                "odontologo": self.odontologo.pk,
                "tipo_turno": self.tipo.pk,
                "fecha": self.fecha.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-slot-group="manana"')
        self.assertNotContains(response, 'data-slot-group="recomendados"')
        self.assertNotContains(response, "Recomendados")
        # Los sugeridos se marcan por horario (no solo con color) y hay una leyenda.
        self.assertContains(response, "Horario sugerido por el consultorio")
        self.assertContains(response, 'data-slot-suggested="true"')
        self.assertContains(response, 'data-slot-suggested="false"')
        self.assertContains(response, ', sugerido"')
        # Dos profesionales y un motivo; quedan marcados el profesional y el motivo elegidos.
        self.assertContains(response, 'role="radio"', count=3)
        self.assertContains(response, 'aria-checked="true"', count=2)
        self.assertNotContains(response, "puntaje")
        self.assertNotContains(response, "public-more-slots")

    def test_landing_muestra_solo_motivos_reservables_publicamente(self):
        tipo_oculto = crear_tipo("Blanqueamiento", "blanqueamiento-smart")
        tipo_sin_reserva = crear_tipo("Ortodoncia", "ortodoncia-smart")
        tipo_inactivo_odontologo = crear_tipo("Implante", "implante-smart")
        _, odontologo_inactivo = crear_usuario_odontologo("inactivo")
        for odontologo, tipo, reserva_publica in (
            (self.odontologo, tipo_oculto, True),
            (self.odontologo, tipo_sin_reserva, False),
            (odontologo_inactivo, tipo_inactivo_odontologo, True),
        ):
            TipoTurnoOdontologo.objects.create(
                odontologo=odontologo,
                tipo_turno=tipo,
                duracion_atencion_minutos=30,
                reserva_publica=reserva_publica,
            )
        TipoTurno.objects.filter(pk=tipo_oculto.pk).update(visible_publicamente=False)
        Odontologo.objects.filter(pk=odontologo_inactivo.pk).update(activo=False)

        response = self.client.get(reverse("landing_publica"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["tipos_turno_publicos"], [self.tipo])
        self.assertContains(response, 'id="servicios"')
        self.assertContains(response, 'href="#servicios"', count=2)
        self.assertContains(response, "Reservar control")
        self.assertContains(response, "Servicio programado.")
        self.assertContains(response, "Elegí profesional y motivo")
        self.assertNotContains(response, "Blanqueamiento")
        self.assertNotContains(response, "Ortodoncia")
        self.assertNotContains(response, "Implante")

    def test_paso_datos_vuelve_a_la_seleccion_conservando_tipo_de_turno(self):
        horarios = self.client.get(
            reverse("turnos:solicitud_publica_horarios"),
            {
                "odontologo": self.odontologo.pk,
                "tipo_turno": self.tipo.pk,
                "fecha": self.fecha.isoformat(),
            },
        ).json()
        opcion = [
            opcion
            for franja in ("horarios_manana", "horarios_tarde")
            for opcion in horarios[franja]
        ][0]

        response = self.client.get(opcion["url"])

        self.assertEqual(response.status_code, 200)
        parametros = urlencode(
            {
                "odontologo": self.odontologo.pk,
                "fecha": self.fecha.isoformat(),
                "tipo_turno": self.tipo.pk,
            }
        )
        url_seleccion = escape(f"{reverse('turnos:solicitud_publica')}?{parametros}")
        self.assertContains(response, f'class="public-progress-label" href="{url_seleccion}"')
        self.assertContains(response, f'href="{url_seleccion}"', count=3)

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

    def crear_turno_interno(self, sufijo, hora, duracion, **extra):
        return Turno.objects.create(
            paciente=crear_paciente(sufijo),
            odontologo=self.odontologo,
            fecha=self.fecha,
            hora_inicio=hora,
            duracion_minutos=duracion,
            estado=extra.pop("estado", Turno.Estado.CONFIRMADO),
            **extra,
        )

    def test_reserva_publica_en_horario_anclado_pasa_las_tres_validaciones(self):
        # Un turno interno de duración manual termina 10:20, fuera de la grilla de 15 minutos.
        self.crear_turno_interno(40, time(9, 0), 80)
        resultado = calcular_horarios_inteligentes(
            odontologo=self.odontologo,
            fecha=self.fecha,
            duracion_atencion_minutos=45,
            margen_posterior_minutos=15,
        )
        self.assertEqual(resultado.algoritmo_version, "smart-v2")
        self.assertIn(time(10, 20), {item.hora_inicio for item in resultado.todos})

        horarios = self.client.get(
            reverse("turnos:solicitud_publica_horarios"),
            {
                "odontologo": self.odontologo.pk,
                "tipo_turno": self.tipo.pk,
                "fecha": self.fecha.isoformat(),
            },
        ).json()
        etiquetas = [
            opcion["label"]
            for franja in ("horarios_manana", "horarios_tarde")
            for opcion in horarios[franja]
        ]
        self.assertIn("10:20", etiquetas)
        self.assertNotIn("puntaje", str(horarios).lower())
        self.assertNotIn("razones", str(horarios).lower())

        reserva = {
            "odontologo": self.odontologo.pk,
            "tipo_turno": self.tipo.pk,
            "fecha": self.fecha.isoformat(),
            "hora_inicio": "10:20",
        }
        # Puerta 1: la página de datos revalida que el horario siga ofrecido.
        self.assertEqual(
            self.client.get(reverse("turnos:solicitud_publica_datos"), reserva).status_code, 200
        )
        # Puertas 2 y 3: choices y clean() del formulario, y el servicio bajo bloqueo.
        response = self.client.post(
            reverse("turnos:solicitud_publica_datos"),
            {
                **reserva,
                "nombre": "Lucía",
                "apellido": "Anclada",
                "telefono": "2604000000",
                "documento": "88111444",
                "email": "lucia.ancla@example.test",
                "motivo": "",
                "idempotency_token": crear_idempotency_token_para_cliente(self.client),
            },
        )
        self.assertRedirects(response, reverse("landing_publica"), fetch_redirect_response=False)

        turno = Turno.objects.get(paciente__documento="88111444")
        solicitud = SolicitudTurnoPublica.objects.get(turno=turno)
        self.assertEqual(turno.hora_inicio, time(10, 20))
        self.assertEqual(turno.duracion_minutos, 60)
        self.assertEqual(turno.algoritmo_horario_version, "smart-v2")
        self.assertEqual(solicitud.algoritmo_version, "smart-v2")
        self.assertEqual(solicitud.horario_puntaje, turno.puntaje_horario)

    def test_reprogramacion_publica_acepta_un_horario_de_fallback(self):
        # Hueco de 75 minutos (10:00-11:15) para un servicio de 60: ninguna alternativa evita
        # dejar 15 minutos, así que 10:00 solo existe por el fallback por intervalo.
        self.crear_turno_interno(41, time(9, 0), 60)
        self.crear_turno_interno(42, time(11, 15), 45)
        turno = self.crear_turno_interno(
            43,
            time(12, 0),
            60,
            tipo_turno=self.tipo,
            tipo_turno_nombre_snapshot="Control",
            duracion_atencion_minutos=45,
            margen_posterior_minutos_snapshot=15,
            algoritmo_horario_version="smart-v1",
            clasificacion_horario=Turno.ClasificacionHorario.RECOMENDADO,
            puntaje_horario=1234,
            estado=Turno.Estado.PENDIENTE,
        )

        form = TurnoReprogramacionAccesoPublicoForm(
            instance=turno,
            initial={"fecha": self.fecha},
            accion_token="token-prueba",
        )
        self.assertIn("10:00", {valor for valor, _etiqueta in form.fields["hora_inicio"].choices})

        token = "token-reprogramacion-fallback"
        accion = AccionPublicaTurno.objects.create(
            turno=turno,
            paciente=turno.paciente,
            tipo_accion=AccionPublicaTurno.TipoAccion.REPROGRAMAR,
            token_hash=make_password(token),
            version_turno=turno.version_publica,
            expira_en=timezone.now() + timedelta(minutes=10),
        )
        with self.captureOnCommitCallbacks(execute=False):
            valida, reprogramado = reprogramar_turno_publico_seguro(
                accion.id,
                token,
                turno.paciente_id,
                {"fecha": self.fecha, "hora_inicio": time(10, 0)},
            )

        self.assertTrue(valida)
        self.assertEqual(reprogramado.hora_inicio, time(10, 0))
        self.assertEqual(reprogramado.duracion_minutos, 60)
        # El horario nuevo se revalidó con smart-v2, así que la versión y el puntaje lo reflejan.
        self.assertEqual(reprogramado.algoritmo_horario_version, "smart-v2")
        self.assertEqual(reprogramado.puntaje_horario, 2100)

    def test_turnos_smart_v1_conservan_su_snapshot_al_reservar_otros(self):
        antiguo = self.crear_turno_interno(
            44,
            time(12, 0),
            60,
            tipo_turno=self.tipo,
            tipo_turno_nombre_snapshot="Control",
            duracion_atencion_minutos=45,
            margen_posterior_minutos_snapshot=15,
            algoritmo_horario_version="smart-v1",
            clasificacion_horario=Turno.ClasificacionHorario.RECOMENDADO,
            puntaje_horario=1990,
        )
        with self.captureOnCommitCallbacks(execute=False):
            nuevo = crear_solicitud_publica_de_turno(self.datos_publicos()).turno

        antiguo.refresh_from_db()
        self.assertEqual(antiguo.algoritmo_horario_version, "smart-v1")
        self.assertEqual(antiguo.puntaje_horario, 1990)
        self.assertEqual(nuevo.algoritmo_horario_version, "smart-v2")

    def test_clave_de_cache_incluye_la_version_del_algoritmo(self):
        clave = _construir_clave(
            configuracion_tipo=self.configuracion_tipo,
            configuracion_agenda=self.configuracion_agenda,
            fecha=self.fecha,
            bucket=1,
        )

        self.assertIn(":smart-v2:", clave)
        self.assertNotIn("smart-v1", clave)

    def test_cantidad_de_alternativos_obsoleta_no_se_expone_en_formulario_ni_admin(self):
        self.assertNotIn(
            "cantidad_horarios_alternativos", ConfiguracionAgendaInteligenteForm().fields
        )
        modelo_admin = ConfiguracionAgendaInteligenteAdmin(
            ConfiguracionAgendaInteligente, admin.site
        )
        request = RequestFactory().get("/")
        request.user = get_user_model().objects.create_superuser("admin.config.smart", password="x")
        formulario_admin = modelo_admin.get_form(request)
        self.assertNotIn("cantidad_horarios_alternativos", formulario_admin.base_fields)

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


@override_settings(TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED=True)
class AgendaInteligenteUiPublicaTests(TestCase):
    """Render sin JavaScript, JSON, próximo disponible y modo sin organización."""

    def setUp(self):
        _usuario, self.odontologo = crear_usuario_odontologo("uipublica")
        self.hoy = timezone.localdate()
        self.fecha = self.hoy + timedelta(days=3)
        for dia_semana in range(7):
            DisponibilidadOdontologo.objects.create(
                odontologo=self.odontologo,
                dia_semana=dia_semana,
                hora_inicio=time(9, 0),
                hora_fin=time(13, 0),
            )
        self.tipo = crear_tipo("Control", "control-uipublica")
        self.configuracion_tipo = TipoTurnoOdontologo.objects.create(
            odontologo=self.odontologo,
            tipo_turno=self.tipo,
            duracion_atencion_minutos=30,
            reserva_publica=True,
        )
        self.configuracion_agenda = ConfiguracionAgendaInteligente.objects.create(
            odontologo=self.odontologo
        )

    def parametros(self, fecha):
        return {
            "odontologo": self.odontologo.pk,
            "tipo_turno": self.tipo.pk,
            "fecha": fecha.isoformat(),
        }

    def consultar_json(self, fecha):
        return self.client.get(
            reverse("turnos:solicitud_publica_horarios"), self.parametros(fecha)
        ).json()

    def consultar_html(self, fecha):
        return self.client.get(reverse("turnos:solicitud_publica"), self.parametros(fecha))

    def bloquear_dias(self, desde, hasta):
        ExcepcionAgenda.objects.create(
            tipo=ExcepcionAgenda.Tipo.FERIADO,
            odontologo=self.odontologo,
            fecha_desde=desde,
            fecha_hasta=hasta,
            todo_el_dia=True,
            motivo="Cierre",
        )

    def crear_turno(self, fecha, hora, duracion, estado=Turno.Estado.CONFIRMADO):
        return Turno.objects.create(
            paciente=crear_paciente(60 + Turno.objects.count()),
            odontologo=self.odontologo,
            fecha=fecha,
            hora_inicio=hora,
            duracion_minutos=duracion,
            estado=estado,
        )

    def franjas_desde_html(self, html):
        franjas = {}
        for grupo, bloque in re.findall(r'data-slot-group="(\w+)">(.*?)</section>', html, re.S):
            franjas[grupo] = re.findall(
                r'data-public-slot="([0-9:]+)"[^>]*?data-slot-suggested="(true|false)"', bloque
            )
        return franjas

    def test_render_sin_javascript_y_json_describen_los_mismos_horarios(self):
        json_horarios = self.consultar_json(self.fecha)
        html = self.consultar_html(self.fecha).content.decode()

        esperado = {
            franja: [
                (opcion["label"], "true" if opcion["sugerido"] else "false")
                for opcion in json_horarios[f"horarios_{franja}"]
            ]
            for franja in ("manana", "tarde")
            if json_horarios[f"horarios_{franja}"]
        }
        self.assertTrue(esperado)
        self.assertEqual(self.franjas_desde_html(html), esperado)
        self.assertEqual(
            json_horarios["hay_sugeridos"], "Horario sugerido por el consultorio" in html
        )

    def test_dia_vacio_ofrece_el_proximo_disponible_en_json_y_sin_javascript(self):
        self.bloquear_dias(self.fecha, self.fecha)

        contenido = self.consultar_json(self.fecha)
        proximo = contenido["proximo_disponible"]
        siguiente = self.fecha + timedelta(days=1)

        self.assertEqual(contenido["horarios_manana"], [])
        self.assertEqual(contenido["horarios_tarde"], [])
        self.assertEqual(proximo["fecha"]["iso"], siguiente.isoformat())
        self.assertEqual(proximo["hora"], "09:00")
        self.assertIn("hora_inicio=09%3A00", proximo["url"])
        self.assertIn(f"fecha={siguiente.isoformat()}", proximo["url"])
        self.assertEqual(contenido["mensaje"], "No hay horarios disponibles para esa fecha.")
        # El próximo disponible sale del mismo motor que la reserva.
        esperado = calcular_horarios_inteligentes(
            odontologo=self.odontologo,
            fecha=siguiente,
            duracion_atencion_minutos=30,
        )
        self.assertEqual(esperado.cronologicos[0].hora_inicio, time(9, 0))
        self.assertNotIn("puntaje", str(contenido).lower())

        response = self.consultar_html(self.fecha)
        self.assertContains(response, "data-public-next-available")
        self.assertContains(response, f'href="{escape(proximo["url"])}"')
        self.assertContains(response, "Próximo disponible:")
        self.assertNotContains(response, "data-public-slot=")

    def test_sin_disponibilidad_posterior_muestra_un_mensaje_neutral(self):
        self.bloquear_dias(self.hoy, self.hoy + timedelta(days=120))
        mensaje = "No encontramos horarios disponibles después de esta fecha"

        contenido = self.consultar_json(self.fecha)

        self.assertIsNone(contenido["proximo_disponible"])
        self.assertIn(mensaje, contenido["mensaje"])
        response = self.consultar_html(self.fecha)
        self.assertContains(response, mensaje)
        self.assertNotContains(response, "data-public-next-available")

    def test_el_mensaje_no_afirma_que_toda_la_ventana_esta_vacia(self):
        # Hay horarios antes de la fecha elegida, pero ninguno después: la búsqueda solo mira
        # hacia adelante, así que el texto no puede decir que no hay nada en toda la ventana.
        elegida = self.hoy + timedelta(days=10)
        self.bloquear_dias(elegida, self.hoy + timedelta(days=120))

        contenido = self.consultar_json(elegida)

        self.assertTrue(self.consultar_json(self.hoy + timedelta(days=5))["horarios_manana"])
        self.assertIsNone(contenido["proximo_disponible"])
        self.assertIn("después de esta fecha", contenido["mensaje"])
        self.assertNotIn("próximos", contenido["mensaje"])
        self.assertContains(self.consultar_html(elegida), "después de esta fecha")

    def test_un_dia_con_horarios_no_busca_el_proximo_disponible(self):
        with patch("turnos.views.public_booking.buscar_proximo_horario_disponible") as buscar:
            contenido = self.consultar_json(self.fecha)
            self.consultar_html(self.fecha)
        buscar.assert_not_called()
        self.assertIsNone(contenido["proximo_disponible"])

        self.bloquear_dias(self.fecha, self.fecha)
        with patch(
            "turnos.views.public_booking.buscar_proximo_horario_disponible", return_value=None
        ) as buscar:
            self.consultar_json(self.fecha)
        buscar.assert_called_once()

    def test_busqueda_del_proximo_disponible_usa_consultas_constantes(self):
        # Toda la ventana está bloqueada: la búsqueda recorre todos los días en memoria.
        self.bloquear_dias(self.hoy, self.hoy + timedelta(days=200))
        consultas = {}
        for ventana_dias in (14, 60):
            ConfiguracionConsultorio.objects.update_or_create(
                pk=1, defaults={"ventana_reserva_publica_dias": ventana_dias}
            )
            with CaptureQueriesContext(connection) as capturadas:
                encontrado = buscar_proximo_horario_disponible(
                    odontologo=self.odontologo,
                    desde=self.hoy,
                    duracion_atencion_minutos=30,
                )
            self.assertIsNone(encontrado)
            consultas[ventana_dias] = len(capturadas)

        self.assertEqual(consultas[14], consultas[60])
        self.assertLessEqual(consultas[14], 8)

    def test_calculo_en_bloque_equivale_al_calculo_dia_por_dia(self):
        # "Ahora" fijo a las 10:00: con 120 minutos de anticipación el primer día solo ofrece
        # horarios desde las 12:00.
        ahora = timezone.make_aware(datetime.combine(self.hoy, time(10, 0)))
        self.crear_turno(self.hoy + timedelta(days=1), time(9, 0), 80)
        self.crear_turno(self.hoy + timedelta(days=4), time(11, 0), 45)
        self.crear_turno(
            self.hoy + timedelta(days=5), time(9, 0), 60, estado=Turno.Estado.CANCELADO
        )
        ExcepcionAgenda.objects.create(
            tipo=ExcepcionAgenda.Tipo.BLOQUEO_PARCIAL,
            odontologo=self.odontologo,
            fecha_desde=self.hoy + timedelta(days=2),
            fecha_hasta=self.hoy + timedelta(days=2),
            todo_el_dia=False,
            hora_inicio=time(10, 0),
            hora_fin=time(10, 45),
            motivo="Bloqueo parcial",
        )
        self.bloquear_dias(self.hoy + timedelta(days=3), self.hoy + timedelta(days=3))

        rango = obtener_rango_reserva_publica(ahora)
        contexto = cargar_contexto_agenda(
            odontologo=self.odontologo,
            fecha_desde=rango.fecha_minima,
            fecha_hasta=rango.fecha_maxima,
            rango=rango,
        )
        dias = (rango.fecha_maxima - rango.fecha_minima).days + 1
        self.assertGreater(dias, 7)
        for desplazamiento in range(dias):
            fecha = rango.fecha_minima + timedelta(days=desplazamiento)
            with self.subTest(fecha=fecha.isoformat()):
                en_bloque = calcular_horarios_del_dia(contexto, fecha, 45, 15)
                por_dia = calcular_horarios_inteligentes(
                    odontologo=self.odontologo,
                    fecha=fecha,
                    duracion_atencion_minutos=45,
                    margen_posterior_minutos=15,
                    ahora=ahora,
                )
                self.assertEqual(en_bloque, por_dia)

        primer_dia = calcular_horarios_del_dia(contexto, rango.fecha_minima, 45, 15)
        self.assertTrue(primer_dia.todos)
        self.assertTrue(all(item.hora_inicio >= time(12, 0) for item in primer_dia.todos))

    def test_sin_organizar_la_agenda_se_ofrece_la_grilla_sin_sugeridos(self):
        self.configuracion_agenda.evitar_huecos = False
        self.configuracion_agenda.save()

        contenido = self.consultar_json(self.fecha)
        opciones = contenido["horarios_manana"] + contenido["horarios_tarde"]
        etiquetas = [opcion["label"] for opcion in opciones]

        # 09:15 y 12:15 dejarían un fragmento de 15 minutos: con la organización encendida no
        # se ofrecen, apagada sí.
        self.assertIn("09:15", etiquetas)
        self.assertIn("12:15", etiquetas)
        self.assertEqual(etiquetas, sorted(etiquetas))
        self.assertFalse(contenido["hay_sugeridos"])
        self.assertFalse(any(opcion["sugerido"] for opcion in opciones))
        self.assertNotContains(
            self.consultar_html(self.fecha), "Horario sugerido por el consultorio"
        )

        with self.captureOnCommitCallbacks(execute=False):
            turno = crear_solicitud_publica_de_turno(
                {
                    "nombre": "Lucía",
                    "apellido": "Grilla",
                    "telefono": "2604000000",
                    "documento": "88111555",
                    "email": "lucia.grilla@example.test",
                    "odontologo": self.odontologo,
                    "tipo_turno": self.tipo,
                    "fecha": self.fecha,
                    "hora_inicio": time(9, 15),
                    "motivo": "",
                }
            ).turno
        self.assertEqual(turno.hora_inicio, time(9, 15))
        self.assertEqual(turno.clasificacion_horario, Turno.ClasificacionHorario.ALTERNATIVO)
        self.assertEqual(turno.algoritmo_horario_version, "smart-v2")

    def test_organizacion_encendida_es_el_valor_por_defecto(self):
        self.assertTrue(ConfiguracionAgendaInteligente().evitar_huecos)
        contenido = self.consultar_json(self.fecha)
        opciones = contenido["horarios_manana"] + contenido["horarios_tarde"]
        self.assertNotIn("09:15", [opcion["label"] for opcion in opciones])
        self.assertTrue(contenido["hay_sugeridos"])


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

    def crear_usuario_con_rol(self, username, rol):
        usuario = get_user_model().objects.create_user(username, password="clave")
        grupo, _ = Group.objects.get_or_create(name=rol)
        usuario.groups.add(grupo)
        return usuario

    def url_agenda(self, odontologo):
        return reverse("turnos:configuracion_agenda_editar", args=[odontologo.pk])

    def test_odontologo_ve_solo_el_interruptor_de_la_agenda(self):
        self.client.force_login(self.usuario)

        response = self.client.get(self.url_agenda(self.odontologo))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.context["form"].fields), {"evitar_huecos"})
        self.assertContains(response, "Organizar turnos para evitar huecos")
        self.assertNotContains(response, "Intervalo de la grilla")
        self.assertNotContains(response, "Hueco mínimo utilizable")
        self.assertNotContains(response, "Modo de compactación")

    def test_post_manual_del_odontologo_con_campos_de_administracion_se_ignora(self):
        self.client.force_login(self.usuario)
        url = self.url_agenda(self.odontologo)
        campos_de_administracion = {
            "activa": "on",
            "intervalo_inicio_minutos": 30,
            "hueco_minimo_util_minutos": 60,
            "cantidad_horarios_recomendados": 8,
            "preservar_bloques_largos": "",
            "bloque_largo_minutos": 240,
            "modo_compactacion": "final",
        }

        # Con el interruptor encendido: se guarda, y nada más cambia.
        response = self.client.post(url, {"evitar_huecos": "on", **campos_de_administracion})
        self.assertEqual(response.status_code, 302)
        # Con el interruptor apagado: solo ese campo cambia, y `activa` no se puede apagar
        # omitiéndolo.
        response = self.client.post(url, campos_de_administracion)
        self.assertEqual(response.status_code, 302)

        configuracion = ConfiguracionAgendaInteligente.objects.get(odontologo=self.odontologo)
        self.assertFalse(configuracion.evitar_huecos)
        self.assertTrue(configuracion.activa)
        self.assertEqual(configuracion.intervalo_inicio_minutos, 15)
        self.assertEqual(configuracion.hueco_minimo_util_minutos, 30)
        self.assertEqual(configuracion.cantidad_horarios_recomendados, 4)
        self.assertTrue(configuracion.preservar_bloques_largos)
        self.assertEqual(configuracion.bloque_largo_minutos, 90)
        self.assertEqual(
            configuracion.modo_compactacion,
            ConfiguracionAgendaInteligente.ModoCompactacion.EQUILIBRADO,
        )

    def test_odontologo_no_edita_la_agenda_ni_los_servicios_de_otro(self):
        ajena = TipoTurnoOdontologo.objects.create(
            odontologo=self.otro_odontologo,
            tipo_turno=self.tipo,
            duracion_atencion_minutos=30,
        )
        self.client.force_login(self.usuario)

        for url in (
            self.url_agenda(self.otro_odontologo),
            reverse("turnos:servicio_odontologo_editar", args=[ajena.pk]),
            reverse("turnos:servicio_odontologo_crear", args=[self.otro_odontologo.pk]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(
                    self.client.post(
                        url, {"evitar_huecos": "", "duracion_atencion_minutos": 90}
                    ).status_code,
                    403,
                )
        ajena.refresh_from_db()
        self.assertEqual(ajena.duracion_atencion_minutos, 30)
        self.assertFalse(
            ConfiguracionAgendaInteligente.objects.filter(odontologo=self.otro_odontologo).exists()
        )

    def test_recepcion_no_puede_editar_la_configuracion(self):
        recepcion = self.crear_usuario_con_rol("recepcion.config", "Recepcionista")
        self.client.force_login(recepcion)
        url = self.url_agenda(self.odontologo)

        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"evitar_huecos": ""}).status_code, 403)
        self.assertEqual(
            self.client.get(reverse("turnos:configuracion_servicios")).status_code, 200
        )
        self.assertFalse(
            ConfiguracionAgendaInteligente.objects.filter(odontologo=self.odontologo).exists()
        )

    def test_administracion_ve_y_guarda_todos_los_campos_de_la_agenda(self):
        administrador = self.crear_usuario_con_rol("admin.config", "Administrador")
        self.client.force_login(administrador)
        url = self.url_agenda(self.odontologo)

        response = self.client.get(url)
        self.assertEqual(
            set(response.context["form"].fields),
            {
                "activa",
                "evitar_huecos",
                "intervalo_inicio_minutos",
                "hueco_minimo_util_minutos",
                "cantidad_horarios_recomendados",
                "preservar_bloques_largos",
                "bloque_largo_minutos",
                "modo_compactacion",
            },
        )
        response = self.client.post(
            url,
            {
                "activa": "on",
                "evitar_huecos": "on",
                "intervalo_inicio_minutos": 30,
                "hueco_minimo_util_minutos": 20,
                "cantidad_horarios_recomendados": 3,
                "preservar_bloques_largos": "on",
                "bloque_largo_minutos": 120,
                "modo_compactacion": "inicio",
            },
        )

        self.assertEqual(response.status_code, 302)
        configuracion = ConfiguracionAgendaInteligente.objects.get(odontologo=self.odontologo)
        self.assertEqual(configuracion.intervalo_inicio_minutos, 30)
        self.assertEqual(configuracion.hueco_minimo_util_minutos, 20)
        self.assertEqual(configuracion.cantidad_horarios_recomendados, 3)
        self.assertEqual(configuracion.bloque_largo_minutos, 120)
        self.assertEqual(configuracion.modo_compactacion, "inicio")

    def test_odontologo_edita_solo_la_duracion_y_la_reserva_online_de_su_servicio(self):
        TipoTurnoOdontologo.objects.filter(pk=self.configuracion.pk).update(
            margen_posterior_minutos=10
        )
        self.client.force_login(self.usuario)
        url = reverse("turnos:servicio_odontologo_editar", args=[self.configuracion.pk])

        response = self.client.get(url)
        self.assertEqual(
            set(response.context["form"].fields),
            {"tipo_turno", "duracion_atencion_minutos", "reserva_publica"},
        )
        # `activo` y el margen no se aceptan por POST manual: ni se cambian ni se apagan
        # por omisión.
        response = self.client.post(
            url,
            {
                "tipo_turno": self.tipo.pk,
                "duracion_atencion_minutos": 45,
                "reserva_publica": "on",
                "margen_posterior_minutos": 30,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.configuracion.refresh_from_db()
        self.assertEqual(self.configuracion.duracion_atencion_minutos, 45)
        self.assertEqual(self.configuracion.margen_posterior_minutos, 10)
        self.assertTrue(self.configuracion.activo)
        self.assertTrue(self.configuracion.reserva_publica)

    def test_servicio_nuevo_del_odontologo_queda_sin_margen_operativo(self):
        otro_tipo = crear_tipo("Limpieza", "limpieza-permisos")
        self.client.force_login(self.usuario)

        response = self.client.post(
            reverse("turnos:servicio_odontologo_crear", args=[self.odontologo.pk]),
            {
                "tipo_turno": otro_tipo.pk,
                "duracion_atencion_minutos": 20,
                "reserva_publica": "on",
                "margen_posterior_minutos": 30,
                "activo": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        creado = TipoTurnoOdontologo.objects.get(odontologo=self.odontologo, tipo_turno=otro_tipo)
        self.assertEqual(creado.duracion_atencion_minutos, 20)
        self.assertEqual(creado.margen_posterior_minutos, 0)
        self.assertTrue(creado.activo)

    def test_administracion_ve_el_margen_y_el_estado_de_los_servicios(self):
        administrador = self.crear_usuario_con_rol("admin.servicio", "Administrador")
        self.client.force_login(administrador)

        response = self.client.get(
            reverse("turnos:servicio_odontologo_editar", args=[self.configuracion.pk])
        )

        self.assertEqual(
            set(response.context["form"].fields),
            {
                "tipo_turno",
                "duracion_atencion_minutos",
                "margen_posterior_minutos",
                "activo",
                "reserva_publica",
            },
        )

    def test_pantalla_de_configuracion_es_simple_para_el_odontologo(self):
        url = reverse("turnos:configuracion_servicios")
        ConfiguracionAgendaInteligente.objects.create(odontologo=self.odontologo)

        self.client.force_login(self.usuario)
        simple = self.client.get(url)
        self.assertContains(simple, "Organizar turnos para evitar huecos")
        for texto in ("Total bloqueado", "Grilla", "Hueco mínimo"):
            self.assertNotContains(simple, texto)

        administrador = self.crear_usuario_con_rol("admin.pantalla", "Administrador")
        self.client.force_login(administrador)
        tecnica = self.client.get(url, {"odontologo": self.odontologo.pk})
        for texto in ("Total bloqueado", "Organizar turnos para evitar huecos"):
            self.assertContains(tecnica, texto)
        self.assertContains(tecnica, "Grilla")


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
                crear_solicitud_publica_de_turno(self._datos_publicos(indice, odontologo, tipo))
                return "creada"
            except ValidationError:
                return "rechazada"
            finally:
                close_old_connections()

        with (
            patch("turnos.solicitudes_publicas.services._notificar_solicitud") as notificar,
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
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
        self.assertEqual(SolicitudTurnoPublica.objects.count(), 1)
        notificar.assert_called_once_with(SolicitudTurnoPublica.objects.get().pk)

    def test_dos_reservas_simultaneas_en_un_horario_anclado_no_se_superponen(self):
        # Un turno interno de 80 minutos termina 10:20, fuera de la grilla. En el hueco
        # 10:20-11:00 un servicio de 30 minutos solo se ofrece anclado al borde (fallback).
        Turno.objects.create(
            paciente=crear_paciente(50),
            odontologo=self.odontologo,
            fecha=self.fecha,
            hora_inicio=time(9, 0),
            duracion_minutos=80,
            estado=Turno.Estado.CONFIRMADO,
        )
        resultado = calcular_horarios_inteligentes(
            odontologo=self.odontologo,
            fecha=self.fecha,
            duracion_atencion_minutos=30,
        )
        self.assertEqual([item.hora_inicio for item in resultado.todos], [time(10, 20)])

        barrera = Barrier(2)

        def reservar(indice):
            close_old_connections()
            odontologo = Odontologo.objects.get(pk=self.odontologo.pk)
            tipo = TipoTurno.objects.get(pk=self.tipo.pk)
            barrera.wait(timeout=5)
            try:
                crear_solicitud_publica_de_turno(
                    self._datos_publicos(indice, odontologo, tipo, hora_inicio=time(10, 20))
                )
                return "creada"
            except ValidationError:
                return "rechazada"
            finally:
                close_old_connections()

        with (
            patch("turnos.solicitudes_publicas.services._notificar_solicitud"),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            resultados = list(executor.map(reservar, (1, 2)))

        self.assertEqual(sorted(resultados), ["creada", "rechazada"])
        self.assertEqual(
            Turno.objects.filter(
                odontologo=self.odontologo,
                fecha=self.fecha,
                hora_inicio=time(10, 20),
            ).count(),
            1,
        )

    def test_doble_click_identico_reutiliza_solicitud(self):
        barrera = Barrier(2)

        def reservar(_indice):
            close_old_connections()
            odontologo = Odontologo.objects.get(pk=self.odontologo.pk)
            tipo = TipoTurno.objects.get(pk=self.tipo.pk)
            barrera.wait(timeout=10)
            try:
                resultado = crear_solicitud_publica_de_turno(
                    self._datos_publicos(
                        3,
                        odontologo,
                        tipo,
                        documento="99111223",
                    )
                )
                return resultado.turno.pk, resultado.duplicada
            finally:
                close_old_connections()

        with (
            patch("turnos.solicitudes_publicas.services._notificar_solicitud") as notificar,
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            resultados = list(executor.map(reservar, (1, 2)))

        self.assertEqual({resultado[0] for resultado in resultados}, {resultados[0][0]})
        self.assertEqual(sorted(resultado[1] for resultado in resultados), [False, True])
        self.assertEqual(Turno.objects.count(), 1)
        self.assertEqual(SolicitudTurnoPublica.objects.count(), 1)
        notificar.assert_called_once_with(SolicitudTurnoPublica.objects.get().pk)

    def test_mismo_paciente_y_horario_con_payload_distinto_no_deduplica(self):
        barrera = Barrier(2)

        def reservar(indice):
            close_old_connections()
            odontologo = Odontologo.objects.get(pk=self.odontologo.pk)
            tipo = TipoTurno.objects.get(pk=self.tipo.pk)
            barrera.wait(timeout=10)
            try:
                resultado = crear_solicitud_publica_de_turno(
                    self._datos_publicos(
                        indice,
                        odontologo,
                        tipo,
                        documento="99111224",
                    )
                )
                return resultado.duplicada
            except ValidationError:
                return "rechazada"
            finally:
                close_old_connections()

        with (
            patch("turnos.solicitudes_publicas.services._notificar_solicitud") as notificar,
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            resultados = list(executor.map(reservar, (4, 5)))

        self.assertEqual(resultados.count(False), 1)
        self.assertEqual(resultados.count("rechazada"), 1)
        self.assertNotIn(True, resultados)
        self.assertEqual(Turno.objects.count(), 1)
        self.assertEqual(SolicitudTurnoPublica.objects.count(), 1)
        notificar.assert_called_once_with(SolicitudTurnoPublica.objects.get().pk)

    def _datos_publicos(self, indice, odontologo, tipo, *, documento=None, hora_inicio=time(9, 0)):
        return {
            "nombre": "Paciente",
            "apellido": f"Concurrente {indice}",
            "telefono": "2604000000",
            "documento": documento or f"9911122{indice}",
            "email": f"concurrente.{indice}@example.test",
            "odontologo": odontologo,
            "tipo_turno": tipo,
            "fecha": self.fecha,
            "hora_inicio": hora_inicio,
            "motivo": "",
        }
