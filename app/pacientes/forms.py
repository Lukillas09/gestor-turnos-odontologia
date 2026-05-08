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


class PacienteDeleteConfirmationForm(forms.Form):
    nombre = forms.CharField(max_length=100)
    apellido = forms.CharField(max_length=100)
    documento = forms.CharField(max_length=20, label="DNI")

    def __init__(self, *args, paciente, **kwargs):
        super().__init__(*args, **kwargs)
        self.paciente = paciente

    def clean(self):
        datos = super().clean()

        if not self.paciente.documento:
            raise forms.ValidationError(
                "El paciente no tiene DNI cargado. Agrega el DNI antes de borrar."
            )

        if not all(datos.get(campo) for campo in ("nombre", "apellido", "documento")):
            return datos

        nombre = datos.get("nombre", "").strip().casefold()
        apellido = datos.get("apellido", "").strip().casefold()
        documento = datos.get("documento", "").strip()

        coincide = (
            nombre == self.paciente.nombre.strip().casefold()
            and apellido == self.paciente.apellido.strip().casefold()
            and documento == self.paciente.documento
        )

        if not coincide:
            raise forms.ValidationError(
                "Los datos ingresados no coinciden con el paciente."
            )

        return datos
