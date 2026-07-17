import json

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from config.form_widgets import HtmlDateInput
from odontogramas.domain import DIENTES_FDI
from odontogramas.models import EstadoDental

from .models import HistoriaClinica, validar_archivo_clinico
from .services import validar_motivo_cambio


class HtmlDateTimeInput(forms.DateTimeInput):
    input_type = "datetime-local"

    def __init__(self, attrs=None, format=None):
        attrs = {"step": "60", **(attrs or {})}
        super().__init__(attrs=attrs, format=format or "%Y-%m-%dT%H:%M")


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        if not data:
            return []

        archivos = data if isinstance(data, (list, tuple)) else [data]
        return [forms.FileField.clean(self, archivo, initial) for archivo in archivos]


class HistoriaClinicaFiltroForm(forms.Form):
    q = forms.CharField(
        required=False,
        label="Buscar",
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "placeholder": "Motivo, diagnóstico, tratamiento, pieza...",
            }
        ),
    )
    fecha_desde = forms.DateField(
        required=False,
        label="Desde",
        widget=HtmlDateInput(),
    )
    fecha_hasta = forms.DateField(
        required=False,
        label="Hasta",
        widget=HtmlDateInput(),
    )

    def clean(self):
        cleaned_data = super().clean()
        fecha_desde = cleaned_data.get("fecha_desde")
        fecha_hasta = cleaned_data.get("fecha_hasta")

        if fecha_desde and fecha_hasta and fecha_desde > fecha_hasta:
            raise forms.ValidationError("La fecha desde no puede ser posterior a la fecha hasta.")

        return cleaned_data


class HistoriaClinicaForm(forms.ModelForm):
    motivo_cambio = forms.CharField(
        required=False,
        label="Motivo de la modificación",
        help_text="Explicá qué información corregiste o completaste.",
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    estados_odontograma = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
    )
    adjuntos = MultipleFileField(
        required=False,
        label="Adjuntos",
        help_text="Podés adjuntar radiografías, imágenes o PDF. Máximo 10 MB por archivo.",
    )

    class Meta:
        model = HistoriaClinica
        fields = (
            "fecha_hora_atencion",
            "motivo_consulta",
            "diagnostico",
            "tratamiento_realizado",
            "pieza_dental",
            "observaciones",
            "proximo_control",
        )
        labels = {
            "fecha_hora_atencion": "Fecha y hora de atención",
            "motivo_consulta": "Motivo de consulta",
            "diagnostico": "Diagnóstico",
            "tratamiento_realizado": "Tratamiento realizado",
            "pieza_dental": "Pieza dental",
            "proximo_control": "Próximo control",
        }
        widgets = {
            "fecha_hora_atencion": HtmlDateTimeInput(),
            "motivo_consulta": forms.Textarea(attrs={"rows": 3, "autofocus": True}),
            "diagnostico": forms.Textarea(attrs={"rows": 3}),
            "tratamiento_realizado": forms.Textarea(attrs={"rows": 3}),
            "observaciones": forms.Textarea(attrs={"rows": 3}),
            "proximo_control": HtmlDateInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["fecha_hora_atencion"].input_formats = [
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%dT%H:%M:%S",
        ]

        if self.instance and self.instance.pk:
            self.fields["motivo_cambio"].required = True
        else:
            self.fields.pop("motivo_cambio", None)

        if not settings.ODONTOGRAMA_FEATURE_ENABLED:
            self.fields.pop("estados_odontograma", None)

    def clean_motivo_cambio(self):
        motivo = self.cleaned_data.get("motivo_cambio")
        if self.instance and self.instance.pk:
            try:
                return validar_motivo_cambio(motivo)
            except ValidationError as error:
                raise forms.ValidationError(error.messages) from error
        return ""

    def clean_adjuntos(self):
        adjuntos = self.cleaned_data.get("adjuntos") or []

        for adjunto in adjuntos:
            validar_archivo_clinico(adjunto)

        return adjuntos

    def clean_estados_odontograma(self):
        valor = self.cleaned_data.get("estados_odontograma")

        if not valor:
            return []

        try:
            datos = json.loads(valor)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(
                "No se pudieron interpretar los cambios del odontograma."
            ) from exc

        if not isinstance(datos, list):
            raise forms.ValidationError("Los cambios del odontograma no son válidos.")

        estados = []

        for item in datos:
            if not isinstance(item, dict):
                raise forms.ValidationError("Los cambios del odontograma no son válidos.")

            try:
                diente = int(item.get("diente"))
            except (TypeError, ValueError) as exc:
                raise forms.ValidationError("El diente del odontograma no es válido.") from exc

            cara = item.get("cara")
            estado_clinico = item.get("estado_clinico")

            if diente not in DIENTES_FDI:
                raise forms.ValidationError("El diente del odontograma no es válido.")
            if cara not in EstadoDental.CaraDental.values:
                raise forms.ValidationError("La cara dental seleccionada no es válida.")
            if estado_clinico not in EstadoDental.EstadoClinico.values:
                raise forms.ValidationError("El estado clínico seleccionado no es válido.")

            estados.append(
                {
                    "diente": diente,
                    "cara": cara,
                    "estado_clinico": estado_clinico,
                    "observacion": (item.get("observacion") or "").strip(),
                    "realizado": bool(item.get("realizado")),
                }
            )

        return estados


class FinalizarHistoriaClinicaForm(forms.Form):
    confirmar = forms.BooleanField(
        required=True,
        label="Confirmo que la entrada está completa y debe quedar bloqueada.",
    )


class HistoriaClinicaEnmiendaForm(forms.Form):
    texto = forms.CharField(
        label="Texto de la enmienda",
        widget=forms.Textarea(attrs={"rows": 6, "autofocus": True}),
    )
    motivo = forms.CharField(
        label="Motivo",
        help_text="Explicá por qué se agrega esta corrección o aclaración.",
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def clean_texto(self):
        texto = (self.cleaned_data.get("texto") or "").strip()
        if not texto:
            raise forms.ValidationError("El texto de la enmienda es obligatorio.")
        return texto

    def clean_motivo(self):
        try:
            return validar_motivo_cambio(self.cleaned_data.get("motivo"))
        except ValidationError as error:
            raise forms.ValidationError(error.messages) from error


class ExportarHistoriaClinicaForm(forms.Form):
    class Motivo(models.TextChoices):
        SOLICITUD_PACIENTE = "solicitud_paciente", "Solicitud del paciente"
        INTERCONSULTA = "interconsulta", "Interconsulta autorizada"
        AUDITORIA = "auditoria", "Auditoría autorizada"
        OTRO = "otro", "Otro procedimiento autorizado"

    motivo = forms.ChoiceField(
        choices=Motivo.choices,
        label="Motivo de la exportación",
    )
