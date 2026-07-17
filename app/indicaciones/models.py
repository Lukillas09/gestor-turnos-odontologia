import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from .storage import almacenamiento_indicaciones_privado


def ruta_privada_indicacion(instance, filename):
    return f"indicaciones/{instance.uuid}/documento.pdf"


def _es_sha256(valor):
    return bool(
        valor and len(valor) == 64 and all(caracter in "0123456789abcdef" for caracter in valor)
    )


class SinMutacionesMasivasQuerySet(models.QuerySet):
    def delete(self):
        raise ProtectedError("Los documentos clínicos no se pueden eliminar físicamente.", self)

    def update(self, **kwargs):
        raise ValidationError("Los documentos clínicos deben modificarse mediante servicios.")


class PlantillaIndicacion(models.Model):
    nombre = models.CharField(max_length=150)
    procedimiento = models.CharField(max_length=150, blank=True)
    titulo_documento = models.CharField(max_length=180)
    contenido = models.TextField()
    pautas_alarma = models.TextField(blank=True)
    recomendaciones_control = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1, editable=False)
    activa = models.BooleanField(default=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="plantillas_indicaciones_creadas",
    )
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="plantillas_indicaciones_actualizadas",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    objects = SinMutacionesMasivasQuerySet.as_manager()

    class Meta:
        ordering = ["nombre", "-version"]
        verbose_name = "Plantilla de indicación"
        verbose_name_plural = "Plantillas de indicaciones"
        constraints = [
            models.CheckConstraint(condition=Q(version__gt=0), name="ind_plant_version_pos")
        ]
        indexes = [models.Index(fields=["activa", "nombre"])]

    def clean(self):
        errors = {}
        if not (self.nombre or "").strip():
            errors["nombre"] = "El nombre es obligatorio."
        if not (self.titulo_documento or "").strip():
            errors["titulo_documento"] = "El título es obligatorio."
        if not (self.contenido or "").strip():
            errors["contenido"] = "El contenido debe ser definido por un profesional."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        actualizacion_versionada = kwargs.pop("actualizacion_versionada", False)
        if self.pk and not actualizacion_versionada:
            raise ValidationError(
                "Las plantillas existentes deben modificarse mediante el servicio de versionado."
            )
        self.nombre = (self.nombre or "").strip()
        self.titulo_documento = (self.titulo_documento or "").strip()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError("Desactivá la plantilla en lugar de eliminarla.", [self])

    def __str__(self):
        return f"{self.nombre} (v{self.version})"


class PlantillaIndicacionVersion(models.Model):
    plantilla = models.ForeignKey(
        PlantillaIndicacion,
        on_delete=models.PROTECT,
        related_name="versiones",
    )
    numero_version = models.PositiveIntegerField()
    snapshot = models.JSONField()
    motivo = models.TextField()
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="versiones_plantillas_indicaciones_creadas",
    )
    creado_en = models.DateTimeField(default=timezone.now, editable=False)

    objects = SinMutacionesMasivasQuerySet.as_manager()

    class Meta:
        ordering = ["plantilla", "numero_version"]
        verbose_name = "Versión de plantilla de indicación"
        verbose_name_plural = "Versiones de plantillas de indicaciones"
        constraints = [
            models.UniqueConstraint(
                fields=["plantilla", "numero_version"],
                name="ind_plant_version_unica",
            ),
            models.CheckConstraint(
                condition=Q(numero_version__gt=0),
                name="ind_plant_hist_num_pos",
            ),
        ]
        indexes = [models.Index(fields=["plantilla", "numero_version"])]

    def clean(self):
        errors = {}
        if not isinstance(self.snapshot, dict) or not self.snapshot:
            errors["snapshot"] = "La versión requiere un snapshot completo."
        if not (self.motivo or "").strip():
            errors["motivo"] = "El motivo de modificación es obligatorio."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self._state.adding or self.pk:
            raise ValidationError("Las versiones de plantilla son inmutables.")
        self.motivo = (self.motivo or "").strip()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError("Las versiones de plantilla no se pueden eliminar.", [self])

    def __str__(self):
        return f"{self.plantilla.nombre} - versión {self.numero_version}"


class IndicacionPaciente(models.Model):
    class Estado(models.TextChoices):
        BORRADOR = "borrador", "Borrador"
        EMITIDA = "emitida", "Emitida"
        ANULADA = "anulada", "Anulada"

    class EstadoEmail(models.TextChoices):
        NO_APLICA = "no_aplica", "No aplica"
        SIN_DESTINO = "sin_destino", "Sin email verificado"
        PENDIENTE = "pendiente", "Email pendiente"
        ENVIANDO = "enviando", "Enviando email"
        ENVIADO = "enviado", "Email enviado"
        ERROR = "error", "Error de email"

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    paciente = models.ForeignKey(
        "pacientes.Paciente",
        on_delete=models.PROTECT,
        related_name="indicaciones_postoperatorias",
    )
    odontologo = models.ForeignKey(
        "turnos.Odontologo",
        on_delete=models.PROTECT,
        related_name="indicaciones_postoperatorias",
    )
    historia_clinica = models.ForeignKey(
        "historias.HistoriaClinica",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="indicaciones_postoperatorias",
    )
    turno = models.ForeignKey(
        "turnos.Turno",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="indicaciones_postoperatorias",
    )
    plantilla = models.ForeignKey(
        PlantillaIndicacion,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="indicaciones_generadas",
    )
    plantilla_version = models.PositiveIntegerField(null=True, blank=True, editable=False)
    titulo = models.CharField(max_length=180)
    procedimiento = models.CharField(max_length=180, blank=True)
    contenido = models.TextField()
    pautas_alarma = models.TextField(blank=True)
    recomendaciones_control = models.TextField(blank=True)
    observaciones_personalizadas = models.TextField(blank=True)
    proximo_control_en = models.DateTimeField(null=True, blank=True)
    estado = models.CharField(
        max_length=20,
        choices=Estado.choices,
        default=Estado.BORRADOR,
    )
    snapshot_paciente = models.JSONField(default=dict, editable=False)
    snapshot_profesional = models.JSONField(default=dict, editable=False)
    snapshot_consultorio = models.JSONField(default=dict, editable=False)
    snapshot_documento = models.JSONField(default=dict, editable=False)
    emitida_en = models.DateTimeField(null=True, blank=True, editable=False)
    emitida_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="indicaciones_emitidas",
    )
    anulada_en = models.DateTimeField(null=True, blank=True, editable=False)
    anulada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="indicaciones_anuladas",
    )
    motivo_anulacion = models.TextField(blank=True)
    reemplaza_a = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="documentos_reemplazo",
    )
    pdf = models.FileField(
        upload_to=ruta_privada_indicacion,
        storage=almacenamiento_indicaciones_privado,
        max_length=255,
        blank=True,
        editable=False,
    )
    pdf_sha256 = models.CharField(max_length=64, blank=True, editable=False)
    sello_integridad = models.CharField(max_length=64, blank=True, editable=False)
    referencia_integridad = models.CharField(max_length=12, blank=True, editable=False)
    email_destino = models.EmailField(blank=True, editable=False)
    email_estado = models.CharField(
        max_length=20,
        choices=EstadoEmail.choices,
        default=EstadoEmail.NO_APLICA,
        editable=False,
    )
    email_enviado_en = models.DateTimeField(null=True, blank=True, editable=False)
    email_ultimo_intento_en = models.DateTimeField(null=True, blank=True, editable=False)
    email_intentos = models.PositiveIntegerField(default=0, editable=False)
    email_clave_idempotencia = models.CharField(max_length=120, blank=True, editable=False)
    ultimo_error_email = models.TextField(blank=True, editable=False)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="indicaciones_creadas",
    )
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="indicaciones_actualizadas",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    objects = SinMutacionesMasivasQuerySet.as_manager()

    CAMPOS_INMUTABLES_EMITIDA = (
        "paciente_id",
        "odontologo_id",
        "historia_clinica_id",
        "turno_id",
        "plantilla_id",
        "plantilla_version",
        "titulo",
        "procedimiento",
        "contenido",
        "pautas_alarma",
        "recomendaciones_control",
        "observaciones_personalizadas",
        "proximo_control_en",
        "snapshot_paciente",
        "snapshot_profesional",
        "snapshot_consultorio",
        "snapshot_documento",
        "emitida_en",
        "emitida_por_id",
        "pdf_sha256",
        "sello_integridad",
        "referencia_integridad",
        "reemplaza_a_id",
        "creado_por_id",
        "creado_en",
    )

    class Meta:
        ordering = ["-emitida_en", "-creado_en"]
        verbose_name = "Indicación postoperatoria"
        verbose_name_plural = "Indicaciones postoperatorias"
        indexes = [
            models.Index(fields=["paciente", "estado", "-emitida_en"]),
            models.Index(fields=["odontologo", "estado", "-emitida_en"]),
            models.Index(fields=["email_estado", "estado"]),
            models.Index(fields=["plantilla", "plantilla_version"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        estado="borrador",
                        emitida_en__isnull=True,
                        emitida_por__isnull=True,
                        anulada_en__isnull=True,
                        anulada_por__isnull=True,
                        pdf="",
                        pdf_sha256="",
                        sello_integridad="",
                        email_estado="no_aplica",
                    )
                    | (
                        Q(
                            estado="emitida",
                            emitida_en__isnull=False,
                            emitida_por__isnull=False,
                            anulada_en__isnull=True,
                            anulada_por__isnull=True,
                            motivo_anulacion="",
                        )
                        & ~Q(pdf="")
                        & ~Q(pdf_sha256="")
                        & ~Q(sello_integridad="")
                    )
                    | (
                        Q(
                            estado="anulada",
                            emitida_en__isnull=False,
                            emitida_por__isnull=False,
                            anulada_en__isnull=False,
                            anulada_por__isnull=False,
                        )
                        & ~Q(motivo_anulacion="")
                        & ~Q(pdf="")
                        & ~Q(pdf_sha256="")
                        & ~Q(sello_integridad="")
                    )
                ),
                name="ind_documento_estado_coherente",
            ),
            models.CheckConstraint(
                condition=Q(reemplaza_a__isnull=True) | ~Q(pk=F("reemplaza_a")),
                name="ind_no_reemplazo_propio",
            ),
        ]

    @property
    def puede_editarse(self):
        return self.estado == self.Estado.BORRADOR

    @property
    def esta_reemplazada(self):
        if hasattr(self, "tiene_reemplazo_emitido"):
            return self.tiene_reemplazo_emitido
        return self.documentos_reemplazo.exclude(estado=self.Estado.BORRADOR).exists()

    def clean(self):
        errors = {}
        if not (self.titulo or "").strip():
            errors["titulo"] = "El título es obligatorio."
        if not (self.contenido or "").strip():
            errors["contenido"] = "Las indicaciones deben ser definidas por el profesional."
        if self.historia_clinica_id and self.historia_clinica.paciente_id != self.paciente_id:
            errors["historia_clinica"] = "La historia debe pertenecer al paciente."
        if self.turno_id and self.turno.paciente_id != self.paciente_id:
            errors["turno"] = "El turno debe pertenecer al paciente."
        if self.reemplaza_a_id:
            if self.pk and self.reemplaza_a_id == self.pk:
                errors["reemplaza_a"] = "Una indicación no puede reemplazarse a sí misma."
            elif self.reemplaza_a.paciente_id != self.paciente_id:
                errors["reemplaza_a"] = "El documento reemplazado debe ser del mismo paciente."

        if self.estado == self.Estado.BORRADOR:
            if any(
                [
                    self.emitida_en,
                    self.emitida_por_id,
                    self.pdf,
                    self.pdf_sha256,
                    self.sello_integridad,
                    self.anulada_en,
                    self.anulada_por_id,
                    self.motivo_anulacion,
                ]
            ):
                errors["estado"] = "Un borrador no puede tener datos de emisión o anulación."
            if any(
                [
                    self.snapshot_paciente,
                    self.snapshot_profesional,
                    self.snapshot_consultorio,
                    self.snapshot_documento,
                ]
            ):
                errors["estado"] = "Un borrador no puede contener snapshots definitivos."
        else:
            if not self.emitida_en or not self.emitida_por_id:
                errors["emitida_en"] = "Un documento emitido requiere fecha y autor."
            if not self.pdf or not _es_sha256(self.pdf_sha256):
                errors["pdf"] = "Un documento emitido requiere PDF y SHA-256 válidos."
            if not _es_sha256(self.sello_integridad):
                errors["sello_integridad"] = "El sello técnico no es válido."
            for campo in (
                "snapshot_paciente",
                "snapshot_profesional",
                "snapshot_consultorio",
                "snapshot_documento",
            ):
                if not isinstance(getattr(self, campo), dict) or not getattr(self, campo):
                    errors[campo] = "El snapshot de emisión es obligatorio."
            if self.estado == self.Estado.EMITIDA and (
                self.anulada_en or self.anulada_por_id or self.motivo_anulacion
            ):
                errors["estado"] = "Una indicación vigente no puede tener datos de anulación."
            if self.estado == self.Estado.ANULADA:
                if not self.anulada_en or not self.anulada_por_id:
                    errors["anulada_en"] = "La anulación requiere fecha y autor."
                if not (self.motivo_anulacion or "").strip():
                    errors["motivo_anulacion"] = "El motivo de anulación es obligatorio."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        permitir_emision = kwargs.pop("permitir_emision", False)
        permitir_anulacion = kwargs.pop("permitir_anulacion", False)
        permitir_actualizacion_email = kwargs.pop("permitir_actualizacion_email", False)
        if self.pk:
            anterior = type(self)._base_manager.get(pk=self.pk)
            self._validar_transicion(
                anterior,
                permitir_emision=permitir_emision,
                permitir_anulacion=permitir_anulacion,
                permitir_actualizacion_email=permitir_actualizacion_email,
            )
        self.titulo = (self.titulo or "").strip()
        self.motivo_anulacion = (self.motivo_anulacion or "").strip()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError("Las indicaciones no se pueden eliminar físicamente.", [self])

    def _validar_transicion(
        self,
        anterior,
        *,
        permitir_emision,
        permitir_anulacion,
        permitir_actualizacion_email,
    ):
        if anterior.estado == self.Estado.ANULADA:
            raise ValidationError("Una indicación anulada es inmutable.")

        if anterior.estado == self.Estado.BORRADOR:
            if self.estado == self.Estado.BORRADOR:
                return
            if self.estado == self.Estado.EMITIDA and permitir_emision:
                return
            raise ValidationError("La transición solicitada no está permitida.")

        campos_modificados = {
            campo
            for campo in self.CAMPOS_INMUTABLES_EMITIDA
            if getattr(self, campo) != getattr(anterior, campo)
        }
        if self.pdf.name != anterior.pdf.name:
            campos_modificados.add("pdf")
        if campos_modificados:
            raise ValidationError("El contenido de una indicación emitida es inmutable.")

        if permitir_actualizacion_email and self.estado == anterior.estado:
            return
        if (
            permitir_anulacion
            and anterior.estado == self.Estado.EMITIDA
            and self.estado == self.Estado.ANULADA
        ):
            return
        raise ValidationError("Una indicación emitida solo puede anularse.")

    def __str__(self):
        return f"{self.paciente} - {self.titulo} - {self.get_estado_display()}"
