from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from .models import (
    ConfiguracionAgendaInteligente,
    DisponibilidadOdontologo,
    ExcepcionAgenda,
    GoogleCalendarConexion,
    Odontologo,
    SolicitudTurnoPublica,
    TipoTurno,
    TipoTurnoOdontologo,
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


class TipoTurnoOdontologoInline(admin.TabularInline):
    model = TipoTurnoOdontologo
    extra = 0
    fields = (
        "tipo_turno",
        "duracion_atencion_minutos",
        "margen_posterior_minutos",
        "duracion_bloqueada_display",
        "reserva_publica",
        "activo",
    )
    readonly_fields = ("duracion_bloqueada_display",)
    autocomplete_fields = ("tipo_turno",)
    can_delete = False

    @admin.display(description="Total bloqueado")
    def duracion_bloqueada_display(self, obj):
        return f"{obj.duracion_bloqueada_minutos} min" if obj.pk else "-"


@admin.register(Odontologo)
class OdontologoAdmin(admin.ModelAdmin):
    inlines = (
        DisponibilidadOdontologoInline,
        TipoTurnoOdontologoInline,
        GoogleCalendarConexionInline,
    )
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


@admin.register(ExcepcionAgenda)
class ExcepcionAgendaAdmin(admin.ModelAdmin):
    list_display = (
        "tipo",
        "alcance_display",
        "fecha_desde",
        "fecha_hasta",
        "horario_display",
        "activo",
    )
    list_filter = ("activo", "tipo", "todo_el_dia", "odontologo")
    search_fields = (
        "motivo",
        "mensaje_publico",
        "odontologo__usuario__first_name",
        "odontologo__usuario__last_name",
        "odontologo__matricula",
    )
    autocomplete_fields = ("odontologo", "creada_por", "actualizada_por", "desactivada_por")
    readonly_fields = (
        "creada_por",
        "actualizada_por",
        "desactivada_por",
        "desactivada_en",
        "creado_en",
        "actualizado_en",
    )
    date_hierarchy = "fecha_desde"
    ordering = ("-activo", "fecha_desde", "hora_inicio")


@admin.register(TipoTurno)
class TipoTurnoAdmin(admin.ModelAdmin):
    list_display = (
        "nombre",
        "slug",
        "orden_publico",
        "activo",
        "visible_publicamente",
    )
    list_filter = ("activo", "visible_publicamente", "icono")
    search_fields = ("nombre", "slug", "descripcion_publica")
    readonly_fields = ("creado_por", "actualizado_por", "creado_en", "actualizado_en")
    ordering = ("orden_publico", "nombre")

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.creado_por = request.user
        obj.actualizado_por = request.user
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TipoTurnoOdontologo)
class TipoTurnoOdontologoAdmin(admin.ModelAdmin):
    list_display = (
        "odontologo",
        "tipo_turno",
        "duracion_atencion_minutos",
        "margen_posterior_minutos",
        "duracion_bloqueada_display",
        "reserva_publica",
        "activo",
    )
    list_filter = ("activo", "reserva_publica", "tipo_turno", "odontologo")
    search_fields = (
        "tipo_turno__nombre",
        "odontologo__usuario__first_name",
        "odontologo__usuario__last_name",
        "odontologo__matricula",
    )
    autocomplete_fields = ("odontologo", "tipo_turno")
    list_select_related = ("odontologo", "odontologo__usuario", "tipo_turno")

    @admin.display(description="Total bloqueado")
    def duracion_bloqueada_display(self, obj):
        return f"{obj.duracion_bloqueada_minutos} min"

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ConfiguracionAgendaInteligente)
class ConfiguracionAgendaInteligenteAdmin(admin.ModelAdmin):
    list_display = (
        "odontologo",
        "activa",
        "intervalo_inicio_minutos",
        "hueco_minimo_util_minutos",
        "cantidad_horarios_recomendados",
        "modo_compactacion",
    )
    list_filter = ("activa", "modo_compactacion", "preservar_bloques_largos")
    search_fields = (
        "odontologo__usuario__first_name",
        "odontologo__usuario__last_name",
        "odontologo__matricula",
    )
    autocomplete_fields = ("odontologo",)
    readonly_fields = ("actualizado_en",)

    def has_delete_permission(self, request, obj=None):
        return False


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
        "tipo_turno",
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
    autocomplete_fields = ("paciente", "odontologo", "tipo_turno")
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
                    "tipo_turno",
                    "tipo_turno_nombre_snapshot",
                    "fecha",
                    "hora_inicio",
                    "duracion_minutos",
                    "duracion_atencion_minutos",
                    "margen_posterior_minutos_snapshot",
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


@admin.register(SolicitudTurnoPublica)
class SolicitudTurnoPublicaAdmin(admin.ModelAdmin):
    list_display = (
        "creado_en",
        "paciente_link",
        "turno_link",
        "paciente_existente",
        "requiere_revision",
        "estado_revision",
        "revisada_por",
    )
    list_filter = (
        "paciente_existente",
        "requiere_revision",
        "estado_revision",
        "turno",
        ("creado_en", admin.DateFieldListFilter),
    )
    search_fields = (
        "documento_enviado",
        "nombre_enviado",
        "apellido_enviado",
        "paciente__documento",
        "paciente__nombre",
        "paciente__apellido",
    )
    autocomplete_fields = ("paciente", "turno", "revisada_por")
    readonly_fields = (
        "documento_enviado",
        "nombre_enviado",
        "apellido_enviado",
        "telefono_enviado",
        "email_enviado",
        "motivo_enviado",
        "tipo_turno",
        "tipo_turno_nombre_snapshot",
        "duracion_atencion_snapshot",
        "duracion_bloqueada_snapshot",
        "margen_posterior_snapshot",
        "algoritmo_version",
        "horario_clasificacion",
        "horario_puntaje",
        "diferencias_detectadas",
        "paciente_existente",
        "requiere_revision",
        "estado_revision",
        "revisada_por",
        "revisada_en",
        "observaciones_revision",
        "campos_actualizados",
        "campos_descartados",
        "notificacion_contacto_existente_en",
        "notificacion_contacto_existente_error",
        "creado_en",
        "actualizado_en",
    )
    list_select_related = (
        "paciente",
        "turno",
        "turno__odontologo",
        "turno__odontologo__usuario",
        "revisada_por",
    )
    ordering = ("-creado_en",)
    list_per_page = 25

    @admin.display(description="Paciente", ordering="paciente__apellido")
    def paciente_link(self, obj):
        url = reverse("admin:pacientes_paciente_change", args=[obj.paciente_id])
        return format_html('<a href="{}">{}</a>', url, obj.paciente)

    @admin.display(description="Turno", ordering="turno__fecha")
    def turno_link(self, obj):
        if not obj.turno_id:
            return "Sin turno"

        url = reverse("admin:turnos_turno_change", args=[obj.turno_id])
        return format_html('<a href="{}">{}</a>', url, obj.turno)
