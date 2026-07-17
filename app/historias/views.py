from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Prefetch, Q
from django.http import FileResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
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

from .access_policy import (
    limitar_historias_clinicas_para_request,
    limitar_pacientes_clinicos_para_request,
    obtener_politica_escritura,
    obtener_politica_lectura,
    registrar_evento_acceso_clinico,
)
from .exports import exportar_historia_completa
from .forms import (
    ExportarHistoriaClinicaForm,
    FinalizarHistoriaClinicaForm,
    HistoriaClinicaEnmiendaForm,
    HistoriaClinicaFiltroForm,
    HistoriaClinicaForm,
)
from .models import (
    AccesoClinicoAuditoria,
    HistoriaClinica,
    HistoriaClinicaAdjunto,
    HistoriaClinicaEnmienda,
    HistoriaClinicaVersion,
)
from .permissions import (
    puede_crear_historia_de_paciente,
    puede_editar_historia_clinica,
    puede_enmendar_historia_clinica,
)
from .services import (
    HistoriaClinicaFinalizadaError,
    actualizar_historia_borrador,
    crear_enmienda_historia,
    crear_historia_borrador,
    finalizar_historia_clinica,
    verificar_integridad_historia_auditada,
)


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
            _registrar_evento(
                request,
                accion=AccesoClinicoAuditoria.Accion.CREAR_BORRADOR,
                resultado=AccesoClinicoAuditoria.Resultado.DENEGADO,
                paciente=self.paciente,
                motivo="Intento de crear un borrador fuera del alcance de escritura.",
                escritura=True,
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
                "finalizada_por",
            )
            .annotate(
                cantidad_adjuntos=Count("adjuntos", distinct=True),
                cantidad_versiones=Count("versiones", distinct=True),
                cantidad_enmiendas=Count("enmiendas", distinct=True),
            )
            .order_by("-numero_asiento", "-fecha_hora_atencion", "-creado_en")
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
        _registrar_evento(
            self.request,
            accion=AccesoClinicoAuditoria.Accion.VER_HISTORIA,
            paciente=self.paciente,
            motivo="Listado clínico consultado.",
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
        return reverse("historias:detalle", kwargs={"pk": self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["titulo"] = "Nueva entrada de historia clínica"
        context["subtitulo"] = f"Registro clínico de {self.paciente}."
        context["texto_boton"] = "Guardar borrador"
        context["url_cancelar"] = reverse(
            "historias:lista_paciente",
            kwargs={"paciente_pk": self.paciente.pk},
        )
        context["mostrar_odontograma_en_form"] = settings.ODONTOGRAMA_FEATURE_ENABLED
        context["es_borrador"] = True

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

        self.object, _ = crear_historia_borrador(
            paciente=self.paciente,
            odontologo=odontologo,
            usuario=self.request.user,
            datos=form.cleaned_data,
            adjuntos=form.cleaned_data.get("adjuntos", []),
            estados_odontograma=form.cleaned_data.get("estados_odontograma", []),
            request=self.request,
        )
        messages.success(
            self.request,
            "Borrador clínico creado. Revisalo y finalizalo cuando esté completo.",
        )
        return HttpResponseRedirect(self.get_success_url())


class HistoriaClinicaDetailView(HistoriaClinicaOdontologoRequeridoMixin, DetailView):
    model = HistoriaClinica
    template_name = "historias/historia_clinica_detail.html"
    context_object_name = "historia"

    def get_queryset(self):
        queryset = limitar_historias_clinicas_para_request(
            super().get_queryset(),
            self.request,
        )
        return queryset.select_related(
            "paciente",
            "odontologo",
            "odontologo__usuario",
            "creado_por",
            "actualizado_por",
            "finalizada_por",
        ).prefetch_related(
            "adjuntos",
            "adjuntos__subido_por",
            "versiones",
            "versiones__creado_por",
            "enmiendas",
            "enmiendas__creado_por",
            "enmiendas__odontologo",
            "enmiendas__odontologo__usuario",
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
        _registrar_evento(
            self.request,
            accion=AccesoClinicoAuditoria.Accion.VER_DETALLE_HISTORIA,
            historia=self.object,
            paciente=self.object.paciente,
            motivo="Detalle clínico consultado.",
        )
        puede_editar = puede_editar_historia_clinica(self.request.user, self.object)
        context["puede_editar_historia"] = puede_editar
        context["puede_finalizar_historia"] = puede_editar
        context["puede_enmendar_historia"] = puede_enmendar_historia_clinica(
            self.request.user,
            self.object,
        )
        context["mostrar_odontograma"] = settings.ODONTOGRAMA_FEATURE_ENABLED
        context["integridad_inicializada"] = bool(self.object.versiones.all())
        return context


class HistoriaClinicaUpdateView(HistoriaClinicaOdontologoRequeridoMixin, UpdateView):
    model = HistoriaClinica
    form_class = HistoriaClinicaForm
    template_name = "historias/historia_clinica_form.html"
    context_object_name = "historia"

    def get_queryset(self):
        return limitar_historias_clinicas_para_request(
            super().get_queryset(),
            self.request,
            lectura=False,
        ).select_related("paciente", "odontologo", "odontologo__usuario")

    def get_object(self, queryset=None):
        historia = super().get_object(queryset)
        if historia.bloqueada_para_edicion or not historia.borrador:
            _registrar_evento(
                self.request,
                accion=AccesoClinicoAuditoria.Accion.INTENTO_EDITAR_FINALIZADA,
                resultado=AccesoClinicoAuditoria.Resultado.DENEGADO,
                historia=historia,
                paciente=historia.paciente,
                motivo="Intento de edición sobre registro finalizado.",
                escritura=True,
            )
            raise PermissionDenied("Una entrada finalizada no puede editarse; agregá una enmienda.")
        if not puede_editar_historia_clinica(self.request.user, historia):
            raise PermissionDenied("Solo el odontólogo responsable puede editar este borrador.")
        return historia

    def get_success_url(self):
        return reverse("historias:detalle", kwargs={"pk": self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["paciente"] = self.object.paciente
        context["titulo"] = "Editar borrador clínico"
        context["subtitulo"] = f"Registro clínico de {self.object.paciente}."
        context["texto_boton"] = "Guardar nueva versión"
        context["url_cancelar"] = self.get_success_url()
        context["mostrar_odontograma_en_form"] = False
        context["es_borrador"] = True
        return context

    def form_valid(self, form):
        try:
            historia, _, hubo_cambios = actualizar_historia_borrador(
                historia=self.object,
                usuario=self.request.user,
                datos=form.cleaned_data,
                motivo_cambio=form.cleaned_data["motivo_cambio"],
                adjuntos=form.cleaned_data.get("adjuntos", []),
                estados_odontograma=form.cleaned_data.get("estados_odontograma", []),
                request=self.request,
            )
        except HistoriaClinicaFinalizadaError as error:
            raise PermissionDenied(error.messages[0]) from error

        self.object = historia
        if hubo_cambios:
            messages.success(self.request, "Borrador actualizado y nueva versión registrada.")
        else:
            messages.info(self.request, "No se detectaron cambios; no se creó una versión vacía.")
        return HttpResponseRedirect(self.get_success_url())


class HistoriaClinicaFinalizarView(HistoriaClinicaOdontologoRequeridoMixin, View):
    template_name = "historias/historia_clinica_finalizar.html"

    def get(self, request, pk):
        historia = self._get_historia(request, pk)
        return render(
            request,
            self.template_name,
            {"historia": historia, "form": FinalizarHistoriaClinicaForm()},
        )

    def post(self, request, pk):
        historia = self._get_historia(request, pk)
        form = FinalizarHistoriaClinicaForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {"historia": historia, "form": form},
                status=400,
            )

        historia, _ = finalizar_historia_clinica(
            historia=historia,
            usuario=request.user,
            request=request,
        )
        messages.success(
            request,
            f"Entrada finalizada como asiento {historia.numero_asiento}. "
            "El original quedó bloqueado.",
        )
        return HttpResponseRedirect(reverse("historias:detalle", kwargs={"pk": historia.pk}))

    @staticmethod
    def _get_historia(request, pk):
        historia = get_object_or_404(
            limitar_historias_clinicas_para_request(
                HistoriaClinica.objects.select_related(
                    "paciente",
                    "odontologo",
                    "odontologo__usuario",
                ),
                request,
                lectura=False,
            ),
            pk=pk,
        )
        if not puede_editar_historia_clinica(request.user, historia):
            raise PermissionDenied("No tenés permiso para finalizar esta entrada.")
        return historia


class HistoriaClinicaEnmiendaCreateView(HistoriaClinicaOdontologoRequeridoMixin, View):
    template_name = "historias/historia_clinica_enmienda_form.html"

    def get(self, request, pk):
        historia = self._get_historia(request, pk)
        return render(
            request,
            self.template_name,
            {"historia": historia, "form": HistoriaClinicaEnmiendaForm()},
        )

    def post(self, request, pk):
        historia = self._get_historia(request, pk)
        form = HistoriaClinicaEnmiendaForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {"historia": historia, "form": form},
                status=400,
            )

        odontologo = obtener_odontologo_del_usuario(request.user)
        if odontologo is None:
            raise PermissionDenied("Solo un odontólogo puede agregar enmiendas.")
        enmienda = crear_enmienda_historia(
            historia=historia,
            usuario=request.user,
            odontologo=odontologo,
            texto=form.cleaned_data["texto"],
            motivo=form.cleaned_data["motivo"],
            request=request,
        )
        messages.success(
            request,
            f"Enmienda {enmienda.numero_enmienda} agregada sin alterar el original.",
        )
        return HttpResponseRedirect(reverse("historias:detalle", kwargs={"pk": historia.pk}))

    @staticmethod
    def _get_historia(request, pk):
        historia = _obtener_historia_visible(request, pk)
        if not puede_enmendar_historia_clinica(request.user, historia):
            raise PermissionDenied("No tenés permiso para enmendar esta entrada.")
        if not historia.versiones.exists():
            raise PermissionDenied(
                "Este registro migrado requiere inicializar su integridad antes de enmendarlo."
            )
        return historia


class HistoriaClinicaVersionDetailView(HistoriaClinicaOdontologoRequeridoMixin, DetailView):
    model = HistoriaClinicaVersion
    template_name = "historias/historia_clinica_version_detail.html"
    context_object_name = "version"

    def get_queryset(self):
        historias_visibles = limitar_historias_clinicas_para_request(
            HistoriaClinica.objects.all(),
            self.request,
        )
        return HistoriaClinicaVersion.objects.filter(
            historia__in=historias_visibles
        ).select_related(
            "historia",
            "historia__paciente",
            "historia__odontologo",
            "historia__odontologo__usuario",
            "creado_por",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        _registrar_evento(
            self.request,
            accion=AccesoClinicoAuditoria.Accion.VER_VERSION,
            historia=self.object.historia,
            paciente=self.object.historia.paciente,
            motivo=f"Versión {self.object.numero_version} consultada.",
        )
        return context


class HistoriaClinicaEnmiendaDetailView(HistoriaClinicaOdontologoRequeridoMixin, DetailView):
    model = HistoriaClinicaEnmienda
    template_name = "historias/historia_clinica_enmienda_detail.html"
    context_object_name = "enmienda"

    def get_queryset(self):
        historias_visibles = limitar_historias_clinicas_para_request(
            HistoriaClinica.objects.all(),
            self.request,
        )
        return HistoriaClinicaEnmienda.objects.filter(
            historia__in=historias_visibles
        ).select_related(
            "historia",
            "historia__paciente",
            "historia__odontologo",
            "historia__odontologo__usuario",
            "odontologo",
            "odontologo__usuario",
            "creado_por",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        _registrar_evento(
            self.request,
            accion=AccesoClinicoAuditoria.Accion.VER_ENMIENDA,
            historia=self.object.historia,
            paciente=self.object.historia.paciente,
            motivo=f"Enmienda {self.object.numero_enmienda} consultada.",
        )
        return context


class HistoriaClinicaVerificarIntegridadView(HistoriaClinicaOdontologoRequeridoMixin, View):
    def post(self, request, pk):
        historia = _obtener_historia_visible(request, pk)
        resultado = verificar_integridad_historia_auditada(
            historia,
            request=request,
            verificar_adjuntos=request.POST.get("verificar_adjuntos") == "1",
        )
        if resultado["valida"]:
            messages.success(request, "La cadena de integridad del asiento es válida.")
        else:
            messages.error(
                request,
                "La verificación detectó inconsistencias. No modifiques el registro "
                "y seguí el procedimiento de incidente.",
            )
        return HttpResponseRedirect(reverse("historias:detalle", kwargs={"pk": historia.pk}))


class HistoriaClinicaExportView(HistoriaClinicaOdontologoRequeridoMixin, View):
    template_name = "historias/historia_clinica_exportar.html"

    def get(self, request, pk):
        historia = _obtener_historia_visible(request, pk)
        return render(
            request,
            self.template_name,
            {"historia": historia, "form": ExportarHistoriaClinicaForm()},
        )

    def post(self, request, pk):
        historia = _obtener_historia_visible(request, pk)
        form = ExportarHistoriaClinicaForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {"historia": historia, "form": form},
                status=400,
            )

        try:
            archivo_zip, nombre = exportar_historia_completa(
                historia_referencia=historia,
                usuario=request.user,
                motivo=form.cleaned_data["motivo"],
                request=request,
            )
        except Exception:
            form.add_error(
                None,
                "No se pudo generar la exportación. El intento quedó auditado.",
            )
            return render(
                request,
                self.template_name,
                {"historia": historia, "form": form},
                status=503,
            )

        response = FileResponse(
            archivo_zip,
            as_attachment=True,
            filename=nombre,
            content_type="application/zip",
        )
        response["X-Content-Type-Options"] = "nosniff"
        return response


class HistoriaClinicaAdjuntoDownloadView(HistoriaClinicaOdontologoRequeridoMixin, View):
    def get(self, request, pk):
        adjunto = get_object_or_404(self._get_queryset(request), pk=pk)
        _registrar_evento(
            request,
            accion=AccesoClinicoAuditoria.Accion.ABRIR_ADJUNTO,
            historia=adjunto.historia,
            paciente=adjunto.historia.paciente,
            adjunto=adjunto,
            motivo=f"Adjunto clínico {adjunto.pk} abierto.",
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


def _obtener_historia_visible(request, pk):
    return get_object_or_404(
        limitar_historias_clinicas_para_request(
            HistoriaClinica.objects.select_related(
                "paciente",
                "odontologo",
                "odontologo__usuario",
            ),
            request,
        ),
        pk=pk,
    )


def _registrar_evento(
    request,
    *,
    accion,
    paciente=None,
    historia=None,
    adjunto=None,
    motivo="",
    resultado=AccesoClinicoAuditoria.Resultado.PERMITIDO,
    escritura=False,
):
    paciente = paciente or (historia.paciente if historia else None)
    if escritura:
        politica = obtener_politica_escritura(request.user, paciente)
    else:
        politica = obtener_politica_lectura(request.user, paciente, request=request)
    registrar_evento_acceso_clinico(
        request=request,
        accion=accion,
        resultado=resultado,
        politica=politica or AccesoClinicoAuditoria.Politica.SIN_PERMISO,
        paciente=paciente,
        historia=historia,
        adjunto=adjunto,
        motivo=motivo,
    )
