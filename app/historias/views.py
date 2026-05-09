from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from pacientes.models import Paciente
from usuarios.mixins import HistoriaClinicaOdontologoRequeridoMixin
from usuarios.roles import (
    obtener_odontologo_del_usuario,
    puede_editar_historia_clinica,
)

from .forms import HistoriaClinicaForm
from .models import HistoriaClinica


class PacienteHistoriaClinicaMixin(HistoriaClinicaOdontologoRequeridoMixin):
    paciente = None

    def dispatch(self, request, *args, **kwargs):
        self.paciente = get_object_or_404(Paciente, pk=kwargs["paciente_pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["paciente"] = self.paciente
        return context


class HistoriaClinicaListView(PacienteHistoriaClinicaMixin, ListView):
    model = HistoriaClinica
    template_name = "historias/historia_clinica_list.html"
    context_object_name = "historias"
    paginate_by = 10

    def get_queryset(self):
        return (
            HistoriaClinica.objects.filter(paciente=self.paciente)
            .select_related("paciente", "odontologo", "odontologo__usuario")
            .order_by("-fecha", "-creado_en")
        )


class HistoriaClinicaCreateView(PacienteHistoriaClinicaMixin, CreateView):
    model = HistoriaClinica
    form_class = HistoriaClinicaForm
    template_name = "historias/historia_clinica_form.html"

    def get_success_url(self):
        return reverse(
            "historias:detalle",
            kwargs={"pk": self.object.pk},
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["titulo"] = "Nueva entrada de historia clinica"
        context["subtitulo"] = f"Registro clinico de {self.paciente}."
        context["texto_boton"] = "Guardar entrada"
        context["url_cancelar"] = reverse(
            "historias:lista_paciente",
            kwargs={"paciente_pk": self.paciente.pk},
        )
        return context

    def form_valid(self, form):
        odontologo = obtener_odontologo_del_usuario(self.request.user)

        if odontologo is None:
            raise PermissionDenied("Solo un odontologo puede cargar historia clinica.")

        form.instance.paciente = self.paciente
        form.instance.odontologo = odontologo
        messages.success(self.request, "Entrada de historia clinica creada correctamente.")
        return super().form_valid(form)


class HistoriaClinicaDetailView(HistoriaClinicaOdontologoRequeridoMixin, DetailView):
    model = HistoriaClinica
    template_name = "historias/historia_clinica_detail.html"
    context_object_name = "historia"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related("paciente", "odontologo", "odontologo__usuario")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["puede_editar_historia"] = puede_editar_historia_clinica(
            self.request.user,
            self.object,
        )
        return context


class HistoriaClinicaUpdateView(HistoriaClinicaOdontologoRequeridoMixin, UpdateView):
    model = HistoriaClinica
    form_class = HistoriaClinicaForm
    template_name = "historias/historia_clinica_form.html"
    context_object_name = "historia"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related("paciente", "odontologo", "odontologo__usuario")
        )

    def get_object(self, queryset=None):
        historia = super().get_object(queryset)

        if not puede_editar_historia_clinica(self.request.user, historia):
            raise PermissionDenied("Solo el odontologo responsable puede editar esta entrada.")

        return historia

    def get_success_url(self):
        return reverse("historias:detalle", kwargs={"pk": self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["paciente"] = self.object.paciente
        context["titulo"] = "Editar entrada de historia clinica"
        context["subtitulo"] = f"Registro clinico de {self.object.paciente}."
        context["texto_boton"] = "Guardar cambios"
        context["url_cancelar"] = self.get_success_url()
        return context

    def form_valid(self, form):
        messages.success(self.request, "Entrada de historia clinica actualizada correctamente.")
        return super().form_valid(form)
