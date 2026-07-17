from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from historias.access_policy import (
    limitar_pacientes_clinicos_para_request,
    obtener_politica_escritura,
    obtener_politica_lectura,
)
from historias.models import AccesoClinicoAuditoria
from pacientes.models import Paciente

from .audit import auditoria_de_indicacion, registrar_evento_indicacion
from .emails import reenviar_indicacion
from .forms import (
    AnularIndicacionForm,
    ConfirmarReemplazoForm,
    EmitirIndicacionForm,
    IndicacionBorradorForm,
    ReenviarIndicacionForm,
)
from .integrity import verificar_sello_indicacion
from .models import IndicacionPaciente
from .permissions import (
    indicaciones_habilitadas,
    obtener_odontologo_activo,
    puede_anular_indicacion,
    puede_crear_indicacion,
    puede_editar_indicacion,
    puede_emitir_indicacion,
    puede_reenviar_indicacion,
)
from .selectors import (
    indicaciones_del_paciente,
    obtener_indicacion_visible,
    plantillas_activas,
)
from .services import (
    actualizar_borrador_indicacion,
    anular_indicacion,
    crear_borrador_indicacion,
    crear_reemplazo_indicacion,
    emitir_indicacion,
)


class IndicacionesFeatureMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not indicaciones_habilitadas():
            raise Http404("Módulo no disponible.")
        return super().dispatch(request, *args, **kwargs)

    def obtener_paciente(self):
        if not hasattr(self, "_paciente"):
            try:
                self._paciente = get_object_or_404(
                    limitar_pacientes_clinicos_para_request(
                        Paciente.objects.all(),
                        self.request,
                        lectura=True,
                    ),
                    pk=self.kwargs["paciente_pk"],
                )
            except Http404:
                registrar_evento_indicacion(
                    request=self.request,
                    accion=AccesoClinicoAuditoria.Accion.INTENTO_ACCESO_INDICACION,
                    resultado=AccesoClinicoAuditoria.Resultado.DENEGADO,
                    politica=AccesoClinicoAuditoria.Politica.SIN_PERMISO,
                    identificador=str(
                        self.kwargs.get("indicacion_uuid", self.kwargs["paciente_pk"])
                    ),
                    motivo="Intento de acceso al módulo de indicaciones fuera de alcance.",
                )
                raise
        return self._paciente

    def obtener_indicacion(self):
        paciente = self.obtener_paciente()
        try:
            return obtener_indicacion_visible(
                paciente_pk=paciente.pk,
                indicacion_uuid=self.kwargs["indicacion_uuid"],
                usuario=self.request.user,
                request=self.request,
            )
        except Http404:
            registrar_evento_indicacion(
                request=self.request,
                accion=AccesoClinicoAuditoria.Accion.INTENTO_ACCESO_INDICACION,
                paciente=paciente,
                resultado=AccesoClinicoAuditoria.Resultado.DENEGADO,
                politica=AccesoClinicoAuditoria.Politica.SIN_PERMISO,
                identificador=str(self.kwargs["indicacion_uuid"]),
                motivo="Intento de acceso a una indicación fuera de alcance.",
            )
            raise

    def contexto_base(self, **extra):
        return {"paciente": self.obtener_paciente(), **extra}


class IndicacionListView(IndicacionesFeatureMixin, View):
    template_name = "indicaciones/indicacion_list.html"

    def get(self, request, paciente_pk):
        paciente = self.obtener_paciente()
        indicaciones = indicaciones_del_paciente(
            paciente,
            request.user,
            request=request,
        )
        registrar_evento_indicacion(
            request=request,
            accion=AccesoClinicoAuditoria.Accion.VER_INDICACION,
            paciente=paciente,
            politica=obtener_politica_lectura(request.user, paciente, request=request),
            identificador=f"paciente:{paciente.pk}",
            motivo="Historial de indicaciones consultado.",
        )
        return render(
            request,
            self.template_name,
            self.contexto_base(
                indicaciones=indicaciones,
                puede_crear=puede_crear_indicacion(request.user, paciente),
            ),
        )


class IndicacionCreateView(IndicacionesFeatureMixin, View):
    template_name = "indicaciones/indicacion_form.html"

    def get(self, request, paciente_pk):
        paciente = self.obtener_paciente()
        _exigir_creacion(request, paciente)
        odontologo = obtener_odontologo_activo(request.user)
        initial = _initial_desde_plantilla(request.GET.get("plantilla"))
        form = IndicacionBorradorForm(
            paciente=paciente,
            odontologo=odontologo,
            initial=initial,
        )
        return render(request, self.template_name, self._contexto_form(form, "Nueva indicación"))

    def post(self, request, paciente_pk):
        paciente = self.obtener_paciente()
        _exigir_creacion(request, paciente)
        odontologo = obtener_odontologo_activo(request.user)
        form = IndicacionBorradorForm(
            request.POST,
            paciente=paciente,
            odontologo=odontologo,
        )
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                self._contexto_form(form, "Nueva indicación"),
                status=400,
            )
        indicacion = crear_borrador_indicacion(
            paciente=paciente,
            usuario=request.user,
            datos=form.cleaned_data,
            request=request,
        )
        messages.success(request, "La indicación se guardó como borrador.")
        return HttpResponseRedirect(_url_detalle(indicacion))

    def _contexto_form(self, form, titulo):
        return self.contexto_base(
            form=form,
            titulo_pagina=titulo,
            texto_boton="Guardar borrador",
            plantillas_json=_plantillas_para_interfaz(),
        )


class IndicacionUpdateView(IndicacionesFeatureMixin, View):
    template_name = "indicaciones/indicacion_form.html"

    def get(self, request, paciente_pk, indicacion_uuid):
        indicacion = self.obtener_indicacion()
        _exigir_edicion(request, indicacion)
        form = IndicacionBorradorForm(
            instance=indicacion,
            paciente=indicacion.paciente,
            odontologo=indicacion.odontologo,
            permitir_plantilla=False,
        )
        return render(request, self.template_name, self._contexto(form, indicacion))

    def post(self, request, paciente_pk, indicacion_uuid):
        indicacion = self.obtener_indicacion()
        _exigir_edicion(request, indicacion)
        form = IndicacionBorradorForm(
            request.POST,
            instance=indicacion,
            paciente=indicacion.paciente,
            odontologo=indicacion.odontologo,
            permitir_plantilla=False,
        )
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                self._contexto(form, indicacion),
                status=400,
            )
        actualizada = actualizar_borrador_indicacion(
            indicacion=indicacion,
            usuario=request.user,
            datos=form.cleaned_data,
            request=request,
        )
        messages.success(request, "Los cambios del borrador se guardaron.")
        return HttpResponseRedirect(_url_detalle(actualizada))

    def _contexto(self, form, indicacion):
        return self.contexto_base(
            form=form,
            indicacion=indicacion,
            titulo_pagina="Editar borrador",
            texto_boton="Guardar cambios",
            plantillas_json=[],
        )


class IndicacionReviewView(IndicacionesFeatureMixin, View):
    template_name = "indicaciones/indicacion_review.html"

    def get(self, request, paciente_pk, indicacion_uuid):
        indicacion = self.obtener_indicacion()
        if not puede_emitir_indicacion(request.user, indicacion):
            _denegar_accion(request, indicacion, "Intento de revisar una indicación no editable.")
        return render(
            request,
            self.template_name,
            self.contexto_base(indicacion=indicacion, form=EmitirIndicacionForm()),
        )


class IndicacionIssueView(IndicacionesFeatureMixin, View):
    template_name = "indicaciones/indicacion_review.html"

    def post(self, request, paciente_pk, indicacion_uuid):
        indicacion = self.obtener_indicacion()
        if not puede_emitir_indicacion(request.user, indicacion):
            _denegar_accion(request, indicacion, "Intento de emitir una indicación no permitida.")
        form = EmitirIndicacionForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                self.contexto_base(indicacion=indicacion, form=form),
                status=400,
            )
        emitida = emitir_indicacion(
            indicacion=indicacion,
            usuario=request.user,
            request=request,
        )
        emitida.refresh_from_db()
        if emitida.email_estado == IndicacionPaciente.EstadoEmail.ENVIADO:
            messages.success(request, "La indicación fue emitida y enviada por email.")
        elif emitida.email_estado == IndicacionPaciente.EstadoEmail.SIN_DESTINO:
            messages.success(
                request,
                "La indicación fue emitida. El paciente no tiene un email verificado.",
            )
        else:
            messages.warning(
                request,
                "La indicación fue emitida, pero el email quedó pendiente de reintento.",
            )
        return HttpResponseRedirect(_url_detalle(emitida))


class IndicacionDetailView(IndicacionesFeatureMixin, View):
    template_name = "indicaciones/indicacion_detail.html"

    def get(self, request, paciente_pk, indicacion_uuid):
        indicacion = self.obtener_indicacion()
        registrar_evento_indicacion(
            request=request,
            accion=AccesoClinicoAuditoria.Accion.VER_INDICACION,
            indicacion=indicacion,
            politica=obtener_politica_lectura(
                request.user,
                indicacion.paciente,
                request=request,
            ),
            motivo="Detalle de indicación consultado.",
        )
        integridad_valida = None
        if indicacion.estado != IndicacionPaciente.Estado.BORRADOR:
            integridad_valida = verificar_sello_indicacion(indicacion)
        return render(
            request,
            self.template_name,
            self.contexto_base(
                indicacion=indicacion,
                puede_editar=puede_editar_indicacion(request.user, indicacion),
                puede_anular=puede_anular_indicacion(request.user, indicacion),
                puede_reenviar=puede_reenviar_indicacion(request.user, indicacion),
                puede_reemplazar=(
                    indicacion.estado == IndicacionPaciente.Estado.ANULADA
                    and puede_crear_indicacion(request.user, indicacion.paciente)
                ),
                integridad_valida=integridad_valida,
                auditoria=auditoria_de_indicacion(indicacion)[:8],
            ),
        )


class IndicacionPdfView(IndicacionesFeatureMixin, View):
    def get(self, request, paciente_pk, indicacion_uuid):
        indicacion = self.obtener_indicacion()
        if indicacion.estado == IndicacionPaciente.Estado.BORRADOR or not indicacion.pdf:
            raise Http404("PDF no disponible.")
        registrar_evento_indicacion(
            request=request,
            accion=AccesoClinicoAuditoria.Accion.DESCARGAR_PDF_INDICACION,
            indicacion=indicacion,
            politica=obtener_politica_lectura(
                request.user,
                indicacion.paciente,
                request=request,
            ),
            motivo="PDF de indicación descargado.",
        )
        fecha = timezone.localtime(indicacion.emitida_en).date()
        response = FileResponse(
            indicacion.pdf.open("rb"),
            as_attachment=True,
            filename=f"indicaciones-{fecha:%Y-%m-%d}.pdf",
            content_type="application/pdf",
        )
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, no-store"
        return response


class IndicacionResendView(IndicacionesFeatureMixin, View):
    template_name = "indicaciones/indicacion_resend.html"

    def get(self, request, paciente_pk, indicacion_uuid):
        indicacion = self.obtener_indicacion()
        if not puede_reenviar_indicacion(request.user, indicacion):
            raise PermissionDenied("La indicación no puede reenviarse.")
        form = ReenviarIndicacionForm(indicacion=indicacion)
        return render(
            request, self.template_name, self.contexto_base(indicacion=indicacion, form=form)
        )

    def post(self, request, paciente_pk, indicacion_uuid):
        indicacion = self.obtener_indicacion()
        if not puede_reenviar_indicacion(request.user, indicacion):
            raise PermissionDenied("La indicación no puede reenviarse.")
        form = ReenviarIndicacionForm(request.POST, indicacion=indicacion)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                self.contexto_base(indicacion=indicacion, form=form),
                status=400,
            )
        enviado = reenviar_indicacion(
            indicacion=indicacion,
            usuario=request.user,
            request=request,
            usar_email_actual=form.cleaned_data.get("usar_email_actual", False),
        )
        if enviado:
            messages.success(request, "La indicación fue reenviada por email.")
        else:
            messages.warning(request, "El reenvío no pudo completarse y quedó registrado.")
        return HttpResponseRedirect(_url_detalle(indicacion))


class IndicacionVoidView(IndicacionesFeatureMixin, View):
    template_name = "indicaciones/indicacion_void.html"

    def get(self, request, paciente_pk, indicacion_uuid):
        indicacion = self.obtener_indicacion()
        if not puede_anular_indicacion(request.user, indicacion):
            raise PermissionDenied("La indicación no puede anularse.")
        return render(
            request,
            self.template_name,
            self.contexto_base(indicacion=indicacion, form=AnularIndicacionForm()),
        )

    def post(self, request, paciente_pk, indicacion_uuid):
        indicacion = self.obtener_indicacion()
        if not puede_anular_indicacion(request.user, indicacion):
            raise PermissionDenied("La indicación no puede anularse.")
        form = AnularIndicacionForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                self.contexto_base(indicacion=indicacion, form=form),
                status=400,
            )
        anulada = anular_indicacion(
            indicacion=indicacion,
            usuario=request.user,
            motivo=form.cleaned_data["motivo"],
            request=request,
        )
        messages.success(request, "La indicación fue anulada sin eliminar el documento original.")
        return HttpResponseRedirect(_url_detalle(anulada))


class IndicacionReplacementView(IndicacionesFeatureMixin, View):
    template_name = "indicaciones/indicacion_replacement.html"

    def get(self, request, paciente_pk, indicacion_uuid):
        indicacion = self.obtener_indicacion()
        _exigir_reemplazo(request, indicacion)
        return render(
            request,
            self.template_name,
            self.contexto_base(indicacion=indicacion, form=ConfirmarReemplazoForm()),
        )

    def post(self, request, paciente_pk, indicacion_uuid):
        indicacion = self.obtener_indicacion()
        _exigir_reemplazo(request, indicacion)
        form = ConfirmarReemplazoForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                self.contexto_base(indicacion=indicacion, form=form),
                status=400,
            )
        reemplazo = crear_reemplazo_indicacion(
            indicacion=indicacion,
            usuario=request.user,
            request=request,
        )
        messages.success(request, "Se creó un nuevo borrador vinculado al documento anulado.")
        return HttpResponseRedirect(_url_detalle(reemplazo))


def _exigir_creacion(request, paciente):
    if not puede_crear_indicacion(request.user, paciente):
        raise PermissionDenied("No tenés permiso para crear indicaciones para este paciente.")


def _exigir_edicion(request, indicacion):
    if not puede_editar_indicacion(request.user, indicacion):
        _denegar_accion(request, indicacion, "Intento de edición de una indicación no editable.")


def _exigir_reemplazo(request, indicacion):
    if indicacion.estado != IndicacionPaciente.Estado.ANULADA or not puede_crear_indicacion(
        request.user, indicacion.paciente
    ):
        raise PermissionDenied("La indicación no puede reemplazarse.")


def _denegar_accion(request, indicacion, motivo):
    registrar_evento_indicacion(
        request=request,
        accion=AccesoClinicoAuditoria.Accion.INTENTO_EDITAR_INDICACION_EMITIDA,
        indicacion=indicacion,
        resultado=AccesoClinicoAuditoria.Resultado.DENEGADO,
        politica=(
            obtener_politica_escritura(request.user, indicacion.paciente)
            or AccesoClinicoAuditoria.Politica.SIN_PERMISO
        ),
        motivo=motivo,
    )
    raise PermissionDenied("La acción solicitada no está permitida.")


def _initial_desde_plantilla(plantilla_id):
    if not plantilla_id:
        return {}
    plantilla = get_object_or_404(plantillas_activas(), pk=plantilla_id)
    return {
        "plantilla": plantilla,
        "titulo": plantilla.titulo_documento,
        "procedimiento": plantilla.procedimiento,
        "contenido": plantilla.contenido,
        "pautas_alarma": plantilla.pautas_alarma,
        "recomendaciones_control": plantilla.recomendaciones_control,
    }


def _plantillas_para_interfaz():
    return [
        {
            "id": plantilla.pk,
            "titulo": plantilla.titulo_documento,
            "procedimiento": plantilla.procedimiento,
            "contenido": plantilla.contenido,
            "pautas_alarma": plantilla.pautas_alarma,
            "recomendaciones_control": plantilla.recomendaciones_control,
        }
        for plantilla in plantillas_activas()
    ]


def _url_detalle(indicacion):
    return reverse(
        "indicaciones:detalle",
        kwargs={
            "paciente_pk": indicacion.paciente_id,
            "indicacion_uuid": indicacion.uuid,
        },
    )
