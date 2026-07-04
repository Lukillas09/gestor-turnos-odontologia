from django.db.models import Q

from usuarios.roles import (
    obtener_odontologo_del_usuario,
    puede_configurar_disponibilidad,
    puede_gestionar_consultorio,
)


def puede_gestionar_excepciones_globales(usuario):
    return puede_gestionar_consultorio(usuario) or puede_configurar_disponibilidad(usuario)


def puede_ver_excepciones_agenda(usuario):
    return usuario.is_authenticated and (
        puede_gestionar_excepciones_globales(usuario)
        or obtener_odontologo_del_usuario(usuario) is not None
    )


def puede_crear_excepcion_agenda(usuario, odontologo=None):
    if not usuario.is_authenticated:
        return False

    if puede_gestionar_excepciones_globales(usuario):
        return True

    odontologo_usuario = obtener_odontologo_del_usuario(usuario)
    return (
        odontologo_usuario is not None
        and odontologo is not None
        and odontologo.pk == odontologo_usuario.pk
    )


def puede_modificar_excepcion_agenda(usuario, excepcion):
    if not usuario.is_authenticated:
        return False

    if puede_gestionar_excepciones_globales(usuario):
        return True

    odontologo_usuario = obtener_odontologo_del_usuario(usuario)
    return (
        odontologo_usuario is not None
        and excepcion.odontologo_id is not None
        and excepcion.odontologo_id == odontologo_usuario.pk
    )


def limitar_excepciones_por_usuario(queryset, usuario):
    if puede_gestionar_excepciones_globales(usuario):
        return queryset

    odontologo = obtener_odontologo_del_usuario(usuario)

    if odontologo:
        return queryset.filter(Q(odontologo=odontologo) | Q(odontologo__isnull=True))

    return queryset.none()
