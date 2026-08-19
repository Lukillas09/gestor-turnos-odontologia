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

from consultorio.services import obtener_o_crear_configuracion_consultorio
from turnos.models import (
    ConfiguracionAgendaInteligente,
    DisponibilidadOdontologo,
    Odontologo,
    SolicitudTurnoPublica,
    TipoTurno,
    TipoTurnoOdontologo,
    Turno,
)

SCREENSHOT_DIR = Path(
    os.environ.get(
        "UI_SCREENSHOT_DIR",
        Path(__file__).resolve().parents[3] / "docs" / "screenshots",
    )
)
RESPONSIVE_VIEWPORTS = (
    (320, 568),
    (360, 800),
    (390, 844),
    (393, 852),
    (430, 932),
    (768, 1024),
    (1440, 900),
)


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
        self.page.set_default_timeout(10_000)
        self.page.set_default_navigation_timeout(15_000)

        configuracion = obtener_o_crear_configuracion_consultorio()
        configuracion.ventana_reserva_publica_dias = 60
        configuracion.save(update_fields=["ventana_reserva_publica_dias"])
        User = get_user_model()
        usuario = User.objects.create_user(
            username="dra.publica.e2e",
            password="clave-segura-e2e",
            first_name="Dra",
            last_name="Publica",
        )
        self.usuario = usuario
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
        if os.environ.get("CAPTURE_UI_FULL_PAGE") == "1":
            self.page.evaluate("window.scrollTo(0, 0)")
        self.page.wait_for_timeout(450)
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(
            path=SCREENSHOT_DIR / name,
            full_page=os.environ.get("CAPTURE_UI_FULL_PAGE") == "1",
        )

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
                        && !element.closest('.public-native-date')
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

    def _assert_centered(self, selector):
        box = self.page.locator(selector).bounding_box()
        viewport = self.page.viewport_size
        self.assertIsNotNone(box)
        self.assertIsNotNone(viewport)
        margen_izquierdo = box["x"]
        margen_derecho = viewport["width"] - (box["x"] + box["width"])
        self.assertAlmostEqual(margen_izquierdo, margen_derecho, delta=2)
        self.assertGreaterEqual(margen_izquierdo, 15)

    def _login(self):
        self.page.goto(f"{self.live_server_url}{reverse('login')}")
        self.page.fill("input[name='username']", self.usuario.username)
        self.page.fill("input[name='password']", "clave-segura-e2e")
        self.page.get_by_role("button", name="Ingresar").click()
        self.page.wait_for_url(f"**{reverse('inicio')}")

    def test_flujo_publico_crea_un_turno_pendiente(self):
        self.page.goto(f"{self.live_server_url}{reverse('landing_publica')}")
        self.page.get_by_role("link", name="Reservá tu turno").first.wait_for()
        mis_turnos = self.page.get_by_role("link", name="Ver mis turnos").first
        mis_turnos.wait_for()
        self.assertIn(reverse("turnos:acceso_publico_solicitar"), mis_turnos.get_attribute("href"))
        self._capture("public-home-desktop.png")
        self.page.get_by_role("link", name="Reservá tu turno").first.click()

        self.page.locator(".public-search-panel-v2.is-enhanced").wait_for()
        continuar = self.page.locator("[data-public-slot-continue]")
        self.assertTrue(continuar.is_disabled())
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
        horario = self.page.locator(".public-slot-button").first
        horario_texto = horario.inner_text().strip()
        horario.click()
        self.assertFalse(continuar.is_disabled())
        self.assertIn(horario_texto, self.page.locator("[data-public-slot-label]").inner_text())
        continuar.click()

        self.page.get_by_role("heading", name="Completá tus datos").wait_for()
        self.page.get_by_role("heading", name="Resumen del turno").wait_for()
        self._capture("public-data-desktop.png")

        self.page.get_by_role("button", name="Enviar solicitud").click()
        self.page.locator(".field-error").first.wait_for()
        self.assertEqual(
            self.page.locator("input[name='nombre']").get_attribute("aria-invalid"),
            "true",
        )
        self.page.fill("input[name='nombre']", "Lucia")
        self.page.fill("input[name='apellido']", "Publica")
        self.page.fill("input[name='telefono']", "1122334455")
        self.page.fill("input[name='documento']", "44111222")
        self.page.fill("input[name='email']", "lucia.publica@example.com")
        self.page.fill("[name='motivo']", "Control E2E")

        guard_state = self.page.evaluate("""() => {
            const form = document.querySelector('[data-public-patient-form]');
            const button = form.querySelector('[data-public-submit]');
            const first = new Event('submit', {bubbles: true, cancelable: true});
            const second = new Event('submit', {bubbles: true, cancelable: true});
            form.dispatchEvent(first);
            const secondAccepted = form.dispatchEvent(second);
            const state = {
                submitting: form.dataset.submitting,
                disabled: button.disabled,
                secondPrevented: !secondAccepted,
            };
            delete form.dataset.submitting;
            button.disabled = false;
            button.removeAttribute('aria-disabled');
            button.removeAttribute('aria-busy');
            button.classList.remove('button-loading');
            return state;
        }""")
        self.assertEqual(guard_state["submitting"], "true")
        self.assertTrue(guard_state["disabled"])
        self.assertTrue(guard_state["secondPrevented"])
        self.page.get_by_role("button", name="Enviar solicitud").click()

        self.page.wait_for_url(f"**{reverse('landing_publica')}")
        self.page.get_by_text("Tu solicitud fue registrada").wait_for()

        self.assertEqual(Turno.objects.filter(motivo="Control E2E").count(), 1)
        turno = Turno.objects.get(motivo="Control E2E")
        self.assertEqual(turno.estado, Turno.Estado.PENDIENTE)
        self.assertEqual(SolicitudTurnoPublica.objects.filter(turno=turno).count(), 1)

    @override_settings(TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED=True)
    def test_flujo_publico_por_motivo_muestra_recomendados_y_alternativas(self):
        control = TipoTurno.objects.create(
            nombre="Control",
            slug="control-e2e",
            descripcion_publica="Revisión periódica.",
            icono=TipoTurno.Icono.CONTROL,
            orden_publico=10,
            visible_publicamente=True,
        )
        limpieza = TipoTurno.objects.create(
            nombre="Limpieza",
            slug="limpieza-e2e",
            descripcion_publica="Higiene profesional.",
            icono=TipoTurno.Icono.CLINICO,
            orden_publico=20,
            visible_publicamente=True,
        )
        TipoTurnoOdontologo.objects.create(
            odontologo=self.odontologo,
            tipo_turno=control,
            duracion_atencion_minutos=20,
            reserva_publica=True,
        )
        TipoTurnoOdontologo.objects.create(
            odontologo=self.odontologo,
            tipo_turno=limpieza,
            duracion_atencion_minutos=45,
            margen_posterior_minutos=15,
            reserva_publica=True,
        )
        ConfiguracionAgendaInteligente.objects.create(odontologo=self.odontologo)

        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.goto(f"{self.live_server_url}{reverse('landing_publica')}")
        self.page.get_by_role("link", name="Reservá tu turno").first.click()
        self.page.locator(".public-search-panel-v2.is-enhanced").wait_for()
        self.page.locator(f"[data-public-professional='{self.odontologo.pk}']").click()

        opcion_limpieza = self.page.locator(f"[data-public-service='{limpieza.pk}']")
        opcion_limpieza.wait_for()
        opcion_limpieza.focus()
        self.page.keyboard.press("Enter")
        self.assertEqual(opcion_limpieza.get_attribute("aria-checked"), "true")
        self.page.get_by_text("Aproximadamente 45 min").first.wait_for()

        calendario_mes = self.page.locator("[data-public-calendar-month]")
        mes_inicial = calendario_mes.inner_text()
        self.page.locator("[data-public-calendar-next]").click()
        self.assertNotEqual(calendario_mes.inner_text(), mes_inicial)
        self.page.locator("[data-public-calendar-prev]").click()
        self.assertEqual(calendario_mes.inner_text(), mes_inicial)

        self.page.fill("input[name='fecha']", self.fecha.isoformat())
        self.page.dispatch_event("input[name='fecha']", "change")
        self.page.locator(
            f"[data-public-date='{self.fecha.isoformat()}'][aria-current='date']"
        ).wait_for()
        self.page.get_by_text("Horarios recomendados", exact=True).wait_for()
        self.page.wait_for_load_state("networkidle")
        horarios_limpieza = self.page.locator("[data-public-slot]").all_inner_texts()
        self.assertGreaterEqual(len(horarios_limpieza), 3)
        self._capture("public-booking-smart-mobile.png")

        mas_horarios = self.page.locator("[data-public-more-slots]")
        mas_horarios.locator("summary").focus()
        self.page.keyboard.press("Enter")
        self.page.wait_for_function("""() => {
                const details = document.querySelector('[data-public-more-slots]');
                return details.open
                    && details.querySelector('summary').getAttribute('aria-expanded') === 'true';
            }""")
        self.assertEqual(mas_horarios.locator("summary").get_attribute("aria-expanded"), "true")

        alternativa = mas_horarios.locator("[data-public-slot]").first
        alternativa.wait_for()
        alternativa.click()
        self.page.locator("[data-public-slot-continue]").click()

        self.page.get_by_role("heading", name="Completá tus datos").wait_for()
        self.page.get_by_text("Limpieza", exact=True).first.wait_for()
        self.page.get_by_text("45 minutos", exact=True).wait_for()
        self.page.fill("input[name='nombre']", "Lucia")
        self.page.fill("input[name='apellido']", "Agenda")
        self.page.fill("input[name='telefono']", "1122334455")
        self.page.fill("input[name='documento']", "45111222")
        self.page.fill("input[name='email']", "lucia.agenda@example.com")
        self.page.fill("[name='motivo']", "Comentario E2E")
        self._assert_no_horizontal_overflow()
        self._capture("public-data-mobile.png")
        self.page.get_by_role("button", name="Enviar solicitud").click()

        self.page.wait_for_url(f"**{reverse('landing_publica')}")
        self.page.get_by_text("Tu solicitud fue registrada").wait_for()
        turno = Turno.objects.get(paciente__documento="45111222")
        self.assertEqual(turno.tipo_turno, limpieza)
        self.assertEqual(turno.tipo_turno_nombre_snapshot, "Limpieza")
        self.assertEqual(turno.duracion_atencion_minutos, 45)
        self.assertEqual(turno.duracion_minutos, 60)
        self.assertEqual(turno.clasificacion_horario, Turno.ClasificacionHorario.ALTERNATIVO)

        self.page.set_viewport_size({"width": 1440, "height": 900})
        self.page.goto(f"{self.live_server_url}{reverse('turnos:solicitud_publica')}")
        self.page.locator(f"[data-public-professional='{self.odontologo.pk}']").click()
        opcion_control = self.page.locator(f"[data-public-service='{control.pk}']")
        opcion_control.wait_for()
        opcion_control.click()
        self.page.get_by_text("Aproximadamente 20 min").first.wait_for()
        self.page.fill("input[name='fecha']", self.fecha.isoformat())
        self.page.dispatch_event("input[name='fecha']", "change")
        self.page.locator(
            f"[data-public-date='{self.fecha.isoformat()}'][aria-current='date']"
        ).wait_for()
        self.page.get_by_text("Horarios recomendados", exact=True).wait_for()
        self.page.wait_for_load_state("networkidle")
        horarios_control = self.page.locator("[data-public-slot]").all_inner_texts()
        self.assertNotEqual(horarios_control, horarios_limpieza)
        self._assert_no_horizontal_overflow()
        self._capture("public-booking-smart-desktop.png")

    def test_landing_publica_mobile_sin_scroll_horizontal(self):
        for width, height in RESPONSIVE_VIEWPORTS:
            with self.subTest(viewport=f"{width}x{height}"):
                self.page.set_viewport_size({"width": width, "height": height})
                self.page.goto(f"{self.live_server_url}{reverse('landing_publica')}")
                self.page.get_by_role("link", name="Reservá tu turno").first.wait_for()
                self.page.wait_for_timeout(150)
                self._assert_centered(".public-premium-hero")
                self._assert_no_horizontal_overflow()

        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.goto(f"{self.live_server_url}{reverse('landing_publica')}")
        self.page.get_by_role("link", name="Reservá tu turno").first.wait_for()
        self._capture("public-home-mobile.png")

        menu = self.page.locator(".public-mobile-menu")
        menu.locator("summary").click()
        self.page.wait_for_function("""() => {
            const menu = document.querySelector('.public-mobile-menu');
            return menu.open
                && menu.querySelector('summary').getAttribute('aria-expanded') === 'true';
        }""")
        self.assertEqual(menu.locator("summary").get_attribute("aria-expanded"), "true")
        self.page.keyboard.press("Escape")
        self.page.wait_for_function("""() => {
            const menu = document.querySelector('.public-mobile-menu');
            return !menu.open
                && menu.querySelector('summary').getAttribute('aria-expanded') === 'false';
        }""")
        self.assertEqual(menu.locator("summary").get_attribute("aria-expanded"), "false")

        self.page.get_by_role("link", name="Reservá tu turno").first.click()
        self.page.locator(".public-search-panel-v2.is-enhanced").wait_for()
        self.page.locator(f"[data-public-professional='{self.odontologo.pk}']").click()
        self.page.fill("input[name='fecha']", self.fecha.isoformat())
        self.page.dispatch_event("input[name='fecha']", "change")
        self.page.locator(".public-slot-button").first.wait_for()
        self.page.locator(".public-booking-results").scroll_into_view_if_needed()
        self._capture("public-booking-mobile.png")
        self._assert_no_horizontal_overflow()

        self.page.set_viewport_size({"width": 768, "height": 1024})
        self.page.wait_for_timeout(150)
        self._capture("public-booking-tablet.png")
        self._assert_no_horizontal_overflow()

    def test_landing_autenticada_conserva_shell_publico(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        self._login()

        self.page.goto(f"{self.live_server_url}{reverse('landing_publica')}")
        self.page.locator(".public-premium-hero").wait_for()

        self.assertEqual(self.page.locator("body.public-shell-body").count(), 1)
        self.assertEqual(self.page.locator(".public-topbar").count(), 1)
        self.assertEqual(self.page.locator(".app-sidebar").count(), 0)
        self.assertEqual(self.page.locator(".app-topbar").count(), 0)
        self.assertEqual(self.page.locator(".mobile-navigation").count(), 0)
        self._assert_centered(".public-premium-hero")
        self._assert_no_horizontal_overflow()
        self._capture("public-home-authenticated-mobile.png")

        self.page.goto(f"{self.live_server_url}{reverse('inicio')}")
        self.page.locator(".app-page").wait_for()

        self.assertEqual(self.page.locator("body.app-shell-body").count(), 1)
        self.page.locator(".mobile-navigation").wait_for()
        self._assert_centered(".app-page")
        self._assert_no_horizontal_overflow()
        self._capture("internal-home-after-public-mobile.png")
