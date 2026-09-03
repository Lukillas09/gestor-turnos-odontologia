import logging
from datetime import datetime, timedelta
from time import perf_counter
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_time
from django.utils.formats import date_format
from django.views import View
from django.views.generic import FormView, TemplateView

from ..excepciones import (
    obtener_excepciones_activas,
    obtener_horarios_publicos_disponibles,
    obtener_rango_reserva_publica,
    validar_fecha_reserva_publica,
)
from ..forms import (
    DURACION_SOLICITUD_PUBLICA_MINUTOS,
    SolicitudTurnoBusquedaPublicaForm,
    SolicitudTurnoPublicaForm,
)
from ..mixins import PublicShellMixin
from ..models import DisponibilidadOdontologo, Odontologo, SolicitudTurnoPublica, Turno
from ..services import crear_solicitud_turno_publica_resultado
from ..smart_scheduling import buscar_candidato, calcular_horarios_inteligentes
from ..smart_scheduling_cache import obtener_horarios_inteligentes_cacheados
from ..solicitudes_publicas.proteccion import (
    IdempotenciaSolicitudPublicaInvalida,
    ProteccionSolicitudPublicaError,
    TurnstileSolicitudPublicaInvalido,
    adquirir_idempotencia,
    completar_idempotencia,
    generar_idempotency_token,
    liberar_idempotencia,
    registrar_intento_creacion_publica,
    turnstile_requerido_para_request,
)
from ..solicitudes_publicas.services import (
    MaximoSolicitudesPendientesError,
    obtener_solicitud_duplicada_exacta,
)
from ..tipos_turno import configuraciones_tipos_publicos, obtener_configuracion_tipo_publica

SOLICITUD_PUBLICA_CONFIRMADA_SESSION_KEY = "solicitud_turno_publica_confirmada"
PUBLIC_BOOKING_NEARBY_DAYS_LIMIT_DEFAULT = 14
PUBLIC_BOOKING_HORARIOS_CACHE_SECONDS_DEFAULT = 0

logger = logging.getLogger(__name__)


def obtener_horarios_publicos_disponibles_cacheados(
    *,
    odontologo,
    fecha,
    duracion_minutos,
    intervalo_minutos=None,
    ahora=None,
):
    ttl = _obtener_entero_configurado(
        "TURNOS_PUBLIC_BOOKING_HORARIOS_CACHE_SECONDS",
        PUBLIC_BOOKING_HORARIOS_CACHE_SECONDS_DEFAULT,
    )

    if ttl <= 0:
        return (
            obtener_horarios_publicos_disponibles(
                odontologo=odontologo,
                fecha=fecha,
                duracion_minutos=duracion_minutos,
                intervalo_minutos=intervalo_minutos,
                ahora=ahora,
            ),
            False,
        )

    momento = timezone.localtime(ahora or timezone.now())
    bucket = int(momento.timestamp() // ttl)
    cache_key = (
        "turnos:public_booking:horarios:v1:"
        f"{odontologo.pk}:{fecha.isoformat()}:{duracion_minutos}:{intervalo_minutos or ''}:{bucket}"
    )

    try:
        cached = cache.get(cache_key)
    except Exception as error:
        _registrar_warning_cache("get", error)
        cached = None

    if cached is not None:
        return [parse_time(valor) for valor in cached if parse_time(valor)], True

    horarios = obtener_horarios_publicos_disponibles(
        odontologo=odontologo,
        fecha=fecha,
        duracion_minutos=duracion_minutos,
        intervalo_minutos=intervalo_minutos,
        ahora=ahora,
    )

    try:
        cache.set(cache_key, [horario.strftime("%H:%M:%S") for horario in horarios], ttl)
    except Exception as error:
        _registrar_warning_cache("set", error)

    return horarios, False


def _registrar_warning_cache(etapa, error):
    logger.warning(
        "No se pudo usar cache de horarios publicos. etapa=%s cache=%s error_type=%s",
        etapa,
        cache.__class__.__name__,
        error.__class__.__name__,
    )


def _obtener_entero_configurado(nombre, default):
    valor = getattr(settings, nombre, default)

    try:
        return max(0, int(valor))
    except (TypeError, ValueError):
        return default


class LandingPublicaPacientesView(PublicShellMixin, TemplateView):
    template_name = "turnos/public/landing.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        confirmacion = self.request.session.pop(
            SOLICITUD_PUBLICA_CONFIRMADA_SESSION_KEY,
            None,
        )
        context["solicitud_turno_confirmada"] = bool(confirmacion)
        context["solicitud_turno_confirmada_con_email"] = False
        context["odontologos_publicos"] = Odontologo.objects.filter(activo=True).select_related(
            "usuario"
        )
        context["fecha_minima_reserva_publica"] = obtener_rango_reserva_publica().fecha_minima
        return context


class SolicitudTurnoPublicaDisponibilidadMixin:
    def _crear_disponibilidad_publica(self, odontologo, fecha, configuracion_tipo=None):
        if settings.TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED:
            return self._crear_disponibilidad_inteligente(
                odontologo,
                fecha,
                configuracion_tipo,
            )

        horarios, cache_hit = obtener_horarios_publicos_disponibles_cacheados(
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
            "cache_hit": cache_hit,
        }

    def _crear_disponibilidad_inteligente(self, odontologo, fecha, configuracion_tipo):
        if not configuracion_tipo:
            return {
                "odontologo": odontologo,
                "fecha": fecha,
                "configuracion_tipo": None,
                "horarios_recomendados_manana": [],
                "horarios_recomendados_tarde": [],
                "horarios_alternativos_manana": [],
                "horarios_alternativos_tarde": [],
                "dias_cercanos": [],
                "cache_hit": False,
                "resultado_inteligente": None,
            }

        resultado, cache_hit = obtener_horarios_inteligentes_cacheados(
            configuracion_tipo=configuracion_tipo,
            fecha=fecha,
        )
        return {
            "odontologo": odontologo,
            "fecha": fecha,
            "configuracion_tipo": configuracion_tipo,
            "tipo_turno": configuracion_tipo.tipo_turno,
            "duracion_atencion_minutos": configuracion_tipo.duracion_atencion_minutos,
            "horarios_recomendados_manana": self._crear_opciones_candidatos(
                configuracion_tipo,
                fecha,
                [
                    candidato
                    for candidato in resultado.recomendados
                    if candidato.hora_inicio.hour < 13
                ],
            ),
            "horarios_recomendados_tarde": self._crear_opciones_candidatos(
                configuracion_tipo,
                fecha,
                [
                    candidato
                    for candidato in resultado.recomendados
                    if candidato.hora_inicio.hour >= 13
                ],
            ),
            "horarios_alternativos_manana": self._crear_opciones_candidatos(
                configuracion_tipo,
                fecha,
                [
                    candidato
                    for candidato in resultado.alternativos
                    if candidato.hora_inicio.hour < 13
                ],
            ),
            "horarios_alternativos_tarde": self._crear_opciones_candidatos(
                configuracion_tipo,
                fecha,
                [
                    candidato
                    for candidato in resultado.alternativos
                    if candidato.hora_inicio.hour >= 13
                ],
            ),
            "dias_cercanos": self._obtener_dias_cercanos(
                odontologo,
                fecha,
                configuracion_tipo,
            ),
            "cache_hit": cache_hit,
            "resultado_inteligente": resultado,
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

    def _crear_opciones_candidatos(self, configuracion_tipo, fecha, candidatos):
        return [
            {
                "hora": candidato.hora_inicio,
                "label": candidato.hora_inicio.strftime("%H:%M"),
                "url": self._crear_url_reserva(
                    configuracion_tipo.odontologo,
                    fecha,
                    candidato.hora_inicio,
                    configuracion_tipo=configuracion_tipo,
                    clasificacion=candidato.clasificacion,
                ),
                "clasificacion": candidato.clasificacion,
            }
            for candidato in candidatos
        ]

    def _obtener_dias_cercanos(self, odontologo, fecha, configuracion_tipo=None):
        if not odontologo or not fecha:
            return []

        rango = obtener_rango_reserva_publica()
        limite = _obtener_entero_configurado(
            "TURNOS_PUBLIC_BOOKING_NEARBY_DAYS_LIMIT",
            PUBLIC_BOOKING_NEARBY_DAYS_LIMIT_DEFAULT,
        )
        limite = max(1, limite)
        candidatos = self._obtener_fechas_candidatas_dias_cercanos(rango, fecha, limite)

        if not candidatos:
            return []

        dias_semana_con_disponibilidad = set(
            DisponibilidadOdontologo.objects.filter(
                odontologo=odontologo,
                activo=True,
                dia_semana__in={dia.weekday() for dia in candidatos},
            ).values_list("dia_semana", flat=True)
        )
        excepciones_todo_el_dia = list(
            obtener_excepciones_activas(
                odontologo,
                min(candidatos),
                max(candidatos),
            ).filter(todo_el_dia=True)
        )
        dias = []

        for dia in candidatos:
            if dia.weekday() not in dias_semana_con_disponibilidad:
                continue

            if self._dia_bloqueado_por_excepcion_todo_el_dia(dia, excepciones_todo_el_dia):
                continue

            dias.append(
                {
                    "fecha": dia,
                    "cantidad": None,
                    "seleccionado": dia == fecha,
                    "url": self._crear_url_seleccion(odontologo, dia, configuracion_tipo),
                }
            )

        return dias

    @staticmethod
    def _obtener_fechas_candidatas_dias_cercanos(rango, fecha_seleccionada, limite):
        candidatos = [
            rango.fecha_minima + timedelta(days=offset)
            for offset in range(min(limite, (rango.fecha_maxima - rango.fecha_minima).days + 1))
        ]

        if (
            rango.fecha_minima <= fecha_seleccionada <= rango.fecha_maxima
            and fecha_seleccionada not in candidatos
        ):
            candidatos.append(fecha_seleccionada)

        return sorted(candidatos)

    @staticmethod
    def _dia_bloqueado_por_excepcion_todo_el_dia(dia, excepciones):
        return any(
            excepcion.fecha_desde <= dia <= excepcion.fecha_hasta for excepcion in excepciones
        )

    @staticmethod
    def _crear_url_seleccion(odontologo, fecha, configuracion_tipo=None):
        parametros = {
            "odontologo": odontologo.pk,
            "fecha": fecha.isoformat(),
        }
        if configuracion_tipo:
            parametros["tipo_turno"] = configuracion_tipo.tipo_turno_id
        querystring = urlencode(parametros)
        return f"{reverse('turnos:solicitud_publica')}?{querystring}"

    @staticmethod
    def _crear_url_reserva(
        odontologo,
        fecha,
        hora,
        configuracion_tipo=None,
        clasificacion="",
    ):
        parametros = {
            "odontologo": odontologo.pk,
            "fecha": fecha.isoformat(),
            "hora_inicio": hora.strftime("%H:%M"),
        }
        if configuracion_tipo:
            parametros["tipo_turno"] = configuracion_tipo.tipo_turno_id
            parametros["clasificacion"] = clasificacion
        querystring = urlencode(parametros)
        return f"{reverse('turnos:solicitud_publica_datos')}?{querystring}"


class SolicitudTurnoPublicaView(
    PublicShellMixin,
    SolicitudTurnoPublicaDisponibilidadMixin,
    TemplateView,
):
    template_name = "turnos/solicitud_publica_seleccion.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        busqueda_form = self._obtener_busqueda_form()
        disponibilidad = {}

        if busqueda_form.is_valid():
            configuracion_tipo = None
            if settings.TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED:
                configuracion_tipo = obtener_configuracion_tipo_publica(
                    busqueda_form.cleaned_data["odontologo"],
                    busqueda_form.cleaned_data["tipo_turno"],
                )
            disponibilidad = self._crear_disponibilidad_publica(
                busqueda_form.cleaned_data["odontologo"],
                busqueda_form.cleaned_data["fecha"],
                configuracion_tipo,
            )

        context["busqueda_form"] = busqueda_form
        context["hay_odontologos"] = Odontologo.objects.filter(activo=True).exists()
        context["smart_scheduling_enabled"] = settings.TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED
        context["configuraciones_tipos_iniciales"] = (
            list(configuraciones_tipos_publicos(disponibilidad.get("odontologo")))
            if settings.TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED and disponibilidad.get("odontologo")
            else []
        )
        context.update(
            {
                "odontologo": None,
                "tipo_turno": None,
                "configuracion_tipo": None,
                "fecha": None,
                "horarios_manana": [],
                "horarios_tarde": [],
                "horarios_recomendados_manana": [],
                "horarios_recomendados_tarde": [],
                "horarios_alternativos_manana": [],
                "horarios_alternativos_tarde": [],
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


class SolicitudTurnoPublicaTiposView(View):
    http_method_names = ["get"]

    def get(self, request):
        if not settings.TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED:
            return JsonResponse(
                {"ok": False, "tipos": [], "codigo": "feature_disabled"},
                status=404,
            )

        odontologo = SolicitudTurnoPublicaHorariosView._obtener_odontologo(
            request.GET.get("odontologo")
        )
        if not odontologo:
            return JsonResponse(
                {
                    "ok": False,
                    "tipos": [],
                    "codigo": "odontologo_invalido",
                    "mensaje": "No se encontró el profesional seleccionado.",
                }
            )

        configuraciones = list(configuraciones_tipos_publicos(odontologo))
        return JsonResponse(
            {
                "ok": True,
                "odontologo": {
                    "id": odontologo.pk,
                    "nombre": odontologo.nombre_completo,
                },
                "tipos": [
                    {
                        "configuracion_id": configuracion.pk,
                        "tipo_turno_id": configuracion.tipo_turno_id,
                        "nombre": configuracion.tipo_turno.nombre,
                        "descripcion": configuracion.tipo_turno.descripcion_publica,
                        "icono": configuracion.tipo_turno.icono or "calendar",
                        "duracion_aproximada": configuracion.duracion_atencion_minutos,
                    }
                    for configuracion in configuraciones
                ],
                "mensaje": (
                    "Elegí el motivo de la visita."
                    if configuraciones
                    else (
                        "Este profesional no tiene turnos disponibles para reserva online "
                        "en este momento."
                    )
                ),
            }
        )


class SolicitudTurnoPublicaHorariosView(SolicitudTurnoPublicaDisponibilidadMixin, View):
    def get(self, request):
        inicio = perf_counter()
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

        configuracion_tipo = None
        if settings.TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED:
            tipo_turno_id = request.GET.get("tipo_turno")
            if not tipo_turno_id:
                return JsonResponse(
                    {
                        "ok": False,
                        "codigo": "sin_tipo_turno",
                        "mensaje": "Elegí el motivo de la visita para ver horarios.",
                        "odontologo": self._serializar_odontologo(odontologo),
                    }
                )
            configuracion_tipo = obtener_configuracion_tipo_publica(
                odontologo,
                tipo_turno_id,
            )
            if not configuracion_tipo:
                return JsonResponse(
                    {
                        "ok": False,
                        "codigo": "tipo_turno_invalido",
                        "mensaje": "El motivo elegido ya no está disponible para este profesional.",
                        "odontologo": self._serializar_odontologo(odontologo),
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

        try:
            disponibilidad = self._crear_disponibilidad_publica(
                odontologo,
                fecha,
                configuracion_tipo,
            )
        except Exception:
            duracion_ms = round((perf_counter() - inicio) * 1000)
            logger.exception(
                "Error al cargar horarios publicos. odontologo_id=%s fecha=%s duracion_ms=%s",
                odontologo.pk,
                fecha.isoformat(),
                duracion_ms,
            )
            return JsonResponse(
                {
                    "ok": False,
                    "codigo": "error_horarios",
                    "mensaje": "No se pudieron cargar los horarios. Intenta nuevamente.",
                },
                status=500,
            )

        if settings.TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED:
            return self._respuesta_horarios_inteligentes(
                disponibilidad,
                inicio,
            )

        horarios_manana = disponibilidad["horarios_manana"]
        horarios_tarde = disponibilidad["horarios_tarde"]
        duracion_ms = round((perf_counter() - inicio) * 1000)

        logger.info(
            (
                "Horarios publicos cargados. odontologo_id=%s fecha=%s duracion_ms=%s "
                "horarios_manana=%s horarios_tarde=%s dias_cercanos=%s cache_hit=%s"
            ),
            odontologo.pk,
            fecha.isoformat(),
            duracion_ms,
            len(horarios_manana),
            len(horarios_tarde),
            len(disponibilidad["dias_cercanos"]),
            disponibilidad.get("cache_hit", False),
        )

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

    def _respuesta_horarios_inteligentes(self, disponibilidad, inicio):
        resultado = disponibilidad["resultado_inteligente"]
        configuracion_tipo = disponibilidad["configuracion_tipo"]
        recomendados_manana = disponibilidad["horarios_recomendados_manana"]
        recomendados_tarde = disponibilidad["horarios_recomendados_tarde"]
        alternativos_manana = disponibilidad["horarios_alternativos_manana"]
        alternativos_tarde = disponibilidad["horarios_alternativos_tarde"]
        total = sum(
            len(grupo)
            for grupo in (
                recomendados_manana,
                recomendados_tarde,
                alternativos_manana,
                alternativos_tarde,
            )
        )
        duracion_ms = round((perf_counter() - inicio) * 1000)
        logger.info(
            (
                "Horarios inteligentes servidos. odontologo_id=%s tipo_turno_id=%s fecha=%s "
                "cantidad_candidatos=%s cantidad_recomendados=%s cantidad_alternativos=%s "
                "cantidad_descartados_fragmentacion=%s cache_hit=%s duracion_ms=%s "
                "algoritmo_version=%s"
            ),
            disponibilidad["odontologo"].pk,
            configuracion_tipo.tipo_turno_id,
            disponibilidad["fecha"].isoformat(),
            resultado.total_candidatos_validos,
            len(resultado.recomendados),
            len(resultado.alternativos),
            resultado.descartados_por_fragmentacion,
            disponibilidad.get("cache_hit", False),
            duracion_ms,
            resultado.algoritmo_version,
        )
        return JsonResponse(
            {
                "ok": True,
                "mensaje": (
                    "Te mostramos primero los horarios que mejor encajan con la disponibilidad."
                    if total
                    else "No hay horarios disponibles para esa fecha. Probá con otro día."
                ),
                "odontologo": self._serializar_odontologo(disponibilidad["odontologo"]),
                "tipo_turno": {
                    "id": configuracion_tipo.tipo_turno_id,
                    "nombre": configuracion_tipo.tipo_turno.nombre,
                    "duracion_aproximada": configuracion_tipo.duracion_atencion_minutos,
                },
                "fecha": self._serializar_fecha(disponibilidad["fecha"]),
                "dias_cercanos": [
                    self._serializar_dia_cercano(dia) for dia in disponibilidad["dias_cercanos"]
                ],
                "horarios_recomendados": {
                    "manana": self._serializar_horarios(recomendados_manana),
                    "tarde": self._serializar_horarios(recomendados_tarde),
                },
                "horarios_alternativos": {
                    "manana": self._serializar_horarios(alternativos_manana),
                    "tarde": self._serializar_horarios(alternativos_tarde),
                },
                "algoritmo_version": resultado.algoritmo_version,
            }
        )

    @staticmethod
    def _obtener_odontologo(odontologo_id):
        if not odontologo_id:
            return None

        try:
            return (
                Odontologo.objects.select_related("usuario")
                .filter(
                    pk=odontologo_id,
                    activo=True,
                )
                .first()
            )
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
            "duracion": (
                None
                if settings.TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED
                else DURACION_SOLICITUD_PUBLICA_MINUTOS
            ),
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


class SolicitudTurnoPublicaDatosView(PublicShellMixin, FormView):
    form_class = SolicitudTurnoPublicaForm
    template_name = "turnos/solicitud_publica_form.html"
    success_url = reverse_lazy("landing_publica")
    turnstile_requerido = False
    proteccion_no_disponible = False

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

        try:
            return super().get(request, *args, **kwargs)
        except ProteccionSolicitudPublicaError as error:
            self.proteccion_no_disponible = True
            form = self.get_form()
            form.add_error(None, error.mensaje)
            return self._form_invalid_con_estado(form, error.status_code, error.retry_after)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        reserva = self._obtener_reserva_desde_get() or self._obtener_reserva_desde_post()
        context.update(reserva or {})
        documento = self.request.POST.get("documento") if self.request.method == "POST" else ""
        if self.proteccion_no_disponible:
            context["turnstile_requerido"] = False
        else:
            context["turnstile_requerido"] = (
                self.turnstile_requerido
                if self.request.method == "POST"
                else turnstile_requerido_para_request(self.request, documento)
            )
        context["turnstile_site_key"] = settings.TURNSTILE_SITE_KEY
        context["smart_scheduling_enabled"] = settings.TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED

        if reserva:
            duracion_visible = reserva.get(
                "duracion_atencion_minutos",
                DURACION_SOLICITUD_PUBLICA_MINUTOS,
            )
            context["hora_fin"] = (
                datetime.combine(reserva["fecha"], reserva["hora_inicio"])
                + timedelta(minutes=duracion_visible)
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
                    with transaction.atomic():
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

            with transaction.atomic():
                resultado = crear_solicitud_turno_publica_resultado(form.cleaned_data)
                completar_idempotencia(resultado_idempotencia.token_hash)
        except ValidationError as error:
            respuesta_liberacion = self._liberar_idempotencia_adquirida(
                form,
                resultado_idempotencia,
            )

            if respuesta_liberacion:
                return respuesta_liberacion

            if hasattr(error, "message_dict"):
                for field, messages in error.message_dict.items():
                    form.add_error(field if field in form.fields else None, messages)
            else:
                form.add_error(None, error)
            return self.form_invalid(form)
        except MaximoSolicitudesPendientesError as error:
            respuesta_liberacion = self._liberar_idempotencia_adquirida(
                form,
                resultado_idempotencia,
            )

            if respuesta_liberacion:
                return respuesta_liberacion

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

    def _liberar_idempotencia_adquirida(self, form, resultado_idempotencia):
        if not resultado_idempotencia or not resultado_idempotencia.debe_procesar:
            return None

        try:
            liberar_idempotencia(resultado_idempotencia.token_hash)
        except ProteccionSolicitudPublicaError as error:
            form.add_error(None, error.mensaje)
            return self._form_invalid_con_estado(form, error.status_code, error.retry_after)

        return None

    def _existe_duplicado_exacto_desde_post(self):
        return obtener_solicitud_duplicada_exacta(self.request.POST) is not None

    def _obtener_reserva_desde_get(self):
        return self._obtener_reserva(self.request.GET, validar_disponibilidad=True)

    def _obtener_reserva_desde_post(self):
        return self._obtener_reserva(self.request.POST, validar_disponibilidad=False)

    @staticmethod
    def _obtener_reserva(data, validar_disponibilidad):
        odontologo_id = data.get("odontologo")
        tipo_turno_id = data.get("tipo_turno")
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

        if settings.TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED:
            configuracion_tipo = obtener_configuracion_tipo_publica(
                odontologo,
                tipo_turno_id,
            )
            if not configuracion_tipo:
                return None
            resultado = calcular_horarios_inteligentes(
                odontologo=odontologo,
                fecha=fecha,
                duracion_atencion_minutos=configuracion_tipo.duracion_atencion_minutos,
                margen_posterior_minutos=configuracion_tipo.margen_posterior_minutos,
            )
            candidato = buscar_candidato(resultado, hora_inicio)
            if validar_disponibilidad and not candidato:
                return None
            return {
                "odontologo": odontologo,
                "tipo_turno": configuracion_tipo.tipo_turno,
                "configuracion_tipo": configuracion_tipo,
                "duracion_atencion_minutos": configuracion_tipo.duracion_atencion_minutos,
                "duracion_bloqueada_minutos": configuracion_tipo.duracion_bloqueada_minutos,
                "clasificacion": candidato.clasificacion if candidato else "",
                "fecha": fecha,
                "hora_inicio": hora_inicio,
            }

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


class SolicitudTurnoPublicaOkView(PublicShellMixin, TemplateView):
    template_name = "turnos/solicitud_publica_ok.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        turno_id = self.request.session.get("solicitud_turno_publica_id")
        solicitud_id = self.request.session.get("solicitud_publica_revision_id")
        context["turno"] = (
            Turno.objects.select_related(
                "odontologo",
                "odontologo__usuario",
                "tipo_turno",
            )
            .filter(pk=turno_id)
            .first()
        )
        context["solicitud"] = (
            SolicitudTurnoPublica.objects.filter(pk=solicitud_id).first() if solicitud_id else None
        )
        return context
