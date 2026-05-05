from django import forms
from django.db.models import Q

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
        odontologos = Odontologo.objects.filter(activo=True)

        if self.instance and self.instance.odontologo_id:
            odontologos = Odontologo.objects.filter(
                Q(activo=True) | Q(pk=self.instance.odontologo_id)
            )

        self.fields["odontologo"].queryset = odontologos
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
