from datetime import datetime, timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.utils.dateparse import parse_date, parse_time
from django.utils.formats import date_format
from django.views import View
from django.views.generic import FormView, TemplateView

from pacientes.normalizacion import normalizar_documento

from ..excepciones import (
    obtener_horarios_publicos_disponibles,
    obtener_rango_reserva_publica,
    validar_fecha_reserva_publica,
)
from ..forms import (
    DURACION_SOLICITUD_PUBLICA_MINUTOS,
    SolicitudTurnoBusquedaPublicaForm,
    SolicitudTurnoPublicaForm,
)
from ..models import Odontologo, SolicitudTurnoPublica, Turno
from ..services import crear_solicitud_turno_publica_resultado
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
from ..solicitudes_publicas.services import MaximoSolicitudesPendientesError

SOLICITUD_PUBLICA_CONFIRMADA_SESSION_KEY = "solicitud_turno_publica_confirmada"


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
            odontologo_pk = int(odontologo_id)
        except (TypeError, ValueError):
            return False

        return (
            SolicitudTurnoPublica.objects.filter(
                paciente__documento=documento,
                turno__odontologo_id=odontologo_pk,
                turno__fecha=fecha,
                turno__hora_inicio=hora_inicio,
                turno__estado__in=[Turno.Estado.PENDIENTE, Turno.Estado.CONFIRMADO],
            )
            .exclude(
                estado_revision=SolicitudTurnoPublica.EstadoRevision.RECHAZADA,
            )
            .exists()
        )

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
            SolicitudTurnoPublica.objects.filter(pk=solicitud_id).first() if solicitud_id else None
        )
        return context
