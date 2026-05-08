from datetime import date, time

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from pacientes.models import Paciente
from turnos.models import DisponibilidadOdontologo, Odontologo, Turno

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
