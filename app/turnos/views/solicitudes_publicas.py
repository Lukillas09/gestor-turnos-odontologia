from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import FormView, ListView

from usuarios.mixins import GestionConsultorioRequeridaMixin
from usuarios.roles import puede_revisar_solicitudes_publicas

from ..forms import RevisionSolicitudTurnoPublicaForm
from ..models import SolicitudTurnoPublica
from ..solicitudes_publicas.selectors import (
    obtener_alertas_administrativas_publicas,
    obtener_solicitudes_publicas_para_bandeja,
)
from ..solicitudes_publicas.services import revisar_solicitud_publica
from .helpers import construir_filas_revision_solicitud_publica


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
            "Revisar paciente nuevo" if es_paciente_nuevo else "Revisar cambios informados"
        )
        context["badge_revision"] = (
            "Pendiente de validación" if es_paciente_nuevo else "Paciente existente"
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
                    "descripcion": (
                        "La solicitud queda para revision administrativa antes de "
                        "reactivar al paciente."
                    ),
                    "boton": "Mantener pendiente",
                    "variante": "neutral",
                },
                {
                    "valor": "rechazar",
                    "titulo": "Marcar solicitud como no valida",
                    "descripcion": (
                        "La solicitud se descarta sin crear turno ni modificar el paciente."
                    ),
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
