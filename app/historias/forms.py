from django import forms

from config.form_widgets import HtmlDateInput

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
            attrs={"placeholder": "Motivo, diagnóstico, tratamiento, pieza..."}
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

    def guardar_adjuntos(self, historia, usuario):
        for archivo in self.cleaned_data.get("adjuntos", []):
            HistoriaClinicaAdjunto.objects.create(
                historia=historia,
                archivo=archivo,
                subido_por=usuario,
            )
