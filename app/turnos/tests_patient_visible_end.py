from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from pacientes.models import Paciente
from turnos.models import DisponibilidadOdontologo, Odontologo, Turno
from turnos.notifications import (
    notificar_recordatorio_turno,
    notificar_solicitud_turno_recibida,
    notificar_turno_cancelado,
    notificar_turno_confirmado,
    notificar_turno_reprogramado,
)
from turnos.public_access.tokens import PUBLIC_ACCESS_SESSION_KEY


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class FinVisiblePacienteTests(TestCase):
    def setUp(self):
        usuario = get_user_model().objects.create_user(
            username="odontologa.fin.visible",
            first_name="Ana",
            last_name="Pérez",
        )
        self.odontologo = Odontologo.objects.create(
            usuario=usuario,
            matricula="FIN-VISIBLE-1",
        )
        self.paciente = Paciente.objects.create(
            nombre="Julia",
            apellido="Díaz",
            documento="40999111",
            email="julia@example.com",
        )
        self.fecha = timezone.localdate() + timedelta(days=7)
        while self.fecha.weekday() >= 5:
            self.fecha += timedelta(days=1)
        DisponibilidadOdontologo.objects.create(
            odontologo=self.odontologo,
            dia_semana=self.fecha.weekday(),
            hora_inicio=time(9, 0),
            hora_fin=time(12, 0),
        )
        self.turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=self.fecha,
            hora_inicio=time(9, 0),
            duracion_minutos=60,
            duracion_atencion_minutos=45,
            margen_posterior_minutos_snapshot=15,
            motivo="Control",
        )

    def assert_horario_visible(self, contenido):
        self.assertIn("09:45", contenido)
        self.assertNotIn("10:00", contenido)

    def test_propiedades_diferencian_fin_visible_y_bloqueado(self):
        self.assertEqual(self.turno.hora_fin_atencion, time(9, 45))
        self.assertEqual(self.turno.hora_fin_bloqueada, time(10, 0))
        self.assertEqual(self.turno.hora_fin, time(10, 0))

    def test_emails_muestran_fin_de_atencion(self):
        notificaciones = (
            notificar_solicitud_turno_recibida,
            notificar_turno_confirmado,
            notificar_turno_cancelado,
            notificar_turno_reprogramado,
            notificar_recordatorio_turno,
        )

        for notificar in notificaciones:
            with self.subTest(notificacion=notificar.__name__):
                mail.outbox.clear()
                resultado = notificar(self.turno, fail_silently=False)
                self.assertTrue(resultado.enviada)
                self.assertEqual(len(mail.outbox), 1)
                self.assert_horario_visible(mail.outbox[0].body)

    def test_portal_publico_muestra_fin_de_atencion(self):
        session = self.client.session
        session[PUBLIC_ACCESS_SESSION_KEY] = {"paciente_id": self.paciente.pk}
        session.save()

        response = self.client.get(reverse("turnos:mis_turnos_publico"))

        self.assertEqual(response.status_code, 200)
        self.assert_horario_visible(response.content.decode())

    def test_confirmacion_publica_muestra_fin_de_atencion(self):
        session = self.client.session
        session["solicitud_turno_publica_id"] = self.turno.pk
        session.save()

        response = self.client.get(reverse("turnos:solicitud_publica_ok"))

        self.assertEqual(response.status_code, 200)
        self.assert_horario_visible(response.content.decode())

    def test_turno_sin_margen_muestra_el_mismo_fin(self):
        turno = Turno(
            hora_inicio=time(9, 0),
            fecha=self.fecha,
            duracion_minutos=45,
            duracion_atencion_minutos=45,
        )

        self.assertEqual(turno.hora_fin_atencion, time(9, 45))
        self.assertEqual(turno.hora_fin_bloqueada, time(9, 45))

    def test_turno_legacy_usa_duracion_bloqueada_como_fallback(self):
        turno = Turno(
            hora_inicio=time(9, 0),
            fecha=self.fecha,
            duracion_minutos=30,
            duracion_atencion_minutos=None,
        )

        self.assertEqual(turno.hora_fin_atencion, time(9, 30))
        self.assertEqual(turno.hora_fin_bloqueada, time(9, 30))
