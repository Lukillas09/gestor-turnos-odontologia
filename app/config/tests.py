from pathlib import Path

from django.test import SimpleTestCase

from .database import configurar_base_de_datos


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
            configurar_base_de_datos("postgres://usuario:clave@localhost:5432", Path("/proyecto/app"))

    def test_rechaza_esquemas_no_soportados(self):
        with self.assertRaises(RuntimeError):
            configurar_base_de_datos("mysql://usuario:clave@host/base", Path("/proyecto/app"))
