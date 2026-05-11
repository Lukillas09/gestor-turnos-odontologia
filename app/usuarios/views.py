from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, redirect_to_login
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import FormView, TemplateView

from historias.models import HistoriaClinica
from pacientes.models import Paciente
from turnos.models import GoogleCalendarConexion, Turno
from turnos.selectors import obtener_bloques_agenda_del_dia, obtener_resumen_estados

from .forms import PerfilUsuarioForm
from .roles import (
    limitar_turnos_por_usuario,
    obtener_odontologo_del_usuario,
    puede_configurar_disponibilidad,
    puede_gestionar_consultorio,
    puede_gestionar_historias_clinicas,
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
        turnos_visibles = limitar_turnos_por_usuario(Turno.objects.all(), usuario)
        turnos_visibles_con_relaciones = turnos_visibles.select_related(
            "paciente",
            "odontologo",
            "odontologo__usuario",
        )
        turnos_hoy_queryset = turnos_visibles_con_relaciones.filter(fecha=hoy)
        pendientes_confirmacion = turnos_visibles_con_relaciones.filter(
            estado=Turno.Estado.PENDIENTE,
            fecha__gte=hoy,
        ).order_by("fecha", "hora_inicio")[:8]
        proximos_turnos = (
            turnos_visibles_con_relaciones.filter(
                fecha__gte=hoy,
                estado__in=[Turno.Estado.PENDIENTE, Turno.Estado.CONFIRMADO],
            )
            .order_by("fecha", "hora_inicio")[:6]
        )

        context.update(
            {
                "hoy": hoy,
                "rol_principal": self._obtener_rol_principal(usuario),
                "turnos_hoy": turnos_hoy_queryset.count(),
                "turnos_pendientes": turnos_visibles.filter(
                    estado=Turno.Estado.PENDIENTE
                ).count(),
                "turnos_confirmados_hoy": turnos_visibles.filter(
                    fecha=hoy,
                    estado=Turno.Estado.CONFIRMADO,
                ).count(),
                "pacientes_total": Paciente.objects.count(),
                "historias_total": HistoriaClinica.objects.count()
                if puede_gestionar_historias_clinicas(usuario)
                else None,
                "proximos_turnos": proximos_turnos,
                "bloques_hoy": obtener_bloques_agenda_del_dia(hoy)[:8]
                if puede_gestionar_consultorio(usuario)
                else [],
                "turnos_hoy_lista": turnos_hoy_queryset.order_by("hora_inicio")[:10],
                "pendientes_confirmacion": pendientes_confirmacion,
                "resumen_hoy": obtener_resumen_estados(turnos_hoy_queryset),
                "proximos_controles": self._obtener_proximos_controles(usuario, hoy),
                "errores_google_calendar": self._obtener_errores_google_calendar(usuario),
                "recordatorios_enviados": turnos_visibles.filter(
                    recordatorio_email_enviado_en__isnull=False,
                ).count(),
                "recordatorios_fallidos": turnos_visibles.exclude(
                    recordatorio_email_ultimo_error="",
                ).count(),
                "recordatorios_fallidos_recientes": turnos_visibles_con_relaciones.exclude(
                    recordatorio_email_ultimo_error="",
                ).order_by("-actualizado_en")[:5],
            }
        )
        return context

    def _obtener_rol_principal(self, usuario):
        if puede_gestionar_consultorio(usuario):
            return "Recepcion"

        if puede_gestionar_historias_clinicas(usuario):
            return "Odontologo"

        if usuario.is_staff and puede_configurar_disponibilidad(usuario):
            return "Administrador"

        return "Usuario interno"

    def _obtener_proximos_controles(self, usuario, hoy):
        queryset = HistoriaClinica.objects.filter(
            proximo_control__isnull=False,
            proximo_control__gte=hoy,
        ).select_related(
            "paciente",
            "odontologo",
            "odontologo__usuario",
        )

        odontologo = obtener_odontologo_del_usuario(usuario)

        if odontologo and not puede_gestionar_consultorio(usuario):
            queryset = queryset.filter(odontologo=odontologo)
        elif not (
            puede_gestionar_consultorio(usuario)
            or puede_configurar_disponibilidad(usuario)
            or puede_gestionar_historias_clinicas(usuario)
        ):
            return []

        return queryset.order_by("proximo_control", "paciente__apellido")[:8]

    def _obtener_errores_google_calendar(self, usuario):
        queryset = GoogleCalendarConexion.objects.exclude(
            ultimo_error="",
        ).select_related("odontologo", "odontologo__usuario")

        odontologo = obtener_odontologo_del_usuario(usuario)

        if odontologo and not (
            puede_gestionar_consultorio(usuario)
            or puede_configurar_disponibilidad(usuario)
        ):
            queryset = queryset.filter(odontologo=odontologo)
        elif not (
            puede_gestionar_consultorio(usuario)
            or puede_configurar_disponibilidad(usuario)
            or odontologo
        ):
            return []

        return queryset.order_by("-actualizado_en")[:5]


def obtener_url_inicio_para_usuario(usuario):
    if puede_gestionar_consultorio(usuario):
        return reverse("pacientes:lista")

    if puede_ver_turnos(usuario):
        return reverse("turnos:lista")

    if usuario.is_staff and puede_configurar_disponibilidad(usuario):
        return reverse("admin:index")

    return reverse("turnos:lista")
