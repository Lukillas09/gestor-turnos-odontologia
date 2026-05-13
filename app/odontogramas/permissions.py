from pacientes.services import paciente_asociado_a_odontologo
from usuarios.roles import (
    obtener_odontologo_del_usuario,
    puede_configurar_disponibilidad,
    puede_gestionar_consultorio,
)


def puede_ver_odontograma(usuario, paciente):
    if not usuario.is_authenticated:
        return False

    if puede_gestionar_consultorio(usuario) or puede_configurar_disponibilidad(usuario):
        return True

    return obtener_odontologo_del_usuario(usuario) is not None


def puede_editar_odontograma(usuario, paciente):
    if not usuario.is_authenticated:
        return False

    odontologo = obtener_odontologo_del_usuario(usuario)

    if odontologo is None:
        return False

    return paciente_asociado_a_odontologo(paciente, odontologo)
