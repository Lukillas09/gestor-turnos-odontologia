from datetime import time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from config.storage_backends import SupabaseStorageError
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
from .services import (
    _borrar_logo_seguro,
    guardar_configuracion_consultorio,
    obtener_configuracion_consultorio,
    obtener_o_crear_configuracion_consultorio,
)

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


class StorageLogoConFallo:
    def __init__(
        self,
        *,
        existe=True,
        falla_en_exists=None,
        falla_en_delete=None,
        falla_en_save=None,
        nombre_guardado=None,
    ):
        self.existe = existe
        self.falla_en_exists = falla_en_exists
        self.falla_en_delete = falla_en_delete
        self.falla_en_save = falla_en_save
        self.nombre_guardado = nombre_guardado
        self.exists_llamado = False
        self.delete_llamado = False
        self.save_llamado = False
        self.nombres_exists = []
        self.nombres_delete = []
        self.nombres_save = []

    def exists(self, nombre):
        self.exists_llamado = True
        self.nombres_exists.append(nombre)

        if self.falla_en_exists:
            raise self.falla_en_exists

        return self.existe

    def delete(self, nombre):
        self.delete_llamado = True
        self.nombres_delete.append(nombre)

        if self.falla_en_delete:
            raise self.falla_en_delete

    def save(self, nombre, content, max_length=None):
        self.save_llamado = True
        self.nombres_save.append(nombre)

        if self.falla_en_save:
            raise self.falla_en_save

        return self.nombre_guardado or nombre

    def generate_filename(self, nombre):
        return nombre

    def open(self, nombre, mode="rb"):
        return ContentFile(PNG_BYTES, name=nombre)

    def size(self, nombre):
        return len(PNG_BYTES)

    def url(self, nombre):
        return f"/media/{nombre}"


class LogoStorageMixin:
    def parchear_storage_logo(self, storage):
        campo_logo = ConfiguracionConsultorio._meta.get_field("logo")
        patcher = patch.object(campo_logo, "storage", storage)
        patcher.start()
        self.addCleanup(patcher.stop)
        return storage

    def definir_logo_persistido(self, nombre):
        obtener_o_crear_configuracion_consultorio()
        ConfiguracionConsultorio.objects.filter(pk=CONFIGURACION_CONSULTORIO_PK).update(logo=nombre)
        return ConfiguracionConsultorio.objects.get(pk=CONFIGURACION_CONSULTORIO_PK)


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
        self.assertFalse(
            ConfiguracionConsultorio.objects.filter(pk=CONFIGURACION_CONSULTORIO_PK).exists()
        )

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
            files={
                "logo": archivo_logo(
                    nombre="logo.svg", contenido=b"<svg></svg>", content_type="image/svg+xml"
                )
            },
            instance=obtener_o_crear_configuracion_consultorio(),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("logo", form.errors)


class LimpiezaLogoConsultorioTests(LogoStorageMixin, TestCase):
    def test_nombre_vacio_no_consulta_storage(self):
        storage = self.parchear_storage_logo(StorageLogoConFallo())

        resultado = _borrar_logo_seguro("")

        self.assertTrue(resultado)
        self.assertFalse(storage.exists_llamado)
        self.assertFalse(storage.delete_llamado)

    def test_archivo_inexistente_no_llama_delete_ni_registra_warning(self):
        storage = self.parchear_storage_logo(StorageLogoConFallo(existe=False))

        with patch("consultorio.services.logger.warning") as warning:
            resultado = _borrar_logo_seguro("consultorio/logo-viejo.png")

        self.assertTrue(resultado)
        self.assertTrue(storage.exists_llamado)
        self.assertFalse(storage.delete_llamado)
        warning.assert_not_called()

    def test_archivo_existente_llama_delete_ni_registra_warning(self):
        storage = self.parchear_storage_logo(StorageLogoConFallo(existe=True))

        with patch("consultorio.services.logger.warning") as warning:
            resultado = _borrar_logo_seguro("consultorio/logo-viejo.png")

        self.assertTrue(resultado)
        self.assertTrue(storage.exists_llamado)
        self.assertTrue(storage.delete_llamado)
        warning.assert_not_called()

    def test_error_supabase_en_exists_no_propaga_ni_llama_delete(self):
        storage = self.parchear_storage_logo(
            StorageLogoConFallo(
                falla_en_exists=SupabaseStorageError(
                    "service-role-key https://signed.example.test cuerpo sensible"
                )
            )
        )

        with self.assertLogs("consultorio.services", level="WARNING") as logs:
            resultado = _borrar_logo_seguro("consultorio/logo-viejo.png")

        salida = "\n".join(logs.output)
        self.assertFalse(resultado)
        self.assertTrue(storage.exists_llamado)
        self.assertFalse(storage.delete_llamado)
        self.assertIn("etapa=exists", salida)
        self.assertIn("storage=StorageLogoConFallo", salida)
        self.assertIn("error_type=SupabaseStorageError", salida)
        self.assertNotIn("service-role-key", salida)
        self.assertNotIn("signed.example.test", salida)
        self.assertNotIn("cuerpo sensible", salida)

    def test_error_supabase_en_delete_no_propaga(self):
        storage = self.parchear_storage_logo(
            StorageLogoConFallo(
                falla_en_delete=SupabaseStorageError(
                    "service-role-key https://signed.example.test cuerpo sensible"
                )
            )
        )

        with self.assertLogs("consultorio.services", level="WARNING") as logs:
            resultado = _borrar_logo_seguro("consultorio/logo-viejo.png")

        salida = "\n".join(logs.output)
        self.assertFalse(resultado)
        self.assertTrue(storage.exists_llamado)
        self.assertTrue(storage.delete_llamado)
        self.assertIn("etapa=delete", salida)
        self.assertIn("storage=StorageLogoConFallo", salida)
        self.assertIn("error_type=SupabaseStorageError", salida)
        self.assertNotIn("service-role-key", salida)
        self.assertNotIn("signed.example.test", salida)
        self.assertNotIn("cuerpo sensible", salida)

    def test_error_oserror_en_exists_no_propaga(self):
        self.parchear_storage_logo(StorageLogoConFallo(falla_en_exists=OSError("sin red")))

        with self.assertLogs("consultorio.services", level="WARNING") as logs:
            resultado = _borrar_logo_seguro("consultorio/logo-viejo.png")

        self.assertFalse(resultado)
        self.assertIn("etapa=exists", "\n".join(logs.output))
        self.assertIn("error_type=OSError", "\n".join(logs.output))

    def test_error_inesperado_en_delete_no_propaga(self):
        self.parchear_storage_logo(
            StorageLogoConFallo(falla_en_delete=RuntimeError("detalle sensible"))
        )

        with self.assertLogs("consultorio.services", level="WARNING") as logs:
            resultado = _borrar_logo_seguro("consultorio/logo-viejo.png")

        salida = "\n".join(logs.output)
        self.assertFalse(resultado)
        self.assertIn("etapa=delete", salida)
        self.assertIn("error_type=RuntimeError", salida)
        self.assertNotIn("detalle sensible", salida)


@override_settings(STORAGES=TEST_STORAGES)
class ConfiguracionConsultorioViewTests(LogoStorageMixin, TestCase):
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
        self._login_recepcionista()

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

    def test_reemplazo_logo_programa_limpieza_despues_del_commit(self):
        self._login_recepcionista()
        storage = self.parchear_storage_logo(StorageLogoConFallo())
        self.definir_logo_persistido("consultorio/logo-anterior.png")

        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            response = self.client.post(
                self.url,
                {**datos_configuracion(), "logo": archivo_logo(nombre="nuevo.png")},
            )

        self.assertRedirects(response, self.url)
        self.assertEqual(len(callbacks), 1)
        self.assertFalse(storage.delete_llamado)

        callbacks[0]()
        self.assertTrue(storage.delete_llamado)
        self.assertEqual(storage.nombres_delete, ["consultorio/logo-anterior.png"])

    def test_reemplazo_logo_con_fallo_de_delete_no_devuelve_500(self):
        self._login_recepcionista()
        storage = self.parchear_storage_logo(
            StorageLogoConFallo(falla_en_delete=SupabaseStorageError("service-role-key"))
        )
        self.definir_logo_persistido("consultorio/logo-anterior.png")

        with self.assertLogs("consultorio.services", level="WARNING"):
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                response = self.client.post(
                    self.url,
                    {**datos_configuracion(), "logo": archivo_logo(nombre="nuevo.png")},
                )

        self.assertRedirects(response, self.url)
        self.assertEqual(len(callbacks), 1)
        configuracion = ConfiguracionConsultorio.objects.get(pk=CONFIGURACION_CONSULTORIO_PK)
        self.assertTrue(configuracion.logo)
        self.assertNotEqual(configuracion.logo.name, "consultorio/logo-anterior.png")
        self.assertTrue(storage.delete_llamado)

    def test_no_borra_logo_anterior_si_transaccion_falla(self):
        usuario = self._login_recepcionista()
        storage = self.parchear_storage_logo(StorageLogoConFallo())
        configuracion = self.definir_logo_persistido("consultorio/logo-anterior.png")
        form = ConfiguracionConsultorioForm(
            data=datos_configuracion(quitar_logo="on"),
            instance=configuracion,
        )
        self.assertTrue(form.is_valid(), form.errors)

        with self.assertRaises(RuntimeError):
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                with transaction.atomic():
                    guardar_configuracion_consultorio(configuracion, form, usuario)
                    raise RuntimeError("rollback simulado")

        self.assertEqual(len(callbacks), 0)
        self.assertFalse(storage.delete_llamado)

    def test_no_borra_si_logo_anterior_y_nuevo_tienen_mismo_nombre(self):
        self._login_recepcionista()
        nombre_logo = "consultorio/identidad/logo/logo.png"
        storage = self.parchear_storage_logo(StorageLogoConFallo(nombre_guardado=nombre_logo))
        self.definir_logo_persistido(nombre_logo)

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            response = self.client.post(
                self.url,
                {**datos_configuracion(), "logo": archivo_logo(nombre="logo.png")},
            )

        self.assertRedirects(response, self.url)
        self.assertEqual(len(callbacks), 0)
        self.assertFalse(storage.delete_llamado)

    def test_quitar_logo_con_fallo_de_delete_redirige_y_deja_campo_vacio(self):
        self._login_recepcionista()
        storage = self.parchear_storage_logo(
            StorageLogoConFallo(falla_en_delete=SupabaseStorageError("service-role-key"))
        )
        self.definir_logo_persistido("consultorio/logo-anterior.png")

        with self.assertLogs("consultorio.services", level="WARNING"):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(self.url, datos_configuracion(quitar_logo="on"))

        self.assertRedirects(response, self.url)
        configuracion = ConfiguracionConsultorio.objects.get(pk=CONFIGURACION_CONSULTORIO_PK)
        self.assertFalse(configuracion.logo)
        self.assertTrue(storage.delete_llamado)

    def test_restaurar_predeterminados_con_fallo_de_delete_redirige_y_deja_campo_vacio(self):
        usuario = self._login_recepcionista()
        storage = self.parchear_storage_logo(
            StorageLogoConFallo(falla_en_delete=SupabaseStorageError("service-role-key"))
        )
        self.definir_logo_persistido("consultorio/logo-anterior.png")
        ConfiguracionConsultorio.objects.filter(pk=CONFIGURACION_CONSULTORIO_PK).update(
            nombre_comercial="Marca temporal",
            titulo_portada="Titulo temporal",
            color_principal="#0F766E",
            actualizado_por=usuario,
        )

        with self.assertLogs("consultorio.services", level="WARNING"):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(self.url, {"accion": "restaurar_defaults"})

        self.assertRedirects(response, self.url)
        configuracion = ConfiguracionConsultorio.objects.get(pk=CONFIGURACION_CONSULTORIO_PK)
        self.assertEqual(configuracion.nombre_comercial, NOMBRE_COMERCIAL_DEFAULT)
        self.assertEqual(configuracion.color_principal, COLOR_PRINCIPAL_DEFAULT)
        self.assertFalse(configuracion.logo)
        self.assertTrue(storage.delete_llamado)

    def test_fallo_al_guardar_logo_nuevo_no_se_silencia_ni_borra_anterior(self):
        usuario = self._login_recepcionista()
        storage = self.parchear_storage_logo(
            StorageLogoConFallo(falla_en_save=SupabaseStorageError("fallo subida"))
        )
        configuracion = self.definir_logo_persistido("consultorio/logo-anterior.png")
        form = ConfiguracionConsultorioForm(
            data=datos_configuracion(),
            files={"logo": archivo_logo(nombre="nuevo.png")},
            instance=configuracion,
        )
        self.assertTrue(form.is_valid(), form.errors)

        with self.assertRaises(SupabaseStorageError):
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                guardar_configuracion_consultorio(configuracion, form, usuario)

        self.assertEqual(len(callbacks), 0)
        self.assertTrue(storage.save_llamado)
        self.assertFalse(storage.delete_llamado)

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
        self._login_recepcionista(username="recepcion.nav")

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


@override_settings(
    STORAGES=TEST_STORAGES, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"
)
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
        obtener_o_crear_configuracion_consultorio()

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
