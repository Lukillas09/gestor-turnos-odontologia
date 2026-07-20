from django.conf import settings

from .roles import (
    obtener_odontologo_del_usuario,
    puede_archivar_pacientes,
    puede_borrar_pacientes,
    puede_conectar_google_calendar,
    puede_configurar_disponibilidad,
    puede_gestionar_catalogo_servicios,
    puede_gestionar_consultorio,
    puede_gestionar_historias_clinicas,
    puede_revisar_solicitudes_publicas,
    puede_ver_configuracion_servicios,
    puede_ver_pacientes,
    puede_ver_turnos,
)


def permisos_usuario(request):
    usuario = request.user
    puede_revisar_publicas = puede_revisar_solicitudes_publicas(usuario)
    acceso_clinico_emergencia = None

    if usuario.is_authenticated:
        from historias.access_policy import obtener_estado_acceso_emergencia

        acceso_clinico_emergencia = obtener_estado_acceso_emergencia(request)

    puede_archivar = puede_archivar_pacientes(usuario)

    return {
        "odontologo_usuario": obtener_odontologo_del_usuario(usuario),
        "puede_ver_pacientes": puede_ver_pacientes(usuario),
        "puede_gestionar_pacientes": puede_gestionar_consultorio(usuario),
        "puede_archivar_pacientes": puede_archivar,
        "puede_borrar_pacientes": puede_borrar_pacientes(usuario),
        "puede_gestionar_turnos": puede_gestionar_consultorio(usuario),
        "puede_revisar_solicitudes_publicas": puede_revisar_publicas,
        "puede_ver_turnos": puede_ver_turnos(usuario),
        "puede_ver_configuracion_servicios": puede_ver_configuracion_servicios(usuario),
        "puede_gestionar_catalogo_servicios": puede_gestionar_catalogo_servicios(usuario),
        "puede_ver_excepciones_agenda": _puede_ver_excepciones_agenda(usuario),
        "puede_configurar_disponibilidad": puede_configurar_disponibilidad(usuario),
        "puede_conectar_google_calendar": puede_conectar_google_calendar(usuario),
        "puede_gestionar_historias_clinicas": puede_gestionar_historias_clinicas(usuario),
        "odontograma_feature_enabled": settings.ODONTOGRAMA_FEATURE_ENABLED,
        "acceso_clinico_emergencia": acceso_clinico_emergencia,
    }


def _puede_ver_excepciones_agenda(usuario):
    try:
        from turnos.excepcion_permissions import puede_ver_excepciones_agenda
    except Exception:
        return False

    return puede_ver_excepciones_agenda(usuario)
