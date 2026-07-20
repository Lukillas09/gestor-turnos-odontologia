import hashlib
from unittest.mock import patch

from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import override_settings

from historias.models import HistoriaClinica
from indicaciones.emails import enviar_indicacion_por_email, reenviar_indicacion
from indicaciones.models import IndicacionPaciente
from indicaciones.services import (
    anular_indicacion,
    crear_borrador_indicacion,
    crear_reemplazo_indicacion,
    emitir_indicacion,
)
from pacientes.models import PacienteOdontologo
from turnos.models import SolicitudTurnoPublica

from .base import IndicacionesTestCase


class IndicacionServiceTests(IndicacionesTestCase):
    def test_emision_genera_pdf_a4_privado_y_callback_posterior_al_commit(self):
        borrador = self.crear_borrador()

        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            emitida = emitir_indicacion(indicacion=borrador, usuario=self.usuario)

        emitida.refresh_from_db()
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(emitida.email_estado, IndicacionPaciente.EstadoEmail.PENDIENTE)
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(emitida.pdf.name.startswith(f"indicaciones/{emitida.uuid}/"))
        with emitida.pdf.open("rb") as archivo:
            pdf = archivo.read()
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertEqual(hashlib.sha256(pdf).hexdigest(), emitida.pdf_sha256)

        callbacks[0]()

        emitida.refresh_from_db()
        self.assertEqual(emitida.email_estado, IndicacionPaciente.EstadoEmail.ENVIADO)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.paciente.email])
        self.assertEqual(mail.outbox[0].attachments[0].mimetype, "application/pdf")

    def test_emision_es_idempotente_y_no_programa_segundo_envio(self):
        emitida, callbacks = self.emitir(ejecutar_callback=False)
        self.assertEqual(len(callbacks), 1)

        with self.captureOnCommitCallbacks(execute=False) as callbacks_repetidos:
            repetida = emitir_indicacion(indicacion=emitida, usuario=self.usuario)

        self.assertEqual(repetida.pk, emitida.pk)
        self.assertEqual(callbacks_repetidos, [])
        self.assertEqual(repetida.pdf.name, emitida.pdf.name)

    def test_emision_bloquea_el_borrador_dentro_de_una_transaccion(self):
        borrador = self.crear_borrador()

        with patch.object(
            IndicacionPaciente.objects,
            "select_for_update",
            wraps=IndicacionPaciente.objects.select_for_update,
        ) as seleccionar_bloqueada:
            self.emitir(indicacion=borrador, ejecutar_callback=False)

        seleccionar_bloqueada.assert_called_once_with(of=("self",))

    def test_fallo_de_email_no_revierte_emision_ni_expone_detalle(self):
        borrador = self.crear_borrador()
        secreto = "service-role-key-ficticia https://signed.example.test/token"

        with (
            patch(
                "indicaciones.emails.EmailMessage.send",
                side_effect=RuntimeError(secreto),
            ),
            self.assertLogs("indicaciones.emails", level="WARNING") as logs,
        ):
            with self.captureOnCommitCallbacks(execute=True):
                emitida = emitir_indicacion(indicacion=borrador, usuario=self.usuario)

        emitida.refresh_from_db()
        salida = "\n".join(logs.output)
        self.assertEqual(emitida.estado, IndicacionPaciente.Estado.EMITIDA)
        self.assertTrue(emitida.pdf)
        self.assertEqual(emitida.email_estado, IndicacionPaciente.EstadoEmail.ERROR)
        self.assertNotIn(secreto, salida)
        self.assertNotIn("signed.example", salida)
        self.assertIn("RuntimeError", salida)

    def test_paciente_sin_email_verificado_no_recibe_envio(self):
        self.paciente.email_verificado_en = None
        self.paciente.save()

        emitida, callbacks = self.emitir(ejecutar_callback=True)

        self.assertEqual(callbacks, [])
        self.assertEqual(emitida.email_destino, "")
        self.assertEqual(emitida.email_estado, IndicacionPaciente.EstadoEmail.SIN_DESTINO)
        self.assertEqual(len(mail.outbox), 0)

    def test_email_propuesto_publico_no_reemplaza_destino_verificado(self):
        SolicitudTurnoPublica.objects.create(
            paciente=self.paciente,
            documento_enviado=self.paciente.documento,
            nombre_enviado=self.paciente.nombre,
            apellido_enviado=self.paciente.apellido,
            telefono_enviado=self.paciente.telefono,
            email_enviado="propuesto-no-verificado@example.test",
            paciente_existente=True,
        )

        emitida, _ = self.emitir(ejecutar_callback=True)

        self.assertEqual(emitida.email_destino, self.paciente.email)
        self.assertEqual(mail.outbox[0].to, [self.paciente.email])
        self.assertNotEqual(emitida.email_destino, "propuesto-no-verificado@example.test")

    def test_reenvio_puede_usar_nuevo_email_actual_verificado(self):
        emitida, _ = self.emitir(ejecutar_callback=True)
        self.paciente.email = "nuevo@example.test"
        self.paciente.email_verificado_en = self.paciente.email_verificado_en
        self.paciente.save()

        enviado = reenviar_indicacion(
            indicacion=emitida,
            usuario=self.usuario,
            usar_email_actual=True,
        )

        emitida.refresh_from_db()
        self.assertTrue(enviado)
        self.assertEqual(emitida.email_destino, "nuevo@example.test")
        self.assertEqual(mail.outbox[-1].to, ["nuevo@example.test"])
        self.assertEqual(emitida.email_intentos, 2)

    def test_reintento_no_duplica_email_ya_enviado_sin_forzar(self):
        emitida, _ = self.emitir(ejecutar_callback=True)

        resultado = enviar_indicacion_por_email(
            indicacion_id=emitida.pk,
            automatico=True,
            forzar=False,
        )

        emitida.refresh_from_db()
        self.assertTrue(resultado)
        self.assertEqual(emitida.email_intentos, 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_feature_flag_bloquea_envio_directo(self):
        emitida, _ = self.emitir(ejecutar_callback=False)

        with (
            override_settings(INDICACIONES_POSTOPERATORIAS_ENABLED=False),
            self.assertRaisesMessage(PermissionDenied, "deshabilitado"),
        ):
            enviar_indicacion_por_email(indicacion_id=emitida.pk, automatico=True)

        emitida.refresh_from_db()
        self.assertEqual(emitida.email_estado, IndicacionPaciente.EstadoEmail.PENDIENTE)
        self.assertEqual(emitida.email_intentos, 0)

    def test_reemplazo_de_otro_odontologo_no_hereda_relaciones_ajenas(self):
        historia = HistoriaClinica.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            creado_por=self.usuario,
            actualizado_por=self.usuario,
            motivo_consulta="Motivo clínico ficticio para la prueba.",
        )
        borrador = self.crear_borrador(historia_clinica=historia)
        emitida, _ = self.emitir(indicacion=borrador, ejecutar_callback=False)
        anulada = anular_indicacion(
            indicacion=emitida,
            usuario=self.usuario,
            motivo="Reemplazo ficticio por otro profesional autorizado.",
        )
        PacienteOdontologo.objects.create(
            paciente=self.paciente,
            odontologo=self.otro_odontologo,
            asignado_por=self.usuario,
            motivo="Acceso compartido ficticio para validar el reemplazo.",
        )

        reemplazo = crear_reemplazo_indicacion(
            indicacion=anulada,
            usuario=self.otro_usuario,
        )

        self.assertEqual(reemplazo.odontologo_id, self.otro_odontologo.pk)
        self.assertIsNone(reemplazo.historia_clinica_id)
        self.assertIsNone(reemplazo.turno_id)

    def test_pdf_se_entrega_al_storage_con_mime_explicito(self):
        storage = self.campo_pdf.storage

        with patch.object(storage, "save", wraps=storage.save) as guardar:
            self.emitir(ejecutar_callback=False)

        contenido = guardar.call_args.args[1]
        self.assertEqual(contenido.content_type, "application/pdf")

    def test_usuario_fuera_de_alcance_no_puede_crear(self):
        with self.assertRaises(PermissionDenied):
            crear_borrador_indicacion(
                paciente=self.paciente,
                usuario=self.otro_usuario,
                datos=self.datos_borrador(),
            )

    def test_relacion_clinica_de_otro_paciente_es_rechazada(self):
        datos = self.datos_borrador(
            turno=self.crear_turno(paciente=self.paciente_fuera_de_alcance),
            historia_clinica=self.crear_historia(
                paciente=self.paciente_fuera_de_alcance,
            ),
        )

        with self.assertRaises(ValidationError) as contexto:
            crear_borrador_indicacion(
                paciente=self.paciente,
                usuario=self.usuario,
                datos=datos,
            )

        self.assertIn("turno", contexto.exception.error_dict)
        self.assertIn("historia_clinica", contexto.exception.error_dict)
        self.assertFalse(IndicacionPaciente.objects.exists())

    def test_relacion_clinica_de_otro_odontologo_es_rechazada(self):
        datos = self.datos_borrador(
            turno=self.crear_turno(odontologo=self.otro_odontologo),
            historia_clinica=self.crear_historia(
                odontologo=self.otro_odontologo,
            ),
        )

        with self.assertRaises(ValidationError) as contexto:
            crear_borrador_indicacion(
                paciente=self.paciente,
                usuario=self.usuario,
                datos=datos,
            )

        self.assertIn("turno", contexto.exception.error_dict)
        self.assertIn("historia_clinica", contexto.exception.error_dict)
        self.assertFalse(IndicacionPaciente.objects.exists())
