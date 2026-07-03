from .tokens import PUBLIC_ACCESS_SESSION_KEY
from ..models import Turno


def obtener_paciente_id_verificado_desde_session(request):
    data = request.session.get(PUBLIC_ACCESS_SESSION_KEY)

    if not isinstance(data, dict):
        return None

    return data.get("paciente_id")


def obtener_turnos_activos_de_paciente(paciente_id):
    return (
        Turno.objects.select_related("paciente", "odontologo", "odontologo__usuario")
        .filter(
            paciente_id=paciente_id,
            estado__in=[Turno.Estado.PENDIENTE, Turno.Estado.CONFIRMADO],
        )
        .order_by("fecha", "hora_inicio")
    )
