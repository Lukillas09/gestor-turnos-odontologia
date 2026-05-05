from django.contrib import admin

from .models import Paciente


@admin.register(Paciente)
class PacienteAdmin(admin.ModelAdmin):
    list_display = ("nombre", "apellido", "dni", "telefono", "email")
    search_fields = ("apellido", "nombre", "documento", "telefono", "email")
    list_filter = (("creado_en", admin.DateFieldListFilter),)
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
                )
            },
        ),
        (
            "Contacto",
            {
                "fields": (
                    "telefono",
                    "email",
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
