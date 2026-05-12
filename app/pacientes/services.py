from usuarios.roles import (
    obtener_odontologo_del_usuario,
    puede_configurar_disponibilidad,
    puede_gestionar_consultorio,
)

from .models import PacienteOdontologo


def asegurar_paciente_asociado_a_odontologo(
    paciente,
    odontologo,
    usuario=None,
    motivo="",
):
    if not paciente or not odontologo:
        return None

    asociacion, creada = PacienteOdontologo.objects.get_or_create(
        paciente=paciente,
        odontologo=odontologo,
        activo=True,
        defaults={
            "asignado_por": usuario if usuario and usuario.is_authenticated else None,
            "motivo": motivo,
        },
    )

    campos_a_actualizar = []

    if not creada and usuario and usuario.is_authenticated and not asociacion.asignado_por_id:
        asociacion.asignado_por = usuario
        campos_a_actualizar.append("asignado_por")

    if not creada and motivo and not asociacion.motivo:
        asociacion.motivo = motivo
        campos_a_actualizar.append("motivo")

    if campos_a_actualizar:
        asociacion.save(update_fields=[*campos_a_actualizar, "actualizado_en"])

    return asociacion


def paciente_asociado_a_odontologo(paciente, odontologo):
    if not paciente or not odontologo:
        return False

    return PacienteOdontologo.objects.filter(
        paciente=paciente,
        odontologo=odontologo,
        activo=True,
    ).exists()


def puede_derivar_paciente(usuario, paciente):
    if not usuario.is_authenticated:
        return False

    if puede_gestionar_consultorio(usuario) or puede_configurar_disponibilidad(usuario):
        return True

    odontologo = obtener_odontologo_del_usuario(usuario)

    return paciente_asociado_a_odontologo(paciente, odontologo)


def asignar_paciente_a_odontologo(paciente, odontologo, usuario, motivo=""):
    return asegurar_paciente_asociado_a_odontologo(
        paciente=paciente,
        odontologo=odontologo,
        usuario=usuario,
        motivo=motivo,
    )
