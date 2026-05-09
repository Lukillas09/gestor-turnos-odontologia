from django import forms
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_time

from config.form_widgets import HtmlDateInput
from usuarios.roles import obtener_odontologo_del_usuario, puede_gestionar_consultorio

from .models import Odontologo, Turno
from .selectors import obtener_horarios_disponibles


def convertir_a_hora(valor):
    if hasattr(valor, "hour"):
        return valor

    hora = parse_time(str(valor))

    if hora is None:
        raise ValueError("Hora invalida")

    return hora


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
            "fecha": HtmlDateInput(),
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


class HorariosDisponiblesFormMixin:
    def _configurar_horarios_disponibles(self):
        odontologo = self._obtener_odontologo_seleccionado()
        fecha = self._obtener_fecha_seleccionada()

        if not odontologo or not fecha:
            self.fields["hora_inicio"].choices = [("", "Elegir odontologo y fecha")]
            self.fields["hora_inicio"].help_text = (
                "Elegi odontologo y fecha para ver opciones disponibles."
            )
            return

        duracion = self._obtener_duracion_minutos(odontologo)
        horarios = obtener_horarios_disponibles(
            odontologo=odontologo,
            fecha=fecha,
            duracion_minutos=duracion,
            turno_excluido=self._obtener_turno_excluido(),
        )

        if not horarios:
            self.fields["hora_inicio"].choices = [("", "Sin horarios disponibles")]
            self.fields["hora_inicio"].help_text = (
                "No hay horarios libres para esa fecha con la duracion indicada."
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

    @staticmethod
    def _formatear_horario(horario):
        return horario.strftime("%H:%M")


class TurnoCreateForm(HorariosDisponiblesFormMixin, TurnoForm):
    hora_inicio = forms.TypedChoiceField(
        choices=(),
        coerce=convertir_a_hora,
        empty_value=None,
        label="Hora de inicio",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._configurar_horarios_disponibles()


class TurnoReprogramacionForm(HorariosDisponiblesFormMixin, forms.ModelForm):
    hora_inicio = forms.TypedChoiceField(
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
            "duracion_minutos": "Duracion en minutos",
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
        empty_label="Seleccionar odontologo",
    )
    fecha = forms.DateField(
        widget=HtmlDateInput(),
    )


class SolicitudTurnoBusquedaPublicaForm(TurnoHorarioBusquedaForm):
    fecha = forms.DateField(
        error_messages={
            "required": "Elegi una fecha para ver horarios.",
            "invalid": "Ingresa una fecha valida.",
        },
        widget=HtmlDateInput(),
    )

    def clean_fecha(self):
        fecha = self.cleaned_data["fecha"]

        if fecha < timezone.localdate():
            raise forms.ValidationError("La fecha no puede ser anterior a hoy.")

        return fecha


class SolicitudTurnoPublicaForm(HorariosDisponiblesFormMixin, forms.Form):
    nombre = forms.CharField(
        max_length=100,
        error_messages={"required": "Ingresa tu nombre."},
    )
    apellido = forms.CharField(
        max_length=100,
        error_messages={"required": "Ingresa tu apellido."},
    )
    documento = forms.CharField(max_length=20, required=False, label="DNI")
    telefono = forms.CharField(max_length=30, required=False)
    email = forms.EmailField(
        required=False,
        error_messages={"invalid": "Ingresa un email valido o deja el campo vacio."},
    )
    odontologo = forms.ModelChoiceField(
        queryset=Odontologo.objects.filter(activo=True),
        empty_label="Seleccionar odontologo",
        error_messages={"required": "Elegi un odontologo."},
    )
    fecha = forms.DateField(
        error_messages={
            "required": "Elegi una fecha.",
            "invalid": "Ingresa una fecha valida.",
        },
        widget=HtmlDateInput(),
    )
    hora_inicio = forms.TypedChoiceField(
        choices=(),
        coerce=convertir_a_hora,
        empty_value=None,
        error_messages={
            "required": "Elegi un horario disponible.",
            "invalid_choice": "Ese horario ya no esta disponible. Volve a buscar horarios.",
        },
        label="Horario",
    )
    motivo = forms.CharField(
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Ej: control, limpieza, urgencia"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._configurar_horarios_disponibles()

    def clean_documento(self):
        documento = self.cleaned_data["documento"].strip()
        return documento or None

    def clean_fecha(self):
        fecha = self.cleaned_data["fecha"]

        if fecha < timezone.localdate():
            raise forms.ValidationError("La fecha no puede ser anterior a hoy.")

        return fecha


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
        empty_label="Todos los odontologos",
    )

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        limitar_odontologos_por_usuario(self.fields["odontologo"], usuario)


class AgendaFiltroForm(forms.Form):
    fecha = forms.DateField(
        required=False,
        widget=HtmlDateInput(),
    )
    odontologo = forms.ModelChoiceField(
        required=False,
        queryset=Odontologo.objects.filter(activo=True),
        empty_label="Todos los odontologos",
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
