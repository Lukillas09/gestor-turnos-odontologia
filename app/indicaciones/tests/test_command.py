from io import StringIO
from unittest.mock import patch

from django.core import mail
from django.core.management import CommandError, call_command
from django.test import override_settings

from indicaciones.models import IndicacionPaciente

from .base import IndicacionesTestCase


class ReenvioPendientesCommandTests(IndicacionesTestCase):
    def test_dry_run_no_envia_ni_modifica_documento(self):
        emitida, _ = self.emitir(ejecutar_callback=False)
        salida = StringIO()

        call_command("reenviar_indicaciones_pendientes", "--dry-run", stdout=salida)

        emitida.refresh_from_db()
        self.assertIn("1 indicación(es) elegibles", salida.getvalue())
        self.assertEqual(emitida.email_estado, IndicacionPaciente.EstadoEmail.PENDIENTE)
        self.assertEqual(emitida.email_intentos, 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_envia_solamente_documentos_pendientes(self):
        pendiente, _ = self.emitir(ejecutar_callback=False)
        enviado, _ = self.emitir(ejecutar_callback=True)
        salida = StringIO()

        call_command("reenviar_indicaciones_pendientes", stdout=salida)

        pendiente.refresh_from_db()
        enviado.refresh_from_db()
        self.assertEqual(pendiente.email_estado, IndicacionPaciente.EstadoEmail.ENVIADO)
        self.assertEqual(enviado.email_intentos, 1)
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("procesados: 1", salida.getvalue())

    def test_respeta_maximo_de_intentos(self):
        emitida, _ = self.emitir(ejecutar_callback=False)
        emitida.email_intentos = 3
        emitida.email_estado = IndicacionPaciente.EstadoEmail.ERROR
        emitida.save(permitir_actualizacion_email=True)
        salida = StringIO()

        call_command(
            "reenviar_indicaciones_pendientes",
            "--max-intentos",
            "3",
            "--dry-run",
            stdout=salida,
        )

        self.assertIn("0 indicación(es) elegibles", salida.getvalue())

    def test_puede_fallar_operativamente_sin_revertir_documento(self):
        emitida, _ = self.emitir(ejecutar_callback=False)

        with (
            patch(
                "indicaciones.emails.EmailMessage.send",
                side_effect=RuntimeError("error técnico ficticio"),
            ),
            self.assertRaises(CommandError),
        ):
            call_command(
                "reenviar_indicaciones_pendientes",
                "--fallar-si-hay-errores",
                stdout=StringIO(),
            )

        emitida.refresh_from_db()
        self.assertEqual(emitida.estado, IndicacionPaciente.Estado.EMITIDA)
        self.assertTrue(emitida.pdf)
        self.assertEqual(emitida.email_estado, IndicacionPaciente.EstadoEmail.ERROR)

    def test_rechaza_limites_invalidos(self):
        with self.assertRaises(CommandError):
            call_command("reenviar_indicaciones_pendientes", "--limite", "0")
        with self.assertRaises(CommandError):
            call_command("reenviar_indicaciones_pendientes", "--max-intentos", "0")

    @override_settings(INDICACIONES_POSTOPERATORIAS_ENABLED=False)
    def test_no_procesa_reintentos_con_feature_flag_deshabilitado(self):
        with self.assertRaisesMessage(CommandError, "deshabilitado"):
            call_command("reenviar_indicaciones_pendientes", stdout=StringIO())
