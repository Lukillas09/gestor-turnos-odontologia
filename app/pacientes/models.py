from django.conf import settings
from django.db import models


class Paciente(models.Model):
    class Genero(models.TextChoices):
        FEMENINO = "femenino", "Femenino"
        MASCULINO = "masculino", "Masculino"
        OTRO = "otro", "Otro"
        PREFIERE_NO_DECIR = "prefiere_no_decir", "Prefiere no decir"

    nombre = models.CharField(max_length=100)
    apellido = models.CharField(max_length=100)
    documento = models.CharField(max_length=20, unique=True, null=True, blank=True)
    telefono = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
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
        if documento is None:
            return None

        documento = documento.strip()
        return documento or None

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
