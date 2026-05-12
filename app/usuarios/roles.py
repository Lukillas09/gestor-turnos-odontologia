from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q


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


def puede_ver_pacientes(usuario):
    return (
        puede_gestionar_consultorio(usuario)
        or puede_configurar_disponibilidad(usuario)
        or (
            pertenece_a_rol(usuario, ROL_ODONTOLOGO)
            and obtener_odontologo_del_usuario(usuario) is not None
        )
    )


def puede_borrar_pacientes(usuario):
    return puede_ver_pacientes(usuario)


def puede_ver_turnos(usuario):
    return (
        puede_gestionar_consultorio(usuario)
        or puede_configurar_disponibilidad(usuario)
        or (
            pertenece_a_rol(usuario, ROL_ODONTOLOGO)
            and obtener_odontologo_del_usuario(usuario) is not None
        )
    )


def puede_configurar_disponibilidad(usuario):
    return usuario.is_authenticated and (
        usuario.is_superuser or pertenece_a_rol(usuario, ROL_ADMINISTRADOR)
    )


def puede_conectar_google_calendar(usuario):
    return usuario.is_authenticated and obtener_odontologo_del_usuario(usuario) is not None


def puede_gestionar_historias_clinicas(usuario):
    return usuario.is_authenticated and obtener_odontologo_del_usuario(usuario) is not None


def puede_editar_historia_clinica(usuario, historia):
    odontologo = obtener_odontologo_del_usuario(usuario)
    return odontologo is not None and historia.odontologo_id == odontologo.pk


def puede_reintentar_sincronizacion_google_calendar(usuario, turno):
    if not usuario.is_authenticated:
        return False

    if puede_gestionar_consultorio(usuario) or puede_configurar_disponibilidad(usuario):
        return True

    odontologo = obtener_odontologo_del_usuario(usuario)
    return odontologo is not None and turno.odontologo_id == odontologo.pk


def puede_reprogramar_turno(usuario, turno):
    if not usuario.is_authenticated:
        return False

    if turno.estado not in [turno.Estado.PENDIENTE, turno.Estado.CONFIRMADO]:
        return False

    if puede_gestionar_consultorio(usuario):
        return True

    odontologo = obtener_odontologo_del_usuario(usuario)
    return odontologo is not None and turno.odontologo_id == odontologo.pk


def limitar_turnos_por_usuario(queryset, usuario):
    if puede_gestionar_consultorio(usuario) or puede_configurar_disponibilidad(usuario):
        return queryset

    odontologo = obtener_odontologo_del_usuario(usuario)

    if odontologo:
        return queryset.filter(
            Q(odontologo=odontologo)
            | Q(
                paciente__odontologos_asociados__odontologo=odontologo,
                paciente__odontologos_asociados__activo=True,
            )
        ).distinct()

    return queryset.none()


def limitar_pacientes_por_usuario(queryset, usuario):
    if puede_gestionar_consultorio(usuario) or puede_configurar_disponibilidad(usuario):
        return queryset

    odontologo = obtener_odontologo_del_usuario(usuario)

    if odontologo:
        return queryset.filter(
            odontologos_asociados__odontologo=odontologo,
            odontologos_asociados__activo=True,
        ).distinct()

    return queryset.none()


def obtener_odontologo_visible(usuario, odontologo_solicitado=None):
    if puede_gestionar_consultorio(usuario):
        return odontologo_solicitado

    return obtener_odontologo_del_usuario(usuario)
