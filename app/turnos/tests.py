from datetime import date, datetime, time, timedelta
from io import StringIO
from urllib.parse import urlencode
from unittest.mock import patch

from django.core import mail
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from pacientes.models import Paciente, PacienteOdontologo
from turnos.google_calendar_sync import ResultadoSincronizacionGoogleCalendar
from turnos.models import (
    BloqueoAgendaOdontologo,
    DisponibilidadOdontologo,
    GoogleCalendarConexion,
    Odontologo,
    Turno,
)
from turnos.notifications import (
    notificar_recordatorio_turno,
    notificar_turno_confirmado,
)
from turnos.public_tokens import crear_token_accion_publica_turno
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


class SolicitudTurnoPublicaTests(TestCase):
    def setUp(self):
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

    def _url_accion_publica(self, nombre_url, turno):
        token = crear_token_accion_publica_turno(turno)
        return f"{reverse(nombre_url)}?{urlencode({'token': token})}"

    def test_landing_publica_carga_sin_login(self):
        response = self.client.get(reverse("landing_publica"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reservá tu turno odontológico")
        self.assertContains(response, reverse("turnos:solicitud_publica"))
        self.assertContains(response, reverse("turnos:consulta_publica"))
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
        self.assertContains(response, "Tu solicitud de turno fue registrada")
        self.assertContains(
            response,
            "Te avisaremos por email cuando el consultorio confirme el turno.",
        )
        self.assertContains(response, reverse("turnos:solicitud_publica"))
        self.assertContains(response, reverse("turnos:consulta_publica"))

        nueva_visita = self.client.get(reverse("landing_publica"))

        self.assertNotContains(nueva_visita, "Tu solicitud de turno fue registrada")

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
        self.assertContains(response, "Paz, Lucia")
        self.assertContains(response, "Paula Publica")
        self.assertContains(response, self.fecha_turno.strftime("%d/%m/%Y"))
        self.assertContains(response, "10:00 a 10:30")
        self.assertContains(response, "Pendiente")

    def test_solicitud_publica_reutiliza_paciente_por_documento(self):
        paciente = Paciente.objects.create(
            nombre="Viejo",
            apellido="Nombre",
            documento="39111222",
            fecha_nacimiento=date(1990, 4, 15),
            obra_social="OSDE",
            contacto_emergencia="Rosa 3415550000",
        )

        response = self.client.post(
            reverse("turnos:solicitud_publica_datos"),
            {
                "nombre": "Nadia",
                "apellido": "Suarez",
                "documento": "39111222",
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

        self.assertRedirects(response, reverse("landing_publica"))
        self.assertEqual(Paciente.objects.filter(documento="39111222").count(), 1)
        self.assertEqual(turno.paciente, paciente)
        self.assertEqual(paciente.nombre, "Nadia")
        self.assertEqual(paciente.telefono, "1199999999")
        self.assertEqual(paciente.email, "nadia@example.com")
        self.assertEqual(paciente.fecha_nacimiento, date(1990, 4, 15))
        self.assertEqual(paciente.obra_social, "OSDE")
        self.assertEqual(paciente.contacto_emergencia, "Rosa 3415550000")

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
        self.assertContains(response, "Elegí un odontólogo.")
        self.assertContains(response, "Elegí una fecha.")
        self.assertContains(response, "Elegí un horario disponible.")

    def test_consulta_publica_por_dni_no_requiere_login(self):
        response = self.client.get(reverse("turnos:consulta_publica"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Consultar o cancelar turno")
        self.assertContains(response, "Ingresá tu DNI")

    def test_api_por_dni_devuelve_solo_turnos_pendientes_y_confirmados(self):
        paciente = Paciente.objects.create(
            nombre="Lucia",
            apellido="Paz",
            documento="38111222",
            telefono="1155667788",
        )
        turno_pendiente = Turno.objects.create(
            paciente=paciente,
            odontologo=self.odontologo,
            fecha=self.fecha_turno,
            hora_inicio=time(9, 0),
            duracion_minutos=30,
            estado=Turno.Estado.PENDIENTE,
            motivo="Pendiente visible",
        )
        turno_confirmado = Turno.objects.create(
            paciente=paciente,
            odontologo=self.odontologo,
            fecha=self.fecha_turno,
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
            motivo="Confirmado visible",
        )
        Turno.objects.create(
            paciente=paciente,
            odontologo=self.odontologo,
            fecha=self.fecha_turno,
            hora_inicio=time(11, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CANCELADO,
            motivo="Cancelado oculto",
        )

        response = self.client.get(
            reverse("turnos:turnos_por_dni"),
            {"dni": paciente.documento},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        motivos = [turno["motivo"] for turno in data["turnos"]]

        self.assertTrue(data["ok"])
        self.assertEqual(len(data["turnos"]), 2)
        self.assertNotIn("paciente", data["turnos"][0])
        self.assertIn("Pendiente visible", motivos)
        self.assertIn("Confirmado visible", motivos)
        self.assertNotIn("Cancelado oculto", motivos)
        turno_pendiente_data = next(
            turno for turno in data["turnos"] if turno["motivo"] == "Pendiente visible"
        )
        turno_confirmado_data = next(
            turno for turno in data["turnos"] if turno["motivo"] == "Confirmado visible"
        )
        self.assertTrue(turno_pendiente_data["puede_reprogramar"])
        self.assertIn(reverse("turnos:reprogramar_publico"), turno_pendiente_data["reprogramar_url"])
        self.assertIn("token=", turno_pendiente_data["reprogramar_url"])
        self.assertNotIn(
            f"/{turno_pendiente.pk}/reprogramar-publico/",
            turno_pendiente_data["reprogramar_url"],
        )
        self.assertIn(reverse("turnos:cancelar_publico"), turno_confirmado_data["cancelar_url"])
        self.assertIn("token=", turno_confirmado_data["cancelar_url"])
        self.assertFalse(turno_confirmado_data["puede_reprogramar"])
        self.assertEqual(turno_confirmado_data["reprogramar_url"], "")

    @override_settings(
        TURNOS_PUBLIC_DNI_RATE_LIMIT_ATTEMPTS=1,
        TURNOS_PUBLIC_DNI_RATE_LIMIT_SECONDS=600,
    )
    def test_api_por_dni_aplica_rate_limit(self):
        cache.clear()

        primera_respuesta = self.client.get(
            reverse("turnos:turnos_por_dni"),
            {"dni": "38111222"},
        )
        segunda_respuesta = self.client.get(
            reverse("turnos:turnos_por_dni"),
            {"dni": "38111222"},
        )

        self.assertEqual(primera_respuesta.status_code, 200)
        self.assertEqual(segunda_respuesta.status_code, 429)
        self.assertFalse(segunda_respuesta.json()["ok"])

    def test_cancelacion_publica_cambia_estado_y_guarda_motivo(self):
        paciente = Paciente.objects.create(
            nombre="Lucia",
            apellido="Paz",
            documento="38111222",
            email="lucia@example.com",
        )
        turno = Turno.objects.create(
            paciente=paciente,
            odontologo=self.odontologo,
            fecha=self.fecha_turno,
            hora_inicio=time(9, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
            motivo="Control",
        )

        response = self.client.post(
            self._url_accion_publica("turnos:cancelar_publico", turno),
            {
                "motivo_cancelacion": "No puedo asistir.",
            },
        )

        turno.refresh_from_db()

        self.assertRedirects(
            response,
            reverse("turnos:consulta_publica"),
        )
        self.assertEqual(turno.estado, Turno.Estado.CANCELADO)
        self.assertEqual(turno.motivo_cancelacion_paciente, "No puedo asistir.")
        self.assertTrue(Turno.objects.filter(pk=turno.pk).exists())

    def test_cancelacion_publica_rechaza_token_invalido(self):
        paciente = Paciente.objects.create(
            nombre="Lucia",
            apellido="Paz",
            documento="38111222",
        )
        turno = Turno.objects.create(
            paciente=paciente,
            odontologo=self.odontologo,
            fecha=self.fecha_turno,
            hora_inicio=time(9, 0),
            duracion_minutos=30,
            estado=Turno.Estado.PENDIENTE,
        )

        response = self.client.post(
            f"{reverse('turnos:cancelar_publico')}?token=token-invalido",
            {
                "motivo_cancelacion": "Intento invalido",
            },
        )

        turno.refresh_from_db()

        self.assertRedirects(response, reverse("turnos:consulta_publica"))
        self.assertEqual(turno.estado, Turno.Estado.PENDIENTE)
        self.assertEqual(turno.motivo_cancelacion_paciente, "")

    def test_reprogramacion_publica_solo_permite_turnos_pendientes(self):
        paciente = Paciente.objects.create(
            nombre="Lucia",
            apellido="Paz",
            documento="38111222",
        )
        turno_confirmado = Turno.objects.create(
            paciente=paciente,
            odontologo=self.odontologo,
            fecha=self.fecha_turno,
            hora_inicio=time(9, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
        )

        response = self.client.get(
            self._url_accion_publica("turnos:reprogramar_publico", turno_confirmado),
        )

        self.assertRedirects(response, reverse("turnos:consulta_publica"))

    def test_reprogramacion_publica_actualiza_turno_pendiente(self):
        paciente = Paciente.objects.create(
            nombre="Lucia",
            apellido="Paz",
            documento="38111222",
            email="lucia@example.com",
        )
        turno = Turno.objects.create(
            paciente=paciente,
            odontologo=self.odontologo,
            fecha=self.fecha_turno,
            hora_inicio=time(9, 0),
            duracion_minutos=30,
            estado=Turno.Estado.PENDIENTE,
        )

        response = self.client.post(
            reverse("turnos:reprogramar_publico"),
            {
                "token": crear_token_accion_publica_turno(turno),
                "fecha": self.fecha_turno.isoformat(),
                "hora_inicio": "12:00",
                "duracion_minutos": "30",
            },
        )

        turno.refresh_from_db()

        self.assertRedirects(
            response,
            reverse("turnos:consulta_publica"),
        )
        self.assertEqual(turno.estado, Turno.Estado.PENDIENTE)
        self.assertEqual(turno.hora_inicio, time(12, 0))

    def test_reprogramacion_publica_valida_disponibilidad(self):
        paciente = Paciente.objects.create(
            nombre="Lucia",
            apellido="Paz",
            documento="38111222",
        )
        turno = Turno.objects.create(
            paciente=paciente,
            odontologo=self.odontologo,
            fecha=self.fecha_turno,
            hora_inicio=time(9, 0),
            duracion_minutos=30,
            estado=Turno.Estado.PENDIENTE,
        )
        Turno.objects.create(
            paciente=Paciente.objects.create(
                nombre="Mario",
                apellido="Ocupado",
                documento="40000111",
            ),
            odontologo=self.odontologo,
            fecha=self.fecha_turno,
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
        )

        response = self.client.post(
            reverse("turnos:reprogramar_publico"),
            {
                "token": crear_token_accion_publica_turno(turno),
                "fecha": self.fecha_turno.isoformat(),
                "hora_inicio": "10:00",
                "duracion_minutos": "30",
            },
        )

        turno.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertIn("hora_inicio", response.context["form"].errors)
        self.assertEqual(turno.hora_inicio, time(9, 0))


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
