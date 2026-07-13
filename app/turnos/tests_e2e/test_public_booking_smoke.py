import os
from datetime import time, timedelta
from pathlib import Path

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import sync_playwright

from consultorio.services import obtener_configuracion_consultorio
from turnos.models import DisponibilidadOdontologo, Odontologo, SolicitudTurnoPublica, Turno

SCREENSHOT_DIR = Path(__file__).resolve().parents[3] / "docs" / "screenshots"


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
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
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
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 900})
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

    def _capture(self, name):
        if os.environ.get("CAPTURE_UI_SCREENSHOTS") != "1":
            return
        self.page.wait_for_timeout(450)
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=SCREENSHOT_DIR / name, full_page=False)

    def _assert_no_horizontal_overflow(self):
        scroll_width = self.page.evaluate("document.documentElement.scrollWidth")
        client_width = self.page.evaluate("document.documentElement.clientWidth")
        overflowing = self.page.evaluate("""() => Array.from(document.querySelectorAll('*'))
                .map((element) => {
                    const rect = element.getBoundingClientRect();
                    return {
                        tag: element.tagName,
                        className: element.className,
                        left: rect.left,
                        right: rect.right,
                        width: rect.width,
                    };
                })
                .filter(
                    (item) => item.left < -1
                        || item.right > document.documentElement.clientWidth + 1
                )
                .slice(0, 12)""")
        self.assertLessEqual(scroll_width, client_width + 1, overflowing)

        visible_overflow = self.page.evaluate(
            """() => Array.from(document.querySelectorAll('main *'))
                .filter((element) => {
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden'
                        && style.position !== 'absolute'
                        && !element.closest('.public-date-strip')
                        && rect.width > 4
                        && (
                            rect.left < -1
                            || rect.right > document.documentElement.clientWidth + 1
                        );
                })
                .map((element) => ({
                    tag: element.tagName,
                    className: element.className,
                    rect: element.getBoundingClientRect().toJSON(),
                }))
                .slice(0, 12)"""
        )
        self.assertEqual(visible_overflow, [])

    def test_flujo_publico_crea_un_turno_pendiente(self):
        self.page.goto(f"{self.live_server_url}{reverse('landing_publica')}")
        self.page.get_by_text("Solicitar turno").first.wait_for()
        self._capture("public-home-desktop.png")
        self.page.get_by_role("link", name="Solicitar turno").first.click()

        self.page.locator(".public-search-panel-v2.is-enhanced").wait_for()
        self.page.locator(f"[data-public-professional='{self.odontologo.pk}']").click()
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
        self._capture("public-booking-desktop.png")
        self.page.locator(".public-slot-button").first.click()
        self.page.locator("[data-public-slot-continue]").click()

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
        self.page.wait_for_timeout(400)
        self._capture("public-home-mobile.png")
        self._assert_no_horizontal_overflow()

        self.page.get_by_role("link", name="Solicitar turno").first.click()
        self.page.locator(".public-search-panel-v2.is-enhanced").wait_for()
        self.page.locator(f"[data-public-professional='{self.odontologo.pk}']").click()
        self.page.fill("input[name='fecha']", self.fecha.isoformat())
        self.page.dispatch_event("input[name='fecha']", "change")
        self.page.locator(".public-slot-button").first.wait_for()
        self.page.locator(".public-booking-results").scroll_into_view_if_needed()
        self._capture("public-booking-mobile.png")
        self._assert_no_horizontal_overflow()
