from django import forms

from .models import HistoriaClinica


class HistoriaClinicaForm(forms.ModelForm):
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
            "fecha": "Fecha de atencion",
            "motivo_consulta": "Motivo de consulta",
            "tratamiento_realizado": "Tratamiento realizado",
            "pieza_dental": "Pieza dental",
            "proximo_control": "Proximo control",
        }
        widgets = {
            "fecha": forms.DateInput(attrs={"type": "date"}),
            "motivo_consulta": forms.Textarea(attrs={"rows": 3, "autofocus": True}),
            "diagnostico": forms.Textarea(attrs={"rows": 3}),
            "tratamiento_realizado": forms.Textarea(attrs={"rows": 3}),
            "observaciones": forms.Textarea(attrs={"rows": 3}),
            "proximo_control": forms.DateInput(attrs={"type": "date"}),
        }
