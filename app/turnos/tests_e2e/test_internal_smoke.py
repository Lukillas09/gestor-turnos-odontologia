import os
from datetime import time, timedelta
from pathlib import Path

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import sync_playwright

from pacientes.models import Paciente, PacienteOdontologo
from turnos.models import DisponibilidadOdontologo, Odontologo, Turno
from usuarios.roles import ROL_ODONTOLOGO, ROL_RECEPCIONISTA

SCREENSHOT_DIR = Path(__file__).resolve().parents[3] / "docs" / "screenshots"


def _fecha_laboral_futura():
    fecha = timezone.localdate() + timedelta(days=1)
    while fecha.weekday() >= 5:
        fecha += timedelta(days=1)
    return fecha


def _crear_disponibilidad(odontologo):
    dias_semana = {*range(5), timezone.localdate().weekday()}
    for dia_semana in sorted(dias_semana):
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
    CLINICAL_INTEGRITY_HMAC_KEY="clave-clinica-e2e-independiente",
)
class InternalSmokeE2ETests(StaticLiveServerTestCase):
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

        User = get_user_model()
        self.usuario = User.objects.create_user(
            username="recepcion.e2e",
            password="clave-segura-e2e",
            first_name="Recepcion",
            last_name="E2E",
        )
        grupo, _ = Group.objects.get_or_create(name=ROL_RECEPCIONISTA)
        self.usuario.groups.add(grupo)

        self.usuario_odontologo = User.objects.create_user(
            username="dra.e2e",
            password="clave-clinica-e2e",
            first_name="Dra",
            last_name="E2E",
        )
        grupo_odontologo, _ = Group.objects.get_or_create(name=ROL_ODONTOLOGO)
        self.usuario_odontologo.groups.add(grupo_odontologo)
        self.odontologo = Odontologo.objects.create(
            usuario=self.usuario_odontologo,
            matricula="E2E-INT",
            especialidad="Odontologia general",
        )
        _crear_disponibilidad(self.odontologo)
        self.paciente = Paciente.objects.create(
            nombre="Paciente",
            apellido="Interno",
            documento="55111222",
            telefono="1133334444",
            email="interno@example.com",
        )
        PacienteOdontologo.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            asignado_por=self.usuario_odontologo,
            motivo="Atención clínica E2E",
        )
        self.turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=_fecha_laboral_futura(),
            hora_inicio=time(9, 0),
            duracion_minutos=30,
            motivo="Control E2E",
        )
        self.turno_hoy = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=timezone.localdate(),
            hora_inicio=time(14, 0),
            duracion_minutos=30,
            motivo="Revisión preventiva",
        )

    def tearDown(self):
        self.context.close()

    def _login(self):
        self.page.goto(f"{self.live_server_url}{reverse('login')}")
        self.page.fill("input[name='username']", "recepcion.e2e")
        self.page.fill("input[name='password']", "clave-segura-e2e")
        self.page.get_by_role("button", name="Ingresar").click()
        self.page.wait_for_url(f"**{reverse('inicio')}")

    def _login_odontologo(self):
        self.page.goto(f"{self.live_server_url}{reverse('login')}")
        self.page.fill("input[name='username']", "dra.e2e")
        self.page.fill("input[name='password']", "clave-clinica-e2e")
        self.page.get_by_role("button", name="Ingresar").click()
        self.page.wait_for_url(f"**{reverse('inicio')}")

    def _capture(self, name):
        if os.environ.get("CAPTURE_UI_SCREENSHOTS") != "1":
            return
        self.page.wait_for_timeout(450)
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=SCREENSHOT_DIR / name, full_page=False)

    def _assert_no_horizontal_overflow(self):
        scroll_width = self.page.evaluate("document.documentElement.scrollWidth")
        client_width = self.page.evaluate("document.documentElement.clientWidth")
        self.assertLessEqual(scroll_width, client_width + 1)

    def _assert_turno_actions_menu(self):
        self.page.goto(f"{self.live_server_url}{reverse('turnos:lista')}")
        self.page.get_by_role("heading", name="Agenda de turnos").wait_for()
        cards = self.page.locator("[data-turno-card]")
        self.assertGreaterEqual(cards.count(), 2)
        first_card = cards.nth(0)
        second_card = cards.nth(1)
        first_menu = first_card.locator("[data-row-actions-menu]")
        second_menu = second_card.locator("[data-row-actions-menu]")
        first_trigger = first_menu.locator(":scope > summary")
        second_trigger = second_menu.locator(":scope > summary")
        first_panel = first_menu.locator(":scope > [role='menu']")

        first_card.evaluate(
            "card => window.scrollTo(0, window.scrollY + card.getBoundingClientRect().top - 180)"
        )
        first_trigger.click()
        self.page.wait_for_function(
            "document.querySelector('[data-row-actions-menu] > summary')"
            ".getAttribute('aria-expanded') === 'true'"
        )

        self.assertEqual(first_trigger.get_attribute("aria-haspopup"), "menu")
        self.assertEqual(first_trigger.get_attribute("aria-expanded"), "true")
        self.assertIn("is-actions-menu-open", first_card.get_attribute("class"))
        first_panel.wait_for(state="visible")
        first_panel.get_by_role("menuitem", name="Llamar").click(trial=True)
        self.assertEqual(
            first_panel.get_by_role("menuitem", name="Llamar").get_attribute("href"),
            f"tel:{self.paciente.telefono}",
        )
        self.assertEqual(
            first_panel.get_by_role("menuitem", name="Enviar email").get_attribute("href"),
            f"mailto:{self.paciente.email}",
        )
        self.assertEqual(
            first_panel.get_by_role("menuitem", name="Ver paciente").get_attribute("href"),
            reverse("pacientes:detalle", args=[self.paciente.pk]),
        )

        menu_box = first_panel.bounding_box()
        next_card_box = second_card.bounding_box()
        self.assertIsNotNone(menu_box)
        self.assertIsNotNone(next_card_box)
        viewport = self.page.viewport_size
        self.assertGreaterEqual(menu_box["x"], 0)
        self.assertGreaterEqual(menu_box["y"], 0)
        self.assertLessEqual(menu_box["x"] + menu_box["width"], viewport["width"] + 1)
        self.assertLessEqual(menu_box["y"] + menu_box["height"], viewport["height"] + 1)

        overlap_top = max(menu_box["y"], next_card_box["y"])
        overlap_bottom = min(
            menu_box["y"] + menu_box["height"],
            next_card_box["y"] + next_card_box["height"],
        )
        self.assertGreater(overlap_bottom, overlap_top)
        test_point = {
            "x": menu_box["x"] + min(12, menu_box["width"] / 2),
            "y": overlap_top + min(8, (overlap_bottom - overlap_top) / 2),
        }
        menu_is_topmost = self.page.evaluate(
            "point => Boolean(document.elementFromPoint(point.x, point.y)?.closest('[role=menu]'))",
            test_point,
        )
        self.assertTrue(menu_is_topmost)
        self._assert_no_horizontal_overflow()

        second_trigger.focus()
        self.page.keyboard.press("Enter")
        self.page.wait_for_function(
            "document.querySelectorAll('[data-row-actions-menu]')[1]"
            ".querySelector('summary').getAttribute('aria-expanded') === 'true'"
        )
        self.assertEqual(first_trigger.get_attribute("aria-expanded"), "false")
        self.assertEqual(second_trigger.get_attribute("aria-expanded"), "true")

        self.page.keyboard.press("Escape")
        self.assertEqual(second_trigger.get_attribute("aria-expanded"), "false")
        self.assertTrue(second_trigger.evaluate("trigger => document.activeElement === trigger"))

        first_trigger.focus()
        self.page.keyboard.press("Enter")
        self.page.wait_for_function(
            "document.querySelector('[data-row-actions-menu] > summary')"
            ".getAttribute('aria-expanded') === 'true'"
        )
        self.assertEqual(first_trigger.get_attribute("aria-expanded"), "true")
        self.page.locator(".turnos-summary-strip").click()
        self.page.wait_for_function(
            "document.querySelector('[data-row-actions-menu] > summary')"
            ".getAttribute('aria-expanded') === 'false'"
        )
        self.assertEqual(first_trigger.get_attribute("aria-expanded"), "false")

    def _assert_clinical_history_flow(self):
        self._login_odontologo()
        self.page.goto(
            f"{self.live_server_url}"
            f"{reverse('historias:crear', kwargs={'paciente_pk': self.paciente.pk})}"
        )
        self.page.get_by_role(
            "heading", name="Nueva entrada de historia clínica", exact=True
        ).wait_for()
        self.page.get_by_text("Estado: Borrador", exact=True).wait_for()
        self.page.get_by_label("Fecha y hora de atención").fill(
            timezone.localtime(timezone.now() - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M")
        )
        self.page.get_by_label("Pieza dental").fill("16")
        self.page.get_by_label("Motivo de consulta").fill("Control clínico E2E")
        self.page.get_by_label("Diagnóstico").fill("Diagnóstico inicial E2E")
        self.page.get_by_label("Tratamiento realizado").fill("Evaluación preventiva")
        self.page.get_by_label("Observaciones").fill("Registro de navegador")
        self.page.get_by_role("button", name="Guardar borrador").click()

        self.page.get_by_role("heading", name="Entrada de historia clínica").wait_for()
        self.page.get_by_text("Borrador editable", exact=True).wait_for()
        self.page.get_by_text("Versión 1", exact=True).wait_for()
        self._assert_no_horizontal_overflow()

        self.page.get_by_role("link", name="Editar borrador").click()
        self.page.get_by_role("heading", name="Editar borrador clínico").wait_for()
        self.page.get_by_label("Diagnóstico").fill("Diagnóstico actualizado E2E")
        self.page.get_by_role("textbox", name="Motivo de la modificación").fill(
            "Se precisó el diagnóstico durante la prueba de navegador."
        )
        self.page.get_by_role("button", name="Guardar nueva versión").click()
        self.page.get_by_text("Versión 2", exact=True).wait_for()
        self.page.get_by_role("link", name="Ver versión").last.click()
        self.page.get_by_role("heading", name="Versión 2", exact=True).wait_for()
        self.page.get_by_text("Diagnóstico actualizado E2E", exact=True).wait_for()
        self.page.get_by_role("link", name="Volver al asiento").click()

        boton_finalizar = self.page.get_by_role("button", name="Finalizar", exact=True)
        boton_finalizar.focus()
        self.assertTrue(boton_finalizar.evaluate("elemento => document.activeElement === elemento"))
        self.page.keyboard.press("Enter")
        dialogo = self.page.locator("dialog[data-finalize-dialog]")
        dialogo.wait_for(state="visible")
        dialogo.get_by_role("heading", name="Finalizar entrada clínica").wait_for()
        dialogo.get_by_label("Confirmo que revisé el contenido y deseo bloquearlo.").check()
        dialogo.get_by_role("button", name="Finalizar y bloquear").click()

        self.page.get_by_text("Registro finalizado e inmutable", exact=True).wait_for()
        self.assertEqual(self.page.get_by_role("link", name="Editar borrador").count(), 0)
        self.assertEqual(self.page.get_by_role("button", name="Finalizar").count(), 0)
        self.page.get_by_role("link", name="Agregar enmienda").click()
        self.page.get_by_role("heading", name="Agregar enmienda").wait_for()
        self.page.get_by_label("Texto de la enmienda").fill(
            "Se agrega una aclaración posterior sin alterar el asiento original."
        )
        self.page.get_by_label("Motivo").fill(
            "Se documenta una aclaración detectada durante la revisión clínica."
        )
        self.page.get_by_role("button", name="Registrar enmienda").click()
        self.page.get_by_text("Enmienda 1", exact=True).wait_for()
        self.page.get_by_role("link", name="Ver enmienda").click()
        self.page.get_by_role("heading", name="Enmienda 1", exact=True).wait_for()
        self.page.get_by_text("El asiento original permanece intacto", exact=True).wait_for()
        self.page.get_by_role("link", name="Volver al original").click()
        self._assert_no_horizontal_overflow()

        self.page.get_by_role("link", name="Exportar", exact=True).click()
        self.page.get_by_role("heading", name="Exportar historia clínica completa").wait_for()
        self.page.get_by_label("Motivo de la exportación").select_option("solicitud_paciente")
        with self.page.expect_download() as descarga_info:
            self.page.get_by_role("button", name="Generar exportación").click()
        descarga = descarga_info.value
        self.assertRegex(descarga.suggested_filename, r"^historia-clinica-paciente-.*\.zip$")
        self._assert_no_horizontal_overflow()

    def test_login_listado_y_detalle_de_turno(self):
        self.page.goto(f"{self.live_server_url}{reverse('login')}")
        self.page.get_by_role("link", name="Volver a turnos online").wait_for()
        password = self.page.locator("input[name='password']")
        password.fill("clave-segura-e2e")
        toggle = self.page.locator("[data-password-toggle]")
        toggle.click()
        self.assertEqual(password.get_attribute("type"), "text")
        self.assertEqual(toggle.get_attribute("aria-pressed"), "true")
        self.assertEqual(toggle.get_attribute("aria-label"), "Ocultar contraseña")
        toggle.click()
        self.assertEqual(password.get_attribute("type"), "password")

        self._login()
        self.page.get_by_role("heading", name="Resumen de la agenda").wait_for()
        self.page.locator(".app-sidebar").wait_for()
        self.page.locator(".app-topbar").wait_for()
        self.page.locator(".internal-dashboard-footer").wait_for()
        self._assert_no_horizontal_overflow()
        self._capture("internal-dashboard-desktop.png")

        self.page.goto(f"{self.live_server_url}{reverse('turnos:agenda_dia')}")
        self.page.get_by_role("heading", name="Agenda diaria").wait_for()
        self.page.get_by_text("Revisión preventiva").wait_for()
        self._capture("agenda-desktop.png")

        self.page.goto(f"{self.live_server_url}{reverse('turnos:lista')}")
        self.page.get_by_role("heading", name="Agenda de turnos").wait_for()
        self.page.get_by_text("Control E2E").wait_for()

        self.page.get_by_role("link", name="Ver").first.click()
        self.page.get_by_role("heading", name="Turno de Interno, Paciente").wait_for()

        self.page.goto(
            f"{self.live_server_url}{reverse('pacientes:detalle', args=[self.paciente.pk])}"
        )
        self.page.get_by_role("heading", name="Paciente Interno").wait_for()
        self._capture("patient-profile-desktop.png")

    def test_navegacion_interna_mobile_no_tapa_contenido(self):
        self.context.close()
        self.context = self.browser.new_context(viewport={"width": 390, "height": 844})
        self.page = self.context.new_page()
        self._login()

        self.page.locator(".mobile-navigation").wait_for()
        self.assertEqual(
            self.page.get_by_role("link", name="Inicio").get_attribute("aria-current"),
            "page",
        )
        self._capture("internal-dashboard-mobile.png")
        self._assert_no_horizontal_overflow()

        for name, heading in (
            ("Agenda", "Agenda diaria"),
            ("Turnos", "Agenda de turnos"),
            ("Pacientes", "Directorio clínico"),
        ):
            self.page.get_by_role("link", name=name, exact=True).click()
            self.page.get_by_role("heading", name=heading, exact=True).wait_for()
            self._assert_no_horizontal_overflow()

        more = self.page.locator(".mobile-more > summary")
        more.click()
        self.page.wait_for_function(
            "document.querySelector('.mobile-more > summary')"
            ".getAttribute('aria-expanded') === 'true'"
        )
        self.assertEqual(more.get_attribute("aria-expanded"), "true")
        self.page.get_by_role("heading", name="Más opciones").wait_for()
        self.page.get_by_role("link", name="Mi perfil").wait_for()
        self.page.keyboard.press("Escape")
        self.page.wait_for_function(
            "document.querySelector('.mobile-more > summary')"
            ".getAttribute('aria-expanded') === 'false'"
        )
        self.assertEqual(more.get_attribute("aria-expanded"), "false")

    def test_menu_acciones_turno_permanece_sobre_las_tarjetas_en_desktop(self):
        self._login()
        self._assert_turno_actions_menu()

    def test_menu_acciones_turno_permanece_sobre_las_tarjetas_en_mobile(self):
        self.context.close()
        self.context = self.browser.new_context(viewport={"width": 390, "height": 844})
        self.page = self.context.new_page()
        self._login()
        self._assert_turno_actions_menu()

    def test_historia_clinica_inmutable_en_desktop(self):
        self._assert_clinical_history_flow()

    def test_historia_clinica_inmutable_en_mobile(self):
        self.context.close()
        self.context = self.browser.new_context(viewport={"width": 390, "height": 844})
        self.page = self.context.new_page()
        self._assert_clinical_history_flow()
