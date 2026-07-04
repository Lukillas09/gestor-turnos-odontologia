from usuarios.roles import puede_gestionar_consultorio


def puede_configurar_identidad_consultorio(usuario):
    if not usuario.is_authenticated:
        return False

    return usuario.is_superuser or puede_gestionar_consultorio(usuario)
