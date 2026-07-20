from django import forms
from django.db.models import Q
from django.utils.dateparse import parse_date

from config.form_widgets import HtmlDateInput
from pacientes.models import Paciente
from usuarios.roles import obtener_odontologo_del_usuario, puede_gestionar_consultorio

from ..models import Odontologo, TipoTurno, TipoTurnoOdontologo, Turno
from ..selectors import obtener_horarios_disponibles
from ..tipos_turno import aplicar_snapshot_interno
from .fields import HorarioDisponibleChoiceField, convertir_a_hora

DURACIONES_CONFIRMACION_TURNO = (
    (30, "30 minutos"),
    (45, "45 minutos"),
    (60, "60 minutos"),
    (90, "90 minutos"),
    (120, "120 minutos"),
)


class TurnoForm(forms.ModelForm):
    tipo_turno = forms.ModelChoiceField(
        queryset=TipoTurno.objects.none(),
        required=False,
        empty_label="Sin tipo preconfigurado",
        label="Tipo de turno (opcional)",
        help_text=(
            "Al elegir un tipo se aplica la duración configurada para el profesional. "
            "Dejalo vacío para cargar una duración manual."
        ),
    )

    class Meta:
        model = Turno
        fields = (
            "paciente",
            "odontologo",
            "tipo_turno",
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
        self._tipo_original_id = self.instance.tipo_turno_id if self.instance.pk else None
        self._odontologo_original_id = self.instance.odontologo_id if self.instance.pk else None
        self._duracion_original = self.instance.duracion_minutos if self.instance.pk else None
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
        self._configurar_tipos_turno()

    def _configurar_tipos_turno(self):
        odontologo = self._obtener_odontologo_para_tipo()
        if not odontologo:
            return
        tipos = TipoTurno.objects.filter(
            configuraciones_odontologos__odontologo=odontologo,
            configuraciones_odontologos__activo=True,
            activo=True,
        )
        if self.instance.pk and self.instance.tipo_turno_id:
            tipos = TipoTurno.objects.filter(
                Q(pk=self.instance.tipo_turno_id)
                | Q(
                    configuraciones_odontologos__odontologo=odontologo,
                    configuraciones_odontologos__activo=True,
                    activo=True,
                )
            )
        self.fields["tipo_turno"].queryset = tipos.distinct().order_by(
            "orden_publico", "nombre", "pk"
        )

    def _obtener_odontologo_para_tipo(self):
        valor = self.data.get("odontologo") if self.is_bound else self.initial.get("odontologo")
        if not valor and self.instance.odontologo_id:
            return self.instance.odontologo
        if isinstance(valor, Odontologo):
            return valor
        try:
            return Odontologo.objects.filter(pk=valor).first() if valor else None
        except (TypeError, ValueError):
            return None

    def clean(self):
        cleaned_data = super().clean() or {}
        odontologo = cleaned_data.get("odontologo")
        tipo_turno = cleaned_data.get("tipo_turno")
        if tipo_turno and odontologo:
            configuracion = TipoTurnoOdontologo.objects.filter(
                odontologo=odontologo,
                tipo_turno=tipo_turno,
                activo=True,
                tipo_turno__activo=True,
            ).first()
            conserva_snapshot = (
                self.instance.pk
                and self._tipo_original_id == tipo_turno.pk
                and self._odontologo_original_id == odontologo.pk
            )
            if not conserva_snapshot:
                if not configuracion:
                    self.add_error(
                        "tipo_turno",
                        "Este tipo no está configurado para el profesional seleccionado.",
                    )
                    return cleaned_data
                cleaned_data["configuracion_tipo_turno"] = configuracion
                cleaned_data["duracion_minutos"] = configuracion.duracion_bloqueada_minutos
        return cleaned_data

    def save(self, commit=True):
        turno = super().save(commit=False)
        configuracion = self.cleaned_data.get("configuracion_tipo_turno")
        tipo_cambio = self._tipo_original_id != turno.tipo_turno_id

        if not turno.pk or tipo_cambio:
            aplicar_snapshot_interno(turno, configuracion)
        elif self._duracion_original != turno.duracion_minutos:
            margen = turno.margen_posterior_minutos_snapshot
            if margen >= turno.duracion_minutos:
                margen = 0
            turno.margen_posterior_minutos_snapshot = margen
            turno.duracion_atencion_minutos = turno.duracion_minutos - margen
            turno.algoritmo_horario_version = ""
            turno.clasificacion_horario = Turno.ClasificacionHorario.INTERNO
            turno.puntaje_horario = None

        if commit:
            turno.save()
            self.save_m2m()
        return turno


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

    def _obtener_duracion_minutos(self, odontologo):
        tipo_turno = self._obtener_valor("tipo_turno")
        if tipo_turno:
            configuracion = TipoTurnoOdontologo.objects.filter(
                odontologo=odontologo,
                tipo_turno_id=getattr(tipo_turno, "pk", tipo_turno),
                activo=True,
                tipo_turno__activo=True,
            ).first()
            if configuracion:
                return configuracion.duracion_bloqueada_minutos
        return super()._obtener_duracion_minutos(odontologo)


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

    def __init__(
        self,
        *args,
        duracion_original=None,
        requiere_confirmacion_cambio=False,
        **kwargs,
    ):
        self.duracion_original = duracion_original
        self.requiere_confirmacion_cambio = requiere_confirmacion_cambio
        super().__init__(*args, **kwargs)
        if requiere_confirmacion_cambio:
            self.fields["confirmar_cambio_duracion"] = forms.BooleanField(
                required=False,
                label=(
                    "Confirmo que quiero cambiar la duración bloqueada y que se volverá "
                    "a comprobar la agenda."
                ),
            )

    def clean(self):
        cleaned_data = super().clean() or {}

        if self.errors:
            return cleaned_data

        duracion_personalizada = cleaned_data.get("duracion_personalizada")
        duracion_rapida = cleaned_data.get("duracion_rapida")

        if duracion_personalizada is not None:
            cleaned_data["duracion_minutos"] = duracion_personalizada
        elif duracion_rapida:
            cleaned_data["duracion_minutos"] = duracion_rapida
        else:
            raise forms.ValidationError(
                "Elegí una duración rápida o ingresá una duración personalizada."
            )

        if (
            self.requiere_confirmacion_cambio
            and self.duracion_original is not None
            and cleaned_data["duracion_minutos"] != self.duracion_original
            and not cleaned_data.get("confirmar_cambio_duracion")
        ):
            self.add_error(
                "confirmar_cambio_duracion",
                "Confirmá explícitamente el cambio de duración.",
            )
        return cleaned_data


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
