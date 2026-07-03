from django.contrib import admin

from .models import AccesoClinicoAuditoria, HistoriaClinica, HistoriaClinicaAdjunto


class SinBorradoFisicoAdminMixin:
    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions


class HistoriaClinicaAdjuntoInline(admin.TabularInline):
    model = HistoriaClinicaAdjunto
    extra = 0
    readonly_fields = ("content_type", "tamano_bytes", "creado_en")
    autocomplete_fields = ("subido_por",)
    can_delete = False


@admin.register(HistoriaClinica)
class HistoriaClinicaAdmin(SinBorradoFisicoAdminMixin, admin.ModelAdmin):
    inlines = (HistoriaClinicaAdjuntoInline,)
    list_display = ("paciente", "odontologo", "fecha", "creado_en")
    search_fields = (
        "paciente__apellido",
        "paciente__nombre",
        "paciente__documento",
        "odontologo__usuario__first_name",
        "odontologo__usuario__last_name",
        "motivo_consulta",
        "diagnostico",
    )
    list_filter = ("fecha", "odontologo")
    readonly_fields = ("creado_en", "actualizado_en")
    autocomplete_fields = ("paciente", "odontologo", "creado_por", "actualizado_por")
    date_hierarchy = "fecha"


@admin.register(HistoriaClinicaAdjunto)
class HistoriaClinicaAdjuntoAdmin(SinBorradoFisicoAdminMixin, admin.ModelAdmin):
    list_display = ("historia", "nombre_archivo", "tamano_legible", "subido_por", "creado_en")
    search_fields = (
        "historia__paciente__apellido",
        "historia__paciente__nombre",
        "historia__paciente__documento",
        "descripcion",
    )
    readonly_fields = ("content_type", "tamano_bytes", "creado_en")
    autocomplete_fields = ("historia", "subido_por")


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
        return request.user.is_staff

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions
