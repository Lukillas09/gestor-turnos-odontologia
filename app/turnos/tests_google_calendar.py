from datetime import date, time, timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from pacientes.models import Paciente
from turnos.integrations.google_calendar import (
    GoogleCalendarClient,
    GoogleCalendarError,
    GoogleCalendarClienteNoConfiguradoError,
    GoogleCalendarEventoSinIdError,
    GoogleOAuthTokens,
    construir_url_autorizacion_google_calendar,
    construir_evento_desde_turno,
    obtener_configuracion_google_calendar,
)
from turnos.google_calendar_sync import (
    MENSAJE_ERROR_INESPERADO,
    sincronizar_turno_actualizado,
    sincronizar_turno_cancelado,
    sincronizar_turno_creado,
)
from turnos.admin import GoogleCalendarConexionAdmin
from turnos.models import (
    DisponibilidadOdontologo,
    GoogleCalendarConexion,
    Odontologo,
    Turno,
)
from turnos.views import GOOGLE_CALENDAR_OAUTH_STATE_SESSION_KEY


class GoogleCalendarConfigTests(SimpleTestCase):
    @override_settings(
        GOOGLE_CALENDAR_CLIENT_ID="",
        GOOGLE_CALENDAR_CLIENT_SECRET="",
        GOOGLE_CALENDAR_CLIENT_SECRETS_FILE="",
        GOOGLE_CALENDAR_REDIRECT_URI="http://127.0.0.1:8000/google/oauth2/callback/",
        GOOGLE_CALENDAR_SCOPES=["https://www.googleapis.com/auth/calendar.events"],
    )
    def test_configuracion_no_esta_lista_sin_credenciales(self):
        configuracion = obtener_configuracion_google_calendar()

        self.assertFalse(configuracion.esta_configurada)

    @override_settings(
        GOOGLE_CALENDAR_CLIENT_ID="client-id",
        GOOGLE_CALENDAR_CLIENT_SECRET="client-secret",
        GOOGLE_CALENDAR_CLIENT_SECRETS_FILE="",
        GOOGLE_CALENDAR_REDIRECT_URI="http://127.0.0.1:8000/google/oauth2/callback/",
        GOOGLE_CALENDAR_SCOPES=["https://www.googleapis.com/auth/calendar.events"],
    )
    def test_configuracion_esta_lista_con_cliente_y_secreto(self):
        configuracion = obtener_configuracion_google_calendar()

        self.assertTrue(configuracion.esta_configurada)

    @override_settings(
        GOOGLE_CALENDAR_CLIENT_ID="",
        GOOGLE_CALENDAR_CLIENT_SECRET="",
        GOOGLE_CALENDAR_CLIENT_SECRETS_FILE="secrets/google-client-secret.json",
        GOOGLE_CALENDAR_REDIRECT_URI="http://127.0.0.1:8000/google/oauth2/callback/",
        GOOGLE_CALENDAR_SCOPES=["https://www.googleapis.com/auth/calendar.events"],
    )
    def test_configuracion_esta_lista_con_archivo_de_credenciales(self):
        configuracion = obtener_configuracion_google_calendar()

        self.assertTrue(configuracion.esta_configurada)


class GoogleCalendarOAuthUrlTests(SimpleTestCase):
    @override_settings(
        GOOGLE_CALENDAR_CLIENT_ID="client-id",
        GOOGLE_CALENDAR_CLIENT_SECRET="client-secret",
        GOOGLE_CALENDAR_REDIRECT_URI="http://127.0.0.1:8000/turnos/google-calendar/callback/",
        GOOGLE_CALENDAR_SCOPES=["scope-a", "scope-b"],
    )
    def test_construye_url_de_autorizacion_oauth(self):
        url = construir_url_autorizacion_google_calendar(
            state="estado-seguro",
            login_hint="odontologo@example.com",
        )
        parametros = parse_qs(urlparse(url).query)

        self.assertTrue(url.startswith("https://accounts.google.com/o/oauth2/v2/auth?"))
        self.assertEqual(parametros["client_id"], ["client-id"])
        self.assertEqual(
            parametros["redirect_uri"],
            ["http://127.0.0.1:8000/turnos/google-calendar/callback/"],
        )
        self.assertEqual(parametros["response_type"], ["code"])
        self.assertEqual(parametros["scope"], ["scope-a scope-b"])
        self.assertEqual(parametros["access_type"], ["offline"])
        self.assertEqual(parametros["prompt"], ["consent"])
        self.assertEqual(parametros["state"], ["estado-seguro"])
        self.assertEqual(parametros["login_hint"], ["odontologo@example.com"])


class GoogleCalendarPayloadTests(TestCase):
    def setUp(self):
        usuario = get_user_model().objects.create_user(
            username="dra.calendar",
            first_name="Carla",
            last_name="Calendar",
        )
        self.odontologo = Odontologo.objects.create(
            usuario=usuario,
            matricula="MN-CALENDAR",
        )
        self.paciente = Paciente.objects.create(
            nombre="Lucas",
            apellido="Paredes",
            documento="30111222",
        )
        self.turno = Turno(
            pk=15,
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=45,
            motivo="Limpieza",
            estado=Turno.Estado.CONFIRMADO,
            notas="Primera visita",
        )

    def test_construye_payload_de_evento_desde_turno(self):
        evento = construir_evento_desde_turno(self.turno)
        payload = evento.como_payload()

        self.assertEqual(payload["summary"], "Turno odontológico - Paredes, Lucas")
        self.assertEqual(payload["start"]["timeZone"], "America/Argentina/Buenos_Aires")
        self.assertEqual(payload["end"]["timeZone"], "America/Argentina/Buenos_Aires")
        self.assertIn("2026-05-08T10:00:00", payload["start"]["dateTime"])
        self.assertIn("2026-05-08T10:45:00", payload["end"]["dateTime"])
        self.assertIn("Paciente: Paredes, Lucas", payload["description"])
        self.assertIn("Odontólogo: Carla Calendar", payload["description"])
        self.assertIn("Motivo: Limpieza", payload["description"])
        self.assertEqual(payload["status"], "confirmed")
        self.assertEqual(
            payload["extendedProperties"]["private"],
            {
                "turno_id": "15",
                "paciente_id": str(self.paciente.pk),
                "odontologo_id": str(self.odontologo.pk),
                "estado": Turno.Estado.CONFIRMADO,
            },
        )

    def test_construye_evento_cancelado(self):
        self.turno.estado = Turno.Estado.CANCELADO

        payload = construir_evento_desde_turno(self.turno).como_payload()

        self.assertEqual(payload["status"], "cancelled")

    def test_construye_evento_pendiente_como_tentativo(self):
        self.turno.estado = Turno.Estado.PENDIENTE

        payload = construir_evento_desde_turno(self.turno).como_payload()

        self.assertEqual(payload["status"], "tentative")


class GoogleCalendarConexionModelTests(TestCase):
    def setUp(self):
        usuario = get_user_model().objects.create_user(
            username="dra.conexion",
            first_name="Laura",
            last_name="Conexion",
        )
        self.odontologo = Odontologo.objects.create(
            usuario=usuario,
            matricula="MN-CONEXION",
        )

    def test_conexion_pertenece_a_un_odontologo(self):
        conexion = GoogleCalendarConexion.objects.create(
            odontologo=self.odontologo,
            refresh_token="refresh-token",
        )

        self.assertEqual(conexion.odontologo, self.odontologo)
        self.assertEqual(self.odontologo.google_calendar_conexion, conexion)
        self.assertTrue(conexion.esta_conectada)

    def test_no_permite_mas_de_una_conexion_por_odontologo(self):
        GoogleCalendarConexion.objects.create(
            odontologo=self.odontologo,
            refresh_token="refresh-token",
        )

        with self.assertRaises(ValidationError):
            GoogleCalendarConexion.objects.create(
                odontologo=self.odontologo,
                refresh_token="otro-refresh-token",
            )

    def test_registra_tokens_oauth(self):
        conexion = GoogleCalendarConexion(odontologo=self.odontologo)
        expiracion = timezone.now() + timedelta(hours=1)

        conexion.registrar_tokens(
            access_token="access-token",
            refresh_token="refresh-token",
            token_expira_en=expiracion,
            scopes=["scope-a", "scope-b"],
        )
        conexion.save()

        conexion.refresh_from_db()

        self.assertEqual(conexion.access_token, "access-token")
        self.assertEqual(conexion.refresh_token, "refresh-token")
        self.assertEqual(conexion.token_expira_en, expiracion)
        self.assertEqual(conexion.scopes, ["scope-a", "scope-b"])
        self.assertTrue(conexion.esta_conectada)
        self.assertFalse(conexion.necesita_renovar_access_token)

    def test_detecta_access_token_expirado(self):
        conexion = GoogleCalendarConexion.objects.create(
            odontologo=self.odontologo,
            access_token="access-token-viejo",
            refresh_token="refresh-token",
            token_expira_en=timezone.now() - timedelta(minutes=5),
        )

        self.assertTrue(conexion.access_token_expirado)
        self.assertTrue(conexion.necesita_renovar_access_token)

    def test_valida_scopes_como_lista(self):
        conexion = GoogleCalendarConexion(
            odontologo=self.odontologo,
            refresh_token="refresh-token",
            scopes="scope-a",
        )

        with self.assertRaises(ValidationError):
            conexion.full_clean()


class GoogleCalendarConexionAdminTests(SimpleTestCase):
    def test_admin_no_expone_campos_reales_de_tokens(self):
        model_admin = GoogleCalendarConexionAdmin(GoogleCalendarConexion, admin.site)
        campos = []

        for _, opciones in model_admin.fieldsets:
            campos.extend(opciones["fields"])

        self.assertNotIn("access_token", campos)
        self.assertNotIn("refresh_token", campos)
        self.assertIn("access_token_estado", campos)
        self.assertIn("refresh_token_estado", campos)


class GoogleCalendarOAuthViewsTests(TestCase):
    def setUp(self):
        self.usuario = get_user_model().objects.create_user(
            username="dra.oauth",
            first_name="Olivia",
            last_name="OAuth",
            email="olivia@example.com",
        )
        self.odontologo = Odontologo.objects.create(
            usuario=self.usuario,
            matricula="MN-OAUTH",
        )
        self.client.force_login(self.usuario)

    def test_estado_muestra_boton_para_conectar_google_calendar(self):
        response = self.client.get(reverse("turnos:google_calendar"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Conectar Google Calendar")
        self.assertContains(response, "Sin conexión")

    def test_estado_no_expone_tokens_ni_error_tecnico(self):
        GoogleCalendarConexion.objects.create(
            odontologo=self.odontologo,
            access_token="access-token-secreto",
            refresh_token="refresh-token-secreto",
            ultimo_error="HTTP 401 invalid_grant access_token=secreto-tecnico",
        )

        response = self.client.get(reverse("turnos:google_calendar"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Guardado")
        self.assertContains(response, "No se pudo autorizar la conexión con Google Calendar")
        self.assertNotContains(response, "access-token-secreto")
        self.assertNotContains(response, "refresh-token-secreto")
        self.assertNotContains(response, "invalid_grant")
        self.assertNotContains(response, "secreto-tecnico")

    @override_settings(
        GOOGLE_CALENDAR_CLIENT_ID="client-id",
        GOOGLE_CALENDAR_CLIENT_SECRET="client-secret",
        GOOGLE_CALENDAR_REDIRECT_URI="http://127.0.0.1:8000/turnos/google-calendar/callback/",
        GOOGLE_CALENDAR_SCOPES=["scope-a"],
    )
    def test_conectar_redirige_a_google_y_guarda_state(self):
        response = self.client.get(reverse("turnos:google_calendar_conectar"))
        session = self.client.session
        parametros = parse_qs(urlparse(response["Location"]).query)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("https://accounts.google.com/"))
        self.assertIn(GOOGLE_CALENDAR_OAUTH_STATE_SESSION_KEY, session)
        self.assertEqual(
            parametros["state"],
            [session[GOOGLE_CALENDAR_OAUTH_STATE_SESSION_KEY]],
        )
        self.assertEqual(parametros["client_id"], ["client-id"])
        self.assertEqual(parametros["login_hint"], ["olivia@example.com"])

    def test_callback_guarda_tokens_oauth(self):
        session = self.client.session
        session[GOOGLE_CALENDAR_OAUTH_STATE_SESSION_KEY] = "state-ok"
        session.save()
        expiracion = timezone.now() + timedelta(hours=1)
        tokens = GoogleOAuthTokens(
            access_token="access-token",
            refresh_token="refresh-token",
            token_type="Bearer",
            token_expira_en=expiracion,
            scopes=["scope-a"],
        )

        with patch("turnos.views.intercambiar_codigo_por_tokens", return_value=tokens):
            response = self.client.get(
                reverse("turnos:google_calendar_callback"),
                {
                    "code": "codigo-google",
                    "state": "state-ok",
                },
            )

        conexion = GoogleCalendarConexion.objects.get(odontologo=self.odontologo)

        self.assertRedirects(response, reverse("turnos:google_calendar"))
        self.assertTrue(conexion.activa)
        self.assertEqual(conexion.access_token, "access-token")
        self.assertEqual(conexion.refresh_token, "refresh-token")
        self.assertEqual(conexion.scopes, ["scope-a"])

    def test_callback_rechaza_state_invalido(self):
        session = self.client.session
        session[GOOGLE_CALENDAR_OAUTH_STATE_SESSION_KEY] = "state-ok"
        session.save()

        response = self.client.get(
            reverse("turnos:google_calendar_callback"),
            {
                "code": "codigo-google",
                "state": "state-falso",
            },
        )

        self.assertRedirects(response, reverse("turnos:google_calendar"))
        self.assertFalse(
            GoogleCalendarConexion.objects.filter(odontologo=self.odontologo).exists()
        )

    def test_desconectar_limpia_tokens(self):
        conexion = GoogleCalendarConexion.objects.create(
            odontologo=self.odontologo,
            access_token="access-token",
            refresh_token="refresh-token",
            scopes=["scope-a"],
            activa=True,
        )

        response = self.client.post(reverse("turnos:google_calendar_desconectar"))

        conexion.refresh_from_db()

        self.assertRedirects(response, reverse("turnos:google_calendar"))
        self.assertFalse(conexion.activa)
        self.assertEqual(conexion.access_token, "")
        self.assertEqual(conexion.refresh_token, "")
        self.assertEqual(conexion.scopes, [])

    def test_usuario_sin_perfil_odontologo_no_puede_conectar(self):
        usuario_sin_perfil = get_user_model().objects.create_user(username="sin.perfil")
        self.client.force_login(usuario_sin_perfil)

        response = self.client.get(reverse("turnos:google_calendar"))

        self.assertEqual(response.status_code, 403)


class GoogleCalendarSyncTests(TestCase):
    def setUp(self):
        usuario = get_user_model().objects.create_user(
            username="dra.sync",
            first_name="Sofia",
            last_name="Sync",
        )
        self.odontologo = Odontologo.objects.create(
            usuario=usuario,
            matricula="MN-SYNC",
        )
        DisponibilidadOdontologo.objects.create(
            odontologo=self.odontologo,
            dia_semana=DisponibilidadOdontologo.DiaSemana.VIERNES,
            hora_inicio=time(9, 0),
            hora_fin=time(18, 0),
        )
        self.conexion = GoogleCalendarConexion.objects.create(
            odontologo=self.odontologo,
            access_token="access-token",
            refresh_token="refresh-token",
        )
        self.paciente = Paciente.objects.create(
            nombre="Tomas",
            apellido="Agenda",
            documento="33111222",
        )
        self.turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Control",
            estado=Turno.Estado.CONFIRMADO,
        )
        self.servicio = GoogleCalendarServiceFake()

    def test_sincronizar_turno_creado_crea_evento_y_guarda_event_id(self):
        resultado = sincronizar_turno_creado(self.turno, self._cliente_factory)

        self.turno.refresh_from_db()
        self.conexion.refresh_from_db()

        self.assertTrue(resultado.realizada)
        self.assertEqual(resultado.event_id, "evento-creado")
        self.assertEqual(self.turno.google_calendar_event_id, "evento-creado")
        self.assertIsNotNone(self.conexion.sincronizado_en)
        self.assertEqual(self.servicio.eventos.acciones[0]["accion"], "insert")

    def test_sincronizar_turno_actualizado_actualiza_evento_existente(self):
        self.turno.google_calendar_event_id = "evento-123"
        self.turno.motivo = "Control actualizado"
        self.turno.save()

        resultado = sincronizar_turno_actualizado(self.turno, self._cliente_factory)

        self.assertTrue(resultado.realizada)
        self.assertEqual(resultado.event_id, "evento-123")
        self.assertEqual(self.servicio.eventos.acciones[0]["accion"], "update")
        self.assertEqual(self.servicio.eventos.acciones[0]["eventId"], "evento-123")

    def test_sincronizar_turno_actualizado_crea_evento_si_no_tiene_event_id(self):
        resultado = sincronizar_turno_actualizado(self.turno, self._cliente_factory)

        self.turno.refresh_from_db()

        self.assertTrue(resultado.realizada)
        self.assertEqual(self.turno.google_calendar_event_id, "evento-creado")
        self.assertEqual(self.servicio.eventos.acciones[0]["accion"], "insert")

    def test_sincronizar_turno_cancelado_elimina_evento_y_limpia_event_id(self):
        self.turno.google_calendar_event_id = "evento-123"
        self.turno.estado = Turno.Estado.CANCELADO
        self.turno.save()

        resultado = sincronizar_turno_cancelado(self.turno, self._cliente_factory)

        self.turno.refresh_from_db()

        self.assertTrue(resultado.realizada)
        self.assertEqual(self.turno.google_calendar_event_id, "")
        self.assertEqual(self.servicio.eventos.acciones[0]["accion"], "delete")
        self.assertEqual(self.servicio.eventos.acciones[0]["eventId"], "evento-123")

    def test_sincronizacion_se_omite_si_el_odontologo_no_tiene_conexion(self):
        self.conexion.delete()

        resultado = sincronizar_turno_creado(self.turno, self._cliente_factory)

        self.turno.refresh_from_db()

        self.assertFalse(resultado.realizada)
        self.assertEqual(self.turno.google_calendar_event_id, "")
        self.assertEqual(self.servicio.eventos.acciones, [])

    def test_sincronizacion_registra_error_sin_romper_turno(self):
        resultado = sincronizar_turno_creado(
            self.turno,
            lambda conexion: GoogleCalendarClientErrorFake(),
        )

        self.turno.refresh_from_db()
        self.conexion.refresh_from_db()

        self.assertFalse(resultado.realizada)
        self.assertEqual(self.turno.google_calendar_event_id, "")
        self.assertIn("Fallo simulado", self.conexion.ultimo_error)
        self.assertTrue(Turno.objects.filter(pk=self.turno.pk).exists())

    def test_sincronizacion_registra_error_inesperado_sin_romper_turno(self):
        with self.assertLogs("turnos.google_calendar_sync", level="ERROR"):
            resultado = sincronizar_turno_creado(
                self.turno,
                lambda conexion: GoogleCalendarClientErrorInesperadoFake(),
            )

        self.turno.refresh_from_db()
        self.conexion.refresh_from_db()

        self.assertFalse(resultado.realizada)
        self.assertEqual(resultado.mensaje, MENSAJE_ERROR_INESPERADO)
        self.assertEqual(self.turno.google_calendar_event_id, "")
        self.assertEqual(self.conexion.ultimo_error, MENSAJE_ERROR_INESPERADO)
        self.assertTrue(Turno.objects.filter(pk=self.turno.pk).exists())

    def _cliente_factory(self, conexion):
        return GoogleCalendarClient(
            servicio=self.servicio,
            calendar_id=conexion.calendar_id,
        )


class GoogleCalendarClientTests(TestCase):
    def setUp(self):
        usuario = get_user_model().objects.create_user(
            username="dr.cliente",
            first_name="Diego",
            last_name="Cliente",
        )
        self.odontologo = Odontologo.objects.create(
            usuario=usuario,
            matricula="MN-CLIENTE",
        )
        self.paciente = Paciente.objects.create(
            nombre="Ana",
            apellido="Gomez",
            documento="32111222",
        )
        self.turno = Turno(
            pk=20,
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(11, 0),
            duracion_minutos=30,
            motivo="Control",
            estado=Turno.Estado.CONFIRMADO,
        )

    def test_crea_evento_usando_servicio_autenticado(self):
        servicio = GoogleCalendarServiceFake()
        cliente = GoogleCalendarClient(servicio=servicio)

        event_id = cliente.crear_evento(self.turno)

        self.assertEqual(event_id, "evento-creado")
        self.assertEqual(servicio.eventos.acciones[0]["accion"], "insert")
        self.assertEqual(servicio.eventos.acciones[0]["calendarId"], "primary")
        self.assertEqual(
            servicio.eventos.acciones[0]["body"]["summary"],
            "Turno odontológico - Gomez, Ana",
        )

    def test_actualiza_evento_existente(self):
        servicio = GoogleCalendarServiceFake()
        cliente = GoogleCalendarClient(servicio=servicio)
        self.turno.google_calendar_event_id = "evento-123"

        event_id = cliente.actualizar_evento(self.turno)

        self.assertEqual(event_id, "evento-123")
        self.assertEqual(servicio.eventos.acciones[0]["accion"], "update")
        self.assertEqual(servicio.eventos.acciones[0]["eventId"], "evento-123")

    def test_actualizacion_requiere_event_id(self):
        cliente = GoogleCalendarClient(servicio=GoogleCalendarServiceFake())

        with self.assertRaises(GoogleCalendarEventoSinIdError):
            cliente.actualizar_evento(self.turno)

    def test_cancelar_evento_elimina_evento_existente(self):
        servicio = GoogleCalendarServiceFake()
        cliente = GoogleCalendarClient(servicio=servicio)
        self.turno.google_calendar_event_id = "evento-123"

        cliente.cancelar_evento(self.turno)

        self.assertEqual(servicio.eventos.acciones[0]["accion"], "delete")
        self.assertEqual(servicio.eventos.acciones[0]["eventId"], "evento-123")

    def test_cancelar_evento_sin_event_id_no_llama_a_google(self):
        servicio = GoogleCalendarServiceFake()
        cliente = GoogleCalendarClient(servicio=servicio)

        respuesta = cliente.cancelar_evento(self.turno)

        self.assertIsNone(respuesta)
        self.assertEqual(servicio.eventos.acciones, [])

    def test_cliente_requiere_servicio_autenticado(self):
        cliente = GoogleCalendarClient()

        with self.assertRaises(GoogleCalendarClienteNoConfiguradoError):
            cliente.crear_evento(self.turno)


class GoogleCalendarServiceFake:
    def __init__(self):
        self.eventos = GoogleCalendarEventsFake()

    def events(self):
        return self.eventos


class GoogleCalendarEventsFake:
    def __init__(self):
        self.acciones = []

    def insert(self, calendarId, body):
        self.acciones.append(
            {
                "accion": "insert",
                "calendarId": calendarId,
                "body": body,
            }
        )
        return GoogleCalendarRequestFake({"id": "evento-creado"})

    def update(self, calendarId, eventId, body):
        self.acciones.append(
            {
                "accion": "update",
                "calendarId": calendarId,
                "eventId": eventId,
                "body": body,
            }
        )
        return GoogleCalendarRequestFake({"id": eventId})

    def delete(self, calendarId, eventId):
        self.acciones.append(
            {
                "accion": "delete",
                "calendarId": calendarId,
                "eventId": eventId,
            }
        )
        return GoogleCalendarRequestFake({})


class GoogleCalendarRequestFake:
    def __init__(self, respuesta):
        self.respuesta = respuesta

    def execute(self):
        return self.respuesta


class GoogleCalendarClientErrorFake:
    def crear_evento(self, turno):
        raise GoogleCalendarError("Fallo simulado al crear evento.")

    def actualizar_evento(self, turno):
        raise GoogleCalendarError("Fallo simulado al actualizar evento.")

    def cancelar_evento(self, turno):
        raise GoogleCalendarError("Fallo simulado al cancelar evento.")


class GoogleCalendarClientErrorInesperadoFake:
    def crear_evento(self, turno):
        raise RuntimeError("Fallo externo inesperado con token tecnico")
