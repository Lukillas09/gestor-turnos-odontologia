import logging

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, OuterRef, Prefetch, Q, Subquery
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DetailView, FormView, ListView, UpdateView, View

from historias.access_policy import (
    finalizar_acceso_clinico_emergencia,
    iniciar_acceso_clinico_emergencia,
    limitar_historias_clinicas_para_request,
    obtener_politica_lectura,
    puede_editar_ficha_odontologica,
    registrar_evento_acceso_clinico,
    usuario_puede_iniciar_acceso_emergencia,
)
from historias.models import HistoriaClinica, HistoriaClinicaAdjunto
from historias.permissions import (
    puede_crear_historia_de_paciente,
    puede_ver_historia_de_paciente,
)
from historias.models import AccesoClinicoAuditoria
from turnos.models import Turno

from usuarios.mixins import (
    ArchivarPacientesRequeridoMixin,
    GestionConsultorioRequeridaMixin,
    VerPacientesRequeridoMixin,
)
from usuarios.roles import (
    limitar_pacientes_por_usuario,
    puede_archivar_pacientes,
    puede_gestionar_historias_clinicas,
)

from .forms import (
    AccesoClinicoEmergenciaForm,
    FichaOdontologicaForm,
    PacienteArchiveForm,
    PacienteDerivacionForm,
    PacienteForm,
    PacienteReactivateForm,
)
from .models import FichaOdontologica, Paciente, PacienteOdontologo
from .services import (
    archivar_paciente,
    asignar_paciente_a_odontologo,
    puede_derivar_paciente,
    reactivar_paciente,
)


logger = logging.getLogger(__name__)


class PacienteListView(VerPacientesRequeridoMixin, ListView):
    model = Paciente
    template_name = "pacientes/paciente_list.html"
    context_object_name = "pacientes"
    paginate_by = 10

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = limitar_pacientes_por_usuario(queryset, self.request.user)
        estado = self.request.GET.get("estado", "activos")

        if estado == "archivados" and self._puede_ver_archivados():
            queryset = queryset.archivados()
        else:
            queryset = queryset.activos()

        busqueda = self.request.GET.get("q", "").strip()
        ultimo_turno = Turno.objects.filter(paciente=OuterRef("pk")).order_by(
            "-fecha",
            "-hora_inicio",
        )

        if busqueda:
            queryset = queryset.filter(
                Q(nombre__icontains=busqueda)
                | Q(apellido__icontains=busqueda)
                | Q(documento__icontains=busqueda)
                | Q(telefono__icontains=busqueda)
                | Q(email__icontains=busqueda)
                | Q(localidad__icontains=busqueda)
                | Q(obra_social__icontains=busqueda)
            )

        return (
            queryset.only(
                "id",
                "nombre",
                "apellido",
                "documento",
                "telefono",
                "email",
                "obra_social",
                "activo",
                "archivado_en",
            )
            .annotate(
                ultimo_turno_fecha=Subquery(ultimo_turno.values("fecha")[:1]),
                ultimo_turno_hora_inicio=Subquery(
                    ultimo_turno.values("hora_inicio")[:1],
                ),
                ultimo_turno_estado=Subquery(ultimo_turno.values("estado")[:1]),
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["busqueda"] = self.request.GET.get("q", "").strip()
        context["estado_actual"] = (
            self.request.GET.get("estado", "activos")
            if self._puede_ver_archivados()
            else "activos"
        )
        context["puede_ver_archivados"] = self._puede_ver_archivados()
        estados_turno = dict(Turno.Estado.choices)

        for paciente in context["pacientes"]:
            paciente.ultimo_turno_resumen = (
                {
                    "fecha": paciente.ultimo_turno_fecha,
                    "hora_inicio": paciente.ultimo_turno_hora_inicio,
                    "estado": paciente.ultimo_turno_estado,
                    "estado_label": estados_turno.get(paciente.ultimo_turno_estado, ""),
                }
                if paciente.ultimo_turno_fecha
                else None
            )

        return context

    def _puede_ver_archivados(self):
        return puede_archivar_pacientes(self.request.user)


class PacienteCreateView(GestionConsultorioRequeridaMixin, CreateView):
    model = Paciente
    form_class = PacienteForm
    template_name = "pacientes/paciente_form.html"
    success_url = reverse_lazy("pacientes:lista")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["titulo"] = "Nuevo paciente"
        context["subtitulo"] = "Carga de datos personales y de contacto."
        context["texto_boton"] = "Guardar paciente"
        context["url_cancelar"] = reverse_lazy("pacientes:lista")
        return context

    def form_valid(self, form):
        messages.success(self.request, "Paciente creado correctamente.")
        return super().form_valid(form)


class PacienteDetailView(VerPacientesRequeridoMixin, DetailView):
    model = Paciente
    template_name = "pacientes/paciente_detail.html"
    context_object_name = "paciente"

    def get_queryset(self):
        queryset = limitar_pacientes_por_usuario(
            super().get_queryset(),
            self.request.user,
        )
        return (
            queryset
            .prefetch_related(
                Prefetch(
                    "odontologos_asociados",
                    queryset=PacienteOdontologo.objects.filter(activo=True).select_related(
                        "odontologo",
                        "odontologo__usuario",
                        "asignado_por",
                    ),
                )
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        paciente = self.object
        hoy = timezone.localdate()
        ahora = timezone.localtime().time()
        puede_ver_historia = (
            puede_gestionar_historias_clinicas(self.request.user)
            and puede_ver_historia_de_paciente(
                self.request.user,
                paciente,
                request=self.request,
            )
        )
        politica_lectura_clinica = obtener_politica_lectura(
            self.request.user,
            paciente,
            request=self.request,
        )
        puede_crear_historia = puede_crear_historia_de_paciente(
            self.request.user,
            paciente,
        )
        puede_derivar = puede_derivar_paciente(self.request.user, paciente)
        puede_editar_ficha = puede_editar_ficha_odontologica(self.request.user, paciente)
        puede_iniciar_emergencia = (
            not politica_lectura_clinica
            and usuario_puede_iniciar_acceso_emergencia(self.request.user)
        )

        turnos = paciente.turnos.select_related("odontologo", "odontologo__usuario")
        turnos_activos = turnos.filter(
            estado__in=[Turno.Estado.PENDIENTE, Turno.Estado.CONFIRMADO],
        )
        cantidad_turnos_activos = turnos_activos.count()
        proximo_turno = (
            turnos_activos.filter(Q(fecha__gt=hoy) | Q(fecha=hoy, hora_inicio__gte=ahora))
            .order_by("fecha", "hora_inicio")
            .first()
        )
        ultimo_turno = turnos.order_by("-fecha", "-hora_inicio").first()
        turnos_recientes = list(turnos.order_by("-fecha", "-hora_inicio")[:5])

        historias_recientes = []
        ultima_historia = None
        cantidad_historias = 0
        cantidad_adjuntos = 0
        ficha_odontologica = None
        alertas_clinicas = []
        indicadores_ficha = []
        detalle_ficha = []

        if puede_ver_historia:
            ficha_odontologica = self._obtener_ficha_odontologica(paciente)
            alertas_clinicas = self._obtener_alertas_clinicas(ficha_odontologica)
            indicadores_ficha = self._obtener_indicadores_ficha(ficha_odontologica)
            detalle_ficha = self._obtener_detalle_ficha(ficha_odontologica)
            historias_visibles = limitar_historias_clinicas_para_request(
                HistoriaClinica.objects.filter(paciente=paciente),
                self.request,
            )
            cantidad_historias = historias_visibles.count()
            cantidad_adjuntos = HistoriaClinicaAdjunto.objects.filter(
                historia__in=historias_visibles,
            ).count()
            historias_recientes = list(
                historias_visibles.select_related("odontologo", "odontologo__usuario")
                .annotate(cantidad_adjuntos=Count("adjuntos"))
                .order_by("-fecha", "-creado_en")[:3]
            )
            ultima_historia = historias_recientes[0] if historias_recientes else None
            registrar_evento_acceso_clinico(
                request=self.request,
                accion=AccesoClinicoAuditoria.Accion.VER_PACIENTE,
                resultado=AccesoClinicoAuditoria.Resultado.PERMITIDO,
                politica=politica_lectura_clinica,
                paciente=paciente,
                motivo="Detalle de paciente con datos clinicos consultado.",
            )

        context.update(
            {
                "edad_paciente": self._calcular_edad(paciente.fecha_nacimiento, hoy),
                "ficha_odontologica": ficha_odontologica,
                "alertas_clinicas": alertas_clinicas,
                "indicadores_ficha": indicadores_ficha,
                "detalle_ficha_odontologica": detalle_ficha,
                "datos_administrativos": self._obtener_datos_administrativos(
                    paciente,
                    hoy,
                ),
                "turnos_recientes": turnos_recientes,
                "turnos_pendientes_o_confirmados": cantidad_turnos_activos,
                "proximo_turno": proximo_turno,
                "ultimo_turno": ultimo_turno,
                "puede_ver_historia_clinica": puede_ver_historia,
                "puede_crear_historia_clinica": puede_crear_historia,
                "puede_editar_ficha_odontologica": puede_editar_ficha,
                "puede_derivar_paciente": puede_derivar,
                "puede_iniciar_acceso_clinico_emergencia": puede_iniciar_emergencia,
                "politica_lectura_clinica": politica_lectura_clinica,
                "paciente_archivado": not paciente.activo,
                "odontologos_asociados": list(paciente.odontologos_asociados.all()),
                "historias_recientes": historias_recientes,
                "ultima_historia_clinica": ultima_historia,
                "cantidad_historias_clinicas": cantidad_historias,
                "cantidad_adjuntos_clinicos": cantidad_adjuntos,
                "resumen_rapido": self._obtener_resumen_rapido(
                    cantidad_turnos_activos,
                    proximo_turno,
                    ultimo_turno,
                    ultima_historia,
                    cantidad_historias,
                    cantidad_adjuntos,
                    puede_ver_historia,
                ),
            }
        )
        return context

    def _obtener_ficha_odontologica(self, paciente):
        try:
            return paciente.ficha_odontologica
        except FichaOdontologica.DoesNotExist:
            return None

    @staticmethod
    def _calcular_edad(fecha_nacimiento, hoy):
        if not fecha_nacimiento or fecha_nacimiento > hoy:
            return None

        edad = hoy.year - fecha_nacimiento.year

        if (hoy.month, hoy.day) < (fecha_nacimiento.month, fecha_nacimiento.day):
            edad -= 1

        return edad

    def _obtener_alertas_clinicas(self, ficha):
        if not ficha:
            return []

        alertas = []
        campos_texto = [
            ("Alergias", ficha.alergias, "danger"),
            ("Medicación actual", ficha.medicacion_actual, "warning"),
            ("Enfermedades relevantes", ficha.enfermedades_relevantes, "warning"),
        ]

        for etiqueta, valor, estado in campos_texto:
            if valor:
                alertas.append(
                    {
                        "etiqueta": etiqueta,
                        "valor": valor,
                        "estado": estado,
                    }
                )

        for etiqueta, valor in self._obtener_respuestas_clinicas(ficha):
            if valor:
                alertas.append(
                    {
                        "etiqueta": etiqueta,
                        "valor": self._mostrar_respuesta_clinica(valor),
                        "estado": self._estado_respuesta_clinica(valor),
                    }
                )

        return alertas

    def _obtener_indicadores_ficha(self, ficha):
        if not ficha:
            return [
                {"etiqueta": etiqueta, "valor": "Sin datos", "estado": "neutral"}
                for etiqueta in (
                    "Alergias",
                    "Diabetes",
                    "Hipertensión",
                    "Problemas cardíacos",
                    "Embarazo",
                )
            ]

        indicadores = [
            {
                "etiqueta": "Alergias",
                "valor": ficha.alergias or "Sin datos",
                "estado": "danger" if ficha.alergias else "neutral",
            }
        ]

        for etiqueta, valor in self._obtener_respuestas_clinicas(ficha):
            indicadores.append(
                {
                    "etiqueta": etiqueta,
                    "valor": self._mostrar_respuesta_clinica(valor),
                    "estado": self._estado_respuesta_clinica(valor),
                }
            )

        return indicadores

    @staticmethod
    def _obtener_respuestas_clinicas(ficha):
        return [
            ("Diabetes", ficha.diabetes),
            ("Hipertensión", ficha.hipertension),
            ("Problemas cardíacos", ficha.problemas_cardiacos),
            ("Embarazo", ficha.embarazo),
        ]

    @staticmethod
    def _mostrar_respuesta_clinica(valor):
        if valor == FichaOdontologica.RespuestaClinica.SI:
            return "Sí"

        if valor == FichaOdontologica.RespuestaClinica.NO:
            return "No"

        return "Sin datos"

    @staticmethod
    def _estado_respuesta_clinica(valor):
        if valor == FichaOdontologica.RespuestaClinica.SI:
            return "danger"

        if valor == FichaOdontologica.RespuestaClinica.NO:
            return "success"

        return "neutral"

    @staticmethod
    def _obtener_detalle_ficha(ficha):
        if not ficha:
            return []

        return [
            ("Antecedentes médicos", ficha.antecedentes_medicos),
            ("Medicación actual", ficha.medicacion_actual),
            ("Enfermedades relevantes", ficha.enfermedades_relevantes),
            ("Observaciones generales", ficha.observaciones_generales),
        ]

    def _obtener_datos_administrativos(self, paciente, hoy):
        edad = self._calcular_edad(paciente.fecha_nacimiento, hoy)
        fecha_nacimiento = (
            paciente.fecha_nacimiento.strftime("%d/%m/%Y")
            if paciente.fecha_nacimiento
            else "-"
        )
        return [
            {
                "titulo": "Identificación",
                "campos": [
                    ("DNI", paciente.documento or "-"),
                    ("Fecha de nacimiento", fecha_nacimiento),
                    ("Edad", f"{edad} años" if edad is not None else "-"),
                    ("Sexo / género", paciente.get_genero_display() or "-"),
                ],
            },
            {
                "titulo": "Contacto",
                "campos": [
                    ("Teléfono", paciente.telefono or "-"),
                    ("Email", paciente.email or "-"),
                    ("Contacto de emergencia", paciente.contacto_emergencia or "-"),
                ],
            },
            {
                "titulo": "Cobertura",
                "campos": [
                    ("Obra social", paciente.obra_social or "-"),
                    ("Número de afiliado", paciente.numero_afiliado or "-"),
                ],
            },
            {
                "titulo": "Dirección",
                "campos": [
                    ("Domicilio", paciente.domicilio or "-"),
                    ("Localidad", paciente.localidad or "-"),
                ],
            },
        ]

    @staticmethod
    def _obtener_resumen_rapido(
        turnos_activos,
        proximo_turno,
        ultimo_turno,
        ultima_historia,
        cantidad_historias,
        cantidad_adjuntos,
        puede_ver_historia,
    ):
        resumen = [
            {
                "etiqueta": "Turnos activos",
                "valor": turnos_activos,
                "detalle": "Pendientes o confirmados",
            },
            {
                "etiqueta": "Próximo turno",
                "valor": (
                    f"{proximo_turno.fecha:%d/%m} {proximo_turno.hora_inicio:%H:%M}"
                    if proximo_turno
                    else "Sin próximo turno"
                ),
                "detalle": proximo_turno.motivo if proximo_turno else "",
            },
            {
                "etiqueta": "Último turno",
                "valor": (
                    f"{ultimo_turno.fecha:%d/%m} {ultimo_turno.hora_inicio:%H:%M}"
                    if ultimo_turno
                    else "Sin turnos"
                ),
                "detalle": ultimo_turno.get_estado_display() if ultimo_turno else "",
            },
        ]

        if puede_ver_historia:
            resumen.extend(
                [
                    {
                        "etiqueta": "Última entrada clínica",
                        "valor": (
                            f"{ultima_historia.fecha:%d/%m/%Y}"
                            if ultima_historia
                            else "Sin entradas clínicas"
                        ),
                        "detalle": ultima_historia.motivo_consulta if ultima_historia else "",
                    },
                    {
                        "etiqueta": "Entradas clínicas",
                        "valor": cantidad_historias,
                        "detalle": "Registros visibles",
                    },
                    {
                        "etiqueta": "Adjuntos clínicos",
                        "valor": cantidad_adjuntos,
                        "detalle": "Archivos cargados",
                    },
                ]
            )

        return resumen


class FichaOdontologicaUpdateView(VerPacientesRequeridoMixin, View):
    template_name = "pacientes/ficha_odontologica_form.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated or not self.test_func():
            return super().dispatch(request, *args, **kwargs)

        self.paciente = get_object_or_404(
            limitar_pacientes_por_usuario(Paciente.objects.all(), request.user),
            pk=self.kwargs["pk"],
        )

        if not puede_editar_ficha_odontologica(request.user, self.paciente):
            registrar_evento_acceso_clinico(
                request=request,
                accion=AccesoClinicoAuditoria.Accion.EDITAR_FICHA,
                resultado=AccesoClinicoAuditoria.Resultado.DENEGADO,
                politica=AccesoClinicoAuditoria.Politica.SIN_PERMISO,
                paciente=self.paciente,
                motivo="Intento de editar ficha odontologica sin permiso.",
            )
            raise PermissionDenied("No tenés permiso para editar la ficha odontológica.")

        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        paciente_form, ficha_form = self._construir_formularios()
        return render(
            request,
            self.template_name,
            self._obtener_contexto(paciente_form, ficha_form),
        )

    def post(self, request, *args, **kwargs):
        paciente_form, ficha_form = self._construir_formularios(data=request.POST)

        if paciente_form.is_valid() and ficha_form.is_valid():
            with transaction.atomic():
                paciente_form.save()
                ficha = ficha_form.save(commit=False)
                ficha.paciente = self.paciente
                ficha.actualizado_por = request.user
                ficha.save()

            registrar_evento_acceso_clinico(
                request=request,
                accion=AccesoClinicoAuditoria.Accion.EDITAR_FICHA,
                resultado=AccesoClinicoAuditoria.Resultado.PERMITIDO,
                politica=AccesoClinicoAuditoria.Politica.ASOCIACION_ACTIVA,
                paciente=self.paciente,
                motivo="Ficha odontologica actualizada.",
            )
            messages.success(request, "Ficha odontológica actualizada correctamente.")
            return redirect(self.get_success_url())

        messages.error(request, "Revisá los datos marcados antes de guardar la ficha.")
        return render(
            request,
            self.template_name,
            self._obtener_contexto(paciente_form, ficha_form),
        )

    def _construir_formularios(self, data=None):
        ficha = self._obtener_ficha_odontologica()
        return (
            PacienteForm(data=data, instance=self.paciente, prefix="paciente"),
            FichaOdontologicaForm(data=data, instance=ficha, prefix="ficha"),
        )

    def _obtener_ficha_odontologica(self):
        try:
            return self.paciente.ficha_odontologica
        except FichaOdontologica.DoesNotExist:
            return FichaOdontologica(paciente=self.paciente)

    def get_success_url(self):
        return reverse_lazy("pacientes:detalle", kwargs={"pk": self.paciente.pk})

    def _obtener_contexto(self, paciente_form, ficha_form):
        return {
            "paciente": self.paciente,
            "ficha": ficha_form.instance,
            "paciente_form": paciente_form,
            "ficha_form": ficha_form,
            "titulo": "Ficha odontológica",
            "subtitulo": (
                "Información general, contacto, cobertura y antecedentes clínicos."
            ),
        }


class PacienteDerivarView(VerPacientesRequeridoMixin, FormView):
    form_class = PacienteDerivacionForm
    template_name = "pacientes/paciente_derivar_form.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated or not self.test_func():
            return super().dispatch(request, *args, **kwargs)

        self.paciente = get_object_or_404(
            limitar_pacientes_por_usuario(Paciente.objects.all(), request.user),
            pk=self.kwargs["pk"],
        )

        if not puede_derivar_paciente(request.user, self.paciente):
            raise PermissionDenied("No tenés permiso para derivar este paciente.")

        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["paciente"] = self.paciente
        return kwargs

    def get_success_url(self):
        return reverse_lazy("pacientes:detalle", kwargs={"pk": self.paciente.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["paciente"] = self.paciente
        context["titulo"] = "Derivar paciente"
        context["subtitulo"] = "Habilitá a otro odontólogo para atender y cargar historia clínica."
        context["url_cancelar"] = self.get_success_url()
        return context

    def form_valid(self, form):
        asignar_paciente_a_odontologo(
            paciente=self.paciente,
            odontologo=form.cleaned_data["odontologo"],
            usuario=self.request.user,
            motivo=form.cleaned_data["motivo"],
        )
        messages.success(
            self.request,
            "Paciente derivado correctamente. El odontólogo destino ya puede atenderlo.",
        )
        return redirect(self.get_success_url())


class PacienteUpdateView(GestionConsultorioRequeridaMixin, UpdateView):
    model = Paciente
    form_class = PacienteForm
    template_name = "pacientes/paciente_form.html"

    def get_queryset(self):
        return limitar_pacientes_por_usuario(
            super().get_queryset(),
            self.request.user,
        )

    def get_success_url(self):
        return reverse_lazy("pacientes:detalle", kwargs={"pk": self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["titulo"] = "Editar paciente"
        context["subtitulo"] = "Actualización de datos personales y de contacto."
        context["texto_boton"] = "Guardar cambios"
        context["url_cancelar"] = self.get_success_url()
        return context

    def form_valid(self, form):
        messages.success(self.request, "Paciente actualizado correctamente.")
        return super().form_valid(form)


class PacienteArchiveView(ArchivarPacientesRequeridoMixin, FormView):
    form_class = PacienteArchiveForm
    template_name = "pacientes/paciente_archive_form.html"
    success_url = reverse_lazy("pacientes:lista")
    estados_que_bloquean_archivo = [
        Turno.Estado.PENDIENTE,
        Turno.Estado.CONFIRMADO,
    ]

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated or not self.test_func():
            return super().dispatch(request, *args, **kwargs)

        self.paciente = self.get_object()
        return super().dispatch(request, *args, **kwargs)

    def get_object(self):
        return get_object_or_404(
            limitar_pacientes_por_usuario(Paciente.objects.all(), self.request.user),
            pk=self.kwargs["pk"],
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["paciente"] = self.paciente
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["paciente"] = self.paciente
        context["cantidad_historias_clinicas"] = self._cantidad_historias_clinicas()
        context["cantidad_adjuntos_clinicos"] = self._cantidad_adjuntos_clinicos()
        context["tiene_ficha_odontologica"] = self._tiene_ficha_odontologica()
        context["turnos_bloqueantes"] = self._turnos_que_bloquean_archivo()
        return context

    def form_valid(self, form):
        nombre_completo = self.paciente.nombre_completo

        if self._turnos_que_bloquean_archivo().exists():
            form.add_error(
                None,
                "No se puede archivar el paciente porque tiene turnos pendientes o confirmados.",
            )
            messages.error(
                self.request,
                "No se puede archivar el paciente porque tiene turnos pendientes o confirmados.",
            )
            return super().form_invalid(form)

        try:
            archivar_paciente(
                self.paciente,
                self.request.user,
                form.cleaned_data["motivo"],
            )
        except Exception as error:
            logger.exception("No se pudo archivar el paciente.")
            form.add_error(
                None,
                error.messages[0] if hasattr(error, "messages") else "No se pudo archivar el paciente.",
            )
            messages.error(
                self.request,
                "No se pudo archivar el paciente.",
            )
            return super().form_invalid(form)

        messages.success(self.request, f"Paciente {nombre_completo} archivado correctamente.")

        return super().form_valid(form)

    def _turnos_que_bloquean_archivo(self):
        return self.paciente.turnos.filter(
            estado__in=self.estados_que_bloquean_archivo
        )

    def _tiene_datos_clinicos(self):
        return self._cantidad_historias_clinicas() > 0 or self._tiene_ficha_odontologica()

    def _cantidad_historias_clinicas(self):
        return self.paciente.historias_clinicas.count()

    def _cantidad_adjuntos_clinicos(self):
        return HistoriaClinicaAdjunto.objects.filter(
            historia__paciente=self.paciente,
        ).count()

    def _tiene_ficha_odontologica(self):
        return FichaOdontologica.objects.filter(paciente=self.paciente).exists()


class PacienteReactivateView(ArchivarPacientesRequeridoMixin, FormView):
    form_class = PacienteReactivateForm
    template_name = "pacientes/paciente_reactivate_form.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated or not self.test_func():
            return super().dispatch(request, *args, **kwargs)

        self.paciente = get_object_or_404(
            limitar_pacientes_por_usuario(Paciente.objects.all(), self.request.user),
            pk=self.kwargs["pk"],
        )
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        return reverse_lazy("pacientes:detalle", kwargs={"pk": self.paciente.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["paciente"] = self.paciente
        return context

    def form_valid(self, form):
        try:
            reactivar_paciente(
                self.paciente,
                self.request.user,
                form.cleaned_data["motivo"],
            )
        except Exception as error:
            form.add_error(
                None,
                error.messages[0] if hasattr(error, "messages") else "No se pudo reactivar el paciente.",
            )
            return super().form_invalid(form)

        messages.success(self.request, "Paciente reactivado correctamente.")
        return super().form_valid(form)


class PacienteEmergenciaClinicaStartView(ArchivarPacientesRequeridoMixin, FormView):
    form_class = AccesoClinicoEmergenciaForm
    template_name = "pacientes/acceso_clinico_emergencia_form.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated or not self.test_func():
            return super().dispatch(request, *args, **kwargs)

        if not usuario_puede_iniciar_acceso_emergencia(request.user):
            raise PermissionDenied("Solo un superusuario puede iniciar acceso clinico de emergencia.")

        self.paciente = get_object_or_404(Paciente.objects.all(), pk=self.kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        return reverse("pacientes:detalle", kwargs={"pk": self.paciente.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["paciente"] = self.paciente
        return context

    def form_valid(self, form):
        motivo = form.cleaned_data["motivo"]
        iniciar_acceso_clinico_emergencia(self.request, self.paciente, motivo)
        registrar_evento_acceso_clinico(
            request=self.request,
            accion=AccesoClinicoAuditoria.Accion.INICIAR_EMERGENCIA,
            resultado=AccesoClinicoAuditoria.Resultado.PERMITIDO,
            politica=AccesoClinicoAuditoria.Politica.EMERGENCIA,
            paciente=self.paciente,
            motivo=motivo,
        )
        messages.warning(
            self.request,
            "Acceso clinico de emergencia activo durante 15 minutos. Todas las lecturas quedan auditadas.",
        )
        return super().form_valid(form)


class PacienteEmergenciaClinicaEndView(View):
    def post(self, request, *args, **kwargs):
        registrar_evento_acceso_clinico(
            request=request,
            accion=AccesoClinicoAuditoria.Accion.FINALIZAR_EMERGENCIA,
            resultado=AccesoClinicoAuditoria.Resultado.PERMITIDO,
            politica=AccesoClinicoAuditoria.Politica.EMERGENCIA,
            motivo="Acceso clinico de emergencia finalizado manualmente.",
        )
        finalizar_acceso_clinico_emergencia(request)
        messages.success(request, "Acceso clinico de emergencia finalizado.")
        return redirect("inicio")


PacienteDeleteView = PacienteArchiveView
