from django.core.exceptions import ObjectDoesNotExist


ROL_RECEPCIONISTA = "Recepcionista"
ROL_ODONTOLOGO = "Odontologo"
ROL_ADMINISTRADOR = "Administrador"


def pertenece_a_rol(usuario, nombre_rol):
    if not usuario.is_authenticated:
        return False

    return usuario.groups.filter(name=nombre_rol).exists()


def obtener_odontologo_del_usuario(usuario):
    if not usuario.is_authenticated:
        return None

    try:
        return usuario.perfil_odontologo
    except (AttributeError, ObjectDoesNotExist):
        return None


def puede_gestionar_consultorio(usuario):
    return usuario.is_authenticated and (
        usuario.is_superuser or pertenece_a_rol(usuario, ROL_RECEPCIONISTA)
    )


def puede_ver_turnos(usuario):
    return puede_gestionar_consultorio(usuario) or (
        pertenece_a_rol(usuario, ROL_ODONTOLOGO)
        and obtener_odontologo_del_usuario(usuario) is not None
    )


def puede_configurar_disponibilidad(usuario):
    return usuario.is_authenticated and (
        usuario.is_superuser or pertenece_a_rol(usuario, ROL_ADMINISTRADOR)
    )


def puede_conectar_google_calendar(usuario):
    return usuario.is_authenticated and obtener_odontologo_del_usuario(usuario) is not None


def limitar_turnos_por_usuario(queryset, usuario):
    if puede_gestionar_consultorio(usuario):
        return queryset

    odontologo = obtener_odontologo_del_usuario(usuario)

    if odontologo:
        return queryset.filter(odontologo=odontologo)

    return queryset.none()


def obtener_odontologo_visible(usuario, odontologo_solicitado=None):
    if puede_gestionar_consultorio(usuario):
        return odontologo_solicitado

    return obtener_odontologo_del_usuario(usuario)
