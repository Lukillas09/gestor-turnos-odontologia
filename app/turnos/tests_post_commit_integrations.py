from datetime import time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from pacientes.models import Paciente
from turnos.integrations.google_calendar import GoogleCalendarHTTPError
from turnos.integrations.post_commit import (
    EMAIL_CONFIRMADO,
    GOOGLE_CREAR,
    programar_integraciones_turno,
)
from turnos.models import DisponibilidadOdontologo, Odontologo, Turno
from turnos.notifications import ResultadoNotificacionEmail


class TurnoIntegracionFixtureMixin:
    def setUp(self):
        super().setUp()
        usuario = get_user_model().objects.create_user(username="integraciones.postcommit")
        self.odontologo = Odontologo.objects.create(
            usuario=usuario,
            matricula="INT-POST-001",
        )
        fecha = timezone.localdate() + timedelta(days=7)
        DisponibilidadOdontologo.objects.create(
            odontologo=self.odontologo,
            dia_semana=fecha.weekday(),
            hora_inicio=time(8, 0),
            hora_fin=time(18, 0),
        )
        paciente = Paciente.objects.create(
            nombre="Paciente",
            apellido="Integraciones",
            documento="77666111",
            email="paciente@example.test",
        )
        self.turno = Turno.objects.create(
            paciente=paciente,
            odontologo=self.odontologo,
            fecha=fecha,
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.CONFIRMADO,
        )


class IntegracionesPostCommitTests(TurnoIntegracionFixtureMixin, TestCase):
    def test_commit_programa_un_callback_y_recarga_el_turno(self):
        with (
            patch("turnos.google_calendar_sync.sincronizar_turno_creado") as sincronizar_google,
            patch(
                "turnos.notifications.notificar_turno_confirmado",
                return_value=ResultadoNotificacionEmail(enviada=True),
            ) as notificar_email,
        ):
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                with transaction.atomic():
                    programar_integraciones_turno(
                        self.turno.pk,
                        google=GOOGLE_CREAR,
                        email=EMAIL_CONFIRMADO,
                    )
                sincronizar_google.assert_not_called()
                notificar_email.assert_not_called()

            self.assertEqual(len(callbacks), 1)
            callbacks[0]()

            sincronizar_google.assert_called_once()
            notificar_email.assert_called_once()
            self.assertEqual(sincronizar_google.call_args.args[0].pk, self.turno.pk)

    def test_rollback_no_ejecuta_ni_conserva_callback(self):
        with (
            patch("turnos.google_calendar_sync.sincronizar_turno_creado") as sincronizar_google,
            self.captureOnCommitCallbacks(execute=False) as callbacks,
        ):
            try:
                with transaction.atomic():
                    programar_integraciones_turno(
                        self.turno.pk,
                        google=GOOGLE_CREAR,
                    )
                    raise RuntimeError("rollback intencional")
            except RuntimeError:
                pass

        self.assertEqual(callbacks, [])
        sincronizar_google.assert_not_called()

    def test_fallo_de_google_no_impide_email_ni_revierte_turno(self):
        detalle_sensible = "token-secreto cuerpo-proveedor paciente@example.test"
        with (
            patch(
                "turnos.google_calendar_sync.sincronizar_turno_creado",
                side_effect=TimeoutError(detalle_sensible),
            ),
            patch(
                "turnos.notifications.notificar_turno_confirmado",
                return_value=ResultadoNotificacionEmail(enviada=True),
            ) as notificar_email,
            self.assertLogs("turnos.integrations.post_commit", level="WARNING") as logs,
            self.captureOnCommitCallbacks(execute=True),
        ):
            with transaction.atomic():
                programar_integraciones_turno(
                    self.turno.pk,
                    google=GOOGLE_CREAR,
                    email=EMAIL_CONFIRMADO,
                )

        self.assertTrue(Turno.objects.filter(pk=self.turno.pk).exists())
        notificar_email.assert_called_once()
        salida = " ".join(logs.output)
        self.assertIn("error_type=TimeoutError", salida)
        self.assertNotIn(detalle_sensible, salida)

    def test_errores_http_de_google_quedan_sanitizados(self):
        for status_code in (400, 503):
            with self.subTest(status_code=status_code):
                detalle_sensible = f"respuesta sensible HTTP {status_code}"
                with (
                    patch(
                        "turnos.google_calendar_sync.sincronizar_turno_creado",
                        side_effect=GoogleCalendarHTTPError(
                            detalle_sensible,
                            status_code=status_code,
                        ),
                    ),
                    self.assertLogs(
                        "turnos.integrations.post_commit",
                        level="WARNING",
                    ) as logs,
                    self.captureOnCommitCallbacks(execute=True),
                ):
                    with transaction.atomic():
                        programar_integraciones_turno(
                            self.turno.pk,
                            google=GOOGLE_CREAR,
                        )

                self.assertTrue(Turno.objects.filter(pk=self.turno.pk).exists())
                self.assertNotIn(detalle_sensible, " ".join(logs.output))

    def test_operaciones_invalidas_no_programan_callbacks(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            with self.assertRaises(ValueError):
                programar_integraciones_turno(self.turno.pk, google="invalida")
            with self.assertRaises(ValueError):
                programar_integraciones_turno(self.turno.pk, email="invalida")

        self.assertEqual(callbacks, [])


class IntegracionesFueraDeTransaccionTests(
    TurnoIntegracionFixtureMixin,
    TransactionTestCase,
):
    def test_proveedores_se_invocan_despues_del_commit_sin_transaccion_activa(self):
        def google_sin_transaccion(_turno):
            self.assertFalse(connection.in_atomic_block)

        def email_sin_transaccion(_turno):
            self.assertFalse(connection.in_atomic_block)
            return ResultadoNotificacionEmail(enviada=True)

        with (
            patch(
                "turnos.google_calendar_sync.sincronizar_turno_creado",
                side_effect=google_sin_transaccion,
            ) as sincronizar_google,
            patch(
                "turnos.notifications.notificar_turno_confirmado",
                side_effect=email_sin_transaccion,
            ) as notificar_email,
        ):
            with transaction.atomic():
                programar_integraciones_turno(
                    self.turno.pk,
                    google=GOOGLE_CREAR,
                    email=EMAIL_CONFIRMADO,
                )

        sincronizar_google.assert_called_once()
        notificar_email.assert_called_once()
