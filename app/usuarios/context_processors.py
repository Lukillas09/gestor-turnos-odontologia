from .roles import (
    obtener_odontologo_del_usuario,
    puede_borrar_pacientes,
    puede_conectar_google_calendar,
    puede_configurar_disponibilidad,
    puede_gestionar_historias_clinicas,
    puede_gestionar_consultorio,
    puede_ver_pacientes,
    puede_ver_turnos,
)


def permisos_usuario(request):
    usuario = request.user

    return {
        "odontologo_usuario": obtener_odontologo_del_usuario(usuario),
        "puede_ver_pacientes": puede_ver_pacientes(usuario),
        "puede_gestionar_pacientes": puede_gestionar_consultorio(usuario),
        "puede_borrar_pacientes": puede_borrar_pacientes(usuario),
        "puede_gestionar_turnos": puede_gestionar_consultorio(usuario),
        "puede_ver_turnos": puede_ver_turnos(usuario),
        "puede_configurar_disponibilidad": puede_configurar_disponibilidad(usuario),
        "puede_conectar_google_calendar": puede_conectar_google_calendar(usuario),
        "puede_gestionar_historias_clinicas": puede_gestionar_historias_clinicas(usuario),
    }
