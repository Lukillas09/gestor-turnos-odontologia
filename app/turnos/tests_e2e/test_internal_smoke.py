import os
from datetime import time, timedelta

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import sync_playwright

from pacientes.models import Paciente
from turnos.models import DisponibilidadOdontologo, Odontologo, Turno
from usuarios.roles import ROL_RECEPCIONISTA


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
        self.context = self.browser.new_context()
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

        usuario_odontologo = User.objects.create_user(
            username="dra.e2e",
            first_name="Dra",
            last_name="E2E",
        )
        self.odontologo = Odontologo.objects.create(
            usuario=usuario_odontologo,
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
        self.turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=_fecha_laboral_futura(),
            hora_inicio=time(9, 0),
            duracion_minutos=30,
            motivo="Control E2E",
        )

    def tearDown(self):
        self.context.close()

    def test_login_listado_y_detalle_de_turno(self):
        self.page.goto(f"{self.live_server_url}{reverse('login')}")
        self.page.fill("input[name='username']", "recepcion.e2e")
        self.page.fill("input[name='password']", "clave-segura-e2e")
        self.page.get_by_role("button", name="Ingresar").click()

        self.page.wait_for_url(f"**{reverse('inicio')}")
        self.page.goto(f"{self.live_server_url}{reverse('turnos:lista')}")
        self.page.get_by_role("heading", name="Agenda de turnos").wait_for()
        self.page.get_by_text("Control E2E").wait_for()

        self.page.get_by_role("link", name="Ver").first.click()
        self.page.get_by_role("heading", name="Turno de Interno, Paciente").wait_for()
