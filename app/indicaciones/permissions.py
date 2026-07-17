from django.conf import settings

from historias.access_policy import obtener_politica_escritura, obtener_politica_lectura
from usuarios.roles import obtener_odontologo_del_usuario


def indicaciones_habilitadas():
    return bool(getattr(settings, "INDICACIONES_POSTOPERATORIAS_ENABLED", False))


def obtener_odontologo_activo(usuario):
    if not getattr(usuario, "is_authenticated", False) or not usuario.is_active:
        return None
    odontologo = obtener_odontologo_del_usuario(usuario)
    if odontologo is None or not odontologo.activo:
        return None
    return odontologo


def puede_ver_indicaciones(usuario, paciente, request=None):
    return indicaciones_habilitadas() and bool(
        obtener_politica_lectura(usuario, paciente, request=request)
    )


def puede_crear_indicacion(usuario, paciente):
    odontologo = obtener_odontologo_activo(usuario)
    return bool(
        indicaciones_habilitadas()
        and odontologo
        and paciente.activo
        and obtener_politica_escritura(usuario, paciente)
    )


def puede_editar_indicacion(usuario, indicacion):
    odontologo = obtener_odontologo_activo(usuario)
    return bool(
        puede_crear_indicacion(usuario, indicacion.paciente)
        and odontologo
        and indicacion.odontologo_id == odontologo.pk
        and indicacion.estado == indicacion.Estado.BORRADOR
    )


def puede_emitir_indicacion(usuario, indicacion):
    return puede_editar_indicacion(usuario, indicacion)


def puede_anular_indicacion(usuario, indicacion):
    odontologo = obtener_odontologo_activo(usuario)
    return bool(
        puede_crear_indicacion(usuario, indicacion.paciente)
        and odontologo
        and indicacion.odontologo_id == odontologo.pk
        and indicacion.estado == indicacion.Estado.EMITIDA
    )


def paciente_tiene_email_actual_verificado(paciente):
    return bool(
        paciente.activo
        and paciente.email
        and paciente.email.strip()
        and paciente.email_verificado_en
    )


def puede_reenviar_indicacion(usuario, indicacion):
    odontologo = obtener_odontologo_activo(usuario)
    return bool(
        puede_crear_indicacion(usuario, indicacion.paciente)
        and odontologo
        and indicacion.odontologo_id == odontologo.pk
        and indicacion.estado == indicacion.Estado.EMITIDA
        and indicacion.pdf
        and (
            indicacion.email_destino or paciente_tiene_email_actual_verificado(indicacion.paciente)
        )
    )
