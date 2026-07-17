from django.core.files.storage import storages


def almacenamiento_indicaciones_privado():
    return storages["clinical_private"]
