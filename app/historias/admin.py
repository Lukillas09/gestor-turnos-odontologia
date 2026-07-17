from django.contrib import admin
from django.db.models import Count

from .access_policy import (
    limitar_historias_clinicas_para_request,
    obtener_politica_lectura,
    registrar_evento_acceso_clinico,
)
from .models import (
    AccesoClinicoAuditoria,
    HistoriaClinica,
    HistoriaClinicaAdjunto,
    HistoriaClinicaEnmienda,
    HistoriaClinicaVersion,
)


class ClinicaSoloLecturaAdminMixin:
    accion_lista = AccesoClinicoAuditoria.Accion.VER_HISTORIA
    accion_detalle = AccesoClinicoAuditoria.Accion.VER_DETALLE_HISTORIA

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        if not request.user.is_authenticated:
            return False
        if obj is None:
            return True
        paciente = _paciente_de_objeto(obj)
        return bool(obtener_politica_lectura(request.user, paciente, request=request))

    def get_actions(self, request):
        return {}

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context=extra_context)
        if response.status_code < 400 and getattr(response, "context_data", None):
            changelist = response.context_data.get("cl")
            if changelist is not None:
                for obj in changelist.result_list:
                    _registrar_visualizacion_admin(request, obj, self.accion_lista)
        return response

    def change_view(self, request, object_id, form_url="", extra_context=None):
        response = super().change_view(
            request,
            object_id,
            form_url=form_url,
            extra_context=extra_context,
        )
        if response.status_code < 400:
            obj = self.get_object(request, object_id)
            if obj is not None:
                _registrar_visualizacion_admin(request, obj, self.accion_detalle)
        return response


class HistoriaClinicaAdjuntoInline(admin.TabularInline):
    model = HistoriaClinicaAdjunto
    extra = 0
    can_delete = False
    fields = (
        "nombre_archivo",
        "descripcion",
        "content_type",
        "tamano_bytes",
        "sha256_abreviado",
        "subido_por",
        "creado_en",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="Archivo")
    def nombre_archivo(self, obj):
        return obj.nombre_archivo

    @admin.display(description="SHA-256")
    def sha256_abreviado(self, obj):
        return f"{obj.sha256[:12]}…" if obj.sha256 else "Pendiente"


@admin.register(HistoriaClinica)
class HistoriaClinicaAdmin(ClinicaSoloLecturaAdminMixin, admin.ModelAdmin):
    inlines = (HistoriaClinicaAdjuntoInline,)
    list_display = (
        "numero_asiento",
        "paciente",
        "odontologo",
        "fecha_hora_atencion",
        "estado",
        "cantidad_versiones",
        "cantidad_enmiendas",
    )
    search_fields = (
        "paciente__apellido",
        "paciente__nombre",
        "paciente__documento",
        "odontologo__usuario__first_name",
        "odontologo__usuario__last_name",
        "motivo_consulta",
        "diagnostico",
    )
    list_filter = (
        "borrador",
        "migrada_desde_legacy",
        "fecha_hora_atencion",
        "odontologo",
    )
    readonly_fields = tuple(field.name for field in HistoriaClinica._meta.fields)
    date_hierarchy = "fecha_hora_atencion"

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return (
            limitar_historias_clinicas_para_request(queryset, request)
            .select_related("paciente", "odontologo", "odontologo__usuario")
            .annotate(
                _cantidad_versiones=Count("versiones", distinct=True),
                _cantidad_enmiendas=Count("enmiendas", distinct=True),
            )
        )

    @admin.display(description="Estado")
    def estado(self, obj):
        return obj.estado_display

    @admin.display(description="Versiones", ordering="_cantidad_versiones")
    def cantidad_versiones(self, obj):
        return obj._cantidad_versiones

    @admin.display(description="Enmiendas", ordering="_cantidad_enmiendas")
    def cantidad_enmiendas(self, obj):
        return obj._cantidad_enmiendas


@admin.register(HistoriaClinicaAdjunto)
class HistoriaClinicaAdjuntoAdmin(ClinicaSoloLecturaAdminMixin, admin.ModelAdmin):
    accion_lista = AccesoClinicoAuditoria.Accion.ABRIR_ADJUNTO
    accion_detalle = AccesoClinicoAuditoria.Accion.ABRIR_ADJUNTO
    list_display = (
        "historia",
        "nombre_archivo",
        "tamano_legible",
        "sha256_abreviado",
        "subido_por",
        "creado_en",
    )
    search_fields = (
        "historia__paciente__apellido",
        "historia__paciente__nombre",
        "historia__paciente__documento",
        "descripcion",
    )
    readonly_fields = tuple(field.name for field in HistoriaClinicaAdjunto._meta.fields)

    def get_queryset(self, request):
        historias = limitar_historias_clinicas_para_request(
            HistoriaClinica.objects.all(),
            request,
        )
        return super().get_queryset(request).filter(historia__in=historias)

    @admin.display(description="SHA-256")
    def sha256_abreviado(self, obj):
        return f"{obj.sha256[:12]}…" if obj.sha256 else "Pendiente"


@admin.register(HistoriaClinicaVersion)
class HistoriaClinicaVersionAdmin(ClinicaSoloLecturaAdminMixin, admin.ModelAdmin):
    accion_lista = AccesoClinicoAuditoria.Accion.VER_VERSION
    accion_detalle = AccesoClinicoAuditoria.Accion.VER_VERSION
    list_display = (
        "historia",
        "numero_version",
        "creado_por",
        "creado_en",
        "hash_abreviado",
    )
    list_filter = ("creado_en",)
    search_fields = (
        "historia__paciente__apellido",
        "historia__paciente__nombre",
        "historia__paciente__documento",
    )
    readonly_fields = tuple(field.name for field in HistoriaClinicaVersion._meta.fields)
    date_hierarchy = "creado_en"

    def get_queryset(self, request):
        historias = limitar_historias_clinicas_para_request(
            HistoriaClinica.objects.all(),
            request,
        )
        return super().get_queryset(request).filter(historia__in=historias)

    @admin.display(description="Sello")
    def hash_abreviado(self, obj):
        return f"{obj.hash_integridad[:12]}…"


@admin.register(HistoriaClinicaEnmienda)
class HistoriaClinicaEnmiendaAdmin(ClinicaSoloLecturaAdminMixin, admin.ModelAdmin):
    accion_lista = AccesoClinicoAuditoria.Accion.VER_ENMIENDA
    accion_detalle = AccesoClinicoAuditoria.Accion.VER_ENMIENDA
    list_display = (
        "historia",
        "numero_enmienda",
        "odontologo",
        "creado_por",
        "creado_en",
        "hash_abreviado",
    )
    list_filter = ("creado_en", "odontologo")
    search_fields = (
        "historia__paciente__apellido",
        "historia__paciente__nombre",
        "historia__paciente__documento",
    )
    readonly_fields = tuple(field.name for field in HistoriaClinicaEnmienda._meta.fields)
    date_hierarchy = "creado_en"

    def get_queryset(self, request):
        historias = limitar_historias_clinicas_para_request(
            HistoriaClinica.objects.all(),
            request,
        )
        return super().get_queryset(request).filter(historia__in=historias)

    @admin.display(description="Sello")
    def hash_abreviado(self, obj):
        return f"{obj.hash_integridad[:12]}…"


@admin.register(AccesoClinicoAuditoria)
class AccesoClinicoAuditoriaAdmin(admin.ModelAdmin):
    list_display = (
        "creado_en",
        "accion",
        "resultado",
        "politica",
        "usuario",
        "paciente",
        "es_emergencia",
        "es_acceso_compartido",
    )
    list_filter = (
        "accion",
        "resultado",
        "politica",
        "es_emergencia",
        "es_acceso_compartido",
        ("creado_en", admin.DateFieldListFilter),
    )
    search_fields = (
        "usuario__username",
        "paciente__apellido",
        "paciente__nombre",
        "paciente__documento",
        "motivo",
        "ruta",
    )
    readonly_fields = tuple(field.name for field in AccesoClinicoAuditoria._meta.fields)
    date_hierarchy = "creado_en"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.is_staff

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        return {}


def _paciente_de_objeto(obj):
    if isinstance(obj, HistoriaClinica):
        return obj.paciente
    if isinstance(obj, HistoriaClinicaAdjunto):
        return obj.historia.paciente
    return obj.historia.paciente


def _registrar_visualizacion_admin(request, obj, accion):
    if isinstance(obj, HistoriaClinica):
        historia = obj
        adjunto = None
    else:
        historia = obj.historia
        adjunto = obj if isinstance(obj, HistoriaClinicaAdjunto) else None
    paciente = historia.paciente
    registrar_evento_acceso_clinico(
        request=request,
        accion=accion,
        resultado=AccesoClinicoAuditoria.Resultado.PERMITIDO,
        politica=(
            obtener_politica_lectura(request.user, paciente, request=request)
            or AccesoClinicoAuditoria.Politica.SIN_PERMISO
        ),
        paciente=paciente,
        historia=historia,
        adjunto=adjunto,
        motivo="Registro clínico consultado desde administración.",
    )
