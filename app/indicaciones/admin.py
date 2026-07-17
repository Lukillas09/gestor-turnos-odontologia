from django.contrib import admin

from .forms import PlantillaIndicacionAdminForm
from .models import IndicacionPaciente, PlantillaIndicacion, PlantillaIndicacionVersion
from .permissions import (
    indicaciones_habilitadas,
    obtener_odontologo_activo,
    puede_ver_indicaciones,
)
from .selectors import indicaciones_visibles_para_usuario
from .services import crear_version_plantilla


class SinBorradoAdminMixin:
    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        return {}


def _puede_administrar_indicaciones(request):
    return indicaciones_habilitadas() and obtener_odontologo_activo(request.user) is not None


@admin.register(PlantillaIndicacion)
class PlantillaIndicacionAdmin(SinBorradoAdminMixin, admin.ModelAdmin):
    form = PlantillaIndicacionAdminForm
    list_display = ("nombre", "procedimiento", "version", "activa", "actualizado_en")
    list_filter = ("activa", "actualizado_en")
    search_fields = ("nombre", "procedimiento", "titulo_documento")
    readonly_fields = (
        "version",
        "creado_por",
        "actualizado_por",
        "creado_en",
        "actualizado_en",
    )
    fieldsets = (
        (
            "Contenido aprobado",
            {
                "description": (
                    "Las plantillas deben ser revisadas y aprobadas por los profesionales "
                    "responsables del consultorio antes de utilizarlas."
                ),
                "fields": (
                    "nombre",
                    "procedimiento",
                    "titulo_documento",
                    "contenido",
                    "pautas_alarma",
                    "recomendaciones_control",
                    "activa",
                    "motivo_modificacion",
                ),
            },
        ),
        (
            "Trazabilidad",
            {
                "fields": (
                    "version",
                    "creado_por",
                    "actualizado_por",
                    "creado_en",
                    "actualizado_en",
                )
            },
        ),
    )

    def has_module_permission(self, request):
        return _puede_administrar_indicaciones(request)

    def has_view_permission(self, request, obj=None):
        return _puede_administrar_indicaciones(request)

    def has_add_permission(self, request):
        return _puede_administrar_indicaciones(request)

    def has_change_permission(self, request, obj=None):
        return _puede_administrar_indicaciones(request)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.creado_por = request.user
            obj.actualizado_por = request.user
            obj.save()
            return
        actualizada = crear_version_plantilla(
            plantilla=obj,
            usuario=request.user,
            datos={
                campo: form.cleaned_data[campo]
                for campo in (
                    "nombre",
                    "procedimiento",
                    "titulo_documento",
                    "contenido",
                    "pautas_alarma",
                    "recomendaciones_control",
                    "activa",
                )
            },
            motivo=form.cleaned_data["motivo_modificacion"],
        )
        for campo in (
            "nombre",
            "procedimiento",
            "titulo_documento",
            "contenido",
            "pautas_alarma",
            "recomendaciones_control",
            "version",
            "activa",
            "actualizado_por",
            "actualizado_en",
        ):
            setattr(obj, campo, getattr(actualizada, campo))


@admin.register(PlantillaIndicacionVersion)
class PlantillaIndicacionVersionAdmin(SinBorradoAdminMixin, admin.ModelAdmin):
    list_display = ("plantilla", "numero_version", "creado_por", "creado_en")
    list_filter = ("creado_en",)
    search_fields = ("plantilla__nombre", "motivo")
    readonly_fields = tuple(field.name for field in PlantillaIndicacionVersion._meta.fields)

    def has_module_permission(self, request):
        return _puede_administrar_indicaciones(request)

    def has_view_permission(self, request, obj=None):
        return _puede_administrar_indicaciones(request)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(IndicacionPaciente)
class IndicacionPacienteAdmin(SinBorradoAdminMixin, admin.ModelAdmin):
    list_display = (
        "creado_en",
        "paciente",
        "odontologo",
        "titulo",
        "estado",
        "email_estado",
        "pdf_disponible",
    )
    list_filter = ("estado", "email_estado", "creado_en", "odontologo")
    search_fields = (
        "paciente__apellido",
        "paciente__nombre",
        "odontologo__usuario__first_name",
        "odontologo__usuario__last_name",
        "titulo",
        "uuid",
    )
    readonly_fields = tuple(field.name for field in IndicacionPaciente._meta.fields)
    date_hierarchy = "creado_en"

    def has_module_permission(self, request):
        return _puede_administrar_indicaciones(request)

    def has_view_permission(self, request, obj=None):
        if not _puede_administrar_indicaciones(request):
            return False
        if obj is None:
            return True
        return puede_ver_indicaciones(request.user, obj.paciente, request=request)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return indicaciones_visibles_para_usuario(request.user, request=request)

    @admin.display(boolean=True, description="PDF")
    def pdf_disponible(self, obj):
        return bool(obj.pdf)
