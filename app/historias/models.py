import hashlib
from datetime import datetime, time
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from django.utils.text import get_valid_filename

MAX_ADJUNTO_HISTORIA_BYTES = 10 * 1024 * 1024
EXTENSIONES_ADJUNTOS_PERMITIDAS = {
    ".bmp",
    ".dcm",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}
EXTENSIONES_IMAGEN = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
FIRMAS_BINARIAS_PELIGROSAS = (
    b"MZ",
    b"\x7fELF",
    b"PK\x03\x04",
    b"Rar!\x1a\x07",
    b"7z\xbc\xaf\x27\x1c",
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
)
PREFIJOS_TEXTO_PELIGROSOS = (
    b"#!",
    b"<!doctype html",
    b"<html",
    b"<script",
    b"<?php",
    b"<?xml",
    b"<svg",
)
EXTENSIONES_POR_FORMATO = {
    "bmp": {".bmp"},
    "dicom": {".dcm"},
    "jpeg": {".jpeg", ".jpg"},
    "pdf": {".pdf"},
    "png": {".png"},
    "tiff": {".tif", ".tiff"},
    "webp": {".webp"},
}


def ruta_adjunto_historia(instance, filename):
    nombre_archivo = get_valid_filename(Path(filename).name)
    fecha = timezone.localdate().strftime("%Y/%m")
    historia_id = instance.historia_id or "sin-historia"
    return f"historias/{historia_id}/{fecha}/{uuid4().hex}_{nombre_archivo}"


def validar_archivo_clinico(archivo):
    if not archivo:
        return

    extension = Path(archivo.name).suffix.lower()

    if extension not in EXTENSIONES_ADJUNTOS_PERMITIDAS:
        raise ValidationError(
            "El archivo debe ser PDF, imagen o DICOM. No se permiten ejecutables."
        )

    if archivo.size > MAX_ADJUNTO_HISTORIA_BYTES:
        raise ValidationError("El archivo no puede superar los 10 MB.")

    if not getattr(archivo, "_committed", False):
        _validar_cabecera_archivo_clinico(archivo, extension)


def _validar_cabecera_archivo_clinico(archivo, extension):
    cabecera = _leer_cabecera_archivo(archivo)
    cabecera_sin_espacios = cabecera.lstrip().lower()

    if cabecera.startswith(FIRMAS_BINARIAS_PELIGROSAS) or cabecera_sin_espacios.startswith(
        PREFIJOS_TEXTO_PELIGROSOS
    ):
        raise ValidationError(
            "El contenido del archivo no corresponde a un documento clínico permitido."
        )

    formato = _detectar_formato_archivo(cabecera)
    if formato and extension not in EXTENSIONES_POR_FORMATO[formato]:
        raise ValidationError("El contenido del archivo no coincide con la extensión seleccionada.")


def _leer_cabecera_archivo(archivo):
    flujo = getattr(archivo, "file", archivo)
    posicion = flujo.tell() if hasattr(flujo, "tell") else None

    try:
        if hasattr(flujo, "seek"):
            flujo.seek(0)
        return flujo.read(1024)
    except (OSError, ValueError) as error:
        raise ValidationError("No se pudo validar el contenido del archivo adjunto.") from error
    finally:
        if posicion is not None and hasattr(flujo, "seek"):
            flujo.seek(posicion)


def _detectar_formato_archivo(cabecera):
    if b"%PDF-" in cabecera:
        return "pdf"
    if cabecera.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if cabecera.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if cabecera.startswith((b"II*\x00", b"MM\x00*")):
        return "tiff"
    if cabecera.startswith(b"BM"):
        return "bmp"
    if cabecera.startswith(b"RIFF") and cabecera[8:12] == b"WEBP":
        return "webp"
    if len(cabecera) >= 132 and cabecera[128:132] == b"DICM":
        return "dicom"
    return ""


def _instante_para_fecha(fecha):
    instante = datetime.combine(fecha, time.min)
    return timezone.make_aware(instante, timezone.get_current_timezone())


def _validar_sha256(valor, campo="sha256"):
    if valor and (
        len(valor) != 64 or any(caracter not in "0123456789abcdef" for caracter in valor)
    ):
        raise ValidationError({campo: "Debe ser un SHA-256 hexadecimal válido."})


class SinBorradoQuerySet(models.QuerySet):
    mensaje_borrado = "Los registros clínicos no se pueden eliminar físicamente."

    def delete(self):
        raise ProtectedError(self.mensaje_borrado, self)


class HistoriaClinicaQuerySet(SinBorradoQuerySet):
    def update(self, **kwargs):
        raise ValidationError(
            "Las historias clínicas deben modificarse mediante los servicios de dominio."
        )


class AppendOnlyQuerySet(SinBorradoQuerySet):
    def update(self, **kwargs):
        raise ValidationError("Este registro clínico es inmutable.")


class AdjuntoClinicoQuerySet(SinBorradoQuerySet):
    def update(self, **kwargs):
        raise ValidationError(
            "Los adjuntos clínicos guardados no se pueden modificar ni reemplazar."
        )


class HistoriaClinica(models.Model):
    CAMPOS_CLINICOS = (
        "paciente_id",
        "odontologo_id",
        "fecha",
        "fecha_hora_atencion",
        "motivo_consulta",
        "diagnostico",
        "tratamiento_realizado",
        "pieza_dental",
        "observaciones",
        "proximo_control",
    )

    paciente = models.ForeignKey(
        "pacientes.Paciente",
        on_delete=models.PROTECT,
        related_name="historias_clinicas",
    )
    odontologo = models.ForeignKey(
        "turnos.Odontologo",
        on_delete=models.PROTECT,
        related_name="historias_clinicas",
    )
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="historias_clinicas_creadas",
    )
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="historias_clinicas_actualizadas",
    )
    finalizada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        editable=False,
        related_name="historias_clinicas_finalizadas",
    )
    fecha = models.DateField(default=timezone.localdate)
    fecha_hora_atencion = models.DateTimeField(default=timezone.now)
    motivo_consulta = models.TextField()
    diagnostico = models.TextField(blank=True)
    tratamiento_realizado = models.TextField(blank=True)
    pieza_dental = models.CharField(max_length=50, blank=True)
    observaciones = models.TextField(blank=True)
    proximo_control = models.DateField(null=True, blank=True)
    borrador = models.BooleanField(default=True)
    bloqueada_para_edicion = models.BooleanField(default=False, editable=False)
    numero_asiento = models.PositiveIntegerField(null=True, blank=True, editable=False)
    finalizada_en = models.DateTimeField(null=True, blank=True, editable=False)
    migrada_desde_legacy = models.BooleanField(default=False, editable=False)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    objects = HistoriaClinicaQuerySet.as_manager()

    class Meta:
        ordering = ["-numero_asiento", "-fecha_hora_atencion", "-creado_en"]
        verbose_name = "Historia clínica"
        verbose_name_plural = "Historias clínicas"
        indexes = [
            models.Index(fields=["paciente", "-numero_asiento"]),
            models.Index(fields=["paciente", "-fecha_hora_atencion"]),
            models.Index(fields=["odontologo", "-fecha_hora_atencion"]),
            models.Index(fields=["borrador", "bloqueada_para_edicion"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["paciente", "numero_asiento"],
                condition=models.Q(numero_asiento__isnull=False),
                name="historias_folio_unico_por_paciente",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        borrador=True,
                        bloqueada_para_edicion=False,
                        finalizada_en__isnull=True,
                        finalizada_por__isnull=True,
                        numero_asiento__isnull=True,
                        migrada_desde_legacy=False,
                    )
                    | models.Q(
                        borrador=False,
                        bloqueada_para_edicion=True,
                        finalizada_en__isnull=False,
                        finalizada_por__isnull=False,
                        numero_asiento__isnull=False,
                        migrada_desde_legacy=False,
                    )
                    | models.Q(
                        borrador=False,
                        bloqueada_para_edicion=True,
                        finalizada_en__isnull=False,
                        numero_asiento__isnull=False,
                        migrada_desde_legacy=True,
                    )
                ),
                name="historias_estado_clinico_coherente",
            ),
        ]

    def __init__(self, *args, **kwargs):
        fecha_explicita = kwargs.get("fecha")
        fecha_hora_explicita = "fecha_hora_atencion" in kwargs
        super().__init__(*args, **kwargs)

        # Conserva compatibilidad para integraciones internas que todavía crean por fecha.
        if fecha_explicita and not fecha_hora_explicita:
            self.fecha_hora_atencion = _instante_para_fecha(fecha_explicita)

    @property
    def estado_display(self):
        if self.borrador:
            return "Borrador"
        if self.migrada_desde_legacy:
            return "Finalizada · Migrada"
        return "Finalizada"

    @property
    def puede_editarse(self):
        return self.borrador and not self.bloqueada_para_edicion

    @property
    def integridad_inicializada(self):
        if not self.pk:
            return False
        return self.versiones.exists()

    def clean(self):
        errors = {}
        ahora = timezone.now()

        if self.fecha_hora_atencion:
            if timezone.is_naive(self.fecha_hora_atencion):
                errors["fecha_hora_atencion"] = "La fecha y hora deben incluir zona horaria."
            elif self.fecha_hora_atencion > ahora:
                errors["fecha_hora_atencion"] = (
                    "La fecha y hora de la atención no pueden ser futuras."
                )

        fecha_atencion = self._fecha_local_atencion()

        if self.proximo_control and fecha_atencion and self.proximo_control < fecha_atencion:
            errors["proximo_control"] = (
                "El próximo control no puede ser anterior a la fecha de atención."
            )

        if self.borrador:
            if self.bloqueada_para_edicion:
                errors["bloqueada_para_edicion"] = "Un borrador no puede estar bloqueado."
            if self.finalizada_en or self.finalizada_por_id or self.numero_asiento:
                errors["borrador"] = "Un borrador no puede tener datos de finalización."
            if self.migrada_desde_legacy:
                errors["migrada_desde_legacy"] = "Un registro legacy no puede ser borrador."
        else:
            if not self.bloqueada_para_edicion:
                errors["bloqueada_para_edicion"] = "Una entrada finalizada debe estar bloqueada."
            if not self.finalizada_en:
                errors["finalizada_en"] = "Una entrada finalizada requiere fecha de finalización."
            if not self.numero_asiento:
                errors["numero_asiento"] = "Una entrada finalizada requiere número de asiento."
            if not self.migrada_desde_legacy and not self.finalizada_por_id:
                errors["finalizada_por"] = "Una entrada finalizada requiere un autor."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            anterior = type(self)._base_manager.filter(pk=self.pk).first()
            if anterior and anterior.bloqueada_para_edicion:
                raise ValidationError(
                    "Una entrada clínica finalizada es inmutable; use una enmienda."
                )

        if self.fecha_hora_atencion and not timezone.is_naive(self.fecha_hora_atencion):
            self.fecha = timezone.localtime(self.fecha_hora_atencion).date()

        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError(
            "Las historias clínicas no se pueden eliminar físicamente.",
            [self],
        )

    def __str__(self):
        folio = f"Asiento {self.numero_asiento}" if self.numero_asiento else "Borrador"
        return f"{self.paciente} - {folio} - {self.fecha:%d/%m/%Y}"

    def _fecha_local_atencion(self):
        if not self.fecha_hora_atencion:
            return self.fecha
        if timezone.is_naive(self.fecha_hora_atencion):
            return self.fecha_hora_atencion.date()
        return timezone.localtime(self.fecha_hora_atencion).date()


class HistoriaClinicaAdjunto(models.Model):
    historia = models.ForeignKey(
        HistoriaClinica,
        on_delete=models.PROTECT,
        related_name="adjuntos",
    )
    archivo = models.FileField(upload_to=ruta_adjunto_historia)
    descripcion = models.CharField(max_length=200, blank=True)
    content_type = models.CharField(max_length=100, blank=True)
    tamano_bytes = models.PositiveIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True, editable=False)
    subido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="adjuntos_historia_clinica",
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    objects = AdjuntoClinicoQuerySet.as_manager()

    class Meta:
        ordering = ["-creado_en"]
        verbose_name = "Adjunto de historia clínica"
        verbose_name_plural = "Adjuntos de historia clínica"
        indexes = [
            models.Index(fields=["historia", "-creado_en"]),
            models.Index(fields=["sha256"]),
        ]

    @property
    def nombre_archivo(self):
        return Path(self.archivo.name).name

    @property
    def extension(self):
        return Path(self.archivo.name).suffix.lower()

    @property
    def es_imagen(self):
        return self.extension in EXTENSIONES_IMAGEN

    @property
    def tamano_legible(self):
        if not self.archivo and not self.tamano_bytes:
            return "-"

        size = self.tamano_bytes or self.archivo.size

        if size < 1024:
            return f"{size} B"

        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"

        return f"{size / (1024 * 1024):.1f} MB"

    def clean(self):
        validar_archivo_clinico(self.archivo)
        _validar_sha256(self.sha256)

        if self._state.adding and self.historia_id:
            historia = getattr(self, "historia", None)
            if historia and historia.bloqueada_para_edicion:
                raise ValidationError(
                    "No se pueden agregar adjuntos a una entrada clínica finalizada."
                )

    def save(self, *args, **kwargs):
        permitir_backfill_sha256 = kwargs.pop("permitir_backfill_sha256", False)
        if self.pk:
            anterior = type(self)._base_manager.select_related("historia").get(pk=self.pk)
            if not self._es_backfill_sha256_legacy_valido(
                anterior,
                permitir_backfill_sha256=permitir_backfill_sha256,
            ):
                raise ValidationError(
                    "Los adjuntos clínicos guardados no se pueden modificar ni reemplazar."
                )

        self._guardar_metadatos_archivo()

        if self._state.adding and self.archivo and not self.sha256:
            self.sha256 = self._calcular_sha256()

        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError(
            "Los adjuntos clínicos no se pueden eliminar físicamente.",
            [self],
        )

    def __str__(self):
        return self.nombre_archivo

    def _guardar_metadatos_archivo(self):
        if not self.archivo:
            return

        if hasattr(self.archivo, "size") and self.archivo.size:
            self.tamano_bytes = self.archivo.size

        archivo = getattr(self.archivo, "file", None)
        content_type = getattr(archivo, "content_type", "")

        if content_type:
            self.content_type = content_type

    def _calcular_sha256(self):
        archivo = getattr(self.archivo, "file", self.archivo)
        posicion = archivo.tell() if hasattr(archivo, "tell") else None
        digest = hashlib.sha256()

        try:
            if hasattr(archivo, "seek"):
                archivo.seek(0)
            if hasattr(archivo, "chunks"):
                for bloque in archivo.chunks():
                    digest.update(bloque)
            else:
                for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
                    digest.update(bloque)
        finally:
            if posicion is not None and hasattr(archivo, "seek"):
                archivo.seek(posicion)

        return digest.hexdigest()

    def _es_backfill_sha256_legacy_valido(
        self,
        anterior,
        *,
        permitir_backfill_sha256,
    ):
        if not permitir_backfill_sha256:
            return False
        if not anterior.historia.migrada_desde_legacy or anterior.historia.versiones.exists():
            return False
        if anterior.sha256 or not self.sha256:
            return False

        campos_inmutables = (
            "historia_id",
            "descripcion",
            "content_type",
            "tamano_bytes",
            "subido_por_id",
            "creado_en",
        )
        if any(getattr(self, campo) != getattr(anterior, campo) for campo in campos_inmutables):
            return False
        return self.archivo.name == anterior.archivo.name


class HistoriaClinicaVersion(models.Model):
    historia = models.ForeignKey(
        HistoriaClinica,
        on_delete=models.PROTECT,
        related_name="versiones",
    )
    numero_version = models.PositiveIntegerField()
    snapshot = models.JSONField()
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="versiones_historia_clinica_creadas",
    )
    creado_en = models.DateTimeField(default=timezone.now, editable=False)
    motivo = models.TextField()
    hash_anterior = models.CharField(max_length=64, blank=True, editable=False)
    hash_integridad = models.CharField(max_length=64, editable=False)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["numero_version", "creado_en"]
        verbose_name = "Versión de historia clínica"
        verbose_name_plural = "Versiones de historia clínica"
        constraints = [
            models.UniqueConstraint(
                fields=["historia", "numero_version"],
                name="historias_version_unica_por_asiento",
            ),
            models.CheckConstraint(
                condition=models.Q(numero_version__gt=0),
                name="historias_version_numero_positivo",
            ),
        ]
        indexes = [
            models.Index(fields=["historia", "numero_version"]),
            models.Index(fields=["creado_en"]),
        ]

    def clean(self):
        if not (self.motivo or "").strip():
            raise ValidationError({"motivo": "El motivo de la versión es obligatorio."})
        if not isinstance(self.snapshot, dict) or not self.snapshot:
            raise ValidationError({"snapshot": "El snapshot debe ser un objeto JSON completo."})
        _validar_sha256(self.hash_anterior, "hash_anterior")
        _validar_sha256(self.hash_integridad, "hash_integridad")

    def save(self, *args, **kwargs):
        if not self._state.adding or self.pk:
            raise ValidationError("Las versiones de historia clínica son inmutables.")
        self.motivo = (self.motivo or "").strip()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError(
            "Las versiones de historia clínica no se pueden eliminar.",
            [self],
        )

    def __str__(self):
        return f"{self.historia} - Versión {self.numero_version}"


class HistoriaClinicaEnmienda(models.Model):
    historia = models.ForeignKey(
        HistoriaClinica,
        on_delete=models.PROTECT,
        related_name="enmiendas",
    )
    numero_enmienda = models.PositiveIntegerField()
    texto = models.TextField()
    motivo = models.TextField()
    odontologo = models.ForeignKey(
        "turnos.Odontologo",
        on_delete=models.PROTECT,
        related_name="enmiendas_historia_clinica",
    )
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="enmiendas_historia_clinica_creadas",
    )
    creado_en = models.DateTimeField(default=timezone.now, editable=False)
    hash_anterior = models.CharField(max_length=64, blank=True, editable=False)
    hash_integridad = models.CharField(max_length=64, editable=False)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["numero_enmienda", "creado_en"]
        verbose_name = "Enmienda de historia clínica"
        verbose_name_plural = "Enmiendas de historia clínica"
        constraints = [
            models.UniqueConstraint(
                fields=["historia", "numero_enmienda"],
                name="historias_enmienda_unica_por_asiento",
            ),
            models.CheckConstraint(
                condition=models.Q(numero_enmienda__gt=0),
                name="historias_enmienda_numero_positivo",
            ),
        ]
        indexes = [
            models.Index(fields=["historia", "numero_enmienda"]),
            models.Index(fields=["creado_en"]),
        ]

    def clean(self):
        errors = {}
        if not (self.texto or "").strip():
            errors["texto"] = "El texto de la enmienda es obligatorio."
        if not (self.motivo or "").strip():
            errors["motivo"] = "El motivo de la enmienda es obligatorio."
        if self.historia_id:
            historia = getattr(self, "historia", None)
            if historia and (historia.borrador or not historia.bloqueada_para_edicion):
                errors["historia"] = "Solo se pueden enmendar entradas finalizadas."
        if errors:
            raise ValidationError(errors)
        _validar_sha256(self.hash_anterior, "hash_anterior")
        _validar_sha256(self.hash_integridad, "hash_integridad")

    def save(self, *args, **kwargs):
        if not self._state.adding or self.pk:
            raise ValidationError("Las enmiendas de historia clínica son inmutables.")
        self.texto = (self.texto or "").strip()
        self.motivo = (self.motivo or "").strip()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError(
            "Las enmiendas de historia clínica no se pueden eliminar.",
            [self],
        )

    def __str__(self):
        return f"{self.historia} - Enmienda {self.numero_enmienda}"


class AccesoClinicoAuditoria(models.Model):
    class Accion(models.TextChoices):
        VER_PACIENTE = "ver_paciente", "Ver paciente"
        VER_HISTORIA = "ver_historia", "Ver historia clínica"
        VER_DETALLE_HISTORIA = "ver_detalle_historia", "Ver detalle de historia"
        CREAR_HISTORIA = "crear_historia", "Crear historia clínica"
        EDITAR_HISTORIA = "editar_historia", "Editar historia clínica"
        CREAR_BORRADOR = "crear_borrador", "Crear borrador clínico"
        EDITAR_BORRADOR = "editar_borrador", "Editar borrador clínico"
        CREAR_VERSION = "crear_version", "Crear versión clínica"
        FINALIZAR_HISTORIA = "finalizar_historia", "Finalizar historia clínica"
        VER_VERSION = "ver_version", "Ver versión clínica"
        CREAR_ENMIENDA = "crear_enmienda", "Crear enmienda clínica"
        VER_ENMIENDA = "ver_enmienda", "Ver enmienda clínica"
        EXPORTAR_HISTORIA = "exportar_historia", "Exportar historia clínica"
        VERIFICAR_INTEGRIDAD = "verificar_integridad", "Verificar integridad clínica"
        INTENTO_EDITAR_FINALIZADA = (
            "intento_editar_finalizada",
            "Intentar editar historia finalizada",
        )
        INTENTO_ELIMINAR_HISTORIA = (
            "intento_eliminar_historia",
            "Intentar eliminar historia clínica",
        )
        ABRIR_ADJUNTO = "abrir_adjunto", "Abrir adjunto clínico"
        VER_FICHA = "ver_ficha", "Ver ficha odontológica"
        EDITAR_FICHA = "editar_ficha", "Editar ficha odontológica"
        VER_ODONTOGRAMA = "ver_odontograma", "Ver odontograma"
        EDITAR_ODONTOGRAMA = "editar_odontograma", "Editar odontograma"
        INICIAR_EMERGENCIA = "iniciar_emergencia", "Iniciar acceso de emergencia"
        FINALIZAR_EMERGENCIA = "finalizar_emergencia", "Finalizar acceso de emergencia"
        ARCHIVAR_PACIENTE = "archivar_paciente", "Archivar paciente"
        REACTIVAR_PACIENTE = "reactivar_paciente", "Reactivar paciente"
        SOLICITUD_PUBLICA_ARCHIVADO = (
            "solicitud_publica_paciente_archivado",
            "Solicitud pública de paciente archivado",
        )
        OTP_ARCHIVADO = "otp_paciente_archivado", "OTP solicitado para paciente archivado"
        CREAR_BORRADOR_INDICACION = (
            "crear_borrador_indicacion",
            "Crear borrador de indicación",
        )
        EDITAR_BORRADOR_INDICACION = (
            "editar_borrador_indicacion",
            "Editar borrador de indicación",
        )
        VER_INDICACION = "ver_indicacion", "Ver indicación"
        EMITIR_INDICACION = "emitir_indicacion", "Emitir indicación"
        GENERAR_PDF_INDICACION = "generar_pdf_indicacion", "Generar PDF de indicación"
        DESCARGAR_PDF_INDICACION = (
            "descargar_pdf_indicacion",
            "Descargar PDF de indicación",
        )
        ENVIAR_EMAIL_INDICACION = "enviar_email_indicacion", "Enviar indicación por email"
        ERROR_EMAIL_INDICACION = "error_email_indicacion", "Error de email de indicación"
        REENVIAR_EMAIL_INDICACION = (
            "reenviar_email_indicacion",
            "Reenviar indicación por email",
        )
        ANULAR_INDICACION = "anular_indicacion", "Anular indicación"
        CREAR_REEMPLAZO_INDICACION = (
            "crear_reemplazo_indicacion",
            "Crear reemplazo de indicación",
        )
        INTENTO_EDITAR_INDICACION_EMITIDA = (
            "intento_editar_indicacion_emitida",
            "Intentar editar indicación emitida",
        )
        INTENTO_ACCESO_INDICACION = (
            "intento_acceso_indicacion",
            "Intentar acceder a indicación",
        )

    class Resultado(models.TextChoices):
        PERMITIDO = "permitido", "Permitido"
        DENEGADO = "denegado", "Denegado"
        ERROR = "error", "Error"

    class Politica(models.TextChoices):
        ASOCIACION_ACTIVA = "asociacion_activa", "Asociación activa"
        COMPARTIDO = "compartido", "Datos compartidos"
        EMERGENCIA = "emergencia", "Emergencia"
        ADMINISTRATIVA = "administrativa", "Administrativa"
        SISTEMA = "sistema", "Sistema"
        SIN_PERMISO = "sin_permiso", "Sin permiso"
        PACIENTE_ARCHIVADO = "paciente_archivado", "Paciente archivado"

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auditorias_acceso_clinico",
    )
    paciente = models.ForeignKey(
        "pacientes.Paciente",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auditorias_acceso_clinico",
    )
    historia = models.ForeignKey(
        HistoriaClinica,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auditorias_acceso",
    )
    adjunto = models.ForeignKey(
        HistoriaClinicaAdjunto,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auditorias_acceso",
    )
    identificador_solicitado = models.CharField(max_length=120, blank=True)
    accion = models.CharField(max_length=50, choices=Accion.choices)
    resultado = models.CharField(max_length=20, choices=Resultado.choices)
    politica = models.CharField(max_length=40, choices=Politica.choices, blank=True)
    motivo = models.TextField(blank=True)
    ruta = models.CharField(max_length=255, blank=True)
    metodo = models.CharField(max_length=12, blank=True)
    ip_hash = models.CharField(max_length=64, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    es_emergencia = models.BooleanField(default=False)
    es_acceso_compartido = models.BooleanField(default=False)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado_en"]
        verbose_name = "Auditoría de acceso clínico"
        verbose_name_plural = "Auditorías de acceso clínico"
        indexes = [
            models.Index(fields=["paciente", "-creado_en"]),
            models.Index(fields=["usuario", "-creado_en"]),
            models.Index(fields=["accion", "-creado_en"]),
            models.Index(fields=["resultado", "-creado_en"]),
            models.Index(fields=["es_emergencia", "-creado_en"]),
        ]

    def __str__(self):
        return f"{self.get_accion_display()} - {self.get_resultado_display()}"
