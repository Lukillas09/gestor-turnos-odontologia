from datetime import date, datetime, time, timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core import mail
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.contrib.auth.hashers import check_password
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from pacientes.models import Paciente, PacienteOdontologo
from turnos.google_calendar_sync import ResultadoSincronizacionGoogleCalendar
from turnos.models import (
    AccionPublicaTurno,
    BloqueoAgendaOdontologo,
    DesafioAccesoPublicoTurnos,
    DisponibilidadOdontologo,
    GoogleCalendarConexion,
    Odontologo,
    SolicitudTurnoPublica,
    Turno,
)
from turnos.notifications import (
    notificar_recordatorio_turno,
    notificar_turno_confirmado,
)
from turnos.public_access.rate_limit import construir_clave
from turnos.public_access.tokens import (
    PUBLIC_ACCESS_PENDING_CHALLENGE_KEY,
    PUBLIC_ACCESS_SESSION_KEY,
    PUBLIC_ACTION_TOKENS_SESSION_KEY,
    hash_valor_publico,
)
from turnos.selectors import (
    obtener_bloques_agenda_del_dia,
    obtener_horarios_disponibles,
    obtener_inicio_semana,
    obtener_turnos_de_la_semana,
    obtener_turnos_del_dia,
)
from turnos.services import (
    enviar_recordatorios_email,
    obtener_turnos_para_recordatorio,
    reprogramar_turno,
)
from turnos.forms import RevisionSolicitudTurnoPublicaForm
from turnos.solicitudes_publicas.services import crear_solicitud_publica_de_turno
from usuarios.roles import ROL_ADMINISTRADOR, ROL_ODONTOLOGO, ROL_RECEPCIONISTA


def asignar_rol(usuario, nombre_rol):
    grupo, _ = Group.objects.get_or_create(name=nombre_rol)
    usuario.groups.add(grupo)


def crear_disponibilidad_laboral(odontologo, hora_inicio=time(9, 0), hora_fin=time(18, 0)):
    disponibilidades = []

    for dia_semana in range(5):
        disponibilidades.append(
            DisponibilidadOdontologo.objects.create(
                odontologo=odontologo,
                dia_semana=dia_semana,
                hora_inicio=hora_inicio,
                hora_fin=hora_fin,
            )
        )

    return disponibilidades


def obtener_fecha_laboral_futura():
    fecha = timezone.localdate() + timedelta(days=1)

    while fecha.weekday() >= 5:
        fecha += timedelta(days=1)

    return fecha


class TurnoModelTests(TestCase):
    def setUp(self):
        usuario = get_user_model().objects.create_user(
            username="dra.gomez",
            first_name="Ana",
            last_name="Gomez",
        )
        self.odontologo = Odontologo.objects.create(
            usuario=usuario,
            matricula="MN-12345",
            hora_inicio_atencion=time(9, 0),
            hora_fin_atencion=time(18, 0),
        )
        crear_disponibilidad_laboral(self.odontologo)
        self.paciente = Paciente.objects.create(
            nombre="Lucas",
            apellido="Perez",
            documento="12345678",
        )

    def test_no_permite_turnos_solapados(self):
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 6),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )

        turno_solapado = Turno(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 6),
            hora_inicio=time(10, 15),
            duracion_minutos=30,
        )

        with self.assertRaises(ValidationError):
            turno_solapado.full_clean()

    def test_guardar_turno_crea_bloqueo_de_agenda(self):
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 6),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )

        self.assertTrue(
            BloqueoAgendaOdontologo.objects.filter(
                odontologo=self.odontologo,
                fecha=date(2026, 5, 6),
            ).exists()
        )

    def test_reprogramar_turno_bloquea_fecha_original_y_nueva(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 6),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )

        reprogramar_turno(
            turno,
            {
                "fecha": date(2026, 5, 7),
                "hora_inicio": time(11, 0),
                "duracion_minutos": 30,
            },
        )

        fechas_bloqueadas = set(
            BloqueoAgendaOdontologo.objects.filter(
                odontologo=self.odontologo,
            ).values_list("fecha", flat=True)
        )

        self.assertIn(date(2026, 5, 6), fechas_bloqueadas)
        self.assertIn(date(2026, 5, 7), fechas_bloqueadas)

    def test_permite_reusar_horario_de_turno_cancelado(self):
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 6),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CANCELADO,
        )

        turno_nuevo = Turno(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 6),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )

        turno_nuevo.full_clean()

    def test_estado_realizado_no_esta_disponible(self):
        valores_estado = [valor for valor, _etiqueta in Turno.Estado.choices]

        self.assertNotIn("realizado", valores_estado)

    def test_no_permite_turnos_fuera_del_horario_de_atencion(self):
        turno_fuera_de_horario = Turno(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 6),
            hora_inicio=time(17, 45),
            duracion_minutos=30,
        )

        with self.assertRaises(ValidationError):
            turno_fuera_de_horario.full_clean()

    def test_no_permite_turnos_en_dias_no_laborables(self):
        turno_dia_no_laborable = Turno(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 9),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )

        with self.assertRaises(ValidationError):
            turno_dia_no_laborable.full_clean()

    def test_no_permite_turnos_para_odontologo_inactivo(self):
        self.odontologo.activo = False
        self.odontologo.save()

        turno = Turno(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 6),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )

        with self.assertRaises(ValidationError):
            turno.full_clean()

    def test_foto_perfil_url_no_rompe_si_storage_falla(self):
        self.odontologo.foto_url = "https://example.com/foto-fallback.jpg"
        self.odontologo.foto_perfil.name = "odontologos/1/perfil/foto.jpg"

        with patch.object(
            self.odontologo.foto_perfil.storage,
            "url",
            side_effect=RuntimeError("Storage no disponible"),
        ):
            with self.assertLogs("turnos.models", level="WARNING"):
                url = self.odontologo.foto_perfil_url

        self.assertEqual(url, "https://example.com/foto-fallback.jpg")


class TurnoViewsTests(TestCase):
    def setUp(self):
        usuario = get_user_model().objects.create_user(
            username="dra.lopez",
            first_name="Maria",
            last_name="Lopez",
        )
        asignar_rol(usuario, ROL_RECEPCIONISTA)
        self.client.force_login(usuario)
        self.odontologo = Odontologo.objects.create(
            usuario=usuario,
            matricula="MN-54321",
            hora_inicio_atencion=time(9, 0),
            hora_fin_atencion=time(18, 0),
        )
        crear_disponibilidad_laboral(self.odontologo)
        self.paciente = Paciente.objects.create(
            nombre="Julia",
            apellido="Diaz",
            documento="30123456",
        )

    def test_listado_muestra_turnos(self):
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )

        response = self.client.get(reverse("turnos:lista"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Diaz")
        self.assertContains(response, "10:00")

    def test_listado_filtra_por_estado(self):
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
        )
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 9),
            hora_inicio=time(11, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CANCELADO,
        )

        response = self.client.get(reverse("turnos:lista"), {"estado": Turno.Estado.CANCELADO})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cancelado")
        self.assertContains(response, "09/05/2026")
        self.assertNotContains(response, "08/05/2026")

    def test_creacion_pide_buscar_horarios_por_odontologo_y_fecha(self):
        response = self.client.get(reverse("turnos:crear"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Buscar horarios")
        self.assertContains(response, "Elegir odontólogo y fecha")

    def test_creacion_muestra_solo_horarios_disponibles(self):
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(9, 30),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
        )

        response = self.client.get(
            reverse("turnos:crear"),
            {
                "odontologo": self.odontologo.pk,
                "fecha": "2026-05-08",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="2026-05-08"', count=2)
        self.assertContains(response, 'value="09:00"')
        self.assertContains(response, 'value="10:00"')
        self.assertNotContains(response, 'value="09:30"')

    def test_creacion_indica_cuando_no_hay_horarios_disponibles(self):
        response = self.client.get(
            reverse("turnos:crear"),
            {
                "odontologo": self.odontologo.pk,
                "fecha": "2026-05-09",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sin horarios disponibles")

    def test_horarios_disponibles_json_devuelve_horarios_libres(self):
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(9, 30),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
        )

        response = self.client.get(
            reverse("turnos:horarios_disponibles"),
            {
                "odontologo": self.odontologo.pk,
                "fecha": "2026-05-08",
                "duracion_minutos": 30,
            },
        )

        self.assertEqual(response.status_code, 200)
        horarios = [horario["value"] for horario in response.json()["horarios"]]
        self.assertIn("09:00", horarios)
        self.assertIn("10:00", horarios)
        self.assertNotIn("09:30", horarios)

    def test_horarios_disponibles_json_indica_si_faltan_datos(self):
        response = self.client.get(reverse("turnos:horarios_disponibles"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["horarios"], [])
        self.assertEqual(
            response.json()["mensaje"],
            "Elegí odontólogo y fecha para ver horarios disponibles.",
        )

    def test_creacion_de_turno_valido(self):
        response = self.client.post(
            reverse("turnos:crear"),
            {
                "paciente": self.paciente.pk,
                "odontologo": self.odontologo.pk,
                "fecha": "2026-05-08",
                "hora_inicio": "10:00",
                "duracion_minutos": 30,
                "motivo": "Control",
                "notas": "",
            },
        )

        self.assertRedirects(response, reverse("turnos:lista"))
        turno = Turno.objects.get(motivo="Control")
        self.assertEqual(turno.estado, Turno.Estado.CONFIRMADO)
        self.assertTrue(
            PacienteOdontologo.objects.filter(
                paciente=self.paciente,
                odontologo=self.odontologo,
                activo=True,
            ).exists()
        )

    def test_creacion_rechaza_turno_solapado(self):
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )

        response = self.client.post(
            reverse("turnos:crear"),
            {
                "paciente": self.paciente.pk,
                "odontologo": self.odontologo.pk,
                "fecha": "2026-05-08",
                "hora_inicio": "10:15",
                "duracion_minutos": 30,
                "motivo": "Limpieza",
                "notas": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Turno.objects.filter(motivo="Limpieza").exists())
        self.assertIn("hora_inicio", response.context["form"].errors)

    def test_creacion_rechaza_turno_fuera_de_horario(self):
        response = self.client.post(
            reverse("turnos:crear"),
            {
                "paciente": self.paciente.pk,
                "odontologo": self.odontologo.pk,
                "fecha": "2026-05-08",
                "hora_inicio": "17:45",
                "duracion_minutos": 30,
                "motivo": "Control fuera de horario",
                "notas": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Turno.objects.filter(motivo="Control fuera de horario").exists())
        self.assertIn("hora_inicio", response.context["form"].errors)

    def test_detalle_muestra_datos_del_turno(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Control",
        )

        response = self.client.get(reverse("turnos:detalle", kwargs={"pk": turno.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Diaz")
        self.assertContains(response, "Control")
        self.assertContains(response, "10:00")
        self.assertNotContains(response, "Estado y sincronizaci")
        self.assertNotContains(response, "Reintentar sincronizaci")

    def test_detalle_no_muestra_estado_tecnico_de_google_calendar(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            google_calendar_event_id="evento-google-123",
        )

        response = self.client.get(reverse("turnos:detalle", kwargs={"pk": turno.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Estado y sincronizaci")
        self.assertNotContains(response, "Sincronizado con Google Calendar")
        self.assertNotContains(response, "evento-google-123")

    def test_detalle_no_muestra_turno_no_sincronizado_con_google_calendar(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )

        response = self.client.get(reverse("turnos:detalle", kwargs={"pk": turno.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "No sincronizado con Google Calendar")

    def test_detalle_no_muestra_ultimo_error_google_calendar(self):
        GoogleCalendarConexion.objects.create(
            odontologo=self.odontologo,
            refresh_token="refresh-token",
            ultimo_error="HTTP 401 invalid_grant access_token=secreto-tecnico",
        )
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )

        response = self.client.get(reverse("turnos:detalle", kwargs={"pk": turno.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Último error de sincronización")
        self.assertNotContains(response, "No se pudo autorizar la conexión con Google Calendar")
        self.assertNotContains(response, "Reintentar sincronización")
        self.assertNotContains(response, "invalid_grant")
        self.assertNotContains(response, "access_token")
        self.assertNotContains(response, "secreto-tecnico")

    def test_detalle_no_muestra_boton_reintentar_sincronizacion(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )

        response = self.client.get(reverse("turnos:detalle", kwargs={"pk": turno.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Reintentar sincronización")
        self.assertNotContains(response, reverse("turnos:reintentar_google_calendar", kwargs={"pk": turno.pk}))

    def test_reintentar_sincronizacion_muestra_mensaje_de_exito(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )

        with patch(
            "turnos.views.reintentar_sincronizacion_google_calendar",
            return_value=ResultadoSincronizacionGoogleCalendar(
                realizada=True,
                accion="crear",
                event_id="evento-creado",
            ),
        ) as sincronizar_mock:
            response = self.client.post(
                reverse("turnos:reintentar_google_calendar", kwargs={"pk": turno.pk}),
                follow=True,
            )

        self.assertRedirects(response, reverse("turnos:detalle", kwargs={"pk": turno.pk}))
        self.assertContains(response, "Turno sincronizado con Google Calendar correctamente.")
        sincronizar_mock.assert_called_once()

    def test_reintentar_sincronizacion_muestra_mensaje_de_error(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )

        with patch(
            "turnos.views.reintentar_sincronizacion_google_calendar",
            return_value=ResultadoSincronizacionGoogleCalendar(
                realizada=False,
                accion="crear",
                mensaje="HTTP 401 invalid_grant access_token=secreto-tecnico",
            ),
        ):
            response = self.client.post(
                reverse("turnos:reintentar_google_calendar", kwargs={"pk": turno.pk}),
                follow=True,
            )

        self.assertRedirects(response, reverse("turnos:detalle", kwargs={"pk": turno.pk}))
        self.assertContains(response, "No se pudo sincronizar con Google Calendar")
        self.assertContains(response, "No se pudo autorizar la conexión con Google Calendar")
        self.assertNotContains(response, "invalid_grant")
        self.assertNotContains(response, "access_token")
        self.assertNotContains(response, "secreto-tecnico")

    def test_detalle_muestra_boton_confirmar_si_el_turno_esta_pendiente(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.PENDIENTE,
        )

        response = self.client.get(reverse("turnos:detalle", kwargs={"pk": turno.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirmar turno")
        self.assertContains(response, 'class="button button-success"')

    def test_detalle_ordena_acciones_del_turno_pendiente(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.PENDIENTE,
        )

        response = self.client.get(reverse("turnos:detalle", kwargs={"pk": turno.pk}))
        contenido = response.content.decode()

        self.assertLess(contenido.index("Confirmar turno"), contenido.index("Reprogramar"))
        self.assertLess(contenido.index("Reprogramar"), contenido.index("Cancelar turno"))
        self.assertLess(contenido.index("Cancelar turno"), contenido.index("Editar turno"))

    def test_detalle_no_muestra_boton_confirmar_si_el_turno_no_esta_pendiente(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
        )

        response = self.client.get(reverse("turnos:detalle", kwargs={"pk": turno.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Confirmar turno")

    def test_detalle_de_turno_cancelado_oculta_acciones_de_gestion(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CANCELADO,
        )

        response = self.client.get(reverse("turnos:detalle", kwargs={"pk": turno.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Confirmar turno")
        self.assertNotContains(response, "Reprogramar")
        self.assertNotContains(response, "Cancelar turno")
        self.assertNotContains(response, "Editar turno")

    def test_confirmacion_pide_duracion_real(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.PENDIENTE,
            motivo="Solicitud publica",
        )

        response = self.client.get(reverse("turnos:confirmar", kwargs={"pk": turno.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirmar turno pendiente")
        self.assertContains(response, "Duraci")
        self.assertContains(response, "Duración personalizada")
        self.assertContains(response, "120 minutos")

    def test_confirmacion_cambia_estado_y_actualiza_duracion_sin_modificar_fecha_ni_horario(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.PENDIENTE,
            motivo="Solicitud publica",
        )

        response = self.client.post(
            reverse("turnos:confirmar", kwargs={"pk": turno.pk}),
            {"duracion_rapida": 60},
        )

        turno.refresh_from_db()

        self.assertRedirects(response, reverse("turnos:detalle", kwargs={"pk": turno.pk}))
        self.assertEqual(turno.estado, Turno.Estado.CONFIRMADO)
        self.assertEqual(turno.fecha, date(2026, 5, 8))
        self.assertEqual(turno.hora_inicio, time(10, 0))
        self.assertEqual(turno.duracion_minutos, 60)

    def test_confirmacion_falla_si_la_duracion_se_superpone_con_turno_confirmado(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(9, 0),
            duracion_minutos=30,
            estado=Turno.Estado.PENDIENTE,
            motivo="Solicitud larga",
        )
        turno_conflictivo = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
            motivo="Control confirmado",
        )

        with patch("turnos.services.sincronizar_turno_actualizado") as sincronizar_mock:
            with patch("turnos.services.notificar_turno_confirmado") as notificar_mock:
                response = self.client.post(
                    reverse("turnos:confirmar", kwargs={"pk": turno.pk}),
                    {"duracion_rapida": 120},
                )

        turno.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(turno.estado, Turno.Estado.PENDIENTE)
        self.assertEqual(turno.duracion_minutos, 30)
        sincronizar_mock.assert_not_called()
        notificar_mock.assert_not_called()
        self.assertContains(response, "se superpone")
        self.assertContains(response, "Control confirmado")
        self.assertContains(
            response,
            reverse("turnos:reprogramar", kwargs={"pk": turno.pk}),
        )
        self.assertContains(
            response,
            reverse("turnos:reprogramar", kwargs={"pk": turno_conflictivo.pk}),
        )

    def test_confirmacion_falla_si_la_duracion_se_superpone_con_turno_pendiente(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(9, 0),
            duracion_minutos=30,
            estado=Turno.Estado.PENDIENTE,
        )
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.PENDIENTE,
        )

        response = self.client.post(
            reverse("turnos:confirmar", kwargs={"pk": turno.pk}),
            {"duracion_rapida": 120},
        )

        turno.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(turno.estado, Turno.Estado.PENDIENTE)
        self.assertContains(response, "se superpone")

    def test_confirmacion_ignora_turnos_cancelados(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(9, 0),
            duracion_minutos=30,
            estado=Turno.Estado.PENDIENTE,
        )
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CANCELADO,
        )

        response = self.client.post(
            reverse("turnos:confirmar", kwargs={"pk": turno.pk}),
            {"duracion_rapida": 120},
        )

        turno.refresh_from_db()
        self.assertRedirects(response, reverse("turnos:detalle", kwargs={"pk": turno.pk}))
        self.assertEqual(turno.estado, Turno.Estado.CONFIRMADO)
        self.assertEqual(turno.duracion_minutos, 120)

    def test_confirmacion_con_duracion_personalizada_actualiza_turno(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.PENDIENTE,
        )

        response = self.client.post(
            reverse("turnos:confirmar", kwargs={"pk": turno.pk}),
            {
                "duracion_rapida": 30,
                "duracion_personalizada": 75,
            },
        )

        turno.refresh_from_db()
        self.assertRedirects(response, reverse("turnos:detalle", kwargs={"pk": turno.pk}))
        self.assertEqual(turno.estado, Turno.Estado.CONFIRMADO)
        self.assertEqual(turno.duracion_minutos, 75)

    def test_confirmacion_rechaza_duracion_personalizada_invalida(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.PENDIENTE,
        )

        response = self.client.post(
            reverse("turnos:confirmar", kwargs={"pk": turno.pk}),
            {
                "duracion_rapida": 30,
                "duracion_personalizada": 361,
            },
        )

        turno.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(turno.estado, Turno.Estado.PENDIENTE)
        self.assertContains(response, "La duración no puede superar las 6 horas.")

    def test_confirmacion_con_duracion_personalizada_muestra_conflicto(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(9, 0),
            duracion_minutos=30,
            estado=Turno.Estado.PENDIENTE,
            motivo="Solicitud personalizada",
        )
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
            motivo="Control conflictivo",
        )

        response = self.client.post(
            reverse("turnos:confirmar", kwargs={"pk": turno.pk}),
            {
                "duracion_rapida": 30,
                "duracion_personalizada": 75,
            },
        )

        turno.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(turno.estado, Turno.Estado.PENDIENTE)
        self.assertEqual(turno.duracion_minutos, 30)
        self.assertContains(response, "se superpone")
        self.assertContains(response, "Control conflictivo")

    def test_edicion_actualiza_turno(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Control",
        )

        response = self.client.post(
            reverse("turnos:editar", kwargs={"pk": turno.pk}),
            {
                "paciente": self.paciente.pk,
                "odontologo": self.odontologo.pk,
                "fecha": "2026-05-08",
                "hora_inicio": "11:00",
                "duracion_minutos": 45,
                "motivo": "Limpieza",
                "estado": Turno.Estado.CONFIRMADO,
                "notas": "Reprogramado",
            },
        )

        turno.refresh_from_db()

        self.assertRedirects(response, reverse("turnos:detalle", kwargs={"pk": turno.pk}))
        self.assertEqual(turno.hora_inicio, time(11, 0))
        self.assertEqual(turno.duracion_minutos, 45)
        self.assertEqual(turno.motivo, "Limpieza")
        self.assertEqual(turno.estado, Turno.Estado.CONFIRMADO)
        self.assertEqual(turno.notas, "Reprogramado")

    def test_edicion_rechaza_turno_solapado(self):
        turno_existente = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(11, 0),
            duracion_minutos=30,
        )

        response = self.client.post(
            reverse("turnos:editar", kwargs={"pk": turno.pk}),
            {
                "paciente": self.paciente.pk,
                "odontologo": self.odontologo.pk,
                "fecha": "2026-05-08",
                "hora_inicio": "10:15",
                "duracion_minutos": 30,
                "motivo": "",
                "estado": Turno.Estado.PENDIENTE,
                "notas": "",
            },
        )

        turno.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertIn("hora_inicio", response.context["form"].errors)
        self.assertEqual(turno.hora_inicio, time(11, 0))
        self.assertEqual(Turno.objects.count(), 2)
        self.assertEqual(turno_existente.hora_inicio, time(10, 0))

    def test_detalle_muestra_boton_reprogramar_turno(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
        )

        response = self.client.get(reverse("turnos:detalle", kwargs={"pk": turno.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reprogramar")
        self.assertContains(response, reverse("turnos:reprogramar", kwargs={"pk": turno.pk}))

    def test_reprogramacion_actualiza_fecha_hora_y_duracion(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
        )

        response = self.client.post(
            reverse("turnos:reprogramar", kwargs={"pk": turno.pk}),
            {
                "fecha": "2026-05-08",
                "hora_inicio": "11:15",
                "duracion_minutos": 45,
            },
        )

        turno.refresh_from_db()

        self.assertRedirects(response, reverse("turnos:detalle", kwargs={"pk": turno.pk}))
        self.assertEqual(turno.fecha, date(2026, 5, 8))
        self.assertEqual(turno.hora_inicio, time(11, 15))
        self.assertEqual(turno.duracion_minutos, 45)

    def test_reprogramacion_rechaza_turno_solapado(self):
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(11, 0),
            duracion_minutos=30,
        )

        response = self.client.post(
            reverse("turnos:reprogramar", kwargs={"pk": turno.pk}),
            {
                "fecha": "2026-05-08",
                "hora_inicio": "10:00",
                "duracion_minutos": 30,
            },
        )

        turno.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertIn("hora_inicio", response.context["form"].errors)
        self.assertEqual(turno.hora_inicio, time(11, 0))

    def test_cancelacion_cambia_estado_sin_borrar_turno(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )

        response = self.client.post(reverse("turnos:cancelar", kwargs={"pk": turno.pk}))

        turno.refresh_from_db()

        self.assertRedirects(response, reverse("turnos:detalle", kwargs={"pk": turno.pk}))
        self.assertEqual(turno.estado, Turno.Estado.CANCELADO)
        self.assertEqual(Turno.objects.count(), 1)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="turnos@example.com",
    TURNOS_PUBLIC_REDIS_REQUIRED=False,
    TURNSTILE_ENABLED=False,
)
class SolicitudTurnoPublicaTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox.clear()
        usuario = get_user_model().objects.create_user(
            username="dra.publica",
            first_name="Paula",
            last_name="Publica",
        )
        self.odontologo = Odontologo.objects.create(
            usuario=usuario,
            matricula="MN-PUB",
            duracion_turno_minutos=30,
            foto_url="https://example.com/paula.jpg",
        )
        crear_disponibilidad_laboral(self.odontologo)
        self.fecha_turno = obtener_fecha_laboral_futura()

    def test_landing_publica_carga_sin_login(self):
        response = self.client.get(reverse("landing_publica"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reservá tu turno odontológico")
        self.assertContains(response, reverse("turnos:solicitud_publica"))
        self.assertContains(response, reverse("turnos:acceso_publico_solicitar"))
        self.assertContains(response, reverse("login"))

    def _datos_solicitud_publica(self, **overrides):
        datos = {
            "nombre": "Lucia",
            "apellido": "Paz",
            "documento": "38111222",
            "telefono": "1155667788",
            "email": "lucia@example.com",
            "odontologo": self.odontologo.pk,
            "fecha": self.fecha_turno.isoformat(),
            "hora_inicio": "10:00",
            "motivo": "Consulta inicial",
        }
        datos.update(overrides)
        return datos

    def _crear_turno_existente(self, estado, hora_inicio=time(10, 0)):
        paciente = Paciente.objects.create(
            nombre="Mario",
            apellido="Rojas",
            documento=f"40{estado[:3]}{hora_inicio.hour:02d}{hora_inicio.minute:02d}",
        )
        return Turno.objects.create(
            paciente=paciente,
            odontologo=self.odontologo,
            fecha=self.fecha_turno,
            hora_inicio=hora_inicio,
            duracion_minutos=30,
            estado=estado,
        )

    def _crear_usuario_recepcion(self, username="recepcion.publica"):
        usuario = get_user_model().objects.create_user(
            username=username,
            first_name="Rocio",
            last_name="Recepcion",
        )
        asignar_rol(usuario, ROL_RECEPCIONISTA)
        return usuario

    def _crear_solicitud_existente_con_diferencias(self, hora_inicio="11:00"):
        paciente = Paciente.objects.create(
            nombre="Viejo",
            apellido="Nombre",
            documento="39111222",
            telefono="1100000000",
            email="contacto-original@example.com",
        )

        self.client.post(
            reverse("turnos:solicitud_publica_datos"),
            {
                "nombre": "Nadia",
                "apellido": "Suarez",
                "documento": "39.111.222",
                "telefono": "1199999999",
                "email": "nadia@example.com",
                "odontologo": self.odontologo.pk,
                "fecha": self.fecha_turno.isoformat(),
                "hora_inicio": hora_inicio,
                "motivo": "Control",
            },
        )

        return SolicitudTurnoPublica.objects.get(paciente=paciente), paciente

    def test_formulario_publico_inicia_sin_odontologo_seleccionado(self):
        response = self.client.get(reverse("turnos:solicitud_publica"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Solicitar turno")
        self.assertContains(response, "Opciones de turnos disponibles")
        self.assertContains(response, "Seleccionar odontólogo")
        self.assertContains(response, "Elegí un odontólogo para ver los horarios disponibles.")
        self.assertContains(response, "Autogestión de turnos")
        self.assertIsNone(response.context["odontologo"])
        self.assertEqual(response.context["horarios_manana"], [])
        self.assertEqual(response.context["horarios_tarde"], [])
        self.assertNotContains(response, 'src="https://example.com/paula.jpg"')

    def test_formulario_publico_muestra_horarios_disponibles(self):
        paciente = Paciente.objects.create(
            nombre="Rita",
            apellido="Moreno",
            documento="37111222",
        )
        Turno.objects.create(
            paciente=paciente,
            odontologo=self.odontologo,
            fecha=self.fecha_turno,
            hora_inicio=time(9, 30),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
        )

        response = self.client.get(
            reverse("turnos:solicitud_publica"),
            {
                "odontologo": self.odontologo.pk,
                "fecha": self.fecha_turno.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'value="{self.fecha_turno.isoformat()}"')
        self.assertContains(response, "09:00")
        self.assertContains(response, "10:00")
        self.assertContains(response, reverse("turnos:solicitud_publica_datos"))
        self.assertNotContains(response, "09:30")

    def test_solicitud_publica_de_paciente_archivado_no_crea_turno(self):
        paciente = Paciente.objects.create(
            nombre="Archivado",
            apellido="Publico",
            documento="66111222",
            email="archivado@example.com",
        )
        paciente.archivar_en_memoria(None, "Archivo administrativo previo")
        paciente.save()

        response = self.client.post(
            reverse("turnos:solicitud_publica_datos"),
            self._datos_solicitud_publica(
                documento="66.111.222",
                nombre="Archivado",
                apellido="Publico",
                email="nuevo@example.com",
                hora_inicio="10:00",
            ),
        )

        self.assertRedirects(response, reverse("landing_publica"))
        solicitud = SolicitudTurnoPublica.objects.get(paciente=paciente)
        self.assertIsNone(solicitud.turno_id)
        self.assertTrue(solicitud.requiere_revision)
        self.assertEqual(
            solicitud.estado_revision,
            SolicitudTurnoPublica.EstadoRevision.PENDIENTE,
        )
        self.assertFalse(Turno.objects.filter(paciente=paciente).exists())
        self.assertFalse(
            PacienteOdontologo.objects.filter(
                paciente=paciente,
                odontologo=self.odontologo,
                activo=True,
            ).exists()
        )

    def test_endpoint_publico_horarios_devuelve_disponibilidad(self):
        paciente = Paciente.objects.create(
            nombre="Rita",
            apellido="Moreno",
            documento="37111222",
        )
        Turno.objects.create(
            paciente=paciente,
            odontologo=self.odontologo,
            fecha=self.fecha_turno,
            hora_inicio=time(9, 30),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
        )

        response = self.client.get(
            reverse("turnos:solicitud_publica_horarios"),
            {
                "odontologo": self.odontologo.pk,
                "fecha": self.fecha_turno.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        horarios = [
            horario["label"]
            for bloque in ("horarios_manana", "horarios_tarde")
            for horario in data[bloque]
        ]

        self.assertTrue(data["ok"])
        self.assertEqual(data["odontologo"]["nombre"], "Paula Publica")
        self.assertEqual(data["odontologo"]["duracion"], 30)
        self.assertEqual(data["fecha"]["iso"], self.fecha_turno.isoformat())
        self.assertIn("09:00", horarios)
        self.assertIn("10:00", horarios)
        self.assertNotIn("09:30", horarios)
        self.assertTrue(
            any(
                reverse("turnos:solicitud_publica_datos") in horario["url"]
                and "hora_inicio=10%3A00" in horario["url"]
                for bloque in ("horarios_manana", "horarios_tarde")
                for horario in data[bloque]
            )
        )

    def test_endpoint_publico_horarios_maneja_odontologo_faltante(self):
        response = self.client.get(
            reverse("turnos:solicitud_publica_horarios"),
            {"fecha": self.fecha_turno.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["codigo"], "sin_odontologo")
        self.assertEqual(
            data["mensaje"],
            "Elegí un odontólogo para ver los horarios disponibles.",
        )

    def test_endpoint_publico_horarios_maneja_fecha_invalida(self):
        response = self.client.get(
            reverse("turnos:solicitud_publica_horarios"),
            {
                "odontologo": self.odontologo.pk,
                "fecha": "fecha-rara",
            },
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["codigo"], "fecha_invalida")
        self.assertEqual(data["mensaje"], "Ingresá una fecha válida.")

    def test_reservar_horario_abre_formulario_de_datos(self):
        response = self.client.get(
            reverse("turnos:solicitud_publica_datos"),
            {
                "odontologo": self.odontologo.pk,
                "fecha": self.fecha_turno.isoformat(),
                "hora_inicio": "10:00",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Completar datos")
        self.assertContains(response, "Paula Publica")
        self.assertContains(response, "10:00 a 10:30")
        self.assertContains(response, "Enviar solicitud")
        self.assertContains(response, "Tus datos de contacto")
        self.assertContains(response, 'name="hora_inicio" value="10:00"')
        self.assertNotContains(response, 'name="hora_inicio" value="10:00:00"')
        self.assertNotContains(response, "Fecha de nacimiento")
        self.assertNotContains(response, "Sexo / gÃ©nero")
        self.assertNotContains(response, "Domicilio")
        self.assertNotContains(response, "Localidad")
        self.assertNotContains(response, "Obra social")
        self.assertNotContains(response, "NÃºmero de afiliado")
        self.assertNotContains(response, "Contacto de emergencia")

    def test_formulario_publico_no_permite_buscar_fecha_pasada(self):
        fecha_pasada = timezone.localdate() - timedelta(days=1)

        response = self.client.get(
            reverse("turnos:solicitud_publica"),
            {
                "odontologo": self.odontologo.pk,
                "fecha": fecha_pasada.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "La fecha no puede ser anterior a hoy.")

    def test_solicitud_publica_crea_paciente_y_turno_pendiente(self):
        response = self.client.post(
            reverse("turnos:solicitud_publica_datos"),
            self._datos_solicitud_publica(),
        )

        turno = Turno.objects.get(motivo="Consulta inicial")

        self.assertRedirects(response, reverse("landing_publica"))
        self.assertEqual(turno.estado, Turno.Estado.PENDIENTE)
        self.assertEqual(turno.paciente.documento, "38111222")
        self.assertEqual(turno.paciente.telefono, "1155667788")
        self.assertEqual(turno.paciente.email, "lucia@example.com")
        self.assertIsNone(turno.paciente.email_verificado_en)
        self.assertIsNone(turno.paciente.telefono_verificado_en)
        self.assertEqual(
            turno.paciente.estado_validacion_datos,
            Paciente.EstadoValidacionDatos.PENDIENTE,
        )
        self.assertEqual(turno.paciente.origen_alta, Paciente.OrigenAlta.SOLICITUD_PUBLICA)
        self.assertIsNone(turno.paciente.fecha_nacimiento)
        self.assertEqual(turno.paciente.genero, "")
        self.assertEqual(turno.paciente.domicilio, "")
        self.assertEqual(turno.paciente.localidad, "")
        self.assertEqual(turno.paciente.obra_social, "")
        self.assertEqual(turno.paciente.numero_afiliado, "")
        self.assertEqual(turno.paciente.contacto_emergencia, "")
        self.assertEqual(turno.hora_inicio, time(10, 0))
        self.assertEqual(turno.duracion_minutos, 30)
        self.assertTrue(
            PacienteOdontologo.objects.filter(
                paciente=turno.paciente,
                odontologo=self.odontologo,
                activo=True,
            ).exists()
        )
        solicitud = turno.solicitud_publica
        self.assertFalse(solicitud.paciente_existente)
        self.assertTrue(solicitud.requiere_revision)
        self.assertEqual(
            solicitud.estado_revision,
            SolicitudTurnoPublica.EstadoRevision.PENDIENTE,
        )
        self.assertEqual(solicitud.documento_enviado, "38111222")
        self.assertEqual(solicitud.nombre_enviado, "Lucia")
        self.assertEqual(solicitud.apellido_enviado, "Paz")
        self.assertEqual(solicitud.telefono_enviado, "1155667788")
        self.assertEqual(solicitud.email_enviado, "lucia@example.com")
        self.assertEqual(solicitud.motivo_enviado, "Consulta inicial")

    def test_solicitud_publica_vuelve_al_inicio_con_banner_confirmacion(self):
        response = self.client.post(
            reverse("turnos:solicitud_publica_datos"),
            self._datos_solicitud_publica(
                documento="38111999",
                email="banner@example.com",
                motivo="Consulta con banner",
            ),
            follow=True,
        )

        self.assertEqual(response.redirect_chain, [(reverse("landing_publica"), 302)])
        self.assertContains(response, "Tu solicitud fue registrada")
        self.assertContains(
            response,
            "El consultorio revisará la información y se comunicará por un medio de contacto válido.",
        )
        self.assertContains(response, reverse("turnos:solicitud_publica"))
        self.assertContains(response, reverse("turnos:acceso_publico_solicitar"))

        nueva_visita = self.client.get(reverse("landing_publica"))

        self.assertNotContains(nueva_visita, "Tu solicitud fue registrada")

    def test_solicitud_publica_acepta_hora_enviada_con_segundos(self):
        response = self.client.post(
            reverse("turnos:solicitud_publica_datos"),
            self._datos_solicitud_publica(
                documento="38111223",
                hora_inicio="09:00:00",
                motivo="Consulta con segundos",
            ),
        )

        turno = Turno.objects.get(motivo="Consulta con segundos")

        self.assertRedirects(response, reverse("landing_publica"))
        self.assertEqual(turno.estado, Turno.Estado.PENDIENTE)
        self.assertEqual(turno.hora_inicio, time(9, 0))

    def test_confirmacion_publica_muestra_datos_del_turno(self):
        self.client.post(
            reverse("turnos:solicitud_publica_datos"),
            self._datos_solicitud_publica(),
        )

        response = self.client.get(reverse("turnos:solicitud_publica_ok"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Paula Publica")
        self.assertContains(response, self.fecha_turno.strftime("%d/%m/%Y"))
        self.assertContains(response, "10:00 a 10:30")
        self.assertContains(response, "Pendiente")
        self.assertNotContains(response, "Paz, Lucia")
        self.assertNotContains(response, "Consulta inicial")

    def test_solicitud_publica_reutiliza_paciente_sin_reemplazar_datos(self):
        paciente = Paciente.objects.create(
            nombre="Viejo",
            apellido="Nombre",
            documento="39111222",
            telefono="1100000000",
            email="contacto-original@example.com",
            fecha_nacimiento=date(1990, 4, 15),
            obra_social="OSDE",
            contacto_emergencia="Rosa 3415550000",
        )
        actualizado_en_original = paciente.actualizado_en

        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("turnos:solicitud_publica_datos"),
                {
                    "nombre": "Nadia",
                    "apellido": "Suarez",
                    "documento": "39.111.222",
                    "telefono": "1199999999",
                    "email": "nadia@example.com",
                    "odontologo": self.odontologo.pk,
                    "fecha": self.fecha_turno.isoformat(),
                    "hora_inicio": "11:00",
                    "motivo": "Control",
                },
            )

        paciente.refresh_from_db()
        turno = Turno.objects.get(motivo="Control")
        solicitud = turno.solicitud_publica

        self.assertRedirects(response, reverse("landing_publica"))
        self.assertEqual(Paciente.objects.filter(documento="39111222").count(), 1)
        self.assertEqual(turno.paciente, paciente)
        self.assertEqual(paciente.nombre, "Viejo")
        self.assertEqual(paciente.apellido, "Nombre")
        self.assertEqual(paciente.telefono, "1100000000")
        self.assertEqual(paciente.email, "contacto-original@example.com")
        self.assertEqual(paciente.fecha_nacimiento, date(1990, 4, 15))
        self.assertEqual(paciente.obra_social, "OSDE")
        self.assertEqual(paciente.contacto_emergencia, "Rosa 3415550000")
        self.assertEqual(paciente.actualizado_en, actualizado_en_original)
        self.assertTrue(solicitud.paciente_existente)
        self.assertTrue(solicitud.requiere_revision)
        self.assertEqual(
            solicitud.estado_revision,
            SolicitudTurnoPublica.EstadoRevision.PENDIENTE,
        )
        self.assertEqual(solicitud.documento_enviado, "39111222")
        self.assertEqual(solicitud.nombre_enviado, "Nadia")
        self.assertEqual(solicitud.apellido_enviado, "Suarez")
        self.assertEqual(solicitud.telefono_enviado, "1199999999")
        self.assertEqual(solicitud.email_enviado, "nadia@example.com")
        self.assertEqual(
            set(solicitud.diferencias_detectadas),
            {"nombre", "apellido", "telefono", "email"},
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["contacto-original@example.com"])
        self.assertNotEqual(mail.outbox[0].to, ["nadia@example.com"])
        solicitud.refresh_from_db()
        self.assertIsNotNone(solicitud.notificacion_contacto_existente_en)

    def test_solicitud_publica_no_alerta_por_diferencias_solo_de_formato(self):
        paciente = Paciente.objects.create(
            nombre="Juan Carlos",
            apellido="Perez",
            documento="30123456",
            telefono="2604000000",
            email="paciente@email.com",
        )

        response = self.client.post(
            reverse("turnos:solicitud_publica_datos"),
            self._datos_solicitud_publica(
                nombre="  JUAN   CARLOS ",
                apellido="perez",
                documento="30.123.456",
                telefono="260 400-0000",
                email="PACIENTE@EMAIL.COM",
                hora_inicio="12:00",
                motivo="Formato equivalente",
            ),
        )

        paciente.refresh_from_db()
        turno = Turno.objects.get(motivo="Formato equivalente")
        solicitud = turno.solicitud_publica

        self.assertRedirects(response, reverse("landing_publica"))
        self.assertEqual(turno.paciente, paciente)
        self.assertEqual(paciente.nombre, "Juan Carlos")
        self.assertEqual(paciente.telefono, "2604000000")
        self.assertEqual(solicitud.diferencias_detectadas, {})
        self.assertFalse(solicitud.requiere_revision)
        self.assertEqual(
            solicitud.estado_revision,
            SolicitudTurnoPublica.EstadoRevision.SIN_DIFERENCIAS,
        )
        self.assertEqual(solicitud.nombre_enviado, "JUAN   CARLOS")
        self.assertEqual(solicitud.documento_enviado, "30123456")

    def test_solicitud_publica_existente_sin_email_no_usa_email_enviado(self):
        paciente = Paciente.objects.create(
            nombre="Sin",
            apellido="Contacto",
            documento="40111222",
            telefono="",
            email="",
        )

        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("turnos:solicitud_publica_datos"),
                self._datos_solicitud_publica(
                    nombre="Sin",
                    apellido="Contacto",
                    documento="40.111.222",
                    email="externo@example.com",
                    hora_inicio="12:30",
                    motivo="Sin contacto almacenado",
                ),
            )

        turno = Turno.objects.get(motivo="Sin contacto almacenado")
        solicitud = turno.solicitud_publica

        self.assertRedirects(response, reverse("landing_publica"))
        self.assertEqual(turno.paciente, paciente)
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(solicitud.requiere_revision)
        self.assertEqual(
            solicitud.estado_revision,
            SolicitudTurnoPublica.EstadoRevision.PENDIENTE,
        )
        self.assertIn("sin email", solicitud.notificacion_contacto_existente_error.lower())

    def test_solicitud_publica_normaliza_dni_y_no_duplica_paciente(self):
        self.client.post(
            reverse("turnos:solicitud_publica_datos"),
            self._datos_solicitud_publica(
                documento="30.123.456",
                hora_inicio="12:00",
                motivo="Primer DNI normalizado",
            ),
        )
        self.client.post(
            reverse("turnos:solicitud_publica_datos"),
            self._datos_solicitud_publica(
                documento="30-123-456",
                hora_inicio="12:30",
                motivo="Segundo DNI normalizado",
            ),
        )

        paciente = Paciente.objects.get(documento="30123456")

        self.assertEqual(Paciente.objects.filter(documento="30123456").count(), 1)
        self.assertEqual(
            SolicitudTurnoPublica.objects.filter(paciente=paciente).count(),
            2,
        )
        self.assertEqual(
            Turno.objects.filter(paciente=paciente, motivo__contains="DNI normalizado").count(),
            2,
        )

    def test_solicitud_publica_rollback_no_deja_paciente_ni_email(self):
        datos = self._datos_solicitud_publica(
            documento="48111222",
            motivo="Rollback publico",
        )
        datos.update(
            {
                "odontologo": self.odontologo,
                "fecha": self.fecha_turno,
                "hora_inicio": time(13, 0),
            }
        )

        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with patch(
                "turnos.solicitudes_publicas.services.Turno.objects.create",
                side_effect=ValidationError("Fallo controlado"),
            ):
                with self.assertRaises(ValidationError):
                    crear_solicitud_publica_de_turno(datos)

        self.assertFalse(Paciente.objects.filter(documento="48111222").exists())
        self.assertFalse(Turno.objects.filter(motivo="Rollback publico").exists())
        self.assertFalse(SolicitudTurnoPublica.objects.filter(documento_enviado="48111222").exists())
        self.assertEqual(callbacks, [])
        self.assertEqual(len(mail.outbox), 0)

    def test_fotografia_no_cambia_si_luego_se_modifica_el_paciente(self):
        self.client.post(
            reverse("turnos:solicitud_publica_datos"),
            self._datos_solicitud_publica(
                documento="50111222",
                motivo="Fotografia estable",
            ),
        )
        solicitud = SolicitudTurnoPublica.objects.get(documento_enviado="50111222")
        paciente = solicitud.paciente

        paciente.nombre = "Nombre Interno"
        paciente.email = "interno@example.com"
        paciente.save(update_fields=["nombre", "email", "actualizado_en"])
        solicitud.refresh_from_db()

        self.assertEqual(solicitud.nombre_enviado, "Lucia")
        self.assertEqual(solicitud.email_enviado, "lucia@example.com")

    def test_solicitud_publica_rechaza_horario_no_disponible(self):
        self._crear_turno_existente(Turno.Estado.PENDIENTE)

        response = self.client.post(
            reverse("turnos:solicitud_publica_datos"),
            self._datos_solicitud_publica(
                nombre="Clara",
                apellido="Luna",
                documento="41111222",
                email="",
                motivo="Horario ocupado",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Turno.objects.filter(motivo="Horario ocupado").exists())
        self.assertIn("hora_inicio", response.context["form"].errors)

    def test_solicitud_publica_rechaza_horario_ocupado_por_turno_confirmado(self):
        self._crear_turno_existente(Turno.Estado.CONFIRMADO)

        response = self.client.post(
            reverse("turnos:solicitud_publica_datos"),
            self._datos_solicitud_publica(
                documento="41111223",
                motivo="Horario confirmado ocupado",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Turno.objects.filter(motivo="Horario confirmado ocupado").exists())
        self.assertIn("hora_inicio", response.context["form"].errors)

    def test_solicitud_publica_permite_horario_de_turno_cancelado(self):
        self._crear_turno_existente(Turno.Estado.CANCELADO)

        response = self.client.post(
            reverse("turnos:solicitud_publica_datos"),
            self._datos_solicitud_publica(
                documento="41111224",
                motivo="Horario cancelado reutilizable",
            ),
        )

        turno = Turno.objects.get(motivo="Horario cancelado reutilizable")

        self.assertRedirects(response, reverse("landing_publica"))
        self.assertEqual(turno.hora_inicio, time(10, 0))

    def test_solicitud_publica_rechaza_fecha_pasada(self):
        fecha_pasada = timezone.localdate() - timedelta(days=1)

        response = self.client.post(
            reverse("turnos:solicitud_publica_datos"),
            self._datos_solicitud_publica(
                nombre="Clara",
                apellido="Luna",
                documento="42111222",
                email="",
                fecha=fecha_pasada.isoformat(),
                motivo="Fecha pasada",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Turno.objects.filter(motivo="Fecha pasada").exists())
        self.assertIn("fecha", response.context["form"].errors)
        self.assertContains(response, "La fecha no puede ser anterior a hoy.")

    def test_solicitud_publica_muestra_mensajes_de_error_claros(self):
        response = self.client.post(reverse("turnos:solicitud_publica_datos"), {})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ingresá tu nombre.")
        self.assertContains(response, "Ingresá tu apellido.")
        self.assertContains(response, "Ingresá tu teléfono.")
        self.assertIn("documento", response.context["form"].errors)
        self.assertContains(response, "Elegí un odontólogo.")
        self.assertContains(response, "Elegí una fecha.")
        self.assertContains(response, "Elegí un horario disponible.")

    def test_bandeja_solicitudes_publicas_es_visible_para_recepcion(self):
        solicitud, _ = self._crear_solicitud_existente_con_diferencias()
        self.client.force_login(self._crear_usuario_recepcion())

        response = self.client.get(reverse("turnos:solicitudes_publicas"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Solicitudes por revisar: 1")
        self.assertContains(response, "Paciente existente")
        self.assertContains(
            response,
            reverse("turnos:solicitud_publica_revision", kwargs={"pk": solicitud.id}),
        )

    def test_odontologo_no_puede_ver_bandeja_de_solicitudes_publicas(self):
        solicitud, _ = self._crear_solicitud_existente_con_diferencias()
        self.client.force_login(self.odontologo.usuario)

        response = self.client.get(reverse("turnos:solicitudes_publicas"))
        response_revision = self.client.get(
            reverse("turnos:solicitud_publica_revision", kwargs={"pk": solicitud.id})
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response_revision.status_code, 403)

    def test_revision_solicitud_publica_requiere_csrf_en_post(self):
        solicitud, _ = self._crear_solicitud_existente_con_diferencias()
        usuario = self._crear_usuario_recepcion()
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(usuario)

        response = csrf_client.post(
            reverse("turnos:solicitud_publica_revision", kwargs={"pk": solicitud.id}),
            {"accion": "conservar"},
        )

        solicitud.refresh_from_db()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            solicitud.estado_revision,
            SolicitudTurnoPublica.EstadoRevision.PENDIENTE,
        )

    def test_revision_paciente_nuevo_no_muestra_comparacion_ni_checkboxes(self):
        self.client.post(
            reverse("turnos:solicitud_publica_datos"),
            self._datos_solicitud_publica(
                documento="53111222",
                hora_inicio="12:00",
                motivo="Paciente nuevo UX",
            ),
        )
        solicitud = SolicitudTurnoPublica.objects.get(documento_enviado="53111222")
        self.client.force_login(self._crear_usuario_recepcion())

        response = self.client.get(
            reverse("turnos:solicitud_publica_revision", kwargs={"pk": solicitud.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Revisar paciente nuevo")
        self.assertContains(response, "Pendiente de validación")
        self.assertContains(response, "Datos del paciente")
        self.assertContains(response, "Turno solicitado")
        self.assertNotContains(response, "Diferencias informadas")
        self.assertNotContains(response, "Actualizar este campo")
        self.assertNotContains(response, 'name="campos"')
        self.assertNotIn("campos", response.context["form"].fields)

    def test_formulario_revision_paciente_nuevo_no_contiene_campo_campos(self):
        self.client.post(
            reverse("turnos:solicitud_publica_datos"),
            self._datos_solicitud_publica(
                documento="54111222",
                hora_inicio="12:00",
                motivo="Formulario sin campos",
            ),
        )
        solicitud = SolicitudTurnoPublica.objects.get(documento_enviado="54111222")

        form = RevisionSolicitudTurnoPublicaForm(solicitud=solicitud)

        self.assertNotIn("campos", form.fields)
        self.assertEqual(
            [choice[0] for choice in form.fields["accion"].choices],
            ["validar_paciente", "mantener_pendiente", "rechazar"],
        )

    def test_revision_paciente_existente_muestra_comparacion_y_seleccion_de_campos(self):
        solicitud, _ = self._crear_solicitud_existente_con_diferencias()
        self.client.force_login(self._crear_usuario_recepcion())

        response = self.client.get(
            reverse("turnos:solicitud_publica_revision", kwargs={"pk": solicitud.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Revisar cambios informados")
        self.assertContains(response, "Paciente existente")
        self.assertContains(response, "Diferencias informadas")
        self.assertContains(response, "Diferente")
        self.assertContains(response, 'name="campos"')
        self.assertContains(response, 'value="telefono"')
        self.assertContains(response, "Actualizar campos seleccionados")
        self.assertContains(response, 'type="radio"')
        self.assertNotContains(response, "Campos a actualizar")

    def test_recepcion_aplica_unicamente_campos_seleccionados(self):
        solicitud, paciente = self._crear_solicitud_existente_con_diferencias()
        usuario = self._crear_usuario_recepcion()
        self.client.force_login(usuario)

        response = self.client.post(
            reverse("turnos:solicitud_publica_revision", kwargs={"pk": solicitud.id}),
            {
                "accion": "aplicar_campos",
                "campos": ["telefono"],
                "observaciones": "Validado por llamada.",
            },
        )

        paciente.refresh_from_db()
        solicitud.refresh_from_db()

        self.assertRedirects(response, reverse("turnos:solicitudes_publicas"))
        self.assertEqual(paciente.nombre, "Viejo")
        self.assertEqual(paciente.apellido, "Nombre")
        self.assertEqual(paciente.telefono, "1199999999")
        self.assertEqual(paciente.email, "contacto-original@example.com")
        self.assertEqual(
            solicitud.estado_revision,
            SolicitudTurnoPublica.EstadoRevision.CAMBIOS_APLICADOS,
        )
        self.assertEqual(solicitud.campos_actualizados, ["telefono"])
        self.assertIn("email", solicitud.campos_descartados)
        self.assertEqual(solicitud.revisada_por, usuario)
        self.assertIsNotNone(solicitud.revisada_en)
        self.assertEqual(solicitud.observaciones_revision, "Validado por llamada.")

    def test_recepcion_puede_conservar_datos_actuales(self):
        solicitud, paciente = self._crear_solicitud_existente_con_diferencias()
        usuario = self._crear_usuario_recepcion(username="recepcion.conservar")
        self.client.force_login(usuario)

        response = self.client.post(
            reverse("turnos:solicitud_publica_revision", kwargs={"pk": solicitud.id}),
            {"accion": "conservar", "observaciones": "No se aplican cambios."},
        )

        paciente.refresh_from_db()
        solicitud.refresh_from_db()

        self.assertRedirects(response, reverse("turnos:solicitudes_publicas"))
        self.assertEqual(paciente.telefono, "1100000000")
        self.assertEqual(paciente.email, "contacto-original@example.com")
        self.assertEqual(
            solicitud.estado_revision,
            SolicitudTurnoPublica.EstadoRevision.REVISADA_SIN_CAMBIOS,
        )
        self.assertEqual(solicitud.campos_actualizados, [])
        self.assertFalse(solicitud.requiere_revision)

    def test_recepcion_puede_mantener_pendiente_para_revisar_mas_tarde(self):
        solicitud, paciente = self._crear_solicitud_existente_con_diferencias()
        usuario = self._crear_usuario_recepcion(username="recepcion.pendiente")
        self.client.force_login(usuario)

        response = self.client.post(
            reverse("turnos:solicitud_publica_revision", kwargs={"pk": solicitud.id}),
            {
                "accion": "mantener_pendiente",
                "observaciones": "Falta llamar al paciente.",
            },
            follow=True,
        )

        paciente.refresh_from_db()
        solicitud.refresh_from_db()

        self.assertEqual(response.redirect_chain, [(reverse("turnos:solicitudes_publicas"), 302)])
        self.assertContains(
            response,
            "La solicitud permanece pendiente para revisarla más adelante.",
        )
        self.assertEqual(paciente.nombre, "Viejo")
        self.assertEqual(paciente.telefono, "1100000000")
        self.assertEqual(
            solicitud.estado_revision,
            SolicitudTurnoPublica.EstadoRevision.PENDIENTE,
        )
        self.assertTrue(solicitud.requiere_revision)
        self.assertIsNone(solicitud.revisada_por)
        self.assertIsNone(solicitud.revisada_en)
        self.assertEqual(solicitud.observaciones_revision, "Falta llamar al paciente.")

    def test_recepcion_rechaza_solicitud_sin_cancelar_turno_ni_eliminar_paciente(self):
        solicitud, paciente = self._crear_solicitud_existente_con_diferencias()
        turno = solicitud.turno
        usuario = self._crear_usuario_recepcion(username="recepcion.rechazar")
        self.client.force_login(usuario)

        response = self.client.post(
            reverse("turnos:solicitud_publica_revision", kwargs={"pk": solicitud.id}),
            {
                "accion": "rechazar",
                "observaciones": "Solicitud no validada por recepción.",
            },
        )

        solicitud.refresh_from_db()
        turno.refresh_from_db()

        self.assertRedirects(response, reverse("turnos:solicitudes_publicas"))
        self.assertTrue(Paciente.objects.filter(pk=paciente.pk).exists())
        self.assertTrue(Turno.objects.filter(pk=turno.pk).exists())
        self.assertEqual(turno.estado, Turno.Estado.PENDIENTE)
        self.assertEqual(
            solicitud.estado_revision,
            SolicitudTurnoPublica.EstadoRevision.RECHAZADA,
        )
        self.assertFalse(solicitud.requiere_revision)
        self.assertEqual(solicitud.revisada_por, usuario)

    def test_revision_no_puede_procesarse_dos_veces(self):
        solicitud, paciente = self._crear_solicitud_existente_con_diferencias()
        self.client.force_login(self._crear_usuario_recepcion())
        url = reverse("turnos:solicitud_publica_revision", kwargs={"pk": solicitud.id})

        self.client.post(url, {"accion": "conservar"})
        response = self.client.post(
            url,
            {
                "accion": "aplicar_campos",
                "campos": ["email"],
            },
        )

        paciente.refresh_from_db()
        solicitud.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(paciente.email, "contacto-original@example.com")
        self.assertEqual(
            solicitud.estado_revision,
            SolicitudTurnoPublica.EstadoRevision.REVISADA_SIN_CAMBIOS,
        )
        self.assertContains(response, "Esta solicitud ya fue revisada.")

    def test_recepcion_valida_paciente_nuevo_desde_revision(self):
        self.client.post(
            reverse("turnos:solicitud_publica_datos"),
            self._datos_solicitud_publica(
                documento="52111222",
                hora_inicio="12:00",
                motivo="Validacion nueva",
            ),
        )
        solicitud = SolicitudTurnoPublica.objects.get(documento_enviado="52111222")
        paciente = solicitud.paciente
        usuario = self._crear_usuario_recepcion(username="recepcion.validar")
        self.client.force_login(usuario)

        response = self.client.post(
            reverse("turnos:solicitud_publica_revision", kwargs={"pk": solicitud.id}),
            {"accion": "validar_paciente"},
        )

        paciente.refresh_from_db()
        solicitud.refresh_from_db()

        self.assertRedirects(response, reverse("turnos:solicitudes_publicas"))
        self.assertEqual(
            paciente.estado_validacion_datos,
            Paciente.EstadoValidacionDatos.VALIDADO,
        )
        self.assertEqual(paciente.validado_por, usuario)
        self.assertIsNotNone(paciente.validado_en)
        self.assertEqual(
            solicitud.estado_revision,
            SolicitudTurnoPublica.EstadoRevision.REVISADA_SIN_CAMBIOS,
        )

    def test_admin_solicitud_publica_mantiene_fotografia_readonly(self):
        from django.contrib.admin.sites import AdminSite

        from turnos.admin import SolicitudTurnoPublicaAdmin

        admin_model = SolicitudTurnoPublicaAdmin(SolicitudTurnoPublica, AdminSite())
        readonly = set(admin_model.get_readonly_fields(None))

        self.assertTrue(
            {
                "documento_enviado",
                "nombre_enviado",
                "apellido_enviado",
                "telefono_enviado",
                "email_enviado",
                "motivo_enviado",
            }.issubset(readonly)
        )

    def _crear_paciente_publico(self, documento="38111222", email="lucia@example.com"):
        return Paciente.objects.create(
            nombre="Lucia",
            apellido="Paz",
            documento=documento,
            telefono="1155667788",
            email=email,
        )

    def _crear_turno_publico(self, paciente, estado=Turno.Estado.PENDIENTE, hora_inicio=time(9, 0), motivo="Control"):
        return Turno.objects.create(
            paciente=paciente,
            odontologo=self.odontologo,
            fecha=self.fecha_turno,
            hora_inicio=hora_inicio,
            duracion_minutos=30,
            estado=estado,
            motivo=motivo,
        )

    def _solicitar_y_validar_acceso_publico(self, paciente, codigo="123456"):
        mail.outbox.clear()

        with patch("turnos.public_access.services.generar_codigo_otp", return_value=codigo):
            response = self.client.post(
                reverse("turnos:acceso_publico_solicitar"),
                {"documento": paciente.documento},
                follow=True,
            )

        self.assertEqual(response.redirect_chain, [(reverse("turnos:acceso_publico_verificar"), 302)])
        desafio = DesafioAccesoPublicoTurnos.objects.get(paciente=paciente)

        self.assertTrue(check_password(codigo, desafio.codigo_hash))
        self.assertNotIn(codigo, desafio.codigo_hash)
        self.assertEqual(len(mail.outbox), 1)

        response = self.client.post(
            reverse("turnos:acceso_publico_verificar"),
            {"codigo": codigo},
        )

        self.assertRedirects(response, reverse("turnos:mis_turnos_publico"))
        desafio.refresh_from_db()
        paciente.refresh_from_db()

        self.assertIsNotNone(desafio.validado_en)
        self.assertIsNotNone(paciente.email_verificado_en)
        self.assertIn(PUBLIC_ACCESS_SESSION_KEY, self.client.session)
        self.assertNotIn(PUBLIC_ACCESS_PENDING_CHALLENGE_KEY, self.client.session)
        return desafio

    def _generar_permiso_publico(self, turno, tipo_accion):
        self.client.get(reverse("turnos:mis_turnos_publico"))
        accion = AccionPublicaTurno.objects.get(
            turno=turno,
            tipo_accion=tipo_accion,
            utilizado_en__isnull=True,
            revocado_en__isnull=True,
        )
        token = self.client.session[PUBLIC_ACTION_TOKENS_SESSION_KEY][str(accion.id)]
        return accion, token

    def test_solicitar_acceso_publico_no_enumera_pacientes(self):
        paciente = self._crear_paciente_publico()
        self._crear_turno_publico(paciente, motivo="Motivo privado")

        with patch("turnos.public_access.services.generar_codigo_otp", return_value="123456"):
            response_real = self.client.post(
                reverse("turnos:acceso_publico_solicitar"),
                {"documento": paciente.documento},
                follow=True,
            )
            response_ficticia = self.client.post(
                reverse("turnos:acceso_publico_solicitar"),
                {"documento": "99999999"},
                follow=True,
            )

        self.assertEqual(response_real.status_code, 200)
        self.assertEqual(response_ficticia.status_code, 200)
        self.assertContains(response_real, "Revis")
        self.assertContains(response_ficticia, "Revis")
        self.assertNotContains(response_real, paciente.email)
        self.assertNotContains(response_real, paciente.telefono)
        self.assertNotContains(response_real, "Motivo privado")
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(
            DesafioAccesoPublicoTurnos.objects.filter(
                paciente__isnull=True,
                canal=DesafioAccesoPublicoTurnos.Canal.FICTICIO,
            ).exists()
        )

    @override_settings(
        TURNSTILE_ENABLED=True,
        TURNSTILE_SITE_KEY="site-key",
        TURNSTILE_SECRET_KEY="secret-key",
        TURNSTILE_REQUIRED_AFTER_ATTEMPTS=0,
    )
    def test_solicitar_acceso_publico_valida_turnstile_si_corresponde(self):
        paciente = self._crear_paciente_publico()

        with (
            patch("turnos.public_access.views.validar_turnstile") as validar_turnstile,
            patch("turnos.public_access.services.generar_codigo_otp", return_value="123456"),
        ):
            validar_turnstile.return_value = SimpleNamespace(valido=True)
            response = self.client.post(
                reverse("turnos:acceso_publico_solicitar"),
                {
                    "documento": paciente.documento,
                    "cf-turnstile-response": "turnstile-token",
                },
            )

        self.assertRedirects(response, reverse("turnos:acceso_publico_verificar"))
        validar_turnstile.assert_called_once_with("turnstile-token", "127.0.0.1")
        self.assertTrue(DesafioAccesoPublicoTurnos.objects.filter(paciente=paciente).exists())

    @override_settings(
        TURNSTILE_ENABLED=True,
        TURNSTILE_SITE_KEY="site-key",
        TURNSTILE_SECRET_KEY="secret-key",
        TURNSTILE_REQUIRED_AFTER_ATTEMPTS=99,
    )
    def test_solicitar_acceso_publico_no_exige_turnstile_antes_del_umbral(self):
        paciente = self._crear_paciente_publico()

        with (
            patch("turnos.public_access.views.validar_turnstile") as validar_turnstile,
            patch("turnos.public_access.services.generar_codigo_otp", return_value="123456"),
        ):
            response = self.client.post(
                reverse("turnos:acceso_publico_solicitar"),
                {"documento": paciente.documento},
            )

        self.assertRedirects(response, reverse("turnos:acceso_publico_verificar"))
        validar_turnstile.assert_not_called()

    @override_settings(
        TURNSTILE_ENABLED=True,
        TURNSTILE_SITE_KEY="site-key",
        TURNSTILE_SECRET_KEY="secret-key",
        TURNSTILE_REQUIRED_AFTER_ATTEMPTS=0,
    )
    def test_solicitar_acceso_publico_rechaza_turnstile_invalido(self):
        paciente = self._crear_paciente_publico()

        with patch("turnos.public_access.views.validar_turnstile") as validar_turnstile:
            validar_turnstile.return_value = SimpleNamespace(valido=False)
            response = self.client.post(
                reverse("turnos:acceso_publico_solicitar"),
                {
                    "documento": paciente.documento,
                    "cf-turnstile-response": "turnstile-token",
                },
            )

        self.assertEqual(response.status_code, 200)
        validar_turnstile.assert_called_once_with("turnstile-token", "127.0.0.1")
        self.assertFalse(DesafioAccesoPublicoTurnos.objects.exists())

    def test_mis_turnos_publico_requiere_sesion_verificada(self):
        response = self.client.get(reverse("turnos:mis_turnos_publico"))

        self.assertRedirects(response, reverse("turnos:acceso_publico_solicitar"))

    def test_codigo_correcto_habilita_sesion_y_lista_solo_turnos_activos_propios(self):
        paciente = self._crear_paciente_publico()
        otro_paciente = self._crear_paciente_publico(documento="39111222", email="otro@example.com")
        self._crear_turno_publico(paciente, hora_inicio=time(9, 0), motivo="Motivo pendiente privado")
        self._crear_turno_publico(paciente, estado=Turno.Estado.CONFIRMADO, hora_inicio=time(10, 0), motivo="Motivo confirmado privado")
        self._crear_turno_publico(paciente, estado=Turno.Estado.CANCELADO, hora_inicio=time(11, 0), motivo="Motivo cancelado privado")
        self._crear_turno_publico(otro_paciente, hora_inicio=time(12, 0), motivo="Turno ajeno privado")

        self._solicitar_y_validar_acceso_publico(paciente)
        response = self.client.get(reverse("turnos:mis_turnos_publico"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "09:00 a 09:30")
        self.assertContains(response, "10:00 a 10:30")
        self.assertNotContains(response, "11:00 a 11:30")
        self.assertNotContains(response, "12:00 a 12:30")
        self.assertNotContains(response, "Motivo pendiente privado")
        self.assertNotContains(response, "Motivo confirmado privado")

    @override_settings(TURNOS_PUBLIC_OTP_ATTEMPTS=1)
    def test_codigo_incorrecto_invalida_desafio_al_superar_intentos(self):
        paciente = self._crear_paciente_publico()

        with patch("turnos.public_access.services.generar_codigo_otp", return_value="123456"):
            self.client.post(
                reverse("turnos:acceso_publico_solicitar"),
                {"documento": paciente.documento},
            )

        response = self.client.post(
            reverse("turnos:acceso_publico_verificar"),
            {"codigo": "000000"},
        )
        desafio = DesafioAccesoPublicoTurnos.objects.get(paciente=paciente)

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(desafio.invalidado_en)
        self.assertNotIn(PUBLIC_ACCESS_SESSION_KEY, self.client.session)

    def test_desafio_ficticio_no_concede_acceso(self):
        with patch("turnos.public_access.services.generar_codigo_otp", return_value="123456"):
            self.client.post(
                reverse("turnos:acceso_publico_solicitar"),
                {"documento": "99999999"},
            )

        response = self.client.post(
            reverse("turnos:acceso_publico_verificar"),
            {"codigo": "123456"},
        )
        desafio = DesafioAccesoPublicoTurnos.objects.get(paciente__isnull=True)

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(desafio.invalidado_en)
        self.assertNotIn(PUBLIC_ACCESS_SESSION_KEY, self.client.session)

    def test_codigo_vencido_no_valida(self):
        paciente = self._crear_paciente_publico()

        with patch("turnos.public_access.services.generar_codigo_otp", return_value="123456"):
            self.client.post(
                reverse("turnos:acceso_publico_solicitar"),
                {"documento": paciente.documento},
            )

        desafio = DesafioAccesoPublicoTurnos.objects.get(paciente=paciente)
        desafio.expira_en = timezone.now() - timedelta(seconds=1)
        desafio.save(update_fields=["expira_en"])

        response = self.client.post(
            reverse("turnos:acceso_publico_verificar"),
            {"codigo": "123456"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(PUBLIC_ACCESS_SESSION_KEY, self.client.session)

    def test_codigo_utilizado_no_puede_reutilizarse(self):
        paciente = self._crear_paciente_publico()
        desafio = self._solicitar_y_validar_acceso_publico(paciente)
        self.client.post(reverse("turnos:acceso_publico_cerrar"))
        session = self.client.session
        session[PUBLIC_ACCESS_PENDING_CHALLENGE_KEY] = str(desafio.id)
        session.save()

        response = self.client.post(
            reverse("turnos:acceso_publico_verificar"),
            {"codigo": "123456"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(PUBLIC_ACCESS_SESSION_KEY, self.client.session)

    def test_solicitar_codigo_nuevo_invalida_desafio_anterior(self):
        paciente = self._crear_paciente_publico()

        with patch("turnos.public_access.services.generar_codigo_otp", return_value="123456"):
            self.client.post(
                reverse("turnos:acceso_publico_solicitar"),
                {"documento": paciente.documento},
            )
            primer_desafio = DesafioAccesoPublicoTurnos.objects.get(paciente=paciente)
            self.client.post(
                reverse("turnos:acceso_publico_solicitar"),
                {"documento": paciente.documento},
            )

        primer_desafio.refresh_from_db()
        desafios_activos = DesafioAccesoPublicoTurnos.objects.filter(
            paciente=paciente,
            invalidado_en__isnull=True,
            validado_en__isnull=True,
        )

        self.assertIsNotNone(primer_desafio.invalidado_en)
        self.assertEqual(desafios_activos.count(), 1)

    def test_reenvio_respeta_cooldown(self):
        paciente = self._crear_paciente_publico()

        with patch("turnos.public_access.services.generar_codigo_otp", return_value="123456"):
            self.client.post(
                reverse("turnos:acceso_publico_solicitar"),
                {"documento": paciente.documento},
            )
            response = self.client.post(
                reverse("turnos:acceso_publico_verificar"),
                {"accion": "reenviar"},
            )

        desafio = DesafioAccesoPublicoTurnos.objects.get(paciente=paciente)

        self.assertRedirects(response, reverse("turnos:acceso_publico_verificar"))
        self.assertEqual(desafio.cantidad_envios, 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_cerrar_acceso_elimina_sesion_publica(self):
        paciente = self._crear_paciente_publico()
        self._crear_turno_publico(paciente)
        self._solicitar_y_validar_acceso_publico(paciente)

        response = self.client.post(reverse("turnos:acceso_publico_cerrar"))

        self.assertRedirects(response, reverse("turnos:acceso_publico_solicitar"))
        self.assertNotIn(PUBLIC_ACCESS_SESSION_KEY, self.client.session)
        self.assertRedirects(
            self.client.get(reverse("turnos:mis_turnos_publico")),
            reverse("turnos:acceso_publico_solicitar"),
        )

    def test_cancelacion_publica_segura_requiere_post_y_permiso_unico(self):
        paciente = self._crear_paciente_publico()
        turno = self._crear_turno_publico(
            paciente,
            estado=Turno.Estado.CONFIRMADO,
            motivo="Control",
        )
        self._solicitar_y_validar_acceso_publico(paciente)
        accion, token = self._generar_permiso_publico(
            turno,
            AccionPublicaTurno.TipoAccion.CANCELAR,
        )
        url = reverse("turnos:mis_turnos_cancelar", kwargs={"accion_id": accion.id})

        self.assertEqual(self.client.get(url).status_code, 405)

        response = self.client.post(
            url,
            {
                "accion_token": token,
                "motivo_cancelacion": "No puedo asistir.",
            },
        )
        turno.refresh_from_db()
        accion.refresh_from_db()

        self.assertRedirects(response, reverse("turnos:mis_turnos_publico"))
        self.assertEqual(turno.estado, Turno.Estado.CANCELADO)
        self.assertEqual(turno.motivo_cancelacion_paciente, "No puedo asistir.")
        self.assertIsNotNone(accion.utilizado_en)

        response = self.client.post(
            url,
            {
                "accion_token": token,
                "motivo_cancelacion": "Segundo intento",
            },
        )
        turno.refresh_from_db()

        self.assertRedirects(response, reverse("turnos:mis_turnos_publico"))
        self.assertEqual(turno.motivo_cancelacion_paciente, "No puedo asistir.")

    def test_permiso_publico_no_autoriza_turno_de_otro_paciente(self):
        paciente = self._crear_paciente_publico()
        otro_paciente = self._crear_paciente_publico(documento="39111222", email="otro@example.com")
        turno = self._crear_turno_publico(paciente, estado=Turno.Estado.CONFIRMADO)
        self._crear_turno_publico(otro_paciente, hora_inicio=time(12, 0))

        self._solicitar_y_validar_acceso_publico(paciente)
        accion, token = self._generar_permiso_publico(
            turno,
            AccionPublicaTurno.TipoAccion.CANCELAR,
        )
        self.client.post(reverse("turnos:acceso_publico_cerrar"))
        self._solicitar_y_validar_acceso_publico(otro_paciente)

        response = self.client.post(
            reverse("turnos:mis_turnos_cancelar", kwargs={"accion_id": accion.id}),
            {
                "accion_token": token,
                "motivo_cancelacion": "Intento ajeno",
            },
        )
        turno.refresh_from_db()

        self.assertRedirects(response, reverse("turnos:mis_turnos_publico"))
        self.assertEqual(turno.estado, Turno.Estado.CONFIRMADO)
        self.assertEqual(turno.motivo_cancelacion_paciente, "")

    def test_reprogramacion_publica_segura_valida_disponibilidad_y_rota_version(self):
        paciente = self._crear_paciente_publico()
        turno = self._crear_turno_publico(paciente, estado=Turno.Estado.PENDIENTE)
        version_anterior = turno.version_publica
        self._crear_turno_publico(
            self._crear_paciente_publico(documento="40111222", email="ocupado@example.com"),
            estado=Turno.Estado.CONFIRMADO,
            hora_inicio=time(10, 0),
            motivo="Horario ocupado",
        )
        self._solicitar_y_validar_acceso_publico(paciente)
        accion, token = self._generar_permiso_publico(
            turno,
            AccionPublicaTurno.TipoAccion.REPROGRAMAR,
        )
        url = reverse("turnos:mis_turnos_reprogramar", kwargs={"accion_id": accion.id})
        horarios_url = reverse(
            "turnos:mis_turnos_reprogramar_horarios",
            kwargs={"accion_id": accion.id},
        )

        pagina = self.client.get(url)
        self.assertEqual(pagina.status_code, 200)
        self.assertContains(pagina, horarios_url)
        self.assertNotContains(pagina, f'data-turno-id="{turno.pk}"')
        self.assertNotContains(pagina, f'turno_id={turno.pk}')

        response_horarios = self.client.get(
            horarios_url,
            {"fecha": self.fecha_turno.isoformat()},
        )
        horarios = [horario["value"] for horario in response_horarios.json()["horarios"]]

        self.assertEqual(response_horarios.status_code, 200)
        self.assertIn("09:00", horarios)
        self.assertIn("12:00", horarios)
        self.assertNotIn("10:00", horarios)

        response = self.client.post(
            url,
            {
                "accion_token": token,
                "fecha": self.fecha_turno.isoformat(),
                "hora_inicio": "10:00",
            },
        )
        turno.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertIn("hora_inicio", response.context["form"].errors)
        self.assertEqual(turno.hora_inicio, time(9, 0))

        response = self.client.post(
            url,
            {
                "accion_token": token,
                "fecha": self.fecha_turno.isoformat(),
                "hora_inicio": "12:00",
            },
        )
        turno.refresh_from_db()
        accion.refresh_from_db()

        self.assertRedirects(response, reverse("turnos:mis_turnos_publico"))
        self.assertEqual(turno.hora_inicio, time(12, 0))
        self.assertNotEqual(turno.version_publica, version_anterior)
        self.assertIsNotNone(accion.utilizado_en)

    def test_permiso_publico_caduca_si_el_turno_cambia(self):
        paciente = self._crear_paciente_publico()
        turno = self._crear_turno_publico(paciente, estado=Turno.Estado.PENDIENTE)
        self._solicitar_y_validar_acceso_publico(paciente)
        accion, token = self._generar_permiso_publico(
            turno,
            AccionPublicaTurno.TipoAccion.CANCELAR,
        )

        turno.hora_inicio = time(12, 0)
        turno.save(update_fields=["hora_inicio", "actualizado_en"])

        response = self.client.post(
            reverse("turnos:mis_turnos_cancelar", kwargs={"accion_id": accion.id}),
            {
                "accion_token": token,
                "motivo_cancelacion": "Token viejo",
            },
        )
        turno.refresh_from_db()

        self.assertRedirects(response, reverse("turnos:mis_turnos_publico"))
        self.assertEqual(turno.estado, Turno.Estado.PENDIENTE)
        self.assertEqual(turno.motivo_cancelacion_paciente, "")

    @override_settings(
        TURNOS_PUBLIC_ACCESS_REQUEST_LIMIT=1,
        TURNOS_PUBLIC_ACCESS_REQUEST_WINDOW_SECONDS=600,
    )
    def test_solicitud_acceso_publico_aplica_rate_limit_con_hashes(self):
        cache.clear()
        paciente = self._crear_paciente_publico()

        with patch("turnos.public_access.services.generar_codigo_otp", return_value="123456"):
            self.client.post(
                reverse("turnos:acceso_publico_solicitar"),
                {"documento": paciente.documento},
            )
            self.client.post(
                reverse("turnos:acceso_publico_solicitar"),
                {"documento": paciente.documento},
            )

        dni_hash = hash_valor_publico(paciente.documento, "dni")
        cache_key = construir_clave("solicitud_dni", dni_hash)

        self.assertEqual(cache.get(cache_key), 2)
        self.assertNotIn(paciente.documento, cache_key)
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(
        TURNOS_PUBLIC_ACCESS_REQUEST_LIMIT=1,
        TURNOS_PUBLIC_ACCESS_REQUEST_WINDOW_SECONDS=600,
    )
    def test_solicitud_acceso_publico_aplica_rate_limit_por_ip(self):
        cache.clear()
        paciente = self._crear_paciente_publico()
        otro_paciente = self._crear_paciente_publico(documento="39111222", email="otro@example.com")

        with patch("turnos.public_access.services.generar_codigo_otp", return_value="123456"):
            self.client.post(
                reverse("turnos:acceso_publico_solicitar"),
                {"documento": paciente.documento},
                REMOTE_ADDR="203.0.113.10",
            )
            self.client.post(
                reverse("turnos:acceso_publico_solicitar"),
                {"documento": otro_paciente.documento},
                REMOTE_ADDR="203.0.113.10",
            )

        ip_hash = hash_valor_publico("203.0.113.10", "ip")
        cache_key = construir_clave("solicitud_ip", ip_hash)

        self.assertEqual(cache.get(cache_key), 2)
        self.assertNotIn("203.0.113.10", cache_key)
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(
        TURNSTILE_ENABLED=True,
        TURNSTILE_SECRET_KEY="secret-key",
        TURNSTILE_VERIFY_URL="https://turnstile.example.test/siteverify",
        TURNSTILE_TIMEOUT_SECONDS=1,
    )
    def test_turnstile_timeout_falla_de_forma_segura(self):
        from turnos.integrations.turnstile import validar_turnstile

        with patch("turnos.integrations.turnstile.urlopen", side_effect=TimeoutError):
            resultado = validar_turnstile("turnstile-token", "203.0.113.10")

        self.assertFalse(resultado.valido)
        self.assertTrue(resultado.requerido)
        self.assertEqual(resultado.error, "TimeoutError")

    def test_comando_limpieza_elimina_desafios_y_acciones_inactivas(self):
        paciente = self._crear_paciente_publico()
        turno = self._crear_turno_publico(paciente)
        ahora = timezone.now()
        desafio_expirado = DesafioAccesoPublicoTurnos.objects.create(
            paciente=paciente,
            codigo_hash="hash",
            expira_en=ahora - timedelta(minutes=1),
        )
        desafio_activo = DesafioAccesoPublicoTurnos.objects.create(
            paciente=paciente,
            codigo_hash="hash",
            expira_en=ahora + timedelta(minutes=5),
        )
        accion_expirada = AccionPublicaTurno.objects.create(
            paciente=paciente,
            turno=turno,
            tipo_accion=AccionPublicaTurno.TipoAccion.CANCELAR,
            token_hash="hash",
            version_turno=turno.version_publica,
            expira_en=ahora - timedelta(minutes=1),
        )
        accion_usada = AccionPublicaTurno.objects.create(
            paciente=paciente,
            turno=turno,
            tipo_accion=AccionPublicaTurno.TipoAccion.REPROGRAMAR,
            token_hash="hash",
            version_turno=turno.version_publica,
            expira_en=ahora + timedelta(minutes=5),
            utilizado_en=ahora,
        )
        accion_activa = AccionPublicaTurno.objects.create(
            paciente=paciente,
            turno=turno,
            tipo_accion=AccionPublicaTurno.TipoAccion.CANCELAR,
            token_hash="hash",
            version_turno=turno.version_publica,
            expira_en=ahora + timedelta(minutes=5),
        )
        salida = StringIO()

        call_command("limpiar_desafios_acceso_publico", stdout=salida)

        self.assertFalse(DesafioAccesoPublicoTurnos.objects.filter(pk=desafio_expirado.pk).exists())
        self.assertTrue(DesafioAccesoPublicoTurnos.objects.filter(pk=desafio_activo.pk).exists())
        self.assertFalse(AccionPublicaTurno.objects.filter(pk=accion_expirada.pk).exists())
        self.assertFalse(AccionPublicaTurno.objects.filter(pk=accion_usada.pk).exists())
        self.assertTrue(AccionPublicaTurno.objects.filter(pk=accion_activa.pk).exists())
        self.assertIn("acciones eliminadas", salida.getvalue())

@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="turnos@example.com",
)
class TurnoEmailNotificationTests(TestCase):
    def setUp(self):
        usuario = get_user_model().objects.create_user(
            username="recepcion.email",
            first_name="Rocio",
            last_name="Email",
        )
        asignar_rol(usuario, ROL_RECEPCIONISTA)
        self.client.force_login(usuario)
        self.odontologo = Odontologo.objects.create(
            usuario=usuario,
            matricula="MN-EMAIL",
            duracion_turno_minutos=30,
        )
        crear_disponibilidad_laboral(self.odontologo)
        self.fecha_turno = obtener_fecha_laboral_futura()
        self.paciente = Paciente.objects.create(
            nombre="Paula",
            apellido="Correo",
            documento="45111222",
            email="paula@example.com",
        )

    def test_solicitud_publica_envia_email_al_paciente(self):
        self.client.logout()

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("turnos:solicitud_publica_datos"),
                {
                    "nombre": "Lucia",
                    "apellido": "Mail",
                    "documento": "46111222",
                    "telefono": "1155667788",
                    "email": "lucia@example.com",
                    "odontologo": self.odontologo.pk,
                    "fecha": self.fecha_turno.isoformat(),
                    "hora_inicio": "10:00",
                    "motivo": "Consulta inicial",
                },
            )

        self.assertRedirects(response, reverse("landing_publica"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["lucia@example.com"])
        self.assertIn("Recibimos tu solicitud de turno", mail.outbox[0].subject)
        self.assertIn("Pendiente", mail.outbox[0].body)

    def test_confirmar_turno_envia_email_al_paciente(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.PENDIENTE,
            motivo="Control",
        )

        response = self.client.post(
            reverse("turnos:confirmar", kwargs={"pk": turno.pk}),
            {"duracion_rapida": 30},
        )

        self.assertRedirects(response, reverse("turnos:detalle", kwargs={"pk": turno.pk}))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["paula@example.com"])
        self.assertIn("Tu turno fue confirmado", mail.outbox[0].subject)
        self.assertIn("Confirmado", mail.outbox[0].body)

    def test_cancelar_turno_envia_email_al_paciente(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
            motivo="Control",
        )

        response = self.client.post(reverse("turnos:cancelar", kwargs={"pk": turno.pk}))

        self.assertRedirects(response, reverse("turnos:detalle", kwargs={"pk": turno.pk}))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["paula@example.com"])
        self.assertIn("Tu turno fue cancelado", mail.outbox[0].subject)
        self.assertIn("Cancelado", mail.outbox[0].body)

    def test_reprogramar_turno_actualiza_google_calendar_y_envia_email(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
            motivo="Control",
        )

        with patch("turnos.services.sincronizar_turno_actualizado") as sincronizar_mock:
            reprogramar_turno(
                turno,
                {
                    "fecha": date(2026, 5, 8),
                    "hora_inicio": time(11, 0),
                    "duracion_minutos": 30,
                },
            )

        turno.refresh_from_db()
        self.assertEqual(turno.hora_inicio, time(11, 0))
        sincronizar_mock.assert_called_once()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["paula@example.com"])
        self.assertIn("Tu turno fue reprogramado", mail.outbox[0].subject)
        self.assertIn("Nuevo horario: 11:00", mail.outbox[0].body)

    def test_recordatorio_turno_envia_email_al_paciente(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
            motivo="Control",
        )

        resultado = notificar_recordatorio_turno(turno)

        self.assertTrue(resultado.enviada)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["paula@example.com"])
        self.assertIn("Recordatorio de tu turno", mail.outbox[0].subject)
        self.assertIn("Te recordamos que tenes un turno confirmado.", mail.outbox[0].body)

    def test_recordatorios_se_envian_solo_a_turnos_confirmados_proximos(self):
        ahora = timezone.make_aware(
            datetime(2026, 5, 7, 10, 0),
            timezone.get_current_timezone(),
        )
        turno_proximo = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
            motivo="Recordatorio esperado",
        )
        turno_pendiente = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 30),
            duracion_minutos=30,
            estado=Turno.Estado.PENDIENTE,
        )
        turno_fuera_de_ventana = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(11, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
        )
        paciente_sin_email = Paciente.objects.create(
            nombre="Sin",
            apellido="Email",
            documento="47111222",
        )
        turno_sin_email = Turno.objects.create(
            paciente=paciente_sin_email,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(11, 30),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
        )

        turnos_para_recordatorio = obtener_turnos_para_recordatorio(
            horas_anticipacion=24,
            ahora=ahora,
        )
        resultado = enviar_recordatorios_email(
            horas_anticipacion=24,
            ahora=ahora,
        )

        turno_proximo.refresh_from_db()
        turno_pendiente.refresh_from_db()
        turno_fuera_de_ventana.refresh_from_db()
        turno_sin_email.refresh_from_db()

        self.assertEqual(turnos_para_recordatorio, [turno_proximo])
        self.assertEqual(resultado.encontrados, 1)
        self.assertEqual(resultado.enviados, 1)
        self.assertEqual(resultado.fallidos, 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["paula@example.com"])
        self.assertIsNotNone(turno_proximo.recordatorio_email_enviado_en)
        self.assertIsNone(turno_pendiente.recordatorio_email_enviado_en)
        self.assertIsNone(turno_fuera_de_ventana.recordatorio_email_enviado_en)
        self.assertIsNone(turno_sin_email.recordatorio_email_enviado_en)

    def test_recordatorios_no_se_envian_dos_veces(self):
        ahora = timezone.make_aware(
            datetime(2026, 5, 7, 10, 0),
            timezone.get_current_timezone(),
        )
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
        )

        primer_resultado = enviar_recordatorios_email(
            horas_anticipacion=24,
            ahora=ahora,
        )
        segundo_resultado = enviar_recordatorios_email(
            horas_anticipacion=24,
            ahora=ahora,
        )

        turno.refresh_from_db()

        self.assertEqual(primer_resultado.enviados, 1)
        self.assertEqual(segundo_resultado.encontrados, 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIsNotNone(turno.recordatorio_email_enviado_en)

    def test_no_envia_email_si_el_paciente_no_tiene_email(self):
        self.paciente.email = ""
        self.paciente.save(update_fields=["email", "actualizado_en"])
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.PENDIENTE,
        )

        self.client.post(
            reverse("turnos:confirmar", kwargs={"pk": turno.pk}),
            {"duracion_rapida": 30},
        )

        self.assertEqual(mail.outbox, [])

    def test_error_smtp_no_interrumpe_confirmacion_del_turno(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.PENDIENTE,
        )

        with self.assertLogs("turnos.notifications", level="ERROR"):
            with patch("turnos.notifications.send_mail", side_effect=OSError("SMTP caido")):
                response = self.client.post(
                    reverse("turnos:confirmar", kwargs={"pk": turno.pk}),
                    {"duracion_rapida": 30},
                )

        turno.refresh_from_db()
        self.assertRedirects(response, reverse("turnos:detalle", kwargs={"pk": turno.pk}))
        self.assertEqual(turno.estado, Turno.Estado.CONFIRMADO)

    def test_notificacion_puede_fallar_fuerte_para_pruebas_reales(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
        )

        with self.assertLogs("turnos.notifications", level="ERROR"):
            with patch("turnos.notifications.send_mail", side_effect=OSError("SMTP caido")):
                with self.assertRaises(OSError):
                    notificar_turno_confirmado(turno, fail_silently=False)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="turnos@example.com",
)
class EmailManagementCommandTests(TestCase):
    def test_probar_email_envia_mensaje_de_prueba(self):
        salida = StringIO()

        call_command("probar_email", "destino@example.com", stdout=salida)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["destino@example.com"])
        self.assertIn("Email de prueba", mail.outbox[0].subject)
        self.assertIn("configuración de email", mail.outbox[0].body)
        self.assertIn("Email de prueba enviado a destino@example.com.", salida.getvalue())

    def test_probar_email_rechaza_destinatario_invalido(self):
        with self.assertRaises(CommandError):
            call_command("probar_email", "email-invalido")

    def test_probar_notificaciones_email_envia_los_tres_mensajes(self):
        salida = StringIO()

        call_command("probar_notificaciones_email", "destino@example.com", stdout=salida)

        self.assertEqual(len(mail.outbox), 3)
        self.assertEqual(mail.outbox[0].to, ["destino@example.com"])
        self.assertEqual(mail.outbox[1].to, ["destino@example.com"])
        self.assertEqual(mail.outbox[2].to, ["destino@example.com"])
        self.assertIn("Recibimos tu solicitud de turno", mail.outbox[0].subject)
        self.assertIn("Tu turno fue confirmado", mail.outbox[1].subject)
        self.assertIn("Tu turno fue cancelado", mail.outbox[2].subject)
        self.assertIn("Se enviaron 3 notificaciones a destino@example.com.", salida.getvalue())

    def test_probar_notificaciones_email_rechaza_destinatario_invalido(self):
        with self.assertRaises(CommandError):
            call_command("probar_notificaciones_email", "email-invalido")

    def test_enviar_recordatorios_email_envia_turnos_confirmados_proximos(self):
        usuario = get_user_model().objects.create_user(
            username="dra.recordatorios",
            first_name="Rita",
            last_name="Recordatorios",
        )
        odontologo = Odontologo.objects.create(
            usuario=usuario,
            matricula="MN-REC",
            duracion_turno_minutos=30,
        )
        crear_disponibilidad_laboral(odontologo)
        paciente = Paciente.objects.create(
            nombre="Paciente",
            apellido="Recordatorio",
            documento="48111222",
            email="paciente@example.com",
        )
        turno = Turno.objects.create(
            paciente=paciente,
            odontologo=odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
        )
        ahora = timezone.make_aware(
            datetime(2026, 5, 7, 10, 0),
            timezone.get_current_timezone(),
        )
        salida = StringIO()

        with patch("turnos.services.timezone.now", return_value=ahora):
            call_command("enviar_recordatorios_email", "--horas", "24", stdout=salida)

        turno.refresh_from_db()

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["paciente@example.com"])
        self.assertIn("Recordatorio de tu turno", mail.outbox[0].subject)
        self.assertIsNotNone(turno.recordatorio_email_enviado_en)
        self.assertIn("Recordatorios encontrados: 1. Enviados: 1. Fallidos: 0.", salida.getvalue())

    def test_enviar_recordatorios_email_rechaza_horas_invalidas(self):
        with self.assertRaises(CommandError):
            call_command("enviar_recordatorios_email", "--horas", "0")

    def test_enviar_recordatorios_email_puede_fallar_si_hay_errores(self):
        usuario = get_user_model().objects.create_user(
            username="dra.recordatorios.error",
            first_name="Rita",
            last_name="Error",
        )
        odontologo = Odontologo.objects.create(
            usuario=usuario,
            matricula="MN-REC-ERR",
            duracion_turno_minutos=30,
        )
        crear_disponibilidad_laboral(odontologo)
        paciente = Paciente.objects.create(
            nombre="Paciente",
            apellido="Error",
            documento="49111222",
            email="paciente-error@example.com",
        )
        Turno.objects.create(
            paciente=paciente,
            odontologo=odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
        )
        ahora = timezone.make_aware(
            datetime(2026, 5, 7, 10, 0),
            timezone.get_current_timezone(),
        )

        salida = StringIO()

        with patch("turnos.services.timezone.now", return_value=ahora):
            with self.assertLogs("turnos.notifications", level="ERROR"):
                with patch("turnos.notifications.send_mail", side_effect=OSError("SMTP caido")):
                    with self.assertRaises(CommandError):
                        call_command(
                            "enviar_recordatorios_email",
                            "--horas",
                            "24",
                            "--fallar-si-hay-errores",
                            stdout=salida,
                        )


class HorariosDisponiblesTests(TestCase):
    def setUp(self):
        usuario = get_user_model().objects.create_user(
            username="dr.romero",
            first_name="Pablo",
            last_name="Romero",
        )
        self.odontologo = Odontologo.objects.create(
            usuario=usuario,
            matricula="MN-99999",
            duracion_turno_minutos=30,
            hora_inicio_atencion=time(9, 0),
            hora_fin_atencion=time(11, 0),
        )
        DisponibilidadOdontologo.objects.create(
            odontologo=self.odontologo,
            dia_semana=DisponibilidadOdontologo.DiaSemana.VIERNES,
            hora_inicio=time(9, 0),
            hora_fin=time(11, 0),
        )
        self.paciente = Paciente.objects.create(
            nombre="Tomas",
            apellido="Silva",
            documento="33111222",
        )

    def test_calcula_horarios_disponibles_del_dia(self):
        horarios = obtener_horarios_disponibles(self.odontologo, date(2026, 5, 8))

        self.assertEqual(
            horarios,
            [time(9, 0), time(9, 30), time(10, 0), time(10, 30)],
        )

    def test_turnos_activos_bloquean_horarios_disponibles(self):
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(9, 30),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
        )

        horarios = obtener_horarios_disponibles(self.odontologo, date(2026, 5, 8))

        self.assertEqual(horarios, [time(9, 0), time(10, 0), time(10, 30)])

    def test_turnos_cancelados_no_bloquean_horarios_disponibles(self):
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(9, 30),
            duracion_minutos=30,
            estado=Turno.Estado.CANCELADO,
        )

        horarios = obtener_horarios_disponibles(self.odontologo, date(2026, 5, 8))

        self.assertIn(time(9, 30), horarios)

    def test_puede_excluir_turno_actual_al_calcular_horarios(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(9, 30),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
        )

        horarios = obtener_horarios_disponibles(
            self.odontologo,
            date(2026, 5, 8),
            turno_excluido=turno,
        )

        self.assertIn(time(9, 30), horarios)

    def test_dia_sin_disponibilidad_no_tiene_horarios(self):
        horarios = obtener_horarios_disponibles(self.odontologo, date(2026, 5, 9))

        self.assertEqual(horarios, [])


class TurnoAccessTests(TestCase):
    def assert_requiere_login(self, url):
        self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")

    def test_listado_requiere_login(self):
        self.assert_requiere_login(reverse("turnos:lista"))

    def test_creacion_requiere_login(self):
        self.assert_requiere_login(reverse("turnos:crear"))

    def test_detalle_requiere_login(self):
        self.assert_requiere_login(reverse("turnos:detalle", kwargs={"pk": 1}))

    def test_edicion_requiere_login(self):
        self.assert_requiere_login(reverse("turnos:editar", kwargs={"pk": 1}))

    def test_reprogramacion_requiere_login(self):
        self.assert_requiere_login(reverse("turnos:reprogramar", kwargs={"pk": 1}))

    def test_agenda_diaria_requiere_login(self):
        self.assert_requiere_login(reverse("turnos:agenda_dia"))

    def test_agenda_semanal_requiere_login(self):
        self.assert_requiere_login(reverse("turnos:agenda_semana"))

    def test_cancelacion_requiere_login(self):
        url = reverse("turnos:cancelar", kwargs={"pk": 1})

        self.assertRedirects(self.client.post(url), f"{reverse('login')}?next={url}")

    def test_confirmacion_requiere_login(self):
        url = reverse("turnos:confirmar", kwargs={"pk": 1})

        self.assertRedirects(self.client.post(url), f"{reverse('login')}?next={url}")

    def test_reintentar_sincronizacion_requiere_login(self):
        url = reverse("turnos:reintentar_google_calendar", kwargs={"pk": 1})

        self.assertRedirects(self.client.post(url), f"{reverse('login')}?next={url}")


class TurnoRoleTests(TestCase):
    def setUp(self):
        usuario_odontologo = get_user_model().objects.create_user(
            username="dr.roles",
            first_name="Ramon",
            last_name="Roles",
        )
        asignar_rol(usuario_odontologo, ROL_ODONTOLOGO)
        self.odontologo = Odontologo.objects.create(
            usuario=usuario_odontologo,
            matricula="MN-ROL",
        )
        crear_disponibilidad_laboral(self.odontologo)

        otro_usuario = get_user_model().objects.create_user(
            username="dra.otra",
            first_name="Laura",
            last_name="Otra",
        )
        self.otro_odontologo = Odontologo.objects.create(
            usuario=otro_usuario,
            matricula="MN-OTRA",
        )
        crear_disponibilidad_laboral(self.otro_odontologo)

        self.paciente = Paciente.objects.create(
            nombre="Marta",
            apellido="Ruiz",
            documento="36111222",
        )
        self.turno_propio = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Turno propio",
        )
        self.turno_ajeno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.otro_odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Turno ajeno",
        )
        self.paciente_no_asociado = Paciente.objects.create(
            nombre="Noelia",
            apellido="Externa",
            documento="36111223",
        )
        self.turno_no_asociado = Turno.objects.create(
            paciente=self.paciente_no_asociado,
            odontologo=self.otro_odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(11, 0),
            duracion_minutos=30,
            motivo="Turno no asociado",
        )
        self.client.force_login(usuario_odontologo)

    def test_odontologo_lista_turnos_de_pacientes_asociados(self):
        response = self.client.get(reverse("turnos:lista"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Roles")
        self.assertContains(response, "Otra")
        self.assertContains(response, "Turno ajeno")
        self.assertNotContains(response, "Turno no asociado")
        self.assertNotContains(response, "Nuevo turno")
        self.assertNotContains(response, "Editar")

    def test_odontologo_puede_ver_detalle_de_turno_propio(self):
        response = self.client.get(
            reverse("turnos:detalle", kwargs={"pk": self.turno_propio.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Turno propio")
        self.assertNotContains(response, "Reintentar sincronización")
        self.assertContains(response, "Reprogramar")
        self.assertNotContains(response, "Cancelar turno")

    def test_odontologo_puede_ver_turno_de_paciente_asociado_solo_lectura(self):
        response = self.client.get(
            reverse("turnos:detalle", kwargs={"pk": self.turno_ajeno.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Turno ajeno")
        self.assertNotContains(response, "Reprogramar")
        self.assertNotContains(response, "Reintentar sincronización")

    def test_odontologo_no_puede_ver_turno_no_asociado(self):
        response = self.client.get(
            reverse("turnos:detalle", kwargs={"pk": self.turno_no_asociado.pk})
        )

        self.assertEqual(response.status_code, 404)

    def test_odontologo_puede_reintentar_sincronizacion_de_turno_propio(self):
        with patch(
            "turnos.views.reintentar_sincronizacion_google_calendar",
            return_value=ResultadoSincronizacionGoogleCalendar(
                realizada=True,
                accion="actualizar",
            ),
        ) as sincronizar_mock:
            response = self.client.post(
                reverse(
                    "turnos:reintentar_google_calendar",
                    kwargs={"pk": self.turno_propio.pk},
                )
            )

        self.assertRedirects(
            response,
            reverse("turnos:detalle", kwargs={"pk": self.turno_propio.pk}),
        )
        sincronizar_mock.assert_called_once()

    def test_odontologo_no_puede_reintentar_sincronizacion_de_turno_ajeno(self):
        with patch("turnos.views.reintentar_sincronizacion_google_calendar") as sincronizar_mock:
            response = self.client.post(
                reverse(
                    "turnos:reintentar_google_calendar",
                    kwargs={"pk": self.turno_ajeno.pk},
                )
            )

        self.assertEqual(response.status_code, 403)
        sincronizar_mock.assert_not_called()

    def test_odontologo_puede_reprogramar_turno_propio(self):
        response = self.client.post(
            reverse("turnos:reprogramar", kwargs={"pk": self.turno_propio.pk}),
            {
                "fecha": "2026-05-08",
                "hora_inicio": "11:00",
                "duracion_minutos": 30,
            },
        )

        self.turno_propio.refresh_from_db()

        self.assertRedirects(
            response,
            reverse("turnos:detalle", kwargs={"pk": self.turno_propio.pk}),
        )
        self.assertEqual(self.turno_propio.hora_inicio, time(11, 0))

    def test_odontologo_no_puede_reprogramar_turno_ajeno(self):
        response = self.client.post(
            reverse("turnos:reprogramar", kwargs={"pk": self.turno_ajeno.pk}),
            {
                "fecha": "2026-05-08",
                "hora_inicio": "11:00",
                "duracion_minutos": 30,
            },
        )

        self.assertEqual(response.status_code, 403)

    def test_administrador_puede_reintentar_sin_boton_visible_en_detalle(self):
        usuario_admin = get_user_model().objects.create_user(
            username="admin.turnos",
            is_staff=True,
        )
        asignar_rol(usuario_admin, ROL_ADMINISTRADOR)
        self.client.force_login(usuario_admin)

        response_detalle = self.client.get(
            reverse("turnos:detalle", kwargs={"pk": self.turno_propio.pk})
        )

        self.assertEqual(response_detalle.status_code, 200)
        self.assertNotContains(response_detalle, "Reintentar sincronización")
        self.assertNotContains(response_detalle, "Reprogramar")
        self.assertNotContains(response_detalle, "Editar")
        self.assertNotContains(response_detalle, "Cancelar turno")

        with patch(
            "turnos.views.reintentar_sincronizacion_google_calendar",
            return_value=ResultadoSincronizacionGoogleCalendar(
                realizada=True,
                accion="actualizar",
            ),
        ) as sincronizar_mock:
            response_reintentar = self.client.post(
                reverse(
                    "turnos:reintentar_google_calendar",
                    kwargs={"pk": self.turno_propio.pk},
                )
            )

        self.assertRedirects(
            response_reintentar,
            reverse("turnos:detalle", kwargs={"pk": self.turno_propio.pk}),
        )
        sincronizar_mock.assert_called_once()

    def test_odontologo_no_puede_crear_editar_ni_cancelar_turnos(self):
        response_crear = self.client.get(reverse("turnos:crear"))
        response_editar = self.client.get(
            reverse("turnos:editar", kwargs={"pk": self.turno_propio.pk})
        )
        response_confirmar = self.client.post(
            reverse("turnos:confirmar", kwargs={"pk": self.turno_propio.pk})
        )
        response_cancelar = self.client.post(
            reverse("turnos:cancelar", kwargs={"pk": self.turno_propio.pk})
        )

        self.turno_propio.refresh_from_db()

        self.assertEqual(response_crear.status_code, 403)
        self.assertEqual(response_editar.status_code, 403)
        self.assertEqual(response_confirmar.status_code, 200)
        self.assertEqual(response_cancelar.status_code, 403)
        self.assertEqual(self.turno_propio.estado, Turno.Estado.PENDIENTE)

    def test_agenda_de_odontologo_queda_filtrada_a_su_perfil(self):
        response = self.client.get(reverse("turnos:agenda_dia"), {"fecha": "2026-05-08"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Turno propio")
        self.assertNotContains(response, "Turno ajeno")


class AgendaSelectorsTests(TestCase):
    def setUp(self):
        usuario = get_user_model().objects.create_user(
            username="dra.agenda",
            first_name="Clara",
            last_name="Molina",
        )
        self.odontologo = Odontologo.objects.create(
            usuario=usuario,
            matricula="MN-11111",
        )
        crear_disponibilidad_laboral(self.odontologo)
        self.paciente = Paciente.objects.create(
            nombre="Nora",
            apellido="Vega",
            documento="34111222",
        )

    def test_obtiene_turnos_del_dia(self):
        turno_del_dia = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 11),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )

        turnos = obtener_turnos_del_dia(date(2026, 5, 8))

        self.assertEqual(list(turnos), [turno_del_dia])

    def test_obtiene_inicio_de_semana(self):
        self.assertEqual(obtener_inicio_semana(date(2026, 5, 8)), date(2026, 5, 4))

    def test_obtiene_bloques_de_agenda_del_dia(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )

        bloques = obtener_bloques_agenda_del_dia(date(2026, 5, 8), self.odontologo)
        bloque_de_turno = next(
            bloque for bloque in bloques if bloque["hora_inicio"] == time(10, 0)
        )

        self.assertEqual(bloques[0]["hora_inicio"], time(9, 0))
        self.assertEqual(bloques[-1]["hora_fin"], time(18, 0))
        self.assertEqual(bloque_de_turno["turnos"], [turno])

    def test_obtiene_turnos_de_la_semana(self):
        turno_lunes = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 4),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )
        turno_viernes = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(11, 0),
            duracion_minutos=30,
        )

        dias = obtener_turnos_de_la_semana(date(2026, 5, 8))

        self.assertEqual(dias[0]["turnos"], [turno_lunes])
        self.assertEqual(dias[4]["turnos"], [turno_viernes])


class AgendaViewsTests(TestCase):
    def setUp(self):
        usuario = get_user_model().objects.create_user(
            username="dra.vistas",
            first_name="Ines",
            last_name="Costa",
        )
        asignar_rol(usuario, ROL_RECEPCIONISTA)
        self.client.force_login(usuario)
        self.odontologo = Odontologo.objects.create(
            usuario=usuario,
            matricula="MN-22222",
        )
        crear_disponibilidad_laboral(self.odontologo)
        self.paciente = Paciente.objects.create(
            nombre="Pedro",
            apellido="Luna",
            documento="35111222",
            telefono="1122334455",
            email="pedro@example.com",
        )

    def test_agenda_diaria_muestra_turnos_de_fecha_seleccionada(self):
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Control diario",
        )
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 11),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Fuera del dia",
        )

        response = self.client.get(reverse("turnos:agenda_dia"), {"fecha": "2026-05-08"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Control diario")
        self.assertContains(response, "10:00 a 10:30")
        self.assertContains(response, "1122334455")
        self.assertContains(response, "Contacto")
        self.assertContains(response, "status-pendiente")
        self.assertNotContains(response, "Fuera del dia")

    def test_agenda_diaria_busca_por_paciente_contacto_o_motivo(self):
        otro_paciente = Paciente.objects.create(
            nombre="Sofia",
            apellido="Rios",
            documento="35999888",
            telefono="1199990000",
        )
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Control visible",
        )
        Turno.objects.create(
            paciente=otro_paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(11, 0),
            duracion_minutos=30,
            motivo="Urgencia filtrada",
        )

        response = self.client.get(
            reverse("turnos:agenda_dia"),
            {"fecha": "2026-05-08", "buscar": "112233"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Control visible")
        self.assertContains(response, 'Filtro activo: "112233"')
        self.assertNotContains(response, "Urgencia filtrada")

    def test_agenda_diaria_filtra_por_odontologo(self):
        otro_usuario = get_user_model().objects.create_user(
            username="dr.otro",
            first_name="Mateo",
            last_name="Ruiz",
        )
        otro_odontologo = Odontologo.objects.create(
            usuario=otro_usuario,
            matricula="MN-33333",
        )
        crear_disponibilidad_laboral(otro_odontologo)
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Turno visible",
        )
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=otro_odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Turno filtrado",
        )

        response = self.client.get(
            reverse("turnos:agenda_dia"),
            {"fecha": "2026-05-08", "odontologo": self.odontologo.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Turno visible")
        self.assertNotContains(response, "Turno filtrado")

    def test_agenda_diaria_agrupa_por_odontologo_y_muestra_acciones_rapidas(self):
        turno_pendiente = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Control con acciones",
            estado=Turno.Estado.PENDIENTE,
        )

        response = self.client.get(reverse("turnos:agenda_dia"), {"fecha": "2026-05-08"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(self.odontologo))
        self.assertContains(response, "1 turno")
        self.assertContains(response, "Pendiente")
        self.assertContains(response, reverse("turnos:confirmar", kwargs={"pk": turno_pendiente.pk}))
        self.assertContains(response, reverse("turnos:reprogramar", kwargs={"pk": turno_pendiente.pk}))
        self.assertContains(response, reverse("turnos:cancelar", kwargs={"pk": turno_pendiente.pk}))

    def test_agenda_semanal_muestra_turnos_de_la_semana(self):
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 4),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Inicio de semana",
        )
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 11),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Fuera de la semana",
        )

        response = self.client.get(reverse("turnos:agenda_semana"), {"fecha": "2026-05-08"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Inicio de semana")
        self.assertContains(response, "1122334455")
        self.assertContains(response, "status-pendiente")
        self.assertNotContains(response, "Fuera de la semana")

    def test_agenda_semanal_busca_por_motivo(self):
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 4),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Ortodoncia visible",
        )
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 5),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Limpieza filtrada",
        )

        response = self.client.get(
            reverse("turnos:agenda_semana"),
            {"fecha": "2026-05-08", "buscar": "Ortodoncia"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ortodoncia visible")
        self.assertContains(response, 'Filtro activo: "Ortodoncia"')
        self.assertNotContains(response, "Limpieza filtrada")

    def test_agenda_semanal_filtra_por_odontologo(self):
        otro_usuario = get_user_model().objects.create_user(
            username="dra.semana",
            first_name="Paula",
            last_name="Ibarra",
        )
        otro_odontologo = Odontologo.objects.create(
            usuario=otro_usuario,
            matricula="MN-44444",
        )
        crear_disponibilidad_laboral(otro_odontologo)
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Semana visible",
        )
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=otro_odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Semana filtrada",
        )

        response = self.client.get(
            reverse("turnos:agenda_semana"),
            {"fecha": "2026-05-08", "odontologo": self.odontologo.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Semana visible")
        self.assertNotContains(response, "Semana filtrada")

    def test_agenda_semanal_muestra_bloques_por_odontologo(self):
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Semana por odontólogo",
            estado=Turno.Estado.CONFIRMADO,
        )

        response = self.client.get(reverse("turnos:agenda_semana"), {"fecha": "2026-05-08"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(self.odontologo))
        self.assertContains(response, "1 turno")
        self.assertContains(response, "Confirmado")
        self.assertContains(response, "Semana por odontólogo")

    def test_odontologo_inactivo_no_tiene_horarios_disponibles(self):
        self.odontologo.activo = False
        self.odontologo.save()

        horarios = obtener_horarios_disponibles(self.odontologo, date(2026, 5, 8))

        self.assertEqual(horarios, [])
