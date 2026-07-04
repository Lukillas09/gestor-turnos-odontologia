from .permissions import puede_configurar_identidad_consultorio
from .services import obtener_configuracion_consultorio


def perfil_consultorio(request):
    return {
        "configuracion_consultorio": obtener_configuracion_consultorio(),
        "puede_configurar_consultorio": puede_configurar_identidad_consultorio(request.user),
    }
