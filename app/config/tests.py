import json
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from django.conf import settings
from django.contrib.messages import constants
from django.contrib.messages.storage.base import Message
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.db import OperationalError
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from .database import (
    AjustesConexionPostgres,
    cargar_ajustes_postgres_desde_env,
    configurar_base_de_datos,
    validar_coherencia_con_gunicorn,
)
from .db_errors import es_espera_de_base_agotada, sqlstate_de
from .middleware import (
    MENSAJE_BASE_OCUPADA,
    RETRY_AFTER_BASE_OCUPADA_SECONDS,
    ErrorEsperaBaseMiddleware,
)
from .storage_backends import SupabaseStorage


class FakeHttpResponse:
    def __init__(self, status=200, body=b"", headers=None):
        self.status = status
        self._body = body
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class DatabaseConfigTests(SimpleTestCase):
    def test_configura_sqlite_por_defecto_si_no_hay_database_url(self):
        base_dir = Path("/proyecto/app")

        config = configurar_base_de_datos("", base_dir)

        self.assertEqual(config["ENGINE"], "django.db.backends.sqlite3")
        self.assertEqual(config["NAME"], base_dir / "db.sqlite3")

    def test_configura_sqlite_desde_database_url(self):
        base_dir = Path("/proyecto/app")

        config = configurar_base_de_datos("sqlite:///data/db.sqlite3", base_dir)

        self.assertEqual(config["ENGINE"], "django.db.backends.sqlite3")
        self.assertEqual(config["NAME"], base_dir / "data/db.sqlite3")

    def test_configura_postgres_desde_database_url(self):
        config = configurar_base_de_datos(
            "postgres://usuario:clave@db.example.com:5432/turnos?sslmode=require",
            Path("/proyecto/app"),
        )

        self.assertEqual(config["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(config["NAME"], "turnos")
        self.assertEqual(config["USER"], "usuario")
        self.assertEqual(config["PASSWORD"], "clave")
        self.assertEqual(config["HOST"], "db.example.com")
        self.assertEqual(config["PORT"], "5432")
        # La URL conserva sus opciones; los timeouts por defecto se suman sin pisarlas.
        self.assertEqual(config["OPTIONS"]["sslmode"], "require")

    def test_configura_postgresql_desde_database_url_con_valores_codificados(self):
        config = configurar_base_de_datos(
            "postgresql://usuario:p%40ss@db.example.com:5432/turnos_prod",
            Path("/proyecto/app"),
        )

        self.assertEqual(config["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(config["NAME"], "turnos_prod")
        self.assertEqual(config["PASSWORD"], "p@ss")

    def test_rechaza_postgres_sin_nombre_de_base(self):
        with self.assertRaises(RuntimeError):
            configurar_base_de_datos(
                "postgres://usuario:clave@localhost:5432", Path("/proyecto/app")
            )

    def test_rechaza_esquemas_no_soportados(self):
        with self.assertRaises(RuntimeError):
            configurar_base_de_datos("mysql://usuario:clave@host/base", Path("/proyecto/app"))

    def configurar_postgres(self, url, **ajustes):
        return configurar_base_de_datos(
            url, Path("/proyecto/app"), AjustesConexionPostgres(**ajustes)
        )

    def test_postgres_aplica_conexiones_persistentes_y_timeouts_por_defecto(self):
        config = configurar_base_de_datos(
            "postgres://usuario:clave@db.example.com:5432/turnos", Path("/proyecto/app")
        )

        self.assertEqual(config["CONN_MAX_AGE"], 60)
        self.assertTrue(config["CONN_HEALTH_CHECKS"])
        self.assertEqual(config["OPTIONS"]["connect_timeout"], 5)
        self.assertEqual(
            config["OPTIONS"]["options"],
            "-c lock_timeout=5000 -c statement_timeout=20000 "
            "-c idle_in_transaction_session_timeout=60000",
        )
        self.assertNotIn("DISABLE_SERVER_SIDE_CURSORS", config)

    def test_postgres_toma_los_ajustes_desde_el_entorno(self):
        entorno = {
            "DATABASE_CONN_MAX_AGE": "120",
            "DATABASE_CONNECT_TIMEOUT_SECONDS": "3",
            "DATABASE_LOCK_TIMEOUT_SECONDS": "2",
            "DATABASE_STATEMENT_TIMEOUT_SECONDS": "10",
            "DATABASE_IDLE_IN_TRANSACTION_TIMEOUT_SECONDS": "15",
        }
        with patch.dict("os.environ", entorno):
            ajustes = cargar_ajustes_postgres_desde_env()
        config = configurar_base_de_datos(
            "postgres://usuario:clave@db.example.com:5432/turnos",
            Path("/proyecto/app"),
            ajustes,
        )

        self.assertEqual(config["CONN_MAX_AGE"], 120)
        self.assertEqual(config["OPTIONS"]["connect_timeout"], 3)
        self.assertEqual(
            config["OPTIONS"]["options"],
            "-c lock_timeout=2000 -c statement_timeout=10000 "
            "-c idle_in_transaction_session_timeout=15000",
        )

    def test_ajustes_del_entorno_invalidos_fallan_con_mensaje_claro(self):
        for valor in ("-1", "abc", "1.5"):
            with self.subTest(valor=valor):
                with patch.dict("os.environ", {"DATABASE_LOCK_TIMEOUT_SECONDS": valor}):
                    with self.assertRaisesMessage(RuntimeError, "DATABASE_LOCK_TIMEOUT_SECONDS"):
                        cargar_ajustes_postgres_desde_env()

    def test_cero_desactiva_cada_limite(self):
        config = self.configurar_postgres(
            "postgres://usuario:clave@db.example.com:5432/turnos",
            lock_timeout=0,
            statement_timeout=0,
            idle_in_transaction_timeout=30,
            connect_timeout=0,
        )

        self.assertEqual(
            config["OPTIONS"], {"options": "-c idle_in_transaction_session_timeout=30000"}
        )

        sin_limites = self.configurar_postgres(
            "postgres://usuario:clave@db.example.com:5432/turnos",
            lock_timeout=0,
            statement_timeout=0,
            idle_in_transaction_timeout=0,
            connect_timeout=0,
        )
        self.assertNotIn("OPTIONS", sin_limites)

    def test_la_url_tiene_prioridad_sobre_los_valores_por_defecto(self):
        config = self.configurar_postgres(
            "postgres://usuario:clave@db.example.com:5432/turnos"
            "?sslmode=require&connect_timeout=10"
            "&options=-c%20lock_timeout%3D1000%20-cstatement_timeout%3D500"
        )

        self.assertEqual(config["OPTIONS"]["sslmode"], "require")
        self.assertEqual(config["OPTIONS"]["connect_timeout"], "10")
        # Los dos que trae la URL no se duplican ni se pisan; solo se agrega el que falta.
        self.assertEqual(
            config["OPTIONS"]["options"],
            "-c lock_timeout=1000 -cstatement_timeout=500 "
            "-c idle_in_transaction_session_timeout=60000",
        )

    def test_pooler_en_modo_transaccion_evita_estado_de_sesion(self):
        config = self.configurar_postgres(
            "postgres://postgres.ref:clave@aws-0-sa.pooler.supabase.com:6543/postgres"
            "?sslmode=require"
        )

        self.assertTrue(config["DISABLE_SERVER_SIDE_CURSORS"])
        self.assertIsNone(config["OPTIONS"]["prepare_threshold"])
        self.assertEqual(config["OPTIONS"]["sslmode"], "require")
        self.assertEqual(config["OPTIONS"]["connect_timeout"], 5)
        # Un pooler en modo transacción no garantiza parámetros de sesión: no se envían.
        self.assertNotIn("options", config["OPTIONS"])
        self.assertEqual(config["CONN_MAX_AGE"], 60)

    def test_pooler_en_modo_sesion_se_trata_como_conexion_normal(self):
        config = self.configurar_postgres(
            "postgres://postgres.ref:clave@aws-0-sa.pooler.supabase.com:5432/postgres"
        )

        self.assertNotIn("DISABLE_SERVER_SIDE_CURSORS", config)
        self.assertNotIn("prepare_threshold", config["OPTIONS"])
        self.assertIn("-c lock_timeout=5000", config["OPTIONS"]["options"])

    def test_sqlite_no_recibe_ajustes_de_postgres(self):
        ajustes = AjustesConexionPostgres(conn_max_age=300)

        for url in ("", "sqlite:///data/db.sqlite3"):
            with self.subTest(url=url):
                config = configurar_base_de_datos(url, Path("/proyecto/app"), ajustes)
                self.assertEqual(set(config), {"ENGINE", "NAME"})

    def test_coherencia_con_el_timeout_de_gunicorn(self):
        validar_coherencia_con_gunicorn(AjustesConexionPostgres(), 30)
        # Sin statement_timeout (migraciones) o sin timeout de gunicorn no hay nada que comparar.
        validar_coherencia_con_gunicorn(AjustesConexionPostgres(statement_timeout=0), 10)
        validar_coherencia_con_gunicorn(AjustesConexionPostgres(), 0)

        with self.assertRaisesMessage(RuntimeError, "GUNICORN_TIMEOUT"):
            validar_coherencia_con_gunicorn(AjustesConexionPostgres(), 25)
        with self.assertRaisesMessage(RuntimeError, "GUNICORN_TIMEOUT"):
            validar_coherencia_con_gunicorn(
                AjustesConexionPostgres(lock_timeout=10, statement_timeout=25), 30
            )


def error_de_base(sqlstate=None, detalle="dni=88777111 token=secreto"):
    """OperationalError como lo arma Django: la causa es el error original de psycopg."""
    error = OperationalError(detalle)
    if sqlstate:
        causa = Exception(detalle)
        causa.sqlstate = sqlstate
        error.__cause__ = causa
    return error


class EsperaDeBaseAgotadaTests(SimpleTestCase):
    def test_reconoce_lock_timeout_statement_timeout_y_deadlock(self):
        for sqlstate in ("55P03", "57014", "40P01"):
            with self.subTest(sqlstate=sqlstate):
                self.assertTrue(es_espera_de_base_agotada(error_de_base(sqlstate)))

    def test_no_confunde_otros_errores_de_base(self):
        self.assertFalse(es_espera_de_base_agotada(error_de_base("08006")))
        self.assertFalse(es_espera_de_base_agotada(error_de_base()))
        self.assertFalse(es_espera_de_base_agotada(ValueError("otra cosa")))
        self.assertEqual(sqlstate_de(error_de_base("55P03")), "55P03")
        self.assertEqual(sqlstate_de(error_de_base()), "")


class ErrorEsperaBaseMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.middleware = ErrorEsperaBaseMiddleware(lambda request: None)
        self.factory = RequestFactory()

    def test_responde_503_neutral_con_retry_after(self):
        request = self.factory.get("/turnos/algo/6b14590e-73d1-492a-9e11-b490ef7ff1d4/")

        with self.assertLogs("config.middleware", level="WARNING") as registro:
            response = self.middleware.process_exception(request, error_de_base("55P03"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Retry-After"], str(RETRY_AFTER_BASE_OCUPADA_SECONDS))
        self.assertEqual(response.content.decode(), MENSAJE_BASE_OCUPADA)
        salida = "\n".join(registro.output)
        self.assertIn("error_type=OperationalError", salida)
        self.assertIn("sqlstate=55P03", salida)
        # Ni el SQL ni los datos del error ni la ruta (puede llevar identificadores).
        for sensible in ("dni=88777111", "secreto", "6b14590e"):
            self.assertNotIn(sensible, salida)
            self.assertNotIn(sensible, response.content.decode())

    def test_responde_json_si_el_cliente_lo_pide(self):
        request = self.factory.get("/", headers={"Accept": "application/json"})

        response = self.middleware.process_exception(request, error_de_base("57014"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(
            json.loads(response.content), {"ok": False, "mensaje": MENSAJE_BASE_OCUPADA}
        )

    def test_no_captura_errores_que_no_son_esperas(self):
        request = self.factory.get("/")

        self.assertIsNone(self.middleware.process_exception(request, error_de_base("08006")))
        self.assertIsNone(self.middleware.process_exception(request, error_de_base()))
        self.assertIsNone(self.middleware.process_exception(request, ValueError("x")))


class ErrorEsperaBaseIntegracionTests(TestCase):
    def test_esta_instalado_y_convierte_la_excepcion_de_una_vista(self):
        self.assertIn("config.middleware.ErrorEsperaBaseMiddleware", settings.MIDDLEWARE)

        with patch(
            "turnos.views.public_booking.LandingPublicaPacientesView.get_context_data",
            side_effect=error_de_base("55P03"),
        ):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, "secreto", status_code=503)


class StaticFilesConfigTests(SimpleTestCase):
    def test_whitenoise_esta_configurado_despues_de_security_middleware(self):
        self.assertEqual(
            settings.MIDDLEWARE[0],
            "django.middleware.security.SecurityMiddleware",
        )
        self.assertEqual(
            settings.MIDDLEWARE[1],
            "whitenoise.middleware.WhiteNoiseMiddleware",
        )

    def test_staticfiles_usa_storage_comprimido_y_versionado(self):
        self.assertEqual(
            settings.STORAGES["staticfiles"]["BACKEND"],
            "whitenoise.storage.CompressedManifestStaticFilesStorage",
        )
        self.assertTrue(str(settings.STATIC_ROOT).endswith("staticfiles"))


class SupabaseStorageTests(SimpleTestCase):
    @override_settings(
        SUPABASE_STORAGE_URL="https://proyecto.supabase.co",
        SUPABASE_STORAGE_BUCKET="historias-clinicas",
        SUPABASE_STORAGE_SERVICE_ROLE_KEY="service-role",
        SUPABASE_STORAGE_TIMEOUT=30,
        SUPABASE_STORAGE_CACHE_CONTROL="3600",
        SUPABASE_STORAGE_SIGNED_URL_SECONDS=300,
    )
    def test_guarda_archivo_en_bucket_configurado(self):
        with patch(
            "config.storage_backends.urlopen",
            side_effect=[
                HTTPError(
                    url="https://proyecto.supabase.co/storage/v1/object/historias-clinicas/historias/1/radiografia.txt",
                    code=404,
                    msg="Not Found",
                    hdrs=None,
                    fp=BytesIO(b"{}"),
                ),
                FakeHttpResponse(status=201, body=b"{}"),
            ],
        ) as urlopen_mock:
            storage = SupabaseStorage()
            nombre = storage.save("historias/1/radiografia.txt", ContentFile(b"contenido"))

        request = urlopen_mock.call_args_list[1].args[0]

        self.assertEqual(nombre, "historias/1/radiografia.txt")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.data, b"contenido")
        self.assertIn(
            "/storage/v1/object/historias-clinicas/historias/1/radiografia.txt",
            request.full_url,
        )
        self.assertEqual(request.headers["Authorization"], "Bearer service-role")

    @override_settings(
        SUPABASE_STORAGE_URL="https://proyecto.supabase.co/storage/v1",
        SUPABASE_STORAGE_BUCKET="historias-clinicas",
        SUPABASE_STORAGE_SERVICE_ROLE_KEY="service-role",
        SUPABASE_STORAGE_TIMEOUT=30,
        SUPABASE_STORAGE_CACHE_CONTROL="3600",
        SUPABASE_STORAGE_SIGNED_URL_SECONDS=300,
    )
    def test_genera_url_firmada_temporal(self):
        response = b'{"signedURL": "/object/sign/historias-clinicas/archivo.pdf?token=abc"}'

        with patch(
            "config.storage_backends.urlopen",
            return_value=FakeHttpResponse(status=200, body=response),
        ):
            storage = SupabaseStorage()
            url = storage.url("archivo.pdf")

        self.assertEqual(
            url,
            "https://proyecto.supabase.co/storage/v1/object/sign/"
            "historias-clinicas/archivo.pdf?token=abc",
        )

    @override_settings(
        SUPABASE_STORAGE_URL="",
        SUPABASE_STORAGE_BUCKET="historias-clinicas",
        SUPABASE_STORAGE_SERVICE_ROLE_KEY="service-role",
        SUPABASE_STORAGE_TIMEOUT=30,
        SUPABASE_STORAGE_CACHE_CONTROL="3600",
        SUPABASE_STORAGE_SIGNED_URL_SECONDS=300,
    )
    def test_requiere_url_de_supabase(self):
        with self.assertRaises(ImproperlyConfigured):
            SupabaseStorage()


class MensajesCompartidosTests(SimpleTestCase):
    def _render(self, *mensajes):
        return render_to_string("includes/messages.html", {"messages": list(mensajes)})

    def test_la_region_en_vivo_va_en_el_texto_y_el_li_conserva_su_rol(self):
        html = self._render(Message(constants.SUCCESS, "Guardado."))

        self.assertIn('<ul class="messages" aria-label="Notificaciones">', html)
        self.assertIn('<li class="message success" data-message>', html)
        self.assertNotRegex(html, r"<li[^>]*role=")

    def test_solo_los_errores_usan_alert(self):
        casos = (
            (constants.ERROR, "error", "alert"),
            (constants.SUCCESS, "success", "status"),
            (constants.WARNING, "warning", "status"),
            (constants.INFO, "info", "status"),
        )
        for nivel, clase, rol in casos:
            with self.subTest(clase=clase):
                html = self._render(Message(nivel, "Texto del mensaje."))

                self.assertRegex(
                    html,
                    rf'(?s)<li class="message {clase}" data-message>.*?'
                    rf'<span class="message-copy" role="{rol}">',
                )
                self.assertEqual(html.count('role="alert"'), 1 if rol == "alert" else 0)
