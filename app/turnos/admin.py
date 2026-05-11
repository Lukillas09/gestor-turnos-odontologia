from django.contrib import admin

from .models import (
    DisponibilidadOdontologo,
    GoogleCalendarConexion,
    Odontologo,
    Turno,
)


class DisponibilidadOdontologoInline(admin.TabularInline):
    model = DisponibilidadOdontologo
    extra = 1
    fields = ("dia_semana", "hora_inicio", "hora_fin", "activo")


class GoogleCalendarConexionInline(admin.StackedInline):
    model = GoogleCalendarConexion
    extra = 0
    max_num = 1
    fields = (
        "calendar_id",
        "activa",
        "token_expira_en",
        "sincronizado_en",
        "ultimo_error",
    )
    readonly_fields = ("sincronizado_en",)


@admin.register(Odontologo)
class OdontologoAdmin(admin.ModelAdmin):
    inlines = (DisponibilidadOdontologoInline, GoogleCalendarConexionInline)
    list_display = (
        "nombre",
        "apellido",
        "matricula",
        "celular",
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
        "celular",
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
                    "celular",
                    "especialidad",
                    "foto_url",
                    "foto_perfil",
                    "foto_posicion_x",
                    "foto_posicion_y",
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

    @admin.display(description="Horario de atención")
    def horario_atencion(self, obj):
        return f"{obj.hora_inicio_atencion:%H:%M} a {obj.hora_fin_atencion:%H:%M}"


@admin.register(DisponibilidadOdontologo)
class DisponibilidadOdontologoAdmin(admin.ModelAdmin):
    list_display = ("odontologo", "dia_semana", "hora_inicio", "hora_fin", "activo")
    list_filter = ("dia_semana", "activo", "odontologo")
    search_fields = (
        "odontologo__usuario__first_name",
        "odontologo__usuario__last_name",
        "odontologo__matricula",
    )
    autocomplete_fields = ("odontologo",)
    ordering = ("odontologo", "dia_semana", "hora_inicio")
    readonly_fields = ("creado_en", "actualizado_en")


@admin.register(GoogleCalendarConexion)
class GoogleCalendarConexionAdmin(admin.ModelAdmin):
    list_display = (
        "odontologo",
        "calendar_id",
        "activa",
        "esta_conectada_display",
        "access_token_expirado_display",
        "token_expira_en",
        "sincronizado_en",
    )
    list_filter = ("activa", "token_type")
    search_fields = (
        "odontologo__usuario__first_name",
        "odontologo__usuario__last_name",
        "odontologo__usuario__email",
        "odontologo__matricula",
        "calendar_id",
    )
    autocomplete_fields = ("odontologo",)
    readonly_fields = (
        "access_token_estado",
        "refresh_token_estado",
        "scopes_display",
        "token_type",
        "token_expira_en",
        "creado_en",
        "actualizado_en",
        "sincronizado_en",
    )
    ordering = ("odontologo",)
    fieldsets = (
        (
            "Odontólogo",
            {
                "fields": (
                    "odontologo",
                    "calendar_id",
                    "activa",
                )
            },
        ),
        (
            "Token OAuth",
            {
                "classes": ("collapse",),
                "fields": (
                    "access_token_estado",
                    "refresh_token_estado",
                    "token_type",
                    "scopes_display",
                    "token_expira_en",
                ),
                "description": (
                    "Estos valores los debe escribir el flujo OAuth. "
                    "Por seguridad, los tokens reales no se muestran en el admin."
                ),
            },
        ),
        (
            "Sincronización",
            {
                "fields": (
                    "sincronizado_en",
                    "ultimo_error",
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

    @admin.display(boolean=True, description="Conectada")
    def esta_conectada_display(self, obj):
        return obj.esta_conectada

    @admin.display(boolean=True, description="Access token expirado")
    def access_token_expirado_display(self, obj):
        return obj.access_token_expirado

    @admin.display(description="Access token")
    def access_token_estado(self, obj):
        return "Guardado" if obj.access_token else "-"

    @admin.display(description="Refresh token")
    def refresh_token_estado(self, obj):
        return "Guardado" if obj.refresh_token else "-"

    @admin.display(description="Permisos OAuth")
    def scopes_display(self, obj):
        return ", ".join(obj.scopes) if obj.scopes else "-"


@admin.register(Turno)
class TurnoAdmin(admin.ModelAdmin):
    list_display = (
        "paciente",
        "odontologo",
        "fecha",
        "hora_inicio",
        "hora_fin_display",
        "estado",
        "recordatorio_email_enviado_en",
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
    readonly_fields = (
        "recordatorio_email_enviado_en",
        "recordatorio_email_ultimo_error",
        "creado_en",
        "actualizado_en",
    )
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
            "Recordatorio por email",
            {
                "fields": (
                    "recordatorio_email_enviado_en",
                    "recordatorio_email_ultimo_error",
                ),
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
