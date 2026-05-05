from django.contrib import admin

from .models import Odontologo, Turno


@admin.register(Odontologo)
class OdontologoAdmin(admin.ModelAdmin):
    list_display = (
        "nombre",
        "apellido",
        "matricula",
        "email",
        "especialidad",
        "horario_atencion",
        "activo",
    )
    search_fields = (
        "usuario__first_name",
        "usuario__last_name",
        "usuario__username",
        "usuario__email",
        "matricula",
        "especialidad",
    )
    list_filter = ("activo", "especialidad")
    readonly_fields = ("creado_en", "actualizado_en")
    autocomplete_fields = ("usuario",)
    list_select_related = ("usuario",)
    ordering = ("usuario__last_name", "usuario__first_name", "matricula")
    list_per_page = 25
    fieldsets = (
        (
            "Usuario",
            {
                "fields": ("usuario",),
            },
        ),
        (
            "Datos profesionales",
            {
                "fields": (
                    "matricula",
                    "especialidad",
                    "activo",
                )
            },
        ),
        (
            "Agenda",
            {
                "fields": (
                    "duracion_turno_minutos",
                    "hora_inicio_atencion",
                    "hora_fin_atencion",
                    "color_calendario",
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

    @admin.display(description="Nombre", ordering="usuario__first_name")
    def nombre(self, obj):
        return obj.usuario.first_name or obj.usuario.username

    @admin.display(description="Apellido", ordering="usuario__last_name")
    def apellido(self, obj):
        return obj.usuario.last_name or "-"

    @admin.display(description="Email", ordering="usuario__email")
    def email(self, obj):
        return obj.usuario.email or "-"

    @admin.display(description="Horario de atencion")
    def horario_atencion(self, obj):
        return f"{obj.hora_inicio_atencion:%H:%M} a {obj.hora_fin_atencion:%H:%M}"


@admin.register(Turno)
class TurnoAdmin(admin.ModelAdmin):
    list_display = (
        "paciente",
        "odontologo",
        "fecha",
        "hora_inicio",
        "hora_fin_display",
        "estado",
    )
    list_filter = (
        "estado",
        ("fecha", admin.DateFieldListFilter),
        "odontologo",
    )
    search_fields = (
        "paciente__apellido",
        "paciente__nombre",
        "paciente__documento",
        "paciente__telefono",
        "odontologo__usuario__first_name",
        "odontologo__usuario__last_name",
        "odontologo__usuario__email",
        "odontologo__matricula",
        "motivo",
    )
    autocomplete_fields = ("paciente", "odontologo")
    list_select_related = (
        "paciente",
        "odontologo",
        "odontologo__usuario",
    )
    date_hierarchy = "fecha"
    readonly_fields = ("creado_en", "actualizado_en")
    ordering = ("fecha", "hora_inicio")
    list_per_page = 25
    fieldsets = (
        (
            "Turno",
            {
                "fields": (
                    "paciente",
                    "odontologo",
                    "fecha",
                    "hora_inicio",
                    "duracion_minutos",
                    "estado",
                )
            },
        ),
        (
            "Detalle",
            {
                "fields": (
                    "motivo",
                    "notas",
                )
            },
        ),
        (
            "Google Calendar",
            {
                "fields": ("google_calendar_event_id",),
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

    @admin.display(description="Hora fin")
    def hora_fin_display(self, obj):
        return obj.hora_fin
