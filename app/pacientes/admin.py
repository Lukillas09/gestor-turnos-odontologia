from django.contrib import admin

from .models import FichaOdontologica, Paciente


class FichaOdontologicaInline(admin.StackedInline):
    model = FichaOdontologica
    extra = 0
    max_num = 1
    fields = (
        "antecedentes_medicos",
        "alergias",
        "medicacion_actual",
        "enfermedades_relevantes",
        "embarazo",
        "hipertension",
        "diabetes",
        "problemas_cardiacos",
        "observaciones_generales",
        "actualizado_por",
        "creado_en",
        "actualizado_en",
    )
    readonly_fields = ("creado_en", "actualizado_en")


@admin.register(Paciente)
class PacienteAdmin(admin.ModelAdmin):
    inlines = (FichaOdontologicaInline,)
    list_display = ("nombre", "apellido", "dni", "telefono", "email", "obra_social")
    search_fields = (
        "apellido",
        "nombre",
        "documento",
        "telefono",
        "email",
        "localidad",
        "obra_social",
    )
    list_filter = ("genero", "obra_social", ("creado_en", admin.DateFieldListFilter))
    readonly_fields = ("creado_en", "actualizado_en")
    ordering = ("apellido", "nombre")
    list_per_page = 25
    fieldsets = (
        (
            "Datos personales",
            {
                "fields": (
                    "nombre",
                    "apellido",
                    "documento",
                    "fecha_nacimiento",
                    "genero",
                )
            },
        ),
        (
            "Contacto",
            {
                "fields": (
                    "telefono",
                    "email",
                    "domicilio",
                    "localidad",
                    "contacto_emergencia",
                )
            },
        ),
        (
            "Cobertura",
            {
                "fields": (
                    "obra_social",
                    "numero_afiliado",
                )
            },
        ),
        (
            "Notas",
            {
                "fields": ("observaciones",),
            },
        ),
        (
            "Auditoria",
            {
                "fields": (
                    "creado_en",
                    "actualizado_en",
                )
            },
        ),
    )

    @admin.display(description="DNI", ordering="documento")
    def dni(self, obj):
        return obj.documento or "-"


@admin.register(FichaOdontologica)
class FichaOdontologicaAdmin(admin.ModelAdmin):
    list_display = ("paciente", "alergias_resumen", "actualizado_en")
    search_fields = (
        "paciente__apellido",
        "paciente__nombre",
        "paciente__documento",
        "antecedentes_medicos",
        "alergias",
        "medicacion_actual",
    )
    list_filter = ("embarazo", "hipertension", "diabetes", "problemas_cardiacos")
    readonly_fields = ("creado_en", "actualizado_en")
    autocomplete_fields = ("paciente", "actualizado_por")

    @admin.display(description="Alergias")
    def alergias_resumen(self, obj):
        return obj.alergias[:80] if obj.alergias else "-"
