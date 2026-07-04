from turnos.models import SolicitudTurnoPublica


def obtener_solicitudes_publicas_pendientes():
    return SolicitudTurnoPublica.objects.filter(
        estado_revision=SolicitudTurnoPublica.EstadoRevision.PENDIENTE,
    )


def obtener_alertas_administrativas_publicas():
    return obtener_solicitudes_publicas_para_bandeja().filter(
        estado_revision=SolicitudTurnoPublica.EstadoRevision.PENDIENTE,
        turno__isnull=True,
    )


def obtener_turnos_con_revision_publica_pendiente(queryset):
    return queryset.filter(
        solicitud_publica__estado_revision=SolicitudTurnoPublica.EstadoRevision.PENDIENTE,
    )


def obtener_solicitudes_publicas_para_bandeja():
    return SolicitudTurnoPublica.objects.select_related(
        "paciente",
        "turno",
        "turno__odontologo",
        "turno__odontologo__usuario",
        "revisada_por",
    )
