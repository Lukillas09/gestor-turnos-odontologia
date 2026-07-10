from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, override_settings

from .database import configurar_base_de_datos
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
        self.assertEqual(config["OPTIONS"], {"sslmode": "require"})

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
