from .access_policy import (
    datos_clinicos_compartidos_habilitados,
    limitar_historias_clinicas_para_request,
    limitar_historias_clinicas_por_usuario,
    limitar_pacientes_clinicos_para_request,
    limitar_pacientes_clinicos_por_usuario,
    obtener_politica_escritura,
    obtener_politica_lectura,
    puede_crear_historia_de_paciente,
    puede_editar_ficha_odontologica,
    puede_editar_historia_clinica,
    puede_enmendar_historia_clinica,
    puede_modificar_datos_clinicos_de_paciente,
    puede_ver_datos_clinicos_de_paciente,
    registrar_evento_acceso_clinico,
)

__all__ = [
    "datos_clinicos_compartidos_habilitados",
    "limitar_historias_clinicas_para_request",
    "limitar_historias_clinicas_por_usuario",
    "limitar_pacientes_clinicos_para_request",
    "limitar_pacientes_clinicos_por_usuario",
    "obtener_politica_escritura",
    "obtener_politica_lectura",
    "puede_crear_historia_de_paciente",
    "puede_editar_ficha_odontologica",
    "puede_editar_historia_clinica",
    "puede_enmendar_historia_clinica",
    "puede_modificar_datos_clinicos_de_paciente",
    "puede_ver_datos_clinicos_de_paciente",
    "puede_ver_historia_de_paciente",
    "registrar_evento_acceso_clinico",
    "usuario_tiene_alcance_clinico_global",
]


def usuario_tiene_alcance_clinico_global(usuario):
    return datos_clinicos_compartidos_habilitados()


def puede_ver_historia_de_paciente(usuario, paciente, request=None):
    return puede_ver_datos_clinicos_de_paciente(usuario, paciente, request=request)
