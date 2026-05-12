from django import forms

from config.form_widgets import HtmlDateInput
from turnos.models import Odontologo

from .models import FichaOdontologica, Paciente, PacienteOdontologo


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
            "genero": "Sexo / género",
            "numero_afiliado": "Número de afiliado",
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
            "medicacion_actual": "Medicación actual",
            "hipertension": "Hipertensión",
            "problemas_cardiacos": "Problemas cardíacos",
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
                label="Confirmación clínica",
                help_text=(
                    "Este paciente tiene datos clínicos cargados. "
                    "Para borrarlos, escribí CONFIRMAR en mayúsculas."
                ),
            )

    def clean(self):
        datos = super().clean()

        if not self.paciente.documento:
            raise forms.ValidationError(
                "El paciente no tiene DNI cargado. Agregá el DNI antes de borrar."
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
                    "Para borrar datos clínicos, escribí CONFIRMAR en mayúsculas.",
                )

        return datos


class PacienteDerivacionForm(forms.Form):
    odontologo = forms.ModelChoiceField(
        queryset=Odontologo.objects.filter(activo=True).select_related("usuario"),
        empty_label="Seleccionar odontólogo",
        label="Odontólogo destino",
    )
    motivo = forms.CharField(
        required=False,
        label="Motivo de derivación",
        widget=forms.Textarea(attrs={"rows": 4}),
    )

    def __init__(self, *args, paciente, **kwargs):
        super().__init__(*args, **kwargs)
        self.paciente = paciente

    def clean_odontologo(self):
        odontologo = self.cleaned_data["odontologo"]

        if PacienteOdontologo.objects.filter(
            paciente=self.paciente,
            odontologo=odontologo,
            activo=True,
        ).exists():
            raise forms.ValidationError(
                "El paciente ya está asociado a ese odontólogo."
            )

        return odontologo
