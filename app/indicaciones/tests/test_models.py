from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from indicaciones.integrity import verificar_sello_indicacion
from indicaciones.models import IndicacionPaciente, PlantillaIndicacionVersion
from indicaciones.services import (
    anular_indicacion,
    crear_reemplazo_indicacion,
    crear_version_plantilla,
)

from .base import IndicacionesTestCase


class IndicacionModelTests(IndicacionesTestCase):
    def test_modelo_rechaza_turno_e_historia_de_otro_paciente(self):
        turno = self.crear_turno(paciente=self.paciente_fuera_de_alcance)
        historia = self.crear_historia(paciente=self.paciente_fuera_de_alcance)
        indicacion = IndicacionPaciente(
            paciente=self.paciente,
            odontologo=self.odontologo,
            turno=turno,
            historia_clinica=historia,
            titulo="Indicaciones de prueba",
            contenido="Contenido clinico ficticio definido por el profesional.",
            creado_por=self.usuario,
            actualizado_por=self.usuario,
        )

        with self.assertRaises(ValidationError) as contexto:
            indicacion.full_clean()

        self.assertIn("turno", contexto.exception.error_dict)
        self.assertIn("historia_clinica", contexto.exception.error_dict)

    def test_borrador_se_crea_sin_datos_definitivos(self):
        indicacion = self.crear_borrador()

        self.assertEqual(indicacion.estado, IndicacionPaciente.Estado.BORRADOR)
        self.assertFalse(indicacion.pdf)
        self.assertEqual(indicacion.snapshot_documento, {})
        self.assertEqual(indicacion.plantilla_version, 1)

    def test_borrador_puede_modificarse(self):
        indicacion = self.crear_borrador()
        indicacion.titulo = "Título ficticio actualizado"
        indicacion.actualizado_por = self.usuario

        indicacion.save()

        indicacion.refresh_from_db()
        self.assertEqual(indicacion.titulo, "Título ficticio actualizado")

    def test_documento_emitido_no_admite_edicion_ni_borrado(self):
        indicacion, _ = self.emitir()
        indicacion.contenido = "Intento de modificación"

        with self.assertRaisesMessage(ValidationError, "inmutable"):
            indicacion.save()
        with self.assertRaises(ProtectedError):
            indicacion.delete()
        with self.assertRaises(ValidationError):
            IndicacionPaciente.objects.filter(pk=indicacion.pk).update(titulo="Cambio")
        with self.assertRaises(ProtectedError):
            IndicacionPaciente.objects.filter(pk=indicacion.pk).delete()

    def test_emision_construye_snapshots_pdf_hash_y_sello_validos(self):
        indicacion, _ = self.emitir()

        self.assertEqual(indicacion.estado, IndicacionPaciente.Estado.EMITIDA)
        self.assertEqual(indicacion.snapshot_paciente["documento"], self.paciente.documento)
        self.assertEqual(indicacion.snapshot_profesional["matricula"], "IND-001")
        self.assertEqual(indicacion.snapshot_documento["titulo"], indicacion.titulo)
        self.assertEqual(len(indicacion.pdf_sha256), 64)
        self.assertEqual(len(indicacion.sello_integridad), 64)
        self.assertTrue(verificar_sello_indicacion(indicacion))

    def test_versionar_plantilla_preserva_snapshot_anterior(self):
        datos = {
            "nombre": self.plantilla.nombre,
            "procedimiento": "Procedimiento actualizado",
            "titulo_documento": "Título actualizado",
            "contenido": "Nuevo contenido ficticio aprobado.",
            "pautas_alarma": "Nueva pauta ficticia.",
            "recomendaciones_control": "Nuevo control ficticio.",
            "activa": True,
        }

        actualizada = crear_version_plantilla(
            plantilla=self.plantilla,
            usuario=self.usuario,
            datos=datos,
            motivo="Actualización ficticia aprobada para pruebas.",
        )

        version = PlantillaIndicacionVersion.objects.get(plantilla=actualizada)
        self.assertEqual(actualizada.version, 2)
        self.assertEqual(version.numero_version, 1)
        self.assertEqual(version.snapshot["contenido"], self.plantilla.contenido)
        with self.assertRaises(ValidationError):
            version.save()

    def test_anulacion_conserva_documento_y_permite_reemplazo_idempotente(self):
        emitida, _ = self.emitir()
        nombre_pdf = emitida.pdf.name
        hash_pdf = emitida.pdf_sha256

        anulada = anular_indicacion(
            indicacion=emitida,
            usuario=self.usuario,
            motivo="Corrección documental ficticia requerida para la prueba.",
        )
        reemplazo = crear_reemplazo_indicacion(
            indicacion=anulada,
            usuario=self.usuario,
        )
        repetido = crear_reemplazo_indicacion(
            indicacion=anulada,
            usuario=self.usuario,
        )

        anulada.refresh_from_db()
        self.assertEqual(anulada.estado, IndicacionPaciente.Estado.ANULADA)
        self.assertEqual(anulada.pdf.name, nombre_pdf)
        self.assertEqual(anulada.pdf_sha256, hash_pdf)
        self.assertEqual(reemplazo.reemplaza_a_id, anulada.pk)
        self.assertEqual(reemplazo.pk, repetido.pk)

    def test_no_se_puede_anular_un_borrador(self):
        borrador = self.crear_borrador()

        with self.assertRaisesMessage(ValidationError, "transición"):
            borrador.estado = IndicacionPaciente.Estado.ANULADA
            borrador.save(permitir_anulacion=True)

    def test_anulacion_exige_motivo_y_luego_es_inmutable(self):
        emitida, _ = self.emitir()

        with self.assertRaisesMessage(ValidationError, "motivo"):
            anular_indicacion(indicacion=emitida, usuario=self.usuario, motivo="   ")

        anulada = anular_indicacion(
            indicacion=emitida,
            usuario=self.usuario,
            motivo="Motivo ficticio suficiente para documentar la anulación.",
        )
        anulada.motivo_anulacion = "Intento de sobrescritura posterior"
        with self.assertRaisesMessage(ValidationError, "inmutable"):
            anulada.save(permitir_anulacion=True)

    def test_plantilla_versionada_no_modifica_borrador_existente(self):
        borrador = self.crear_borrador()
        contenido_original = borrador.contenido
        crear_version_plantilla(
            plantilla=self.plantilla,
            usuario=self.usuario,
            datos={
                "nombre": self.plantilla.nombre,
                "procedimiento": "Procedimiento v2",
                "titulo_documento": "Documento v2",
                "contenido": "Contenido ficticio de una versión posterior.",
                "pautas_alarma": "Pauta ficticia v2.",
                "recomendaciones_control": "Control ficticio v2.",
                "activa": True,
            },
            motivo="Nueva revisión ficticia aprobada para la prueba.",
        )

        borrador.refresh_from_db()
        self.assertEqual(borrador.plantilla_version, 1)
        self.assertEqual(borrador.contenido, contenido_original)

    def test_estado_emitido_exige_autor_pdf_hash_sello_y_snapshots(self):
        borrador = self.crear_borrador()
        borrador.estado = IndicacionPaciente.Estado.EMITIDA
        borrador.emitida_en = timezone.now()

        with self.assertRaises(ValidationError) as contexto:
            borrador.full_clean()

        errores = contexto.exception.error_dict
        self.assertIn("emitida_en", errores)
        self.assertIn("pdf", errores)
        self.assertIn("sello_integridad", errores)
        self.assertIn("snapshot_paciente", errores)
        self.assertIn("snapshot_profesional", errores)
        self.assertIn("snapshot_consultorio", errores)
        self.assertIn("snapshot_documento", errores)

    def test_emitida_no_puede_volver_a_borrador(self):
        emitida, _ = self.emitir()
        emitida.estado = IndicacionPaciente.Estado.BORRADOR

        with self.assertRaisesMessage(ValidationError, "solo puede anularse"):
            emitida.save()
