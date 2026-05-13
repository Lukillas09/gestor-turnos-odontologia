from django import forms

from .domain import color_para_estado
from .models import EstadoDental


class EstadoDentalForm(forms.ModelForm):
    class Meta:
        model = EstadoDental
        fields = (
            "diente",
            "cara",
            "estado_clinico",
            "observacion",
            "realizado",
        )
        labels = {
            "diente": "Diente",
            "cara": "Cara",
            "estado_clinico": "Estado clínico",
            "observacion": "Observación",
            "realizado": "Tratamiento realizado",
        }
        widgets = {
            "diente": forms.HiddenInput(),
            "cara": forms.HiddenInput(),
            "observacion": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": "Agregá una observación clínica breve.",
                }
            ),
        }

    def clean(self):
        cleaned_data = super().clean()
        estado_clinico = cleaned_data.get("estado_clinico")

        if estado_clinico:
            self.instance.color = color_para_estado(estado_clinico)

        return cleaned_data
