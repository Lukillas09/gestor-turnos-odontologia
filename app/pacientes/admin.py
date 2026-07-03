from django.contrib import admin

from .models import FichaOdontologica, Paciente, PacienteOdontologo


class SinBorradoFisicoAdminMixin:
    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions


class PacienteOdontologoInline(admin.TabularInline):
    model = PacienteOdontologo
    extra = 0
    fields = ("odontologo", "asignado_por", "motivo", "activo", "creado_en")
    readonly_fields = ("creado_en",)
    autocomplete_fields = ("odontologo", "asignado_por")


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
class PacienteAdmin(SinBorradoFisicoAdminMixin, admin.ModelAdmin):
    inlines = (PacienteOdontologoInline, FichaOdontologicaInline)
    list_display = (
        "nombre",
        "apellido",
        "dni",
        "telefono",
        "email",
        "estado_validacion_datos",
        "origen_alta",
        "activo",
        "archivado_en",
        "obra_social",
    )
    search_fields = (
        "apellido",
        "nombre",
        "documento",
        "telefono",
        "email",
        "localidad",
        "obra_social",
    )
    list_filter = (
        "genero",
        "estado_validacion_datos",
        "origen_alta",
        "obra_social",
        "activo",
        ("creado_en", admin.DateFieldListFilter),
    )
    readonly_fields = (
        "creado_en",
        "actualizado_en",
        "email_verificado_en",
        "telefono_verificado_en",
        "archivado_en",
        "archivado_por",
        "motivo_archivado",
    )
    autocomplete_fields = ("validado_por",)
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
                    "email_verificado_en",
                    "telefono_verificado_en",
                    "domicilio",
                    "localidad",
                    "contacto_emergencia",
                )
            },
        ),
        (
            "Validacion administrativa",
            {
                "fields": (
                    "estado_validacion_datos",
                    "origen_alta",
                    "validado_por",
                    "validado_en",
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
            "Estado operativo",
            {
                "fields": (
                    "activo",
                    "archivado_en",
                    "archivado_por",
                    "motivo_archivado",
                )
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
class FichaOdontologicaAdmin(SinBorradoFisicoAdminMixin, admin.ModelAdmin):
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


@admin.register(PacienteOdontologo)
class PacienteOdontologoAdmin(SinBorradoFisicoAdminMixin, admin.ModelAdmin):
    list_display = ("paciente", "odontologo", "activo", "asignado_por", "creado_en")
    search_fields = (
        "paciente__apellido",
        "paciente__nombre",
        "paciente__documento",
        "odontologo__usuario__first_name",
        "odontologo__usuario__last_name",
        "odontologo__usuario__username",
        "motivo",
    )
    list_filter = ("activo", ("creado_en", admin.DateFieldListFilter))
    autocomplete_fields = ("paciente", "odontologo", "asignado_por")
    readonly_fields = ("creado_en", "actualizado_en")
