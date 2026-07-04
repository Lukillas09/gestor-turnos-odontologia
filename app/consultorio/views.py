from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import TemplateView

from .forms import ConfiguracionConsultorioForm
from .permissions import puede_configurar_identidad_consultorio
from .services import (
    guardar_configuracion_consultorio,
    obtener_o_crear_configuracion_consultorio,
    restaurar_configuracion_consultorio,
)


class ConfiguracionConsultorioView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "consultorio/configuracion_form.html"

    def test_func(self):
        return puede_configurar_identidad_consultorio(self.request.user)

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            raise PermissionDenied("No tenés permiso para configurar el consultorio.")

        return super().handle_no_permission()

    def get(self, request, *args, **kwargs):
        configuracion = obtener_o_crear_configuracion_consultorio()
        return self.render_to_response(
            self.get_context_data(
                configuracion=configuracion,
                form=ConfiguracionConsultorioForm(instance=configuracion),
            )
        )

    def post(self, request, *args, **kwargs):
        if request.POST.get("accion") == "restaurar_defaults":
            restaurar_configuracion_consultorio(request.user)
            messages.success(request, "Perfil del consultorio restaurado a los valores predeterminados.")
            return redirect(reverse("consultorio:configuracion"))

        configuracion = obtener_o_crear_configuracion_consultorio()
        form = ConfiguracionConsultorioForm(
            request.POST,
            request.FILES,
            instance=configuracion,
        )

        if form.is_valid():
            guardar_configuracion_consultorio(configuracion, form, request.user)
            messages.success(request, "Perfil del consultorio actualizado correctamente.")
            return redirect(reverse("consultorio:configuracion"))

        return self.render_to_response(
            self.get_context_data(configuracion=configuracion, form=form)
        )
