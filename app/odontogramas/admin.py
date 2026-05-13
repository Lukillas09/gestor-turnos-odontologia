from django.contrib import admin

from .models import EstadoDental, Odontograma


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


@admin.register(Odontograma)
class OdontogramaAdmin(admin.ModelAdmin):
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
class EstadoDentalAdmin(admin.ModelAdmin):
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
