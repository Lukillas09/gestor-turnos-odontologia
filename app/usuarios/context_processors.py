from django.conf import settings

from .roles import (
    obtener_odontologo_del_usuario,
    puede_borrar_pacientes,
    puede_conectar_google_calendar,
    puede_configurar_disponibilidad,
    puede_gestionar_historias_clinicas,
    puede_gestionar_consultorio,
    puede_revisar_solicitudes_publicas,
    puede_ver_pacientes,
    puede_ver_turnos,
)


def permisos_usuario(request):
    usuario = request.user
    puede_revisar_publicas = puede_revisar_solicitudes_publicas(usuario)
    solicitudes_publicas_pendientes = 0

    if puede_revisar_publicas:
        from turnos.models import SolicitudTurnoPublica

        solicitudes_publicas_pendientes = SolicitudTurnoPublica.objects.filter(
            estado_revision=SolicitudTurnoPublica.EstadoRevision.PENDIENTE,
        ).count()

    return {
        "odontologo_usuario": obtener_odontologo_del_usuario(usuario),
        "puede_ver_pacientes": puede_ver_pacientes(usuario),
        "puede_gestionar_pacientes": puede_gestionar_consultorio(usuario),
        "puede_borrar_pacientes": puede_borrar_pacientes(usuario),
        "puede_gestionar_turnos": puede_gestionar_consultorio(usuario),
        "puede_revisar_solicitudes_publicas": puede_revisar_publicas,
        "solicitudes_publicas_pendientes": solicitudes_publicas_pendientes,
        "puede_ver_turnos": puede_ver_turnos(usuario),
        "puede_configurar_disponibilidad": puede_configurar_disponibilidad(usuario),
        "puede_conectar_google_calendar": puede_conectar_google_calendar(usuario),
        "puede_gestionar_historias_clinicas": puede_gestionar_historias_clinicas(usuario),
        "odontograma_feature_enabled": settings.ODONTOGRAMA_FEATURE_ENABLED,
    }
