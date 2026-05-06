from .roles import (
    puede_configurar_disponibilidad,
    puede_gestionar_consultorio,
    puede_ver_turnos,
)


def permisos_usuario(request):
    usuario = request.user

    return {
        "puede_gestionar_pacientes": puede_gestionar_consultorio(usuario),
        "puede_gestionar_turnos": puede_gestionar_consultorio(usuario),
        "puede_ver_turnos": puede_ver_turnos(usuario),
        "puede_configurar_disponibilidad": puede_configurar_disponibilidad(usuario),
    }
