from datetime import timedelta
from secrets import token_urlsafe

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views import View
from django.views.generic import (
    CreateView,
    DetailView,
    FormView,
    ListView,
    TemplateView,
    UpdateView,
)

from usuarios.mixins import (
    GestionConsultorioRequeridaMixin,
    GoogleCalendarRequeridoMixin,
    VerTurnosRequeridoMixin,
)
from usuarios.roles import (
    limitar_turnos_por_usuario,
    obtener_odontologo_del_usuario,
    obtener_odontologo_visible,
    puede_reprogramar_turno,
    puede_reintentar_sincronizacion_google_calendar,
)

from .forms import (
    AgendaFiltroForm,
    SolicitudTurnoBusquedaPublicaForm,
    SolicitudTurnoPublicaForm,
    TurnoCreateForm,
    TurnoFiltroForm,
    TurnoForm,
    TurnoHorarioBusquedaForm,
    TurnoReprogramacionForm,
)
from .google_calendar_oauth import (
    desconectar_google_calendar_de_odontologo,
    guardar_tokens_oauth_de_odontologo,
)
from .integrations.google_calendar import (
    GoogleCalendarError,
    construir_url_autorizacion_google_calendar,
    intercambiar_codigo_por_tokens,
)
from .models import (
    GoogleCalendarConexion,
    Odontologo,
    Turno,
    normalizar_error_google_calendar_para_usuario,
)
from .selectors import (
    obtener_agenda_diaria_por_odontologo,
    obtener_agenda_semanal_por_odontologo,
    obtener_horarios_disponibles,
    obtener_bloques_agenda_del_dia,
    obtener_inicio_semana,
    obtener_resumen_estados,
    obtener_turnos_de_la_semana,
    obtener_turnos_del_dia,
)
from .services import (
    actualizar_turno_desde_formulario,
    cancelar_turno,
    confirmar_turno,
    crear_solicitud_turno_publica,
    crear_turno_desde_formulario,
    reprogramar_turno,
    reintentar_sincronizacion_google_calendar,
)


GOOGLE_CALENDAR_OAUTH_STATE_SESSION_KEY = "google_calendar_oauth_state"


class HorariosDisponiblesJsonView(View):
    def get(self, request):
        odontologo = self._obtener_odontologo(request.GET.get("odontologo"))
        fecha = self._obtener_fecha(request.GET.get("fecha"))

        if not odontologo or not fecha:
            return JsonResponse(
                {
                    "horarios": [],
                    "mensaje": "Elegi odontologo y fecha para ver horarios disponibles.",
                }
            )

        duracion_minutos = self._obtener_duracion_minutos(
            request.GET.get("duracion_minutos"),
            odontologo,
        )
        turno_excluido = self._obtener_turno_excluido(
            request.GET.get("turno_id"),
            request.user,
        )
        horarios = obtener_horarios_disponibles(
            odontologo=odontologo,
            fecha=fecha,
            duracion_minutos=duracion_minutos,
            turno_excluido=turno_excluido,
        )

        if not horarios:
            return JsonResponse(
                {
                    "horarios": [],
                    "mensaje": "No hay horarios libres para esa fecha.",
                }
            )

        return JsonResponse(
            {
                "horarios": [
                    {"value": horario.strftime("%H:%M"), "label": horario.strftime("%H:%M")}
                    for horario in horarios
                ],
                "mensaje": "Solo se muestran horarios libres.",
            }
        )

    def _obtener_odontologo(self, odontologo_id):
        if not odontologo_id:
            return None

        try:
            return Odontologo.objects.filter(pk=odontologo_id, activo=True).first()
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _obtener_fecha(valor):
        if not valor:
            return None

        return parse_date(valor)

    @staticmethod
    def _obtener_duracion_minutos(valor, odontologo):
        if not valor:
            return odontologo.duracion_turno_minutos

        try:
            duracion = int(valor)
        except (TypeError, ValueError):
            return odontologo.duracion_turno_minutos

        if duracion <= 0:
            return odontologo.duracion_turno_minutos

        return duracion

    @staticmethod
    def _obtener_turno_excluido(turno_id, usuario):
        if not turno_id or not usuario.is_authenticated:
            return None

        try:
            return limitar_turnos_por_usuario(Turno.objects.all(), usuario).filter(
                pk=turno_id,
            ).first()
        except (TypeError, ValueError):
            return None


class TurnoListView(VerTurnosRequeridoMixin, ListView):
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
        queryset = limitar_turnos_por_usuario(queryset, self.request.user)
        self.filtros_form = TurnoFiltroForm(self.request.GET, usuario=self.request.user)

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


class TurnoCreateView(GestionConsultorioRequeridaMixin, CreateView):
    model = Turno
    form_class = TurnoCreateForm
    template_name = "turnos/turno_form.html"
    success_url = reverse_lazy("turnos:lista")

    def get_initial(self):
        initial = super().get_initial()
        busqueda_form = self._obtener_busqueda_form()

        if busqueda_form.is_valid():
            odontologo = busqueda_form.cleaned_data["odontologo"]
            initial["odontologo"] = odontologo
            initial["fecha"] = busqueda_form.cleaned_data["fecha"]
            initial["duracion_minutos"] = odontologo.duracion_turno_minutos

        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["titulo"] = "Nuevo turno"
        context["subtitulo"] = "Elegi odontologo y fecha para usar horarios disponibles."
        context["texto_boton"] = "Guardar turno"
        context["url_cancelar"] = reverse_lazy("turnos:lista")
        context["busqueda_form"] = self._obtener_busqueda_form()
        return context

    def form_valid(self, form):
        self.object = crear_turno_desde_formulario(form)
        messages.success(self.request, "Turno creado correctamente.")
        return redirect(self.get_success_url())

    def _obtener_busqueda_form(self):
        return TurnoHorarioBusquedaForm(self.request.GET or None, auto_id="id_busqueda_%s")


class SolicitudTurnoPublicaView(FormView):
    form_class = SolicitudTurnoPublicaForm
    template_name = "turnos/solicitud_publica_form.html"
    success_url = reverse_lazy("turnos:solicitud_publica_ok")

    def get_initial(self):
        initial = super().get_initial()
        busqueda_form = self._obtener_busqueda_form()

        if busqueda_form.is_valid():
            initial["odontologo"] = busqueda_form.cleaned_data["odontologo"]
            initial["fecha"] = busqueda_form.cleaned_data["fecha"]

        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["busqueda_form"] = self._obtener_busqueda_form()
        return context

    def form_valid(self, form):
        try:
            turno = crear_solicitud_turno_publica(form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
            return self.form_invalid(form)

        self.request.session["solicitud_turno_publica_id"] = turno.pk
        messages.success(self.request, "Solicitud de turno recibida correctamente.")
        return super().form_valid(form)

    def _obtener_busqueda_form(self):
        return SolicitudTurnoBusquedaPublicaForm(
            self.request.GET or None,
            auto_id="id_busqueda_%s",
        )


class SolicitudTurnoPublicaOkView(TemplateView):
    template_name = "turnos/solicitud_publica_ok.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        turno_id = self.request.session.get("solicitud_turno_publica_id")
        context["turno"] = (
            Turno.objects.select_related(
                "paciente",
                "odontologo",
                "odontologo__usuario",
            )
            .filter(pk=turno_id)
            .first()
        )
        return context


class GoogleCalendarOdontologoMixin(GoogleCalendarRequeridoMixin):
    def obtener_odontologo(self):
        return obtener_odontologo_del_usuario(self.request.user)


class GoogleCalendarConexionView(GoogleCalendarOdontologoMixin, TemplateView):
    template_name = "turnos/google_calendar_conexion.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        odontologo = self.obtener_odontologo()
        context["odontologo"] = odontologo
        context["conexion"] = GoogleCalendarConexion.objects.filter(
            odontologo=odontologo
        ).first()
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
            messages.error(request, f"Google no autorizo la conexion: {error_google}.")
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
            tokens = intercambiar_codigo_por_tokens(code)
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


class TurnoDetailView(VerTurnosRequeridoMixin, DetailView):
    model = Turno
    template_name = "turnos/turno_detail.html"
    context_object_name = "turno"

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .select_related(
                "paciente",
                "odontologo",
                "odontologo__usuario",
                "odontologo__google_calendar_conexion",
            )
        )
        return limitar_turnos_por_usuario(queryset, self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["puede_reintentar_sincronizacion_google_calendar"] = (
            puede_reintentar_sincronizacion_google_calendar(
                self.request.user,
                self.object,
            )
        )
        context["puede_reprogramar_turno"] = puede_reprogramar_turno(
            self.request.user,
            self.object,
        )
        context["google_calendar_ultimo_error"] = self._obtener_ultimo_error_google_calendar()
        return context

    def _obtener_ultimo_error_google_calendar(self):
        try:
            conexion = self.object.odontologo.google_calendar_conexion
        except GoogleCalendarConexion.DoesNotExist:
            return ""

        return conexion.ultimo_error_para_usuario


class TurnoReintentarSincronizacionGoogleCalendarView(VerTurnosRequeridoMixin, View):
    def post(self, request, pk):
        turno = get_object_or_404(
            limitar_turnos_por_usuario(
                Turno.objects.select_related("odontologo", "odontologo__usuario"),
                request.user,
            ),
            pk=pk,
        )

        if not puede_reintentar_sincronizacion_google_calendar(request.user, turno):
            raise PermissionDenied("No tenes permiso para reintentar esta sincronizacion.")

        resultado = reintentar_sincronizacion_google_calendar(turno)

        if resultado.realizada:
            messages.success(request, "Turno sincronizado con Google Calendar correctamente.")
        else:
            mensaje = normalizar_error_google_calendar_para_usuario(resultado.mensaje) or (
                "No se pudo sincronizar el turno."
            )
            messages.error(request, f"No se pudo sincronizar con Google Calendar: {mensaje}")

        return redirect("turnos:detalle", pk=turno.pk)


class TurnoReprogramView(VerTurnosRequeridoMixin, UpdateView):
    model = Turno
    form_class = TurnoReprogramacionForm
    template_name = "turnos/turno_form.html"

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .select_related("paciente", "odontologo", "odontologo__usuario")
        )
        return limitar_turnos_por_usuario(queryset, self.request.user)

    def get_object(self, queryset=None):
        turno = super().get_object(queryset)

        if not puede_reprogramar_turno(self.request.user, turno):
            raise PermissionDenied("No tenes permiso para reprogramar este turno.")

        return turno

    def get_success_url(self):
        return reverse("turnos:detalle", kwargs={"pk": self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["titulo"] = "Reprogramar turno"
        context["subtitulo"] = "Actualizacion de fecha, horario y duracion del turno."
        context["texto_boton"] = "Reprogramar turno"
        context["url_cancelar"] = self.get_success_url()
        context["horarios_odontologo_id"] = self.object.odontologo_id
        return context

    def form_valid(self, form):
        self.object = reprogramar_turno(self.object, form.cleaned_data)
        messages.success(self.request, "Turno reprogramado correctamente.")
        return redirect(self.get_success_url())


class TurnoUpdateView(GestionConsultorioRequeridaMixin, UpdateView):
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
        self.object = actualizar_turno_desde_formulario(form)
        messages.success(self.request, "Turno actualizado correctamente.")
        return redirect(self.get_success_url())


class TurnoConfirmView(GestionConsultorioRequeridaMixin, View):
    def post(self, request, pk):
        turno = get_object_or_404(Turno, pk=pk)
        confirmar_turno(turno)
        messages.success(request, "Turno confirmado correctamente.")
        return redirect("turnos:detalle", pk=turno.pk)


class TurnoCancelView(GestionConsultorioRequeridaMixin, View):
    def post(self, request, pk):
        turno = get_object_or_404(Turno, pk=pk)
        cancelar_turno(turno)
        messages.success(request, "Turno cancelado correctamente.")
        return redirect("turnos:detalle", pk=turno.pk)


class AgendaDiaView(VerTurnosRequeridoMixin, TemplateView):
    template_name = "turnos/agenda_dia.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filtros_form = AgendaFiltroForm(self.request.GET, usuario=self.request.user)
        fecha = timezone.localdate()
        odontologo_solicitado = None
        busqueda = ""

        if filtros_form.is_valid():
            fecha = filtros_form.cleaned_data["fecha"] or fecha
            odontologo_solicitado = filtros_form.cleaned_data["odontologo"]
            busqueda = filtros_form.cleaned_data["buscar"].strip()

        odontologo = obtener_odontologo_visible(self.request.user, odontologo_solicitado)

        context["filtros_form"] = filtros_form
        context["odontologo"] = odontologo
        context["busqueda"] = busqueda
        context["fecha"] = fecha
        context["fecha_anterior"] = fecha - timedelta(days=1)
        context["fecha_siguiente"] = fecha + timedelta(days=1)
        context["turnos"] = obtener_turnos_del_dia(fecha, odontologo, busqueda)
        context["bloques_agenda"] = obtener_bloques_agenda_del_dia(
            fecha,
            odontologo,
            busqueda=busqueda,
        )
        context["agenda_odontologos"] = obtener_agenda_diaria_por_odontologo(
            fecha,
            odontologo,
            busqueda,
        )
        context["resumen_estados"] = obtener_resumen_estados(context["turnos"])
        return context


class AgendaSemanaView(VerTurnosRequeridoMixin, TemplateView):
    template_name = "turnos/agenda_semana.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filtros_form = AgendaFiltroForm(self.request.GET, usuario=self.request.user)
        fecha_referencia = timezone.localdate()
        odontologo_solicitado = None
        busqueda = ""

        if filtros_form.is_valid():
            fecha_referencia = filtros_form.cleaned_data["fecha"] or fecha_referencia
            odontologo_solicitado = filtros_form.cleaned_data["odontologo"]
            busqueda = filtros_form.cleaned_data["buscar"].strip()

        inicio_semana = obtener_inicio_semana(fecha_referencia)
        odontologo = obtener_odontologo_visible(self.request.user, odontologo_solicitado)

        context["filtros_form"] = filtros_form
        context["odontologo"] = odontologo
        context["busqueda"] = busqueda
        context["inicio_semana"] = inicio_semana
        context["fin_semana"] = inicio_semana + timedelta(days=6)
        context["semana_anterior"] = inicio_semana - timedelta(days=7)
        context["semana_siguiente"] = inicio_semana + timedelta(days=7)
        context["dias"] = obtener_turnos_de_la_semana(
            fecha_referencia,
            odontologo,
            busqueda,
        )
        turnos_semana = [
            turno
            for dia in context["dias"]
            for turno in dia["turnos"]
        ]
        context["agenda_odontologos"] = obtener_agenda_semanal_por_odontologo(
            fecha_referencia,
            odontologo,
            busqueda,
        )
        context["resumen_estados"] = obtener_resumen_estados(turnos_semana)
        return context
