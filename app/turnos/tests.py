from datetime import date, time

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from pacientes.models import Paciente
from turnos.models import Odontologo, Turno


class TurnoModelTests(TestCase):
    def setUp(self):
        usuario = get_user_model().objects.create_user(
            username="dra.gomez",
            first_name="Ana",
            last_name="Gomez",
        )
        self.odontologo = Odontologo.objects.create(
            usuario=usuario,
            matricula="MN-12345",
            hora_inicio_atencion=time(9, 0),
            hora_fin_atencion=time(18, 0),
        )
        self.paciente = Paciente.objects.create(
            nombre="Lucas",
            apellido="Perez",
            documento="12345678",
        )

    def test_no_permite_turnos_solapados(self):
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 6),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )

        turno_solapado = Turno(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 6),
            hora_inicio=time(10, 15),
            duracion_minutos=30,
        )

        with self.assertRaises(ValidationError):
            turno_solapado.full_clean()

    def test_permite_reusar_horario_de_turno_cancelado(self):
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 6),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CANCELADO,
        )

        turno_nuevo = Turno(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 6),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )

        turno_nuevo.full_clean()

    def test_no_permite_turnos_fuera_del_horario_de_atencion(self):
        turno_fuera_de_horario = Turno(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 6),
            hora_inicio=time(17, 45),
            duracion_minutos=30,
        )

        with self.assertRaises(ValidationError):
            turno_fuera_de_horario.full_clean()


class TurnoViewsTests(TestCase):
    def setUp(self):
        usuario = get_user_model().objects.create_user(
            username="dra.lopez",
            first_name="Maria",
            last_name="Lopez",
        )
        self.odontologo = Odontologo.objects.create(
            usuario=usuario,
            matricula="MN-54321",
            hora_inicio_atencion=time(9, 0),
            hora_fin_atencion=time(18, 0),
        )
        self.paciente = Paciente.objects.create(
            nombre="Julia",
            apellido="Diaz",
            documento="30123456",
        )

    def test_listado_muestra_turnos(self):
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )

        response = self.client.get(reverse("turnos:lista"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Diaz")
        self.assertContains(response, "10:00")

    def test_listado_filtra_por_estado(self):
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
        )
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 9),
            hora_inicio=time(11, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CANCELADO,
        )

        response = self.client.get(reverse("turnos:lista"), {"estado": Turno.Estado.CANCELADO})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cancelado")
        self.assertContains(response, "09/05/2026")
        self.assertNotContains(response, "08/05/2026")

    def test_creacion_de_turno_valido(self):
        response = self.client.post(
            reverse("turnos:crear"),
            {
                "paciente": self.paciente.pk,
                "odontologo": self.odontologo.pk,
                "fecha": "2026-05-08",
                "hora_inicio": "10:00",
                "duracion_minutos": 30,
                "motivo": "Control",
                "estado": Turno.Estado.PENDIENTE,
                "notas": "",
            },
        )

        self.assertRedirects(response, reverse("turnos:lista"))
        self.assertTrue(Turno.objects.filter(motivo="Control").exists())

    def test_creacion_rechaza_turno_solapado(self):
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )

        response = self.client.post(
            reverse("turnos:crear"),
            {
                "paciente": self.paciente.pk,
                "odontologo": self.odontologo.pk,
                "fecha": "2026-05-08",
                "hora_inicio": "10:15",
                "duracion_minutos": 30,
                "motivo": "Limpieza",
                "estado": Turno.Estado.PENDIENTE,
                "notas": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Turno.objects.filter(motivo="Limpieza").exists())
        self.assertIn("hora_inicio", response.context["form"].errors)

    def test_creacion_rechaza_turno_fuera_de_horario(self):
        response = self.client.post(
            reverse("turnos:crear"),
            {
                "paciente": self.paciente.pk,
                "odontologo": self.odontologo.pk,
                "fecha": "2026-05-08",
                "hora_inicio": "17:45",
                "duracion_minutos": 30,
                "motivo": "Control fuera de horario",
                "estado": Turno.Estado.PENDIENTE,
                "notas": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Turno.objects.filter(motivo="Control fuera de horario").exists())
        self.assertIn("duracion_minutos", response.context["form"].errors)

    def test_detalle_muestra_datos_del_turno(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Control",
        )

        response = self.client.get(reverse("turnos:detalle", kwargs={"pk": turno.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Diaz")
        self.assertContains(response, "Control")
        self.assertContains(response, "10:00")

    def test_edicion_actualiza_turno(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Control",
        )

        response = self.client.post(
            reverse("turnos:editar", kwargs={"pk": turno.pk}),
            {
                "paciente": self.paciente.pk,
                "odontologo": self.odontologo.pk,
                "fecha": "2026-05-08",
                "hora_inicio": "11:00",
                "duracion_minutos": 45,
                "motivo": "Limpieza",
                "estado": Turno.Estado.CONFIRMADO,
                "notas": "Reprogramado",
            },
        )

        turno.refresh_from_db()

        self.assertRedirects(response, reverse("turnos:detalle", kwargs={"pk": turno.pk}))
        self.assertEqual(turno.hora_inicio, time(11, 0))
        self.assertEqual(turno.duracion_minutos, 45)
        self.assertEqual(turno.motivo, "Limpieza")
        self.assertEqual(turno.estado, Turno.Estado.CONFIRMADO)
        self.assertEqual(turno.notas, "Reprogramado")

    def test_edicion_rechaza_turno_solapado(self):
        turno_existente = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(11, 0),
            duracion_minutos=30,
        )

        response = self.client.post(
            reverse("turnos:editar", kwargs={"pk": turno.pk}),
            {
                "paciente": self.paciente.pk,
                "odontologo": self.odontologo.pk,
                "fecha": "2026-05-08",
                "hora_inicio": "10:15",
                "duracion_minutos": 30,
                "motivo": "",
                "estado": Turno.Estado.PENDIENTE,
                "notas": "",
            },
        )

        turno.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertIn("hora_inicio", response.context["form"].errors)
        self.assertEqual(turno.hora_inicio, time(11, 0))
        self.assertEqual(Turno.objects.count(), 2)
        self.assertEqual(turno_existente.hora_inicio, time(10, 0))

    def test_cancelacion_cambia_estado_sin_borrar_turno(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )

        response = self.client.post(reverse("turnos:cancelar", kwargs={"pk": turno.pk}))

        turno.refresh_from_db()

        self.assertRedirects(response, reverse("turnos:detalle", kwargs={"pk": turno.pk}))
        self.assertEqual(turno.estado, Turno.Estado.CANCELADO)
        self.assertEqual(Turno.objects.count(), 1)
