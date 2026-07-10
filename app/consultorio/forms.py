from django import forms

from .models import ConfiguracionConsultorio
from .validators import normalizar_color_hex, validar_logo_consultorio


class ConfiguracionConsultorioForm(forms.ModelForm):
    quitar_logo = forms.BooleanField(
        required=False,
        label="Quitar logo actual",
        help_text="Marcá esta opción si querés dejar la marca solo con texto.",
    )

    class Meta:
        model = ConfiguracionConsultorio
        fields = [
            "nombre_comercial",
            "nombre_corto",
            "logo",
            "direccion",
            "localidad",
            "provincia",
            "telefono",
            "whatsapp",
            "email",
            "horario_atencion",
            "titulo_portada",
            "texto_bienvenida",
            "politica_cancelacion",
            "color_principal",
            "mostrar_direccion",
            "mostrar_telefono",
            "mostrar_whatsapp",
            "mostrar_email",
            "mostrar_horario_atencion",
            "ventana_reserva_publica_dias",
            "permitir_reserva_publica_mismo_dia",
            "anticipacion_minima_reserva_publica_minutos",
        ]
        widgets = {
            "color_principal": forms.TextInput(attrs={"type": "color"}),
            "horario_atencion": forms.Textarea(attrs={"rows": 3}),
            "texto_bienvenida": forms.Textarea(attrs={"rows": 4}),
            "politica_cancelacion": forms.Textarea(attrs={"rows": 4}),
            "ventana_reserva_publica_dias": forms.NumberInput(attrs={"min": 1, "max": 90}),
            "anticipacion_minima_reserva_publica_minutos": forms.NumberInput(
                attrs={"min": 0, "max": 10080, "step": 15}
            ),
            "logo": forms.FileInput(
                attrs={"accept": ".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp"}
            ),
        }
        help_texts = {
            "nombre_corto": "Se usa en encabezados y menús si el nombre comercial es largo.",
            "logo": "PNG, JPG, JPEG o WEBP. Máximo 2 MB. No se permiten SVG.",
            "whatsapp": (
                "Conviene ingresar código de país y característica. "
                "Se publicará como enlace seguro de WhatsApp."
            ),
            "horario_atencion": (
                "Texto informativo. No modifica la disponibilidad real de los odontólogos."
            ),
            "politica_cancelacion": (
                "Texto público opcional. Se mostrará escapado, sin interpretar HTML."
            ),
            "color_principal": "Color de marca en formato HEX.",
            "ventana_reserva_publica_dias": (
                "Cantidad máxima de días visibles y reservables desde la página pública. "
                "El día actual cuenta dentro de la ventana."
            ),
            "anticipacion_minima_reserva_publica_minutos": (
                "Tiempo mínimo entre el momento actual y el inicio del turno público."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        for _nombre, campo in self.fields.items():
            if isinstance(campo.widget, forms.CheckboxInput):
                continue
            campo.widget.attrs.setdefault("class", "form-control")

    def clean_color_principal(self):
        return normalizar_color_hex(self.cleaned_data["color_principal"])

    def clean_logo(self):
        logo = self.cleaned_data.get("logo")

        if logo:
            validar_logo_consultorio(logo)

        return logo

    def clean(self):
        cleaned_data = super().clean() or {}
        logo = cleaned_data.get("logo")
        quitar_logo = cleaned_data.get("quitar_logo")
        logo_nuevo = bool(logo) and not getattr(logo, "_committed", False)

        if logo_nuevo and quitar_logo:
            raise forms.ValidationError(
                "Elegí entre subir un nuevo logo o quitar el actual, no ambas opciones."
            )

        return cleaned_data
