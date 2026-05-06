from pacientes.models import Paciente

from .models import Turno


def confirmar_turno(turno):
    if turno.estado != Turno.Estado.PENDIENTE:
        return turno

    turno.estado = Turno.Estado.CONFIRMADO
    turno.save(update_fields=["estado", "actualizado_en"])
    return turno


def cancelar_turno(turno):
    if turno.estado == Turno.Estado.CANCELADO:
        return turno

    turno.estado = Turno.Estado.CANCELADO
    turno.save(update_fields=["estado", "actualizado_en"])
    return turno


def crear_solicitud_turno_publica(datos):
    paciente = obtener_o_crear_paciente_desde_solicitud(datos)
    odontologo = datos["odontologo"]

    return Turno.objects.create(
        paciente=paciente,
        odontologo=odontologo,
        fecha=datos["fecha"],
        hora_inicio=datos["hora_inicio"],
        duracion_minutos=odontologo.duracion_turno_minutos,
        motivo=datos["motivo"],
        estado=Turno.Estado.PENDIENTE,
    )


def obtener_o_crear_paciente_desde_solicitud(datos):
    documento = datos.get("documento")
    paciente = None

    if documento:
        paciente = Paciente.objects.filter(documento=documento).first()

    if not paciente:
        return Paciente.objects.create(
            nombre=datos["nombre"],
            apellido=datos["apellido"],
            documento=documento,
            telefono=datos["telefono"],
            email=datos["email"],
        )

    paciente.nombre = datos["nombre"]
    paciente.apellido = datos["apellido"]
    paciente.telefono = datos["telefono"]
    paciente.email = datos["email"]
    paciente.save(
        update_fields=[
            "nombre",
            "apellido",
            "telefono",
            "email",
            "actualizado_en",
        ]
    )
    return paciente
