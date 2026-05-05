from datetime import timedelta

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView

from .forms import AgendaFiltroForm, TurnoFiltroForm, TurnoForm
from .models import Turno
from .selectors import obtener_inicio_semana, obtener_turnos_de_la_semana, obtener_turnos_del_dia
from .services import cancelar_turno


class TurnoListView(ListView):
    model = Turno
    template_name = "turnos/turno_list.html"
    context_object_name = "turnos"
    paginate_by = 20

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .select_related(
                "paciente",
                "odontologo",
                "odontologo__usuario",
            )
        )
        self.filtros_form = TurnoFiltroForm(self.request.GET)

        if self.filtros_form.is_valid():
            filtros = self.filtros_form.cleaned_data

            if filtros["fecha"]:
                queryset = queryset.filter(fecha=filtros["fecha"])

            if filtros["estado"]:
                queryset = queryset.filter(estado=filtros["estado"])

            if filtros["odontologo"]:
                queryset = queryset.filter(odontologo=filtros["odontologo"])

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query_params = self.request.GET.copy()
        query_params.pop("page", None)

        context["filtros_form"] = self.filtros_form
        context["filtros_querystring"] = query_params.urlencode()
        return context


class TurnoCreateView(CreateView):
    model = Turno
    form_class = TurnoForm
    template_name = "turnos/turno_form.html"
    success_url = reverse_lazy("turnos:lista")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["titulo"] = "Nuevo turno"
        context["subtitulo"] = "Carga de paciente, odontologo y horario de atencion."
        context["texto_boton"] = "Guardar turno"
        context["url_cancelar"] = reverse_lazy("turnos:lista")
        return context

    def form_valid(self, form):
        messages.success(self.request, "Turno creado correctamente.")
        return super().form_valid(form)


class TurnoDetailView(DetailView):
    model = Turno
    template_name = "turnos/turno_detail.html"
    context_object_name = "turno"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related(
                "paciente",
                "odontologo",
                "odontologo__usuario",
            )
        )


class TurnoUpdateView(UpdateView):
    model = Turno
    form_class = TurnoForm
    template_name = "turnos/turno_form.html"

    def get_success_url(self):
        return reverse("turnos:detalle", kwargs={"pk": self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["titulo"] = "Editar turno"
        context["subtitulo"] = "Actualizacion de paciente, odontologo, horario y estado."
        context["texto_boton"] = "Guardar cambios"
        context["url_cancelar"] = self.get_success_url()
        return context

    def form_valid(self, form):
        messages.success(self.request, "Turno actualizado correctamente.")
        return super().form_valid(form)


class TurnoCancelView(View):
    def post(self, request, pk):
        turno = get_object_or_404(Turno, pk=pk)
        cancelar_turno(turno)
        messages.success(request, "Turno cancelado correctamente.")
        return redirect("turnos:detalle", pk=turno.pk)


class AgendaDiaView(TemplateView):
    template_name = "turnos/agenda_dia.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filtros_form = AgendaFiltroForm(self.request.GET)
        fecha = timezone.localdate()
        odontologo = None

        if filtros_form.is_valid():
            fecha = filtros_form.cleaned_data["fecha"] or fecha
            odontologo = filtros_form.cleaned_data["odontologo"]

        context["filtros_form"] = filtros_form
        context["odontologo"] = odontologo
        context["fecha"] = fecha
        context["fecha_anterior"] = fecha - timedelta(days=1)
        context["fecha_siguiente"] = fecha + timedelta(days=1)
        context["turnos"] = obtener_turnos_del_dia(fecha, odontologo)
        return context


class AgendaSemanaView(TemplateView):
    template_name = "turnos/agenda_semana.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filtros_form = AgendaFiltroForm(self.request.GET)
        fecha_referencia = timezone.localdate()
        odontologo = None

        if filtros_form.is_valid():
            fecha_referencia = filtros_form.cleaned_data["fecha"] or fecha_referencia
            odontologo = filtros_form.cleaned_data["odontologo"]

        inicio_semana = obtener_inicio_semana(fecha_referencia)

        context["filtros_form"] = filtros_form
        context["odontologo"] = odontologo
        context["inicio_semana"] = inicio_semana
        context["fin_semana"] = inicio_semana + timedelta(days=6)
        context["semana_anterior"] = inicio_semana - timedelta(days=7)
        context["semana_siguiente"] = inicio_semana + timedelta(days=7)
        context["dias"] = obtener_turnos_de_la_semana(fecha_referencia, odontologo)
        return context
