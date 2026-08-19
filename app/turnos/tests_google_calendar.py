import json
from datetime import date, time, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import OperationalError, connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from pacientes.models import Paciente
from turnos.admin import GoogleCalendarConexionAdmin
from turnos.fields import ENCRYPTED_TEXT_PREFIX
from turnos.google_calendar_sync import (
    MENSAJE_ERROR_INESPERADO,
    sincronizar_turno_actualizado,
    sincronizar_turno_cancelado,
    sincronizar_turno_creado,
)
from turnos.integrations.google_calendar import (
    GoogleCalendarClient,
    GoogleCalendarClienteNoConfiguradoError,
    GoogleCalendarCredencialesOAuthIncompletasError,
    GoogleCalendarError,
    GoogleCalendarEventoSinIdError,
    GoogleCalendarHTTPError,
    GoogleOAuthTokens,
    construir_evento_desde_turno,
    construir_google_calendar_event_id,
    construir_url_autorizacion_google_calendar,
    intercambiar_codigo_por_tokens,
    obtener_configuracion_google_calendar,
    renovar_access_token,
)
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

    def test_configuracion_esta_lista_con_archivo_de_credenciales(self):
        with TemporaryDirectory() as directorio:
            archivo = Path(directorio) / "oauth.json"
            archivo.write_text(
                json.dumps(
                    {
                        "web": {
                            "client_id": "file-client-id",
                            "client_secret": "file-client-secret",
                        }
                    }
                ),
                encoding="utf-8",
            )
            with override_settings(
                GOOGLE_CALENDAR_CLIENT_ID="",
                GOOGLE_CALENDAR_CLIENT_SECRET="",
                GOOGLE_CALENDAR_CLIENT_SECRET_FILE=str(archivo),
                GOOGLE_CALENDAR_CLIENT_SECRETS_FILE="",
            ):
                configuracion = obtener_configuracion_google_calendar()

        self.assertTrue(configuracion.esta_configurada)
        self.assertEqual(configuracion.client_id, "file-client-id")
        self.assertEqual(configuracion.client_secret, "file-client-secret")

    def test_variables_completas_tienen_prioridad_sobre_archivo(self):
        with override_settings(
            GOOGLE_CALENDAR_CLIENT_ID="env-client-id",
            GOOGLE_CALENDAR_CLIENT_SECRET="env-client-secret",
            GOOGLE_CALENDAR_CLIENT_SECRET_FILE="archivo-inexistente.json",
            GOOGLE_CALENDAR_CLIENT_SECRETS_FILE="",
        ):
            configuracion = obtener_configuracion_google_calendar()

        self.assertEqual(configuracion.client_id, "env-client-id")
        self.assertEqual(configuracion.client_secret, "env-client-secret")

    def test_archivo_inexistente_no_expone_ruta(self):
        ruta = "directorio-secreto/credenciales-super-secretas.json"
        with override_settings(
            GOOGLE_CALENDAR_CLIENT_ID="",
            GOOGLE_CALENDAR_CLIENT_SECRET="",
            GOOGLE_CALENDAR_CLIENT_SECRET_FILE=ruta,
            GOOGLE_CALENDAR_CLIENT_SECRETS_FILE="",
        ):
            with self.assertRaises(GoogleCalendarCredencialesOAuthIncompletasError) as contexto:
                obtener_configuracion_google_calendar()

        self.assertNotIn(ruta, str(contexto.exception))

    def test_archivo_json_invalido(self):
        self._assert_archivo_invalido("{no-json", "JSON válido")

    def test_archivo_sin_web(self):
        self._assert_archivo_invalido(json.dumps({"installed": {}}), "objeto web")

    def test_archivo_sin_client_id(self):
        self._assert_archivo_invalido(
            json.dumps({"web": {"client_secret": "secret"}}),
            "client_id",
        )

    def test_archivo_sin_client_secret(self):
        self._assert_archivo_invalido(
            json.dumps({"web": {"client_id": "client-id"}}),
            "client_secret",
        )

    def _assert_archivo_invalido(self, contenido, mensaje):
        with TemporaryDirectory() as directorio:
            archivo = Path(directorio) / "oauth.json"
            archivo.write_text(contenido, encoding="utf-8")
            with override_settings(
                GOOGLE_CALENDAR_CLIENT_ID="",
                GOOGLE_CALENDAR_CLIENT_SECRET="",
                GOOGLE_CALENDAR_CLIENT_SECRET_FILE=str(archivo),
                GOOGLE_CALENDAR_CLIENT_SECRETS_FILE="",
            ):
                with self.assertRaisesMessage(
                    GoogleCalendarCredencialesOAuthIncompletasError,
                    mensaje,
                ):
                    obtener_configuracion_google_calendar()


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

    def test_autorizacion_usa_credenciales_de_archivo(self):
        with TemporaryDirectory() as directorio:
            archivo = Path(directorio) / "oauth.json"
            archivo.write_text(
                json.dumps({"web": {"client_id": "file-id", "client_secret": "file-secret"}}),
                encoding="utf-8",
            )
            with override_settings(
                GOOGLE_CALENDAR_CLIENT_ID="",
                GOOGLE_CALENDAR_CLIENT_SECRET="",
                GOOGLE_CALENDAR_CLIENT_SECRET_FILE=str(archivo),
                GOOGLE_CALENDAR_CLIENT_SECRETS_FILE="",
            ):
                url = construir_url_autorizacion_google_calendar("state")

        self.assertEqual(parse_qs(urlparse(url).query)["client_id"], ["file-id"])


class GoogleCalendarOAuthArchivoTests(TestCase):
    def setUp(self):
        usuario = get_user_model().objects.create_user(username="oauth.archivo")
        self.odontologo = Odontologo.objects.create(usuario=usuario, matricula="OAUTH-FILE")

    def _crear_archivo(self, directorio):
        archivo = Path(directorio) / "oauth.json"
        archivo.write_text(
            json.dumps({"web": {"client_id": "file-id", "client_secret": "file-secret"}}),
            encoding="utf-8",
        )
        return archivo

    def test_intercambio_usa_credenciales_resueltas_del_archivo(self):
        with TemporaryDirectory() as directorio:
            archivo = self._crear_archivo(directorio)
            with (
                override_settings(
                    GOOGLE_CALENDAR_CLIENT_ID="",
                    GOOGLE_CALENDAR_CLIENT_SECRET="",
                    GOOGLE_CALENDAR_CLIENT_SECRET_FILE=str(archivo),
                    GOOGLE_CALENDAR_CLIENT_SECRETS_FILE="",
                ),
                patch(
                    "turnos.integrations.google_calendar._ejecutar_request_json",
                    return_value={"access_token": "access"},
                ) as ejecutar,
            ):
                tokens = intercambiar_codigo_por_tokens("codigo")

        parametros = parse_qs(ejecutar.call_args.args[0].data.decode())
        self.assertEqual(tokens.access_token, "access")
        self.assertEqual(parametros["client_id"], ["file-id"])
        self.assertEqual(parametros["client_secret"], ["file-secret"])

    def test_refresh_usa_credenciales_resueltas_del_archivo(self):
        conexion = GoogleCalendarConexion.objects.create(
            odontologo=self.odontologo,
            refresh_token="refresh",
        )
        with TemporaryDirectory() as directorio:
            archivo = self._crear_archivo(directorio)
            with (
                override_settings(
                    GOOGLE_CALENDAR_CLIENT_ID="",
                    GOOGLE_CALENDAR_CLIENT_SECRET="",
                    GOOGLE_CALENDAR_CLIENT_SECRET_FILE=str(archivo),
                    GOOGLE_CALENDAR_CLIENT_SECRETS_FILE="",
                ),
                patch(
                    "turnos.integrations.google_calendar._ejecutar_request_json",
                    return_value={"access_token": "nuevo-access"},
                ) as ejecutar,
            ):
                renovar_access_token(conexion)

        parametros = parse_qs(ejecutar.call_args.args[0].data.decode())
        conexion.refresh_from_db()
        self.assertEqual(conexion.access_token, "nuevo-access")
        self.assertEqual(parametros["client_id"], ["file-id"])
        self.assertEqual(parametros["client_secret"], ["file-secret"])


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
            duracion_atencion_minutos=30,
            margen_posterior_minutos_snapshot=15,
            motivo="Limpieza",
            estado=Turno.Estado.CONFIRMADO,
            notas="Primera visita",
        )

    def test_construye_payload_de_evento_desde_turno(self):
        evento = construir_evento_desde_turno(self.turno)
        payload = evento.como_payload()

        self.assertEqual(set(payload), {"summary", "start", "end", "extendedProperties"})
        self.assertEqual(payload["summary"], "Turno odontológico")
        self.assertEqual(payload["start"]["timeZone"], "America/Argentina/Buenos_Aires")
        self.assertEqual(payload["end"]["timeZone"], "America/Argentina/Buenos_Aires")
        self.assertIn("2026-05-08T10:00:00", payload["start"]["dateTime"])
        self.assertIn("2026-05-08T10:45:00", payload["end"]["dateTime"])
        contenido = json.dumps(payload)
        for dato_sensible in (
            "Paredes",
            "Lucas",
            "Carla",
            "Calendar",
            "Limpieza",
            "Primera visita",
        ):
            self.assertNotIn(dato_sensible, contenido)
        for clave_sensible in ("turno_id", "paciente_id", "odontologo_id", "description"):
            self.assertNotIn(clave_sensible, contenido)
        self.assertEqual(
            payload["extendedProperties"]["private"],
            {
                "source": "gestor-turnos",
                "appointment_ref": evento.event_id,
            },
        )
        self.assertEqual(payload["end"]["dateTime"][11:19], "10:45:00")

    def test_id_determinista_es_estable_opaco_y_valido(self):
        event_id = construir_google_calendar_event_id(self.turno)

        self.assertEqual(event_id, construir_google_calendar_event_id(self.turno))
        self.assertRegex(event_id, r"^gt[0-9a-v]{64}$")
        self.assertNotEqual(event_id, str(self.turno.pk))

    def test_create_agrega_id_y_update_lo_omite(self):
        evento = construir_evento_desde_turno(self.turno)

        self.assertEqual(evento.como_payload(incluir_id=True)["id"], evento.event_id)
        self.assertNotIn("id", evento.como_payload())

    def test_evento_legacy_conserva_id_existente(self):
        self.turno.google_calendar_event_id = "evento-legacy"

        self.assertEqual(
            construir_google_calendar_event_id(self.turno),
            "evento-legacy",
        )


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

    def _obtener_tokens_raw(self, conexion):
        tabla = connection.ops.quote_name(GoogleCalendarConexion._meta.db_table)

        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT access_token, refresh_token FROM {tabla} WHERE id = %s",
                [conexion.pk],
            )
            return cursor.fetchone()

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

    def test_tokens_oauth_se_guardan_cifrados_en_base_de_datos(self):
        conexion = GoogleCalendarConexion.objects.create(
            odontologo=self.odontologo,
            access_token="access-token-secreto",
            refresh_token="refresh-token-secreto",
        )
        access_token_raw, refresh_token_raw = self._obtener_tokens_raw(conexion)

        self.assertNotEqual(access_token_raw, "access-token-secreto")
        self.assertNotEqual(refresh_token_raw, "refresh-token-secreto")
        self.assertTrue(access_token_raw.startswith(ENCRYPTED_TEXT_PREFIX))
        self.assertTrue(refresh_token_raw.startswith(ENCRYPTED_TEXT_PREFIX))

        conexion.refresh_from_db()

        self.assertEqual(conexion.access_token, "access-token-secreto")
        self.assertEqual(conexion.refresh_token, "refresh-token-secreto")

    def test_tokens_oauth_legacy_en_texto_plano_se_recifran_al_guardar(self):
        conexion = GoogleCalendarConexion.objects.create(odontologo=self.odontologo)
        tabla = connection.ops.quote_name(GoogleCalendarConexion._meta.db_table)

        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {tabla} SET access_token = %s, refresh_token = %s WHERE id = %s",
                ["access-token-legacy", "refresh-token-legacy", conexion.pk],
            )

        conexion.refresh_from_db()

        self.assertEqual(conexion.access_token, "access-token-legacy")
        self.assertEqual(conexion.refresh_token, "refresh-token-legacy")

        conexion.save(update_fields=["access_token", "refresh_token", "actualizado_en"])
        access_token_raw, refresh_token_raw = self._obtener_tokens_raw(conexion)

        self.assertTrue(access_token_raw.startswith(ENCRYPTED_TEXT_PREFIX))
        self.assertTrue(refresh_token_raw.startswith(ENCRYPTED_TEXT_PREFIX))
        self.assertNotEqual(access_token_raw, "access-token-legacy")
        self.assertNotEqual(refresh_token_raw, "refresh-token-legacy")

    @override_settings(
        DEBUG=False,
        OAUTH_TOKEN_ENCRYPTION_KEY="",
        OAUTH_TOKEN_ENCRYPTION_KEY_REQUIRED=True,
    )
    def test_exige_clave_de_cifrado_explicita_en_produccion(self):
        conexion = GoogleCalendarConexion(
            odontologo=self.odontologo,
            access_token="access-token",
            refresh_token="refresh-token",
        )

        with self.assertRaisesMessage(ImproperlyConfigured, "OAUTH_TOKEN_ENCRYPTION_KEY"):
            conexion.save()

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
        self.assertFalse(GoogleCalendarConexion.objects.filter(odontologo=self.odontologo).exists())

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
        event_id_esperado = construir_google_calendar_event_id(self.turno)
        resultado = sincronizar_turno_creado(self.turno, self._cliente_factory)

        self.turno.refresh_from_db()
        self.conexion.refresh_from_db()

        self.assertTrue(resultado.realizada)
        self.assertEqual(resultado.event_id, event_id_esperado)
        self.assertEqual(self.turno.google_calendar_event_id, event_id_esperado)
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
        event_id_esperado = construir_google_calendar_event_id(self.turno)
        resultado = sincronizar_turno_actualizado(self.turno, self._cliente_factory)

        self.turno.refresh_from_db()

        self.assertTrue(resultado.realizada)
        self.assertEqual(self.turno.google_calendar_event_id, event_id_esperado)
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
        self.assertEqual(
            self.conexion.ultimo_error,
            "No se pudo completar la sincronización con Google Calendar.",
        )
        self.assertTrue(Turno.objects.filter(pk=self.turno.pk).exists())

    def test_sincronizacion_registra_error_inesperado_sin_romper_turno(self):
        with self.assertLogs("turnos.google_calendar_sync", level="WARNING") as logs:
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
        self.assertNotIn("token tecnico", " ".join(logs.output))

    def test_retry_tras_timeout_posterior_al_alta_no_duplica_evento(self):
        servicio = GoogleCalendarServiceIdempotenteFake(fallar_despues_de_insert=True)

        def factory(_conexion):
            return GoogleCalendarClient(servicio=servicio)

        primer_resultado = sincronizar_turno_creado(self.turno, factory)
        servicio.fallar_despues_de_insert = False
        segundo_resultado = sincronizar_turno_creado(self.turno, factory)

        self.turno.refresh_from_db()
        self.assertFalse(primer_resultado.realizada)
        self.assertTrue(segundo_resultado.realizada)
        self.assertEqual(len(servicio.eventos.remotos), 1)
        self.assertEqual(self.turno.google_calendar_event_id, segundo_resultado.event_id)
        self.assertEqual(
            [accion["accion"] for accion in servicio.eventos.acciones],
            ["insert", "insert", "update"],
        )

    def test_retry_tras_fallo_local_no_duplica_evento(self):
        servicio = GoogleCalendarServiceIdempotenteFake()

        def factory(_conexion):
            return GoogleCalendarClient(servicio=servicio)

        with patch.object(
            Turno.objects,
            "filter",
            side_effect=OperationalError("fallo local con contenido sensible"),
        ):
            primer_resultado = sincronizar_turno_creado(self.turno, factory)

        segundo_resultado = sincronizar_turno_creado(self.turno, factory)

        self.turno.refresh_from_db()
        self.assertFalse(primer_resultado.realizada)
        self.assertTrue(segundo_resultado.realizada)
        self.assertEqual(len(servicio.eventos.remotos), 1)
        self.assertEqual(self.turno.google_calendar_event_id, segundo_resultado.event_id)

    def test_logs_no_incluyen_payload_ni_error_del_proveedor(self):
        with self.assertLogs("turnos.google_calendar_sync", level="WARNING") as logs:
            sincronizar_turno_creado(
                self.turno,
                lambda _conexion: GoogleCalendarClientErrorConDatosSensiblesFake(),
            )

        salida = " ".join(logs.output)
        self.assertNotIn(self.paciente.nombre, salida)
        self.assertNotIn(self.turno.motivo, salida)
        self.assertNotIn("token-super-secreto", salida)

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

        self.assertEqual(event_id, construir_google_calendar_event_id(self.turno))
        self.assertEqual(servicio.eventos.acciones[0]["accion"], "insert")
        self.assertEqual(servicio.eventos.acciones[0]["calendarId"], "primary")
        self.assertEqual(
            servicio.eventos.acciones[0]["body"]["summary"],
            "Turno odontológico",
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
        return GoogleCalendarRequestFake({"id": body["id"]})

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


class GoogleCalendarServiceIdempotenteFake:
    def __init__(self, *, fallar_despues_de_insert=False):
        self.eventos = GoogleCalendarEventsIdempotenteFake(self)
        self.fallar_despues_de_insert = fallar_despues_de_insert

    def events(self):
        return self.eventos


class GoogleCalendarEventsIdempotenteFake:
    def __init__(self, servicio):
        self.servicio = servicio
        self.remotos = {}
        self.acciones = []

    def insert(self, calendarId, body):
        def ejecutar():
            event_id = body["id"]
            self.acciones.append({"accion": "insert", "eventId": event_id})
            if event_id in self.remotos:
                raise GoogleCalendarHTTPError("duplicate", status_code=409)
            self.remotos[event_id] = body
            if self.servicio.fallar_despues_de_insert:
                raise GoogleCalendarHTTPError("timeout remoto")
            return {"id": event_id}

        return GoogleCalendarCallableRequestFake(ejecutar)

    def update(self, calendarId, eventId, body):
        def ejecutar():
            self.acciones.append({"accion": "update", "eventId": eventId})
            self.remotos[eventId] = body
            return {"id": eventId}

        return GoogleCalendarCallableRequestFake(ejecutar)

    def delete(self, calendarId, eventId):
        def ejecutar():
            self.acciones.append({"accion": "delete", "eventId": eventId})
            self.remotos.pop(eventId, None)
            return {}

        return GoogleCalendarCallableRequestFake(ejecutar)


class GoogleCalendarCallableRequestFake:
    def __init__(self, callback):
        self.callback = callback

    def execute(self):
        return self.callback()


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


class GoogleCalendarClientErrorConDatosSensiblesFake:
    def crear_evento(self, turno):
        raise GoogleCalendarError(
            f"paciente={turno.paciente.nombre} motivo={turno.motivo} token-super-secreto"
        )
