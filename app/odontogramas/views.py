import json

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.views import View
from django.views.generic import TemplateView

from historias.access_policy import (
    limitar_pacientes_clinicos_para_request,
    obtener_politica_lectura,
    registrar_evento_acceso_clinico,
)
from historias.models import AccesoClinicoAuditoria
from pacientes.models import Paciente
from usuarios.mixins import VerPacientesRequeridoMixin

from .forms import EstadoDentalForm
from .models import Odontograma
from .permissions import puede_editar_odontograma, puede_ver_odontograma
from .selectors import construir_filas_odontograma, construir_leyenda_colores, construir_tooltip
from .services import obtener_o_crear_odontograma, registrar_estado_dental


class PacienteOdontogramaMixin(VerPacientesRequeridoMixin):
    paciente = None
    odontograma = None

    def dispatch(self, request, *args, **kwargs):
        if not settings.ODONTOGRAMA_FEATURE_ENABLED:
            raise Http404("El odontograma todavia no esta disponible.")

        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)

        if not self.test_func():
            return super().dispatch(request, *args, **kwargs)

        self.paciente = get_object_or_404(
            limitar_pacientes_clinicos_para_request(Paciente.objects.all(), request),
            pk=kwargs["paciente_pk"],
        )

        if not puede_ver_odontograma(request.user, self.paciente, request=request):
            raise PermissionDenied("No tenés permiso para ver este odontograma.")

        if puede_editar_odontograma(request.user, self.paciente):
            self.odontograma = obtener_o_crear_odontograma(self.paciente)
        else:
            self.odontograma = get_object_or_404(Odontograma, paciente=self.paciente)

        registrar_evento_acceso_clinico(
            request=request,
            accion=AccesoClinicoAuditoria.Accion.VER_ODONTOGRAMA,
            resultado=AccesoClinicoAuditoria.Resultado.PERMITIDO,
            politica=obtener_politica_lectura(request.user, self.paciente, request=request),
            paciente=self.paciente,
            motivo="Odontograma consultado.",
        )
        return super().dispatch(request, *args, **kwargs)


class OdontogramaDetailView(PacienteOdontogramaMixin, TemplateView):
    template_name = "odontogramas/odontograma_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        puede_editar = puede_editar_odontograma(self.request.user, self.paciente)
        historial = self.odontograma.estados_dentales.select_related(
            "odontologo",
            "odontologo__usuario",
            "registrado_por",
        ).order_by("-creado_en")[:30]
        context.update(
            {
                "paciente": self.paciente,
                "odontograma": self.odontograma,
                "filas_odontograma": construir_filas_odontograma(self.odontograma),
                "historial": historial,
                "puede_editar_odontograma": puede_editar,
                "estado_form": EstadoDentalForm(),
                "leyenda_colores": construir_leyenda_colores(),
                "odontograma_titulo": "Odontograma",
                "odontograma_mostrar_historial": True,
                "odontograma_save_mode": "ajax",
            }
        )
        return context


class EstadoDentalCreateView(PacienteOdontogramaMixin, View):
    def post(self, request, *args, **kwargs):
        if not puede_editar_odontograma(request.user, self.paciente):
            return JsonResponse(
                {"ok": False, "error": "No tenés permiso para editar este odontograma."},
                status=403,
            )

        try:
            payload = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            return JsonResponse(
                {"ok": False, "error": "No se pudo interpretar la solicitud."},
                status=400,
            )

        form = EstadoDentalForm(payload)

        if not form.is_valid():
            return JsonResponse(
                {"ok": False, "errors": form.errors},
                status=400,
            )

        estado = registrar_estado_dental(
            odontograma=self.odontograma,
            diente=form.cleaned_data["diente"],
            cara=form.cleaned_data["cara"],
            estado_clinico=form.cleaned_data["estado_clinico"],
            observacion=form.cleaned_data["observacion"],
            realizado=form.cleaned_data["realizado"],
            usuario=request.user,
        )
        registrar_evento_acceso_clinico(
            request=request,
            accion=AccesoClinicoAuditoria.Accion.EDITAR_ODONTOGRAMA,
            resultado=AccesoClinicoAuditoria.Resultado.PERMITIDO,
            politica=AccesoClinicoAuditoria.Politica.ASOCIACION_ACTIVA,
            paciente=self.paciente,
            motivo="Estado dental registrado.",
        )

        historial_html = render_to_string(
            "odontogramas/includes/historial_item.html",
            {"estado": estado},
            request=request,
        )

        return JsonResponse(
            {
                "ok": True,
                "estado": serializar_estado_dental(estado),
                "historial_html": historial_html,
            }
        )


def serializar_estado_dental(estado):
    return {
        "id": estado.pk,
        "diente": estado.diente,
        "cara": estado.cara,
        "cara_label": estado.cara_display,
        "estado_clinico": estado.estado_clinico,
        "estado_label": estado.get_estado_clinico_display(),
        "color": estado.color,
        "color_hex": estado.color_hex,
        "observacion": estado.observacion,
        "realizado": estado.realizado,
        "fecha": estado.fecha.strftime("%d/%m/%Y"),
        "odontologo": str(estado.odontologo) if estado.odontologo else "-",
        "tooltip": construir_tooltip(estado.diente, estado.cara_display, estado),
    }
