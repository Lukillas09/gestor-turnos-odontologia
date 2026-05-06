from django import forms
from django.db.models import Q
from django.utils.dateparse import parse_date, parse_time

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


class HorariosDisponiblesFormMixin:
    def _configurar_horarios_disponibles(self):
        odontologo = self._obtener_odontologo_seleccionado()
        fecha = self._obtener_fecha_seleccionada()

        if not odontologo or not fecha:
            self.fields["hora_inicio"].choices = [("", "Elegir odontologo y fecha")]
            self.fields["hora_inicio"].help_text = (
                "Primero busca horarios por odontologo y fecha para ver opciones disponibles."
            )
            return

        duracion = self._obtener_duracion_minutos(odontologo)
        horarios = obtener_horarios_disponibles(
            odontologo=odontologo,
            fecha=fecha,
            duracion_minutos=duracion,
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


class TurnoHorarioBusquedaForm(forms.Form):
    odontologo = forms.ModelChoiceField(
        queryset=Odontologo.objects.filter(activo=True),
        empty_label="Seleccionar odontologo",
    )
    fecha = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
    )


class SolicitudTurnoPublicaForm(HorariosDisponiblesFormMixin, forms.Form):
    nombre = forms.CharField(max_length=100)
    apellido = forms.CharField(max_length=100)
    documento = forms.CharField(max_length=20, required=False, label="DNI")
    telefono = forms.CharField(max_length=30, required=False)
    email = forms.EmailField(required=False)
    odontologo = forms.ModelChoiceField(
        queryset=Odontologo.objects.filter(activo=True),
        empty_label="Seleccionar odontologo",
    )
    fecha = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    hora_inicio = forms.TypedChoiceField(
        choices=(),
        coerce=convertir_a_hora,
        empty_value=None,
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

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        limitar_odontologos_por_usuario(self.fields["odontologo"], usuario)


class AgendaFiltroForm(forms.Form):
    fecha = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
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
