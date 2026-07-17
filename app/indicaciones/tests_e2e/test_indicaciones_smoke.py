import os
import tempfile
from pathlib import Path

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core import mail
from django.core.files.storage import FileSystemStorage
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import sync_playwright

from indicaciones.models import IndicacionPaciente, PlantillaIndicacion
from pacientes.models import Paciente, PacienteOdontologo
from turnos.models import Odontologo
from usuarios.roles import ROL_ODONTOLOGO


@override_settings(
    INDICACIONES_POSTOPERATORIAS_ENABLED=True,
    CLINICAL_INTEGRITY_ENABLED=True,
    CLINICAL_INTEGRITY_HMAC_KEY="clave-integridad-indicaciones-e2e",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="consultorio@example.test",
    INDICACIONES_PDF_MAX_BYTES=5 * 1024 * 1024,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        "clinical_private": {"BACKEND": "config.storage_backends.PrivateClinicalFileSystemStorage"},
    },
)
class IndicacionesSmokeE2ETests(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        super().tearDownClass()

    def setUp(self):
        self.directorio_privado = tempfile.TemporaryDirectory()
        self.campo_pdf = IndicacionPaciente._meta.get_field("pdf")
        self.storage_original = self.campo_pdf.storage
        self.campo_pdf.storage = FileSystemStorage(location=self.directorio_privado.name)

        User = get_user_model()
        self.usuario = User.objects.create_user(
            username="odontologa.indicaciones.e2e",
            password="clave-e2e",
            first_name="Ana",
            last_name="E2E",
        )
        grupo, _ = Group.objects.get_or_create(name=ROL_ODONTOLOGO)
        self.usuario.groups.add(grupo)
        self.odontologo = Odontologo.objects.create(
            usuario=self.usuario,
            matricula="IND-E2E",
            especialidad="Odontología general",
        )
        self.paciente = Paciente.objects.create(
            nombre="Paciente",
            apellido="E2E",
            documento="88999111",
            email="paciente-e2e@example.test",
            email_verificado_en=timezone.now(),
        )
        PacienteOdontologo.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            asignado_por=self.usuario,
            motivo="Relación clínica ficticia E2E",
        )
        self.plantilla = PlantillaIndicacion.objects.create(
            nombre="Plantilla ficticia E2E",
            procedimiento="Procedimiento ficticio E2E",
            titulo_documento="Indicaciones ficticias E2E",
            contenido="Contenido clínico ficticio para la prueba E2E.",
            pautas_alarma="Pauta ficticia para la prueba E2E.",
            recomendaciones_control="Control ficticio para la prueba E2E.",
            creado_por=self.usuario,
            actualizado_por=self.usuario,
        )
        self.context = None
        self.page = None

    def tearDown(self):
        if self.context is not None:
            self.context.close()
        self.campo_pdf.storage = self.storage_original
        self.directorio_privado.cleanup()

    def _abrir_contexto(self, viewport):
        if self.context is not None:
            self.context.close()
        self.context = self.browser.new_context(viewport=viewport, accept_downloads=True)
        self.page = self.context.new_page()

    def _login(self):
        self.page.goto(f"{self.live_server_url}{reverse('login')}")
        self.page.fill("input[name='username']", self.usuario.username)
        self.page.fill("input[name='password']", "clave-e2e")
        self.page.get_by_role("button", name="Ingresar").click()
        self.page.wait_for_url(f"**{reverse('inicio')}")

    def _assert_no_horizontal_overflow(self):
        ancho_scroll = self.page.evaluate("document.documentElement.scrollWidth")
        ancho_cliente = self.page.evaluate("document.documentElement.clientWidth")
        self.assertLessEqual(ancho_scroll, ancho_cliente + 1)

    def _capturar_si_corresponde(self, nombre):
        directorio = os.environ.get("INDICACIONES_E2E_SCREENSHOT_DIR", "").strip()
        if not directorio:
            return
        ruta = Path(directorio)
        ruta.mkdir(parents=True, exist_ok=True)
        self.page.wait_for_timeout(700)
        self.page.screenshot(path=ruta / nombre, full_page=False)

    def _ejecutar_flujo(self, viewport):
        self._abrir_contexto(viewport)
        self._login()
        self.page.goto(
            f"{self.live_server_url}{reverse('pacientes:detalle', args=[self.paciente.pk])}"
        )
        self.page.get_by_role("heading", name="Paciente E2E", exact=True).wait_for()
        self.page.get_by_role("link", name="Nueva indicación", exact=True).first.click()

        self.page.get_by_role("heading", name="Nueva indicación", exact=True).wait_for()
        self.page.get_by_label("Plantilla").select_option(str(self.plantilla.pk))
        self.page.get_by_label("Título del documento").fill("Documento ficticio E2E personalizado")
        self.page.locator("#id_contenido").fill(
            "Contenido ficticio personalizado y revisado en el flujo E2E."
        )
        self.page.get_by_label("Observaciones personalizadas").fill(
            "Observación ficticia individual para la prueba E2E."
        )
        self._assert_no_horizontal_overflow()
        self.page.get_by_role("button", name="Guardar borrador").click()

        self.page.get_by_role(
            "heading", name="Documento ficticio E2E personalizado", exact=True, level=1
        ).wait_for()
        self.page.get_by_text("Borrador editable", exact=True).wait_for()
        self.page.get_by_role("link", name="Revisar y emitir").click()
        self.page.get_by_role("heading", name="Revisar indicación", exact=True).wait_for()
        self.page.get_by_label(
            "Confirmo que revisé el contenido y deseo emitir este documento."
        ).check()
        self._assert_no_horizontal_overflow()
        self.page.get_by_role("button", name="Emitir y enviar").click()

        self.page.get_by_text("Documento emitido e inmutable", exact=True).wait_for()
        self.assertEqual(self.page.get_by_role("link", name="Editar").count(), 0)
        self.assertEqual(len(mail.outbox), 1)
        self._capturar_si_corresponde(f"indicaciones-emitida-{viewport['width']}.png")
        with self.page.expect_download() as descarga_info:
            self.page.get_by_role("link", name="Descargar PDF").click()
        self.assertRegex(
            descarga_info.value.suggested_filename,
            r"^indicaciones-\d{4}-\d{2}-\d{2}\.pdf$",
        )

        indicacion = IndicacionPaciente.objects.get(titulo="Documento ficticio E2E personalizado")
        respuesta_edicion = self.page.goto(
            f"{self.live_server_url}"
            f"{reverse('indicaciones:editar', args=[self.paciente.pk, indicacion.uuid])}"
        )
        self.assertEqual(respuesta_edicion.status, 403)
        self.page.goto(
            f"{self.live_server_url}"
            f"{reverse('indicaciones:detalle', args=[self.paciente.pk, indicacion.uuid])}"
        )
        self.page.get_by_role("link", name="Anular").click()
        self.page.get_by_role("heading", name="Anular indicación", exact=True).wait_for()
        self.page.get_by_label("Motivo de anulación").fill(
            "Corrección ficticia requerida durante la validación E2E del documento."
        )
        self.page.get_by_role("button", name="Confirmar anulación").click()

        self.page.get_by_text("Documento anulado", exact=True).wait_for()
        self.page.get_by_role("link", name="Crear reemplazo").click()
        self.page.get_by_label(
            "Crear un nuevo borrador vinculado a este documento anulado."
        ).check()
        self.page.get_by_role("button", name="Crear borrador").click()
        self.page.get_by_text("Borrador editable", exact=True).wait_for()
        self._assert_no_horizontal_overflow()
        reemplazo = IndicacionPaciente.objects.get(reemplaza_a=indicacion)
        self.assertEqual(reemplazo.estado, IndicacionPaciente.Estado.BORRADOR)

    def test_flujo_completo_en_desktop(self):
        self._ejecutar_flujo({"width": 1440, "height": 900})

    def test_flujo_completo_en_mobile(self):
        self._ejecutar_flujo({"width": 390, "height": 844})

    def test_formulario_no_desborda_en_viewports_objetivo(self):
        for ancho, alto in (
            (320, 568),
            (360, 800),
            (390, 844),
            (430, 932),
            (768, 1024),
            (1440, 900),
        ):
            with self.subTest(viewport=f"{ancho}x{alto}"):
                self._abrir_contexto({"width": ancho, "height": alto})
                self._login()
                self.page.goto(
                    f"{self.live_server_url}"
                    f"{reverse('indicaciones:crear', args=[self.paciente.pk])}"
                )
                self.page.get_by_role("heading", name="Nueva indicación", exact=True).wait_for()
                self.page.get_by_role(
                    "button", name="Guardar borrador"
                ).scroll_into_view_if_needed()
                self._assert_no_horizontal_overflow()

                navegacion = self.page.locator(".mobile-navigation")
                if navegacion.is_visible():
                    boton = self.page.get_by_role("button", name="Guardar borrador").bounding_box()
                    caja_navegacion = navegacion.bounding_box()
                    self.assertIsNotNone(boton)
                    self.assertIsNotNone(caja_navegacion)
                    self.assertLessEqual(boton["y"] + boton["height"], caja_navegacion["y"] + 1)
