from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


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
        verbose_name = "Historia clinica"
        verbose_name_plural = "Historias clinicas"
        indexes = [
            models.Index(fields=["paciente", "-fecha"]),
            models.Index(fields=["odontologo", "-fecha"]),
        ]

    def clean(self):
        errors = {}

        if self.fecha and self.fecha > timezone.localdate():
            errors["fecha"] = "La fecha de la atencion no puede ser futura."

        if self.proximo_control and self.fecha and self.proximo_control < self.fecha:
            errors["proximo_control"] = (
                "El proximo control no puede ser anterior a la fecha de atencion."
            )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.paciente} - {self.fecha:%d/%m/%Y}"
