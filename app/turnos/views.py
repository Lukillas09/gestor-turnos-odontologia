from datetime import datetime, timedelta
from secrets import token_urlsafe
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_time
from django.utils.formats import date_format
from django.views import View
from django.views.generic import (
    CreateView,
    DetailView,
    FormView,
    ListView,
    TemplateView,
    UpdateView,
)

from pacientes.normalizacion import normalizar_documento
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
    ConfirmacionTurnoForm,
    DURACION_SOLICITUD_PUBLICA_MINUTOS,
    ExcepcionAgendaForm,
    RechazoSolicitudTurnoPublicaForm,
    RevisionSolicitudTurnoPublicaForm,
    RevisionYConfirmacionTurnoPublicoForm,
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
    ExcepcionAgenda,
    GoogleCalendarConexion,
    Odontologo,
    SolicitudTurnoPublica,
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
    confirmar_turno_con_duracion,
    crear_solicitud_turno_publica_resultado,
    crear_turno_desde_formulario,
    reprogramar_turno,
    reintentar_sincronizacion_google_calendar,
)
from .excepcion_permissions import (
    limitar_excepciones_por_usuario,
    puede_modificar_excepcion_agenda,
    puede_ver_excepciones_agenda,
)
from .excepciones import (
    TurnosAfectadosPorExcepcionError,
    actualizar_excepcion_agenda,
    crear_excepcion_agenda,
    desactivar_excepcion_agenda,
    obtener_excepciones_activas,
    obtener_horarios_publicos_disponibles,
    obtener_rango_reserva_publica,
    obtener_turnos_afectados_por_excepcion,
    validar_fecha_reserva_publica,
)
from .solicitudes_publicas.permissions import puede_revisar_solicitudes_publicas
from .solicitudes_publicas.proteccion import (
    IdempotenciaSolicitudPublicaInvalida,
    ProteccionSolicitudPublicaError,
    TurnstileSolicitudPublicaInvalido,
    completar_idempotencia,
    adquirir_idempotencia,
    generar_idempotency_token,
    liberar_idempotencia,
    registrar_intento_creacion_publica,
    turnstile_requerido_para_request,
)
from .solicitudes_publicas.selectors import obtener_solicitudes_publicas_para_bandeja
from .solicitudes_publicas.selectors import (
    obtener_alertas_administrativas_publicas,
    obtener_turnos_con_revision_publica_pendiente,
)
from .solicitudes_publicas.services import (
    MaximoSolicitudesPendientesError,
    rechazar_solicitud_publica_y_cancelar_turno,
    revisar_solicitud_publica,
    revisar_y_confirmar_solicitud_publica,
)


GOOGLE_CALENDAR_OAUTH_STATE_SESSION_KEY = "google_calendar_oauth_state"
SOLICITUD_PUBLICA_CONFIRMADA_SESSION_KEY = "solicitud_turno_publica_confirmada"
CAMPO_REVISION_PUBLICA_LABELS = (
    ("nombre", "Nombre"),
    ("apellido", "Apellido"),
    ("telefono", "Teléfono"),
    ("email", "Email"),
)


def construir_filas_revision_solicitud_publica(solicitud):
    paciente = solicitud.paciente
    diferencias = set((solicitud.diferencias_detectadas or {}).keys())
    filas = []

    for orden, (campo, etiqueta) in enumerate(CAMPO_REVISION_PUBLICA_LABELS, start=1):
        filas.append(
            {
                "campo": campo,
                "etiqueta": etiqueta,
                "actual": getattr(paciente, campo) or "-",
                "enviado": getattr(solicitud, f"{campo}_enviado") or "-",
                "diferente": campo in diferencias,
                "orden": orden,
            }
        )

    return sorted(filas, key=lambda fila: (not fila["diferente"], fila["orden"]))


class LandingPublicaPacientesView(TemplateView):
    template_name = "turnos/public/landing.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        confirmacion = self.request.session.pop(
            SOLICITUD_PUBLICA_CONFIRMADA_SESSION_KEY,
            None,
        )
        context["solicitud_turno_confirmada"] = bool(confirmacion)
        context["solicitud_turno_confirmada_con_email"] = False
        return context


class HorariosDisponiblesJsonView(View):
    def get(self, request):
        odontologo = self._obtener_odontologo(request.GET.get("odontologo"))
        fecha = self._obtener_fecha(request.GET.get("fecha"))

        if not odontologo or not fecha:
            return JsonResponse(
                {
                    "horarios": [],
                    "mensaje": "Elegí odontólogo y fecha para ver horarios disponibles.",
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
                "activo": fecha_actual == f"{manana:%Y-%m-%d}" and not self.request.GET.get("estado"),
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


class SolicitudTurnoPublicaDisponibilidadMixin:
    def _crear_disponibilidad_publica(self, odontologo, fecha):
        horarios = obtener_horarios_publicos_disponibles(
            odontologo=odontologo,
            fecha=fecha,
            duracion_minutos=DURACION_SOLICITUD_PUBLICA_MINUTOS,
        )

        return {
            "odontologo": odontologo,
            "fecha": fecha,
            "horarios_manana": self._crear_opciones_horarias(
                odontologo,
                fecha,
                [horario for horario in horarios if horario.hour < 13],
            ),
            "horarios_tarde": self._crear_opciones_horarias(
                odontologo,
                fecha,
                [horario for horario in horarios if horario.hour >= 13],
            ),
            "dias_cercanos": self._obtener_dias_cercanos(odontologo, fecha),
        }

    def _crear_opciones_horarias(self, odontologo, fecha, horarios):
        if not odontologo or not fecha:
            return []

        return [
            {
                "hora": horario,
                "label": horario.strftime("%H:%M"),
                "url": self._crear_url_reserva(odontologo, fecha, horario),
            }
            for horario in horarios
        ]

    def _obtener_dias_cercanos(self, odontologo, fecha):
        if not odontologo or not fecha:
            return []

        dias = []
        rango = obtener_rango_reserva_publica()

        for offset in range((rango.fecha_maxima - rango.fecha_minima).days + 1):
            dia = rango.fecha_minima + timedelta(days=offset)
            if dia > rango.fecha_maxima:
                break

            horarios = obtener_horarios_publicos_disponibles(
                odontologo=odontologo,
                fecha=dia,
                duracion_minutos=DURACION_SOLICITUD_PUBLICA_MINUTOS,
            )

            if not horarios:
                continue

            dias.append(
                {
                    "fecha": dia,
                    "cantidad": len(horarios),
                    "seleccionado": dia == fecha,
                    "url": self._crear_url_seleccion(odontologo, dia),
                }
            )

        return dias

    @staticmethod
    def _crear_url_seleccion(odontologo, fecha):
        querystring = urlencode(
            {
                "odontologo": odontologo.pk,
                "fecha": fecha.isoformat(),
            }
        )
        return f"{reverse('turnos:solicitud_publica')}?{querystring}"

    @staticmethod
    def _crear_url_reserva(odontologo, fecha, hora):
        querystring = urlencode(
            {
                "odontologo": odontologo.pk,
                "fecha": fecha.isoformat(),
                "hora_inicio": hora.strftime("%H:%M"),
            }
        )
        return f"{reverse('turnos:solicitud_publica_datos')}?{querystring}"


class SolicitudTurnoPublicaView(SolicitudTurnoPublicaDisponibilidadMixin, TemplateView):
    template_name = "turnos/solicitud_publica_seleccion.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        busqueda_form = self._obtener_busqueda_form()
        disponibilidad = {}

        if busqueda_form.is_valid():
            disponibilidad = self._crear_disponibilidad_publica(
                busqueda_form.cleaned_data["odontologo"],
                busqueda_form.cleaned_data["fecha"],
            )

        context["busqueda_form"] = busqueda_form
        context["hay_odontologos"] = Odontologo.objects.filter(activo=True).exists()
        context.update(
            {
                "odontologo": None,
                "fecha": None,
                "horarios_manana": [],
                "horarios_tarde": [],
                "dias_cercanos": [],
                **disponibilidad,
            }
        )
        return context

    def _obtener_busqueda_form(self):
        data = self.request.GET if self.request.GET else None
        return SolicitudTurnoBusquedaPublicaForm(
            data,
            initial={"fecha": obtener_rango_reserva_publica().fecha_minima},
            auto_id="id_busqueda_%s",
        )


class SolicitudTurnoPublicaHorariosView(SolicitudTurnoPublicaDisponibilidadMixin, View):
    def get(self, request):
        odontologo = self._obtener_odontologo(request.GET.get("odontologo"))
        fecha = self._obtener_fecha(request.GET.get("fecha"))

        if not request.GET.get("odontologo"):
            return JsonResponse(
                {
                    "ok": False,
                    "codigo": "sin_odontologo",
                    "mensaje": "Elegí un odontólogo para ver los horarios disponibles.",
                }
            )

        if not odontologo:
            return JsonResponse(
                {
                    "ok": False,
                    "codigo": "odontologo_invalido",
                    "mensaje": "No se encontró el odontólogo seleccionado.",
                }
            )

        if not request.GET.get("fecha"):
            return JsonResponse(
                {
                    "ok": False,
                    "codigo": "sin_fecha",
                    "mensaje": "Elegí una fecha para ver horarios disponibles.",
                    "odontologo": self._serializar_odontologo(odontologo),
                }
            )

        if not fecha:
            return JsonResponse(
                {
                    "ok": False,
                    "codigo": "fecha_invalida",
                    "mensaje": "Ingresá una fecha válida.",
                    "odontologo": self._serializar_odontologo(odontologo),
                }
            )

        try:
            validar_fecha_reserva_publica(fecha)
        except ValidationError as error:
            return JsonResponse(
                {
                    "ok": False,
                    "codigo": "fecha_fuera_de_ventana",
                    "mensaje": error.messages[0],
                    "odontologo": self._serializar_odontologo(odontologo),
                }
            )

        disponibilidad = self._crear_disponibilidad_publica(odontologo, fecha)
        horarios_manana = disponibilidad["horarios_manana"]
        horarios_tarde = disponibilidad["horarios_tarde"]

        return JsonResponse(
            {
                "ok": True,
                "mensaje": (
                    "Horarios disponibles actualizados."
                    if horarios_manana or horarios_tarde
                    else "No hay horarios disponibles para esa fecha. Probá con otro día."
                ),
                "odontologo": self._serializar_odontologo(odontologo),
                "fecha": self._serializar_fecha(fecha),
                "dias_cercanos": [
                    self._serializar_dia_cercano(dia) for dia in disponibilidad["dias_cercanos"]
                ],
                "horarios_manana": self._serializar_horarios(horarios_manana),
                "horarios_tarde": self._serializar_horarios(horarios_tarde),
            }
        )

    @staticmethod
    def _obtener_odontologo(odontologo_id):
        if not odontologo_id:
            return None

        try:
            return Odontologo.objects.select_related("usuario").filter(
                pk=odontologo_id,
                activo=True,
            ).first()
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _obtener_fecha(valor):
        if not valor:
            return None

        return parse_date(valor)

    @staticmethod
    def _serializar_odontologo(odontologo):
        return {
            "id": odontologo.pk,
            "nombre": odontologo.nombre_completo,
            "especialidad": odontologo.especialidad or "Odontología general",
            "duracion": DURACION_SOLICITUD_PUBLICA_MINUTOS,
            "matricula": odontologo.matricula,
            "celular": odontologo.celular,
            "foto_url": odontologo.foto_perfil_url,
            "foto_object_position": odontologo.foto_object_position,
            "inicial": odontologo.nombre_completo[:1],
        }

    @staticmethod
    def _serializar_fecha(fecha):
        return {
            "iso": fecha.isoformat(),
            "display": date_format(fecha, "l d/m/Y"),
        }

    @staticmethod
    def _serializar_dia_cercano(dia):
        return {
            "fecha": dia["fecha"].isoformat(),
            "label": dia["fecha"].strftime("%d/%m"),
            "cantidad": dia["cantidad"],
            "seleccionado": dia["seleccionado"],
            "url": dia["url"],
        }

    @staticmethod
    def _serializar_horarios(horarios):
        return [
            {
                "label": opcion["label"],
                "url": opcion["url"],
            }
            for opcion in horarios
        ]


class SolicitudTurnoPublicaDatosView(FormView):
    form_class = SolicitudTurnoPublicaForm
    template_name = "turnos/solicitud_publica_form.html"
    success_url = reverse_lazy("landing_publica")
    turnstile_requerido = False

    def get_initial(self):
        initial = super().get_initial()
        reserva = self._obtener_reserva_desde_get()

        if reserva:
            initial.update(reserva)

        if self.request.method == "GET":
            initial["idempotency_token"] = generar_idempotency_token(self.request)

        return initial

    def get(self, request, *args, **kwargs):
        if not self._obtener_reserva_desde_get():
            messages.error(
                request,
                "Elegí un horario disponible antes de completar tus datos.",
            )
            return redirect("turnos:solicitud_publica")

        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        reserva = self._obtener_reserva_desde_get() or self._obtener_reserva_desde_post()
        context.update(reserva or {})
        documento = self.request.POST.get("documento") if self.request.method == "POST" else ""
        context["turnstile_requerido"] = (
            self.turnstile_requerido
            if self.request.method == "POST"
            else turnstile_requerido_para_request(self.request, documento)
        )
        context["turnstile_site_key"] = settings.TURNSTILE_SITE_KEY

        if reserva:
            context["hora_fin"] = (
                datetime.combine(reserva["fecha"], reserva["hora_inicio"])
                + timedelta(minutes=DURACION_SOLICITUD_PUBLICA_MINUTOS)
            ).time()

        return context

    def post(self, request, *args, **kwargs):
        try:
            intento = registrar_intento_creacion_publica(request)
            self.turnstile_requerido = intento.requiere_turnstile
        except ProteccionSolicitudPublicaError as error:
            self.turnstile_requerido = isinstance(error, TurnstileSolicitudPublicaInvalido)
            form = self.get_form()
            form.add_error(None, error.mensaje)
            return self._form_invalid_con_estado(form, error.status_code, error.retry_after)

        if self._existe_duplicado_exacto_desde_post():
            try:
                resultado_idempotencia = adquirir_idempotencia(
                    request,
                    request.POST.get("idempotency_token"),
                )
                if resultado_idempotencia.debe_procesar:
                    completar_idempotencia(resultado_idempotencia.token_hash)
            except ProteccionSolicitudPublicaError as error:
                form = self.get_form()
                form.add_error(None, error.mensaje)
                return self._form_invalid_con_estado(form, error.status_code, error.retry_after)

            return self._redirigir_confirmacion_generica()

        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        resultado_idempotencia = None

        try:
            resultado_idempotencia = adquirir_idempotencia(
                self.request,
                form.cleaned_data.get("idempotency_token"),
            )

            if resultado_idempotencia.es_repetido:
                return self._redirigir_confirmacion_generica()

            resultado = crear_solicitud_turno_publica_resultado(form.cleaned_data)
            completar_idempotencia(resultado_idempotencia.token_hash)
        except ValidationError as error:
            if resultado_idempotencia and resultado_idempotencia.debe_procesar:
                liberar_idempotencia(resultado_idempotencia.token_hash)

            if hasattr(error, "message_dict"):
                for field, messages in error.message_dict.items():
                    form.add_error(field if field in form.fields else None, messages)
            else:
                form.add_error(None, error)
            return self.form_invalid(form)
        except MaximoSolicitudesPendientesError as error:
            if resultado_idempotencia and resultado_idempotencia.debe_procesar:
                liberar_idempotencia(resultado_idempotencia.token_hash)

            form.add_error(None, error.mensaje)
            return self._form_invalid_con_estado(
                form,
                429,
                settings.TURNOS_PUBLIC_BOOKING_DNI_WINDOW_SECONDS,
            )
        except IdempotenciaSolicitudPublicaInvalida as error:
            form.add_error(None, error.mensaje)
            return self._form_invalid_con_estado(form, error.status_code, error.retry_after)
        except ProteccionSolicitudPublicaError as error:
            form.add_error(None, error.mensaje)
            return self._form_invalid_con_estado(form, error.status_code, error.retry_after)

        return self._redirigir_confirmacion_generica(resultado)

    def _redirigir_confirmacion_generica(self, resultado=None):
        self.request.session["solicitud_turno_publica_id"] = (
            resultado.turno.pk if resultado and resultado.turno else None
        )
        self.request.session["solicitud_publica_revision_id"] = (
            str(resultado.solicitud.pk) if resultado else None
        )
        self.request.session[SOLICITUD_PUBLICA_CONFIRMADA_SESSION_KEY] = {
            "registrada": True,
        }
        return redirect(self.get_success_url())

    def _form_invalid_con_estado(self, form, status_code, retry_after=None):
        response = self.form_invalid(form)
        response.status_code = status_code

        if retry_after:
            response["Retry-After"] = str(retry_after)

        return response

    def _existe_duplicado_exacto_desde_post(self):
        documento = normalizar_documento(self.request.POST.get("documento"))
        fecha = parse_date(self.request.POST.get("fecha") or "")
        hora_inicio = parse_time(self.request.POST.get("hora_inicio") or "")
        odontologo_id = self.request.POST.get("odontologo")

        if not documento or not fecha or not hora_inicio or not odontologo_id:
            return False

        try:
            odontologo_id = int(odontologo_id)
        except (TypeError, ValueError):
            return False

        return SolicitudTurnoPublica.objects.filter(
            paciente__documento=documento,
            turno__odontologo_id=odontologo_id,
            turno__fecha=fecha,
            turno__hora_inicio=hora_inicio,
            turno__estado__in=[Turno.Estado.PENDIENTE, Turno.Estado.CONFIRMADO],
        ).exclude(
            estado_revision=SolicitudTurnoPublica.EstadoRevision.RECHAZADA,
        ).exists()

    def _obtener_reserva_desde_get(self):
        return self._obtener_reserva(self.request.GET, validar_disponibilidad=True)

    def _obtener_reserva_desde_post(self):
        return self._obtener_reserva(self.request.POST, validar_disponibilidad=False)

    @staticmethod
    def _obtener_reserva(data, validar_disponibilidad):
        odontologo_id = data.get("odontologo")
        fecha = parse_date(data.get("fecha") or "")
        hora_inicio = parse_time(data.get("hora_inicio") or "")

        if not odontologo_id or not fecha or not hora_inicio:
            return None

        if not fecha:
            return None

        try:
            validar_fecha_reserva_publica(fecha)
        except ValidationError:
            return None

        try:
            odontologo = Odontologo.objects.filter(pk=odontologo_id, activo=True).first()
        except (TypeError, ValueError):
            return None

        if not odontologo:
            return None

        if validar_disponibilidad:
            horarios = obtener_horarios_publicos_disponibles(
                odontologo=odontologo,
                fecha=fecha,
                duracion_minutos=DURACION_SOLICITUD_PUBLICA_MINUTOS,
            )

            if hora_inicio not in horarios:
                return None

        return {
            "odontologo": odontologo,
            "fecha": fecha,
            "hora_inicio": hora_inicio,
        }


class SolicitudTurnoPublicaOkView(TemplateView):
    template_name = "turnos/solicitud_publica_ok.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        turno_id = self.request.session.get("solicitud_turno_publica_id")
        solicitud_id = self.request.session.get("solicitud_publica_revision_id")
        context["turno"] = (
            Turno.objects.select_related(
                "odontologo",
                "odontologo__usuario",
            )
            .filter(pk=turno_id)
            .first()
        )
        context["solicitud"] = (
            SolicitudTurnoPublica.objects.filter(pk=solicitud_id).first()
            if solicitud_id
            else None
        )
        return context


class SolicitudTurnoPublicaListView(GestionConsultorioRequeridaMixin, ListView):
    model = SolicitudTurnoPublica
    template_name = "turnos/solicitudes_publicas/lista.html"
    context_object_name = "solicitudes"
    paginate_by = 20

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if not puede_revisar_solicitudes_publicas(request.user):
            raise PermissionDenied("No tenés permiso para revisar solicitudes públicas.")

        if obtener_alertas_administrativas_publicas().exists():
            return redirect("turnos:alertas_administrativas")

        return redirect(f"{reverse('turnos:lista')}?datos_por_revisar=on")

    def get_queryset(self):
        queryset = obtener_solicitudes_publicas_para_bandeja().order_by("-creado_en")
        estado = self.request.GET.get("estado", SolicitudTurnoPublica.EstadoRevision.PENDIENTE)

        if estado:
            queryset = queryset.filter(estado_revision=estado)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["estado_actual"] = self.request.GET.get(
            "estado",
            SolicitudTurnoPublica.EstadoRevision.PENDIENTE,
        )
        context["estados_revision"] = SolicitudTurnoPublica.EstadoRevision.choices
        context["pendientes_revision"] = SolicitudTurnoPublica.objects.filter(
            estado_revision=SolicitudTurnoPublica.EstadoRevision.PENDIENTE,
        ).count()
        return context


class AlertasAdministrativasPublicasView(GestionConsultorioRequeridaMixin, ListView):
    model = SolicitudTurnoPublica
    template_name = "turnos/solicitudes_publicas/alertas_administrativas.html"
    context_object_name = "solicitudes"
    paginate_by = 20

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if not puede_revisar_solicitudes_publicas(request.user):
            raise PermissionDenied("No tenés permiso para revisar alertas administrativas.")

        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return obtener_alertas_administrativas_publicas().order_by("-creado_en")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["total_alertas"] = self.get_queryset().count()
        return context


class SolicitudTurnoPublicaRevisionView(GestionConsultorioRequeridaMixin, FormView):
    form_class = RevisionSolicitudTurnoPublicaForm
    template_name = "turnos/solicitudes_publicas/revision.html"
    solicitud = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if not puede_revisar_solicitudes_publicas(request.user):
            raise PermissionDenied("No tenés permiso para revisar solicitudes públicas.")

        self.solicitud = get_object_or_404(
            obtener_solicitudes_publicas_para_bandeja(),
            pk=kwargs["pk"],
        )

        if self.solicitud.turno_id:
            if self.solicitud.esta_pendiente_revision:
                return redirect("turnos:confirmar", pk=self.solicitud.turno_id)

            return redirect("turnos:detalle", pk=self.solicitud.turno_id)

        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["solicitud"] = self.solicitud
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context["form"]
        filas_revision = self._construir_filas_revision()
        es_paciente_nuevo = not self.solicitud.paciente_existente
        paciente_archivado = not self.solicitud.paciente.activo
        context["solicitud"] = self.solicitud
        context["es_paciente_nuevo"] = es_paciente_nuevo
        context["paciente_archivado"] = paciente_archivado
        context["solicitud_sin_turno"] = self.solicitud.turno_id is None
        context["titulo_revision"] = (
            "Revisar paciente nuevo"
            if es_paciente_nuevo
            else "Revisar cambios informados"
        )
        context["badge_revision"] = (
            "Pendiente de validación"
            if es_paciente_nuevo
            else "Paciente existente"
        )
        if paciente_archivado:
            context["titulo_revision"] = "Revisar paciente archivado"
            context["badge_revision"] = "Paciente archivado"
        context["filas_revision"] = filas_revision
        context["filas_diferentes"] = [fila for fila in filas_revision if fila["diferente"]]
        context["filas_iguales"] = [fila for fila in filas_revision if not fila["diferente"]]
        context["campos_seleccionados"] = self._obtener_campos_seleccionados(form)
        context["accion_actual"] = self._obtener_accion_actual(form)
        context["acciones_revision"] = self._construir_acciones_revision(
            es_paciente_nuevo,
            context["accion_actual"],
            paciente_archivado,
        )
        context["boton_principal_revision"] = self._obtener_texto_boton_principal(
            context["acciones_revision"],
            context["accion_actual"],
        )
        return context

    def form_valid(self, form):
        accion = form.cleaned_data["accion"]
        try:
            revisar_solicitud_publica(
                solicitud_id=self.solicitud.id,
                usuario=self.request.user,
                accion=accion,
                campos_a_actualizar=form.cleaned_data.get("campos"),
                observaciones=form.cleaned_data.get("observaciones", ""),
            )
        except ValidationError as error:
            form.add_error(None, error)
            return self.form_invalid(form)

        if accion == "mantener_pendiente":
            messages.success(
                self.request,
                "La solicitud permanece pendiente para revisarla más adelante.",
            )
        else:
            messages.success(self.request, "Solicitud pública revisada correctamente.")
        return redirect("turnos:alertas_administrativas")

    def _construir_filas_revision(self):
        return construir_filas_revision_solicitud_publica(self.solicitud)

    @staticmethod
    def _obtener_campos_seleccionados(form):
        if "campos" not in form.fields:
            return set()

        valor = form["campos"].value() or []

        if isinstance(valor, str):
            return {valor}

        return set(valor)

    @staticmethod
    def _obtener_accion_actual(form):
        return form["accion"].value() or form.fields["accion"].initial

    @staticmethod
    def _construir_acciones_revision(es_paciente_nuevo, accion_actual, paciente_archivado=False):
        if paciente_archivado:
            acciones = [
                {
                    "valor": "mantener_pendiente",
                    "titulo": "Mantener pendiente",
                    "descripcion": "La solicitud queda para revision administrativa antes de reactivar al paciente.",
                    "boton": "Mantener pendiente",
                    "variante": "neutral",
                },
                {
                    "valor": "rechazar",
                    "titulo": "Marcar solicitud como no valida",
                    "descripcion": "La solicitud se descarta sin crear turno ni modificar el paciente.",
                    "boton": "Marcar como no valida",
                    "variante": "danger",
                },
            ]
        elif es_paciente_nuevo:
            acciones = [
                {
                    "valor": "validar_paciente",
                    "titulo": "Validar paciente",
                    "descripcion": (
                        "Los datos quedarán confirmados por recepción y el paciente "
                        "dejará de estar pendiente."
                    ),
                    "boton": "Validar paciente",
                    "variante": "success",
                },
                {
                    "valor": "mantener_pendiente",
                    "titulo": "Revisar más tarde",
                    "descripcion": "La solicitud seguirá apareciendo en la bandeja de pendientes.",
                    "boton": "Guardar para después",
                    "variante": "neutral",
                },
                {
                    "valor": "rechazar",
                    "titulo": "Marcar solicitud como no válida",
                    "descripcion": (
                        "La solicitud administrativa se marcará como rechazada. "
                        "Esta acción no cancela el turno ni elimina el paciente."
                    ),
                    "boton": "Marcar como no válida",
                    "variante": "danger",
                },
            ]
        else:
            acciones = [
                {
                    "valor": "conservar",
                    "titulo": "Conservar datos actuales",
                    "descripcion": "No se aplicarán los datos enviados al registro del paciente.",
                    "boton": "Conservar datos actuales",
                    "variante": "neutral",
                },
                {
                    "valor": "aplicar_campos",
                    "titulo": "Actualizar campos seleccionados",
                    "descripcion": "Solo se actualizarán los campos marcados en las diferencias.",
                    "boton": "Actualizar campos seleccionados",
                    "variante": "success",
                },
                {
                    "valor": "mantener_pendiente",
                    "titulo": "Revisar más tarde",
                    "descripcion": "La solicitud seguirá apareciendo en la bandeja de pendientes.",
                    "boton": "Guardar para después",
                    "variante": "neutral",
                },
                {
                    "valor": "rechazar",
                    "titulo": "Marcar solicitud como no válida",
                    "descripcion": (
                        "La solicitud administrativa se marcará como rechazada. "
                        "No cancela el turno ni elimina el paciente."
                    ),
                    "boton": "Marcar como no válida",
                    "variante": "danger",
                },
            ]

        return [
            {
                **accion,
                "seleccionada": accion["valor"] == accion_actual,
            }
            for accion in acciones
        ]

    @staticmethod
    def _obtener_texto_boton_principal(acciones, accion_actual):
        for accion in acciones:
            if accion["valor"] == accion_actual:
                return accion["boton"]

        return "Guardar revisión"


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
            solicitud_pendiente and puede_revisar_publica and self.object.estado == Turno.Estado.PENDIENTE
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

        if self._requiere_revision_publica() and not puede_revisar_solicitudes_publicas(request.user):
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

        if self._requiere_revision_publica():
            kwargs["solicitud"] = self.solicitud_publica
            kwargs["usuario"] = self.request.user

        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        duraciones_rapidas = {30, 45, 60, 90, 120}
        initial["duracion_rapida"] = (
            self.turno.duracion_minutos
            if self.turno.duracion_minutos in duraciones_rapidas
            else 30
        )
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
        context["campos_seleccionados"] = self._obtener_campos_seleccionados(
            context["form"]
        )
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
            or self.solicitud.estado_revision
            != SolicitudTurnoPublica.EstadoRevision.PENDIENTE
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
        return {
            campo: form.cleaned_data[campo]
            for campo in ExcepcionAgendaForm.Meta.fields
        }


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


def construir_excepciones_agenda_contexto(fecha_desde, fecha_hasta, odontologo, usuario):
    excepciones = list(obtener_excepciones_activas(odontologo, fecha_desde, fecha_hasta))
    items = []

    for excepcion in excepciones:
        turnos_afectados = obtener_turnos_afectados_por_excepcion(excepcion)

        if odontologo:
            turnos_afectados = [
                turno for turno in turnos_afectados if turno.odontologo_id == odontologo.pk
            ]

        items.append(
            {
                "excepcion": excepcion,
                "turnos_afectados": turnos_afectados,
            }
        )

    return items


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
        context["excepciones_agenda"] = construir_excepciones_agenda_contexto(
            fecha,
            fecha,
            odontologo,
            self.request.user,
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
        context["excepciones_agenda"] = construir_excepciones_agenda_contexto(
            inicio_semana,
            context["fin_semana"],
            odontologo,
            self.request.user,
        )
        context["resumen_estados"] = obtener_resumen_estados(turnos_semana)
        return context
