from .models import Turno


def cancelar_turno(turno):
    if turno.estado == Turno.Estado.CANCELADO:
        return turno

    turno.estado = Turno.Estado.CANCELADO
    turno.save(update_fields=["estado", "actualizado_en"])
    return turno
