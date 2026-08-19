import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.storage import FileSystemStorage
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from indicaciones.emails import enviar_indicacion_por_email
from indicaciones.models import IndicacionPaciente, PlantillaIndicacion
from indicaciones.services import crear_borrador_indicacion, emitir_indicacion
from pacientes.models import Paciente, PacienteOdontologo
from turnos.models import Odontologo
from usuarios.roles import ROL_ODONTOLOGO


@override_settings(
    INDICACIONES_POSTOPERATORIAS_ENABLED=True,
    CLINICAL_INTEGRITY_ENABLED=True,
    CLINICAL_INTEGRITY_HMAC_KEY="clave-integridad-email-transaccion-pruebas",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="consultorio@example.test",
    INDICACIONES_PDF_MAX_BYTES=5 * 1024 * 1024,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class IndicacionEmailTransactionTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.directorio_privado = tempfile.TemporaryDirectory()
        self.campo_pdf = IndicacionPaciente._meta.get_field("pdf")
        self.storage_original = self.campo_pdf.storage
        self.campo_pdf.storage = FileSystemStorage(location=self.directorio_privado.name)
        self.addCleanup(self._restaurar_storage)

        grupo, _ = Group.objects.get_or_create(name=ROL_ODONTOLOGO)
        self.usuario = get_user_model().objects.create_user(
            username="odontologa.email.transaccion",
            password="clave-pruebas",
        )
        self.usuario.groups.add(grupo)
        self.odontologo = Odontologo.objects.create(
            usuario=self.usuario,
            matricula="IND-TX-001",
            especialidad="Odontología general",
        )
        self.paciente = Paciente.objects.create(
            nombre="Paciente",
            apellido="Transacción",
            documento="88777111",
        )
        PacienteOdontologo.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            asignado_por=self.usuario,
            motivo="Relación clínica ficticia para probar el envío fuera de transacción.",
        )
        plantilla = PlantillaIndicacion.objects.create(
            nombre="Plantilla transaccional ficticia",
            procedimiento="Procedimiento de prueba",
            titulo_documento="Indicaciones de prueba",
            contenido="Contenido clínico ficticio para la prueba transaccional.",
            creado_por=self.usuario,
            actualizado_por=self.usuario,
        )
        borrador = crear_borrador_indicacion(
            paciente=self.paciente,
            usuario=self.usuario,
            datos={
                "plantilla": plantilla,
                "historia_clinica": None,
                "turno": None,
                "titulo": "Indicaciones de prueba",
                "procedimiento": "Procedimiento de prueba",
                "contenido": "Contenido clínico ficticio revisado para la prueba.",
                "pautas_alarma": "Pauta ficticia.",
                "recomendaciones_control": "Control ficticio.",
                "observaciones_personalizadas": "",
                "proximo_control_en": None,
            },
        )
        self.indicacion = emitir_indicacion(indicacion=borrador, usuario=self.usuario)
        self.paciente.email = "paciente@example.test"
        self.paciente.email_verificado_en = timezone.now()
        self.paciente.save()

    def _restaurar_storage(self):
        self.campo_pdf.storage = self.storage_original
        self.directorio_privado.cleanup()

    def test_proveedor_se_invoca_fuera_de_transaccion(self):
        def enviar_fuera_de_transaccion(*args, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            return 1

        with patch(
            "indicaciones.emails.EmailMessage.send",
            side_effect=enviar_fuera_de_transaccion,
        ):
            resultado = enviar_indicacion_por_email(
                indicacion_id=self.indicacion.pk,
                usuario=self.usuario,
                usar_email_actual=True,
            )

        self.indicacion.refresh_from_db()
        self.assertTrue(resultado)
        self.assertEqual(
            self.indicacion.email_estado,
            IndicacionPaciente.EstadoEmail.ENVIADO,
        )

    def test_fallo_fuera_de_transaccion_se_persiste_como_error(self):
        def fallar_fuera_de_transaccion(*args, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            raise RuntimeError("detalle sensible simulado")

        with patch(
            "indicaciones.emails.EmailMessage.send",
            side_effect=fallar_fuera_de_transaccion,
        ):
            resultado = enviar_indicacion_por_email(
                indicacion_id=self.indicacion.pk,
                usuario=self.usuario,
                usar_email_actual=True,
            )

        self.indicacion.refresh_from_db()
        self.assertFalse(resultado)
        self.assertEqual(
            self.indicacion.email_estado,
            IndicacionPaciente.EstadoEmail.ERROR,
        )
        self.assertEqual(
            self.indicacion.ultimo_error_email,
            "No se pudo entregar el email al proveedor configurado.",
        )
