from django import forms

from .models import Odontologo, Turno


class TurnoForm(forms.ModelForm):
    class Meta:
        model = Turno
        fields = (
            "paciente",
            "odontologo",
            "fecha",
            "hora_inicio",
            "duracion_minutos",
            "motivo",
            "estado",
            "notas",
        )
        labels = {
            "hora_inicio": "Hora de inicio",
            "duracion_minutos": "Duracion en minutos",
        }
        widgets = {
            "fecha": forms.DateInput(attrs={"type": "date"}),
            "hora_inicio": forms.TimeInput(attrs={"type": "time"}),
            "motivo": forms.TextInput(attrs={"placeholder": "Ej: control, limpieza, urgencia"}),
            "notas": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["paciente"].empty_label = "Seleccionar paciente"
        self.fields["odontologo"].queryset = Odontologo.objects.filter(activo=True)
        self.fields["odontologo"].empty_label = "Seleccionar odontologo"


class TurnoFiltroForm(forms.Form):
    fecha = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    estado = forms.ChoiceField(
        required=False,
        choices=[("", "Todos los estados"), *Turno.Estado.choices],
    )
    odontologo = forms.ModelChoiceField(
        required=False,
        queryset=Odontologo.objects.filter(activo=True),
        empty_label="Todos los odontologos",
    )
