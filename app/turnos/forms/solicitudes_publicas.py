from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError

from config.form_widgets import HtmlDateInput
from pacientes.models import Paciente
from pacientes.normalizacion import normalizar_documento

from ..excepciones import (
    obtener_horarios_publicos_disponibles,
    obtener_rango_reserva_publica,
    validar_fecha_reserva_publica,
    validar_intervalo_reserva_publica,
)
from ..models import Odontologo, SolicitudTurnoPublica, TipoTurno
from ..smart_scheduling import buscar_candidato, calcular_horarios_inteligentes
from ..tipos_turno import obtener_configuracion_tipo_publica
from .fields import HorarioDisponibleChoiceField, convertir_a_hora
from .turnos import ConfirmacionTurnoForm, HorariosDisponiblesFormMixin, TurnoHorarioBusquedaForm

DURACION_SOLICITUD_PUBLICA_MINUTOS = 30
MENSAJE_EMAIL_PUBLICO_REQUERIDO = "Ingresá un email para poder consultar y administrar tu turno."


class SolicitudTurnoBusquedaPublicaForm(TurnoHorarioBusquedaForm):
    tipo_turno = forms.ModelChoiceField(
        queryset=TipoTurno.objects.none(),
        empty_label="Seleccionar motivo",
        label="Motivo de la visita",
        error_messages={"required": "Elegí el motivo de la visita."},
    )
    fecha = forms.DateField(
        error_messages={
            "required": "Elegí una fecha para ver horarios.",
            "invalid": "Ingresá una fecha válida.",
        },
        widget=HtmlDateInput(),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not settings.TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED:
            self.fields.pop("tipo_turno", None)
        else:
            self._configurar_tipos_disponibles()
        rango = obtener_rango_reserva_publica()
        self.fields["fecha"].widget.attrs.update(
            {
                "min": rango.fecha_minima.isoformat(),
                "max": rango.fecha_maxima.isoformat(),
            }
        )

    def _configurar_tipos_disponibles(self):
        odontologo = self._obtener_odontologo_seleccionado()
        if not odontologo:
            return
        self.fields["tipo_turno"].queryset = TipoTurno.objects.filter(
            configuraciones_odontologos__odontologo=odontologo,
            configuraciones_odontologos__activo=True,
            configuraciones_odontologos__reserva_publica=True,
            activo=True,
            visible_publicamente=True,
        ).distinct()

    def _obtener_odontologo_seleccionado(self):
        valor = self.data.get("odontologo") if self.is_bound else self.initial.get("odontologo")
        if isinstance(valor, Odontologo):
            return valor if valor.activo else None
        try:
            return Odontologo.objects.filter(pk=valor, activo=True).first() if valor else None
        except (TypeError, ValueError):
            return None

    def clean_fecha(self):
        fecha = self.cleaned_data["fecha"]
        try:
            validar_fecha_reserva_publica(fecha)
        except ValidationError as error:
            raise forms.ValidationError(error.messages) from error

        return fecha


class SolicitudTurnoPublicaForm(HorariosDisponiblesFormMixin, forms.Form):
    nombre = forms.CharField(
        max_length=100,
        label="Nombre",
        error_messages={"required": "Ingresá tu nombre."},
        widget=forms.TextInput(
            attrs={
                "autocomplete": "given-name",
                "placeholder": "Nombre del paciente",
            }
        ),
    )
    apellido = forms.CharField(
        max_length=100,
        label="Apellido",
        error_messages={"required": "Ingresá tu apellido."},
        widget=forms.TextInput(
            attrs={
                "autocomplete": "family-name",
                "placeholder": "Apellido del paciente",
            }
        ),
    )
    telefono = forms.CharField(
        max_length=30,
        label="Teléfono",
        error_messages={"required": "Ingresá tu teléfono."},
        widget=forms.TextInput(
            attrs={
                "autocomplete": "tel",
                "inputmode": "tel",
                "placeholder": "Teléfono de contacto",
            }
        ),
    )
    documento = forms.CharField(
        max_length=20,
        label="DNI",
        error_messages={"required": "Ingresá tu DNI."},
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "inputmode": "numeric",
                "placeholder": "DNI del paciente",
            }
        ),
    )
    email = forms.EmailField(
        required=False,
        label="Email",
        help_text=(
            "Lo usamos para enviarte códigos de acceso con los que podés consultar, "
            "cancelar o reprogramar tus turnos. Si ya sos paciente, tus datos "
            "registrados no se modifican automáticamente."
        ),
        widget=forms.EmailInput(
            attrs={
                "autocomplete": "email",
                "inputmode": "email",
                "placeholder": "tuemail@ejemplo.com",
            }
        ),
        error_messages={"invalid": "Ingresá un email válido."},
    )
    odontologo = forms.ModelChoiceField(
        queryset=Odontologo.objects.filter(activo=True),
        empty_label="Seleccionar odontólogo",
        error_messages={"required": "Elegí un odontólogo."},
    )
    tipo_turno = forms.ModelChoiceField(
        queryset=TipoTurno.objects.none(),
        label="Motivo de la visita",
        error_messages={
            "required": "Elegí el motivo de la visita.",
            "invalid_choice": "El motivo elegido ya no está disponible para este profesional.",
        },
    )
    fecha = forms.DateField(
        error_messages={
            "required": "Elegí una fecha.",
            "invalid": "Ingresá una fecha válida.",
        },
        widget=HtmlDateInput(),
    )
    hora_inicio = HorarioDisponibleChoiceField(
        choices=(),
        coerce=convertir_a_hora,
        empty_value=None,
        error_messages={
            "required": "Elegí un horario disponible.",
            "invalid_choice": "Ese horario ya no está disponible. Volvé a buscar horarios.",
        },
        label="Horario",
    )
    motivo = forms.CharField(
        max_length=200,
        required=False,
        label="Motivo breve",
        widget=forms.TextInput(attrs={"placeholder": "Control, limpieza o urgencia"}),
    )
    idempotency_token = forms.CharField(required=False, widget=forms.HiddenInput())
    turnstile_token = forms.CharField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._paciente_por_documento = None
        self._paciente_por_documento_consultado = False
        rango = obtener_rango_reserva_publica()
        self.fields["fecha"].widget.attrs.update(
            {
                "min": rango.fecha_minima.isoformat(),
                "max": rango.fecha_maxima.isoformat(),
            }
        )
        if settings.TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED:
            self._configurar_tipos_disponibles()
            self.fields["motivo"].label = "Comentario adicional"
            self.fields["motivo"].widget.attrs[
                "placeholder"
            ] = "Información breve que quieras agregar"
        else:
            self.fields.pop("tipo_turno", None)
        self._configurar_horarios_disponibles()

    def _obtener_duracion_minutos(self, odontologo):
        if settings.TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED:
            configuracion = self._obtener_configuracion_tipo(odontologo)
            return configuracion.duracion_bloqueada_minutos if configuracion else 0
        return DURACION_SOLICITUD_PUBLICA_MINUTOS

    def _obtener_horarios_disponibles(self, **kwargs):
        if settings.TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED:
            configuracion = self._obtener_configuracion_tipo(kwargs["odontologo"])
            if not configuracion:
                return []
            resultado = calcular_horarios_inteligentes(
                odontologo=kwargs["odontologo"],
                fecha=kwargs["fecha"],
                duracion_atencion_minutos=configuracion.duracion_atencion_minutos,
                margen_posterior_minutos=configuracion.margen_posterior_minutos,
            )
            return [candidato.hora_inicio for candidato in resultado.todos]
        return obtener_horarios_publicos_disponibles(**kwargs)

    def _configurar_tipos_disponibles(self):
        odontologo = self._obtener_odontologo_seleccionado()
        if not odontologo:
            return
        self.fields["tipo_turno"].queryset = TipoTurno.objects.filter(
            configuraciones_odontologos__odontologo=odontologo,
            configuraciones_odontologos__activo=True,
            configuraciones_odontologos__reserva_publica=True,
            activo=True,
            visible_publicamente=True,
        ).distinct()

    def _obtener_configuracion_tipo(self, odontologo):
        if not odontologo:
            return None
        tipo = None
        if hasattr(self, "cleaned_data"):
            tipo = self.cleaned_data.get("tipo_turno")
        if not tipo:
            tipo = self._obtener_valor("tipo_turno")
        return obtener_configuracion_tipo_publica(odontologo, tipo)

    def clean_documento(self):
        documento = normalizar_documento(self.cleaned_data["documento"])

        if not documento:
            raise forms.ValidationError("Ingresá tu DNI.")

        return documento

    def _obtener_paciente_por_documento(self, documento):
        if not self._paciente_por_documento_consultado:
            self._paciente_por_documento = (
                Paciente.objects.filter(documento=documento).first() if documento else None
            )
            self._paciente_por_documento_consultado = True

        return self._paciente_por_documento

    def clean_fecha(self):
        fecha = self.cleaned_data["fecha"]
        try:
            validar_fecha_reserva_publica(fecha)
        except ValidationError as error:
            raise forms.ValidationError(error.messages) from error

        return fecha

    def clean(self):
        cleaned_data = super().clean() or {}
        documento = cleaned_data.get("documento")
        email = (cleaned_data.get("email") or "").strip()
        odontologo = cleaned_data.get("odontologo")
        fecha = cleaned_data.get("fecha")
        hora_inicio = cleaned_data.get("hora_inicio")
        tipo_turno = cleaned_data.get("tipo_turno")

        if "email" not in self.errors and documento:
            paciente = self._obtener_paciente_por_documento(documento)
            paciente_activo_sin_email = (
                paciente is not None and paciente.activo and not paciente.email
            )

            if (paciente is None or paciente_activo_sin_email) and not email:
                self.add_error("email", MENSAJE_EMAIL_PUBLICO_REQUERIDO)

        if (
            settings.TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED
            and odontologo
            and tipo_turno
            and fecha
            and hora_inicio
        ):
            configuracion = obtener_configuracion_tipo_publica(odontologo, tipo_turno)
            if not configuracion:
                self.add_error(
                    "tipo_turno",
                    "El motivo elegido ya no está disponible para este profesional.",
                )
                return cleaned_data
            resultado = calcular_horarios_inteligentes(
                odontologo=odontologo,
                fecha=fecha,
                duracion_atencion_minutos=configuracion.duracion_atencion_minutos,
                margen_posterior_minutos=configuracion.margen_posterior_minutos,
            )
            candidato = buscar_candidato(resultado, hora_inicio)
            if not candidato:
                self.add_error(
                    "hora_inicio",
                    "Ese horario ya no está disponible. Volvé a buscar horarios.",
                )
                return cleaned_data
            cleaned_data["configuracion_tipo_turno"] = configuracion
            cleaned_data["candidato_horario"] = candidato
        elif (
            not settings.TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED
            and odontologo
            and fecha
            and hora_inicio
        ):
            try:
                validar_intervalo_reserva_publica(
                    fecha,
                    hora_inicio,
                    DURACION_SOLICITUD_PUBLICA_MINUTOS,
                )
            except ValidationError as error:
                raise forms.ValidationError(error.messages) from error

        return cleaned_data


class RevisionSolicitudTurnoPublicaForm(forms.Form):
    ACCIONES = (
        ("conservar", "Conservar datos actuales"),
        ("aplicar_campos", "Actualizar campos seleccionados"),
        ("mantener_pendiente", "Revisar más tarde"),
        ("rechazar", "Marcar solicitud como no válida"),
    )
    CAMPOS_ACTUALIZABLES = (
        ("nombre", "Actualizar nombre"),
        ("apellido", "Actualizar apellido"),
        ("telefono", "Actualizar teléfono"),
        ("email", "Actualizar email"),
    )

    accion = forms.ChoiceField(choices=ACCIONES, widget=forms.RadioSelect)
    campos = forms.MultipleChoiceField(
        choices=CAMPOS_ACTUALIZABLES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    observaciones = forms.CharField(
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Observaciones internas"}),
    )

    def __init__(self, *args, **kwargs):
        self.solicitud = kwargs.pop("solicitud", None)
        super().__init__(*args, **kwargs)
        self.fields["accion"].initial = "conservar"

        if self.solicitud and not self.solicitud.paciente_existente:
            self.fields["accion"].choices = (
                ("validar_paciente", "Validar paciente"),
                ("mantener_pendiente", "Revisar más tarde"),
                ("rechazar", "Marcar solicitud como no válida"),
            )
            self.fields["accion"].initial = "validar_paciente"
            self.fields.pop("campos", None)
        elif self.solicitud and not self.solicitud.paciente.activo:
            self.fields["accion"].choices = (
                ("mantener_pendiente", "Mantener pendiente"),
                ("rechazar", "Marcar solicitud como no valida"),
            )
            self.fields["accion"].initial = "mantener_pendiente"
            self.fields.pop("campos", None)

    def clean(self):
        cleaned_data = super().clean()
        accion = cleaned_data.get("accion")
        campos = cleaned_data.get("campos") or []

        if accion == "aplicar_campos" and not campos:
            raise forms.ValidationError("Seleccioná al menos un campo para actualizar.")

        if (
            self.solicitud
            and self.solicitud.estado_revision != SolicitudTurnoPublica.EstadoRevision.PENDIENTE
        ):
            raise forms.ValidationError("Esta solicitud ya fue revisada.")

        return cleaned_data


class RevisionYConfirmacionTurnoPublicoForm(ConfirmacionTurnoForm):
    ACCIONES_EXISTENTE = (
        ("conservar", "Conservar datos actuales"),
        ("aplicar_campos", "Actualizar campos seleccionados"),
    )
    ACCIONES_NUEVO = (("validar_paciente", "Validar paciente y confirmar turno"),)
    CAMPOS_ACTUALIZABLES = RevisionSolicitudTurnoPublicaForm.CAMPOS_ACTUALIZABLES

    accion_revision = forms.ChoiceField(
        choices=ACCIONES_EXISTENTE,
        widget=forms.RadioSelect,
        label="Acción de revisión",
    )
    campos = forms.MultipleChoiceField(
        choices=CAMPOS_ACTUALIZABLES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Campos a actualizar",
    )
    observaciones = forms.CharField(
        required=False,
        max_length=1000,
        label="Observaciones",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Observaciones internas"}),
    )

    def __init__(self, *args, solicitud=None, usuario=None, **kwargs):
        self.solicitud = solicitud
        self.usuario = usuario
        super().__init__(*args, **kwargs)
        self.fields["accion_revision"].widget.attrs.setdefault(
            "class",
            "sr-only review-action-input",
        )

        if solicitud and not solicitud.paciente_existente:
            self.fields["accion_revision"].choices = self.ACCIONES_NUEVO
            self.fields["accion_revision"].initial = "validar_paciente"
            self.fields.pop("campos", None)
        else:
            self.fields["accion_revision"].initial = "conservar"
            diferencias = set((getattr(solicitud, "diferencias_detectadas", None) or {}).keys())
            opciones = [opcion for opcion in self.CAMPOS_ACTUALIZABLES if opcion[0] in diferencias]

            if opciones:
                self.fields["campos"].choices = opciones
            else:
                self.fields["accion_revision"].choices = (
                    ("conservar", "Conservar datos actuales"),
                )
                self.fields.pop("campos", None)

        for campo in self.fields.values():
            if isinstance(campo.widget, forms.RadioSelect) or isinstance(
                campo.widget, forms.CheckboxSelectMultiple
            ):
                continue
            campo.widget.attrs.setdefault("class", "form-control")

    def clean(self):
        cleaned_data = super().clean()

        if self.errors:
            return cleaned_data

        if not self.solicitud:
            raise forms.ValidationError("No se encontró la solicitud pública asociada.")

        if self.solicitud.estado_revision != SolicitudTurnoPublica.EstadoRevision.PENDIENTE:
            raise forms.ValidationError("Esta solicitud ya fue revisada.")

        accion = cleaned_data.get("accion_revision")
        campos = set(cleaned_data.get("campos") or [])
        diferencias = set((self.solicitud.diferencias_detectadas or {}).keys())

        if accion == "aplicar_campos":
            if not campos:
                raise forms.ValidationError("Seleccioná al menos un campo para actualizar.")

            if campos - diferencias:
                raise forms.ValidationError(
                    "Solo se pueden aplicar campos con diferencias detectadas."
                )

        if not self.solicitud.paciente_existente and accion != "validar_paciente":
            raise forms.ValidationError(
                "Para confirmar este turno primero hay que validar el paciente."
            )

        return cleaned_data


class RechazoSolicitudTurnoPublicaForm(forms.Form):
    motivo = forms.CharField(
        max_length=1000,
        label="Motivo",
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "placeholder": "Motivo administrativo del rechazo",
            }
        ),
        error_messages={"required": "Indicá un motivo para rechazar la solicitud."},
    )
