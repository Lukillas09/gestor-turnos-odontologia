from django.db import transaction
from django.utils import timezone

from usuarios.roles import obtener_odontologo_del_usuario

from .domain import CARAS_DENTALES, DIENTES_FDI, color_para_estado
from .models import EstadoDental, Odontograma


def obtener_o_crear_odontograma(paciente):
    if not paciente.activo:
        raise ValueError("No se pueden crear odontogramas para pacientes archivados.")

    odontograma, _ = Odontograma.objects.get_or_create(paciente=paciente)
    return odontograma


def registrar_estado_dental(
    *,
    odontograma,
    diente,
    cara,
    estado_clinico,
    observacion="",
    usuario=None,
    realizado=False,
    historia_clinica=None,
):
    diente = int(diente)

    if diente not in DIENTES_FDI:
        raise ValueError("El diente no pertenece a la nomenclatura FDI.")

    if cara not in CARAS_DENTALES:
        raise ValueError("La cara dental no es válida.")

    odontologo = obtener_odontologo_del_usuario(usuario) if usuario else None

    with transaction.atomic():
        EstadoDental.objects.filter(
            odontograma=odontograma,
            diente=diente,
            cara=cara,
            activo=True,
        ).update(activo=False)

        estado = EstadoDental.objects.create(
            odontograma=odontograma,
            historia_clinica=historia_clinica,
            diente=diente,
            cara=cara,
            estado_clinico=estado_clinico,
            color=color_para_estado(estado_clinico),
            observacion=observacion,
            odontologo=odontologo,
            registrado_por=usuario if usuario and usuario.is_authenticated else None,
            fecha=timezone.localdate(),
            realizado=realizado,
            activo=True,
        )

    return estado
