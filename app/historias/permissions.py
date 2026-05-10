from django.db.models import Q

from usuarios.roles import obtener_odontologo_del_usuario


def puede_ver_historia_de_paciente(usuario, paciente):
    odontologo = obtener_odontologo_del_usuario(usuario)

    if odontologo is None:
        return False

    return paciente.__class__.objects.filter(pk=paciente.pk).filter(
        _filtro_paciente_relacionado_con_odontologo(odontologo),
    ).exists()


def limitar_historias_clinicas_por_usuario(queryset, usuario):
    odontologo = obtener_odontologo_del_usuario(usuario)

    if odontologo is None:
        return queryset.none()

    return queryset.filter(
        Q(odontologo=odontologo)
        | Q(paciente__turnos__odontologo=odontologo),
    ).distinct()


def _filtro_paciente_relacionado_con_odontologo(odontologo):
    return Q(turnos__odontologo=odontologo) | Q(historias_clinicas__odontologo=odontologo)
