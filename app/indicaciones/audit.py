from historias.access_policy import registrar_evento_acceso_clinico
from historias.models import AccesoClinicoAuditoria

ACCIONES_INDICACIONES = (
    AccesoClinicoAuditoria.Accion.CREAR_BORRADOR_INDICACION,
    AccesoClinicoAuditoria.Accion.EDITAR_BORRADOR_INDICACION,
    AccesoClinicoAuditoria.Accion.VER_INDICACION,
    AccesoClinicoAuditoria.Accion.EMITIR_INDICACION,
    AccesoClinicoAuditoria.Accion.GENERAR_PDF_INDICACION,
    AccesoClinicoAuditoria.Accion.DESCARGAR_PDF_INDICACION,
    AccesoClinicoAuditoria.Accion.ENVIAR_EMAIL_INDICACION,
    AccesoClinicoAuditoria.Accion.ERROR_EMAIL_INDICACION,
    AccesoClinicoAuditoria.Accion.REENVIAR_EMAIL_INDICACION,
    AccesoClinicoAuditoria.Accion.ANULAR_INDICACION,
    AccesoClinicoAuditoria.Accion.CREAR_REEMPLAZO_INDICACION,
    AccesoClinicoAuditoria.Accion.INTENTO_EDITAR_INDICACION_EMITIDA,
    AccesoClinicoAuditoria.Accion.INTENTO_ACCESO_INDICACION,
)


def registrar_evento_indicacion(
    *,
    accion,
    indicacion=None,
    paciente=None,
    request=None,
    usuario=None,
    resultado=AccesoClinicoAuditoria.Resultado.PERMITIDO,
    politica="",
    motivo="",
    identificador="",
):
    paciente = paciente or (indicacion.paciente if indicacion is not None else None)
    identificador = identificador or (str(indicacion.uuid) if indicacion is not None else "")
    return registrar_evento_acceso_clinico(
        request=request,
        usuario=usuario,
        accion=accion,
        resultado=resultado,
        politica=politica,
        paciente=paciente,
        identificador_solicitado=identificador,
        motivo=motivo,
    )


def auditoria_de_indicacion(indicacion):
    return AccesoClinicoAuditoria.objects.filter(
        identificador_solicitado=str(indicacion.uuid),
        accion__in=ACCIONES_INDICACIONES,
    ).select_related("usuario")
