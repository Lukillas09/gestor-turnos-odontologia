from django import forms

from .models import Paciente


class PacienteForm(forms.ModelForm):
    class Meta:
        model = Paciente
        fields = (
            "nombre",
            "apellido",
            "documento",
            "telefono",
            "email",
            "fecha_nacimiento",
            "observaciones",
        )
        labels = {
            "documento": "DNI",
            "fecha_nacimiento": "Fecha de nacimiento",
        }
        widgets = {
            "nombre": forms.TextInput(attrs={"autofocus": True}),
            "fecha_nacimiento": forms.DateInput(attrs={"type": "date"}),
            "observaciones": forms.Textarea(attrs={"rows": 4}),
        }
