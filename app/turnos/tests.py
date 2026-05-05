from datetime import date, time

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from pacientes.models import Paciente
from turnos.models import DisponibilidadOdontologo, Odontologo, Turno
from turnos.selectors import (
    obtener_horarios_disponibles,
    obtener_inicio_semana,
    obtener_turnos_de_la_semana,
    obtener_turnos_del_dia,
)


def crear_disponibilidad_laboral(odontologo, hora_inicio=time(9, 0), hora_fin=time(18, 0)):
    disponibilidades = []

    for dia_semana in range(5):
        disponibilidades.append(
            DisponibilidadOdontologo.objects.create(
                odontologo=odontologo,
                dia_semana=dia_semana,
                hora_inicio=hora_inicio,
                hora_fin=hora_fin,
            )
        )

    return disponibilidades


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
        crear_disponibilidad_laboral(self.odontologo)
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

    def test_no_permite_turnos_en_dias_no_laborables(self):
        turno_dia_no_laborable = Turno(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 9),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )

        with self.assertRaises(ValidationError):
            turno_dia_no_laborable.full_clean()

    def test_no_permite_turnos_para_odontologo_inactivo(self):
        self.odontologo.activo = False
        self.odontologo.save()

        turno = Turno(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 6),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )

        with self.assertRaises(ValidationError):
            turno.full_clean()


class TurnoViewsTests(TestCase):
    def setUp(self):
        usuario = get_user_model().objects.create_user(
            username="dra.lopez",
            first_name="Maria",
            last_name="Lopez",
        )
        self.client.force_login(usuario)
        self.odontologo = Odontologo.objects.create(
            usuario=usuario,
            matricula="MN-54321",
            hora_inicio_atencion=time(9, 0),
            hora_fin_atencion=time(18, 0),
        )
        crear_disponibilidad_laboral(self.odontologo)
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
        self.assertIn("hora_inicio", response.context["form"].errors)

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


class HorariosDisponiblesTests(TestCase):
    def setUp(self):
        usuario = get_user_model().objects.create_user(
            username="dr.romero",
            first_name="Pablo",
            last_name="Romero",
        )
        self.odontologo = Odontologo.objects.create(
            usuario=usuario,
            matricula="MN-99999",
            duracion_turno_minutos=30,
            hora_inicio_atencion=time(9, 0),
            hora_fin_atencion=time(11, 0),
        )
        DisponibilidadOdontologo.objects.create(
            odontologo=self.odontologo,
            dia_semana=DisponibilidadOdontologo.DiaSemana.VIERNES,
            hora_inicio=time(9, 0),
            hora_fin=time(11, 0),
        )
        self.paciente = Paciente.objects.create(
            nombre="Tomas",
            apellido="Silva",
            documento="33111222",
        )

    def test_calcula_horarios_disponibles_del_dia(self):
        horarios = obtener_horarios_disponibles(self.odontologo, date(2026, 5, 8))

        self.assertEqual(
            horarios,
            [time(9, 0), time(9, 30), time(10, 0), time(10, 30)],
        )

    def test_turnos_activos_bloquean_horarios_disponibles(self):
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(9, 30),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
        )

        horarios = obtener_horarios_disponibles(self.odontologo, date(2026, 5, 8))

        self.assertEqual(horarios, [time(9, 0), time(10, 0), time(10, 30)])

    def test_turnos_cancelados_no_bloquean_horarios_disponibles(self):
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(9, 30),
            duracion_minutos=30,
            estado=Turno.Estado.CANCELADO,
        )

        horarios = obtener_horarios_disponibles(self.odontologo, date(2026, 5, 8))

        self.assertIn(time(9, 30), horarios)

    def test_dia_sin_disponibilidad_no_tiene_horarios(self):
        horarios = obtener_horarios_disponibles(self.odontologo, date(2026, 5, 9))

        self.assertEqual(horarios, [])


class TurnoAccessTests(TestCase):
    def assert_requiere_login(self, url):
        self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")

    def test_listado_requiere_login(self):
        self.assert_requiere_login(reverse("turnos:lista"))

    def test_creacion_requiere_login(self):
        self.assert_requiere_login(reverse("turnos:crear"))

    def test_detalle_requiere_login(self):
        self.assert_requiere_login(reverse("turnos:detalle", kwargs={"pk": 1}))

    def test_edicion_requiere_login(self):
        self.assert_requiere_login(reverse("turnos:editar", kwargs={"pk": 1}))

    def test_agenda_diaria_requiere_login(self):
        self.assert_requiere_login(reverse("turnos:agenda_dia"))

    def test_agenda_semanal_requiere_login(self):
        self.assert_requiere_login(reverse("turnos:agenda_semana"))

    def test_cancelacion_requiere_login(self):
        url = reverse("turnos:cancelar", kwargs={"pk": 1})

        self.assertRedirects(self.client.post(url), f"{reverse('login')}?next={url}")


class AgendaSelectorsTests(TestCase):
    def setUp(self):
        usuario = get_user_model().objects.create_user(
            username="dra.agenda",
            first_name="Clara",
            last_name="Molina",
        )
        self.odontologo = Odontologo.objects.create(
            usuario=usuario,
            matricula="MN-11111",
        )
        crear_disponibilidad_laboral(self.odontologo)
        self.paciente = Paciente.objects.create(
            nombre="Nora",
            apellido="Vega",
            documento="34111222",
        )

    def test_obtiene_turnos_del_dia(self):
        turno_del_dia = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 11),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )

        turnos = obtener_turnos_del_dia(date(2026, 5, 8))

        self.assertEqual(list(turnos), [turno_del_dia])

    def test_obtiene_inicio_de_semana(self):
        self.assertEqual(obtener_inicio_semana(date(2026, 5, 8)), date(2026, 5, 4))

    def test_obtiene_turnos_de_la_semana(self):
        turno_lunes = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 4),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )
        turno_viernes = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(11, 0),
            duracion_minutos=30,
        )

        dias = obtener_turnos_de_la_semana(date(2026, 5, 8))

        self.assertEqual(dias[0]["turnos"], [turno_lunes])
        self.assertEqual(dias[4]["turnos"], [turno_viernes])


class AgendaViewsTests(TestCase):
    def setUp(self):
        usuario = get_user_model().objects.create_user(
            username="dra.vistas",
            first_name="Ines",
            last_name="Costa",
        )
        self.client.force_login(usuario)
        self.odontologo = Odontologo.objects.create(
            usuario=usuario,
            matricula="MN-22222",
        )
        crear_disponibilidad_laboral(self.odontologo)
        self.paciente = Paciente.objects.create(
            nombre="Pedro",
            apellido="Luna",
            documento="35111222",
        )

    def test_agenda_diaria_muestra_turnos_de_fecha_seleccionada(self):
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Control diario",
        )
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 11),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Fuera del dia",
        )

        response = self.client.get(reverse("turnos:agenda_dia"), {"fecha": "2026-05-08"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Control diario")
        self.assertNotContains(response, "Fuera del dia")

    def test_agenda_diaria_filtra_por_odontologo(self):
        otro_usuario = get_user_model().objects.create_user(
            username="dr.otro",
            first_name="Mateo",
            last_name="Ruiz",
        )
        otro_odontologo = Odontologo.objects.create(
            usuario=otro_usuario,
            matricula="MN-33333",
        )
        crear_disponibilidad_laboral(otro_odontologo)
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Turno visible",
        )
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=otro_odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Turno filtrado",
        )

        response = self.client.get(
            reverse("turnos:agenda_dia"),
            {"fecha": "2026-05-08", "odontologo": self.odontologo.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Turno visible")
        self.assertNotContains(response, "Turno filtrado")

    def test_agenda_semanal_muestra_turnos_de_la_semana(self):
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 4),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Inicio de semana",
        )
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 11),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Fuera de la semana",
        )

        response = self.client.get(reverse("turnos:agenda_semana"), {"fecha": "2026-05-08"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Inicio de semana")
        self.assertNotContains(response, "Fuera de la semana")

    def test_agenda_semanal_filtra_por_odontologo(self):
        otro_usuario = get_user_model().objects.create_user(
            username="dra.semana",
            first_name="Paula",
            last_name="Ibarra",
        )
        otro_odontologo = Odontologo.objects.create(
            usuario=otro_usuario,
            matricula="MN-44444",
        )
        crear_disponibilidad_laboral(otro_odontologo)
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Semana visible",
        )
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=otro_odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Semana filtrada",
        )

        response = self.client.get(
            reverse("turnos:agenda_semana"),
            {"fecha": "2026-05-08", "odontologo": self.odontologo.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Semana visible")
        self.assertNotContains(response, "Semana filtrada")

    def test_odontologo_inactivo_no_tiene_horarios_disponibles(self):
        self.odontologo.activo = False
        self.odontologo.save()

        horarios = obtener_horarios_disponibles(self.odontologo, date(2026, 5, 8))

        self.assertEqual(horarios, [])
