from turnos.models import SolicitudTurnoPublica


def obtener_solicitudes_publicas_pendientes():
    return SolicitudTurnoPublica.objects.filter(
        estado_revision=SolicitudTurnoPublica.EstadoRevision.PENDIENTE,
    )


def obtener_solicitudes_publicas_para_bandeja():
    return SolicitudTurnoPublica.objects.select_related(
        "paciente",
        "turno",
        "turno__odontologo",
        "turno__odontologo__usuario",
        "revisada_por",
    )
