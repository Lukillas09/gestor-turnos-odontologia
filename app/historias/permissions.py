from usuarios.roles import obtener_odontologo_del_usuario


def puede_ver_historia_de_paciente(usuario, paciente):
    odontologo = obtener_odontologo_del_usuario(usuario)
    return odontologo is not None


def puede_crear_historia_de_paciente(usuario, paciente):
    odontologo = obtener_odontologo_del_usuario(usuario)

    if odontologo is None:
        return False

    return paciente.odontologos_asociados.filter(
        odontologo=odontologo,
        activo=True,
    ).exists()


def puede_editar_historia_clinica(usuario, historia):
    odontologo = obtener_odontologo_del_usuario(usuario)
    return odontologo is not None and historia.odontologo_id == odontologo.pk


def limitar_historias_clinicas_por_usuario(queryset, usuario):
    odontologo = obtener_odontologo_del_usuario(usuario)

    if odontologo is None:
        return queryset.none()

    return queryset
