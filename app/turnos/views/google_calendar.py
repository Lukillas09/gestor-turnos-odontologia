from importlib import import_module
from secrets import token_urlsafe

from django.contrib import messages
from django.shortcuts import redirect
from django.views import View
from django.views.generic import TemplateView

from usuarios.mixins import GoogleCalendarRequeridoMixin
from usuarios.roles import obtener_odontologo_del_usuario

from ..google_calendar_oauth import (
    desconectar_google_calendar_de_odontologo,
    guardar_tokens_oauth_de_odontologo,
)
from ..integrations.google_calendar import (
    GoogleCalendarError,
    construir_url_autorizacion_google_calendar,
)
from ..models import GoogleCalendarConexion

GOOGLE_CALENDAR_OAUTH_STATE_SESSION_KEY = "google_calendar_oauth_state"


class GoogleCalendarOdontologoMixin(GoogleCalendarRequeridoMixin):
    def obtener_odontologo(self):
        return obtener_odontologo_del_usuario(self.request.user)


class GoogleCalendarConexionView(GoogleCalendarOdontologoMixin, TemplateView):
    template_name = "turnos/google_calendar_conexion.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        odontologo = self.obtener_odontologo()
        context["odontologo"] = odontologo
        context["conexion"] = GoogleCalendarConexion.objects.filter(odontologo=odontologo).first()
        return context


class GoogleCalendarConectarView(GoogleCalendarOdontologoMixin, View):
    def get(self, request):
        odontologo = self.obtener_odontologo()
        state = token_urlsafe(32)
        request.session[GOOGLE_CALENDAR_OAUTH_STATE_SESSION_KEY] = state

        try:
            url_autorizacion = construir_url_autorizacion_google_calendar(
                state=state,
                login_hint=odontologo.usuario.email,
            )
        except GoogleCalendarError as error:
            messages.error(request, str(error))
            return redirect("turnos:google_calendar")

        return redirect(url_autorizacion)


class GoogleCalendarCallbackView(GoogleCalendarOdontologoMixin, View):
    def get(self, request):
        error_google = request.GET.get("error")

        if error_google:
            messages.error(request, f"Google no autorizó la conexión: {error_google}.")
            return redirect("turnos:google_calendar")

        state_recibido = request.GET.get("state")
        state_esperado = request.session.pop(
            GOOGLE_CALENDAR_OAUTH_STATE_SESSION_KEY,
            None,
        )

        if not state_esperado or state_recibido != state_esperado:
            messages.error(request, "No se pudo validar la respuesta de Google Calendar.")
            return redirect("turnos:google_calendar")

        code = request.GET.get("code")

        if not code:
            messages.error(request, "Google no devolvio un codigo de autorizacion.")
            return redirect("turnos:google_calendar")

        try:
            views_publicas = import_module("turnos.views")
            tokens = views_publicas.intercambiar_codigo_por_tokens(code)
            guardar_tokens_oauth_de_odontologo(self.obtener_odontologo(), tokens)
        except GoogleCalendarError as error:
            messages.error(request, str(error))
            return redirect("turnos:google_calendar")

        messages.success(request, "Google Calendar conectado correctamente.")
        return redirect("turnos:google_calendar")


class GoogleCalendarDesconectarView(GoogleCalendarOdontologoMixin, View):
    def post(self, request):
        desconectar_google_calendar_de_odontologo(self.obtener_odontologo())
        messages.success(request, "Google Calendar desconectado correctamente.")
        return redirect("turnos:google_calendar")
