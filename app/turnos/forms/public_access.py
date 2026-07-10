from django import forms
from django.core.exceptions import ValidationError

from config.form_widgets import HtmlDateInput
from pacientes.normalizacion import normalizar_documento

from ..excepciones import (
    obtener_horarios_publicos_disponibles,
    obtener_rango_reserva_publica,
    validar_fecha_reserva_publica,
    validar_intervalo_reserva_publica,
)
from ..models import Turno
from .fields import HorarioDisponibleChoiceField, convertir_a_hora
from .solicitudes_publicas import DURACION_SOLICITUD_PUBLICA_MINUTOS
from .turnos import HorariosDisponiblesFormMixin


class SolicitudAccesoPublicoTurnosForm(forms.Form):
    documento = forms.CharField(
        max_length=20,
        label="DNI",
        error_messages={"required": "Ingresá tu DNI para solicitar acceso."},
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "inputmode": "numeric",
                "placeholder": "DNI del paciente",
            }
        ),
    )
    turnstile_token = forms.CharField(required=False, widget=forms.HiddenInput())

    def clean_documento(self):
        return normalizar_documento(self.cleaned_data["documento"]) or ""


class VerificacionAccesoPublicoTurnosForm(forms.Form):
    codigo = forms.CharField(
        min_length=6,
        max_length=6,
        label="Código de acceso",
        error_messages={
            "required": "Ingresá el código de acceso.",
            "min_length": "El código debe tener 6 dígitos.",
            "max_length": "El código debe tener 6 dígitos.",
        },
        widget=forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "placeholder": "000000",
            }
        ),
    )

    def clean_codigo(self):
        codigo = self.cleaned_data["codigo"].strip()

        if not codigo.isdigit():
            raise forms.ValidationError("El código debe tener 6 dígitos.")

        return codigo


class CancelacionAccesoPublicoTurnoForm(forms.Form):
    accion_token = forms.CharField(widget=forms.HiddenInput())
    motivo_cancelacion = forms.CharField(
        required=False,
        max_length=500,
        label="Motivo de cancelación",
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "placeholder": "Motivo de cancelación",
            }
        ),
    )


class TurnoReprogramacionAccesoPublicoForm(HorariosDisponiblesFormMixin, forms.ModelForm):
    accion_token = forms.CharField(widget=forms.HiddenInput())
    hora_inicio = HorarioDisponibleChoiceField(
        choices=(),
        coerce=convertir_a_hora,
        empty_value=None,
        label="Nuevo horario",
    )

    class Meta:
        model = Turno
        fields = ("fecha", "hora_inicio")
        labels = {
            "fecha": "Nueva fecha",
        }
        widgets = {
            "fecha": HtmlDateInput(),
        }

    def __init__(self, *args, **kwargs):
        accion_token = kwargs.pop("accion_token", "")
        super().__init__(*args, **kwargs)
        self.fields["accion_token"].initial = accion_token
        self.initial.setdefault("fecha", self.instance.fecha)
        self.initial.setdefault("hora_inicio", self._formatear_horario(self.instance.hora_inicio))
        rango = obtener_rango_reserva_publica()
        self.fields["fecha"].widget.attrs.update(
            {
                "min": rango.fecha_minima.isoformat(),
                "max": rango.fecha_maxima.isoformat(),
            }
        )
        self._configurar_horarios_disponibles()

    def _obtener_odontologo_seleccionado(self):
        if self.instance and self.instance.odontologo_id and self.instance.odontologo.activo:
            return self.instance.odontologo

        return None

    def _obtener_duracion_minutos(self, odontologo):
        return self.instance.duracion_minutos or DURACION_SOLICITUD_PUBLICA_MINUTOS

    def _obtener_turno_excluido(self):
        return self.instance

    def _obtener_horarios_disponibles(self, **kwargs):
        return obtener_horarios_publicos_disponibles(**kwargs)

    def clean_accion_token(self):
        return self.cleaned_data["accion_token"].strip()

    def clean_fecha(self):
        fecha = self.cleaned_data["fecha"]
        try:
            validar_fecha_reserva_publica(fecha)
        except ValidationError as error:
            raise forms.ValidationError(error.messages) from error

        return fecha

    def clean(self):
        cleaned_data = super().clean()
        fecha = cleaned_data.get("fecha")
        hora_inicio = cleaned_data.get("hora_inicio")

        if fecha and hora_inicio:
            try:
                validar_intervalo_reserva_publica(
                    fecha,
                    hora_inicio,
                    self.instance.duracion_minutos or DURACION_SOLICITUD_PUBLICA_MINUTOS,
                )
            except ValidationError as error:
                raise forms.ValidationError(error.messages) from error

        return cleaned_data

    def save(self, commit=True):
        turno = super().save(commit=False)

        if commit:
            turno.save(update_fields=["fecha", "hora_inicio", "actualizado_en"])

        return turno
