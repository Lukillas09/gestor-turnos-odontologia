from django.contrib import admin
from django.core.exceptions import PermissionDenied

from .models import CONFIGURACION_CONSULTORIO_PK, ConfiguracionConsultorio


@admin.register(ConfiguracionConsultorio)
class ConfiguracionConsultorioAdmin(admin.ModelAdmin):
    list_display = ["nombre_comercial", "email", "telefono", "actualizado_por", "actualizado_en"]
    readonly_fields = ["actualizado_por", "creado_en", "actualizado_en"]
    fieldsets = (
        (
            "Identidad",
            {
                "fields": (
                    "nombre_comercial",
                    "nombre_corto",
                    "logo",
                    "color_principal",
                )
            },
        ),
        (
            "Contacto",
            {
                "fields": (
                    "direccion",
                    "localidad",
                    "provincia",
                    "telefono",
                    "whatsapp",
                    "email",
                    "horario_atencion",
                )
            },
        ),
        (
            "Página pública",
            {
                "fields": (
                    "titulo_portada",
                    "texto_bienvenida",
                    "politica_cancelacion",
                )
            },
        ),
        (
            "Visibilidad pública",
            {
                "fields": (
                    "mostrar_direccion",
                    "mostrar_telefono",
                    "mostrar_whatsapp",
                    "mostrar_email",
                    "mostrar_horario_atencion",
                )
            },
        ),
        (
            "Auditoría",
            {
                "fields": (
                    "actualizado_por",
                    "creado_en",
                    "actualizado_en",
                )
            },
        ),
    )

    def has_add_permission(self, request):
        if ConfiguracionConsultorio.objects.filter(pk=CONFIGURACION_CONSULTORIO_PK).exists():
            return False

        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def save_model(self, request, obj, form, change):
        if obj.pk not in (None, CONFIGURACION_CONSULTORIO_PK):
            raise PermissionDenied("Solo puede existir una configuración del consultorio.")

        obj.pk = CONFIGURACION_CONSULTORIO_PK
        obj.actualizado_por = request.user if request.user.is_authenticated else None
        super().save_model(request, obj, form, change)
