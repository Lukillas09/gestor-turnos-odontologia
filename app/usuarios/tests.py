from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from pacientes.models import Paciente
from turnos.models import (
    DisponibilidadOdontologo,
    Odontologo,
    Turno,
)

from .roles import (
    ROL_ADMINISTRADOR,
    ROL_ODONTOLOGO,
    ROL_RECEPCIONISTA,
    puede_borrar_pacientes,
    puede_configurar_disponibilidad,
    puede_gestionar_consultorio,
    puede_reprogramar_turno,
    puede_reintentar_sincronizacion_google_calendar,
    puede_ver_pacientes,
    puede_ver_turnos,
)


def asignar_rol(usuario, nombre_rol):
    grupo, _ = Group.objects.get_or_create(name=nombre_rol)
    usuario.groups.add(grupo)


PERFIL_TEST_STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.InMemoryStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}


class RolesTests(TestCase):
    def test_recepcionista_puede_gestionar_consultorio(self):
        usuario = get_user_model().objects.create_user(username="recepcion.roles")
        asignar_rol(usuario, ROL_RECEPCIONISTA)

        self.assertTrue(puede_gestionar_consultorio(usuario))
        self.assertTrue(puede_ver_pacientes(usuario))
        self.assertTrue(puede_borrar_pacientes(usuario))
        self.assertTrue(puede_ver_turnos(usuario))

    def test_odontologo_puede_ver_turnos_sin_gestionar(self):
        usuario = get_user_model().objects.create_user(username="odontologo.roles")
        asignar_rol(usuario, ROL_ODONTOLOGO)
        Odontologo.objects.create(usuario=usuario, matricula="MN-USU")

        self.assertFalse(puede_gestionar_consultorio(usuario))
        self.assertTrue(puede_ver_pacientes(usuario))
        self.assertTrue(puede_borrar_pacientes(usuario))
        self.assertTrue(puede_ver_turnos(usuario))

    def test_administrador_puede_configurar_disponibilidad(self):
        usuario = get_user_model().objects.create_user(username="admin.roles", is_staff=True)
        asignar_rol(usuario, ROL_ADMINISTRADOR)

        self.assertTrue(puede_configurar_disponibilidad(usuario))
        self.assertTrue(puede_ver_pacientes(usuario))
        self.assertTrue(puede_borrar_pacientes(usuario))
        self.assertTrue(puede_ver_turnos(usuario))

    def test_roles_iniciales_crean_permisos_de_disponibilidad(self):
        grupo = Group.objects.get(name=ROL_ADMINISTRADOR)

        self.assertTrue(
            grupo.permissions.filter(codename="change_disponibilidadodontologo").exists()
        )

    def test_recepcionista_puede_reintentar_sincronizacion_google_calendar(self):
        usuario = get_user_model().objects.create_user(username="recepcion.sync")
        asignar_rol(usuario, ROL_RECEPCIONISTA)
        turno = crear_turno_para_permiso()

        self.assertTrue(puede_reintentar_sincronizacion_google_calendar(usuario, turno))

    def test_administrador_puede_reintentar_sincronizacion_google_calendar(self):
        usuario = get_user_model().objects.create_user(username="admin.sync", is_staff=True)
        asignar_rol(usuario, ROL_ADMINISTRADOR)
        turno = crear_turno_para_permiso()

        self.assertTrue(puede_reintentar_sincronizacion_google_calendar(usuario, turno))

    def test_odontologo_puede_reintentar_solo_sus_turnos(self):
        usuario = get_user_model().objects.create_user(username="odontologo.sync")
        asignar_rol(usuario, ROL_ODONTOLOGO)
        odontologo = Odontologo.objects.create(usuario=usuario, matricula="MN-SYNC-ROL")
        turno_propio = crear_turno_para_permiso(odontologo=odontologo)
        turno_ajeno = crear_turno_para_permiso(matricula="MN-SYNC-AJENO")

        self.assertTrue(
            puede_reintentar_sincronizacion_google_calendar(usuario, turno_propio)
        )
        self.assertFalse(
            puede_reintentar_sincronizacion_google_calendar(usuario, turno_ajeno)
        )

    def test_odontologo_puede_reprogramar_solo_sus_turnos_activos(self):
        usuario = get_user_model().objects.create_user(username="odontologo.reprograma")
        asignar_rol(usuario, ROL_ODONTOLOGO)
        odontologo = Odontologo.objects.create(usuario=usuario, matricula="MN-REPROG")
        turno_propio = crear_turno_para_permiso(odontologo=odontologo)
        turno_ajeno = crear_turno_para_permiso(matricula="MN-REPROG-AJENO")
        turno_cancelado = crear_turno_para_permiso(matricula="MN-REPROG-CANCELADO")
        turno_cancelado.odontologo = odontologo
        turno_cancelado.estado = Turno.Estado.CANCELADO
        turno_cancelado.save()

        self.assertTrue(puede_reprogramar_turno(usuario, turno_propio))
        self.assertFalse(puede_reprogramar_turno(usuario, turno_ajeno))
        self.assertFalse(puede_reprogramar_turno(usuario, turno_cancelado))


class PerfilUsuarioTests(TestCase):
    def test_perfil_requiere_login(self):
        response = self.client.get(reverse("perfil"))

        self.assertRedirects(response, f"{reverse('login')}?next={reverse('perfil')}")

    def test_panel_interno_muestra_acceso_al_perfil(self):
        usuario = get_user_model().objects.create_user(username="recepcion.perfil")
        asignar_rol(usuario, ROL_RECEPCIONISTA)
        self.client.force_login(usuario)

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("perfil"))
        self.assertContains(response, "recepcion.perfil")

    @override_settings(STORAGES=PERFIL_TEST_STORAGES)
    def test_odontologo_actualiza_datos_y_foto_desde_perfil(self):
        usuario = get_user_model().objects.create_user(
            username="odontologo.perfil",
            first_name="Lucas",
            last_name="Martinez",
            email="lucas@example.com",
        )
        asignar_rol(usuario, ROL_ODONTOLOGO)
        odontologo = Odontologo.objects.create(
            usuario=usuario,
            matricula="MN-PERFIL",
            especialidad="Odontologia general",
        )
        foto = SimpleUploadedFile(
            "perfil.jpg",
            b"imagen-de-prueba",
            content_type="image/jpeg",
        )
        self.client.force_login(usuario)

        response = self.client.post(
            reverse("perfil"),
            {
                "first_name": "Lucia",
                "last_name": "Perez",
                "email": "lucia@example.com",
                "celular": "3415550000",
                "especialidad": "Ortodoncia",
                "matricula": "MN-PERFIL-2",
                "duracion_turno_minutos": "45",
                "foto_posicion_x": "35",
                "foto_posicion_y": "70",
                "foto_perfil": foto,
            },
        )

        self.assertRedirects(response, reverse("perfil"))
        usuario.refresh_from_db()
        odontologo.refresh_from_db()
        self.assertEqual(usuario.first_name, "Lucia")
        self.assertEqual(usuario.last_name, "Perez")
        self.assertEqual(usuario.email, "lucia@example.com")
        self.assertEqual(odontologo.especialidad, "Ortodoncia")
        self.assertEqual(odontologo.celular, "3415550000")
        self.assertEqual(odontologo.matricula, "MN-PERFIL-2")
        self.assertEqual(odontologo.duracion_turno_minutos, 45)
        self.assertEqual(odontologo.foto_posicion_x, 35)
        self.assertEqual(odontologo.foto_posicion_y, 70)
        self.assertTrue(odontologo.foto_perfil.name.startswith("odontologos/"))


class InicioDashboardTests(TestCase):
    def test_inicio_requiere_login(self):
        response = self.client.get("/")

        self.assertRedirects(response, f"{reverse('login')}?next=%2F")

    def test_recepcionista_ve_dashboard_interno(self):
        usuario = get_user_model().objects.create_user(username="recepcion.dashboard")
        asignar_rol(usuario, ROL_RECEPCIONISTA)
        self.client.force_login(usuario)

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Resumen de la agenda")
        self.assertContains(response, "Turnos de hoy")
        self.assertContains(response, "Turnos de la semana")
        self.assertContains(response, "Pendientes")
        self.assertNotContains(response, "Accesos rápidos")

    def test_dashboard_muestra_solo_resumen_y_turnos_de_hoy(self):
        usuario = get_user_model().objects.create_user(username="recepcion.operativa")
        asignar_rol(usuario, ROL_RECEPCIONISTA)
        odontologo = crear_odontologo_dashboard()
        paciente = Paciente.objects.create(
            nombre="Ana",
            apellido="Control",
            documento="DASH-001",
        )
        hoy = timezone.localdate()
        crear_disponibilidad_para_fecha(odontologo, hoy)
        crear_disponibilidad_para_fecha(odontologo, hoy + timedelta(days=1))
        crear_disponibilidad_para_fecha(odontologo, hoy + timedelta(days=2))
        turno_hoy = Turno.objects.create(
            paciente=paciente,
            odontologo=odontologo,
            fecha=hoy,
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.PENDIENTE,
            motivo="Consulta dashboard",
        )
        Turno.objects.create(
            paciente=paciente,
            odontologo=odontologo,
            fecha=hoy + timedelta(days=1),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
            recordatorio_email_enviado_en=timezone.now(),
        )
        Turno.objects.create(
            paciente=paciente,
            odontologo=odontologo,
            fecha=hoy + timedelta(days=2),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
            recordatorio_email_ultimo_error="SMTP temporal",
        )
        self.client.force_login(usuario)

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Resumen de la agenda")
        self.assertContains(response, "Turnos de hoy")
        self.assertContains(response, "Turnos de la semana")
        self.assertContains(response, "Pendientes")
        self.assertContains(response, "Consulta dashboard")
        self.assertContains(response, reverse("turnos:detalle", kwargs={"pk": turno_hoy.pk}))
        self.assertContains(response, f"{reverse('turnos:agenda_dia')}?fecha={hoy.isoformat()}")
        self.assertContains(response, f"{reverse('turnos:lista')}?estado=pendiente")
        self.assertNotContains(response, "Historias clínicas")
        self.assertNotContains(response, "Recordatorios")
        self.assertNotContains(response, "Próximos controles")
        self.assertNotContains(response, "Estado operativo")
        self.assertNotContains(response, "Accesos rápidos")

    def test_dashboard_odontologo_ve_solo_sus_turnos_y_links_filtrados(self):
        usuario_odontologo = get_user_model().objects.create_user(
            username="odontologo.dashboard",
            first_name="Odo",
            last_name="Propio",
        )
        asignar_rol(usuario_odontologo, ROL_ODONTOLOGO)
        odontologo = Odontologo.objects.create(
            usuario=usuario_odontologo,
            matricula="MN-DASH-PROPIO",
        )
        otro_odontologo = crear_odontologo_dashboard(
            username="dr.dashboard.ajeno",
            matricula="MN-DASH-AJENO",
        )
        paciente = Paciente.objects.create(
            nombre="Paciente",
            apellido="Propio",
            documento="DASH-002",
        )
        paciente_ajeno = Paciente.objects.create(
            nombre="Paciente",
            apellido="Ajeno",
            documento="DASH-003",
        )
        hoy = timezone.localdate()
        crear_disponibilidad_para_fecha(odontologo, hoy)
        crear_disponibilidad_para_fecha(otro_odontologo, hoy)
        crear_disponibilidad_para_fecha(odontologo, hoy + timedelta(days=1))
        crear_disponibilidad_para_fecha(otro_odontologo, hoy + timedelta(days=1))
        turno_propio = Turno.objects.create(
            paciente=paciente,
            odontologo=odontologo,
            fecha=hoy,
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.PENDIENTE,
            motivo="Control propio",
        )
        Turno.objects.create(
            paciente=paciente,
            odontologo=odontologo,
            fecha=hoy + timedelta(days=1),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
            motivo="Control semana propio",
        )
        Turno.objects.create(
            paciente=paciente_ajeno,
            odontologo=otro_odontologo,
            fecha=hoy,
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.PENDIENTE,
            motivo="Control ajeno",
        )
        Turno.objects.create(
            paciente=paciente_ajeno,
            odontologo=otro_odontologo,
            fecha=hoy + timedelta(days=1),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
            motivo="Control semana ajeno",
        )
        self.client.force_login(usuario_odontologo)

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tu agenda de hoy")
        self.assertContains(response, "Control propio")
        self.assertContains(response, reverse("turnos:detalle", kwargs={"pk": turno_propio.pk}))
        self.assertContains(
            response,
            f"{reverse('turnos:agenda_dia')}?fecha={hoy.isoformat()}&amp;odontologo={odontologo.pk}",
        )
        self.assertContains(
            response,
            f"{reverse('turnos:agenda_semana')}?fecha={hoy.isoformat()}&amp;odontologo={odontologo.pk}",
        )
        self.assertContains(
            response,
            f"{reverse('turnos:lista')}?estado=pendiente&amp;odontologo={odontologo.pk}",
        )
        self.assertNotContains(response, "Control ajeno")
        self.assertNotContains(response, "Control semana ajeno")
        self.assertNotContains(response, "Paciente Ajeno")


def crear_turno_para_permiso(odontologo=None, matricula="MN-SYNC-PERMISO"):
    if odontologo is None:
        usuario = get_user_model().objects.create_user(username=f"usuario.{matricula.lower()}")
        odontologo = Odontologo.objects.create(usuario=usuario, matricula=matricula)

    DisponibilidadOdontologo.objects.get_or_create(
        odontologo=odontologo,
        dia_semana=DisponibilidadOdontologo.DiaSemana.VIERNES,
        defaults={
            "hora_inicio": time(9, 0),
            "hora_fin": time(18, 0),
        },
    )
    paciente = Paciente.objects.create(
        nombre="Paciente",
        apellido=matricula,
        documento=matricula,
    )
    return Turno.objects.create(
        paciente=paciente,
        odontologo=odontologo,
        fecha=date(2026, 5, 8),
        hora_inicio=time(10, 0),
        duracion_minutos=30,
    )


def crear_odontologo_dashboard(username="dr.dashboard", matricula="MN-DASH"):
    usuario = get_user_model().objects.create_user(
        username=username,
        first_name="Lucas",
        last_name="Martinez",
    )
    return Odontologo.objects.create(usuario=usuario, matricula=matricula)


def crear_disponibilidad_para_fecha(odontologo, fecha):
    DisponibilidadOdontologo.objects.get_or_create(
        odontologo=odontologo,
        dia_semana=fecha.weekday(),
        defaults={
            "hora_inicio": time(9, 0),
            "hora_fin": time(18, 0),
        },
    )
