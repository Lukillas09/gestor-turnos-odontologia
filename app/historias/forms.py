import json

from django import forms

from config.form_widgets import HtmlDateInput
from odontogramas.domain import DIENTES_FDI
from odontogramas.models import EstadoDental

from .models import HistoriaClinica, HistoriaClinicaAdjunto, validar_archivo_clinico


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
            "fecha",
            "motivo_consulta",
            "diagnostico",
            "tratamiento_realizado",
            "pieza_dental",
            "observaciones",
            "proximo_control",
        )
        labels = {
            "fecha": "Fecha de atención",
            "motivo_consulta": "Motivo de consulta",
            "tratamiento_realizado": "Tratamiento realizado",
            "pieza_dental": "Pieza dental",
            "proximo_control": "Próximo control",
        }
        widgets = {
            "fecha": HtmlDateInput(),
            "motivo_consulta": forms.Textarea(attrs={"rows": 3, "autofocus": True}),
            "diagnostico": forms.Textarea(attrs={"rows": 3}),
            "tratamiento_realizado": forms.Textarea(attrs={"rows": 3}),
            "observaciones": forms.Textarea(attrs={"rows": 3}),
            "proximo_control": HtmlDateInput(),
        }

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

    def guardar_adjuntos(self, historia, usuario):
        for archivo in self.cleaned_data.get("adjuntos", []):
            HistoriaClinicaAdjunto.objects.create(
                historia=historia,
                archivo=archivo,
                subido_por=usuario,
            )

    def guardar_estados_odontograma(self, historia, odontograma, usuario):
        from odontogramas.services import registrar_estado_dental

        for estado in self.cleaned_data.get("estados_odontograma", []):
            registrar_estado_dental(
                odontograma=odontograma,
                diente=estado["diente"],
                cara=estado["cara"],
                estado_clinico=estado["estado_clinico"],
                observacion=estado["observacion"],
                realizado=estado["realizado"],
                usuario=usuario,
                historia_clinica=historia,
            )
