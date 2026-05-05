from django.db import models


class Paciente(models.Model):
    nombre = models.CharField(max_length=100)
    apellido = models.CharField(max_length=100)
    documento = models.CharField(max_length=20, unique=True, null=True, blank=True)
    telefono = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    fecha_nacimiento = models.DateField(null=True, blank=True)
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
