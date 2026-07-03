from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import get_valid_filename
from django.utils import timezone


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


class HistoriaClinica(models.Model):
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
    fecha = models.DateField(default=timezone.localdate)
    motivo_consulta = models.TextField()
    diagnostico = models.TextField(blank=True)
    tratamiento_realizado = models.TextField(blank=True)
    pieza_dental = models.CharField(max_length=50, blank=True)
    observaciones = models.TextField(blank=True)
    proximo_control = models.DateField(null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-fecha", "-creado_en"]
        verbose_name = "Historia clínica"
        verbose_name_plural = "Historias clínicas"
        indexes = [
            models.Index(fields=["paciente", "-fecha"]),
            models.Index(fields=["odontologo", "-fecha"]),
        ]

    def clean(self):
        errors = {}

        if self.fecha and self.fecha > timezone.localdate():
            errors["fecha"] = "La fecha de la atención no puede ser futura."

        if self.proximo_control and self.fecha and self.proximo_control < self.fecha:
            errors["proximo_control"] = (
                "El próximo control no puede ser anterior a la fecha de atención."
            )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.paciente} - {self.fecha:%d/%m/%Y}"


class HistoriaClinicaAdjunto(models.Model):
    historia = models.ForeignKey(
        HistoriaClinica,
        on_delete=models.CASCADE,
        related_name="adjuntos",
    )
    archivo = models.FileField(upload_to=ruta_adjunto_historia)
    descripcion = models.CharField(max_length=200, blank=True)
    content_type = models.CharField(max_length=100, blank=True)
    tamano_bytes = models.PositiveIntegerField(default=0)
    subido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="adjuntos_historia_clinica",
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado_en"]
        verbose_name = "Adjunto de historia clínica"
        verbose_name_plural = "Adjuntos de historia clínica"
        indexes = [
            models.Index(fields=["historia", "-creado_en"]),
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

    def save(self, *args, **kwargs):
        self._guardar_metadatos_archivo()
        self.full_clean()
        super().save(*args, **kwargs)

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


class AccesoClinicoAuditoria(models.Model):
    class Accion(models.TextChoices):
        VER_PACIENTE = "ver_paciente", "Ver paciente"
        VER_HISTORIA = "ver_historia", "Ver historia clinica"
        VER_DETALLE_HISTORIA = "ver_detalle_historia", "Ver detalle de historia"
        CREAR_HISTORIA = "crear_historia", "Crear historia clinica"
        EDITAR_HISTORIA = "editar_historia", "Editar historia clinica"
        ABRIR_ADJUNTO = "abrir_adjunto", "Abrir adjunto clinico"
        VER_FICHA = "ver_ficha", "Ver ficha odontologica"
        EDITAR_FICHA = "editar_ficha", "Editar ficha odontologica"
        VER_ODONTOGRAMA = "ver_odontograma", "Ver odontograma"
        EDITAR_ODONTOGRAMA = "editar_odontograma", "Editar odontograma"
        INICIAR_EMERGENCIA = "iniciar_emergencia", "Iniciar acceso de emergencia"
        FINALIZAR_EMERGENCIA = "finalizar_emergencia", "Finalizar acceso de emergencia"
        ARCHIVAR_PACIENTE = "archivar_paciente", "Archivar paciente"
        REACTIVAR_PACIENTE = "reactivar_paciente", "Reactivar paciente"
        SOLICITUD_PUBLICA_ARCHIVADO = (
            "solicitud_publica_paciente_archivado",
            "Solicitud publica de paciente archivado",
        )
        OTP_ARCHIVADO = "otp_paciente_archivado", "OTP solicitado para paciente archivado"

    class Resultado(models.TextChoices):
        PERMITIDO = "permitido", "Permitido"
        DENEGADO = "denegado", "Denegado"
        ERROR = "error", "Error"

    class Politica(models.TextChoices):
        ASOCIACION_ACTIVA = "asociacion_activa", "Asociacion activa"
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
        verbose_name = "Auditoria de acceso clinico"
        verbose_name_plural = "Auditorias de acceso clinico"
        indexes = [
            models.Index(fields=["paciente", "-creado_en"]),
            models.Index(fields=["usuario", "-creado_en"]),
            models.Index(fields=["accion", "-creado_en"]),
            models.Index(fields=["resultado", "-creado_en"]),
            models.Index(fields=["es_emergencia", "-creado_en"]),
        ]

    def __str__(self):
        return f"{self.get_accion_display()} - {self.get_resultado_display()}"
