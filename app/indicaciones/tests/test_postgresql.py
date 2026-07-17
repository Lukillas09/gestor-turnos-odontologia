import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.storage import FileSystemStorage
from django.db import DatabaseError, close_old_connections, connection
from django.test import TransactionTestCase, override_settings

from indicaciones.models import (
    IndicacionPaciente,
    PlantillaIndicacion,
    PlantillaIndicacionVersion,
)
from indicaciones.services import (
    crear_borrador_indicacion,
    crear_version_plantilla,
    emitir_indicacion,
)
from pacientes.models import Paciente, PacienteOdontologo
from turnos.models import Odontologo
from usuarios.roles import ROL_ODONTOLOGO


@unittest.skipUnless(
    connection.vendor == "postgresql",
    "La concurrencia y los triggers se prueban únicamente con PostgreSQL.",
)
@override_settings(
    INDICACIONES_POSTOPERATORIAS_ENABLED=True,
    CLINICAL_INTEGRITY_ENABLED=True,
    CLINICAL_INTEGRITY_HMAC_KEY="clave-integridad-indicaciones-postgresql-pruebas",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="consultorio@example.test",
    INDICACIONES_PDF_MAX_BYTES=5 * 1024 * 1024,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class IndicacionesPostgreSQLTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        self.directorio_privado = tempfile.TemporaryDirectory()
        self.campo_pdf = IndicacionPaciente._meta.get_field("pdf")
        self.storage_original = self.campo_pdf.storage
        self.campo_pdf.storage = FileSystemStorage(location=self.directorio_privado.name)
        self.addCleanup(self._restaurar_storage)

        grupo, _ = Group.objects.get_or_create(name=ROL_ODONTOLOGO)
        self.usuario = get_user_model().objects.create_user(
            username="odontologa.indicaciones.postgresql",
            password="clave-pruebas",
            first_name="Ana",
            last_name="Profesional",
        )
        self.usuario.groups.add(grupo)
        self.odontologo = Odontologo.objects.create(
            usuario=self.usuario,
            matricula="IND-PG-001",
            especialidad="Odontología general",
        )
        self.paciente = Paciente.objects.create(
            nombre="Paciente",
            apellido="PostgreSQL",
            documento="88999111",
            telefono="1100001111",
        )
        PacienteOdontologo.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            asignado_por=self.usuario,
            motivo="Relación clínica ficticia para pruebas PostgreSQL",
        )
        self.plantilla = PlantillaIndicacion.objects.create(
            nombre="Plantilla PostgreSQL ficticia",
            procedimiento="Procedimiento de prueba",
            titulo_documento="Indicaciones de prueba",
            contenido="Contenido clínico ficticio definido exclusivamente para pruebas.",
            pautas_alarma="Pauta ficticia para pruebas.",
            recomendaciones_control="Control ficticio para pruebas.",
            creado_por=self.usuario,
            actualizado_por=self.usuario,
        )

    def _restaurar_storage(self):
        self.campo_pdf.storage = self.storage_original
        self.directorio_privado.cleanup()

    def _crear_borrador(self):
        return crear_borrador_indicacion(
            paciente=self.paciente,
            usuario=self.usuario,
            datos={
                "plantilla": self.plantilla,
                "historia_clinica": None,
                "turno": None,
                "titulo": "Indicaciones PostgreSQL ficticias",
                "procedimiento": "Procedimiento de prueba",
                "contenido": "Contenido ficticio revisado por el profesional de prueba.",
                "pautas_alarma": "Pauta ficticia para validar el documento.",
                "recomendaciones_control": "Control ficticio indicado por el profesional.",
                "observaciones_personalizadas": "Observación ficticia individual.",
                "proximo_control_en": None,
            },
        )

    def test_emisiones_concurrentes_generan_un_unico_pdf(self):
        borrador = self._crear_borrador()

        def emitir_en_hilo(_):
            close_old_connections()
            try:
                indicacion = IndicacionPaciente.objects.get(pk=borrador.pk)
                usuario = get_user_model().objects.get(pk=self.usuario.pk)
                emitida = emitir_indicacion(indicacion=indicacion, usuario=usuario)
                return emitida.pk, emitida.pdf.name
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            resultados = list(executor.map(emitir_en_hilo, range(2)))

        borrador.refresh_from_db()
        archivos_pdf = list(Path(self.directorio_privado.name).rglob("*.pdf"))
        self.assertEqual(resultados[0], resultados[1])
        self.assertEqual(borrador.estado, IndicacionPaciente.Estado.EMITIDA)
        self.assertEqual(len(archivos_pdf), 1)

    def test_triggers_bloquean_mutaciones_y_borrados_directos(self):
        emitida = emitir_indicacion(
            indicacion=self._crear_borrador(),
            usuario=self.usuario,
        )
        crear_version_plantilla(
            plantilla=self.plantilla,
            usuario=self.usuario,
            datos={
                "nombre": self.plantilla.nombre,
                "procedimiento": self.plantilla.procedimiento,
                "titulo_documento": self.plantilla.titulo_documento,
                "contenido": "Contenido ficticio actualizado para la versión siguiente.",
                "pautas_alarma": self.plantilla.pautas_alarma,
                "recomendaciones_control": self.plantilla.recomendaciones_control,
                "activa": True,
            },
            motivo="Se prueba la protección append-only de PostgreSQL.",
        )
        version = PlantillaIndicacionVersion.objects.get(plantilla=self.plantilla)

        with self.assertRaises(DatabaseError), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE indicaciones_plantillaindicacionversion " "SET motivo = %s WHERE id = %s",
                ["Alterado", version.pk],
            )
        with self.assertRaises(DatabaseError), connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM indicaciones_plantillaindicacionversion WHERE id = %s",
                [version.pk],
            )
        with self.assertRaises(DatabaseError), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE indicaciones_indicacionpaciente SET contenido = %s WHERE id = %s",
                ["Alterado", emitida.pk],
            )
        with self.assertRaises(DatabaseError), connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM indicaciones_indicacionpaciente WHERE id = %s",
                [emitida.pk],
            )

        emitida.refresh_from_db()
        version.refresh_from_db()
        self.assertEqual(
            emitida.contenido,
            "Contenido ficticio revisado por el profesional de prueba.",
        )
        self.assertEqual(
            version.motivo,
            "Se prueba la protección append-only de PostgreSQL.",
        )
