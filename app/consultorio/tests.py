from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from pacientes.models import Paciente
from turnos.models import DisponibilidadOdontologo, Odontologo, Turno
from turnos.notifications import notificar_turno_confirmado
from usuarios.roles import ROL_ODONTOLOGO, ROL_RECEPCIONISTA

from .forms import ConfiguracionConsultorioForm
from .models import (
    COLOR_PRINCIPAL_DEFAULT,
    CONFIGURACION_CONSULTORIO_PK,
    NOMBRE_COMERCIAL_DEFAULT,
    TEXTO_BIENVENIDA_DEFAULT,
    TITULO_PORTADA_DEFAULT,
    ConfiguracionConsultorio,
)
from .services import obtener_configuracion_consultorio, obtener_o_crear_configuracion_consultorio


TEST_STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.InMemoryStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}


PNG_BYTES = b"\x89PNG\r\n\x1a\nlogo"


def asignar_rol(usuario, nombre_rol):
    grupo, _ = Group.objects.get_or_create(name=nombre_rol)
    usuario.groups.add(grupo)


def datos_configuracion(**overrides):
    data = {
        "nombre_comercial": "Consultorio Norte",
        "nombre_corto": "Norte",
        "direccion": "Av. Siempre Viva 123",
        "localidad": "Rosario",
        "provincia": "Santa Fe",
        "telefono": "3415550000",
        "whatsapp": "+54 9 341 555 0000",
        "email": "turnos@norte.example",
        "horario_atencion": "Lunes a viernes de 9 a 18",
        "titulo_portada": "Turnos odontológicos simples",
        "texto_bienvenida": "Elegí tu horario y enviá la solicitud.",
        "politica_cancelacion": "Cancelá con 24 horas de anticipación.",
        "color_principal": "#0f766e",
        "mostrar_direccion": "on",
        "mostrar_telefono": "on",
        "mostrar_whatsapp": "on",
        "mostrar_email": "on",
        "mostrar_horario_atencion": "on",
        "ventana_reserva_publica_dias": "14",
        "permitir_reserva_publica_mismo_dia": "on",
        "anticipacion_minima_reserva_publica_minutos": "120",
    }
    data.update(overrides)
    return data


def archivo_logo(nombre="logo.png", contenido=PNG_BYTES, content_type="image/png"):
    return SimpleUploadedFile(nombre, contenido, content_type=content_type)


class ConfiguracionConsultorioModelTests(TestCase):
    def test_migracion_crea_configuracion_predeterminada(self):
        self.assertTrue(
            ConfiguracionConsultorio.objects.filter(pk=CONFIGURACION_CONSULTORIO_PK).exists()
        )

    def test_no_pueden_existir_dos_configuraciones(self):
        obtener_o_crear_configuracion_consultorio()

        with self.assertRaises(ValidationError):
            ConfiguracionConsultorio(
                pk=2,
                nombre_comercial="Otro consultorio",
            ).save()

    def test_modelo_no_puede_borrarse(self):
        configuracion = obtener_o_crear_configuracion_consultorio()

        with self.assertRaises(ProtectedError):
            configuracion.delete()

    def test_servicio_devuelve_defaults_si_falta_el_registro(self):
        ConfiguracionConsultorio.objects.filter(pk=CONFIGURACION_CONSULTORIO_PK).delete()

        configuracion = obtener_configuracion_consultorio()

        self.assertEqual(configuracion.pk, CONFIGURACION_CONSULTORIO_PK)
        self.assertEqual(configuracion.nombre_comercial, NOMBRE_COMERCIAL_DEFAULT)
        self.assertEqual(configuracion.titulo_portada, TITULO_PORTADA_DEFAULT)
        self.assertEqual(configuracion.texto_bienvenida, TEXTO_BIENVENIDA_DEFAULT)
        self.assertEqual(configuracion.color_principal, COLOR_PRINCIPAL_DEFAULT)
        self.assertFalse(ConfiguracionConsultorio.objects.filter(pk=CONFIGURACION_CONSULTORIO_PK).exists())

    def test_color_invalido_es_rechazado(self):
        form = ConfiguracionConsultorioForm(
            data=datos_configuracion(color_principal="red"),
            instance=obtener_o_crear_configuracion_consultorio(),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("color_principal", form.errors)

    def test_color_se_normaliza(self):
        form = ConfiguracionConsultorioForm(
            data=datos_configuracion(color_principal="#0f766e"),
            instance=obtener_o_crear_configuracion_consultorio(),
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["color_principal"], "#0F766E")

    def test_logo_demasiado_grande_es_rechazado(self):
        archivo = archivo_logo(contenido=PNG_BYTES + (b"x" * (2 * 1024 * 1024 + 1)))
        form = ConfiguracionConsultorioForm(
            data=datos_configuracion(),
            files={"logo": archivo},
            instance=obtener_o_crear_configuracion_consultorio(),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("logo", form.errors)

    def test_extension_no_permitida_es_rechazada(self):
        form = ConfiguracionConsultorioForm(
            data=datos_configuracion(),
            files={"logo": archivo_logo(nombre="logo.gif", contenido=b"GIF89a")},
            instance=obtener_o_crear_configuracion_consultorio(),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("logo", form.errors)

    def test_svg_es_rechazado(self):
        form = ConfiguracionConsultorioForm(
            data=datos_configuracion(),
            files={"logo": archivo_logo(nombre="logo.svg", contenido=b"<svg></svg>", content_type="image/svg+xml")},
            instance=obtener_o_crear_configuracion_consultorio(),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("logo", form.errors)


@override_settings(STORAGES=TEST_STORAGES)
class ConfiguracionConsultorioViewTests(TestCase):
    def setUp(self):
        self.url = reverse("consultorio:configuracion")

    def test_usuario_anonimo_es_redirigido_al_login(self):
        response = self.client.get(self.url)

        self.assertRedirects(response, f"{reverse('login')}?next={self.url}")

    def test_usuario_sin_permiso_recibe_403(self):
        usuario = get_user_model().objects.create_user(username="usuario.sin.permiso")
        self.client.force_login(usuario)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_odontologo_normal_no_puede_configurar_identidad(self):
        usuario = get_user_model().objects.create_user(username="dr.identidad")
        asignar_rol(usuario, ROL_ODONTOLOGO)
        Odontologo.objects.create(usuario=usuario, matricula="MN-ID")
        self.client.force_login(usuario)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_usuario_autorizado_puede_ver_formulario(self):
        usuario = self._login_recepcionista()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Perfil del consultorio")
        self.assertContains(response, "Guardar cambios")
        self.assertContains(response, "Restaurar predeterminados")

    def test_usuario_autorizado_puede_guardar_cambios_y_actualizado_por(self):
        usuario = self._login_recepcionista()

        response = self.client.post(self.url, datos_configuracion())

        self.assertRedirects(response, self.url)
        configuracion = ConfiguracionConsultorio.objects.get(pk=CONFIGURACION_CONSULTORIO_PK)
        self.assertEqual(configuracion.nombre_comercial, "Consultorio Norte")
        self.assertEqual(configuracion.nombre_visible, "Norte")
        self.assertEqual(configuracion.color_principal, "#0F766E")
        self.assertEqual(configuracion.actualizado_por, usuario)

    def test_se_puede_subir_y_quitar_logo(self):
        self._login_recepcionista()

        response_upload = self.client.post(
            self.url,
            {**datos_configuracion(), "logo": archivo_logo()},
        )

        self.assertRedirects(response_upload, self.url)
        configuracion = ConfiguracionConsultorio.objects.get(pk=CONFIGURACION_CONSULTORIO_PK)
        self.assertTrue(configuracion.logo)

        response_remove = self.client.post(
            self.url,
            datos_configuracion(quitar_logo="on"),
        )

        self.assertRedirects(response_remove, self.url)
        configuracion.refresh_from_db()
        self.assertFalse(configuracion.logo)

    def test_restaurar_predeterminados(self):
        usuario = self._login_recepcionista()
        ConfiguracionConsultorio.objects.update_or_create(
            pk=CONFIGURACION_CONSULTORIO_PK,
            defaults={
                "nombre_comercial": "Marca temporal",
                "titulo_portada": "Titulo temporal",
                "color_principal": "#0F766E",
                "actualizado_por": usuario,
            },
        )

        response = self.client.post(self.url, {"accion": "restaurar_defaults"})

        self.assertRedirects(response, self.url)
        configuracion = ConfiguracionConsultorio.objects.get(pk=CONFIGURACION_CONSULTORIO_PK)
        self.assertEqual(configuracion.nombre_comercial, NOMBRE_COMERCIAL_DEFAULT)
        self.assertEqual(configuracion.titulo_portada, TITULO_PORTADA_DEFAULT)
        self.assertEqual(configuracion.color_principal, COLOR_PRINCIPAL_DEFAULT)
        self.assertEqual(configuracion.actualizado_por, usuario)

    def test_navegacion_muestra_enlace_solo_a_autorizados(self):
        recepcion = self._login_recepcionista(username="recepcion.nav")

        response_autorizado = self.client.get(reverse("inicio"))

        self.assertContains(response_autorizado, reverse("consultorio:configuracion"))

        self.client.logout()
        odontologo_user = get_user_model().objects.create_user(username="dr.nav")
        asignar_rol(odontologo_user, ROL_ODONTOLOGO)
        Odontologo.objects.create(usuario=odontologo_user, matricula="MN-NAV")
        self.client.force_login(odontologo_user)

        response_no_autorizado = self.client.get(reverse("inicio"))

        self.assertNotContains(response_no_autorizado, reverse("consultorio:configuracion"))

    def _login_recepcionista(self, username="recepcion.identidad"):
        usuario = get_user_model().objects.create_user(username=username)
        asignar_rol(usuario, ROL_RECEPCIONISTA)
        self.client.force_login(usuario)
        return usuario


@override_settings(STORAGES=TEST_STORAGES, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PerfilConsultorioPublicoTests(TestCase):
    def setUp(self):
        mail.outbox.clear()

    def test_landing_muestra_nombre_logo_y_contacto_habilitado(self):
        configuracion = obtener_o_crear_configuracion_consultorio()
        configuracion.nombre_comercial = "Clínica Sonrisa"
        configuracion.nombre_corto = "Sonrisa"
        configuracion.titulo_portada = "Turnos simples para tu sonrisa"
        configuracion.texto_bienvenida = "Elegí un horario disponible."
        configuracion.telefono = "3415550000"
        configuracion.whatsapp = "+54 9 341 555 0000"
        configuracion.email = "hola@sonrisa.example"
        configuracion.direccion = "San Martín 100"
        configuracion.localidad = "Rosario"
        configuracion.logo = archivo_logo()
        configuracion.save()

        response = self.client.get(reverse("landing_publica"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Clínica Sonrisa")
        self.assertContains(response, "Turnos simples para tu sonrisa")
        self.assertContains(response, "3415550000")
        self.assertContains(response, "https://wa.me/5493415550000")
        self.assertContains(response, "hola@sonrisa.example")
        self.assertContains(response, "San Martín 100, Rosario")
        self.assertContains(response, "<img", html=False)

    def test_campos_ocultos_no_se_muestran_publicamente(self):
        configuracion = obtener_o_crear_configuracion_consultorio()
        configuracion.telefono = "3415550000"
        configuracion.email = "oculto@example.com"
        configuracion.mostrar_telefono = False
        configuracion.mostrar_email = False
        configuracion.save()

        response = self.client.get(reverse("landing_publica"))

        self.assertNotContains(response, "3415550000")
        self.assertNotContains(response, "oculto@example.com")

    def test_base_usa_nombre_configurado_y_color_principal(self):
        configuracion = obtener_o_crear_configuracion_consultorio()
        configuracion.nombre_comercial = "Consultorio Verde"
        configuracion.color_principal = "#0f766e"
        configuracion.save()

        response = self.client.get(reverse("landing_publica"))

        self.assertContains(response, "Turnos online | Consultorio Verde")
        self.assertContains(response, "--primary: #0F766E;")

    def test_valores_predeterminados_mantienen_apariencia_anterior(self):
        configuracion = obtener_o_crear_configuracion_consultorio()

        response = self.client.get(reverse("landing_publica"))

        self.assertContains(response, NOMBRE_COMERCIAL_DEFAULT)
        self.assertContains(response, TITULO_PORTADA_DEFAULT)
        self.assertContains(response, TEXTO_BIENVENIDA_DEFAULT)
        self.assertContains(response, f"--primary: {COLOR_PRINCIPAL_DEFAULT};")

    def test_context_processor_no_crea_registro_en_request_publica(self):
        ConfiguracionConsultorio.objects.filter(pk=CONFIGURACION_CONSULTORIO_PK).delete()

        response = self.client.get(reverse("landing_publica"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, NOMBRE_COMERCIAL_DEFAULT)
        self.assertFalse(
            ConfiguracionConsultorio.objects.filter(pk=CONFIGURACION_CONSULTORIO_PK).exists()
        )

    def test_flujo_publico_de_solicitud_sigue_disponible(self):
        response = self.client.get(reverse("turnos:solicitud_publica"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Opciones de turnos disponibles")

    def test_textos_configurables_se_escapan(self):
        configuracion = obtener_o_crear_configuracion_consultorio()
        configuracion.titulo_portada = "<script>alert(1)</script>"
        configuracion.texto_bienvenida = "<strong>No interpretar</strong>"
        configuracion.politica_cancelacion = "<img src=x onerror=alert(1)>"
        configuracion.save()

        response = self.client.get(reverse("landing_publica"))

        self.assertNotContains(response, "<script>alert(1)</script>", html=False)
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;", html=False)
        self.assertContains(response, "&lt;strong&gt;No interpretar&lt;/strong&gt;", html=False)
        self.assertContains(response, "&lt;img src=x onerror=alert(1)&gt;", html=False)

    def test_emails_reciben_configuracion_del_consultorio(self):
        configuracion = obtener_o_crear_configuracion_consultorio()
        configuracion.nombre_comercial = "Consultorio Email"
        configuracion.telefono = "3415550000"
        configuracion.politica_cancelacion = "Avisar con 24 horas."
        configuracion.save()
        turno = self._crear_turno()

        resultado = notificar_turno_confirmado(turno)

        self.assertTrue(resultado.enviada)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Consultorio Email", mail.outbox[0].body)
        self.assertIn("3415550000", mail.outbox[0].body)
        self.assertIn("Avisar con 24 horas.", mail.outbox[0].body)

    def _crear_turno(self):
        usuario = get_user_model().objects.create_user(username="dr.email")
        odontologo = Odontologo.objects.create(usuario=usuario, matricula="MN-EMAIL")
        fecha = timezone.localdate() + timedelta(days=10)
        DisponibilidadOdontologo.objects.create(
            odontologo=odontologo,
            dia_semana=fecha.weekday(),
            hora_inicio=time(9, 0),
            hora_fin=time(18, 0),
        )
        paciente = Paciente.objects.create(
            nombre="Paula",
            apellido="Email",
            documento="45111222",
            email="paula@example.com",
        )
        return Turno.objects.create(
            paciente=paciente,
            odontologo=odontologo,
            fecha=fecha,
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.PENDIENTE,
        )
