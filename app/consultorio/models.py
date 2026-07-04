from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.deletion import ProtectedError

from .validators import normalizar_color_hex, validar_color_hex, validar_logo_consultorio


CONFIGURACION_CONSULTORIO_PK = 1
COLOR_PRINCIPAL_DEFAULT = "#2563EB"
NOMBRE_COMERCIAL_DEFAULT = "Gestor de Turnos"
VENTANA_RESERVA_PUBLICA_DIAS_DEFAULT = 14
ANTICIPACION_MINIMA_RESERVA_PUBLICA_MINUTOS_DEFAULT = 120
TITULO_PORTADA_DEFAULT = "Reservá tu turno odontológico de forma simple"
TEXTO_BIENVENIDA_DEFAULT = (
    "Elegí un odontólogo, seleccioná un horario disponible y enviá tu solicitud. "
    "El consultorio te avisará cuando el turno quede confirmado."
)


def ruta_logo_consultorio(instance, filename):
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
    return f"consultorio/identidad/logo/logo.{extension}"


class ConfiguracionConsultorio(models.Model):
    nombre_comercial = models.CharField(
        max_length=120,
        default=NOMBRE_COMERCIAL_DEFAULT,
    )
    nombre_corto = models.CharField(max_length=80, blank=True)
    logo = models.FileField(
        upload_to=ruta_logo_consultorio,
        blank=True,
        validators=[validar_logo_consultorio],
    )
    direccion = models.CharField(max_length=180, blank=True)
    localidad = models.CharField(max_length=100, blank=True)
    provincia = models.CharField(max_length=100, blank=True)
    telefono = models.CharField(max_length=40, blank=True)
    whatsapp = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    horario_atencion = models.TextField(blank=True)
    titulo_portada = models.CharField(
        max_length=160,
        default=TITULO_PORTADA_DEFAULT,
    )
    texto_bienvenida = models.TextField(default=TEXTO_BIENVENIDA_DEFAULT)
    politica_cancelacion = models.TextField(blank=True)
    color_principal = models.CharField(
        max_length=7,
        default=COLOR_PRINCIPAL_DEFAULT,
        validators=[validar_color_hex],
    )
    mostrar_direccion = models.BooleanField(default=True)
    mostrar_telefono = models.BooleanField(default=True)
    mostrar_whatsapp = models.BooleanField(default=True)
    mostrar_email = models.BooleanField(default=True)
    mostrar_horario_atencion = models.BooleanField(default=True)
    ventana_reserva_publica_dias = models.PositiveSmallIntegerField(
        default=VENTANA_RESERVA_PUBLICA_DIAS_DEFAULT,
    )
    permitir_reserva_publica_mismo_dia = models.BooleanField(default=True)
    anticipacion_minima_reserva_publica_minutos = models.PositiveIntegerField(
        default=ANTICIPACION_MINIMA_RESERVA_PUBLICA_MINUTOS_DEFAULT,
    )
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="configuraciones_consultorio_actualizadas",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuración del consultorio"
        verbose_name_plural = "Configuración del consultorio"
        constraints = [
            models.CheckConstraint(
                name="configuracion_consultorio_pk_unico",
                condition=Q(id=CONFIGURACION_CONSULTORIO_PK),
            )
        ]

    def clean(self):
        errors = {}

        if self.pk not in (None, CONFIGURACION_CONSULTORIO_PK):
            errors["id"] = "Solo puede existir una configuración del consultorio."

        if self.color_principal:
            try:
                self.color_principal = normalizar_color_hex(self.color_principal)
            except ValidationError as error:
                errors["color_principal"] = error

        if not 1 <= self.ventana_reserva_publica_dias <= 90:
            errors["ventana_reserva_publica_dias"] = (
                "La ventana pública debe estar entre 1 y 90 días."
            )

        if not 0 <= self.anticipacion_minima_reserva_publica_minutos <= 10080:
            errors["anticipacion_minima_reserva_publica_minutos"] = (
                "La anticipación mínima debe estar entre 0 minutos y 7 días."
            )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk is None:
            self.pk = CONFIGURACION_CONSULTORIO_PK

        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError(
            "La configuración del consultorio no se puede borrar.",
            self,
        )

    @property
    def nombre_visible(self):
        return (self.nombre_corto or self.nombre_comercial or NOMBRE_COMERCIAL_DEFAULT).strip()

    @property
    def direccion_completa(self):
        partes = [
            self.direccion.strip(),
            self.localidad.strip(),
            self.provincia.strip(),
        ]
        return ", ".join(parte for parte in partes if parte)

    @property
    def whatsapp_normalizado(self):
        return "".join(caracter for caracter in (self.whatsapp or "") if caracter.isdigit())

    @property
    def whatsapp_url(self):
        numero = self.whatsapp_normalizado
        return f"https://wa.me/{numero}" if numero else ""

    @property
    def logo_url(self):
        if not self.logo:
            return ""

        try:
            return self.logo.url
        except Exception:
            return ""

    @property
    def iniciales(self):
        palabras = [palabra for palabra in self.nombre_visible.split() if palabra]

        if not palabras:
            return "GT"

        if len(palabras) == 1:
            return palabras[0][:2].upper()

        return f"{palabras[0][0]}{palabras[1][0]}".upper()

    @property
    def color_principal_oscuro(self):
        return _mezclar_color(self.color_principal, "#000000", 0.20)

    @property
    def color_principal_suave(self):
        return _mezclar_color(self.color_principal, "#FFFFFF", 0.88)

    @property
    def color_texto_sobre_principal(self):
        r, g, b = _hex_a_rgb(self.color_principal)
        luminancia = ((r * 299) + (g * 587) + (b * 114)) / 1000
        return "#111827" if luminancia > 150 else "#FFFFFF"

    def __str__(self):
        return self.nombre_visible


def _hex_a_rgb(valor):
    color = normalizar_color_hex(valor).lstrip("#")
    return tuple(int(color[indice : indice + 2], 16) for indice in (0, 2, 4))


def _rgb_a_hex(r, g, b):
    return f"#{r:02X}{g:02X}{b:02X}"


def _mezclar_color(origen, destino, proporcion_destino):
    r1, g1, b1 = _hex_a_rgb(origen)
    r2, g2, b2 = _hex_a_rgb(destino)
    proporcion_origen = 1 - proporcion_destino
    return _rgb_a_hex(
        round((r1 * proporcion_origen) + (r2 * proporcion_destino)),
        round((g1 * proporcion_origen) + (g2 * proporcion_destino)),
        round((b1 * proporcion_origen) + (b2 * proporcion_destino)),
    )
