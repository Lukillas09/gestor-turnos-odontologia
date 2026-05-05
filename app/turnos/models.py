from datetime import datetime, time, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Odontologo(models.Model):
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="perfil_odontologo",
    )
    matricula = models.CharField(max_length=50, unique=True)
    especialidad = models.CharField(max_length=100, blank=True)
    duracion_turno_minutos = models.PositiveSmallIntegerField(default=30)
    hora_inicio_atencion = models.TimeField(default=time(9, 0))
    hora_fin_atencion = models.TimeField(default=time(18, 0))
    color_calendario = models.CharField(max_length=7, default="#2f80ed")
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["usuario__last_name", "usuario__first_name", "usuario__username"]
        verbose_name = "Odontologo"
        verbose_name_plural = "Odontologos"

    def clean(self):
        errors = {}

        if self.duracion_turno_minutos <= 0:
            errors["duracion_turno_minutos"] = "La duracion debe ser mayor a 0."

        if self.hora_inicio_atencion >= self.hora_fin_atencion:
            errors["hora_fin_atencion"] = "La hora de fin debe ser posterior al inicio."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def nombre_completo(self):
        full_name = self.usuario.get_full_name()
        return full_name or self.usuario.username

    def __str__(self):
        return self.nombre_completo


class Turno(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        CONFIRMADO = "confirmado", "Confirmado"
        CANCELADO = "cancelado", "Cancelado"
        REALIZADO = "realizado", "Realizado"

    paciente = models.ForeignKey(
        "pacientes.Paciente",
        on_delete=models.PROTECT,
        related_name="turnos",
    )
    odontologo = models.ForeignKey(
        Odontologo,
        on_delete=models.PROTECT,
        related_name="turnos",
    )
    fecha = models.DateField()
    hora_inicio = models.TimeField()
    duracion_minutos = models.PositiveSmallIntegerField(default=30)
    motivo = models.CharField(max_length=200, blank=True)
    estado = models.CharField(
        max_length=20,
        choices=Estado.choices,
        default=Estado.PENDIENTE,
    )
    notas = models.TextField(blank=True)
    google_calendar_event_id = models.CharField(max_length=255, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["fecha", "hora_inicio"]
        verbose_name = "Turno"
        verbose_name_plural = "Turnos"
        indexes = [
            models.Index(fields=["fecha", "hora_inicio"]),
            models.Index(fields=["odontologo", "fecha"]),
            models.Index(fields=["estado"]),
        ]

    @property
    def fecha_hora_inicio(self):
        return datetime.combine(self.fecha, self.hora_inicio)

    @property
    def fecha_hora_fin(self):
        return self.fecha_hora_inicio + timedelta(minutes=self.duracion_minutos)

    @property
    def hora_fin(self):
        return self.fecha_hora_fin.time()

    def clean(self):
        errors = {}

        if self.duracion_minutos <= 0:
            errors["duracion_minutos"] = "La duracion debe ser mayor a 0."

        if not errors and self.fecha and self.hora_inicio and self.odontologo_id:
            self._validar_horario_atencion(errors)
            self._validar_solapamiento(errors)

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def _validar_horario_atencion(self, errors):
        if self.fecha_hora_fin.date() != self.fecha:
            errors["duracion_minutos"] = "El turno debe terminar el mismo dia."
            return

        if self.hora_inicio < self.odontologo.hora_inicio_atencion:
            errors["hora_inicio"] = "El turno empieza antes del horario de atencion."

        if self.hora_fin > self.odontologo.hora_fin_atencion:
            errors["duracion_minutos"] = "El turno termina fuera del horario de atencion."

    def _validar_solapamiento(self, errors):
        if self.estado == self.Estado.CANCELADO:
            return

        turnos_activos = Turno.objects.filter(
            odontologo=self.odontologo,
            fecha=self.fecha,
            estado__in=[self.Estado.PENDIENTE, self.Estado.CONFIRMADO],
        )

        if self.pk:
            turnos_activos = turnos_activos.exclude(pk=self.pk)

        for turno in turnos_activos:
            if (
                self.fecha_hora_inicio < turno.fecha_hora_fin
                and self.fecha_hora_fin > turno.fecha_hora_inicio
            ):
                errors["hora_inicio"] = "Ya existe un turno para ese odontologo en ese horario."
                return

    def __str__(self):
        return f"{self.fecha} {self.hora_inicio} - {self.paciente}"
