from datetime import timedelta
from importlib import import_module

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views import View
from django.views.generic import CreateView, DetailView, FormView, ListView, UpdateView

from usuarios.mixins import GestionConsultorioRequeridaMixin, VerTurnosRequeridoMixin
from usuarios.roles import (
    limitar_turnos_por_usuario,
    obtener_odontologo_del_usuario,
    puede_configurar_disponibilidad,
    puede_gestionar_consultorio,
    puede_reintentar_sincronizacion_google_calendar,
    puede_reprogramar_turno,
    puede_revisar_solicitudes_publicas,
)

from ..forms import (
    ConfirmacionTurnoForm,
    RechazoSolicitudTurnoPublicaForm,
    RevisionYConfirmacionTurnoPublicoForm,
    TurnoCreateForm,
    TurnoFiltroForm,
    TurnoForm,
    TurnoHorarioBusquedaForm,
    TurnoReprogramacionForm,
)
from ..models import (
    GoogleCalendarConexion,
    Odontologo,
    SolicitudTurnoPublica,
    TipoTurnoOdontologo,
    Turno,
    normalizar_error_google_calendar_para_usuario,
)
from ..selectors import obtener_horarios_disponibles
from ..services import (
    actualizar_turno_desde_formulario,
    cancelar_turno,
    confirmar_turno_con_duracion,
    crear_turno_desde_formulario,
    reprogramar_turno,
)
from ..solicitudes_publicas.selectors import obtener_turnos_con_revision_publica_pendiente
from ..solicitudes_publicas.services import (
    rechazar_solicitud_publica_y_cancelar_turno,
    revisar_y_confirmar_solicitud_publica,
)
from .helpers import construir_filas_revision_solicitud_publica


class HorariosDisponiblesJsonView(VerTurnosRequeridoMixin, View):
    def get(self, request):
        odontologo = self._obtener_odontologo(
            request.GET.get("odontologo"),
            request.user,
        )
        fecha = self._obtener_fecha(request.GET.get("fecha"))

        if not odontologo or not fecha:
            return JsonResponse(
                {
                    "horarios": [],
                    "mensaje": "Elegí odontólogo y fecha para ver horarios disponibles.",
                }
            )

        configuracion_tipo = self._obtener_configuracion_tipo(
            odontologo,
            request.GET.get("tipo_turno"),
        )
        duracion_minutos = (
            configuracion_tipo.duracion_bloqueada_minutos
            if configuracion_tipo
            else self._obtener_duracion_minutos(
                request.GET.get("duracion_minutos"),
                odontologo,
            )
        )
        turno_excluido = self._obtener_turno_excluido(
            request.GET.get("turno_id"),
            request.user,
        )
        if turno_excluido and turno_excluido.odontologo_id != odontologo.pk:
            raise PermissionDenied("No tenés permiso para consultar ese turno.")
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
                    **self._serializar_duracion_configurada(configuracion_tipo),
                }
            )

        return JsonResponse(
            {
                "horarios": [
                    {"value": horario.strftime("%H:%M"), "label": horario.strftime("%H:%M")}
                    for horario in horarios
                ],
                "mensaje": "Solo se muestran horarios libres.",
                **self._serializar_duracion_configurada(configuracion_tipo),
            }
        )

    @staticmethod
    def _obtener_configuracion_tipo(odontologo, tipo_turno_id):
        if not tipo_turno_id:
            return None
        try:
            return TipoTurnoOdontologo.objects.filter(
                odontologo=odontologo,
                tipo_turno_id=tipo_turno_id,
                activo=True,
                tipo_turno__activo=True,
            ).first()
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _serializar_duracion_configurada(configuracion_tipo):
        if not configuracion_tipo:
            return {}
        return {
            "duracion_sugerida": configuracion_tipo.duracion_bloqueada_minutos,
            "duracion_atencion": configuracion_tipo.duracion_atencion_minutos,
        }

    def _obtener_odontologo(self, odontologo_id, usuario):
        if not odontologo_id:
            return None

        try:
            odontologo = Odontologo.objects.filter(pk=odontologo_id, activo=True).first()
        except (TypeError, ValueError):
            return None

        if not odontologo:
            return None

        acceso_global = puede_gestionar_consultorio(usuario) or puede_configurar_disponibilidad(
            usuario
        )
        odontologo_usuario = obtener_odontologo_del_usuario(usuario)
        if not acceso_global and (
            odontologo_usuario is None or odontologo_usuario.pk != odontologo.pk
        ):
            raise PermissionDenied("No tenés permiso para consultar esa agenda.")

        return odontologo

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
            turno = Turno.objects.filter(pk=turno_id).first()
        except (TypeError, ValueError):
            return None

        if turno and not puede_reprogramar_turno(usuario, turno):
            raise PermissionDenied("No tenés permiso para consultar ese turno.")

        return turno


class TurnoListView(VerTurnosRequeridoMixin, ListView):
    model = Turno
    template_name = "turnos/turno_list.html"
    context_object_name = "turnos"
    paginate_by = 10

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .select_related(
                "paciente",
                "odontologo",
                "odontologo__usuario",
                "solicitud_publica",
                "solicitud_publica__revisada_por",
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

            if filtros["datos_por_revisar"]:
                queryset = obtener_turnos_con_revision_publica_pendiente(queryset)

        self.turnos_filtrados = queryset
        self.hay_filtros_activos = any(
            self.request.GET.get(campo)
            for campo in ("fecha", "estado", "odontologo", "datos_por_revisar")
        )
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query_params = self.request.GET.copy()
        query_params.pop("page", None)

        context["filtros_form"] = self.filtros_form
        context["filtros_querystring"] = query_params.urlencode()
        context["resumen_turnos"] = self._obtener_resumen_turnos(self.turnos_filtrados)
        context["hay_filtros_activos"] = self.hay_filtros_activos
        context["accesos_rapidos_turnos"] = self._obtener_accesos_rapidos()
        context["mensaje_sin_turnos"] = (
            "No tenés turnos asociados a tu agenda."
            if obtener_odontologo_del_usuario(self.request.user)
            else "No hay turnos para los filtros seleccionados."
        )
        return context

    @staticmethod
    def _obtener_resumen_turnos(queryset):
        conteos = {
            item["estado"]: item["cantidad"]
            for item in queryset.order_by().values("estado").annotate(cantidad=Count("id"))
        }

        return {
            "total": sum(conteos.values()),
            Turno.Estado.PENDIENTE: conteos.get(Turno.Estado.PENDIENTE, 0),
            Turno.Estado.CONFIRMADO: conteos.get(Turno.Estado.CONFIRMADO, 0),
            Turno.Estado.CANCELADO: conteos.get(Turno.Estado.CANCELADO, 0),
        }

    def _obtener_accesos_rapidos(self):
        hoy = timezone.localdate()
        manana = hoy + timedelta(days=1)
        fecha_actual = self.request.GET.get("fecha", "")

        return [
            {
                "label": "Hoy",
                "url": f"{reverse('turnos:lista')}?fecha={hoy:%Y-%m-%d}",
                "activo": fecha_actual == f"{hoy:%Y-%m-%d}" and not self.request.GET.get("estado"),
            },
            {
                "label": "Mañana",
                "url": f"{reverse('turnos:lista')}?fecha={manana:%Y-%m-%d}",
                "activo": fecha_actual == f"{manana:%Y-%m-%d}"
                and not self.request.GET.get("estado"),
            },
            {
                "label": "Esta semana",
                "url": reverse("turnos:agenda_semana"),
                "activo": False,
            },
            {
                "label": "Datos por revisar",
                "url": f"{reverse('turnos:lista')}?datos_por_revisar=on",
                "activo": bool(self.request.GET.get("datos_por_revisar")),
            },
            {
                "label": "Todos",
                "url": reverse("turnos:lista"),
                "activo": not self.hay_filtros_activos,
            },
        ]


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
        context["subtitulo"] = "Elegí odontólogo y fecha para usar horarios disponibles."
        context["texto_boton"] = "Guardar turno"
        context["url_cancelar"] = reverse_lazy("turnos:lista")
        context["busqueda_form"] = self._obtener_busqueda_form()
        return context

    def form_valid(self, form):
        try:
            self.object = crear_turno_desde_formulario(form, usuario=self.request.user)
        except ValidationError as error:
            form.add_error(None, error)
            return self.form_invalid(form)

        messages.success(self.request, "Turno creado correctamente.")
        return redirect(self.get_success_url())

    def _obtener_busqueda_form(self):
        return TurnoHorarioBusquedaForm(self.request.GET or None, auto_id="id_busqueda_%s")


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
                "solicitud_publica",
                "solicitud_publica__revisada_por",
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
        solicitud_publica = getattr(self.object, "solicitud_publica", None)
        solicitud_pendiente = (
            solicitud_publica is not None
            and solicitud_publica.estado_revision == SolicitudTurnoPublica.EstadoRevision.PENDIENTE
        )
        puede_revisar_publica = puede_revisar_solicitudes_publicas(self.request.user)
        context["solicitud_publica"] = solicitud_publica
        context["filas_revision_publica"] = (
            construir_filas_revision_solicitud_publica(solicitud_publica)
            if solicitud_publica
            else []
        )
        context["filas_diferentes_publicas"] = [
            fila for fila in context["filas_revision_publica"] if fila["diferente"]
        ]
        context["puede_revisar_solicitud_publica"] = puede_revisar_publica
        context["requiere_revision_publica"] = solicitud_pendiente
        context["puede_confirmar_turno"] = (
            self.object.estado == Turno.Estado.PENDIENTE
            and context["puede_reprogramar_turno"]
            and (not solicitud_pendiente or puede_revisar_publica)
        )
        context["confirmacion_requiere_revision_publica"] = (
            context["puede_confirmar_turno"] and solicitud_pendiente
        )
        context["puede_rechazar_solicitud_publica"] = (
            solicitud_pendiente
            and puede_revisar_publica
            and self.object.estado == Turno.Estado.PENDIENTE
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
            raise PermissionDenied("No tenés permiso para reintentar esta sincronización.")

        views_publicas = import_module("turnos.views")
        resultado = views_publicas.reintentar_sincronizacion_google_calendar(turno)

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
            super().get_queryset().select_related("paciente", "odontologo", "odontologo__usuario")
        )
        return limitar_turnos_por_usuario(queryset, self.request.user)

    def get_object(self, queryset=None):
        turno = super().get_object(queryset)

        if not puede_reprogramar_turno(self.request.user, turno):
            raise PermissionDenied("No tenés permiso para reprogramar este turno.")

        return turno

    def get_success_url(self):
        return reverse("turnos:detalle", kwargs={"pk": self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["titulo"] = "Reprogramar turno"
        context["subtitulo"] = "Actualización de fecha, horario y duración del turno."
        context["texto_boton"] = "Reprogramar turno"
        context["url_cancelar"] = self.get_success_url()
        context["horarios_odontologo_id"] = self.object.odontologo_id
        return context

    def form_valid(self, form):
        try:
            self.object = reprogramar_turno(self.object, form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
            return self.form_invalid(form)

        messages.success(self.request, "Turno reprogramado correctamente.")
        return redirect(self.get_success_url())


class TurnoUpdateView(GestionConsultorioRequeridaMixin, UpdateView):
    model = Turno
    form_class = TurnoForm
    template_name = "turnos/turno_form.html"

    def get_queryset(self):
        return limitar_turnos_por_usuario(
            super().get_queryset(),
            self.request.user,
        ).select_related("paciente", "odontologo", "odontologo__usuario")

    def get_success_url(self):
        return reverse("turnos:detalle", kwargs={"pk": self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["titulo"] = "Editar turno"
        context["subtitulo"] = "Actualización de paciente, odontólogo, horario y estado."
        context["texto_boton"] = "Guardar cambios"
        context["url_cancelar"] = self.get_success_url()
        return context

    def form_valid(self, form):
        try:
            self.object = actualizar_turno_desde_formulario(form, usuario=self.request.user)
        except ValidationError as error:
            form.add_error(None, error)
            return self.form_invalid(form)

        messages.success(self.request, "Turno actualizado correctamente.")
        return redirect(self.get_success_url())


class TurnoConfirmView(VerTurnosRequeridoMixin, FormView):
    form_class = ConfirmacionTurnoForm
    template_name = "turnos/turno_confirm.html"
    turno = None
    resultado = None
    solicitud_publica = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated or not self.test_func():
            return self.handle_no_permission()

        self.turno = get_object_or_404(
            limitar_turnos_por_usuario(
                Turno.objects.select_related(
                    "paciente",
                    "odontologo",
                    "odontologo__usuario",
                    "solicitud_publica",
                    "solicitud_publica__revisada_por",
                ),
                request.user,
            ),
            pk=kwargs["pk"],
        )

        if not puede_reprogramar_turno(request.user, self.turno):
            raise PermissionDenied("No tenés permiso para confirmar este turno.")

        self.solicitud_publica = getattr(self.turno, "solicitud_publica", None)

        if self._requiere_revision_publica() and not puede_revisar_solicitudes_publicas(
            request.user
        ):
            messages.warning(
                request,
                "Este turno requiere revisión administrativa antes de confirmarse.",
            )
            return redirect("turnos:detalle", pk=self.turno.pk)

        return super().dispatch(request, *args, **kwargs)

    def get_form_class(self):
        if self._requiere_revision_publica():
            return RevisionYConfirmacionTurnoPublicoForm

        return ConfirmacionTurnoForm

    def get_template_names(self):
        if self._requiere_revision_publica():
            return ["turnos/turno_revision_confirm.html"]

        return [self.template_name]

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["duracion_original"] = self.turno.duracion_minutos
        kwargs["requiere_confirmacion_cambio"] = bool(self.turno.tipo_turno_id)

        if self._requiere_revision_publica():
            kwargs["solicitud"] = self.solicitud_publica
            kwargs["usuario"] = self.request.user

        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        duraciones_rapidas = {30, 45, 60, 90, 120}
        if self.turno.duracion_minutos in duraciones_rapidas:
            initial["duracion_rapida"] = self.turno.duracion_minutos
        else:
            initial["duracion_rapida"] = ""
            initial["duracion_personalizada"] = self.turno.duracion_minutos
        return initial

    def get_success_url(self):
        return reverse("turnos:detalle", kwargs={"pk": self.turno.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["turno"] = self.turno
        context["solicitud"] = self.solicitud_publica
        context["filas_revision"] = (
            construir_filas_revision_solicitud_publica(self.solicitud_publica)
            if self.solicitud_publica
            else []
        )
        context["filas_diferentes"] = [
            fila for fila in context["filas_revision"] if fila["diferente"]
        ]
        context["campos_seleccionados"] = self._obtener_campos_seleccionados(context["form"])
        context["conflicto"] = self.resultado.conflicto if self.resultado else None
        context["mensaje_conflicto"] = self.resultado.mensaje if self.resultado else ""
        return context

    def form_valid(self, form):
        if self._requiere_revision_publica():
            try:
                self.turno, self.solicitud_publica = revisar_y_confirmar_solicitud_publica(
                    solicitud_id=self.solicitud_publica.id,
                    usuario=self.request.user,
                    accion=form.cleaned_data["accion_revision"],
                    campos_a_actualizar=form.cleaned_data.get("campos"),
                    observaciones=form.cleaned_data.get("observaciones", ""),
                    duracion_minutos=form.cleaned_data["duracion_minutos"],
                )
            except ValidationError as error:
                form.add_error(None, error)
                return self.form_invalid(form)

            messages.success(
                self.request,
                "Solicitud revisada y turno confirmado correctamente.",
            )
            return redirect(self.get_success_url())

        self.resultado = confirmar_turno_con_duracion(
            self.turno,
            form.cleaned_data["duracion_minutos"],
        )

        if self.resultado.confirmado:
            messages.success(self.request, self.resultado.mensaje)
            return redirect(self.get_success_url())

        form.add_error(None, self.resultado.mensaje)
        return self.form_invalid(form)

    def _requiere_revision_publica(self):
        return (
            self.solicitud_publica is not None
            and self.solicitud_publica.estado_revision
            == SolicitudTurnoPublica.EstadoRevision.PENDIENTE
        )

    @staticmethod
    def _obtener_campos_seleccionados(form):
        if "campos" not in form.fields:
            return set()

        valor = form["campos"].value() or []

        if isinstance(valor, str):
            return {valor}

        return set(valor)


class TurnoSolicitudPublicaRechazarView(VerTurnosRequeridoMixin, FormView):
    form_class = RechazoSolicitudTurnoPublicaForm
    template_name = "turnos/turno_solicitud_rechazar.html"
    turno = None
    solicitud = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated or not self.test_func():
            return self.handle_no_permission()

        self.turno = get_object_or_404(
            limitar_turnos_por_usuario(
                Turno.objects.select_related(
                    "paciente",
                    "odontologo",
                    "odontologo__usuario",
                    "solicitud_publica",
                ),
                request.user,
            ),
            pk=kwargs["pk"],
        )
        self.solicitud = getattr(self.turno, "solicitud_publica", None)

        if (
            self.solicitud is None
            or self.solicitud.estado_revision != SolicitudTurnoPublica.EstadoRevision.PENDIENTE
        ):
            messages.warning(request, "Este turno no tiene una solicitud pendiente para rechazar.")
            return redirect("turnos:detalle", pk=self.turno.pk)

        if not puede_revisar_solicitudes_publicas(request.user):
            raise PermissionDenied("No tenés permiso para rechazar esta solicitud.")

        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        return reverse("turnos:detalle", kwargs={"pk": self.turno.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["turno"] = self.turno
        context["solicitud"] = self.solicitud
        context["filas_revision"] = construir_filas_revision_solicitud_publica(self.solicitud)
        return context

    def form_valid(self, form):
        try:
            rechazar_solicitud_publica_y_cancelar_turno(
                solicitud_id=self.solicitud.id,
                usuario=self.request.user,
                motivo=form.cleaned_data["motivo"],
            )
        except ValidationError as error:
            form.add_error(None, error)
            return self.form_invalid(form)

        messages.success(self.request, "Solicitud rechazada y turno cancelado correctamente.")
        return redirect(self.get_success_url())


class TurnoCancelView(GestionConsultorioRequeridaMixin, View):
    def post(self, request, pk):
        turno = get_object_or_404(
            limitar_turnos_por_usuario(
                Turno.objects.select_related("solicitud_publica"),
                request.user,
            ),
            pk=pk,
        )
        cancelar_turno(
            turno,
            motivo_cancelacion_paciente="Turno cancelado desde el panel interno.",
            usuario=request.user,
        )
        messages.success(request, "Turno cancelado correctamente.")
        return redirect("turnos:detalle", pk=turno.pk)
