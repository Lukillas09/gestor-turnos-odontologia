import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.storage import FileSystemStorage
from django.test import TestCase, override_settings
from django.utils import timezone

from indicaciones.models import IndicacionPaciente, PlantillaIndicacion
from indicaciones.services import crear_borrador_indicacion, emitir_indicacion
from pacientes.models import Paciente, PacienteOdontologo
from turnos.models import Odontologo
from usuarios.roles import ROL_ODONTOLOGO, ROL_RECEPCIONISTA


@override_settings(
    INDICACIONES_POSTOPERATORIAS_ENABLED=True,
    CLINICAL_INTEGRITY_ENABLED=True,
    CLINICAL_INTEGRITY_HMAC_KEY="clave-integridad-indicaciones-solo-pruebas",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="consultorio@example.test",
    INDICACIONES_PDF_MAX_BYTES=5 * 1024 * 1024,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class IndicacionesTestCase(TestCase):
    def setUp(self):
        super().setUp()
        self.directorio_privado = tempfile.TemporaryDirectory()
        self.campo_pdf = IndicacionPaciente._meta.get_field("pdf")
        self.storage_original = self.campo_pdf.storage
        self.campo_pdf.storage = FileSystemStorage(location=self.directorio_privado.name)
        self.addCleanup(self._restaurar_storage)

        User = get_user_model()
        grupo_odontologo, _ = Group.objects.get_or_create(name=ROL_ODONTOLOGO)
        grupo_recepcion, _ = Group.objects.get_or_create(name=ROL_RECEPCIONISTA)

        self.usuario = User.objects.create_user(
            username="odontologa.indicaciones",
            password="clave-segura-pruebas",
            first_name="Ana",
            last_name="Profesional",
        )
        self.usuario.groups.add(grupo_odontologo)
        self.odontologo = Odontologo.objects.create(
            usuario=self.usuario,
            matricula="IND-001",
            especialidad="Odontología general",
        )

        self.otro_usuario = User.objects.create_user(
            username="otro.odontologo",
            password="clave-segura-pruebas",
            first_name="Bruno",
            last_name="Profesional",
        )
        self.otro_usuario.groups.add(grupo_odontologo)
        self.otro_odontologo = Odontologo.objects.create(
            usuario=self.otro_usuario,
            matricula="IND-002",
            especialidad="Odontología general",
        )

        self.recepcionista = User.objects.create_user(
            username="recepcion.indicaciones",
            password="clave-segura-pruebas",
        )
        self.recepcionista.groups.add(grupo_recepcion)

        self.paciente = Paciente.objects.create(
            nombre="Paciente",
            apellido="Ficticio",
            documento="88000111",
            telefono="1100001111",
            email="paciente@example.test",
            email_verificado_en=timezone.now(),
            obra_social="Cobertura de prueba",
        )
        PacienteOdontologo.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            asignado_por=self.usuario,
            motivo="Relación clínica ficticia para pruebas",
        )
        self.paciente_fuera_de_alcance = Paciente.objects.create(
            nombre="Paciente",
            apellido="Fuera de alcance",
            documento="88000222",
            email="otro@example.test",
            email_verificado_en=timezone.now(),
        )
        PacienteOdontologo.objects.create(
            paciente=self.paciente_fuera_de_alcance,
            odontologo=self.otro_odontologo,
            asignado_por=self.otro_usuario,
            motivo="Relación clínica ficticia para pruebas",
        )

        self.plantilla = PlantillaIndicacion.objects.create(
            nombre="Plantilla ficticia",
            procedimiento="Procedimiento de prueba",
            titulo_documento="Indicaciones de prueba",
            contenido="Contenido clínico ficticio definido exclusivamente para pruebas.",
            pautas_alarma="Pauta ficticia revisada para pruebas.",
            recomendaciones_control="Control ficticio según indicación profesional.",
            creado_por=self.usuario,
            actualizado_por=self.usuario,
        )

    def _restaurar_storage(self):
        self.campo_pdf.storage = self.storage_original
        self.directorio_privado.cleanup()

    def datos_borrador(self, **cambios):
        datos = {
            "plantilla": self.plantilla,
            "historia_clinica": None,
            "turno": None,
            "titulo": "Indicaciones de prueba",
            "procedimiento": "Procedimiento de prueba",
            "contenido": "Contenido clínico ficticio definido por el profesional de prueba.",
            "pautas_alarma": "Pauta ficticia para validar el documento.",
            "recomendaciones_control": "Control ficticio indicado por el profesional.",
            "observaciones_personalizadas": "Observación ficticia individual.",
            "proximo_control_en": None,
        }
        datos.update(cambios)
        return datos

    def crear_borrador(self, **cambios):
        return crear_borrador_indicacion(
            paciente=self.paciente,
            usuario=self.usuario,
            datos=self.datos_borrador(**cambios),
        )

    def emitir(self, indicacion=None, *, ejecutar_callback=False):
        indicacion = indicacion or self.crear_borrador()
        with self.captureOnCommitCallbacks(execute=ejecutar_callback) as callbacks:
            emitida = emitir_indicacion(indicacion=indicacion, usuario=self.usuario)
        emitida.refresh_from_db()
        return emitida, callbacks
