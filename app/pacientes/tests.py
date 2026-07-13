from datetime import date, datetime, time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from historias.models import HistoriaClinica, HistoriaClinicaAdjunto
from turnos.models import DisponibilidadOdontologo, Odontologo, SolicitudTurnoPublica, Turno
from usuarios.roles import ROL_ADMINISTRADOR, ROL_ODONTOLOGO, ROL_RECEPCIONISTA

from .models import FichaOdontologica, Paciente, PacienteOdontologo


def asignar_rol(usuario, nombre_rol):
    grupo, _ = Group.objects.get_or_create(name=nombre_rol)
    usuario.groups.add(grupo)


TEST_STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.InMemoryStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}


class LoginInternoTests(TestCase):
    def test_login_responde_correctamente(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ingresar")

    def test_login_redirige_al_panel_con_credenciales_validas(self):
        usuario = get_user_model().objects.create_user(
            username="recepcion",
            password="Password123!",
        )
        asignar_rol(usuario, ROL_RECEPCIONISTA)

        response = self.client.post(
            reverse("login"),
            {
                "username": "recepcion",
                "password": "Password123!",
            },
        )

        self.assertRedirects(response, reverse("inicio"))

    def test_logout_redirige_al_login(self):
        usuario = get_user_model().objects.create_user(
            username="recepcion",
            password="Password123!",
        )
        self.client.force_login(usuario)

        response = self.client.post(reverse("logout"))

        self.assertRedirects(response, reverse("login"))


class PacienteAccessTests(TestCase):
    def test_listado_requiere_login(self):
        response = self.client.get(reverse("pacientes:lista"))

        self.assertRedirects(response, f"{reverse('login')}?next={reverse('pacientes:lista')}")

    def test_borrado_requiere_login(self):
        url = reverse("pacientes:borrar", kwargs={"pk": 1})

        response = self.client.get(url)

        self.assertRedirects(response, f"{reverse('login')}?next={url}")

    def test_odontologo_puede_ver_pacientes_sin_crear_ni_editar(self):
        usuario = get_user_model().objects.create_user(username="dr.pacientes")
        asignar_rol(usuario, ROL_ODONTOLOGO)
        Odontologo.objects.create(usuario=usuario, matricula="MN-PAC")
        paciente = Paciente.objects.create(nombre="Eva", apellido="Ramos", documento="10111222")
        self.client.force_login(usuario)

        response_lista = self.client.get(reverse("pacientes:lista"))
        response_crear = self.client.get(reverse("pacientes:crear"))
        response_editar = self.client.get(reverse("pacientes:editar", kwargs={"pk": paciente.pk}))

        self.assertEqual(response_lista.status_code, 200)
        self.assertNotContains(response_lista, "Nuevo paciente")
        self.assertEqual(response_crear.status_code, 403)
        self.assertEqual(response_editar.status_code, 403)

    def test_odontologo_lista_solo_pacientes_asociados(self):
        usuario = get_user_model().objects.create_user(username="dr.scope.pacientes")
        asignar_rol(usuario, ROL_ODONTOLOGO)
        odontologo = Odontologo.objects.create(usuario=usuario, matricula="MN-SCOPE")
        paciente_asociado = Paciente.objects.create(
            nombre="Paciente",
            apellido="Asociado",
            documento="20111222",
        )
        Paciente.objects.create(
            nombre="Paciente",
            apellido="Externo",
            documento="20111223",
        )
        paciente_asociacion_inactiva = Paciente.objects.create(
            nombre="Paciente",
            apellido="Inactivo",
            documento="20111226",
        )
        PacienteOdontologo.objects.create(
            paciente=paciente_asociado,
            odontologo=odontologo,
            motivo="Asignacion inicial",
        )
        PacienteOdontologo.objects.create(
            paciente=paciente_asociacion_inactiva,
            odontologo=odontologo,
            activo=False,
            motivo="Relacion cerrada",
        )
        self.client.force_login(usuario)

        response = self.client.get(reverse("pacientes:lista"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Asociado")
        self.assertNotContains(response, "Externo")
        self.assertNotContains(response, "Inactivo")

    def test_derivar_paciente_crea_asociacion_para_odontologo_destino(self):
        usuario_origen = get_user_model().objects.create_user(username="dr.origen")
        usuario_destino = get_user_model().objects.create_user(username="dr.destino")
        asignar_rol(usuario_origen, ROL_ODONTOLOGO)
        asignar_rol(usuario_destino, ROL_ODONTOLOGO)
        odontologo_origen = Odontologo.objects.create(
            usuario=usuario_origen,
            matricula="MN-ORIGEN",
        )
        odontologo_destino = Odontologo.objects.create(
            usuario=usuario_destino,
            matricula="MN-DESTINO",
        )
        paciente = Paciente.objects.create(
            nombre="Derivado",
            apellido="Clinico",
            documento="20111224",
        )
        PacienteOdontologo.objects.create(
            paciente=paciente,
            odontologo=odontologo_origen,
            motivo="Paciente propio",
        )
        self.client.force_login(usuario_origen)

        response = self.client.post(
            reverse("pacientes:derivar", kwargs={"pk": paciente.pk}),
            {
                "odontologo": odontologo_destino.pk,
                "motivo": "Interconsulta",
            },
        )

        self.assertRedirects(response, reverse("pacientes:detalle", kwargs={"pk": paciente.pk}))
        self.assertTrue(
            PacienteOdontologo.objects.filter(
                paciente=paciente,
                odontologo=odontologo_destino,
                activo=True,
                motivo="Interconsulta",
            ).exists()
        )

        self.client.force_login(usuario_destino)
        response_lista_destino = self.client.get(reverse("pacientes:lista"))

        self.assertContains(response_lista_destino, "Clinico")

    def test_odontologo_asociado_abre_detalle_y_ficha(self):
        usuario = get_user_model().objects.create_user(username="dr.asociado.paciente")
        asignar_rol(usuario, ROL_ODONTOLOGO)
        odontologo = Odontologo.objects.create(usuario=usuario, matricula="MN-ASOC-PAC")
        paciente = Paciente.objects.create(
            nombre="Paciente",
            apellido="Visible",
            documento="20111227",
        )
        PacienteOdontologo.objects.create(
            paciente=paciente,
            odontologo=odontologo,
            motivo="Tratamiento activo",
        )
        self.client.force_login(usuario)

        response_detalle = self.client.get(reverse("pacientes:detalle", kwargs={"pk": paciente.pk}))
        response_ficha = self.client.get(
            reverse("pacientes:ficha_odontologica", kwargs={"pk": paciente.pk})
        )

        self.assertEqual(response_detalle.status_code, 200)
        self.assertContains(response_detalle, "Visible")
        self.assertEqual(response_ficha.status_code, 200)

    @override_settings(STORAGES=TEST_STORAGES)
    def test_odontologo_no_asociado_recibe_404_y_no_modifica_datos_por_url(self):
        usuario_duenio = get_user_model().objects.create_user(username="dr.duenio.scope")
        usuario_atacante = get_user_model().objects.create_user(username="dr.atacante.scope")
        asignar_rol(usuario_duenio, ROL_ODONTOLOGO)
        asignar_rol(usuario_atacante, ROL_ODONTOLOGO)
        odontologo_duenio = Odontologo.objects.create(
            usuario=usuario_duenio,
            matricula="MN-DUENIO-SCOPE",
        )
        odontologo_atacante = Odontologo.objects.create(
            usuario=usuario_atacante,
            matricula="MN-ATACANTE-SCOPE",
        )
        paciente = Paciente.objects.create(
            nombre="Secreto",
            apellido="Paciente",
            documento="99111222",
            telefono="11112222",
            email="secreto@example.com",
        )
        PacienteOdontologo.objects.create(
            paciente=paciente,
            odontologo=odontologo_duenio,
            motivo="Profesional tratante",
        )
        historia = HistoriaClinica.objects.create(
            paciente=paciente,
            odontologo=odontologo_duenio,
            fecha=date(2026, 5, 8),
            motivo_consulta="Dato clinico privado",
        )
        adjunto = HistoriaClinicaAdjunto.objects.create(
            historia=historia,
            archivo=SimpleUploadedFile("privado.pdf", b"contenido", content_type="application/pdf"),
        )
        turno = Turno.objects.create(
            paciente=paciente,
            odontologo=odontologo_duenio,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CANCELADO,
        )
        self.client.force_login(usuario_atacante)

        response_detalle = self.client.get(reverse("pacientes:detalle", kwargs={"pk": paciente.pk}))
        response_ficha_get = self.client.get(
            reverse("pacientes:ficha_odontologica", kwargs={"pk": paciente.pk})
        )
        response_ficha_post = self.client.post(
            reverse("pacientes:ficha_odontologica", kwargs={"pk": paciente.pk}),
            self._datos_ficha_post(paciente, telefono="99999999"),
        )
        response_derivar_post = self.client.post(
            reverse("pacientes:derivar", kwargs={"pk": paciente.pk}),
            {
                "odontologo": odontologo_atacante.pk,
                "motivo": "Acceso no autorizado",
            },
        )
        response_derivar_get = self.client.get(
            reverse("pacientes:derivar", kwargs={"pk": paciente.pk})
        )
        response_borrar_get = self.client.get(
            reverse("pacientes:borrar", kwargs={"pk": paciente.pk})
        )
        response_borrar_post = self.client.post(
            reverse("pacientes:borrar", kwargs={"pk": paciente.pk}),
            {
                "nombre": "Secreto",
                "apellido": "Paciente",
                "documento": "99111222",
                "confirmacion_clinica": "CONFIRMAR",
            },
        )

        self.assertEqual(response_detalle.status_code, 404)
        self.assertNotContains(response_detalle, "Secreto", status_code=404)
        self.assertNotContains(response_detalle, "99111222", status_code=404)
        self.assertNotContains(response_detalle, "secreto@example.com", status_code=404)
        self.assertEqual(response_ficha_get.status_code, 404)
        self.assertEqual(response_ficha_post.status_code, 404)
        self.assertEqual(response_derivar_get.status_code, 404)
        self.assertEqual(response_derivar_post.status_code, 404)
        self.assertEqual(response_borrar_get.status_code, 403)
        self.assertEqual(response_borrar_post.status_code, 403)

        paciente.refresh_from_db()
        self.assertEqual(paciente.telefono, "11112222")
        self.assertFalse(FichaOdontologica.objects.filter(paciente=paciente).exists())
        self.assertFalse(
            PacienteOdontologo.objects.filter(
                paciente=paciente,
                odontologo=odontologo_atacante,
            ).exists()
        )
        self.assertTrue(Paciente.objects.filter(pk=paciente.pk).exists())
        self.assertTrue(Turno.objects.filter(pk=turno.pk).exists())
        self.assertTrue(HistoriaClinica.objects.filter(pk=historia.pk).exists())
        self.assertTrue(HistoriaClinicaAdjunto.objects.filter(pk=adjunto.pk).exists())

    @staticmethod
    def _datos_ficha_post(paciente, telefono):
        return {
            "paciente-nombre": paciente.nombre,
            "paciente-apellido": paciente.apellido,
            "paciente-documento": paciente.documento,
            "paciente-telefono": telefono,
            "paciente-email": paciente.email,
            "paciente-fecha_nacimiento": "",
            "paciente-genero": "",
            "paciente-domicilio": "",
            "paciente-localidad": "",
            "paciente-obra_social": "",
            "paciente-numero_afiliado": "",
            "paciente-contacto_emergencia": "",
            "paciente-observaciones": "",
            "ficha-antecedentes_medicos": "Dato no autorizado",
            "ficha-alergias": "Latex",
            "ficha-medicacion_actual": "",
            "ficha-enfermedades_relevantes": "",
            "ficha-embarazo": "",
            "ficha-hipertension": "",
            "ficha-diabetes": "",
            "ficha-problemas_cardiacos": "",
            "ficha-observaciones_generales": "",
        }


class PacienteViewsTests(TestCase):
    def setUp(self):
        self.usuario = get_user_model().objects.create_user(
            username="usuario.pacientes",
            password="Password123!",
        )
        asignar_rol(self.usuario, ROL_RECEPCIONISTA)
        self.client.force_login(self.usuario)

    def test_listado_muestra_pacientes(self):
        Paciente.objects.create(
            nombre="Ana",
            apellido="Gomez",
            documento="12345678",
            telefono="1122334455",
            email="ana@example.com",
        )

        response = self.client.get(reverse("pacientes:lista"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Gomez")
        self.assertContains(response, "12345678")

    def test_listado_filtra_por_busqueda(self):
        Paciente.objects.create(nombre="Ana", apellido="Gomez", documento="12345678")
        Paciente.objects.create(nombre="Luis", apellido="Perez", documento="87654321")

        response = self.client.get(reverse("pacientes:lista"), {"q": "Perez"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Perez")
        self.assertNotContains(response, "Gomez")

    def test_creacion_de_paciente_valido(self):
        response = self.client.post(
            reverse("pacientes:crear"),
            {
                "nombre": "Carla",
                "apellido": "Lopez",
                "documento": "30111222",
                "telefono": "1155667788",
                "email": "carla@example.com",
                "fecha_nacimiento": "",
                "observaciones": "",
            },
        )

        self.assertRedirects(response, reverse("pacientes:lista"))
        self.assertTrue(Paciente.objects.filter(documento="30111222").exists())

    def test_creacion_permite_mas_de_un_paciente_sin_documento(self):
        datos_base = {
            "telefono": "",
            "email": "",
            "fecha_nacimiento": "",
            "observaciones": "",
        }

        primera_respuesta = self.client.post(
            reverse("pacientes:crear"),
            {
                **datos_base,
                "nombre": "Mario",
                "apellido": "Sosa",
                "documento": "",
            },
        )
        segunda_respuesta = self.client.post(
            reverse("pacientes:crear"),
            {
                **datos_base,
                "nombre": "Laura",
                "apellido": "Diaz",
                "documento": "",
            },
        )

        self.assertRedirects(primera_respuesta, reverse("pacientes:lista"))
        self.assertRedirects(segunda_respuesta, reverse("pacientes:lista"))
        self.assertEqual(Paciente.objects.filter(documento__isnull=True).count(), 2)

    def test_detalle_muestra_datos_del_paciente(self):
        paciente = Paciente.objects.create(
            nombre="Elena",
            apellido="Rios",
            documento="20111222",
            telefono="1144556677",
            email="elena@example.com",
        )

        response = self.client.get(reverse("pacientes:detalle", kwargs={"pk": paciente.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Elena")
        self.assertContains(response, "20111222")

    def test_detalle_muestra_solamente_el_ultimo_turno_pasado_no_cancelado(self):
        paciente = Paciente.objects.create(
            nombre="Elena",
            apellido="Ultimo turno",
            documento="20111228",
        )
        odontologo = self._crear_odontologo_para_paciente(paciente, "ULTIMO")
        Turno.objects.bulk_create(
            [
                Turno(
                    paciente=paciente,
                    odontologo=odontologo,
                    fecha=date(2026, 7, 10),
                    hora_inicio=time(9, 0),
                    motivo="Pasado antiguo",
                    estado=Turno.Estado.CONFIRMADO,
                ),
                Turno(
                    paciente=paciente,
                    odontologo=odontologo,
                    fecha=date(2026, 7, 13),
                    hora_inicio=time(11, 30),
                    motivo="Control pasado reciente",
                    estado=Turno.Estado.PENDIENTE,
                ),
                Turno(
                    paciente=paciente,
                    odontologo=odontologo,
                    fecha=date(2026, 7, 13),
                    hora_inicio=time(11, 45),
                    motivo="Pasado cancelado",
                    estado=Turno.Estado.CANCELADO,
                ),
                Turno(
                    paciente=paciente,
                    odontologo=odontologo,
                    fecha=date(2026, 7, 13),
                    hora_inicio=time(12, 30),
                    motivo="Turno futuro",
                    estado=Turno.Estado.CONFIRMADO,
                ),
            ]
        )
        turno_esperado = Turno.objects.get(motivo="Control pasado reciente")
        SolicitudTurnoPublica.objects.create(
            turno=turno_esperado,
            paciente=paciente,
            documento_enviado=paciente.documento,
            nombre_enviado=paciente.nombre,
            apellido_enviado=paciente.apellido,
            telefono_enviado="11112222",
        )
        momento_local = timezone.make_aware(datetime(2026, 7, 13, 12, 0))

        with (
            patch("pacientes.views.timezone.localtime", return_value=momento_local),
            patch("pacientes.views.timezone.localdate", return_value=momento_local.date()),
        ):
            response = self.client.get(reverse("pacientes:detalle", kwargs={"pk": paciente.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["ultimo_turno"].pk, turno_esperado.pk)
        self.assertNotIn("turnos_recientes", response.context)
        self.assertContains(response, "Último turno")
        self.assertNotContains(response, "Turnos recientes")
        self.assertContains(response, "13 de julio de 2026")
        self.assertContains(response, "11:30 a 12:00")
        self.assertContains(response, "Control pasado reciente")
        self.assertContains(response, str(odontologo))
        self.assertContains(response, "Pendiente")
        self.assertContains(response, "Solicitud web")
        self.assertContains(response, "Ver turno")
        self.assertContains(response, 'class="clinical-last-turn"', count=1)
        self.assertNotContains(response, "Pasado antiguo")
        self.assertNotContains(response, "Pasado cancelado")

        ultimo_turno = response.context["ultimo_turno"]
        with self.assertNumQueries(0):
            str(ultimo_turno.odontologo.usuario)
            self.assertTrue(ultimo_turno.tiene_solicitud_publica)

    def test_detalle_muestra_estado_vacio_sin_turnos_anteriores_validos(self):
        paciente = Paciente.objects.create(
            nombre="Elena",
            apellido="Sin anteriores",
            documento="20111229",
        )
        odontologo = self._crear_odontologo_para_paciente(paciente, "VACIO")
        Turno.objects.bulk_create(
            [
                Turno(
                    paciente=paciente,
                    odontologo=odontologo,
                    fecha=date(2026, 7, 12),
                    hora_inicio=time(9, 0),
                    motivo="Cancelado anterior",
                    estado=Turno.Estado.CANCELADO,
                ),
                Turno(
                    paciente=paciente,
                    odontologo=odontologo,
                    fecha=date(2026, 7, 14),
                    hora_inicio=time(9, 0),
                    motivo="Próximo control",
                    estado=Turno.Estado.CONFIRMADO,
                ),
            ]
        )
        momento_local = timezone.make_aware(datetime(2026, 7, 13, 12, 0))

        with (
            patch("pacientes.views.timezone.localtime", return_value=momento_local),
            patch("pacientes.views.timezone.localdate", return_value=momento_local.date()),
        ):
            response = self.client.get(reverse("pacientes:detalle", kwargs={"pk": paciente.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["ultimo_turno"])
        self.assertContains(response, "Último turno")
        self.assertContains(response, "Sin turnos anteriores")
        self.assertContains(
            response,
            "Este paciente todavía no tiene turnos anteriores registrados.",
        )
        self.assertNotContains(response, "Turnos recientes")

    def test_detalle_no_muestra_edicion_separada_de_paciente(self):
        paciente = Paciente.objects.create(
            nombre="Elena",
            apellido="Rios",
            documento="20111225",
        )

        response = self.client.get(reverse("pacientes:detalle", kwargs={"pk": paciente.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Información clínica restringida")
        self.assertNotContains(response, "Odontograma")
        self.assertNotContains(response, "Editar paciente")
        self.assertNotContains(
            response,
            reverse("pacientes:editar", kwargs={"pk": paciente.pk}),
        )

    def test_detalle_muestra_perfil_clinico_con_alertas_y_resumen(self):
        paciente = Paciente.objects.create(
            nombre="Elena",
            apellido="Clinica",
            documento="20111223",
            fecha_nacimiento=date(1990, 4, 15),
            telefono="1144556677",
            email="elena@example.com",
            obra_social="OSDE",
            localidad="Rosario",
        )
        odontologo = self._crear_odontologo_para_paciente(paciente, "PERFIL")
        DisponibilidadOdontologo.objects.create(
            odontologo=odontologo,
            dia_semana=DisponibilidadOdontologo.DiaSemana.VIERNES,
            hora_inicio=time(9, 0),
            hora_fin=time(18, 0),
        )
        FichaOdontologica.objects.create(
            paciente=paciente,
            alergias="Penicilina",
            medicacion_actual="Losartan",
            diabetes=FichaOdontologica.RespuestaClinica.SI,
            hipertension=FichaOdontologica.RespuestaClinica.NO,
        )
        Turno.objects.create(
            paciente=paciente,
            odontologo=odontologo,
            fecha=date(2026, 5, 15),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
            motivo="Control",
        )
        HistoriaClinica.objects.create(
            paciente=paciente,
            odontologo=odontologo,
            fecha=date(2026, 5, 8),
            motivo_consulta="Dolor molar",
            diagnostico="Caries",
        )
        asignar_rol(odontologo.usuario, ROL_ODONTOLOGO)
        self.client.force_login(odontologo.usuario)

        response = self.client.get(reverse("pacientes:detalle", kwargs={"pk": paciente.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Perfil clínico")
        self.assertContains(response, "Alertas clínicas")
        self.assertContains(response, "Penicilina")
        self.assertContains(response, "Losartan")
        self.assertContains(response, "Diabetes")
        self.assertContains(response, "Turnos activos")
        self.assertContains(response, "Próximo turno")
        self.assertContains(response, "Historia clínica reciente")
        self.assertContains(response, "Dolor molar")

    def test_detalle_sin_ficha_muestra_alertas_vacias(self):
        paciente = Paciente.objects.create(
            nombre="Elena",
            apellido="Sin ficha",
            documento="20111224",
        )

        response = self.client.get(reverse("pacientes:detalle", kwargs={"pk": paciente.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Información clínica restringida")
        self.assertContains(response, "Sin próximo turno")

    def test_edicion_actualiza_paciente(self):
        paciente = Paciente.objects.create(
            nombre="Sofia",
            apellido="Mendez",
            documento="22111222",
            telefono="1111",
        )

        response = self.client.post(
            reverse("pacientes:editar", kwargs={"pk": paciente.pk}),
            {
                "nombre": "Sofia",
                "apellido": "Mendez",
                "documento": "22111222",
                "telefono": "2222",
                "email": "sofia@example.com",
                "fecha_nacimiento": "",
                "genero": Paciente.Genero.FEMENINO,
                "domicilio": "Av. Siempre Viva 123",
                "localidad": "Rosario",
                "obra_social": "OSDE",
                "numero_afiliado": "A123",
                "contacto_emergencia": "Maria 3415550000",
                "observaciones": "Paciente actualizada",
            },
        )

        paciente.refresh_from_db()

        self.assertRedirects(response, reverse("pacientes:detalle", kwargs={"pk": paciente.pk}))
        self.assertEqual(paciente.telefono, "2222")
        self.assertEqual(paciente.email, "sofia@example.com")
        self.assertEqual(paciente.genero, Paciente.Genero.FEMENINO)
        self.assertEqual(paciente.domicilio, "Av. Siempre Viva 123")
        self.assertEqual(paciente.localidad, "Rosario")
        self.assertEqual(paciente.obra_social, "OSDE")
        self.assertEqual(paciente.numero_afiliado, "A123")
        self.assertEqual(paciente.contacto_emergencia, "Maria 3415550000")
        self.assertEqual(paciente.observaciones, "Paciente actualizada")

    def test_ficha_odontologica_puede_cargarse_desde_detalle(self):
        paciente = Paciente.objects.create(
            nombre="Camila",
            apellido="Clinica",
            documento="23111222",
            telefono="1111",
        )
        usuario_odontologo = self._login_odontologo_asociado(paciente, "FICHA-NUEVA")

        response = self.client.post(
            reverse("pacientes:ficha_odontologica", kwargs={"pk": paciente.pk}),
            {
                "paciente-nombre": "Camila",
                "paciente-apellido": "Clinica",
                "paciente-documento": "23111222",
                "paciente-telefono": "2222",
                "paciente-email": "camila@example.com",
                "paciente-fecha_nacimiento": "1991-06-20",
                "paciente-genero": Paciente.Genero.FEMENINO,
                "paciente-domicilio": "San Martin 123",
                "paciente-localidad": "Mendoza",
                "paciente-obra_social": "OSEP",
                "paciente-numero_afiliado": "A-456",
                "paciente-contacto_emergencia": "Laura 2604000000",
                "paciente-observaciones": "Prefiere atencion por la tarde.",
                "ficha-antecedentes_medicos": "Asma leve",
                "ficha-alergias": "Penicilina",
                "ficha-medicacion_actual": "Salbutamol",
                "ficha-enfermedades_relevantes": "Sin otras enfermedades",
                "ficha-embarazo": FichaOdontologica.RespuestaClinica.NO,
                "ficha-hipertension": FichaOdontologica.RespuestaClinica.NO,
                "ficha-diabetes": FichaOdontologica.RespuestaClinica.NO,
                "ficha-problemas_cardiacos": FichaOdontologica.RespuestaClinica.SI,
                "ficha-observaciones_generales": "Avisar antes de anestesia.",
            },
        )

        self.assertRedirects(response, reverse("pacientes:detalle", kwargs={"pk": paciente.pk}))
        paciente.refresh_from_db()
        ficha = paciente.ficha_odontologica
        self.assertEqual(paciente.telefono, "2222")
        self.assertEqual(paciente.email, "camila@example.com")
        self.assertEqual(paciente.fecha_nacimiento, date(1991, 6, 20))
        self.assertEqual(paciente.genero, Paciente.Genero.FEMENINO)
        self.assertEqual(paciente.domicilio, "San Martin 123")
        self.assertEqual(paciente.localidad, "Mendoza")
        self.assertEqual(paciente.obra_social, "OSEP")
        self.assertEqual(paciente.numero_afiliado, "A-456")
        self.assertEqual(paciente.contacto_emergencia, "Laura 2604000000")
        self.assertEqual(paciente.observaciones, "Prefiere atencion por la tarde.")
        self.assertEqual(ficha.alergias, "Penicilina")
        self.assertEqual(ficha.problemas_cardiacos, FichaOdontologica.RespuestaClinica.SI)
        self.assertEqual(ficha.actualizado_por, usuario_odontologo)

    def test_ficha_odontologica_actualiza_ficha_existente_sin_duplicar(self):
        paciente = Paciente.objects.create(
            nombre="Julieta",
            apellido="Clinica",
            documento="23111224",
            telefono="1111",
        )
        FichaOdontologica.objects.create(
            paciente=paciente,
            alergias="Latex",
        )
        self._login_odontologo_asociado(paciente, "FICHA-EDIT")

        response = self.client.post(
            reverse("pacientes:ficha_odontologica", kwargs={"pk": paciente.pk}),
            {
                "paciente-nombre": "Julieta",
                "paciente-apellido": "Clinica",
                "paciente-documento": "23111224",
                "paciente-telefono": "3333",
                "paciente-email": "julieta@example.com",
                "paciente-fecha_nacimiento": "",
                "paciente-genero": "",
                "paciente-domicilio": "",
                "paciente-localidad": "",
                "paciente-obra_social": "",
                "paciente-numero_afiliado": "",
                "paciente-contacto_emergencia": "",
                "paciente-observaciones": "",
                "ficha-antecedentes_medicos": "",
                "ficha-alergias": "Penicilina",
                "ficha-medicacion_actual": "Ibuprofeno",
                "ficha-enfermedades_relevantes": "",
                "ficha-embarazo": "",
                "ficha-hipertension": FichaOdontologica.RespuestaClinica.NO,
                "ficha-diabetes": FichaOdontologica.RespuestaClinica.NO,
                "ficha-problemas_cardiacos": "",
                "ficha-observaciones_generales": "Controlar dosis.",
            },
        )

        self.assertRedirects(response, reverse("pacientes:detalle", kwargs={"pk": paciente.pk}))
        paciente.refresh_from_db()
        ficha = paciente.ficha_odontologica
        self.assertEqual(FichaOdontologica.objects.filter(paciente=paciente).count(), 1)
        self.assertEqual(paciente.telefono, "3333")
        self.assertEqual(ficha.alergias, "Penicilina")
        self.assertEqual(ficha.medicacion_actual, "Ibuprofeno")

    def test_ficha_odontologica_devuelve_404_a_odontologo_no_asociado(self):
        paciente = Paciente.objects.create(
            nombre="Camila",
            apellido="Sin permiso",
            documento="23111223",
        )
        usuario_odontologo = get_user_model().objects.create_user(username="dr.sin.permiso")
        asignar_rol(usuario_odontologo, ROL_ODONTOLOGO)
        Odontologo.objects.create(usuario=usuario_odontologo, matricula="MN-SIN-PERMISO")
        self.client.force_login(usuario_odontologo)

        response = self.client.get(
            reverse("pacientes:ficha_odontologica", kwargs={"pk": paciente.pk}),
        )

        self.assertEqual(response.status_code, 404)

    def test_edicion_muestra_fecha_de_nacimiento_cargada(self):
        paciente = Paciente.objects.create(
            nombre="Sofia",
            apellido="Mendez",
            documento="22111223",
            fecha_nacimiento=date(1990, 4, 15),
        )

        response = self.client.get(reverse("pacientes:editar", kwargs={"pk": paciente.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="1990-04-15"')

    def test_edicion_no_permite_documento_duplicado(self):
        Paciente.objects.create(nombre="Ana", apellido="Gomez", documento="12345678")
        paciente = Paciente.objects.create(nombre="Luis", apellido="Perez", documento="87654321")

        response = self.client.post(
            reverse("pacientes:editar", kwargs={"pk": paciente.pk}),
            {
                "nombre": "Luis",
                "apellido": "Perez",
                "documento": "12345678",
                "telefono": "",
                "email": "",
                "fecha_nacimiento": "",
                "observaciones": "",
            },
        )

        paciente.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertIn("documento", response.context["form"].errors)
        self.assertEqual(paciente.documento, "87654321")

    def test_borrado_muestra_confirmacion_segura(self):
        paciente = Paciente.objects.create(
            nombre="Clara",
            apellido="Borrar",
            documento="40111222",
        )

        response = self.client.get(reverse("pacientes:borrar", kwargs={"pk": paciente.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Archivar paciente")
        self.assertContains(response, "sin eliminar turnos, ficha, historias ni adjuntos")
        self.assertContains(response, "40111222")

    def test_borrado_rechaza_datos_que_no_coinciden(self):
        paciente = Paciente.objects.create(
            nombre="Clara",
            apellido="Borrar",
            documento="40111222",
        )

        response = self.client.post(
            reverse("pacientes:borrar", kwargs={"pk": paciente.pk}),
            {
                "motivo": "Archivo administrativo de prueba",
                "confirmacion": "ARCHIVAR",
                "documento": "99999999",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Paciente.objects.filter(pk=paciente.pk).exists())
        self.assertContains(response, "El DNI ingresado no coincide")

    def test_borrado_elimina_paciente_con_confirmacion_correcta(self):
        paciente = Paciente.objects.create(
            nombre="Clara",
            apellido="Borrar",
            documento="40111222",
        )

        response = self.client.post(
            reverse("pacientes:borrar", kwargs={"pk": paciente.pk}),
            self._datos_archivo(paciente),
        )

        self.assertRedirects(response, reverse("pacientes:lista"))
        paciente.refresh_from_db()
        self.assertFalse(paciente.activo)

    def test_borrado_no_elimina_paciente_con_turno_pendiente(self):
        paciente = Paciente.objects.create(
            nombre="Clara",
            apellido="Protegida",
            documento="42111222",
        )
        self._crear_turno_para_paciente(
            paciente=paciente,
            estado=Turno.Estado.PENDIENTE,
        )

        response = self.client.post(
            reverse("pacientes:borrar", kwargs={"pk": paciente.pk}),
            self._datos_archivo(paciente),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Paciente.objects.filter(pk=paciente.pk).exists())
        self.assertContains(response, "tiene turnos pendientes o confirmados")

    def test_borrado_no_elimina_paciente_con_turno_confirmado(self):
        paciente = Paciente.objects.create(
            nombre="Clara",
            apellido="Confirmada",
            documento="45111222",
        )
        self._crear_turno_para_paciente(
            paciente=paciente,
            estado=Turno.Estado.CONFIRMADO,
        )

        response = self.client.post(
            reverse("pacientes:borrar", kwargs={"pk": paciente.pk}),
            self._datos_archivo(paciente),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Paciente.objects.filter(pk=paciente.pk).exists())
        self.assertContains(response, "tiene turnos pendientes o confirmados")

    def test_borrado_elimina_paciente_con_turnos_cancelados(self):
        paciente = Paciente.objects.create(
            nombre="Clara",
            apellido="Cancelada",
            documento="44111222",
        )
        turno = self._crear_turno_para_paciente(
            paciente=paciente,
            estado=Turno.Estado.CANCELADO,
        )

        response = self.client.post(
            reverse("pacientes:borrar", kwargs={"pk": paciente.pk}),
            self._datos_archivo(paciente),
        )

        self.assertRedirects(response, reverse("pacientes:lista"))
        paciente.refresh_from_db()
        self.assertFalse(paciente.activo)
        self.assertTrue(Turno.objects.filter(pk=turno.pk).exists())

    def test_borrado_elimina_paciente_con_turnos_cancelados_historicos(self):
        paciente = Paciente.objects.create(
            nombre="Clara",
            apellido="Historica",
            documento="46111222",
        )
        turno = self._crear_turno_para_paciente(
            paciente=paciente,
            estado=Turno.Estado.CANCELADO,
        )

        response = self.client.post(
            reverse("pacientes:borrar", kwargs={"pk": paciente.pk}),
            self._datos_archivo(paciente),
        )

        self.assertRedirects(response, reverse("pacientes:lista"))
        paciente.refresh_from_db()
        self.assertFalse(paciente.activo)
        self.assertTrue(Turno.objects.filter(pk=turno.pk).exists())

    def test_borrado_con_ficha_odontologica_exige_confirmacion_clinica(self):
        paciente = Paciente.objects.create(
            nombre="Clara",
            apellido="Ficha",
            documento="47111222",
        )
        FichaOdontologica.objects.create(
            paciente=paciente,
            alergias="Penicilina",
        )

        response_get = self.client.get(reverse("pacientes:borrar", kwargs={"pk": paciente.pk}))

        self.assertEqual(response_get.status_code, 200)
        self.assertContains(response_get, "Historias cl")
        self.assertContains(response_get, "ARCHIVAR")

        response_post = self.client.post(
            reverse("pacientes:borrar", kwargs={"pk": paciente.pk}),
            {
                "motivo": "Archivo administrativo de prueba",
                "documento": "47111222",
                "confirmacion": "confirmar",
            },
        )

        self.assertEqual(response_post.status_code, 200)
        self.assertTrue(Paciente.objects.filter(pk=paciente.pk).exists())
        self.assertTrue(FichaOdontologica.objects.filter(paciente=paciente).exists())
        self.assertContains(response_post, "ARCHIVAR")

    @override_settings(STORAGES=TEST_STORAGES)
    def test_borrado_confirmado_elimina_historia_ficha_adjuntos_y_turnos_no_activos(self):
        paciente = Paciente.objects.create(
            nombre="Clara",
            apellido="Clinica",
            documento="48111222",
        )
        odontologo = self._crear_odontologo_para_paciente(paciente, "CLINICA")
        DisponibilidadOdontologo.objects.create(
            odontologo=odontologo,
            dia_semana=DisponibilidadOdontologo.DiaSemana.VIERNES,
            hora_inicio=time(9, 0),
            hora_fin=time(18, 0),
        )
        historia = HistoriaClinica.objects.create(
            paciente=paciente,
            odontologo=odontologo,
            fecha=date(2026, 5, 8),
            motivo_consulta="Control",
        )
        adjunto = HistoriaClinicaAdjunto.objects.create(
            historia=historia,
            archivo=SimpleUploadedFile(
                "radiografia.jpg",
                b"contenido",
                content_type="image/jpeg",
            ),
        )
        FichaOdontologica.objects.create(
            paciente=paciente,
            antecedentes_medicos="Asma",
        )
        turno = Turno.objects.create(
            paciente=paciente,
            odontologo=odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CANCELADO,
        )

        response = self.client.post(
            reverse("pacientes:borrar", kwargs={"pk": paciente.pk}),
            self._datos_archivo(paciente),
        )

        self.assertRedirects(response, reverse("pacientes:lista"))
        paciente.refresh_from_db()
        self.assertFalse(paciente.activo)
        self.assertTrue(FichaOdontologica.objects.filter(paciente_id=paciente.pk).exists())
        self.assertTrue(HistoriaClinica.objects.filter(pk=historia.pk).exists())
        self.assertTrue(HistoriaClinicaAdjunto.objects.filter(pk=adjunto.pk).exists())
        self.assertTrue(Turno.objects.filter(pk=turno.pk).exists())

    def _datos_archivo(self, paciente, motivo="Archivo administrativo de prueba"):
        return {
            "motivo": motivo,
            "confirmacion": "ARCHIVAR",
            "documento": paciente.documento,
        }

    def _crear_turno_para_paciente(self, paciente, estado):
        odontologo = self._crear_odontologo_para_paciente(paciente, "TURNO")
        DisponibilidadOdontologo.objects.create(
            odontologo=odontologo,
            dia_semana=DisponibilidadOdontologo.DiaSemana.VIERNES,
            hora_inicio=time(9, 0),
            hora_fin=time(18, 0),
        )
        return Turno.objects.create(
            paciente=paciente,
            odontologo=odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=estado,
        )

    def _crear_odontologo_para_paciente(self, paciente, sufijo):
        usuario_odontologo = get_user_model().objects.create_user(
            username=f"dr.{paciente.documento}.{sufijo.lower()}"
        )
        odontologo = Odontologo.objects.create(
            usuario=usuario_odontologo,
            matricula=f"MN-{paciente.documento}-{sufijo}",
        )
        PacienteOdontologo.objects.create(
            paciente=paciente,
            odontologo=odontologo,
            motivo="Relacion de prueba",
        )
        return odontologo

    def _login_odontologo_asociado(self, paciente, sufijo):
        usuario_odontologo = get_user_model().objects.create_user(
            username=f"dr.{paciente.documento}.{sufijo.lower()}"
        )
        asignar_rol(usuario_odontologo, ROL_ODONTOLOGO)
        odontologo = Odontologo.objects.create(
            usuario=usuario_odontologo,
            matricula=f"MN-{paciente.documento}-{sufijo}",
        )
        PacienteOdontologo.objects.create(
            paciente=paciente,
            odontologo=odontologo,
            motivo="Relacion clinica activa",
        )
        self.client.force_login(usuario_odontologo)
        return usuario_odontologo


class PacienteDeleteRoleTests(TestCase):
    def setUp(self):
        self.paciente = Paciente.objects.create(
            nombre="Rol",
            apellido="Delete",
            documento="43111222",
        )

    def test_odontologo_no_puede_archivar_paciente(self):
        usuario = get_user_model().objects.create_user(username="dr.delete")
        asignar_rol(usuario, ROL_ODONTOLOGO)
        odontologo = Odontologo.objects.create(usuario=usuario, matricula="MN-DEL-ODO")
        PacienteOdontologo.objects.create(
            paciente=self.paciente,
            odontologo=odontologo,
            motivo="Paciente propio",
        )
        self.client.force_login(usuario)

        response = self.client.post(
            reverse("pacientes:borrar", kwargs={"pk": self.paciente.pk}),
            self._datos_archivo(self.paciente),
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Paciente.objects.filter(pk=self.paciente.pk, activo=True).exists())

    def test_administrador_puede_borrar_paciente_con_confirmacion(self):
        usuario = get_user_model().objects.create_user(username="admin.delete", is_staff=True)
        asignar_rol(usuario, ROL_ADMINISTRADOR)
        self.client.force_login(usuario)

        response = self.client.post(
            reverse("pacientes:borrar", kwargs={"pk": self.paciente.pk}),
            self._datos_archivo(self.paciente),
        )

        self.assertRedirects(response, reverse("pacientes:lista"))
        self.paciente.refresh_from_db()
        self.assertFalse(self.paciente.activo)

    def _datos_archivo(self, paciente):
        return {
            "motivo": "Archivo administrativo por rol",
            "confirmacion": "ARCHIVAR",
            "documento": paciente.documento,
        }
