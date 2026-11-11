from django import forms
from django.db.models import Q

from ..models import ConfiguracionAgendaInteligente, TipoTurno, TipoTurnoOdontologo


class TipoTurnoForm(forms.ModelForm):
    class Meta:
        model = TipoTurno
        fields = (
            "nombre",
            "slug",
            "descripcion_publica",
            "icono",
            "orden_publico",
            "activo",
            "visible_publicamente",
        )
        widgets = {
            "descripcion_publica": forms.Textarea(attrs={"rows": 3}),
        }


class TipoTurnoOdontologoForm(forms.ModelForm):
    class Meta:
        model = TipoTurnoOdontologo
        fields: tuple[str, ...] = (
            "tipo_turno",
            "duracion_atencion_minutos",
            "margen_posterior_minutos",
            "activo",
            "reserva_publica",
        )
        labels = {
            "duracion_atencion_minutos": "Duración aproximada para el paciente",
            "margen_posterior_minutos": "Margen operativo posterior",
            "reserva_publica": "Disponible para reserva online",
        }
        widgets = {
            "duracion_atencion_minutos": forms.NumberInput(
                attrs={"min": 10, "max": 240, "step": 5}
            ),
            "margen_posterior_minutos": forms.NumberInput(attrs={"min": 0, "max": 60, "step": 5}),
        }

    def __init__(self, *args, odontologo, **kwargs):
        self.odontologo = odontologo
        super().__init__(*args, **kwargs)
        self.instance.odontologo = odontologo
        tipos = TipoTurno.objects.filter(activo=True)
        configurados = TipoTurnoOdontologo.objects.filter(odontologo=odontologo).values_list(
            "tipo_turno_id", flat=True
        )
        if self.instance.pk:
            tipos = TipoTurno.objects.filter(
                Q(pk=self.instance.tipo_turno_id)
                | Q(activo=True, configuraciones_odontologos__odontologo=odontologo)
                | Q(activo=True)
            )
            configurados = configurados.exclude(pk=self.instance.pk)
            self.fields["tipo_turno"].disabled = True
        self.fields["tipo_turno"].queryset = tipos.exclude(pk__in=configurados).distinct()

    def save(self, commit=True):
        self.instance.odontologo = self.odontologo
        return super().save(commit=commit)


class TipoTurnoOdontologoSimplificadoForm(TipoTurnoOdontologoForm):
    """Lo que ve el odontólogo: tipo (al crear), duración y si se reserva online.

    El margen operativo y el estado administrativo quedan solo para administración. Los campos
    no declarados acá se ignoran aunque lleguen en el POST.
    """

    class Meta(TipoTurnoOdontologoForm.Meta):
        fields = (
            "tipo_turno",
            "duracion_atencion_minutos",
            "reserva_publica",
        )


class ConfiguracionAgendaInteligenteForm(forms.ModelForm):
    """Configuración completa de la agenda inteligente (solo administración)."""

    class Meta:
        model = ConfiguracionAgendaInteligente
        fields: tuple[str, ...] = (
            "activa",
            "evitar_huecos",
            "intervalo_inicio_minutos",
            "hueco_minimo_util_minutos",
            "cantidad_horarios_recomendados",
            "preservar_bloques_largos",
            "bloque_largo_minutos",
            "modo_compactacion",
        )
        labels = {
            "activa": "Agenda inteligente habilitada",
            "evitar_huecos": "Organizar turnos para evitar huecos",
            "intervalo_inicio_minutos": "Intervalo de la grilla",
            "hueco_minimo_util_minutos": "Hueco mínimo utilizable",
            "cantidad_horarios_recomendados": "Horarios recomendados",
            "preservar_bloques_largos": "Preservar bloques largos",
            "bloque_largo_minutos": "Duración de un bloque largo",
            "modo_compactacion": "Modo de compactación",
        }
        help_texts = {
            "activa": (
                "Si se desactiva, este profesional no ofrece ningún horario en la reserva online."
            ),
            "evitar_huecos": (
                "Ordena los horarios para que queden menos huecos inutilizables entre turnos. "
                "Si se apaga, se ofrecen todos los horarios de la grilla."
            ),
        }


class ConfiguracionAgendaOdontologoForm(ConfiguracionAgendaInteligenteForm):
    """Lo que ve el odontólogo: solo el interruptor. Los campos técnicos son de administración."""

    class Meta(ConfiguracionAgendaInteligenteForm.Meta):
        fields = ("evitar_huecos",)
