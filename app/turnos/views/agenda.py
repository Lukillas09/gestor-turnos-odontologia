from datetime import timedelta

from django.utils import timezone
from django.views.generic import TemplateView

from usuarios.mixins import VerTurnosRequeridoMixin
from usuarios.roles import obtener_odontologo_visible

from ..excepciones import obtener_excepciones_activas, obtener_turnos_afectados_por_excepcion
from ..forms import AgendaFiltroForm
from ..selectors import (
    obtener_agenda_diaria_por_odontologo,
    obtener_agenda_semanal_por_odontologo,
    obtener_bloques_agenda_del_dia,
    obtener_inicio_semana,
    obtener_resumen_estados,
    obtener_turnos_de_la_semana,
    obtener_turnos_del_dia,
)


def construir_excepciones_agenda_contexto(fecha_desde, fecha_hasta, odontologo, usuario):
    excepciones = list(obtener_excepciones_activas(odontologo, fecha_desde, fecha_hasta))
    items = []

    for excepcion in excepciones:
        turnos_afectados = obtener_turnos_afectados_por_excepcion(excepcion)

        if odontologo:
            turnos_afectados = [
                turno for turno in turnos_afectados if turno.odontologo_id == odontologo.pk
            ]

        items.append(
            {
                "excepcion": excepcion,
                "turnos_afectados": turnos_afectados,
            }
        )

    return items


class AgendaDiaView(VerTurnosRequeridoMixin, TemplateView):
    template_name = "turnos/agenda_dia.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filtros_form = AgendaFiltroForm(self.request.GET, usuario=self.request.user)
        fecha = timezone.localdate()
        odontologo_solicitado = None
        busqueda = ""

        if filtros_form.is_valid():
            fecha = filtros_form.cleaned_data["fecha"] or fecha
            odontologo_solicitado = filtros_form.cleaned_data["odontologo"]
            busqueda = filtros_form.cleaned_data["buscar"].strip()

        odontologo = obtener_odontologo_visible(self.request.user, odontologo_solicitado)

        context["filtros_form"] = filtros_form
        context["odontologo"] = odontologo
        context["busqueda"] = busqueda
        context["fecha"] = fecha
        context["fecha_anterior"] = fecha - timedelta(days=1)
        context["fecha_siguiente"] = fecha + timedelta(days=1)
        context["turnos"] = obtener_turnos_del_dia(fecha, odontologo, busqueda)
        context["bloques_agenda"] = obtener_bloques_agenda_del_dia(
            fecha,
            odontologo,
            busqueda=busqueda,
        )
        context["agenda_odontologos"] = obtener_agenda_diaria_por_odontologo(
            fecha,
            odontologo,
            busqueda,
        )
        context["excepciones_agenda"] = construir_excepciones_agenda_contexto(
            fecha,
            fecha,
            odontologo,
            self.request.user,
        )
        context["resumen_estados"] = obtener_resumen_estados(context["turnos"])
        return context


class AgendaSemanaView(VerTurnosRequeridoMixin, TemplateView):
    template_name = "turnos/agenda_semana.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filtros_form = AgendaFiltroForm(self.request.GET, usuario=self.request.user)
        fecha_referencia = timezone.localdate()
        odontologo_solicitado = None
        busqueda = ""

        if filtros_form.is_valid():
            fecha_referencia = filtros_form.cleaned_data["fecha"] or fecha_referencia
            odontologo_solicitado = filtros_form.cleaned_data["odontologo"]
            busqueda = filtros_form.cleaned_data["buscar"].strip()

        inicio_semana = obtener_inicio_semana(fecha_referencia)
        odontologo = obtener_odontologo_visible(self.request.user, odontologo_solicitado)

        context["filtros_form"] = filtros_form
        context["odontologo"] = odontologo
        context["busqueda"] = busqueda
        context["inicio_semana"] = inicio_semana
        context["fin_semana"] = inicio_semana + timedelta(days=6)
        context["hoy"] = timezone.localdate()
        context["semana_anterior"] = inicio_semana - timedelta(days=7)
        context["semana_siguiente"] = inicio_semana + timedelta(days=7)
        context["dias"] = obtener_turnos_de_la_semana(
            fecha_referencia,
            odontologo,
            busqueda,
        )
        turnos_semana = [turno for dia in context["dias"] for turno in dia["turnos"]]
        context["agenda_odontologos"] = obtener_agenda_semanal_por_odontologo(
            fecha_referencia,
            odontologo,
            busqueda,
        )
        context["excepciones_agenda"] = construir_excepciones_agenda_contexto(
            inicio_semana,
            context["fin_semana"],
            odontologo,
            self.request.user,
        )
        context["resumen_estados"] = obtener_resumen_estados(turnos_semana)
        return context
