from django.contrib import admin

from .models import EstadoDental, Odontograma


class SinBorradoFisicoAdminMixin:
    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions


class EstadoDentalInline(admin.TabularInline):
    model = EstadoDental
    extra = 0
    fields = (
        "diente",
        "cara",
        "estado_clinico",
        "color",
        "realizado",
        "historia_clinica",
        "odontologo",
        "fecha",
        "activo",
    )
    readonly_fields = ("color",)
    autocomplete_fields = ("odontologo",)
    can_delete = False


@admin.register(Odontograma)
class OdontogramaAdmin(SinBorradoFisicoAdminMixin, admin.ModelAdmin):
    inlines = (EstadoDentalInline,)
    list_display = ("paciente", "actualizado_en")
    search_fields = (
        "paciente__apellido",
        "paciente__nombre",
        "paciente__documento",
    )
    readonly_fields = ("creado_en", "actualizado_en")
    autocomplete_fields = ("paciente",)


@admin.register(EstadoDental)
class EstadoDentalAdmin(SinBorradoFisicoAdminMixin, admin.ModelAdmin):
    list_display = (
        "odontograma",
        "diente",
        "cara",
        "estado_clinico",
        "color",
        "realizado",
        "activo",
        "fecha",
        "odontologo",
        "historia_clinica",
    )
    search_fields = (
        "odontograma__paciente__apellido",
        "odontograma__paciente__nombre",
        "odontograma__paciente__documento",
        "observacion",
    )
    list_filter = ("estado_clinico", "color", "realizado", "activo", "fecha")
    readonly_fields = ("color", "creado_en")
    autocomplete_fields = ("odontograma", "odontologo", "registrado_por")
