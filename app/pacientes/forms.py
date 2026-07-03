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
            "genero": "Sexo / genero",
            "numero_afiliado": "Numero de afiliado",
            "contacto_emergencia": "Contacto de emergencia",
        }
        widgets = {
            "nombre": forms.TextInput(
                attrs={
                    "autocomplete": "given-name",
                    "autofocus": True,
                }
            ),
            "apellido": forms.TextInput(attrs={"autocomplete": "family-name"}),
            "documento": forms.TextInput(attrs={"autocomplete": "off", "inputmode": "numeric"}),
            "telefono": forms.TextInput(attrs={"autocomplete": "tel", "inputmode": "tel"}),
            "email": forms.EmailInput(attrs={"autocomplete": "email", "inputmode": "email"}),
            "fecha_nacimiento": HtmlDateInput(),
            "domicilio": forms.TextInput(attrs={"autocomplete": "street-address"}),
            "localidad": forms.TextInput(attrs={"autocomplete": "address-level2"}),
            "numero_afiliado": forms.TextInput(attrs={"autocomplete": "off"}),
            "contacto_emergencia": forms.TextInput(
                attrs={"autocomplete": "tel", "inputmode": "tel"}
            ),
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


class PacienteArchiveForm(forms.Form):
    motivo = forms.CharField(
        min_length=10,
        max_length=1000,
        label="Motivo de archivo",
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "placeholder": "Motivo administrativo para archivar el paciente",
            }
        ),
    )
    confirmacion = forms.CharField(
        label="Confirmacion",
        help_text="Para confirmar, escribi ARCHIVAR en mayusculas.",
    )
    documento = forms.CharField(
        max_length=20,
        label="DNI",
        widget=forms.TextInput(attrs={"autocomplete": "off", "inputmode": "numeric"}),
    )

    def __init__(self, *args, paciente, **kwargs):
        super().__init__(*args, **kwargs)
        self.paciente = paciente

    def clean(self):
        datos = super().clean()

        if not self.paciente.documento:
            raise forms.ValidationError(
                "El paciente no tiene DNI cargado. Agregalo antes de archivarlo."
            )

        if not all(datos.get(campo) for campo in ("motivo", "confirmacion", "documento")):
            return datos

        if datos["documento"].strip() != self.paciente.documento:
            raise forms.ValidationError("El DNI ingresado no coincide con el paciente.")

        if datos["confirmacion"].strip() != "ARCHIVAR":
            self.add_error("confirmacion", "Para archivar, escribi ARCHIVAR en mayusculas.")

        return datos


class PacienteReactivateForm(forms.Form):
    motivo = forms.CharField(
        min_length=10,
        max_length=1000,
        label="Motivo de reactivacion",
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "placeholder": "Motivo administrativo para reactivar el paciente",
            }
        ),
    )
    confirmacion = forms.CharField(
        label="Confirmacion",
        help_text="Para confirmar, escribi REACTIVAR en mayusculas.",
    )

    def clean_confirmacion(self):
        confirmacion = self.cleaned_data["confirmacion"].strip()

        if confirmacion != "REACTIVAR":
            raise forms.ValidationError("Para reactivar, escribi REACTIVAR en mayusculas.")

        return confirmacion


class AccesoClinicoEmergenciaForm(forms.Form):
    motivo = forms.CharField(
        min_length=20,
        max_length=1000,
        label="Motivo del acceso de emergencia",
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "placeholder": "Motivo clinico/operativo que justifica el acceso excepcional",
            }
        ),
    )
    confirmacion = forms.BooleanField(
        label="Confirmo que este acceso es excepcional, paciente-especifico y auditado.",
    )


class PacienteDerivacionForm(forms.Form):
    odontologo = forms.ModelChoiceField(
        queryset=Odontologo.objects.filter(activo=True).select_related("usuario"),
        empty_label="Seleccionar odontologo",
        label="Odontologo destino",
    )
    motivo = forms.CharField(
        required=False,
        label="Motivo de derivacion",
        widget=forms.Textarea(attrs={"rows": 4}),
    )

    def __init__(self, *args, paciente, **kwargs):
        super().__init__(*args, **kwargs)
        self.paciente = paciente

    def clean_odontologo(self):
        if not self.paciente.activo:
            raise forms.ValidationError("No se puede derivar un paciente archivado.")

        odontologo = self.cleaned_data["odontologo"]

        if PacienteOdontologo.objects.filter(
            paciente=self.paciente,
            odontologo=odontologo,
            activo=True,
        ).exists():
            raise forms.ValidationError(
                "El paciente ya esta asociado a ese odontologo."
            )

        return odontologo
