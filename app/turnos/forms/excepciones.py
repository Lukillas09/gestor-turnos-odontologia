from django import forms

from config.form_widgets import HtmlDateInput
from usuarios.roles import obtener_odontologo_del_usuario

from ..excepcion_permissions import puede_gestionar_excepciones_globales
from ..models import ExcepcionAgenda, Odontologo


class ExcepcionAgendaForm(forms.ModelForm):
    confirmar_afectados = forms.BooleanField(required=False, widget=forms.HiddenInput())

    class Meta:
        model = ExcepcionAgenda
        fields = (
            "tipo",
            "odontologo",
            "fecha_desde",
            "fecha_hasta",
            "todo_el_dia",
            "hora_inicio",
            "hora_fin",
            "motivo",
            "mensaje_publico",
        )
        widgets = {
            "fecha_desde": HtmlDateInput(),
            "fecha_hasta": HtmlDateInput(),
            "hora_inicio": forms.TimeInput(attrs={"type": "time", "inputmode": "numeric"}),
            "hora_fin": forms.TimeInput(attrs={"type": "time", "inputmode": "numeric"}),
            "motivo": forms.TextInput(attrs={"placeholder": "Vacaciones, feriado, capacitación"}),
            "mensaje_publico": forms.TextInput(
                attrs={"placeholder": "Agenda no disponible para reservas públicas"}
            ),
        }
        help_texts = {
            "odontologo": "Dejalo vacío solo si querés bloquear todo el consultorio.",
            "todo_el_dia": "Si está activo, no hace falta cargar horario.",
            "mensaje_publico": "Opcional. Evitá datos sensibles o personales.",
        }

    def __init__(self, *args, usuario=None, **kwargs):
        self.usuario = usuario
        super().__init__(*args, **kwargs)
        self.fields["odontologo"].queryset = Odontologo.objects.filter(activo=True)
        self.fields["odontologo"].empty_label = "Todo el consultorio"

        if not puede_gestionar_excepciones_globales(usuario):
            odontologo = obtener_odontologo_del_usuario(usuario)
            self.fields["odontologo"].queryset = (
                Odontologo.objects.filter(pk=odontologo.pk, activo=True)
                if odontologo
                else Odontologo.objects.none()
            )
            self.fields["odontologo"].empty_label = None
            self.fields["odontologo"].initial = odontologo
            self.fields["odontologo"].disabled = True

        for campo in self.fields.values():
            if isinstance(campo.widget, forms.CheckboxInput) or isinstance(
                campo.widget, forms.HiddenInput
            ):
                continue
            campo.widget.attrs.setdefault("class", "form-control")

    def clean_odontologo(self):
        odontologo = self.cleaned_data.get("odontologo")

        if puede_gestionar_excepciones_globales(self.usuario):
            return odontologo

        odontologo_usuario = obtener_odontologo_del_usuario(self.usuario)

        if odontologo_usuario is None:
            raise forms.ValidationError("No tenés un odontólogo asociado para gestionar agenda.")

        return odontologo_usuario
