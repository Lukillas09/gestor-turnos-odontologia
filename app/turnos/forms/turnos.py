from django import forms
from django.db.models import Q
from django.utils.dateparse import parse_date

from config.form_widgets import HtmlDateInput
from pacientes.models import Paciente
from usuarios.roles import obtener_odontologo_del_usuario, puede_gestionar_consultorio

from ..models import Odontologo, Turno
from ..selectors import obtener_horarios_disponibles
from .fields import HorarioDisponibleChoiceField, convertir_a_hora

DURACIONES_CONFIRMACION_TURNO = (
    (30, "30 minutos"),
    (45, "45 minutos"),
    (60, "60 minutos"),
    (90, "90 minutos"),
    (120, "120 minutos"),
)


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
            "duracion_minutos": "Duración en minutos",
        }
        widgets = {
            "fecha": HtmlDateInput(),
            "hora_inicio": forms.TimeInput(attrs={"type": "time", "inputmode": "numeric"}),
            "motivo": forms.TextInput(attrs={"placeholder": "Control, limpieza o urgencia"}),
            "notas": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["paciente"].empty_label = "Seleccionar paciente"
        pacientes = Paciente.objects.activos()

        if self.instance and self.instance.paciente_id:
            pacientes = Paciente.objects.filter(Q(activo=True) | Q(pk=self.instance.paciente_id))

        self.fields["paciente"].queryset = pacientes
        odontologos = Odontologo.objects.filter(activo=True)

        if self.instance and self.instance.odontologo_id:
            odontologos = Odontologo.objects.filter(
                Q(activo=True) | Q(pk=self.instance.odontologo_id)
            )

        self.fields["odontologo"].queryset = odontologos
        self.fields["odontologo"].empty_label = "Seleccionar odontólogo"


class HorariosDisponiblesFormMixin:
    def _configurar_horarios_disponibles(self):
        odontologo = self._obtener_odontologo_seleccionado()
        fecha = self._obtener_fecha_seleccionada()

        if not odontologo or not fecha:
            self.fields["hora_inicio"].choices = [("", "Elegir odontólogo y fecha")]
            self.fields["hora_inicio"].help_text = (
                "Elegí odontólogo y fecha para ver opciones disponibles."
            )
            return

        duracion = self._obtener_duracion_minutos(odontologo)
        horarios = self._obtener_horarios_disponibles(
            odontologo=odontologo,
            fecha=fecha,
            duracion_minutos=duracion,
            turno_excluido=self._obtener_turno_excluido(),
        )

        if not horarios:
            self.fields["hora_inicio"].choices = [("", "Sin horarios disponibles")]
            self.fields["hora_inicio"].help_text = (
                "No hay horarios libres para esa fecha con la duración indicada."
            )
            return

        self.fields["hora_inicio"].choices = [
            (self._formatear_horario(horario), self._formatear_horario(horario))
            for horario in horarios
        ]
        self.fields["hora_inicio"].help_text = "Solo se muestran horarios libres."

    def _obtener_odontologo_seleccionado(self):
        valor = self._obtener_valor("odontologo")

        if isinstance(valor, Odontologo):
            return valor if valor.activo else None

        if not valor:
            return None

        try:
            return Odontologo.objects.filter(pk=valor, activo=True).first()
        except (TypeError, ValueError):
            return None

    def _obtener_fecha_seleccionada(self):
        valor = self._obtener_valor("fecha")

        if hasattr(valor, "year"):
            return valor

        if not valor:
            return None

        return parse_date(str(valor))

    def _obtener_duracion_minutos(self, odontologo):
        valor = self._obtener_valor("duracion_minutos")

        if not valor:
            return odontologo.duracion_turno_minutos

        try:
            return int(valor)
        except (TypeError, ValueError):
            return odontologo.duracion_turno_minutos

    def _obtener_valor(self, nombre_campo):
        if self.is_bound:
            return self.data.get(nombre_campo)

        return self.initial.get(nombre_campo)

    def _obtener_turno_excluido(self):
        return None

    def _obtener_horarios_disponibles(self, **kwargs):
        return obtener_horarios_disponibles(**kwargs)

    @staticmethod
    def _formatear_horario(horario):
        return horario.strftime("%H:%M")


class TurnoCreateForm(HorariosDisponiblesFormMixin, TurnoForm):
    hora_inicio = HorarioDisponibleChoiceField(
        choices=(),
        coerce=convertir_a_hora,
        empty_value=None,
        label="Hora de inicio",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("estado", None)
        self._configurar_horarios_disponibles()


class TurnoReprogramacionForm(HorariosDisponiblesFormMixin, forms.ModelForm):
    hora_inicio = HorarioDisponibleChoiceField(
        choices=(),
        coerce=convertir_a_hora,
        empty_value=None,
        label="Hora de inicio",
    )

    class Meta:
        model = Turno
        fields = (
            "fecha",
            "hora_inicio",
            "duracion_minutos",
        )
        labels = {
            "hora_inicio": "Hora de inicio",
            "duracion_minutos": "Duración en minutos",
        }
        widgets = {
            "fecha": HtmlDateInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.setdefault("fecha", self.instance.fecha)
        self.initial.setdefault("hora_inicio", self._formatear_horario(self.instance.hora_inicio))
        self.initial.setdefault("duracion_minutos", self.instance.duracion_minutos)
        self._configurar_horarios_disponibles()

    def _obtener_odontologo_seleccionado(self):
        if self.instance and self.instance.odontologo_id and self.instance.odontologo.activo:
            return self.instance.odontologo

        return None

    def _obtener_turno_excluido(self):
        return self.instance


class TurnoHorarioBusquedaForm(forms.Form):
    odontologo = forms.ModelChoiceField(
        queryset=Odontologo.objects.filter(activo=True),
        empty_label="Seleccionar odontólogo",
    )
    fecha = forms.DateField(
        widget=HtmlDateInput(),
    )


class ConfirmacionTurnoForm(forms.Form):
    duracion_rapida = forms.TypedChoiceField(
        choices=DURACIONES_CONFIRMACION_TURNO,
        coerce=int,
        required=False,
        label="Duración real del turno",
        help_text="Usá una opción rápida o cargá una duración personalizada.",
    )
    duracion_personalizada = forms.IntegerField(
        required=False,
        min_value=5,
        max_value=360,
        label="Duración personalizada",
        widget=forms.NumberInput(
            attrs={
                "inputmode": "numeric",
                "max": 360,
                "min": 5,
                "placeholder": "75",
            }
        ),
        error_messages={
            "invalid": "Ingresá una duración válida en minutos.",
            "min_value": "La duración debe ser de al menos 5 minutos.",
            "max_value": "La duración no puede superar las 6 horas.",
        },
    )

    def clean(self):
        cleaned_data = super().clean() or {}

        if self.errors:
            return cleaned_data

        duracion_personalizada = cleaned_data.get("duracion_personalizada")
        duracion_rapida = cleaned_data.get("duracion_rapida")

        if duracion_personalizada is not None:
            cleaned_data["duracion_minutos"] = duracion_personalizada
            return cleaned_data

        if duracion_rapida:
            cleaned_data["duracion_minutos"] = duracion_rapida
            return cleaned_data

        raise forms.ValidationError(
            "Elegí una duración rápida o ingresá una duración personalizada."
        )


class TurnoFiltroForm(forms.Form):
    fecha = forms.DateField(
        required=False,
        widget=HtmlDateInput(),
    )
    estado = forms.ChoiceField(
        required=False,
        choices=[("", "Todos los estados"), *Turno.Estado.choices],
    )
    odontologo = forms.ModelChoiceField(
        required=False,
        queryset=Odontologo.objects.filter(activo=True),
        empty_label="Todos los odontólogos",
    )
    datos_por_revisar = forms.BooleanField(
        required=False,
        label="Datos por revisar",
    )

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        limitar_odontologos_por_usuario(self.fields["odontologo"], usuario)


def limitar_odontologos_por_usuario(campo_odontologo, usuario):
    if not usuario or puede_gestionar_consultorio(usuario):
        return

    odontologo = obtener_odontologo_del_usuario(usuario)

    if not odontologo:
        campo_odontologo.queryset = Odontologo.objects.none()
        return

    campo_odontologo.queryset = Odontologo.objects.filter(pk=odontologo.pk, activo=True)
    campo_odontologo.empty_label = None
    campo_odontologo.initial = odontologo
