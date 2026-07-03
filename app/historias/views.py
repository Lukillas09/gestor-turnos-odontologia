import logging

from django.contrib import messages
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.http import FileResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from odontogramas.forms import EstadoDentalForm
from odontogramas.models import EstadoDental
from odontogramas.permissions import puede_editar_odontograma
from odontogramas.selectors import construir_filas_odontograma, construir_leyenda_colores
from odontogramas.services import obtener_o_crear_odontograma
from pacientes.models import Paciente
from usuarios.mixins import HistoriaClinicaOdontologoRequeridoMixin
from usuarios.roles import obtener_odontologo_del_usuario

from .forms import HistoriaClinicaFiltroForm, HistoriaClinicaForm
from .access_policy import (
    limitar_historias_clinicas_para_request,
    limitar_pacientes_clinicos_para_request,
    obtener_politica_lectura,
    obtener_politica_escritura,
    registrar_evento_acceso_clinico,
)
from .models import AccesoClinicoAuditoria, HistoriaClinica, HistoriaClinicaAdjunto
from .permissions import (
    puede_crear_historia_de_paciente,
    puede_editar_historia_clinica,
)


logger = logging.getLogger(__name__)


class PacienteHistoriaClinicaMixin(HistoriaClinicaOdontologoRequeridoMixin):
    paciente = None
    requiere_permiso_creacion = False

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated or not self.test_func():
            return super().dispatch(request, *args, **kwargs)

        self.paciente = get_object_or_404(
            self.get_paciente_queryset(),
            pk=kwargs["paciente_pk"],
        )

        if self.requiere_permiso_creacion and not puede_crear_historia_de_paciente(
            request.user,
            self.paciente,
        ):
            _registrar_evento_clinico(
                request,
                "creacion_denegada_paciente_no_asociado",
                paciente=self.paciente,
                detalle="Intento de crear historia en paciente no asociado.",
            )
            raise PermissionDenied(
                "No tenés permiso para cargar historia clínica de este paciente."
            )

        return super().dispatch(request, *args, **kwargs)

    def get_paciente_queryset(self):
        return limitar_pacientes_clinicos_para_request(
            Paciente.objects.all(),
            self.request,
            lectura=not self.requiere_permiso_creacion,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["paciente"] = self.paciente
        return context


class HistoriaClinicaListView(PacienteHistoriaClinicaMixin, ListView):
    model = HistoriaClinica
    template_name = "historias/historia_clinica_list.html"
    context_object_name = "historias"
    paginate_by = 10

    def get_queryset(self):
        queryset = (
            limitar_historias_clinicas_para_request(
                HistoriaClinica.objects.filter(paciente=self.paciente),
                self.request,
            )
            .select_related(
                "paciente",
                "odontologo",
                "odontologo__usuario",
                "creado_por",
                "actualizado_por",
            )
            .annotate(cantidad_adjuntos=Count("adjuntos"))
            .order_by("-fecha", "-creado_en")
        )
        self.filtros_form = HistoriaClinicaFiltroForm(self.request.GET)

        if self.filtros_form.is_valid():
            filtros = self.filtros_form.cleaned_data

            if filtros["q"]:
                busqueda = filtros["q"]
                queryset = queryset.filter(
                    Q(motivo_consulta__icontains=busqueda)
                    | Q(diagnostico__icontains=busqueda)
                    | Q(tratamiento_realizado__icontains=busqueda)
                    | Q(pieza_dental__icontains=busqueda)
                    | Q(observaciones__icontains=busqueda)
                    | Q(odontologo__usuario__first_name__icontains=busqueda)
                    | Q(odontologo__usuario__last_name__icontains=busqueda)
                )

            if filtros["fecha_desde"]:
                queryset = queryset.filter(fecha__gte=filtros["fecha_desde"])

            if filtros["fecha_hasta"]:
                queryset = queryset.filter(fecha__lte=filtros["fecha_hasta"])

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query_params = self.request.GET.copy()
        query_params.pop("page", None)
        _registrar_evento_clinico(
            self.request,
            "ver_historia_paciente",
            paciente=self.paciente,
            detalle="Listado clínico consultado.",
        )
        context["filtros_form"] = self.filtros_form
        context["filtros_querystring"] = query_params.urlencode()
        context["puede_crear_historia_clinica"] = puede_crear_historia_de_paciente(
            self.request.user,
            self.paciente,
        )
        return context


class HistoriaClinicaCreateView(PacienteHistoriaClinicaMixin, CreateView):
    model = HistoriaClinica
    form_class = HistoriaClinicaForm
    template_name = "historias/historia_clinica_form.html"
    requiere_permiso_creacion = True

    def get_success_url(self):
        return reverse(
            "historias:detalle",
            kwargs={"pk": self.object.pk},
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["titulo"] = "Nueva entrada de historia clínica"
        context["subtitulo"] = f"Registro clínico de {self.paciente}."
        context["texto_boton"] = "Guardar entrada"
        context["url_cancelar"] = reverse(
            "historias:lista_paciente",
            kwargs={"paciente_pk": self.paciente.pk},
        )
        context["mostrar_odontograma_en_form"] = settings.ODONTOGRAMA_FEATURE_ENABLED

        if not settings.ODONTOGRAMA_FEATURE_ENABLED:
            return context

        odontograma = obtener_o_crear_odontograma(self.paciente)
        context["odontograma"] = odontograma
        context["filas_odontograma"] = construir_filas_odontograma(odontograma)
        context["estado_form"] = EstadoDentalForm()
        context["leyenda_colores"] = construir_leyenda_colores()
        context["puede_editar_odontograma"] = puede_editar_odontograma(
            self.request.user,
            self.paciente,
        )
        context["odontograma_titulo"] = "Odontograma de la entrada"
        context["odontograma_mostrar_historial"] = False
        context["odontograma_save_mode"] = "deferred"
        return context

    def form_valid(self, form):
        odontologo = obtener_odontologo_del_usuario(self.request.user)

        if odontologo is None:
            raise PermissionDenied("Solo un odontólogo puede cargar historia clínica.")

        with transaction.atomic():
            self.object = form.save(commit=False)
            self.object.paciente = self.paciente
            self.object.odontologo = odontologo
            self.object.creado_por = self.request.user
            self.object.actualizado_por = self.request.user
            self.object.save()
            form.guardar_adjuntos(self.object, self.request.user)
            if settings.ODONTOGRAMA_FEATURE_ENABLED:
                odontograma = obtener_o_crear_odontograma(self.paciente)
                form.guardar_estados_odontograma(
                    self.object,
                    odontograma,
                    self.request.user,
                )

        _registrar_evento_clinico(
            self.request,
            "crear_historia",
            historia=self.object,
            paciente=self.paciente,
            detalle="Entrada clínica creada.",
        )
        messages.success(self.request, "Entrada de historia clínica creada correctamente.")
        return HttpResponseRedirect(self.get_success_url())


class HistoriaClinicaDetailView(HistoriaClinicaOdontologoRequeridoMixin, DetailView):
    model = HistoriaClinica
    template_name = "historias/historia_clinica_detail.html"
    context_object_name = "historia"

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = limitar_historias_clinicas_para_request(queryset, self.request)
        return queryset.select_related(
            "paciente",
            "odontologo",
            "odontologo__usuario",
            "creado_por",
            "actualizado_por",
        ).prefetch_related(
            "adjuntos",
            "adjuntos__subido_por",
            Prefetch(
                "estados_dentales",
                queryset=EstadoDental.objects.select_related(
                    "odontologo",
                    "odontologo__usuario",
                    "registrado_por",
                ).order_by("diente", "cara", "-creado_en"),
            ),
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        _registrar_evento_clinico(
            self.request,
            "ver_detalle_historia",
            historia=self.object,
            paciente=self.object.paciente,
            detalle="Detalle clínico consultado.",
        )
        context["puede_editar_historia"] = puede_editar_historia_clinica(
            self.request.user,
            self.object,
        )
        context["mostrar_odontograma"] = settings.ODONTOGRAMA_FEATURE_ENABLED
        return context


class HistoriaClinicaUpdateView(HistoriaClinicaOdontologoRequeridoMixin, UpdateView):
    model = HistoriaClinica
    form_class = HistoriaClinicaForm
    template_name = "historias/historia_clinica_form.html"
    context_object_name = "historia"

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = limitar_historias_clinicas_para_request(
            queryset,
            self.request,
            lectura=False,
        )
        return queryset.select_related("paciente", "odontologo", "odontologo__usuario")

    def get_object(self, queryset=None):
        historia = super().get_object(queryset)

        if not puede_editar_historia_clinica(self.request.user, historia):
            raise PermissionDenied("Solo el odontólogo responsable puede editar esta entrada.")

        return historia

    def get_success_url(self):
        return reverse("historias:detalle", kwargs={"pk": self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["paciente"] = self.object.paciente
        context["titulo"] = "Editar entrada de historia clínica"
        context["subtitulo"] = f"Registro clínico de {self.object.paciente}."
        context["texto_boton"] = "Guardar cambios"
        context["url_cancelar"] = self.get_success_url()
        return context

    def form_valid(self, form):
        form.instance.actualizado_por = self.request.user
        response = super().form_valid(form)
        form.guardar_adjuntos(self.object, self.request.user)
        _registrar_evento_clinico(
            self.request,
            "editar_historia",
            historia=self.object,
            paciente=self.object.paciente,
            detalle="Entrada clínica modificada.",
        )
        messages.success(self.request, "Entrada de historia clínica actualizada correctamente.")
        return response


class HistoriaClinicaAdjuntoDownloadView(HistoriaClinicaOdontologoRequeridoMixin, View):
    def get(self, request, pk):
        adjunto = get_object_or_404(
            self._get_queryset(request),
            pk=pk,
        )
        _registrar_evento_clinico(
            request,
            "abrir_adjunto",
            historia=adjunto.historia,
            paciente=adjunto.historia.paciente,
            detalle=f"Adjunto clínico abierto: {adjunto.pk}.",
        )

        return FileResponse(
            adjunto.archivo.open("rb"),
            as_attachment=False,
            filename=adjunto.nombre_archivo,
        )

    @staticmethod
    def _get_queryset(request):
        historias_visibles = limitar_historias_clinicas_para_request(
            HistoriaClinica.objects.all(),
            request,
        )
        return HistoriaClinicaAdjunto.objects.filter(
            historia__in=historias_visibles,
        ).select_related(
            "historia",
            "historia__paciente",
            "historia__odontologo",
            "historia__odontologo__usuario",
            "subido_por",
        )


def _registrar_evento_clinico(request, accion, paciente=None, historia=None, detalle=""):
    mapa_acciones = {
        "creacion_denegada_paciente_no_asociado": AccesoClinicoAuditoria.Accion.CREAR_HISTORIA,
        "ver_historia_paciente": AccesoClinicoAuditoria.Accion.VER_HISTORIA,
        "ver_detalle_historia": AccesoClinicoAuditoria.Accion.VER_DETALLE_HISTORIA,
        "crear_historia": AccesoClinicoAuditoria.Accion.CREAR_HISTORIA,
        "editar_historia": AccesoClinicoAuditoria.Accion.EDITAR_HISTORIA,
        "abrir_adjunto": AccesoClinicoAuditoria.Accion.ABRIR_ADJUNTO,
    }
    resultado = (
        AccesoClinicoAuditoria.Resultado.DENEGADO
        if "denegada" in accion
        else AccesoClinicoAuditoria.Resultado.PERMITIDO
    )
    politica = (
        obtener_politica_escritura(request.user, paciente)
        if accion in {"crear_historia", "editar_historia", "creacion_denegada_paciente_no_asociado"}
        else obtener_politica_lectura(request.user, paciente, request=request)
    )

    registrar_evento_acceso_clinico(
        request=request,
        accion=mapa_acciones.get(accion, AccesoClinicoAuditoria.Accion.VER_HISTORIA),
        resultado=resultado,
        politica=politica or AccesoClinicoAuditoria.Politica.SIN_PERMISO,
        paciente=paciente,
        historia=historia,
        motivo=detalle,
    )
