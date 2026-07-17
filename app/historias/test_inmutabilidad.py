import base64
import hashlib
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, time, timedelta
from html.parser import HTMLParser
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import DatabaseError, close_old_connections, connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import ProtectedError
from django.test import (
    RequestFactory,
    TestCase,
    TransactionTestCase,
    override_settings,
)
from django.urls import reverse
from django.utils import timezone

from pacientes.models import Paciente, PacienteOdontologo
from turnos.models import Odontologo
from usuarios.roles import ROL_ODONTOLOGO, ROL_RECEPCIONISTA

from .admin import (
    HistoriaClinicaAdmin,
    HistoriaClinicaEnmiendaAdmin,
    HistoriaClinicaVersionAdmin,
)
from .exports import (
    _copiar_adjunto,
    _detectar_mime_imagen_segura,
    _evaluar_candidato_inline,
    _nombre_exportado_seguro,
)
from .integrity import serializar_json_canonico
from .models import (
    AccesoClinicoAuditoria,
    HistoriaClinica,
    HistoriaClinicaAdjunto,
    HistoriaClinicaEnmienda,
    HistoriaClinicaVersion,
)
from .services import (
    HistoriaClinicaFinalizadaError,
    actualizar_historia_borrador,
    crear_enmienda_historia,
    crear_historia_borrador,
    finalizar_historia_clinica,
    verificar_integridad_historia,
)

CLAVE_PRUEBAS = "clave-clinica-de-pruebas-con-alta-entropia"
IMAGEN_JPEG_BASE64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////"
    "////////////////////////////////////////////////////////2wBDAf//"
    "//////////////////////////////////////////////////////////wAARCAAB"
    "AAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAA"
    "AAAAAAAAAAAA/9oADAMBAAIQAxAAAAH/AP/EABQQAQAAAAAAAAAAAAAAAAAAABD/"
    "2gAIAQEAAQUCf//EABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQMBAT8Bf//EABQR"
    "AQAAAAAAAAAAAAAAAAAAABD/2gAIAQIBAT8Bf//EABQQAQAAAAAAAAAAAAAAAAAA"
    "ABD/2gAIAQEABj8Cf//Z"
)
IMAGEN_JPEG_PRUEBA = base64.b64decode(IMAGEN_JPEG_BASE64 + ("=" * (-len(IMAGEN_JPEG_BASE64) % 4)))
IMAGEN_PNG_PRUEBA = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8A"
    "AQUBAScY42YAAAAASUVORK5CYII="
)
IMAGEN_WEBP_PRUEBA = base64.b64decode(
    "UklGRiIAAABXRUJQVlA4IBYAAAAwAQCdASoBAAEAAUAmJaQAA3AA/vuUAAA="
)
DOCUMENTO_PDF_PRUEBA = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF"


class ExportHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.articulos = []
        self.enlace_actual = ""
        self.enlaces = []
        self.imagenes = []
        self.metas = []

    def handle_starttag(self, tag, attrs):
        atributos = dict(attrs)
        if tag == "article":
            self.articulos.append([])
        elif tag == "a":
            self.enlace_actual = atributos.get("href", "")
            self.enlaces.append(atributos)
        elif tag == "img":
            imagen = {**atributos, "href": self.enlace_actual}
            self.imagenes.append(imagen)
            if self.articulos:
                self.articulos[-1].append(imagen)
        elif tag == "meta":
            self.metas.append(atributos)

    def handle_endtag(self, tag):
        if tag == "a":
            self.enlace_actual = ""


def crear_contexto_clinico(*, sufijo="base"):
    usuario = get_user_model().objects.create_user(
        username=f"odontologo.{sufijo}",
        first_name="Ana",
        last_name="Prueba",
    )
    grupo, _ = Group.objects.get_or_create(name=ROL_ODONTOLOGO)
    usuario.groups.add(grupo)
    odontologo = Odontologo.objects.create(
        usuario=usuario,
        matricula=f"MAT-{sufijo}",
        especialidad="Odontología general",
    )
    paciente = Paciente.objects.create(
        nombre="Paciente",
        apellido=f"Clínico {sufijo}",
        documento=f"77{sufijo.encode().hex()[:6]}"[:20],
    )
    PacienteOdontologo.objects.create(
        paciente=paciente,
        odontologo=odontologo,
        asignado_por=usuario,
        motivo="Atención clínica de prueba",
    )
    return usuario, odontologo, paciente


def datos_clinicos(**cambios):
    datos = {
        "fecha_hora_atencion": timezone.now() - timedelta(minutes=5),
        "motivo_consulta": "Control odontológico integral",
        "diagnostico": "Diagnóstico inicial",
        "tratamiento_realizado": "Tratamiento preventivo",
        "pieza_dental": "16",
        "observaciones": "Evolución estable",
        "proximo_control": timezone.localdate() + timedelta(days=30),
    }
    datos.update(cambios)
    return datos


def request_clinico(usuario, *, metodo="post", ruta="/historias/prueba/"):
    factory = RequestFactory()
    request = getattr(factory, metodo)(ruta)
    request.user = usuario
    request.session = {}
    return request


class MediaTemporalMixin:
    def setUp(self):
        super().setUp()
        self.media_dir = tempfile.TemporaryDirectory()
        self.media_override = override_settings(
            CLINICAL_INTEGRITY_HMAC_KEY=CLAVE_PRUEBAS,
            MEDIA_ROOT=self.media_dir.name,
            STORAGES={
                "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
            },
        )
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.addCleanup(self.media_dir.cleanup)


class HistoriaClinicaModeloInmutableTests(MediaTemporalMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.usuario, self.odontologo, self.paciente = crear_contexto_clinico(sufijo="modelo")

    def crear_borrador(self, **cambios):
        historia, _ = crear_historia_borrador(
            paciente=self.paciente,
            odontologo=self.odontologo,
            usuario=self.usuario,
            datos=datos_clinicos(**cambios),
        )
        return historia

    def test_historia_nueva_comienza_como_borrador_y_sin_finalizador(self):
        historia = self.crear_borrador()

        self.assertTrue(historia.borrador)
        self.assertFalse(historia.bloqueada_para_edicion)
        self.assertIsNone(historia.finalizada_en)
        self.assertIsNone(historia.finalizada_por)
        self.assertIsNone(historia.numero_asiento)

    def test_estado_finalizado_incompleto_no_es_valido(self):
        historia = HistoriaClinica(
            paciente=self.paciente,
            odontologo=self.odontologo,
            motivo_consulta="Registro incompleto",
            fecha_hora_atencion=timezone.now() - timedelta(minutes=1),
            borrador=False,
            bloqueada_para_edicion=True,
        )

        with self.assertRaises(ValidationError):
            historia.full_clean()

    def test_finalizada_sin_autor_solo_es_valida_si_proviene_de_legacy(self):
        datos = {
            "paciente": self.paciente,
            "odontologo": self.odontologo,
            "motivo_consulta": "Registro final de prueba",
            "fecha_hora_atencion": timezone.now() - timedelta(minutes=1),
            "borrador": False,
            "bloqueada_para_edicion": True,
            "finalizada_en": timezone.now(),
            "numero_asiento": 1,
        }
        nativa = HistoriaClinica(**datos)

        with self.assertRaises(ValidationError):
            nativa.full_clean()

        legacy = HistoriaClinica(**datos, migrada_desde_legacy=True)
        legacy.full_clean()

    def test_finalizada_queda_bloqueada_y_no_admite_edicion_o_borrado(self):
        historia = self.crear_borrador()
        historia, _ = finalizar_historia_clinica(
            historia=historia,
            usuario=self.usuario,
        )
        historia.diagnostico = "Contenido alterado"

        with self.assertRaises(ValidationError):
            historia.save()
        with self.assertRaises(ProtectedError):
            historia.delete()
        with self.assertRaises(ValidationError):
            HistoriaClinica.objects.filter(pk=historia.pk).update(diagnostico="Alteración masiva")
        with self.assertRaises(ProtectedError):
            HistoriaClinica.objects.filter(pk=historia.pk).delete()

    def test_folio_es_unico_por_paciente_y_reutilizable_en_otro_paciente(self):
        primera = self.crear_borrador(motivo_consulta="Primer asiento")
        primera, _ = finalizar_historia_clinica(
            historia=primera,
            usuario=self.usuario,
        )
        otra_usuario, otra_odontologa, otro_paciente = crear_contexto_clinico(sufijo="folio")
        segunda, _ = crear_historia_borrador(
            paciente=otro_paciente,
            odontologo=otra_odontologa,
            usuario=otra_usuario,
            datos=datos_clinicos(motivo_consulta="Otro paciente"),
        )
        segunda, _ = finalizar_historia_clinica(
            historia=segunda,
            usuario=otra_usuario,
        )

        self.assertEqual(primera.numero_asiento, 1)
        self.assertEqual(segunda.numero_asiento, 1)
        duplicada = HistoriaClinica(
            paciente=self.paciente,
            odontologo=self.odontologo,
            creado_por=self.usuario,
            actualizado_por=self.usuario,
            finalizada_por=self.usuario,
            fecha_hora_atencion=timezone.now() - timedelta(minutes=2),
            motivo_consulta="Folio repetido",
            borrador=False,
            bloqueada_para_edicion=True,
            finalizada_en=timezone.now(),
            numero_asiento=1,
        )
        with self.assertRaises(ValidationError):
            duplicada.save()

    def test_version_y_enmienda_son_append_only(self):
        historia = self.crear_borrador()
        version = historia.versiones.get(numero_version=1)
        historia, _ = finalizar_historia_clinica(
            historia=historia,
            usuario=self.usuario,
        )
        enmienda = crear_enmienda_historia(
            historia=historia,
            usuario=self.usuario,
            odontologo=self.odontologo,
            texto="Se aclara el alcance del control posterior.",
            motivo="Aclaración solicitada durante la revisión profesional.",
        )

        version.motivo = "Intento de cambio"
        with self.assertRaises(ValidationError):
            version.save()
        with self.assertRaises(ValidationError):
            HistoriaClinicaVersion.objects.filter(pk=version.pk).update(motivo="Intento")
        with self.assertRaises(ProtectedError):
            version.delete()
        enmienda.texto = "Intento de cambio"
        with self.assertRaises(ValidationError):
            enmienda.save()
        with self.assertRaises(ValidationError):
            HistoriaClinicaEnmienda.objects.filter(pk=enmienda.pk).update(texto="Intento")
        with self.assertRaises(ProtectedError):
            enmienda.delete()

    def test_enmienda_rechaza_texto_y_motivo_vacios(self):
        historia = self.crear_borrador()
        historia, version = finalizar_historia_clinica(
            historia=historia,
            usuario=self.usuario,
        )
        enmienda = HistoriaClinicaEnmienda(
            historia=historia,
            numero_enmienda=1,
            texto="   ",
            motivo="   ",
            odontologo=self.odontologo,
            creado_por=self.usuario,
            hash_anterior=version.hash_integridad,
            hash_integridad="a" * 64,
        )

        with self.assertRaises(ValidationError) as contexto:
            enmienda.full_clean()

        self.assertIn("texto", contexto.exception.message_dict)
        self.assertIn("motivo", contexto.exception.message_dict)

    def test_adjunto_calcula_sha256_y_no_se_puede_borrar(self):
        historia = self.crear_borrador()
        adjunto = HistoriaClinicaAdjunto.objects.create(
            historia=historia,
            archivo=SimpleUploadedFile(
                "radiografia.pdf",
                b"contenido-clinico",
                content_type="application/pdf",
            ),
            subido_por=self.usuario,
        )

        self.assertEqual(len(adjunto.sha256), 64)
        self.assertEqual(
            adjunto.sha256,
            "21b9357a127243a1ed888fa011a2f18fe8ef0d012015875c39e3bb371d51b9c6",
        )
        with self.assertRaises(ProtectedError):
            adjunto.delete()

    def test_adjunto_guardado_no_se_puede_reemplazar_ni_actualizar_en_masa(self):
        historia = self.crear_borrador()
        adjunto = HistoriaClinicaAdjunto.objects.create(
            historia=historia,
            archivo=SimpleUploadedFile(
                "original.pdf",
                b"contenido-original",
                content_type="application/pdf",
            ),
            subido_por=self.usuario,
        )
        nombre_original = adjunto.archivo.name
        hash_original = adjunto.sha256

        adjunto.archivo = SimpleUploadedFile(
            "reemplazo.pdf",
            b"contenido-reemplazado",
            content_type="application/pdf",
        )
        with self.assertRaises(ValidationError):
            adjunto.save()
        with self.assertRaises(ValidationError):
            HistoriaClinicaAdjunto.objects.filter(pk=adjunto.pk).update(descripcion="Alterado")

        adjunto.refresh_from_db()
        self.assertEqual(adjunto.archivo.name, nombre_original)
        self.assertEqual(adjunto.sha256, hash_original)

    def test_no_se_agregan_adjuntos_a_una_finalizada(self):
        historia = self.crear_borrador()
        historia, _ = finalizar_historia_clinica(
            historia=historia,
            usuario=self.usuario,
        )

        with self.assertRaises(ValidationError):
            HistoriaClinicaAdjunto.objects.create(
                historia=historia,
                archivo=SimpleUploadedFile(
                    "posterior.pdf",
                    b"posterior",
                    content_type="application/pdf",
                ),
                subido_por=self.usuario,
            )


class HistoriaClinicaServiciosTests(MediaTemporalMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.usuario, self.odontologo, self.paciente = crear_contexto_clinico(sufijo="servicios")

    def crear_borrador(self, **kwargs):
        return crear_historia_borrador(
            paciente=self.paciente,
            odontologo=self.odontologo,
            usuario=self.usuario,
            datos=datos_clinicos(),
            **kwargs,
        )[0]

    def test_crear_borrador_genera_version_uno_y_auditoria_neutral(self):
        request = request_clinico(self.usuario)
        historia, version = crear_historia_borrador(
            paciente=self.paciente,
            odontologo=self.odontologo,
            usuario=self.usuario,
            datos=datos_clinicos(),
            request=request,
        )

        self.assertEqual(version.numero_version, 1)
        self.assertEqual(version.snapshot["motivo_consulta"], historia.motivo_consulta)
        self.assertEqual(version.snapshot["creado_por_id"], self.usuario.pk)
        self.assertEqual(version.snapshot["actualizado_por_id"], self.usuario.pk)
        self.assertTrue(version.snapshot["creado_en"].endswith("Z"))
        self.assertTrue(version.snapshot["actualizado_en"].endswith("Z"))
        self.assertTrue(version.snapshot["hora_atencion_historica_disponible"])
        self.assertEqual(len(version.hash_integridad), 64)
        motivos_auditoria = " ".join(
            AccesoClinicoAuditoria.objects.values_list("motivo", flat=True)
        )
        self.assertNotIn(historia.diagnostico, motivos_auditoria)
        self.assertTrue(
            AccesoClinicoAuditoria.objects.filter(
                accion=AccesoClinicoAuditoria.Accion.CREAR_VERSION
            ).exists()
        )

    def test_serializacion_canonica_no_depende_del_orden_del_diccionario(self):
        izquierda = {"texto": "atención", "datos": {"b": 2, "a": 1}}
        derecha = {"datos": {"a": 1, "b": 2}, "texto": "atención"}

        self.assertEqual(
            serializar_json_canonico(izquierda),
            serializar_json_canonico(derecha),
        )

    @override_settings(CLINICAL_INTEGRITY_HMAC_KEY="")
    def test_falta_de_clave_revierte_la_creacion(self):
        with self.assertRaises(ImproperlyConfigured):
            self.crear_borrador()

        self.assertFalse(HistoriaClinica.objects.exists())
        self.assertFalse(HistoriaClinicaVersion.objects.exists())

    def test_editar_exige_motivo_y_genera_version_dos(self):
        historia = self.crear_borrador()

        with self.assertRaises(ValidationError):
            actualizar_historia_borrador(
                historia=historia,
                usuario=self.usuario,
                datos=datos_clinicos(diagnostico="Diagnóstico corregido"),
                motivo_cambio="cambio",
            )

        historia, version, cambio = actualizar_historia_borrador(
            historia=historia,
            usuario=self.usuario,
            datos=datos_clinicos(diagnostico="Diagnóstico corregido"),
            motivo_cambio="Se precisó el diagnóstico luego de revisar el estudio.",
        )

        self.assertTrue(cambio)
        self.assertEqual(version.numero_version, 2)
        self.assertEqual(
            version.hash_anterior, historia.versiones.get(numero_version=1).hash_integridad
        )
        self.assertEqual(historia.diagnostico, "Diagnóstico corregido")

    def test_edicion_sin_cambios_no_genera_version_inutil(self):
        datos = datos_clinicos()
        historia, _ = crear_historia_borrador(
            paciente=self.paciente,
            odontologo=self.odontologo,
            usuario=self.usuario,
            datos=datos,
        )

        _, version, cambio = actualizar_historia_borrador(
            historia=historia,
            usuario=self.usuario,
            datos=datos,
            motivo_cambio="Se revisó el asiento sin encontrar cambios necesarios.",
        )

        self.assertFalse(cambio)
        self.assertIsNone(version)
        self.assertEqual(historia.versiones.count(), 1)

    def test_finalizar_asigna_folio_version_final_y_no_permite_repetir(self):
        historia = self.crear_borrador()
        historia, version = finalizar_historia_clinica(
            historia=historia,
            usuario=self.usuario,
        )

        self.assertFalse(historia.borrador)
        self.assertTrue(historia.bloqueada_para_edicion)
        self.assertEqual(historia.numero_asiento, 1)
        self.assertEqual(version.snapshot["estado"], "finalizada")
        self.assertEqual(version.numero_version, 2)
        with self.assertRaises(HistoriaClinicaFinalizadaError):
            finalizar_historia_clinica(historia=historia, usuario=self.usuario)

    def test_folios_sucesivos_del_mismo_paciente(self):
        primera = self.crear_borrador()
        segunda = self.crear_borrador()
        primera, _ = finalizar_historia_clinica(
            historia=primera,
            usuario=self.usuario,
        )
        segunda, _ = finalizar_historia_clinica(
            historia=segunda,
            usuario=self.usuario,
        )

        self.assertEqual([primera.numero_asiento, segunda.numero_asiento], [1, 2])

    def test_enmienda_no_modifica_original_y_encadena_sello(self):
        historia = self.crear_borrador()
        original = historia.diagnostico
        historia, version_final = finalizar_historia_clinica(
            historia=historia,
            usuario=self.usuario,
        )
        enmienda = crear_enmienda_historia(
            historia=historia,
            usuario=self.usuario,
            odontologo=self.odontologo,
            texto="Se deja constancia de una aclaración posterior.",
            motivo="Aclaración profesional posterior a la finalización.",
        )
        historia.refresh_from_db()

        self.assertEqual(historia.diagnostico, original)
        self.assertEqual(enmienda.numero_enmienda, 1)
        self.assertEqual(enmienda.hash_anterior, version_final.hash_integridad)

    @unittest.skipIf(
        connection.vendor == "postgresql",
        "Los triggers PostgreSQL impiden preparar una alteración para esta prueba.",
    )
    def test_verificador_detecta_snapshot_y_enmienda_alterados(self):
        historia = self.crear_borrador()
        historia, _ = finalizar_historia_clinica(
            historia=historia,
            usuario=self.usuario,
        )
        enmienda = crear_enmienda_historia(
            historia=historia,
            usuario=self.usuario,
            odontologo=self.odontologo,
            texto="Aclaración posterior correctamente registrada.",
            motivo="Se agrega contexto clínico omitido en el cierre original.",
        )
        self.assertTrue(verificar_integridad_historia(historia)["valida"])

        version = historia.versiones.order_by("numero_version").first()
        snapshot_alterado = dict(version.snapshot)
        snapshot_alterado["diagnostico"] = "Alterado fuera del servicio"
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE historias_historiaclinicaversion SET snapshot = %s WHERE id = %s",
                [json.dumps(snapshot_alterado), version.pk],
            )
        resultado_version = verificar_integridad_historia(historia)
        self.assertFalse(resultado_version["valida"])
        self.assertTrue(any("versión" in error for error in resultado_version["errores"]))

        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE historias_historiaclinicaenmienda SET texto = %s WHERE id = %s",
                ["Enmienda alterada", enmienda.pk],
            )
        resultado_enmienda = verificar_integridad_historia(historia)
        self.assertFalse(resultado_enmienda["valida"])
        self.assertTrue(any("enmienda" in error for error in resultado_enmienda["errores"]))

    def test_cambios_de_identidad_relacionada_no_invalidan_el_snapshot_historico(self):
        historia = self.crear_borrador()
        historia, _ = finalizar_historia_clinica(
            historia=historia,
            usuario=self.usuario,
        )

        self.paciente.nombre = "Nombre corregido"
        self.paciente.documento = "70999111"
        self.paciente.save()
        self.usuario.first_name = "Nombre profesional actualizado"
        self.usuario.save(update_fields=["first_name"])
        self.odontologo.matricula = "MAT-ACTUALIZADA"
        self.odontologo.especialidad = "Endodoncia"
        self.odontologo.save()

        resultado = verificar_integridad_historia(historia)

        self.assertTrue(resultado["valida"], resultado["errores"])

    def test_error_de_sello_revierte_creacion_completa(self):
        with patch("historias.services.crear_sello_version", side_effect=RuntimeError("fallo")):
            with self.assertRaises(RuntimeError):
                crear_historia_borrador(
                    paciente=self.paciente,
                    odontologo=self.odontologo,
                    usuario=self.usuario,
                    datos=datos_clinicos(),
                )

        self.assertFalse(HistoriaClinica.objects.exists())
        self.assertFalse(HistoriaClinicaVersion.objects.exists())

    def test_usuario_fuera_de_alcance_no_puede_editar(self):
        historia = self.crear_borrador()
        otro_usuario, _, _ = crear_contexto_clinico(sufijo="sin-alcance")

        with self.assertRaises(PermissionDenied):
            actualizar_historia_borrador(
                historia=historia,
                usuario=otro_usuario,
                datos=datos_clinicos(diagnostico="No autorizado"),
                motivo_cambio="Intento de modificación fuera del alcance clínico.",
            )


class HistoriaClinicaVistasInmutablesTests(MediaTemporalMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.usuario, self.odontologo, self.paciente = crear_contexto_clinico(sufijo="vistas")
        self.client.force_login(self.usuario)

    def crear_borrador(self):
        return crear_historia_borrador(
            paciente=self.paciente,
            odontologo=self.odontologo,
            usuario=self.usuario,
            datos=datos_clinicos(),
        )[0]

    def test_detalle_muestra_acciones_segun_estado(self):
        historia = self.crear_borrador()
        response_borrador = self.client.get(
            reverse("historias:detalle", kwargs={"pk": historia.pk})
        )

        self.assertContains(response_borrador, "Editar borrador")
        self.assertContains(response_borrador, "Finalizar")
        self.assertNotContains(response_borrador, "Agregar enmienda")

        historia, _ = finalizar_historia_clinica(
            historia=historia,
            usuario=self.usuario,
        )
        response_final = self.client.get(reverse("historias:detalle", kwargs={"pk": historia.pk}))
        self.assertNotContains(response_final, "Editar borrador")
        self.assertContains(response_final, "Agregar enmienda")
        self.assertContains(response_final, "Registro finalizado e inmutable")

    def test_post_directo_edicion_finalizada_devuelve_403_y_audita(self):
        historia = self.crear_borrador()
        historia, _ = finalizar_historia_clinica(
            historia=historia,
            usuario=self.usuario,
        )

        response = self.client.post(
            reverse("historias:editar", kwargs={"pk": historia.pk}),
            {
                "fecha_hora_atencion": timezone.localtime(historia.fecha_hora_atencion).strftime(
                    "%Y-%m-%dT%H:%M"
                ),
                "motivo_consulta": "Intento directo",
                "diagnostico": "No debe guardarse",
                "tratamiento_realizado": "",
                "pieza_dental": "",
                "observaciones": "",
                "proximo_control": "",
                "motivo_cambio": "Intento directo sobre registro finalizado.",
            },
        )

        self.assertEqual(response.status_code, 403)
        historia.refresh_from_db()
        self.assertNotEqual(historia.diagnostico, "No debe guardarse")
        self.assertTrue(
            AccesoClinicoAuditoria.objects.filter(
                accion=AccesoClinicoAuditoria.Accion.INTENTO_EDITAR_FINALIZADA,
                resultado=AccesoClinicoAuditoria.Resultado.DENEGADO,
            ).exists()
        )

    def test_finalizar_y_crear_enmienda_desde_vistas(self):
        historia = self.crear_borrador()
        response_finalizar = self.client.post(
            reverse("historias:finalizar", kwargs={"pk": historia.pk}),
            {"confirmar": "on"},
        )
        historia.refresh_from_db()

        self.assertRedirects(
            response_finalizar,
            reverse("historias:detalle", kwargs={"pk": historia.pk}),
        )
        self.assertFalse(historia.borrador)
        response_enmienda = self.client.post(
            reverse("historias:crear_enmienda", kwargs={"pk": historia.pk}),
            {
                "texto": "Aclaración incorporada desde la interfaz clínica.",
                "motivo": "Se completa información luego de revisar el asiento.",
            },
        )
        self.assertRedirects(
            response_enmienda,
            reverse("historias:detalle", kwargs={"pk": historia.pk}),
        )
        self.assertEqual(historia.enmiendas.count(), 1)

    def test_lista_muestra_folio_estado_versiones_y_enmiendas(self):
        historia = self.crear_borrador()
        historia, _ = finalizar_historia_clinica(
            historia=historia,
            usuario=self.usuario,
        )
        crear_enmienda_historia(
            historia=historia,
            usuario=self.usuario,
            odontologo=self.odontologo,
            texto="Aclaración para el listado de prueba.",
            motivo="Se prueba la trazabilidad visible en el listado.",
        )

        response = self.client.get(
            reverse("historias:lista_paciente", kwargs={"paciente_pk": self.paciente.pk})
        )
        self.assertContains(response, "Asiento 1")
        self.assertContains(response, "Finalizada")
        self.assertContains(response, "2 versiones")
        self.assertContains(response, "1 enmienda")

    def test_version_y_enmienda_fuera_de_alcance_responden_404(self):
        historia = self.crear_borrador()
        version = historia.versiones.first()
        historia, _ = finalizar_historia_clinica(
            historia=historia,
            usuario=self.usuario,
        )
        enmienda = crear_enmienda_historia(
            historia=historia,
            usuario=self.usuario,
            odontologo=self.odontologo,
            texto="Aclaración privada fuera de alcance.",
            motivo="Se valida autorización por objeto en la vista de enmienda.",
        )
        otro_usuario, _, _ = crear_contexto_clinico(sufijo="idor")
        self.client.force_login(otro_usuario)

        self.assertEqual(
            self.client.get(
                reverse("historias:detalle_version", kwargs={"pk": version.pk})
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse("historias:detalle_enmienda", kwargs={"pk": enmienda.pk})
            ).status_code,
            404,
        )

    def test_recepcion_no_puede_crear_enmienda(self):
        historia = self.crear_borrador()
        historia, _ = finalizar_historia_clinica(
            historia=historia,
            usuario=self.usuario,
        )
        recepcion = get_user_model().objects.create_user(username="recepcion.enmienda")
        grupo, _ = Group.objects.get_or_create(name=ROL_RECEPCIONISTA)
        recepcion.groups.add(grupo)
        self.client.force_login(recepcion)

        response = self.client.post(
            reverse("historias:crear_enmienda", kwargs={"pk": historia.pk}),
            {"texto": "No autorizado", "motivo": "Intento administrativo no autorizado."},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(historia.enmiendas.exists())


class HistoriaClinicaExportacionTests(MediaTemporalMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.usuario, self.odontologo, self.paciente = crear_contexto_clinico(sufijo="export")
        self.client.force_login(self.usuario)

    def crear_historia_completa(self):
        historia, _ = crear_historia_borrador(
            paciente=self.paciente,
            odontologo=self.odontologo,
            usuario=self.usuario,
            datos=datos_clinicos(),
            adjuntos=[
                SimpleUploadedFile(
                    "fotografia-inicial.jpg",
                    IMAGEN_JPEG_PRUEBA,
                    content_type="image/jpeg",
                ),
                SimpleUploadedFile(
                    "informe-inicial.pdf",
                    DOCUMENTO_PDF_PRUEBA,
                    content_type="application/pdf",
                ),
            ],
        )
        historia, _, _ = actualizar_historia_borrador(
            historia=historia,
            usuario=self.usuario,
            datos=datos_clinicos(diagnostico="Diagnóstico revisado"),
            motivo_cambio="Se revisó el diagnóstico con el estudio disponible.",
            adjuntos=[
                SimpleUploadedFile(
                    "control.png",
                    IMAGEN_PNG_PRUEBA,
                    content_type="image/png",
                )
            ],
        )
        historia, _ = finalizar_historia_clinica(
            historia=historia,
            usuario=self.usuario,
        )
        for numero in (1, 2):
            crear_enmienda_historia(
                historia=historia,
                usuario=self.usuario,
                odontologo=self.odontologo,
                texto=f"Aclaración posterior número {numero}.",
                motivo=f"Motivo suficientemente detallado para enmienda {numero}.",
            )
        return historia

    def exportar_y_leer(self, historia, *, motivo="solicitud_paciente"):
        response = self.client.post(
            reverse("historias:exportar", kwargs={"pk": historia.pk}),
            {"motivo": motivo},
        )
        self.assertEqual(response.status_code, 200)
        contenido_zip = b"".join(response.streaming_content)
        with ZipFile(BytesIO(contenido_zip)) as archivo_zip:
            nombres = archivo_zip.namelist()
            manifest = json.loads(archivo_zip.read("manifest.json"))
            html = archivo_zip.read("historia_clinica.html").decode("utf-8")
            archivos = {
                adjunto["ruta"]: archivo_zip.read(adjunto["ruta"])
                for adjunto in manifest["adjuntos"]
            }
        return response, nombres, manifest, html, archivos

    def test_zip_incluye_manifest_html_versiones_enmiendas_y_adjuntos(self):
        historia = self.crear_historia_completa()
        usuario_ajeno, odontologo_ajeno, paciente_ajeno = crear_contexto_clinico(sufijo="ajeno")
        historia_ajena, _ = crear_historia_borrador(
            paciente=paciente_ajeno,
            odontologo=odontologo_ajeno,
            usuario=usuario_ajeno,
            datos=datos_clinicos(motivo_consulta="Contenido de otro paciente"),
        )
        estado_antes = (
            historia.motivo_consulta,
            historia.diagnostico,
            historia.actualizado_en,
            historia.borrador,
            historia.numero_asiento,
        )
        versiones_antes = list(historia.versiones.values_list("pk", "hash_integridad"))
        enmiendas_antes = list(historia.enmiendas.values_list("pk", "hash_integridad"))
        adjuntos_antes = list(historia.adjuntos.values_list("pk", "sha256"))
        storage = HistoriaClinicaAdjunto._meta.get_field("archivo").storage

        with patch.object(storage, "open", wraps=storage.open) as abrir_storage:
            response = self.client.post(
                reverse("historias:exportar", kwargs={"pk": historia.pk}),
                {"motivo": "solicitud_paciente"},
            )
            contenido_zip = b"".join(response.streaming_content)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertNotIn(self.paciente.documento, response["Content-Disposition"])
        self.assertRegex(
            response["Content-Disposition"],
            r'^attachment; filename="historia-clinica-paciente-\d+-\d{8}T\d{6}Z\.zip"$',
        )
        self.assertTrue(response.streaming)
        self.assertEqual(abrir_storage.call_count, 3)
        with ZipFile(BytesIO(contenido_zip)) as archivo_zip:
            nombres = archivo_zip.namelist()
            self.assertIn("manifest.json", nombres)
            self.assertIn("historia_clinica.html", nombres)
            self.assertEqual(
                len([n for n in nombres if n.startswith("versiones/") and n.endswith(".json")]), 3
            )
            self.assertEqual(
                len([n for n in nombres if n.startswith("enmiendas/") and n.endswith(".json")]), 2
            )
            self.assertEqual(
                len([n for n in nombres if n.startswith("adjuntos/") and not n.endswith("/")]), 3
            )
            self.assertTrue(any(nombre.endswith("fotografia-inicial.jpg") for nombre in nombres))
            self.assertTrue(any(nombre.endswith("control.png") for nombre in nombres))
            self.assertTrue(any(nombre.endswith("informe-inicial.pdf") for nombre in nombres))
            manifest = json.loads(archivo_zip.read("manifest.json"))
            html = archivo_zip.read("historia_clinica.html").decode("utf-8")
            archivos_exportados = {
                item["ruta"]: archivo_zip.read(item["ruta"]) for item in manifest["adjuntos"]
            }
            contenido_textual = b"\n".join(
                archivo_zip.read(nombre)
                for nombre in nombres
                if nombre.endswith((".json", ".html"))
            ).lower()

        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["asientos"][0]["numero_asiento"], 1)
        self.assertEqual(
            [version["numero"] for version in manifest["asientos"][0]["versiones"]],
            [1, 2, 3],
        )
        self.assertEqual(
            [enmienda["numero"] for enmienda in manifest["asientos"][0]["enmiendas"]],
            [1, 2],
        )
        self.assertNotIn(
            historia_ajena.pk,
            [asiento["historia_id"] for asiento in manifest["asientos"]],
        )
        self.assertEqual(manifest["asientos"][0]["adjuntos"], manifest["adjuntos"])
        self.assertTrue(all(adjunto["sha256"] for adjunto in manifest["adjuntos"]))
        for adjunto in manifest["adjuntos"]:
            self.assertEqual(
                hashlib.sha256(archivos_exportados[adjunto["ruta"]]).hexdigest(),
                adjunto["sha256"],
            )

        parser = ExportHTMLParser()
        parser.feed(html)
        self.assertEqual(len(parser.articulos), 1)
        self.assertEqual(len(parser.imagenes), 2)
        self.assertEqual(len(parser.articulos[0]), 2)
        adjuntos_por_ruta = {adjunto["ruta"]: adjunto for adjunto in manifest["adjuntos"]}
        for imagen in parser.imagenes:
            ruta_original = imagen["href"]
            metadatos = adjuntos_por_ruta[ruta_original]
            prefijo, contenido_base64 = imagen["src"].split(",", 1)
            contenido_inline = base64.b64decode(contenido_base64, validate=True)

            self.assertEqual(prefijo, f"data:{metadatos['content_type']};base64")
            self.assertEqual(contenido_inline, archivos_exportados[ruta_original])
            self.assertEqual(hashlib.sha256(contenido_inline).hexdigest(), metadatos["sha256"])
            self.assertTrue(metadatos["vista_previa_inline"])
            self.assertIsNone(metadatos["motivo_sin_vista_previa"])
            self.assertEqual(imagen["alt"], "Adjunto clínico del asiento 1")

        pdf = next(
            adjunto
            for adjunto in manifest["adjuntos"]
            if adjunto["content_type"] == "application/pdf"
        )
        self.assertFalse(pdf["vista_previa_inline"])
        self.assertEqual(pdf["motivo_sin_vista_previa"], "tipo_no_compatible")
        self.assertNotIn(pdf["ruta"], [imagen["href"] for imagen in parser.imagenes])
        self.assertIn(pdf["ruta"], [enlace.get("href") for enlace in parser.enlaces])
        csp = next(
            meta["content"]
            for meta in parser.metas
            if meta.get("http-equiv") == "Content-Security-Policy"
        )
        self.assertIn("default-src 'none'", csp)
        self.assertIn("img-src data:", csp)
        self.assertIn("script-src 'none'", csp)
        self.assertIn("object-src 'none'", csp)
        self.assertIn("Control odontológico integral", html)
        self.assertIn("Aclaración posterior número 1", html)
        self.assertIn("Aclaración posterior número 2", html)
        serializado = json.dumps(manifest).lower()
        self.assertNotIn("base64", serializado)
        self.assertNotIn("data:image", serializado)
        self.assertNotIn("authorization", serializado)
        self.assertNotIn("service_role", serializado)
        self.assertNotIn(b"authorization", contenido_textual)
        self.assertNotIn(b"service_role", contenido_textual)
        self.assertNotIn(b"supabase", contenido_textual)
        self.assertNotIn(b"https://", contenido_textual)
        self.assertNotIn(b"http://", contenido_textual)
        self.assertNotIn("<script", html.lower())
        self.assertNotIn("<iframe", html.lower())
        self.assertNotIn("<object", html.lower())

        historia.refresh_from_db()
        self.assertEqual(
            (
                historia.motivo_consulta,
                historia.diagnostico,
                historia.actualizado_en,
                historia.borrador,
                historia.numero_asiento,
            ),
            estado_antes,
        )
        self.assertEqual(
            list(historia.versiones.values_list("pk", "hash_integridad")),
            versiones_antes,
        )
        self.assertEqual(
            list(historia.enmiendas.values_list("pk", "hash_integridad")),
            enmiendas_antes,
        )
        self.assertEqual(
            list(historia.adjuntos.values_list("pk", "sha256")),
            adjuntos_antes,
        )
        self.assertTrue(
            AccesoClinicoAuditoria.objects.filter(
                accion=AccesoClinicoAuditoria.Accion.EXPORTAR_HISTORIA,
                resultado=AccesoClinicoAuditoria.Resultado.PERMITIDO,
            ).exists()
        )

    def test_webp_compatible_se_incrusta_y_recupera_los_bytes_originales(self):
        historia, _ = crear_historia_borrador(
            paciente=self.paciente,
            odontologo=self.odontologo,
            usuario=self.usuario,
            datos=datos_clinicos(),
            adjuntos=[
                SimpleUploadedFile(
                    "control.webp",
                    IMAGEN_WEBP_PRUEBA,
                    content_type="image/webp",
                )
            ],
        )
        historia, _ = finalizar_historia_clinica(historia=historia, usuario=self.usuario)

        _, _, manifest, html, archivos = self.exportar_y_leer(historia)
        parser = ExportHTMLParser()
        parser.feed(html)

        self.assertEqual(len(parser.imagenes), 1)
        imagen = parser.imagenes[0]
        prefijo, contenido_base64 = imagen["src"].split(",", 1)
        contenido = base64.b64decode(contenido_base64, validate=True)
        metadatos = manifest["adjuntos"][0]
        self.assertEqual(prefijo, "data:image/webp;base64")
        self.assertEqual(contenido, IMAGEN_WEBP_PRUEBA)
        self.assertEqual(contenido, archivos[metadatos["ruta"]])
        self.assertEqual(hashlib.sha256(contenido).hexdigest(), metadatos["sha256"])
        self.assertTrue(metadatos["vista_previa_inline"])

    def test_imagen_que_supera_limite_conserva_original_y_usa_fallback(self):
        historia, _ = crear_historia_borrador(
            paciente=self.paciente,
            odontologo=self.odontologo,
            usuario=self.usuario,
            datos=datos_clinicos(),
            adjuntos=[
                SimpleUploadedFile(
                    "imagen-grande.png",
                    IMAGEN_PNG_PRUEBA,
                    content_type="image/png",
                )
            ],
        )
        historia, _ = finalizar_historia_clinica(historia=historia, usuario=self.usuario)

        with patch("historias.exports.CLINICAL_EXPORT_INLINE_IMAGE_MAX_BYTES", 8):
            _, _, manifest, html, archivos = self.exportar_y_leer(historia)

        parser = ExportHTMLParser()
        parser.feed(html)
        metadatos = manifest["adjuntos"][0]
        self.assertEqual(parser.imagenes, [])
        self.assertFalse(metadatos["vista_previa_inline"])
        self.assertEqual(metadatos["motivo_sin_vista_previa"], "supera_limite")
        self.assertEqual(archivos[metadatos["ruta"]], IMAGEN_PNG_PRUEBA)
        self.assertIn("Vista previa no incluida por el tamaño del archivo", html)
        self.assertIn(metadatos["ruta"], [enlace.get("href") for enlace in parser.enlaces])

    def test_svg_y_mime_no_coincidente_no_son_inline(self):
        historia, _ = crear_historia_borrador(
            paciente=self.paciente,
            odontologo=self.odontologo,
            usuario=self.usuario,
            datos=datos_clinicos(),
        )
        casos = (
            (901, "vector.svg", b"<svg><script/></svg>", "image/svg+xml", "tipo_no_compatible"),
            (902, "discordante.png", IMAGEN_PNG_PRUEBA, "image/jpeg", "tipo_no_coincidente"),
        )
        buffer_zip = BytesIO()
        resultados = []
        with ZipFile(buffer_zip, "w") as archivo_zip:
            for adjunto_id, nombre, contenido, content_type, _ in casos:
                adjunto = HistoriaClinicaAdjunto(
                    historia=historia,
                    archivo=SimpleUploadedFile(nombre, contenido, content_type=content_type),
                    content_type=content_type,
                    tamano_bytes=len(contenido),
                    sha256=hashlib.sha256(contenido).hexdigest(),
                )
                adjunto.pk = adjunto_id
                resultados.append(
                    _copiar_adjunto(
                        archivo_zip,
                        adjunto=adjunto,
                        clave_asiento="borrador-prueba",
                    )
                )

        with ZipFile(BytesIO(buffer_zip.getvalue())) as archivo_zip:
            nombres_zip = archivo_zip.namelist()
        for resultado, caso in zip(resultados, casos, strict=True):
            self.assertFalse(resultado.puede_mostrarse_inline)
            self.assertEqual(resultado.data_uri, "")
            self.assertEqual(resultado.motivo_sin_vista_previa, caso[4])
            self.assertIn(resultado.ruta_zip, nombres_zip)

        self.assertEqual(_detectar_mime_imagen_segura(b"<svg><script/></svg>"), "")
        self.assertEqual(
            _evaluar_candidato_inline("image/svg+xml", ".svg"),
            (False, "tipo_no_compatible"),
        )
        self.assertEqual(
            _evaluar_candidato_inline("image/jpeg", ".png"),
            (False, "tipo_no_coincidente"),
        )
        self.assertEqual(
            _evaluar_candidato_inline("image/gif", ".gif"),
            (False, "tipo_no_compatible"),
        )

    def test_nombres_exportados_son_seguros_unicos_y_acotados(self):
        nombre_malicioso = '../../<script>alert("x")</script>.jpg'
        primero = _nombre_exportado_seguro(nombre_malicioso, 7)
        segundo = _nombre_exportado_seguro(nombre_malicioso, 8)
        sin_extension = _nombre_exportado_seguro("archivo-clinico", 9)
        muy_largo = _nombre_exportado_seguro(("a" * 400) + ".png", 10)

        self.assertNotEqual(primero, segundo)
        self.assertTrue(primero.startswith("adjunto-00000007-"))
        self.assertTrue(primero.endswith(".jpg"))
        self.assertTrue(sin_extension.endswith(".bin"))
        self.assertLessEqual(len(muy_largo), 112)
        for nombre in (primero, segundo, sin_extension, muy_largo):
            self.assertNotIn("..", nombre)
            self.assertNotIn("/", nombre)
            self.assertNotIn("\\", nombre)
            self.assertNotIn('"', nombre)
            self.assertNotIn("<", nombre)
            self.assertNotIn(">", nombre)

    def test_adjuntos_con_el_mismo_nombre_no_colisionan_en_el_zip(self):
        historia, _ = crear_historia_borrador(
            paciente=self.paciente,
            odontologo=self.odontologo,
            usuario=self.usuario,
            datos=datos_clinicos(),
            adjuntos=[
                SimpleUploadedFile("misma.png", IMAGEN_PNG_PRUEBA, content_type="image/png"),
                SimpleUploadedFile("misma.png", IMAGEN_PNG_PRUEBA, content_type="image/png"),
            ],
        )
        historia, _ = finalizar_historia_clinica(historia=historia, usuario=self.usuario)

        _, nombres, manifest, _, _ = self.exportar_y_leer(historia)
        rutas = [adjunto["ruta"] for adjunto in manifest["adjuntos"]]

        self.assertEqual(
            [adjunto["nombre_original"] for adjunto in manifest["adjuntos"]],
            [
                "misma.png",
                "misma.png",
            ],
        )
        self.assertEqual(len(rutas), len(set(rutas)))
        self.assertTrue(all(ruta in nombres for ruta in rutas))

    def test_html_escapa_contenido_clinico_y_no_ejecuta_etiquetas(self):
        historia, _ = crear_historia_borrador(
            paciente=self.paciente,
            odontologo=self.odontologo,
            usuario=self.usuario,
            datos=datos_clinicos(
                motivo_consulta='<script>alert("dato")</script>',
                observaciones='<img src="https://ejemplo.test/rastreo" onerror="alert(1)">',
            ),
        )
        historia, _ = finalizar_historia_clinica(historia=historia, usuario=self.usuario)

        _, _, _, html, _ = self.exportar_y_leer(historia)

        self.assertNotIn('<script>alert("dato")</script>', html)
        self.assertNotIn('<img src="https://ejemplo.test/rastreo"', html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;img", html)

    def test_cada_imagen_permanece_en_el_asiento_que_le_corresponde(self):
        historia_uno, _ = crear_historia_borrador(
            paciente=self.paciente,
            odontologo=self.odontologo,
            usuario=self.usuario,
            datos=datos_clinicos(motivo_consulta="Primer asiento"),
            adjuntos=[
                SimpleUploadedFile(
                    "primera.jpg",
                    IMAGEN_JPEG_PRUEBA,
                    content_type="image/jpeg",
                )
            ],
        )
        historia_uno, _ = finalizar_historia_clinica(
            historia=historia_uno,
            usuario=self.usuario,
        )
        historia_dos, _ = crear_historia_borrador(
            paciente=self.paciente,
            odontologo=self.odontologo,
            usuario=self.usuario,
            datos=datos_clinicos(motivo_consulta="Segundo asiento"),
            adjuntos=[
                SimpleUploadedFile(
                    "segunda.png",
                    IMAGEN_PNG_PRUEBA,
                    content_type="image/png",
                )
            ],
        )
        finalizar_historia_clinica(historia=historia_dos, usuario=self.usuario)

        _, _, manifest, html, _ = self.exportar_y_leer(historia_uno)
        parser = ExportHTMLParser()
        parser.feed(html)

        self.assertEqual(len(parser.articulos), 2)
        self.assertEqual([len(imagenes) for imagenes in parser.articulos], [1, 1])
        adjuntos_por_historia = {
            adjunto["historia_id"]: adjunto for adjunto in manifest["adjuntos"]
        }
        self.assertEqual(
            parser.articulos[0][0]["href"],
            adjuntos_por_historia[historia_uno.pk]["ruta"],
        )
        self.assertEqual(
            parser.articulos[1][0]["href"],
            adjuntos_por_historia[historia_dos.pk]["ruta"],
        )

    def test_adjunto_faltante_cancela_exportacion_sin_exponer_ruta(self):
        historia = self.crear_historia_completa()
        adjunto = historia.adjuntos.order_by("pk").first()
        ruta_interna = Path(adjunto.archivo.path)
        ruta_interna.unlink()

        response = self.client.post(
            reverse("historias:exportar", kwargs={"pk": historia.pk}),
            {"motivo": "auditoria"},
        )

        self.assertEqual(response.status_code, 503)
        contenido = response.content.decode("utf-8")
        self.assertIn("No se pudo generar la exportación", contenido)
        self.assertNotIn(str(ruta_interna), contenido)
        self.assertNotIn(adjunto.archivo.name, contenido)
        auditoria = AccesoClinicoAuditoria.objects.filter(
            accion=AccesoClinicoAuditoria.Accion.EXPORTAR_HISTORIA,
            resultado=AccesoClinicoAuditoria.Resultado.ERROR,
        ).latest("creado_en")
        self.assertNotIn(str(ruta_interna), auditoria.motivo)
        self.assertNotIn(adjunto.archivo.name, auditoria.motivo)

    def test_recepcion_no_puede_exportar_historia_clinica(self):
        historia = self.crear_historia_completa()
        recepcion = get_user_model().objects.create_user(username="recepcion.exportacion")
        grupo, _ = Group.objects.get_or_create(name=ROL_RECEPCIONISTA)
        recepcion.groups.add(grupo)
        self.client.force_login(recepcion)

        response = self.client.post(
            reverse("historias:exportar", kwargs={"pk": historia.pk}),
            {"motivo": "solicitud_paciente"},
        )

        self.assertEqual(response.status_code, 403)

    def test_exportacion_fuera_de_alcance_responde_404(self):
        historia = self.crear_historia_completa()
        usuario_ajeno, _, _ = crear_contexto_clinico(sufijo="idor-export")
        self.client.force_login(usuario_ajeno)
        url = reverse("historias:exportar", kwargs={"pk": historia.pk})

        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(
            self.client.post(url, {"motivo": "solicitud_paciente"}).status_code,
            404,
        )

    def test_exportacion_exige_motivo(self):
        historia = self.crear_historia_completa()

        response = self.client.post(
            reverse("historias:exportar", kwargs={"pk": historia.pk}),
            {},
        )

        self.assertEqual(response.status_code, 400)
        self.assertNotEqual(response["Content-Type"], "application/zip")

    def test_fallo_de_exportacion_queda_auditado_sin_detalle_de_excepcion(self):
        historia = self.crear_historia_completa()
        with patch("historias.exports._crear_zip", side_effect=OSError("token-secreto")):
            response = self.client.post(
                reverse("historias:exportar", kwargs={"pk": historia.pk}),
                {"motivo": "auditoria"},
            )

        self.assertEqual(response.status_code, 503)
        auditoria = AccesoClinicoAuditoria.objects.filter(
            accion=AccesoClinicoAuditoria.Accion.EXPORTAR_HISTORIA,
            resultado=AccesoClinicoAuditoria.Resultado.ERROR,
        ).latest("creado_en")
        self.assertNotIn("token-secreto", auditoria.motivo)


class HistoriaClinicaAdminYComandosTests(MediaTemporalMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.usuario, self.odontologo, self.paciente = crear_contexto_clinico(sufijo="admin")
        self.historia, _ = crear_historia_borrador(
            paciente=self.paciente,
            odontologo=self.odontologo,
            usuario=self.usuario,
            datos=datos_clinicos(),
        )

    def test_admin_no_permite_cambiar_agregar_o_borrar(self):
        request = request_clinico(self.usuario, metodo="get", ruta="/admin/historias/")
        historia_admin = HistoriaClinicaAdmin(HistoriaClinica, admin.site)
        version_admin = HistoriaClinicaVersionAdmin(HistoriaClinicaVersion, admin.site)
        enmienda_admin = HistoriaClinicaEnmiendaAdmin(
            HistoriaClinicaEnmienda,
            admin.site,
        )

        for model_admin in (historia_admin, version_admin, enmienda_admin):
            self.assertFalse(model_admin.has_add_permission(request))
            self.assertFalse(model_admin.has_change_permission(request, self.historia))
            self.assertFalse(model_admin.has_delete_permission(request, self.historia))

    def test_consultas_autorizadas_desde_admin_quedan_auditadas(self):
        self.usuario.is_staff = True
        self.usuario.save(update_fields=["is_staff"])
        self.client.force_login(self.usuario)

        response_lista = self.client.get(reverse("admin:historias_historiaclinica_changelist"))
        response_detalle = self.client.get(
            reverse(
                "admin:historias_historiaclinica_change",
                args=[self.historia.pk],
            )
        )

        self.assertEqual(response_lista.status_code, 200)
        self.assertEqual(response_detalle.status_code, 200)
        eventos = AccesoClinicoAuditoria.objects.filter(
            usuario=self.usuario,
            historia=self.historia,
            motivo="Registro clínico consultado desde administración.",
        )
        self.assertTrue(eventos.filter(accion=AccesoClinicoAuditoria.Accion.VER_HISTORIA).exists())
        self.assertTrue(
            eventos.filter(accion=AccesoClinicoAuditoria.Accion.VER_DETALLE_HISTORIA).exists()
        )
        self.assertTrue(all(evento.ruta.startswith("/admin/") for evento in eventos))

    @unittest.skipIf(
        connection.vendor == "postgresql",
        "La preparación legacy controlada usa SQL que los triggers bloquean.",
    )
    def test_comandos_backfill_inicializacion_y_verificacion(self):
        adjunto = HistoriaClinicaAdjunto.objects.create(
            historia=self.historia,
            archivo=SimpleUploadedFile(
                "legacy.pdf",
                b"contenido-legacy",
                content_type="application/pdf",
            ),
            subido_por=self.usuario,
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE historias_historiaclinicaadjunto SET sha256 = %s WHERE id = %s",
                ["", adjunto.pk],
            )
        self.historia, _ = finalizar_historia_clinica(
            historia=self.historia,
            usuario=self.usuario,
        )
        self.historia.versiones.all()
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM historias_historiaclinicaversion WHERE historia_id = %s",
                [self.historia.pk],
            )
            cursor.execute(
                "UPDATE historias_historiaclinica SET migrada_desde_legacy = %s WHERE id = %s",
                [True, self.historia.pk],
            )

        salida_hash = StringIO()
        call_command(
            "completar_hashes_adjuntos_legacy",
            historia=self.historia.pk,
            stdout=salida_hash,
        )
        adjunto.refresh_from_db()
        self.assertEqual(len(adjunto.sha256), 64)

        salida_init = StringIO()
        call_command(
            "inicializar_integridad_historias_legacy",
            historia=self.historia.pk,
            stdout=salida_init,
        )
        self.assertEqual(self.historia.versiones.count(), 1)
        self.assertFalse(self.historia.versiones.first().snapshot["trazabilidad_previa_disponible"])
        self.assertFalse(
            self.historia.versiones.first().snapshot["hora_atencion_historica_disponible"]
        )

        salida_verificar = StringIO()
        call_command(
            "verificar_integridad_historias",
            paciente=self.paciente.pk,
            historia=self.historia.pk,
            verificar_adjuntos=True,
            fallar_si_hay_errores=True,
            stdout=salida_verificar,
        )
        self.assertIn("válida", salida_verificar.getvalue())
        self.assertIn("Historias verificadas: 1", salida_verificar.getvalue())
        self.assertIn("Versiones verificadas: 1", salida_verificar.getvalue())
        self.assertIn("Enmiendas verificadas: 0", salida_verificar.getvalue())
        self.assertIn("Errores de integridad: 0", salida_verificar.getvalue())


class HistoriaClinicaMigracionLegacyTests(TransactionTestCase):
    migrate_from = [("historias", "0004_accesoclinicoauditoria")]
    migrate_to = [("historias", "0006_migrar_historias_legacy")]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        apps = self.executor.loader.project_state(self.migrate_from).apps
        User = apps.get_model("auth", "User")
        PacienteLegacy = apps.get_model("pacientes", "Paciente")
        OdontologoLegacy = apps.get_model("turnos", "Odontologo")
        HistoriaLegacy = apps.get_model("historias", "HistoriaClinica")
        usuario = User.objects.create(username="autor.legacy")
        odontologo = OdontologoLegacy.objects.create(
            usuario_id=usuario.pk,
            matricula="LEGACY-1",
        )
        paciente = PacienteLegacy.objects.create(
            nombre="Paciente",
            apellido="Legacy",
            documento="79999111",
        )
        self.primera_id = HistoriaLegacy.objects.create(
            paciente_id=paciente.pk,
            odontologo_id=odontologo.pk,
            fecha=date(2024, 1, 10),
            motivo_consulta="Contenido histórico uno",
        ).pk
        self.segunda_id = HistoriaLegacy.objects.create(
            paciente_id=paciente.pk,
            odontologo_id=odontologo.pk,
            creado_por_id=usuario.pk,
            actualizado_por_id=usuario.pk,
            fecha=date(2024, 2, 10),
            motivo_consulta="Contenido histórico dos",
        ).pk

        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_to)
        self.apps = self.executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_migracion_conserva_contenido_folia_y_no_inventa_autor(self):
        HistoriaMigrada = self.apps.get_model("historias", "HistoriaClinica")
        primera = HistoriaMigrada.objects.get(pk=self.primera_id)
        segunda = HistoriaMigrada.objects.get(pk=self.segunda_id)

        self.assertEqual(primera.motivo_consulta, "Contenido histórico uno")
        self.assertEqual(segunda.motivo_consulta, "Contenido histórico dos")
        self.assertEqual([primera.numero_asiento, segunda.numero_asiento], [1, 2])
        self.assertFalse(primera.borrador)
        self.assertTrue(primera.bloqueada_para_edicion)
        self.assertTrue(primera.migrada_desde_legacy)
        self.assertIsNone(primera.finalizada_por_id)
        self.assertIsNotNone(segunda.finalizada_por_id)
        self.assertEqual(
            timezone.localtime(primera.fecha_hora_atencion).time(),
            time.min,
        )


@unittest.skipUnless(
    connection.vendor == "postgresql",
    "La concurrencia y los triggers se prueban únicamente con PostgreSQL.",
)
class HistoriaClinicaPostgreSQLTests(MediaTemporalMixin, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        self.usuario, self.odontologo, self.paciente = crear_contexto_clinico(sufijo="postgres")

    def crear_borrador(self):
        return crear_historia_borrador(
            paciente=self.paciente,
            odontologo=self.odontologo,
            usuario=self.usuario,
            datos=datos_clinicos(),
        )[0]

    def test_finalizaciones_concurrentes_no_duplican_folio(self):
        historias = [self.crear_borrador(), self.crear_borrador()]

        def finalizar(pk):
            close_old_connections()
            historia = HistoriaClinica.objects.get(pk=pk)
            usuario = get_user_model().objects.get(pk=self.usuario.pk)
            resultado, _ = finalizar_historia_clinica(
                historia=historia,
                usuario=usuario,
            )
            close_old_connections()
            return resultado.numero_asiento

        with ThreadPoolExecutor(max_workers=2) as executor:
            folios = list(executor.map(finalizar, [historia.pk for historia in historias]))

        self.assertEqual(sorted(folios), [1, 2])

    def test_ediciones_concurrentes_no_duplican_version(self):
        historia = self.crear_borrador()

        def editar(diagnostico):
            close_old_connections()
            try:
                historia_hilo = HistoriaClinica.objects.get(pk=historia.pk)
                usuario = get_user_model().objects.get(pk=self.usuario.pk)
                _, version, cambio = actualizar_historia_borrador(
                    historia=historia_hilo,
                    usuario=usuario,
                    datos=datos_clinicos(diagnostico=diagnostico),
                    motivo_cambio=(
                        f"Se registró el ajuste concurrente correspondiente a {diagnostico}."
                    ),
                )
                return version.numero_version, cambio
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            resultados = list(executor.map(editar, ["Diagnóstico A", "Diagnóstico B"]))

        self.assertTrue(all(cambio for _, cambio in resultados))
        self.assertEqual(
            list(
                historia.versiones.order_by("numero_version").values_list(
                    "numero_version", flat=True
                )
            ),
            [1, 2, 3],
        )

    def test_enmiendas_concurrentes_no_duplican_numero(self):
        historia = self.crear_borrador()
        historia, _ = finalizar_historia_clinica(
            historia=historia,
            usuario=self.usuario,
        )

        def enmendar(numero):
            close_old_connections()
            try:
                historia_hilo = HistoriaClinica.objects.get(pk=historia.pk)
                usuario = get_user_model().objects.get(pk=self.usuario.pk)
                odontologo = Odontologo.objects.get(pk=self.odontologo.pk)
                enmienda = crear_enmienda_historia(
                    historia=historia_hilo,
                    usuario=usuario,
                    odontologo=odontologo,
                    texto=f"Aclaración concurrente número {numero}.",
                    motivo=f"Se incorpora la aclaración concurrente número {numero}.",
                )
                return enmienda.numero_enmienda
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            numeros = list(executor.map(enmendar, [1, 2]))

        self.assertEqual(sorted(numeros), [1, 2])
        self.assertEqual(historia.enmiendas.count(), 2)

    def test_triggers_bloquean_mutaciones_y_borrados_directos(self):
        historia = self.crear_borrador()
        adjunto = HistoriaClinicaAdjunto.objects.create(
            historia=historia,
            archivo=SimpleUploadedFile(
                "trigger.pdf",
                b"contenido-trigger",
                content_type="application/pdf",
            ),
            subido_por=self.usuario,
        )
        historia, _ = finalizar_historia_clinica(
            historia=historia,
            usuario=self.usuario,
        )
        version = historia.versiones.first()
        enmienda = crear_enmienda_historia(
            historia=historia,
            usuario=self.usuario,
            odontologo=self.odontologo,
            texto="Aclaración protegida por trigger.",
            motivo="Se verifica la protección adicional de PostgreSQL.",
        )

        with self.assertRaises(DatabaseError), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE historias_historiaclinicaversion SET motivo = %s WHERE id = %s",
                ["Alterado", version.pk],
            )
        with self.assertRaises(DatabaseError), connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM historias_historiaclinicaversion WHERE id = %s",
                [version.pk],
            )
        with self.assertRaises(DatabaseError), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE historias_historiaclinicaenmienda SET motivo = %s WHERE id = %s",
                ["Alterado", enmienda.pk],
            )
        with self.assertRaises(DatabaseError), connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM historias_historiaclinicaenmienda WHERE id = %s",
                [enmienda.pk],
            )
        with self.assertRaises(DatabaseError), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE historias_historiaclinica SET diagnostico = %s WHERE id = %s",
                ["Alterado", historia.pk],
            )
        with self.assertRaises(DatabaseError), connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM historias_historiaclinica WHERE id = %s",
                [historia.pk],
            )
        with self.assertRaises(DatabaseError), connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM historias_historiaclinicaadjunto WHERE id = %s",
                [adjunto.pk],
            )
