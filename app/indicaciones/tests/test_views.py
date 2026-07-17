from django.core import mail
from django.test import override_settings
from django.urls import reverse

from historias.models import AccesoClinicoAuditoria
from indicaciones.models import IndicacionPaciente
from indicaciones.services import (
    anular_indicacion,
    crear_reemplazo_indicacion,
    emitir_indicacion,
)

from .base import IndicacionesTestCase


class IndicacionViewTests(IndicacionesTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.usuario)

    def test_ficha_paciente_muestra_modulo_y_accion_al_odontologo_asociado(self):
        response = self.client.get(reverse("pacientes:detalle", args=[self.paciente.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Indicaciones postoperatorias")
        self.assertContains(response, "Nueva indicación")

    @override_settings(INDICACIONES_POSTOPERATORIAS_ENABLED=False)
    def test_feature_flag_oculta_integracion_y_rutas(self):
        ficha = self.client.get(reverse("pacientes:detalle", args=[self.paciente.pk]))
        lista = self.client.get(reverse("indicaciones:lista", args=[self.paciente.pk]))

        self.assertNotContains(ficha, "Indicaciones postoperatorias")
        self.assertEqual(lista.status_code, 404)

    def test_recepcion_no_puede_ver_modulo_clinico(self):
        self.client.force_login(self.recepcionista)

        ficha = self.client.get(reverse("pacientes:detalle", args=[self.paciente.pk]))
        response = self.client.get(reverse("indicaciones:lista", args=[self.paciente.pk]))

        self.assertEqual(ficha.status_code, 200)
        self.assertNotContains(ficha, "Nueva indicación")
        self.assertEqual(response.status_code, 404)

    def test_odontologo_sin_asociacion_recibe_404(self):
        self.client.force_login(self.otro_usuario)

        response = self.client.get(reverse("indicaciones:lista", args=[self.paciente.pk]))

        self.assertEqual(response.status_code, 404)

    def test_flujo_crear_editar_revisar_emitir_y_descargar(self):
        crear_url = reverse("indicaciones:crear", args=[self.paciente.pk])
        response = self.client.post(
            crear_url,
            {
                "plantilla": self.plantilla.pk,
                "titulo": "Documento ficticio desde vista",
                "procedimiento": "Procedimiento de prueba",
                "contenido": "Contenido ficticio revisado por el profesional de prueba.",
                "pautas_alarma": "Pauta ficticia.",
                "recomendaciones_control": "Control ficticio.",
                "observaciones_personalizadas": "Observación ficticia.",
                "proximo_control_en": "",
                "turno": "",
                "historia_clinica": "",
            },
        )
        indicacion = IndicacionPaciente.objects.get(titulo="Documento ficticio desde vista")
        self.assertRedirects(
            response,
            reverse("indicaciones:detalle", args=[self.paciente.pk, indicacion.uuid]),
        )

        editar_url = reverse(
            "indicaciones:editar",
            args=[self.paciente.pk, indicacion.uuid],
        )
        editar = self.client.post(
            editar_url,
            {
                "titulo": "Documento ficticio corregido",
                "procedimiento": "Procedimiento de prueba",
                "contenido": "Contenido ficticio corregido por el profesional.",
                "pautas_alarma": "Pauta ficticia.",
                "recomendaciones_control": "Control ficticio.",
                "observaciones_personalizadas": "Observación ficticia.",
                "proximo_control_en": "",
                "turno": "",
                "historia_clinica": "",
            },
        )
        self.assertEqual(editar.status_code, 302)

        revisar = self.client.get(
            reverse("indicaciones:revisar", args=[self.paciente.pk, indicacion.uuid])
        )
        self.assertContains(revisar, "La emisión es permanente")

        with self.captureOnCommitCallbacks(execute=True):
            emitir = self.client.post(
                reverse("indicaciones:emitir", args=[self.paciente.pk, indicacion.uuid]),
                {"confirmar": "on"},
            )
        self.assertEqual(emitir.status_code, 302)
        indicacion.refresh_from_db()
        self.assertEqual(indicacion.estado, IndicacionPaciente.Estado.EMITIDA)
        self.assertEqual(len(mail.outbox), 1)

        pdf = self.client.get(reverse("indicaciones:pdf", args=[self.paciente.pk, indicacion.uuid]))
        contenido = b"".join(pdf.streaming_content)
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertEqual(pdf["Cache-Control"], "private, no-store")
        self.assertTrue(contenido.startswith(b"%PDF-"))

        edicion_prohibida = self.client.get(editar_url)
        self.assertEqual(edicion_prohibida.status_code, 403)

    def test_anular_y_crear_reemplazo_desde_vistas(self):
        emitida, _ = self.emitir(ejecutar_callback=True)

        anulacion = self.client.post(
            reverse("indicaciones:anular", args=[self.paciente.pk, emitida.uuid]),
            {"motivo": "Corrección ficticia documentada para validar la anulación."},
        )
        self.assertEqual(anulacion.status_code, 302)
        emitida.refresh_from_db()
        self.assertEqual(emitida.estado, IndicacionPaciente.Estado.ANULADA)

        reemplazo = self.client.post(
            reverse("indicaciones:reemplazo", args=[self.paciente.pk, emitida.uuid]),
            {"confirmar": "on"},
        )
        self.assertEqual(reemplazo.status_code, 302)
        nuevo = IndicacionPaciente.objects.get(reemplaza_a=emitida)
        self.assertEqual(nuevo.estado, IndicacionPaciente.Estado.BORRADOR)

    def test_reenvio_desde_vista_incrementa_intentos(self):
        emitida, _ = self.emitir(ejecutar_callback=True)

        response = self.client.post(
            reverse("indicaciones:reenviar", args=[self.paciente.pk, emitida.uuid]),
            {},
        )

        emitida.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(emitida.email_estado, IndicacionPaciente.EstadoEmail.ENVIADO)
        self.assertEqual(emitida.email_intentos, 2)
        self.assertEqual(len(mail.outbox), 2)

    def test_reenvio_sin_destino_original_exige_email_actual_explicito(self):
        self.paciente.email = ""
        self.paciente.email_verificado_en = None
        self.paciente.save(update_fields=["email", "email_verificado_en"])
        emitida, _ = self.emitir(ejecutar_callback=True)
        self.assertEqual(emitida.email_estado, IndicacionPaciente.EstadoEmail.SIN_DESTINO)

        self.paciente.email = "nuevo@example.test"
        self.paciente.email_verificado_en = self.paciente.creado_en
        self.paciente.save(update_fields=["email", "email_verificado_en"])
        url = reverse("indicaciones:reenviar", args=[self.paciente.pk, emitida.uuid])

        formulario = self.client.get(url)
        sin_confirmar = self.client.post(url, {})
        confirmado = self.client.post(url, {"usar_email_actual": "on"})

        self.assertEqual(formulario.status_code, 200)
        self.assertContains(formulario, "Sin destinatario capturado al emitir")
        self.assertEqual(sin_confirmar.status_code, 400)
        self.assertContains(
            sin_confirmar,
            "Confirmá que querés usar el email actual verificado del paciente.",
            status_code=400,
        )
        self.assertEqual(confirmado.status_code, 302)
        emitida.refresh_from_db()
        self.assertEqual(emitida.email_destino, "nuevo@example.test")
        self.assertEqual(emitida.email_estado, IndicacionPaciente.EstadoEmail.ENVIADO)
        self.assertEqual(len(mail.outbox), 1)

    def test_uuid_conocido_no_concede_acceso_a_otro_odontologo(self):
        emitida, _ = self.emitir(ejecutar_callback=False)
        self.client.force_login(self.otro_usuario)

        detalle = self.client.get(
            reverse("indicaciones:detalle", args=[self.paciente.pk, emitida.uuid])
        )
        pdf = self.client.get(reverse("indicaciones:pdf", args=[self.paciente.pk, emitida.uuid]))

        self.assertEqual(detalle.status_code, 404)
        self.assertEqual(pdf.status_code, 404)
        self.assertTrue(
            AccesoClinicoAuditoria.objects.filter(
                usuario=self.otro_usuario,
                accion=AccesoClinicoAuditoria.Accion.INTENTO_ACCESO_INDICACION,
                resultado=AccesoClinicoAuditoria.Resultado.DENEGADO,
                identificador_solicitado=str(emitida.uuid),
            ).exists()
        )

    def test_lista_identifica_documento_que_ya_tiene_reemplazo_emitido(self):
        emitida, _ = self.emitir(ejecutar_callback=False)
        anulada = anular_indicacion(
            indicacion=emitida,
            usuario=self.usuario,
            motivo="Corrección ficticia para emitir un reemplazo.",
        )
        reemplazo = crear_reemplazo_indicacion(
            indicacion=anulada,
            usuario=self.usuario,
        )
        with self.captureOnCommitCallbacks(execute=False):
            emitir_indicacion(indicacion=reemplazo, usuario=self.usuario)

        response = self.client.get(reverse("indicaciones:lista", args=[self.paciente.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reemplazada", count=1)

    def test_borrador_no_tiene_descarga_pdf(self):
        borrador = self.crear_borrador()

        response = self.client.get(
            reverse("indicaciones:pdf", args=[self.paciente.pk, borrador.uuid])
        )

        self.assertEqual(response.status_code, 404)
