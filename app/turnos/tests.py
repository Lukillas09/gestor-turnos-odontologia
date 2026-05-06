from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from pacientes.models import Paciente
from turnos.models import DisponibilidadOdontologo, Odontologo, Turno
from turnos.selectors import (
    obtener_bloques_agenda_del_dia,
    obtener_horarios_disponibles,
    obtener_inicio_semana,
    obtener_turnos_de_la_semana,
    obtener_turnos_del_dia,
)
from usuarios.roles import ROL_ODONTOLOGO, ROL_RECEPCIONISTA


def asignar_rol(usuario, nombre_rol):
    grupo, _ = Group.objects.get_or_create(name=nombre_rol)
    usuario.groups.add(grupo)


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
        asignar_rol(usuario, ROL_RECEPCIONISTA)
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

    def test_creacion_pide_buscar_horarios_por_odontologo_y_fecha(self):
        response = self.client.get(reverse("turnos:crear"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Buscar horarios")
        self.assertContains(response, "Elegir odontologo y fecha")

    def test_creacion_muestra_solo_horarios_disponibles(self):
        Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(9, 30),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
        )

        response = self.client.get(
            reverse("turnos:crear"),
            {
                "odontologo": self.odontologo.pk,
                "fecha": "2026-05-08",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="09:00"')
        self.assertContains(response, 'value="10:00"')
        self.assertNotContains(response, 'value="09:30"')

    def test_creacion_indica_cuando_no_hay_horarios_disponibles(self):
        response = self.client.get(
            reverse("turnos:crear"),
            {
                "odontologo": self.odontologo.pk,
                "fecha": "2026-05-09",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sin horarios disponibles")

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

    def test_detalle_muestra_boton_confirmar_si_el_turno_esta_pendiente(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.PENDIENTE,
        )

        response = self.client.get(reverse("turnos:detalle", kwargs={"pk": turno.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirmar turno")

    def test_detalle_no_muestra_boton_confirmar_si_el_turno_no_esta_pendiente(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
        )

        response = self.client.get(reverse("turnos:detalle", kwargs={"pk": turno.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Confirmar turno")

    def test_confirmacion_cambia_estado_sin_modificar_fecha_ni_horario(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=60,
            estado=Turno.Estado.PENDIENTE,
            motivo="Solicitud publica",
        )

        response = self.client.post(reverse("turnos:confirmar", kwargs={"pk": turno.pk}))

        turno.refresh_from_db()

        self.assertRedirects(response, reverse("turnos:detalle", kwargs={"pk": turno.pk}))
        self.assertEqual(turno.estado, Turno.Estado.CONFIRMADO)
        self.assertEqual(turno.fecha, date(2026, 5, 8))
        self.assertEqual(turno.hora_inicio, time(10, 0))
        self.assertEqual(turno.duracion_minutos, 60)

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


class SolicitudTurnoPublicaTests(TestCase):
    def setUp(self):
        usuario = get_user_model().objects.create_user(
            username="dra.publica",
            first_name="Paula",
            last_name="Publica",
        )
        self.odontologo = Odontologo.objects.create(
            usuario=usuario,
            matricula="MN-PUB",
            duracion_turno_minutos=30,
        )
        crear_disponibilidad_laboral(self.odontologo)

    def test_formulario_publico_no_requiere_login(self):
        response = self.client.get(reverse("turnos:solicitud_publica"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Solicitar turno")
        self.assertContains(response, "Enviar solicitud")

    def test_formulario_publico_muestra_horarios_disponibles(self):
        paciente = Paciente.objects.create(
            nombre="Rita",
            apellido="Moreno",
            documento="37111222",
        )
        Turno.objects.create(
            paciente=paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(9, 30),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
        )

        response = self.client.get(
            reverse("turnos:solicitud_publica"),
            {
                "odontologo": self.odontologo.pk,
                "fecha": "2026-05-08",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="09:00"')
        self.assertContains(response, 'value="10:00"')
        self.assertNotContains(response, 'value="09:30"')

    def test_formulario_publico_no_permite_buscar_fecha_pasada(self):
        fecha_pasada = timezone.localdate() - timedelta(days=1)

        response = self.client.get(
            reverse("turnos:solicitud_publica"),
            {
                "odontologo": self.odontologo.pk,
                "fecha": fecha_pasada.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "La fecha no puede ser anterior a hoy.")

    def test_solicitud_publica_crea_paciente_y_turno_pendiente(self):
        response = self.client.post(
            reverse("turnos:solicitud_publica"),
            {
                "nombre": "Lucia",
                "apellido": "Paz",
                "documento": "38111222",
                "telefono": "1155667788",
                "email": "lucia@example.com",
                "odontologo": self.odontologo.pk,
                "fecha": "2026-05-08",
                "hora_inicio": "10:00",
                "motivo": "Consulta inicial",
            },
        )

        turno = Turno.objects.get(motivo="Consulta inicial")

        self.assertRedirects(response, reverse("turnos:solicitud_publica_ok"))
        self.assertEqual(turno.estado, Turno.Estado.PENDIENTE)
        self.assertEqual(turno.paciente.documento, "38111222")
        self.assertEqual(turno.hora_inicio, time(10, 0))

    def test_confirmacion_publica_muestra_datos_del_turno(self):
        self.client.post(
            reverse("turnos:solicitud_publica"),
            {
                "nombre": "Lucia",
                "apellido": "Paz",
                "documento": "38111222",
                "telefono": "1155667788",
                "email": "lucia@example.com",
                "odontologo": self.odontologo.pk,
                "fecha": "2026-05-08",
                "hora_inicio": "10:00",
                "motivo": "Consulta inicial",
            },
        )

        response = self.client.get(reverse("turnos:solicitud_publica_ok"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Paz, Lucia")
        self.assertContains(response, "Paula Publica")
        self.assertContains(response, "08/05/2026")
        self.assertContains(response, "10:00 a 10:30")
        self.assertContains(response, "Pendiente")

    def test_solicitud_publica_reutiliza_paciente_por_documento(self):
        paciente = Paciente.objects.create(
            nombre="Viejo",
            apellido="Nombre",
            documento="39111222",
        )

        response = self.client.post(
            reverse("turnos:solicitud_publica"),
            {
                "nombre": "Nadia",
                "apellido": "Suarez",
                "documento": "39111222",
                "telefono": "1199999999",
                "email": "nadia@example.com",
                "odontologo": self.odontologo.pk,
                "fecha": "2026-05-08",
                "hora_inicio": "11:00",
                "motivo": "Control",
            },
        )

        paciente.refresh_from_db()
        turno = Turno.objects.get(motivo="Control")

        self.assertRedirects(response, reverse("turnos:solicitud_publica_ok"))
        self.assertEqual(Paciente.objects.filter(documento="39111222").count(), 1)
        self.assertEqual(turno.paciente, paciente)
        self.assertEqual(paciente.nombre, "Nadia")
        self.assertEqual(paciente.email, "nadia@example.com")

    def test_solicitud_publica_rechaza_horario_no_disponible(self):
        paciente = Paciente.objects.create(
            nombre="Mario",
            apellido="Rojas",
            documento="40111222",
        )
        Turno.objects.create(
            paciente=paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )

        response = self.client.post(
            reverse("turnos:solicitud_publica"),
            {
                "nombre": "Clara",
                "apellido": "Luna",
                "documento": "41111222",
                "telefono": "",
                "email": "",
                "odontologo": self.odontologo.pk,
                "fecha": "2026-05-08",
                "hora_inicio": "10:00",
                "motivo": "Horario ocupado",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Turno.objects.filter(motivo="Horario ocupado").exists())
        self.assertIn("hora_inicio", response.context["form"].errors)

    def test_solicitud_publica_rechaza_fecha_pasada(self):
        fecha_pasada = timezone.localdate() - timedelta(days=1)

        response = self.client.post(
            reverse("turnos:solicitud_publica"),
            {
                "nombre": "Clara",
                "apellido": "Luna",
                "documento": "42111222",
                "telefono": "",
                "email": "",
                "odontologo": self.odontologo.pk,
                "fecha": fecha_pasada.isoformat(),
                "hora_inicio": "10:00",
                "motivo": "Fecha pasada",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Turno.objects.filter(motivo="Fecha pasada").exists())
        self.assertIn("fecha", response.context["form"].errors)
        self.assertContains(response, "La fecha no puede ser anterior a hoy.")

    def test_solicitud_publica_muestra_mensajes_de_error_claros(self):
        response = self.client.post(reverse("turnos:solicitud_publica"), {})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ingresa tu nombre.")
        self.assertContains(response, "Ingresa tu apellido.")
        self.assertContains(response, "Elegi un odontologo.")
        self.assertContains(response, "Elegi una fecha.")
        self.assertContains(response, "Elegi un horario disponible.")


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

    def test_confirmacion_requiere_login(self):
        url = reverse("turnos:confirmar", kwargs={"pk": 1})

        self.assertRedirects(self.client.post(url), f"{reverse('login')}?next={url}")


class TurnoRoleTests(TestCase):
    def setUp(self):
        usuario_odontologo = get_user_model().objects.create_user(
            username="dr.roles",
            first_name="Ramon",
            last_name="Roles",
        )
        asignar_rol(usuario_odontologo, ROL_ODONTOLOGO)
        self.odontologo = Odontologo.objects.create(
            usuario=usuario_odontologo,
            matricula="MN-ROL",
        )
        crear_disponibilidad_laboral(self.odontologo)

        otro_usuario = get_user_model().objects.create_user(
            username="dra.otra",
            first_name="Laura",
            last_name="Otra",
        )
        self.otro_odontologo = Odontologo.objects.create(
            usuario=otro_usuario,
            matricula="MN-OTRA",
        )
        crear_disponibilidad_laboral(self.otro_odontologo)

        self.paciente = Paciente.objects.create(
            nombre="Marta",
            apellido="Ruiz",
            documento="36111222",
        )
        self.turno_propio = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Turno propio",
        )
        self.turno_ajeno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.otro_odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            motivo="Turno ajeno",
        )
        self.client.force_login(usuario_odontologo)

    def test_odontologo_lista_solo_sus_turnos(self):
        response = self.client.get(reverse("turnos:lista"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Roles")
        self.assertNotContains(response, "Otra")
        self.assertNotContains(response, "Nuevo turno")
        self.assertNotContains(response, "Editar")

    def test_odontologo_puede_ver_detalle_de_turno_propio(self):
        response = self.client.get(
            reverse("turnos:detalle", kwargs={"pk": self.turno_propio.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Turno propio")
        self.assertNotContains(response, "Cancelar turno")

    def test_odontologo_no_puede_ver_turno_ajeno(self):
        response = self.client.get(
            reverse("turnos:detalle", kwargs={"pk": self.turno_ajeno.pk})
        )

        self.assertEqual(response.status_code, 404)

    def test_odontologo_no_puede_gestionar_turnos(self):
        response_crear = self.client.get(reverse("turnos:crear"))
        response_editar = self.client.get(
            reverse("turnos:editar", kwargs={"pk": self.turno_propio.pk})
        )
        response_confirmar = self.client.post(
            reverse("turnos:confirmar", kwargs={"pk": self.turno_propio.pk})
        )
        response_cancelar = self.client.post(
            reverse("turnos:cancelar", kwargs={"pk": self.turno_propio.pk})
        )

        self.turno_propio.refresh_from_db()

        self.assertEqual(response_crear.status_code, 403)
        self.assertEqual(response_editar.status_code, 403)
        self.assertEqual(response_confirmar.status_code, 403)
        self.assertEqual(response_cancelar.status_code, 403)
        self.assertEqual(self.turno_propio.estado, Turno.Estado.PENDIENTE)

    def test_agenda_de_odontologo_queda_filtrada_a_su_perfil(self):
        response = self.client.get(reverse("turnos:agenda_dia"), {"fecha": "2026-05-08"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Turno propio")
        self.assertNotContains(response, "Turno ajeno")


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

    def test_obtiene_bloques_de_agenda_del_dia(self):
        turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
        )

        bloques = obtener_bloques_agenda_del_dia(date(2026, 5, 8), self.odontologo)
        bloque_de_turno = next(
            bloque for bloque in bloques if bloque["hora_inicio"] == time(10, 0)
        )

        self.assertEqual(bloques[0]["hora_inicio"], time(9, 0))
        self.assertEqual(bloques[-1]["hora_fin"], time(18, 0))
        self.assertEqual(bloque_de_turno["turnos"], [turno])

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
        asignar_rol(usuario, ROL_RECEPCIONISTA)
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
        self.assertContains(response, "10:00 a 10:30")
        self.assertContains(response, "status-pendiente")
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
        self.assertContains(response, "status-pendiente")
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
