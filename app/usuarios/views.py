from datetime import timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, redirect_to_login
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import FormView, TemplateView

from turnos.models import Turno
from turnos.selectors import obtener_inicio_semana
from turnos.solicitudes_publicas.selectors import (
    obtener_alertas_administrativas_publicas,
    obtener_turnos_con_revision_publica_pendiente,
)

from .forms import PerfilUsuarioForm
from .roles import (
    limitar_turnos_por_usuario,
    obtener_odontologo_del_usuario,
    puede_configurar_disponibilidad,
    puede_gestionar_consultorio,
    puede_revisar_solicitudes_publicas,
    puede_ver_turnos,
)


class LoginInternoView(LoginView):
    template_name = "registration/login.html"

    def get_success_url(self):
        return reverse("inicio")


class PerfilUsuarioView(LoginRequiredMixin, FormView):
    template_name = "usuarios/perfil.html"
    form_class = PerfilUsuarioForm
    success_url = reverse_lazy("perfil")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["usuario"] = self.request.user
        kwargs["odontologo"] = obtener_odontologo_del_usuario(self.request.user)
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["odontologo"] = obtener_odontologo_del_usuario(self.request.user)
        return context

    def form_valid(self, form):
        form.save()
        messages.success(self.request, "Perfil actualizado correctamente.")
        return super().form_valid(form)


class InicioView(TemplateView):
    template_name = "usuarios/inicio.html"

    def get(self, request):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), login_url=reverse("login"))

        return super().get(request)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        usuario = self.request.user
        hoy = timezone.localdate()
        inicio_semana = obtener_inicio_semana(hoy)
        fin_semana = inicio_semana + timedelta(days=6)
        odontologo = obtener_odontologo_del_usuario(usuario)
        es_dashboard_odontologo = odontologo is not None and not puede_gestionar_consultorio(
            usuario
        )
        odontologo_filtro_url = odontologo if es_dashboard_odontologo else None
        turnos_visibles = limitar_turnos_por_usuario(Turno.objects.all(), usuario)
        turnos_visibles_con_relaciones = turnos_visibles.select_related(
            "paciente",
            "odontologo",
            "odontologo__usuario",
            "solicitud_publica",
        )
        turnos_hoy_queryset = turnos_visibles_con_relaciones.filter(fecha=hoy)
        turnos_semana_queryset = turnos_visibles_con_relaciones.filter(
            fecha__gte=inicio_semana,
            fecha__lte=fin_semana,
        )
        pendientes_queryset = turnos_visibles_con_relaciones.filter(
            estado=Turno.Estado.PENDIENTE,
        )
        turnos_datos_por_revisar_queryset = obtener_turnos_con_revision_publica_pendiente(
            pendientes_queryset,
        )
        puede_revisar_publicas = puede_revisar_solicitudes_publicas(usuario)
        alertas_administrativas_publicas = (
            obtener_alertas_administrativas_publicas().count() if puede_revisar_publicas else 0
        )

        context.update(
            {
                "hoy": hoy,
                "inicio_semana": inicio_semana,
                "fin_semana": fin_semana,
                "odontologo_inicio": odontologo,
                "es_dashboard_odontologo": es_dashboard_odontologo,
                "rol_principal": self._obtener_rol_principal(usuario),
                "turnos_hoy": turnos_hoy_queryset.count(),
                "turnos_semana": turnos_semana_queryset.count(),
                "turnos_pendientes": pendientes_queryset.count(),
                "turnos_datos_por_revisar": turnos_datos_por_revisar_queryset.count(),
                "alertas_administrativas_publicas": alertas_administrativas_publicas,
                "turnos_hoy_lista": turnos_hoy_queryset.order_by("hora_inicio")[:10],
                "url_turnos_hoy": self._crear_url_agenda_dia(hoy, odontologo_filtro_url),
                "url_turnos_semana": self._crear_url_agenda_semana(
                    hoy,
                    odontologo_filtro_url,
                ),
                "url_turnos_pendientes": self._crear_url_turnos_pendientes(
                    odontologo_filtro_url,
                ),
                "url_turnos_datos_por_revisar": self._crear_url_turnos_datos_por_revisar(
                    odontologo_filtro_url,
                ),
                "url_alertas_administrativas": reverse("turnos:alertas_administrativas"),
            }
        )
        return context

    def _obtener_rol_principal(self, usuario):
        if puede_gestionar_consultorio(usuario):
            return "Recepción"

        if obtener_odontologo_del_usuario(usuario):
            return "Odontólogo"

        if usuario.is_staff and puede_configurar_disponibilidad(usuario):
            return "Administrador"

        return "Usuario interno"

    @staticmethod
    def _crear_url_agenda_dia(fecha, odontologo):
        return InicioView._crear_url_con_query(
            reverse("turnos:agenda_dia"),
            InicioView._crear_filtros_fecha_odontologo(fecha, odontologo),
        )

    @staticmethod
    def _crear_url_agenda_semana(fecha, odontologo):
        return InicioView._crear_url_con_query(
            reverse("turnos:agenda_semana"),
            InicioView._crear_filtros_fecha_odontologo(fecha, odontologo),
        )

    @staticmethod
    def _crear_url_turnos_pendientes(odontologo):
        filtros = {"estado": Turno.Estado.PENDIENTE}

        if odontologo:
            filtros["odontologo"] = odontologo.pk

        return InicioView._crear_url_con_query(reverse("turnos:lista"), filtros)

    @staticmethod
    def _crear_url_turnos_datos_por_revisar(odontologo):
        filtros = {
            "estado": Turno.Estado.PENDIENTE,
            "datos_por_revisar": "on",
        }

        if odontologo:
            filtros["odontologo"] = odontologo.pk

        return InicioView._crear_url_con_query(reverse("turnos:lista"), filtros)

    @staticmethod
    def _crear_filtros_fecha_odontologo(fecha, odontologo):
        filtros = {"fecha": fecha.isoformat()}

        if odontologo:
            filtros["odontologo"] = odontologo.pk

        return filtros

    @staticmethod
    def _crear_url_con_query(url, filtros):
        return f"{url}?{urlencode(filtros)}"


def obtener_url_inicio_para_usuario(usuario):
    if puede_gestionar_consultorio(usuario):
        return reverse("pacientes:lista")

    if puede_ver_turnos(usuario):
        return reverse("turnos:lista")

    if usuario.is_staff and puede_configurar_disponibilidad(usuario):
        return reverse("admin:index")

    return reverse("turnos:lista")
