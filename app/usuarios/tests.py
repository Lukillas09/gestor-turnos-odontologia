from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from turnos.models import Odontologo

from .roles import (
    ROL_ADMINISTRADOR,
    ROL_ODONTOLOGO,
    ROL_RECEPCIONISTA,
    puede_configurar_disponibilidad,
    puede_gestionar_consultorio,
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
        self.assertTrue(puede_ver_turnos(usuario))

    def test_odontologo_puede_ver_turnos_sin_gestionar(self):
        usuario = get_user_model().objects.create_user(username="odontologo.roles")
        asignar_rol(usuario, ROL_ODONTOLOGO)
        Odontologo.objects.create(usuario=usuario, matricula="MN-USU")

        self.assertFalse(puede_gestionar_consultorio(usuario))
        self.assertTrue(puede_ver_turnos(usuario))

    def test_administrador_puede_configurar_disponibilidad(self):
        usuario = get_user_model().objects.create_user(username="admin.roles", is_staff=True)
        asignar_rol(usuario, ROL_ADMINISTRADOR)

        self.assertTrue(puede_configurar_disponibilidad(usuario))

    def test_roles_iniciales_crean_permisos_de_disponibilidad(self):
        grupo = Group.objects.get(name=ROL_ADMINISTRADOR)

        self.assertTrue(
            grupo.permissions.filter(codename="change_disponibilidadodontologo").exists()
        )
