import json

from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.views import View
from django.views.generic import TemplateView

from pacientes.models import Paciente
from usuarios.mixins import VerPacientesRequeridoMixin

from .domain import COLORES_HEX
from .forms import EstadoDentalForm
from .models import EstadoDental
from .permissions import puede_editar_odontograma, puede_ver_odontograma
from .selectors import construir_filas_odontograma, construir_tooltip
from .services import obtener_o_crear_odontograma, registrar_estado_dental


class PacienteOdontogramaMixin(VerPacientesRequeridoMixin):
    paciente = None
    odontograma = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)

        self.paciente = get_object_or_404(Paciente, pk=kwargs["paciente_pk"])

        if not puede_ver_odontograma(request.user, self.paciente):
            raise PermissionDenied("No tenés permiso para ver este odontograma.")

        self.odontograma = obtener_o_crear_odontograma(self.paciente)
        return super().dispatch(request, *args, **kwargs)


class OdontogramaDetailView(PacienteOdontogramaMixin, TemplateView):
    template_name = "odontogramas/odontograma_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        puede_editar = puede_editar_odontograma(self.request.user, self.paciente)
        historial = (
            self.odontograma.estados_dentales.select_related(
                "odontologo",
                "odontologo__usuario",
                "registrado_por",
            )
            .order_by("-creado_en")[:30]
        )
        context.update(
            {
                "paciente": self.paciente,
                "odontograma": self.odontograma,
                "filas_odontograma": construir_filas_odontograma(self.odontograma),
                "historial": historial,
                "puede_editar_odontograma": puede_editar,
                "estado_form": EstadoDentalForm(),
                "leyenda_colores": [
                    {
                        "color": "azul",
                        "hex": COLORES_HEX["azul"],
                        "titulo": "Realizado / existente",
                        "detalle": "Obturación, corona, implante, conducto o prótesis.",
                    },
                    {
                        "color": "rojo",
                        "hex": COLORES_HEX["rojo"],
                        "titulo": "Pendiente",
                        "detalle": "Caries, extracción indicada o restauración necesaria.",
                    },
                    {
                        "color": "verde",
                        "hex": COLORES_HEX["verde"],
                        "titulo": "Control",
                        "detalle": "Temporal, sellador o seguimiento.",
                    },
                    {
                        "color": "negro",
                        "hex": COLORES_HEX["negro"],
                        "titulo": "Ausente / especial",
                        "detalle": "Ausente, extraído, fractura u observación especial.",
                    },
                ],
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
