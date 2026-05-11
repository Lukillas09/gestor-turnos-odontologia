import logging

from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DetailView, FormView, ListView, UpdateView

from historias.models import HistoriaClinica, HistoriaClinicaAdjunto
from historias.permissions import (
    limitar_historias_clinicas_por_usuario,
    puede_ver_historia_de_paciente,
)
from turnos.models import Turno

from usuarios.mixins import (
    BorrarPacientesRequeridoMixin,
    GestionConsultorioRequeridaMixin,
    VerPacientesRequeridoMixin,
)
from usuarios.roles import puede_gestionar_historias_clinicas

from .forms import FichaOdontologicaForm, PacienteDeleteConfirmationForm, PacienteForm
from .models import FichaOdontologica, Paciente


logger = logging.getLogger(__name__)


class PacienteListView(VerPacientesRequeridoMixin, ListView):
    model = Paciente
    template_name = "pacientes/paciente_list.html"
    context_object_name = "pacientes"
    paginate_by = 20

    def get_queryset(self):
        queryset = super().get_queryset()
        busqueda = self.request.GET.get("q", "").strip()

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

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["busqueda"] = self.request.GET.get("q", "").strip()
        return context


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
        return (
            super()
            .get_queryset()
            .select_related("ficha_odontologica")
            .prefetch_related(
                "turnos",
                "turnos__odontologo",
                "turnos__odontologo__usuario",
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        paciente = self.object
        hoy = timezone.localdate()
        ahora = timezone.localtime().time()
        ficha_odontologica = self._obtener_ficha_odontologica(paciente)
        puede_ver_historia = (
            puede_gestionar_historias_clinicas(self.request.user)
            and puede_ver_historia_de_paciente(self.request.user, paciente)
        )

        turnos = paciente.turnos.select_related("odontologo", "odontologo__usuario")
        turnos_activos = turnos.filter(
            estado__in=[Turno.Estado.PENDIENTE, Turno.Estado.CONFIRMADO],
        )
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

        if puede_ver_historia:
            historias_visibles = limitar_historias_clinicas_por_usuario(
                HistoriaClinica.objects.filter(paciente=paciente),
                self.request.user,
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

        context.update(
            {
                "edad_paciente": self._calcular_edad(paciente.fecha_nacimiento, hoy),
                "ficha_odontologica": ficha_odontologica,
                "alertas_clinicas": self._obtener_alertas_clinicas(ficha_odontologica),
                "indicadores_ficha": self._obtener_indicadores_ficha(ficha_odontologica),
                "detalle_ficha_odontologica": self._obtener_detalle_ficha(
                    ficha_odontologica,
                ),
                "datos_administrativos": self._obtener_datos_administrativos(
                    paciente,
                    hoy,
                ),
                "turnos_recientes": turnos_recientes,
                "turnos_pendientes_o_confirmados": turnos_activos.count(),
                "proximo_turno": proximo_turno,
                "ultimo_turno": ultimo_turno,
                "puede_ver_historia_clinica": puede_ver_historia,
                "historias_recientes": historias_recientes,
                "ultima_historia_clinica": ultima_historia,
                "cantidad_historias_clinicas": cantidad_historias,
                "cantidad_adjuntos_clinicos": cantidad_adjuntos,
                "resumen_rapido": self._obtener_resumen_rapido(
                    turnos_activos.count(),
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


class FichaOdontologicaUpdateView(VerPacientesRequeridoMixin, UpdateView):
    model = FichaOdontologica
    form_class = FichaOdontologicaForm
    template_name = "pacientes/ficha_odontologica_form.html"
    context_object_name = "ficha"

    def dispatch(self, request, *args, **kwargs):
        self.paciente = get_object_or_404(Paciente, pk=self.kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        ficha, _ = FichaOdontologica.objects.get_or_create(paciente=self.paciente)
        return ficha

    def get_success_url(self):
        return reverse_lazy("pacientes:detalle", kwargs={"pk": self.paciente.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["paciente"] = self.paciente
        context["titulo"] = "Ficha odontológica"
        context["subtitulo"] = "Antecedentes y datos clínicos generales del paciente."
        return context

    def form_valid(self, form):
        form.instance.paciente = self.paciente
        form.instance.actualizado_por = self.request.user
        messages.success(self.request, "Ficha odontológica actualizada correctamente.")
        return super().form_valid(form)


class PacienteUpdateView(GestionConsultorioRequeridaMixin, UpdateView):
    model = Paciente
    form_class = PacienteForm
    template_name = "pacientes/paciente_form.html"

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


class PacienteDeleteView(BorrarPacientesRequeridoMixin, FormView):
    form_class = PacienteDeleteConfirmationForm
    template_name = "pacientes/paciente_confirm_delete.html"
    success_url = reverse_lazy("pacientes:lista")
    estados_que_bloquean_borrado = [
        Turno.Estado.PENDIENTE,
        Turno.Estado.CONFIRMADO,
    ]

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)

        self.paciente = self.get_object()
        return super().dispatch(request, *args, **kwargs)

    def get_object(self):
        return get_object_or_404(Paciente, pk=self.kwargs["pk"])

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["paciente"] = self.paciente
        kwargs["requiere_confirmacion_clinica"] = self._tiene_datos_clinicos()
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["paciente"] = self.paciente
        context["cantidad_historias_clinicas"] = self._cantidad_historias_clinicas()
        context["cantidad_adjuntos_clinicos"] = self._cantidad_adjuntos_clinicos()
        context["tiene_ficha_odontologica"] = self._tiene_ficha_odontologica()
        context["requiere_confirmacion_clinica"] = self._tiene_datos_clinicos()
        return context

    def form_valid(self, form):
        nombre_completo = self.paciente.nombre_completo
        tiene_datos_clinicos = self._tiene_datos_clinicos()

        if self._tiene_turnos_que_bloquean_borrado():
            form.add_error(
                None,
                "No se puede borrar el paciente porque tiene turnos pendientes o confirmados.",
            )
            messages.error(
                self.request,
                "No se puede borrar el paciente porque tiene turnos pendientes o confirmados.",
            )
            return super().form_invalid(form)

        try:
            with transaction.atomic():
                self._borrar_datos_clinicos()
                self._borrar_turnos_que_no_bloquean()
                self.paciente.delete()
        except Exception:
            logger.exception("No se pudo borrar el paciente y sus datos asociados.")
            form.add_error(
                None,
                "No se pudo completar el borrado. Revisá los adjuntos clínicos e intentá nuevamente.",
            )
            messages.error(
                self.request,
                "No se pudo completar el borrado del paciente.",
            )
            return super().form_invalid(form)

        if tiene_datos_clinicos:
            messages.success(
                self.request,
                f"Paciente {nombre_completo} y sus datos clínicos fueron borrados correctamente.",
            )
        else:
            messages.success(self.request, f"Paciente {nombre_completo} borrado correctamente.")

        return super().form_valid(form)

    def _tiene_turnos_que_bloquean_borrado(self):
        return self.paciente.turnos.filter(
            estado__in=self.estados_que_bloquean_borrado
        ).exists()

    def _borrar_turnos_que_no_bloquean(self):
        self.paciente.turnos.exclude(
            estado__in=self.estados_que_bloquean_borrado
        ).delete()

    def _borrar_datos_clinicos(self):
        adjuntos = HistoriaClinicaAdjunto.objects.filter(
            historia__paciente=self.paciente,
        )

        for adjunto in adjuntos:
            if adjunto.archivo:
                adjunto.archivo.delete(save=False)

        HistoriaClinica.objects.filter(paciente=self.paciente).delete()
        FichaOdontologica.objects.filter(paciente=self.paciente).delete()

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
