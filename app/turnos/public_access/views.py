from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.utils.dateparse import parse_date
from django.utils.formats import date_format
from django.views import View
from django.views.generic import FormView, TemplateView

from turnos.excepciones import (
    obtener_horarios_publicos_disponibles,
    validar_fecha_reserva_publica,
)
from turnos.forms import (
    CancelacionAccesoPublicoTurnoForm,
    SolicitudAccesoPublicoTurnosForm,
    TurnoReprogramacionAccesoPublicoForm,
    VerificacionAccesoPublicoTurnosForm,
)
from turnos.integrations.turnstile import validar_turnstile
from turnos.mixins import PublicShellMixin
from turnos.models import AccionPublicaTurno
from turnos.public_access.exceptions import (
    MENSAJE_PROTECCION_PUBLICA_NO_DISPONIBLE,
    RETRY_AFTER_PROTECCION_PUBLICA_SECONDS,
    ProteccionPublicaNoDisponible,
)
from turnos.smart_scheduling import calcular_horarios_inteligentes

from .permissions import AccesoPublicoTurnosRequeridoMixin
from .rate_limit import incrementar_limite, leer_contador
from .selectors import (
    obtener_paciente_id_verificado_desde_session,
    obtener_turnos_activos_de_paciente,
)
from .services import (
    MENSAJE_ACCION_INVALIDA,
    MENSAJE_CODIGO_INVALIDO,
    MENSAJE_SOLICITUD_GENERICA,
    cancelar_turno_publico_seguro,
    cerrar_acceso_publico,
    generar_permisos_para_turnos,
    obtener_token_accion_desde_session,
    reenviar_codigo_acceso_publico,
    registrar_intento_solicitud_acceso,
    reprogramar_turno_publico_seguro,
    solicitar_acceso_publico_turnos,
    validar_accion_publica_sin_consumir,
    validar_codigo_acceso_publico,
)
from .tokens import (
    PUBLIC_ACCESS_PENDING_CHALLENGE_KEY,
    hash_valor_publico,
    normalizar_documento,
    obtener_ip_cliente,
)


def _respuesta_proteccion_no_disponible():
    response = HttpResponse(
        MENSAJE_PROTECCION_PUBLICA_NO_DISPONIBLE,
        status=503,
        content_type="text/plain; charset=utf-8",
    )
    response["Retry-After"] = str(RETRY_AFTER_PROTECCION_PUBLICA_SECONDS)
    return response


def _respuesta_accion_limitada():
    response = HttpResponse(
        MENSAJE_ACCION_INVALIDA,
        status=429,
        content_type="text/plain; charset=utf-8",
    )
    response["Retry-After"] = str(settings.TURNOS_PUBLIC_ACTION_WINDOW_SECONDS)
    return response


def _permitir_operacion_publica(request, nombre):
    ip_hash = hash_valor_publico(obtener_ip_cliente(request), "ip")
    return incrementar_limite(
        nombre,
        ip_hash,
        settings.TURNOS_PUBLIC_ACTION_LIMIT,
        settings.TURNOS_PUBLIC_ACTION_WINDOW_SECONDS,
    ).permitido


class ProteccionPublicaDisponibleMixin:
    def dispatch(self, request, *args, **kwargs):
        try:
            return super().dispatch(request, *args, **kwargs)
        except ProteccionPublicaNoDisponible:
            return _respuesta_proteccion_no_disponible()


class SolicitarAccesoPublicoTurnosView(
    ProteccionPublicaDisponibleMixin,
    PublicShellMixin,
    FormView,
):
    form_class = SolicitudAccesoPublicoTurnosForm
    template_name = "turnos/public_access/solicitar_acceso.html"
    success_url = "turnos:acceso_publico_verificar"
    turnstile_requerido = False
    limites_solicitud = None

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.setdefault("initial", {})
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        documento = ""

        if self.request.method == "POST":
            documento = normalizar_documento(self.request.POST.get("documento"))

        context["turnstile_requerido"] = (
            self.turnstile_requerido
            if self.request.method == "POST"
            else self._requiere_turnstile(documento)
        )
        context["turnstile_site_key"] = settings.TURNSTILE_SITE_KEY
        return context

    def post(self, request, *args, **kwargs):
        documento = normalizar_documento(request.POST.get("documento"))
        self.turnstile_requerido = self._requiere_turnstile(documento)
        self.limites_solicitud = registrar_intento_solicitud_acceso(request, documento)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        documento = form.cleaned_data["documento"]

        if self.turnstile_requerido:
            resultado_turnstile = validar_turnstile(
                self.request.POST.get("cf-turnstile-response")
                or form.cleaned_data["turnstile_token"],
                obtener_ip_cliente(self.request),
            )

            if not resultado_turnstile.valido:
                form.add_error(None, MENSAJE_SOLICITUD_GENERICA)
                return self.form_invalid(form)

        solicitar_acceso_publico_turnos(
            self.request,
            documento,
            limites=self.limites_solicitud,
        )
        messages.success(self.request, MENSAJE_SOLICITUD_GENERICA)
        return redirect(self.success_url)

    def _requiere_turnstile(self, documento):
        if not settings.TURNSTILE_ENABLED:
            return False

        if settings.TURNSTILE_REQUIRED_AFTER_ATTEMPTS <= 0:
            return True

        if settings.TURNOS_PUBLIC_ACCESS_REQUEST_LIMIT <= 0:
            return False

        ip_hash = hash_valor_publico(obtener_ip_cliente(self.request), "ip")
        requiere_por_ip = (
            leer_contador(
                "solicitud_ip",
                ip_hash,
                settings.TURNOS_PUBLIC_ACCESS_REQUEST_WINDOW_SECONDS,
            )
            >= settings.TURNSTILE_REQUIRED_AFTER_ATTEMPTS
        )

        if not documento:
            return requiere_por_ip

        dni_hash = hash_valor_publico(documento, "dni")
        requiere_por_dni = (
            leer_contador(
                "solicitud_dni",
                dni_hash,
                settings.TURNOS_PUBLIC_ACCESS_REQUEST_WINDOW_SECONDS,
            )
            >= settings.TURNSTILE_REQUIRED_AFTER_ATTEMPTS
        )
        return requiere_por_ip or requiere_por_dni


class VerificarAccesoPublicoTurnosView(
    ProteccionPublicaDisponibleMixin,
    PublicShellMixin,
    FormView,
):
    form_class = VerificacionAccesoPublicoTurnosForm
    template_name = "turnos/public_access/verificar.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.session.get(PUBLIC_ACCESS_PENDING_CHALLENGE_KEY):
            return redirect("turnos:acceso_publico_solicitar")

        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if request.POST.get("accion") == "reenviar":
            messages.success(request, reenviar_codigo_acceso_publico(request))
            return redirect("turnos:acceso_publico_verificar")

        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        resultado = validar_codigo_acceso_publico(self.request, form.cleaned_data["codigo"])

        if not resultado.valido:
            form.add_error("codigo", MENSAJE_CODIGO_INVALIDO)
            return self.form_invalid(form)

        messages.success(self.request, "Acceso verificado correctamente.")
        return redirect("turnos:mis_turnos_publico")


class MisTurnosPublicoView(
    PublicShellMixin,
    AccesoPublicoTurnosRequeridoMixin,
    TemplateView,
):
    template_name = "turnos/public_access/mis_turnos.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        turnos = list(obtener_turnos_activos_de_paciente(self.paciente_id_publico))
        acciones = generar_permisos_para_turnos(self.request, self.paciente_id_publico, turnos)

        context["items_turnos"] = [
            self._construir_item_turno(turno, acciones.get(turno.pk, {})) for turno in turnos
        ]
        return context

    def _construir_item_turno(self, turno, acciones):
        cancelar = acciones.get(AccionPublicaTurno.TipoAccion.CANCELAR)
        reprogramar = acciones.get(AccionPublicaTurno.TipoAccion.REPROGRAMAR)

        return {
            "turno": turno,
            "fecha": date_format(turno.fecha, "d/m/Y"),
            "hora": f"{turno.hora_inicio:%H:%M} a {turno.hora_fin:%H:%M}",
            "estado": turno.get_estado_display(),
            "odontologo": turno.odontologo.nombre_completo,
            "tipo_turno": turno.tipo_turno_nombre_snapshot,
            "duracion_atencion": turno.duracion_atencion_minutos,
            "cancelar_accion": cancelar,
            "cancelar_token": (
                obtener_token_accion_desde_session(self.request, cancelar.id) if cancelar else ""
            ),
            "reprogramar_accion": reprogramar,
        }


class CerrarAccesoPublicoTurnosView(View):
    http_method_names = ["post"]

    def post(self, request):
        cerrar_acceso_publico(request)
        messages.success(request, "El acceso temporal fue cerrado correctamente.")
        return redirect("turnos:acceso_publico_solicitar")


class CancelarTurnoPublicoSeguroView(
    ProteccionPublicaDisponibleMixin,
    AccesoPublicoTurnosRequeridoMixin,
    View,
):
    http_method_names = ["post"]

    def post(self, request, accion_id):
        if not _permitir_operacion_publica(request, "cancelar"):
            return _respuesta_accion_limitada()

        form = CancelacionAccesoPublicoTurnoForm(request.POST)

        if not form.is_valid():
            messages.error(request, MENSAJE_ACCION_INVALIDA)
            return redirect("turnos:mis_turnos_publico")

        ok = cancelar_turno_publico_seguro(
            accion_id=accion_id,
            token=form.cleaned_data["accion_token"],
            paciente_id=self.paciente_id_publico,
            motivo_cancelacion=form.cleaned_data["motivo_cancelacion"],
        )

        messages.success(
            request, "Tu turno fue cancelado correctamente." if ok else MENSAJE_ACCION_INVALIDA
        )
        return redirect("turnos:mis_turnos_publico")


class HorariosReprogramacionPublicaJsonView(AccesoPublicoTurnosRequeridoMixin, View):
    http_method_names = ["get"]

    def get(self, request, accion_id):
        token = obtener_token_accion_desde_session(request, accion_id)
        accion = validar_accion_publica_sin_consumir(
            accion_id,
            token,
            self.paciente_id_publico,
            AccionPublicaTurno.TipoAccion.REPROGRAMAR,
        )

        if accion is None:
            return JsonResponse({"horarios": [], "mensaje": MENSAJE_ACCION_INVALIDA}, status=403)

        fecha = parse_date(request.GET.get("fecha") or "")

        if not fecha:
            return JsonResponse(
                {
                    "horarios": [],
                    "mensaje": "Elegí una fecha para ver horarios disponibles.",
                }
            )

        try:
            validar_fecha_reserva_publica(fecha)
        except ValidationError as error:
            return JsonResponse({"horarios": [], "mensaje": error.messages[0]})

        if settings.TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED and accion.turno.tipo_turno_id:
            resultado = calcular_horarios_inteligentes(
                odontologo=accion.turno.odontologo,
                fecha=fecha,
                duracion_atencion_minutos=(
                    accion.turno.duracion_atencion_minutos or accion.turno.duracion_minutos
                ),
                margen_posterior_minutos=(accion.turno.margen_posterior_minutos_snapshot),
                turno_excluido=accion.turno,
            )
            recomendados = [
                self._serializar_candidato(candidato) for candidato in resultado.recomendados
            ]
            alternativos = [
                self._serializar_candidato(candidato) for candidato in resultado.alternativos
            ]
            horarios = recomendados + alternativos
        else:
            horarios_legacy = obtener_horarios_publicos_disponibles(
                odontologo=accion.turno.odontologo,
                fecha=fecha,
                duracion_minutos=accion.turno.duracion_minutos,
                turno_excluido=accion.turno,
            )
            recomendados = []
            alternativos = []
            horarios = [
                {"value": horario.strftime("%H:%M"), "label": horario.strftime("%H:%M")}
                for horario in horarios_legacy
            ]

        if not horarios:
            return JsonResponse(
                {
                    "horarios": [],
                    "mensaje": "No hay horarios libres para esa fecha.",
                }
            )

        return JsonResponse(
            {
                "horarios": horarios,
                "horarios_recomendados": recomendados,
                "horarios_alternativos": alternativos,
                "mensaje": (
                    "Te mostramos primero los horarios que mejor encajan con la disponibilidad."
                    if recomendados
                    else "Solo se muestran horarios libres."
                ),
            }
        )

    @staticmethod
    def _serializar_candidato(candidato):
        valor = candidato.hora_inicio.strftime("%H:%M")
        return {"value": valor, "label": valor}


class ReprogramarTurnoPublicoSeguroView(
    ProteccionPublicaDisponibleMixin,
    PublicShellMixin,
    AccesoPublicoTurnosRequeridoMixin,
    FormView,
):
    form_class = TurnoReprogramacionAccesoPublicoForm
    template_name = "turnos/public_access/reprogramar.html"
    accion = None
    accion_token = ""

    def dispatch(self, request, *args, **kwargs):
        self.paciente_id_publico = obtener_paciente_id_verificado_desde_session(request)

        if not self.paciente_id_publico:
            return redirect("turnos:acceso_publico_solicitar")

        self.accion_token = obtener_token_accion_desde_session(request, kwargs.get("accion_id"))
        self.accion = validar_accion_publica_sin_consumir(
            kwargs.get("accion_id"),
            self.accion_token,
            self.paciente_id_publico,
            AccionPublicaTurno.TipoAccion.REPROGRAMAR,
        )

        if self.accion is None:
            messages.error(request, MENSAJE_ACCION_INVALIDA)
            return redirect("turnos:mis_turnos_publico")

        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if not _permitir_operacion_publica(request, "reprogramar"):
            return _respuesta_accion_limitada()

        return super().post(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.accion.turno
        kwargs["accion_token"] = self.accion_token
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["turno"] = self.accion.turno
        context["accion"] = self.accion
        return context

    def form_valid(self, form):
        try:
            ok, _turno = reprogramar_turno_publico_seguro(
                accion_id=self.kwargs["accion_id"],
                token=form.cleaned_data["accion_token"],
                paciente_id=self.paciente_id_publico,
                datos={
                    "fecha": form.cleaned_data["fecha"],
                    "hora_inicio": form.cleaned_data["hora_inicio"],
                    "duracion_minutos": self.accion.turno.duracion_minutos,
                },
            )
        except ValidationError as error:
            form.add_error(None, error)
            return self.form_invalid(form)

        if not ok:
            messages.error(self.request, MENSAJE_ACCION_INVALIDA)
            return redirect("turnos:mis_turnos_publico")

        messages.success(self.request, "Tu turno fue reprogramado correctamente.")
        return redirect("turnos:mis_turnos_publico")
