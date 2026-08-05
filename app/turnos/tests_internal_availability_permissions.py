from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from pacientes.models import Paciente
from turnos.models import DisponibilidadOdontologo, Odontologo, Turno
from usuarios.roles import ROL_ADMINISTRADOR, ROL_ODONTOLOGO, ROL_RECEPCIONISTA


class HorariosDisponiblesJsonPermisosTests(TestCase):
    def setUp(self):
        self.fecha = timezone.localdate() + timedelta(days=7)
        while self.fecha.weekday() >= 5:
            self.fecha += timedelta(days=1)

        self.usuario_a, self.odontologo_a = self._crear_odontologo("a", "MAT-A")
        self.usuario_b, self.odontologo_b = self._crear_odontologo("b", "MAT-B")
        self.recepcionista = self._crear_usuario_con_rol("recepcion", ROL_RECEPCIONISTA)
        self.administrador = self._crear_usuario_con_rol("admin", ROL_ADMINISTRADOR)
        self.superuser = get_user_model().objects.create_superuser(
            username="super-horarios",
            email="super@example.com",
            password="test",
        )
        self.paciente = Paciente.objects.create(
            nombre="Paciente",
            apellido="Prueba",
            documento="40111222",
        )
        self.turno_a = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo_a,
            fecha=self.fecha,
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )
        self.turno_b = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo_b,
            fecha=self.fecha,
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )
        self.url = reverse("turnos:horarios_disponibles")

    def _crear_usuario_con_rol(self, username, rol):
        usuario = get_user_model().objects.create_user(username=username, password="test")
        grupo, _ = Group.objects.get_or_create(name=rol)
        usuario.groups.add(grupo)
        return usuario

    def _crear_odontologo(self, username, matricula):
        usuario = self._crear_usuario_con_rol(username, ROL_ODONTOLOGO)
        odontologo = Odontologo.objects.create(usuario=usuario, matricula=matricula)
        DisponibilidadOdontologo.objects.create(
            odontologo=odontologo,
            dia_semana=self.fecha.weekday(),
            hora_inicio=time(9, 0),
            hora_fin=time(12, 0),
        )
        return usuario, odontologo

    def _consultar(self, usuario, odontologo, turno=None):
        self.client.force_login(usuario)
        parametros = {
            "odontologo": odontologo.pk,
            "fecha": self.fecha.isoformat(),
            "duracion_minutos": 30,
        }
        if turno:
            parametros["turno_id"] = turno.pk
        return self.client.get(self.url, parametros)

    def test_anonimo_debe_autenticarse(self):
        response = self.client.get(
            self.url,
            {"odontologo": self.odontologo_a.pk, "fecha": self.fecha.isoformat()},
        )

        self.assertEqual(response.status_code, 302)

    def test_odontologo_puede_consultar_su_agenda_y_turno(self):
        response = self._consultar(self.usuario_a, self.odontologo_a, self.turno_a)

        self.assertEqual(response.status_code, 200)
        self.assertIn("horarios", response.json())

    def test_odontologo_no_puede_consultar_agenda_ajena(self):
        response = self._consultar(self.usuario_a, self.odontologo_b)

        self.assertEqual(response.status_code, 403)

    def test_odontologo_no_puede_excluir_turno_ajeno(self):
        response = self._consultar(self.usuario_a, self.odontologo_a, self.turno_b)

        self.assertEqual(response.status_code, 403)

    def test_recepcion_puede_consultar_agenda_y_turno_ajenos(self):
        response = self._consultar(self.recepcionista, self.odontologo_b, self.turno_b)

        self.assertEqual(response.status_code, 200)

    def test_administrador_puede_consultar_agendas(self):
        response = self._consultar(self.administrador, self.odontologo_b)

        self.assertEqual(response.status_code, 200)

    def test_administrador_no_excluye_turnos_si_no_puede_reprogramarlos(self):
        response = self._consultar(self.administrador, self.odontologo_b, self.turno_b)

        self.assertEqual(response.status_code, 403)

    def test_superuser_puede_consultar_agenda_y_turno(self):
        response = self._consultar(self.superuser, self.odontologo_b, self.turno_b)

        self.assertEqual(response.status_code, 200)

    def test_odontologo_b_tampoco_accede_al_turno_de_a(self):
        response = self._consultar(self.usuario_b, self.odontologo_b, self.turno_a)

        self.assertEqual(response.status_code, 403)
