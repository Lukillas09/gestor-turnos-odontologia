from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from .normalizacion import normalizar_documento


class Paciente(models.Model):
    class Genero(models.TextChoices):
        FEMENINO = "femenino", "Femenino"
        MASCULINO = "masculino", "Masculino"
        OTRO = "otro", "Otro"
        PREFIERE_NO_DECIR = "prefiere_no_decir", "Prefiere no decir"

    class EstadoValidacionDatos(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        VALIDADO = "validado", "Validado"

    class OrigenAlta(models.TextChoices):
        INTERNO = "interno", "Carga interna"
        SOLICITUD_PUBLICA = "solicitud_publica", "Solicitud publica"

    nombre = models.CharField(max_length=100)
    apellido = models.CharField(max_length=100)
    documento = models.CharField(max_length=20, unique=True, null=True, blank=True)
    telefono = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    email_verificado_en = models.DateTimeField(null=True, blank=True)
    telefono_verificado_en = models.DateTimeField(null=True, blank=True)
    estado_validacion_datos = models.CharField(
        max_length=20,
        choices=EstadoValidacionDatos.choices,
        default=EstadoValidacionDatos.VALIDADO,
    )
    origen_alta = models.CharField(
        max_length=30,
        choices=OrigenAlta.choices,
        default=OrigenAlta.INTERNO,
    )
    validado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pacientes_validados",
    )
    validado_en = models.DateTimeField(null=True, blank=True)
    fecha_nacimiento = models.DateField(null=True, blank=True)
    genero = models.CharField(max_length=30, choices=Genero.choices, blank=True)
    domicilio = models.CharField(max_length=200, blank=True)
    localidad = models.CharField(max_length=100, blank=True)
    obra_social = models.CharField(max_length=100, blank=True)
    numero_afiliado = models.CharField(max_length=50, blank=True)
    contacto_emergencia = models.CharField(max_length=150, blank=True)
    observaciones = models.TextField(blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["apellido", "nombre"]
        verbose_name = "Paciente"
        verbose_name_plural = "Pacientes"

    @property
    def nombre_completo(self):
        return f"{self.apellido}, {self.nombre}"

    def clean(self):
        self.documento = self._normalizar_documento(self.documento)

    def save(self, *args, **kwargs):
        self.documento = self._normalizar_documento(self.documento)
        super().save(*args, **kwargs)

    @staticmethod
    def _normalizar_documento(documento):
        return normalizar_documento(documento)

    def __str__(self):
        return self.nombre_completo


class FichaOdontologica(models.Model):
    class RespuestaClinica(models.TextChoices):
        SIN_DATOS = "", "Sin datos"
        SI = "si", "Si"
        NO = "no", "No"

    paciente = models.OneToOneField(
        Paciente,
        on_delete=models.CASCADE,
        related_name="ficha_odontologica",
    )
    antecedentes_medicos = models.TextField(blank=True)
    alergias = models.TextField(blank=True)
    medicacion_actual = models.TextField(blank=True)
    enfermedades_relevantes = models.TextField(blank=True)
    embarazo = models.CharField(
        max_length=10,
        choices=RespuestaClinica.choices,
        blank=True,
    )
    hipertension = models.CharField(
        max_length=10,
        choices=RespuestaClinica.choices,
        blank=True,
    )
    diabetes = models.CharField(
        max_length=10,
        choices=RespuestaClinica.choices,
        blank=True,
    )
    problemas_cardiacos = models.CharField(
        max_length=10,
        choices=RespuestaClinica.choices,
        blank=True,
    )
    observaciones_generales = models.TextField(blank=True)
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fichas_odontologicas_actualizadas",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Ficha odontológica"
        verbose_name_plural = "Fichas odontológicas"

    def __str__(self):
        return f"Ficha odontológica de {self.paciente}"


class PacienteOdontologo(models.Model):
    paciente = models.ForeignKey(
        Paciente,
        on_delete=models.CASCADE,
        related_name="odontologos_asociados",
    )
    odontologo = models.ForeignKey(
        "turnos.Odontologo",
        on_delete=models.CASCADE,
        related_name="pacientes_asociados",
    )
    asignado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="asignaciones_pacientes_odontologos",
    )
    motivo = models.TextField(blank=True)
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["paciente__apellido", "paciente__nombre", "odontologo"]
        verbose_name = "Asociación paciente-odontólogo"
        verbose_name_plural = "Asociaciones paciente-odontólogo"
        indexes = [
            models.Index(fields=["paciente", "activo"]),
            models.Index(fields=["odontologo", "activo"]),
            models.Index(fields=["paciente", "odontologo", "activo"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["paciente", "odontologo"],
                condition=Q(activo=True),
                name="uniq_paciente_odontologo_activo",
            )
        ]

    def clean(self):
        if not self.activo:
            return

        if not self.paciente_id or not self.odontologo_id:
            return

        asociaciones = PacienteOdontologo.objects.filter(
            paciente=self.paciente,
            odontologo=self.odontologo,
            activo=True,
        )

        if self.pk:
            asociaciones = asociaciones.exclude(pk=self.pk)

        if asociaciones.exists():
            raise ValidationError(
                "Ya existe una asociación activa entre este paciente y odontólogo."
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.paciente} - {self.odontologo}"
