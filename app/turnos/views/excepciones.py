from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import FormView, ListView

from usuarios.mixins import VerTurnosRequeridoMixin

from ..excepcion_permissions import (
    limitar_excepciones_por_usuario,
    puede_modificar_excepcion_agenda,
    puede_ver_excepciones_agenda,
)
from ..excepciones import (
    TurnosAfectadosPorExcepcionError,
    actualizar_excepcion_agenda,
    crear_excepcion_agenda,
    desactivar_excepcion_agenda,
)
from ..forms import ExcepcionAgendaForm
from ..models import ExcepcionAgenda


class ExcepcionAgendaPermisoMixin(VerTurnosRequeridoMixin):
    def test_func(self):
        return puede_ver_excepciones_agenda(self.request.user)


class ExcepcionAgendaListView(ExcepcionAgendaPermisoMixin, ListView):
    model = ExcepcionAgenda
    template_name = "turnos/excepciones/lista.html"
    context_object_name = "excepciones"
    paginate_by = 20

    def get_queryset(self):
        queryset = limitar_excepciones_por_usuario(
            ExcepcionAgenda.objects.select_related(
                "odontologo",
                "odontologo__usuario",
                "creada_por",
                "actualizada_por",
            ),
            self.request.user,
        )
        estado = self.request.GET.get("estado", "activas")

        if estado == "inactivas":
            queryset = queryset.filter(activo=False)
        elif estado != "todas":
            queryset = queryset.filter(activo=True)

        return queryset.order_by("-activo", "fecha_desde", "hora_inicio")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["estado_actual"] = self.request.GET.get("estado", "activas")
        context["puede_crear_excepcion"] = True
        return context


class ExcepcionAgendaFormMixin(ExcepcionAgendaPermisoMixin, FormView):
    form_class = ExcepcionAgendaForm
    template_name = "turnos/excepciones/form.html"
    success_url = reverse_lazy("turnos:excepciones")
    object = None
    titulo = "Nueva excepción de agenda"
    texto_boton = "Guardar excepción"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["usuario"] = self.request.user

        if self.object is not None:
            kwargs["instance"] = self.object

        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["titulo"] = self.titulo
        context["texto_boton"] = self.texto_boton
        context["url_cancelar"] = reverse("turnos:excepciones")
        context["turnos_afectados"] = getattr(self, "turnos_afectados", [])
        context["requiere_confirmacion_afectados"] = getattr(
            self,
            "requiere_confirmacion_afectados",
            False,
        )
        context["object"] = self.object
        return context

    def _datos_excepcion(self, form):
        return {campo: form.cleaned_data[campo] for campo in ExcepcionAgendaForm.Meta.fields}


class ExcepcionAgendaCreateView(ExcepcionAgendaFormMixin):
    def form_valid(self, form):
        try:
            self.object = crear_excepcion_agenda(
                self._datos_excepcion(form),
                usuario=self.request.user,
                confirmar_afectados=form.cleaned_data.get("confirmar_afectados", False),
            )
        except TurnosAfectadosPorExcepcionError as error:
            self.turnos_afectados = error.turnos
            self.requiere_confirmacion_afectados = True
            return self.form_invalid(form)
        except ValidationError as error:
            form.add_error(None, error)
            return self.form_invalid(form)

        messages.success(self.request, "Excepción de agenda creada correctamente.")
        return redirect(self.get_success_url())


class ExcepcionAgendaUpdateView(ExcepcionAgendaFormMixin):
    titulo = "Editar excepción de agenda"
    texto_boton = "Guardar cambios"

    def dispatch(self, request, *args, **kwargs):
        self.object = get_object_or_404(
            limitar_excepciones_por_usuario(ExcepcionAgenda.objects.all(), request.user),
            pk=kwargs["pk"],
        )

        if not puede_modificar_excepcion_agenda(request.user, self.object):
            raise PermissionDenied("No tenés permiso para modificar esta excepción.")

        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            self.object = actualizar_excepcion_agenda(
                self.object,
                self._datos_excepcion(form),
                usuario=self.request.user,
                confirmar_afectados=form.cleaned_data.get("confirmar_afectados", False),
            )
        except TurnosAfectadosPorExcepcionError as error:
            self.turnos_afectados = error.turnos
            self.requiere_confirmacion_afectados = True
            return self.form_invalid(form)
        except ValidationError as error:
            form.add_error(None, error)
            return self.form_invalid(form)

        messages.success(self.request, "Excepción de agenda actualizada correctamente.")
        return redirect(self.get_success_url())


class ExcepcionAgendaDeactivateView(ExcepcionAgendaPermisoMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        excepcion = get_object_or_404(
            limitar_excepciones_por_usuario(ExcepcionAgenda.objects.all(), request.user),
            pk=pk,
        )

        if not puede_modificar_excepcion_agenda(request.user, excepcion):
            raise PermissionDenied("No tenés permiso para desactivar esta excepción.")

        desactivar_excepcion_agenda(excepcion, usuario=request.user)
        messages.success(request, "Excepción de agenda desactivada correctamente.")
        return redirect("turnos:excepciones")
