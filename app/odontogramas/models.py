from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from pacientes.models import Paciente
from turnos.models import Odontologo

from .domain import CARAS_DENTALES, COLORES_HEX, DIENTES_FDI, color_para_estado


class Odontograma(models.Model):
    paciente = models.OneToOneField(
        Paciente,
        on_delete=models.CASCADE,
        related_name="odontograma",
    )
    observaciones_generales = models.TextField(blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["paciente__apellido", "paciente__nombre"]
        verbose_name = "Odontograma"
        verbose_name_plural = "Odontogramas"

    def __str__(self):
        return f"Odontograma de {self.paciente}"


class EstadoDental(models.Model):
    class EstadoClinico(models.TextChoices):
        SANO = "sano", "Sano"
        CARIES = "caries", "Caries"
        RESTAURACION_NECESARIA = "restauracion_necesaria", "Restauración necesaria"
        EXTRACCION_INDICADA = "extraccion_indicada", "Extracción indicada"
        OBTURACION = "obturacion", "Obturación"
        CORONA = "corona", "Corona"
        IMPLANTE = "implante", "Implante"
        CONDUCTO = "conducto", "Conducto"
        FRACTURA = "fractura", "Fractura"
        SELLADOR = "sellador", "Sellador"
        PROTESIS = "protesis", "Prótesis"
        TEMPORAL = "temporal", "Temporal"
        CONTROL = "control", "Control"
        AUSENTE = "ausente", "Ausente"
        EXTRAIDO = "extraido", "Extraído"
        OBSERVACION_ESPECIAL = "observacion_especial", "Observación especial"

    class CaraDental(models.TextChoices):
        VESTIBULAR = "vestibular", "Vestibular"
        LINGUAL_PALATINA = "lingual_palatina", "Lingual / palatina"
        MESIAL = "mesial", "Mesial"
        DISTAL = "distal", "Distal"
        OCLUSAL_INCISAL = "oclusal_incisal", "Oclusal / incisal"

    class ColorClinico(models.TextChoices):
        AZUL = "azul", "Azul"
        ROJO = "rojo", "Rojo"
        VERDE = "verde", "Verde"
        NEGRO = "negro", "Negro"
        NEUTRO = "neutro", "Sin color"

    odontograma = models.ForeignKey(
        Odontograma,
        on_delete=models.CASCADE,
        related_name="estados_dentales",
    )
    diente = models.PositiveSmallIntegerField()
    cara = models.CharField(max_length=30, choices=CaraDental.choices)
    estado_clinico = models.CharField(max_length=40, choices=EstadoClinico.choices)
    color = models.CharField(
        max_length=20,
        choices=ColorClinico.choices,
        default=ColorClinico.NEUTRO,
    )
    observacion = models.TextField(blank=True)
    odontologo = models.ForeignKey(
        Odontologo,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="estados_dentales_registrados",
    )
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="estados_dentales_registrados",
    )
    fecha = models.DateField(default=timezone.localdate)
    realizado = models.BooleanField(default=False)
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha", "-creado_en"]
        verbose_name = "Estado dental"
        verbose_name_plural = "Estados dentales"
        indexes = [
            models.Index(fields=["odontograma", "diente", "cara", "activo"]),
            models.Index(fields=["odontograma", "fecha"]),
            models.Index(fields=["odontologo", "fecha"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["odontograma", "diente", "cara"],
                condition=Q(activo=True),
                name="uniq_estado_dental_activo_por_cara",
            ),
        ]

    def clean(self):
        errors = {}

        if self.diente not in DIENTES_FDI:
            errors["diente"] = "El diente no pertenece a la nomenclatura FDI configurada."

        if self.cara not in CARAS_DENTALES:
            errors["cara"] = "La cara dental no es válida."

        color_esperado = color_para_estado(self.estado_clinico)
        if self.color and self.color != color_esperado:
            errors["color"] = "El color clínico debe corresponder al estado seleccionado."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.color = color_para_estado(self.estado_clinico)
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def color_hex(self):
        return COLORES_HEX.get(self.color, COLORES_HEX["neutro"])

    @property
    def cara_display(self):
        return CARAS_DENTALES.get(self.cara, self.cara)

    def __str__(self):
        return f"{self.diente} {self.cara_display} - {self.get_estado_clinico_display()}"
