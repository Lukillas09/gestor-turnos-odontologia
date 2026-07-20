from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import FormView, TemplateView, UpdateView

from usuarios.mixins import RolRequeridoMixin
from usuarios.roles import (
    obtener_odontologo_del_usuario,
    puede_configurar_servicios,
    puede_gestionar_catalogo_servicios,
    puede_ver_configuracion_servicios,
)

from ..forms import (
    ConfiguracionAgendaInteligenteForm,
    TipoTurnoForm,
    TipoTurnoOdontologoForm,
)
from ..models import (
    ConfiguracionAgendaInteligente,
    Odontologo,
    TipoTurno,
    TipoTurnoOdontologo,
)


class ConfiguracionServiciosView(RolRequeridoMixin, TemplateView):
    template_name = "turnos/tipos_turno/configuracion.html"

    def test_func(self):
        return puede_ver_configuracion_servicios(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        odontologo = self._obtener_odontologo_visible()
        context["odontologos"] = Odontologo.objects.filter(activo=True).select_related("usuario")
        context["odontologo_seleccionado"] = odontologo
        context["puede_editar_servicios"] = bool(
            odontologo and puede_configurar_servicios(self.request.user, odontologo)
        )
        context["puede_gestionar_catalogo"] = puede_gestionar_catalogo_servicios(self.request.user)
        context["configuraciones"] = (
            TipoTurnoOdontologo.objects.filter(odontologo=odontologo)
            .select_related("tipo_turno")
            .order_by("tipo_turno__orden_publico", "tipo_turno__nombre")
            if odontologo
            else TipoTurnoOdontologo.objects.none()
        )
        context["configuracion_agenda"] = (
            ConfiguracionAgendaInteligente.objects.filter(odontologo=odontologo).first()
            if odontologo
            else None
        )
        context["tipos_globales"] = (
            TipoTurno.objects.all()
            if context["puede_gestionar_catalogo"]
            else TipoTurno.objects.none()
        )
        return context

    def _obtener_odontologo_visible(self):
        propio = obtener_odontologo_del_usuario(self.request.user)
        if propio and not puede_gestionar_catalogo_servicios(self.request.user):
            return propio

        odontologo_id = self.request.GET.get("odontologo")
        if odontologo_id:
            try:
                odontologo = Odontologo.objects.filter(pk=odontologo_id).first()
            except (TypeError, ValueError):
                odontologo = None
            if odontologo:
                return odontologo
        return propio or Odontologo.objects.filter(activo=True).select_related("usuario").first()


class ServicioOdontologoPermisoMixin(RolRequeridoMixin):
    odontologo = None

    def test_func(self):
        self.odontologo = self.obtener_odontologo()
        return bool(
            self.odontologo and puede_configurar_servicios(self.request.user, self.odontologo)
        )

    def obtener_odontologo(self):
        raise NotImplementedError

    def get_permission_denied_message(self):
        return "No tenés permiso para modificar los servicios de este profesional."

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["odontologo"] = self.odontologo
        return kwargs


class TipoTurnoOdontologoCreateView(ServicioOdontologoPermisoMixin, FormView):
    form_class = TipoTurnoOdontologoForm
    template_name = "turnos/tipos_turno/form.html"

    def obtener_odontologo(self):
        return get_object_or_404(Odontologo.objects.select_related("usuario"), pk=self.kwargs["pk"])

    def form_valid(self, form):
        form.save()
        messages.success(self.request, "Servicio agregado al profesional.")
        return redirect(self.get_success_url())

    def get_success_url(self):
        return f"{reverse('turnos:configuracion_servicios')}?odontologo={self.odontologo.pk}"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "titulo": "Agregar servicio",
                "subtitulo": self.odontologo.nombre_completo,
                "texto_boton": "Guardar servicio",
                "url_cancelar": self.get_success_url(),
            }
        )
        return context


class TipoTurnoOdontologoUpdateView(ServicioOdontologoPermisoMixin, UpdateView):
    model = TipoTurnoOdontologo
    form_class = TipoTurnoOdontologoForm
    template_name = "turnos/tipos_turno/form.html"

    def obtener_odontologo(self):
        self.object = self.get_object()
        return self.object.odontologo

    def get_queryset(self):
        return super().get_queryset().select_related("odontologo", "odontologo__usuario")

    def form_valid(self, form):
        self.object = form.save()
        messages.success(self.request, "Configuración del servicio actualizada.")
        return redirect(self.get_success_url())

    def get_success_url(self):
        return f"{reverse('turnos:configuracion_servicios')}?odontologo={self.odontologo.pk}"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "titulo": "Editar servicio",
                "subtitulo": self.object.tipo_turno.nombre,
                "texto_boton": "Guardar cambios",
                "url_cancelar": self.get_success_url(),
            }
        )
        return context


class ConfiguracionAgendaUpdateView(ServicioOdontologoPermisoMixin, UpdateView):
    model = ConfiguracionAgendaInteligente
    form_class = ConfiguracionAgendaInteligenteForm
    template_name = "turnos/tipos_turno/form.html"

    def obtener_odontologo(self):
        return get_object_or_404(Odontologo.objects.select_related("usuario"), pk=self.kwargs["pk"])

    def get_object(self, queryset=None):
        return ConfiguracionAgendaInteligente.objects.get_or_create(odontologo=self.odontologo)[0]

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.pop("odontologo", None)
        return kwargs

    def form_valid(self, form):
        self.object = form.save()
        messages.success(self.request, "Configuración de agenda actualizada.")
        return redirect(self.get_success_url())

    def get_success_url(self):
        return f"{reverse('turnos:configuracion_servicios')}?odontologo={self.odontologo.pk}"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "titulo": "Configurar agenda inteligente",
                "subtitulo": self.odontologo.nombre_completo,
                "texto_boton": "Guardar configuración",
                "url_cancelar": self.get_success_url(),
            }
        )
        return context


class CatalogoServiciosPermisoMixin(RolRequeridoMixin):
    def test_func(self):
        return puede_gestionar_catalogo_servicios(self.request.user)

    def get_permission_denied_message(self):
        return "No tenés permiso para modificar el catálogo global de servicios."


class TipoTurnoCreateView(CatalogoServiciosPermisoMixin, FormView):
    form_class = TipoTurnoForm
    template_name = "turnos/tipos_turno/form.html"

    def form_valid(self, form):
        tipo = form.save(commit=False)
        tipo.creado_por = self.request.user
        tipo.actualizado_por = self.request.user
        tipo.save()
        messages.success(self.request, "Tipo de turno creado.")
        return redirect("turnos:configuracion_servicios")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "titulo": "Nuevo tipo de turno",
                "subtitulo": "Catálogo global del consultorio",
                "texto_boton": "Crear tipo",
                "url_cancelar": reverse("turnos:configuracion_servicios"),
            }
        )
        return context


class TipoTurnoUpdateView(CatalogoServiciosPermisoMixin, UpdateView):
    model = TipoTurno
    form_class = TipoTurnoForm
    template_name = "turnos/tipos_turno/form.html"

    def form_valid(self, form):
        tipo = form.save(commit=False)
        tipo.actualizado_por = self.request.user
        tipo.save()
        self.object = tipo
        messages.success(self.request, "Tipo de turno actualizado.")
        return redirect("turnos:configuracion_servicios")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "titulo": "Editar tipo de turno",
                "subtitulo": self.object.nombre,
                "texto_boton": "Guardar cambios",
                "url_cancelar": reverse("turnos:configuracion_servicios"),
            }
        )
        return context
