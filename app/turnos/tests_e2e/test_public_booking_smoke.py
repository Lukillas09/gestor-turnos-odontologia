import os
from datetime import time, timedelta

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import sync_playwright

from consultorio.services import obtener_configuracion_consultorio
from turnos.models import DisponibilidadOdontologo, Odontologo, SolicitudTurnoPublica, Turno


def _fecha_laboral_futura():
    fecha = timezone.localdate() + timedelta(days=1)
    while fecha.weekday() >= 5:
        fecha += timedelta(days=1)
    return fecha


def _crear_disponibilidad(odontologo):
    for dia_semana in range(5):
        DisponibilidadOdontologo.objects.create(
            odontologo=odontologo,
            dia_semana=dia_semana,
            hora_inicio=time(9, 0),
            hora_fin=time(18, 0),
        )


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    MEDIA_STORAGE_BACKEND="django.core.files.storage.FileSystemStorage",
    TURNOS_PUBLIC_REDIS_REQUIRED=False,
    TURNSTILE_ENABLED=False,
)
class PublicBookingSmokeE2ETests(StaticLiveServerTestCase):
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
        self.context = self.browser.new_context()
        self.page = self.context.new_page()

        obtener_configuracion_consultorio()
        User = get_user_model()
        usuario = User.objects.create_user(
            username="dra.publica.e2e",
            first_name="Dra",
            last_name="Publica",
        )
        self.odontologo = Odontologo.objects.create(
            usuario=usuario,
            matricula="E2E-PUB",
            especialidad="Odontologia general",
        )
        _crear_disponibilidad(self.odontologo)
        self.fecha = _fecha_laboral_futura()

    def tearDown(self):
        self.context.close()

    def test_flujo_publico_crea_un_turno_pendiente(self):
        self.page.goto(f"{self.live_server_url}{reverse('landing_publica')}")
        self.page.get_by_text("Solicitar turno").first.wait_for()
        self.page.get_by_role("link", name="Solicitar turno").first.click()

        self.page.select_option("select[name='odontologo']", str(self.odontologo.pk))
        self.page.fill("input[name='fecha']", self.fecha.isoformat())
        self.page.dispatch_event("input[name='fecha']", "change")
        self.page.locator(".public-slot-button").first.wait_for()

        fecha_chip = self.fecha + timedelta(days=1)
        while fecha_chip.weekday() >= 5:
            fecha_chip += timedelta(days=1)

        self.page.locator(f"[data-public-date='{fecha_chip.isoformat()}']").wait_for()
        self.page.locator(f"[data-public-date='{fecha_chip.isoformat()}']").click()
        self.assertEqual(self.page.input_value("input[name='fecha']"), fecha_chip.isoformat())
        self.page.locator(".public-slot-button").first.wait_for()
        self.page.locator(".public-slot-button").first.click()

        self.page.get_by_role("heading", name="Completar datos").wait_for()
        self.page.fill("input[name='nombre']", "Lucia")
        self.page.fill("input[name='apellido']", "Publica")
        self.page.fill("input[name='telefono']", "1122334455")
        self.page.fill("input[name='documento']", "44111222")
        self.page.fill("input[name='email']", "lucia.publica@example.com")
        self.page.fill("input[name='motivo']", "Control E2E")
        self.page.get_by_role("button", name="Enviar solicitud").click()

        self.page.wait_for_url(f"**{reverse('landing_publica')}")
        self.page.get_by_text("Tu solicitud fue registrada").wait_for()

        self.assertEqual(Turno.objects.filter(motivo="Control E2E").count(), 1)
        turno = Turno.objects.get(motivo="Control E2E")
        self.assertEqual(turno.estado, Turno.Estado.PENDIENTE)
        self.assertEqual(SolicitudTurnoPublica.objects.filter(turno=turno).count(), 1)

    def test_landing_publica_mobile_sin_scroll_horizontal(self):
        self.context.close()
        self.context = self.browser.new_context(viewport={"width": 390, "height": 844})
        self.page = self.context.new_page()

        self.page.goto(f"{self.live_server_url}{reverse('landing_publica')}")
        self.page.get_by_role("link", name="Solicitar turno").first.wait_for()

        scroll_width = self.page.evaluate("document.documentElement.scrollWidth")
        client_width = self.page.evaluate("document.documentElement.clientWidth")
        self.assertLessEqual(scroll_width, client_width + 1)
