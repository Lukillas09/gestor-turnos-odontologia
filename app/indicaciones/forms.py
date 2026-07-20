from django import forms

from historias.models import HistoriaClinica
from turnos.models import Turno

from .models import IndicacionPaciente, PlantillaIndicacion
from .selectors import plantillas_activas


class IndicacionBorradorForm(forms.ModelForm):
    plantilla = forms.ModelChoiceField(
        queryset=PlantillaIndicacion.objects.none(),
        required=False,
        empty_label="Sin plantilla",
        help_text="La plantilla copia un punto de partida que luego podés personalizar.",
    )

    class Meta:
        model = IndicacionPaciente
        fields = (
            "plantilla",
            "turno",
            "historia_clinica",
            "titulo",
            "procedimiento",
            "contenido",
            "pautas_alarma",
            "recomendaciones_control",
            "observaciones_personalizadas",
            "proximo_control_en",
        )
        labels = {
            "historia_clinica": "Historia clínica relacionada",
            "titulo": "Título del documento",
            "contenido": "Indicaciones",
            "pautas_alarma": "Pautas de alarma",
            "recomendaciones_control": "Recomendaciones de control",
            "observaciones_personalizadas": "Observaciones personalizadas",
            "proximo_control_en": "Próximo control",
        }
        widgets = {
            "contenido": forms.Textarea(attrs={"rows": 9}),
            "pautas_alarma": forms.Textarea(attrs={"rows": 5}),
            "recomendaciones_control": forms.Textarea(attrs={"rows": 5}),
            "observaciones_personalizadas": forms.Textarea(attrs={"rows": 5}),
            "proximo_control_en": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
        }

    def __init__(self, *args, paciente, odontologo, permitir_plantilla=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.paciente = paciente
        self.odontologo = odontologo
        if self.instance._state.adding:
            self.instance.paciente = paciente
            self.instance.odontologo = odontologo
        else:
            if self.instance.paciente_id != paciente.pk:
                raise ValueError("La instancia de la indicación no pertenece al paciente indicado.")
            if self.instance.odontologo_id != odontologo.pk:
                raise ValueError(
                    "La instancia de la indicación no pertenece al odontólogo indicado."
                )
        self.fields["turno"].queryset = Turno.objects.filter(
            paciente=paciente,
            odontologo=odontologo,
        ).order_by("-fecha", "-hora_inicio")
        self.fields["historia_clinica"].queryset = HistoriaClinica.objects.filter(
            paciente=paciente,
            odontologo=odontologo,
        ).order_by("-fecha_hora_atencion")
        self.fields["proximo_control_en"].input_formats = ["%Y-%m-%dT%H:%M"]
        if permitir_plantilla:
            self.fields["plantilla"].queryset = plantillas_activas()
        else:
            self.fields.pop("plantilla")

    def clean(self):
        datos = super().clean()
        turno = datos.get("turno")
        historia = datos.get("historia_clinica")
        if turno and (
            turno.paciente_id != self.paciente.pk or turno.odontologo_id != self.odontologo.pk
        ):
            self.add_error("turno", "El turno seleccionado no está disponible.")
        if historia and (
            historia.paciente_id != self.paciente.pk or historia.odontologo_id != self.odontologo.pk
        ):
            self.add_error("historia_clinica", "La historia seleccionada no está disponible.")
        return datos


class EmitirIndicacionForm(forms.Form):
    confirmar = forms.BooleanField(
        label="Confirmo que revisé el contenido y deseo emitir este documento.",
    )


class AnularIndicacionForm(forms.Form):
    motivo = forms.CharField(
        label="Motivo de anulación",
        min_length=10,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text="El motivo quedará registrado y el documento original se conservará.",
    )


class ReenviarIndicacionForm(forms.Form):
    usar_email_actual = forms.BooleanField(
        required=False,
        label="Usar el email actual verificado del paciente",
    )

    def __init__(self, *args, indicacion, **kwargs):
        super().__init__(*args, **kwargs)
        self.indicacion = indicacion
        paciente = indicacion.paciente
        email_actual = (
            paciente.email.strip()
            if paciente.activo and paciente.email and paciente.email_verificado_en
            else ""
        )
        self.email_actual_alternativo = bool(
            email_actual and email_actual.casefold() != indicacion.email_destino.casefold()
        )
        self.email_actual = email_actual
        if not self.email_actual_alternativo:
            self.fields.pop("usar_email_actual")

    def clean(self):
        datos = super().clean()
        if not self.indicacion.email_destino and not datos.get("usar_email_actual"):
            self.add_error(
                "usar_email_actual" if "usar_email_actual" in self.fields else None,
                "Confirmá que querés usar el email actual verificado del paciente.",
            )
        return datos


class ConfirmarReemplazoForm(forms.Form):
    confirmar = forms.BooleanField(
        label="Crear un nuevo borrador vinculado a este documento anulado.",
    )


class PlantillaIndicacionAdminForm(forms.ModelForm):
    motivo_modificacion = forms.CharField(
        required=False,
        min_length=10,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Obligatorio al modificar. Se guardará junto con la versión anterior.",
    )

    class Meta:
        model = PlantillaIndicacion
        fields = (
            "nombre",
            "procedimiento",
            "titulo_documento",
            "contenido",
            "pautas_alarma",
            "recomendaciones_control",
            "activa",
        )
        widgets = {
            "contenido": forms.Textarea(attrs={"rows": 10}),
            "pautas_alarma": forms.Textarea(attrs={"rows": 6}),
            "recomendaciones_control": forms.Textarea(attrs={"rows": 6}),
        }

    def clean_motivo_modificacion(self):
        motivo = (self.cleaned_data.get("motivo_modificacion") or "").strip()
        if self.instance.pk and not motivo:
            raise forms.ValidationError("Ingresá el motivo de la modificación.")
        return motivo
