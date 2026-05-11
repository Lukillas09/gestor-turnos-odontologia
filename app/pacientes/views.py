from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import CreateView, DetailView, FormView, ListView, UpdateView

from historias.models import HistoriaClinica
from historias.permissions import (
    limitar_historias_clinicas_por_usuario,
    puede_ver_historia_de_paciente,
)
from turnos.models import Turno

from usuarios.mixins import (
    BorrarPacientesRequeridoMixin,
    GestionConsultorioRequeridaMixin,
    VerPacientesRequeridoMixin,
)
from usuarios.roles import puede_gestionar_historias_clinicas

from .forms import FichaOdontologicaForm, PacienteDeleteConfirmationForm, PacienteForm
from .models import FichaOdontologica, Paciente


class PacienteListView(VerPacientesRequeridoMixin, ListView):
    model = Paciente
    template_name = "pacientes/paciente_list.html"
    context_object_name = "pacientes"
    paginate_by = 20

    def get_queryset(self):
        queryset = super().get_queryset()
        busqueda = self.request.GET.get("q", "").strip()

        if busqueda:
            queryset = queryset.filter(
                Q(nombre__icontains=busqueda)
                | Q(apellido__icontains=busqueda)
                | Q(documento__icontains=busqueda)
                | Q(telefono__icontains=busqueda)
                | Q(email__icontains=busqueda)
                | Q(localidad__icontains=busqueda)
                | Q(obra_social__icontains=busqueda)
            )

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["busqueda"] = self.request.GET.get("q", "").strip()
        return context


class PacienteCreateView(GestionConsultorioRequeridaMixin, CreateView):
    model = Paciente
    form_class = PacienteForm
    template_name = "pacientes/paciente_form.html"
    success_url = reverse_lazy("pacientes:lista")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["titulo"] = "Nuevo paciente"
        context["subtitulo"] = "Carga de datos personales y de contacto."
        context["texto_boton"] = "Guardar paciente"
        context["url_cancelar"] = reverse_lazy("pacientes:lista")
        return context

    def form_valid(self, form):
        messages.success(self.request, "Paciente creado correctamente.")
        return super().form_valid(form)


class PacienteDetailView(VerPacientesRequeridoMixin, DetailView):
    model = Paciente
    template_name = "pacientes/paciente_detail.html"
    context_object_name = "paciente"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        puede_ver_historia = (
            puede_gestionar_historias_clinicas(self.request.user)
            and puede_ver_historia_de_paciente(self.request.user, self.object)
        )
        context["turnos_recientes"] = (
            self.object.turnos.select_related("odontologo", "odontologo__usuario")
            .order_by("-fecha", "-hora_inicio")[:5]
        )
        context["turnos_pendientes_o_confirmados"] = self.object.turnos.filter(
            estado__in=[Turno.Estado.PENDIENTE, Turno.Estado.CONFIRMADO],
        ).count()
        context["ficha_odontologica"] = getattr(
            self.object,
            "ficha_odontologica",
            None,
        )
        context["puede_ver_historia_clinica"] = puede_ver_historia
        context["historias_recientes"] = (
            limitar_historias_clinicas_por_usuario(
                HistoriaClinica.objects.filter(paciente=self.object),
                self.request.user,
            )
            .select_related("odontologo", "odontologo__usuario")
            .order_by("-fecha", "-creado_en")[:3]
            if puede_ver_historia
            else []
        )
        return context


class FichaOdontologicaUpdateView(VerPacientesRequeridoMixin, UpdateView):
    model = FichaOdontologica
    form_class = FichaOdontologicaForm
    template_name = "pacientes/ficha_odontologica_form.html"
    context_object_name = "ficha"

    def dispatch(self, request, *args, **kwargs):
        self.paciente = get_object_or_404(Paciente, pk=self.kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        ficha, _ = FichaOdontologica.objects.get_or_create(paciente=self.paciente)
        return ficha

    def get_success_url(self):
        return reverse_lazy("pacientes:detalle", kwargs={"pk": self.paciente.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["paciente"] = self.paciente
        context["titulo"] = "Ficha odontologica"
        context["subtitulo"] = "Antecedentes y datos clinicos generales del paciente."
        return context

    def form_valid(self, form):
        form.instance.paciente = self.paciente
        form.instance.actualizado_por = self.request.user
        messages.success(self.request, "Ficha odontologica actualizada correctamente.")
        return super().form_valid(form)


class PacienteUpdateView(GestionConsultorioRequeridaMixin, UpdateView):
    model = Paciente
    form_class = PacienteForm
    template_name = "pacientes/paciente_form.html"

    def get_success_url(self):
        return reverse_lazy("pacientes:detalle", kwargs={"pk": self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["titulo"] = "Editar paciente"
        context["subtitulo"] = "Actualizacion de datos personales y de contacto."
        context["texto_boton"] = "Guardar cambios"
        context["url_cancelar"] = self.get_success_url()
        return context

    def form_valid(self, form):
        messages.success(self.request, "Paciente actualizado correctamente.")
        return super().form_valid(form)


class PacienteDeleteView(BorrarPacientesRequeridoMixin, FormView):
    form_class = PacienteDeleteConfirmationForm
    template_name = "pacientes/paciente_confirm_delete.html"
    success_url = reverse_lazy("pacientes:lista")
    estados_que_bloquean_borrado = [
        Turno.Estado.PENDIENTE,
        Turno.Estado.CONFIRMADO,
    ]

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)

        self.paciente = self.get_object()
        return super().dispatch(request, *args, **kwargs)

    def get_object(self):
        return get_object_or_404(Paciente, pk=self.kwargs["pk"])

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["paciente"] = self.paciente
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["paciente"] = self.paciente
        return context

    def form_valid(self, form):
        nombre_completo = self.paciente.nombre_completo

        if self._tiene_turnos_que_bloquean_borrado():
            form.add_error(
                None,
                "No se puede borrar el paciente porque tiene turnos pendientes o confirmados.",
            )
            messages.error(
                self.request,
                "No se puede borrar el paciente porque tiene turnos pendientes o confirmados.",
            )
            return super().form_invalid(form)

        if self._tiene_historias_clinicas():
            form.add_error(
                None,
                "No se puede borrar el paciente porque tiene historia clinica cargada.",
            )
            messages.error(
                self.request,
                "No se puede borrar el paciente porque tiene historia clinica cargada.",
            )
            return super().form_invalid(form)

        self._borrar_turnos_que_no_bloquean()
        self.paciente.delete()
        messages.success(self.request, f"Paciente {nombre_completo} borrado correctamente.")
        return super().form_valid(form)

    def _tiene_turnos_que_bloquean_borrado(self):
        return self.paciente.turnos.filter(
            estado__in=self.estados_que_bloquean_borrado
        ).exists()

    def _borrar_turnos_que_no_bloquean(self):
        self.paciente.turnos.exclude(
            estado__in=self.estados_que_bloquean_borrado
        ).delete()

    def _tiene_historias_clinicas(self):
        return self.paciente.historias_clinicas.exists()
