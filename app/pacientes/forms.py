from django import forms

from config.form_widgets import HtmlDateInput

from .models import FichaOdontologica, Paciente


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
            "genero",
            "domicilio",
            "localidad",
            "obra_social",
            "numero_afiliado",
            "contacto_emergencia",
            "observaciones",
        )
        labels = {
            "documento": "DNI",
            "fecha_nacimiento": "Fecha de nacimiento",
            "genero": "Sexo / genero",
            "numero_afiliado": "Numero de afiliado",
            "contacto_emergencia": "Contacto de emergencia",
        }
        widgets = {
            "nombre": forms.TextInput(attrs={"autofocus": True}),
            "fecha_nacimiento": HtmlDateInput(),
            "observaciones": forms.Textarea(attrs={"rows": 4}),
        }


class FichaOdontologicaForm(forms.ModelForm):
    class Meta:
        model = FichaOdontologica
        fields = (
            "antecedentes_medicos",
            "alergias",
            "medicacion_actual",
            "enfermedades_relevantes",
            "embarazo",
            "hipertension",
            "diabetes",
            "problemas_cardiacos",
            "observaciones_generales",
        )
        labels = {
            "medicacion_actual": "Medicacion actual",
            "hipertension": "Hipertension",
            "problemas_cardiacos": "Problemas cardiacos",
        }
        widgets = {
            "antecedentes_medicos": forms.Textarea(attrs={"rows": 4}),
            "alergias": forms.Textarea(attrs={"rows": 3}),
            "medicacion_actual": forms.Textarea(attrs={"rows": 3}),
            "enfermedades_relevantes": forms.Textarea(attrs={"rows": 3}),
            "observaciones_generales": forms.Textarea(attrs={"rows": 4}),
        }


class PacienteDeleteConfirmationForm(forms.Form):
    nombre = forms.CharField(max_length=100)
    apellido = forms.CharField(max_length=100)
    documento = forms.CharField(max_length=20, label="DNI")

    def __init__(self, *args, paciente, requiere_confirmacion_clinica=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.paciente = paciente
        self.requiere_confirmacion_clinica = requiere_confirmacion_clinica

        if requiere_confirmacion_clinica:
            self.fields["confirmacion_clinica"] = forms.CharField(
                label="Confirmacion clinica",
                help_text=(
                    "Este paciente tiene datos clinicos cargados. "
                    "Para borrarlos, escribi CONFIRMAR en mayusculas."
                ),
            )

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

        if self.requiere_confirmacion_clinica:
            confirmacion = datos.get("confirmacion_clinica", "").strip()

            if confirmacion != "CONFIRMAR":
                self.add_error(
                    "confirmacion_clinica",
                    "Para borrar datos clinicos, escribi CONFIRMAR en mayusculas.",
                )

        return datos
