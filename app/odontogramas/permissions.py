from historias.access_policy import (
    puede_modificar_datos_clinicos_de_paciente,
    puede_ver_datos_clinicos_de_paciente,
)


def puede_ver_odontograma(usuario, paciente, request=None):
    return puede_ver_datos_clinicos_de_paciente(usuario, paciente, request=request)


def puede_editar_odontograma(usuario, paciente):
    return puede_modificar_datos_clinicos_de_paciente(usuario, paciente)
