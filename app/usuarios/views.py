from django.contrib.auth.views import LoginView, redirect_to_login
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView

from historias.models import HistoriaClinica
from pacientes.models import Paciente
from turnos.models import Turno
from turnos.selectors import obtener_bloques_agenda_del_dia

from .roles import limitar_turnos_por_usuario
from .roles import (
    puede_configurar_disponibilidad,
    puede_gestionar_consultorio,
    puede_gestionar_historias_clinicas,
    puede_ver_turnos,
)


class LoginInternoView(LoginView):
    template_name = "registration/login.html"

    def get_success_url(self):
        return reverse("inicio")


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
        proximos_turnos = (
            turnos_visibles.select_related("paciente", "odontologo", "odontologo__usuario")
            .filter(
                fecha__gte=hoy,
                estado__in=[Turno.Estado.PENDIENTE, Turno.Estado.CONFIRMADO],
            )
            .order_by("fecha", "hora_inicio")[:6]
        )

        context.update(
            {
                "hoy": hoy,
                "rol_principal": self._obtener_rol_principal(usuario),
                "turnos_hoy": turnos_visibles.filter(fecha=hoy).count(),
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


def obtener_url_inicio_para_usuario(usuario):
    if puede_gestionar_consultorio(usuario):
        return reverse("pacientes:lista")

    if puede_ver_turnos(usuario):
        return reverse("turnos:lista")

    if usuario.is_staff and puede_configurar_disponibilidad(usuario):
        return reverse("admin:index")

    return reverse("turnos:lista")
