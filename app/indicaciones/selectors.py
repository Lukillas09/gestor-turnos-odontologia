from django.db import connection
from django.db.models import Exists, OuterRef
from django.shortcuts import get_object_or_404

from historias.access_policy import (
    limitar_pacientes_clinicos_para_request,
    limitar_pacientes_clinicos_por_usuario,
)
from pacientes.models import Paciente

from .models import IndicacionPaciente, PlantillaIndicacion


def indicaciones_base():
    reemplazos_definitivos = IndicacionPaciente.objects.filter(reemplaza_a=OuterRef("pk")).exclude(
        estado=IndicacionPaciente.Estado.BORRADOR
    )
    return IndicacionPaciente.objects.select_related(
        "paciente",
        "odontologo",
        "odontologo__usuario",
        "historia_clinica",
        "turno",
        "plantilla",
        "emitida_por",
        "anulada_por",
        "reemplaza_a",
    ).annotate(tiene_reemplazo_emitido=Exists(reemplazos_definitivos))


def indicaciones_visibles_para_usuario(usuario, *, request=None):
    pacientes = Paciente.objects.all()
    if request is not None:
        pacientes = limitar_pacientes_clinicos_para_request(pacientes, request, lectura=True)
    else:
        pacientes = limitar_pacientes_clinicos_por_usuario(pacientes, usuario, lectura=True)
    return indicaciones_base().filter(paciente__in=pacientes)


def indicaciones_del_paciente(paciente, usuario, *, request=None):
    return indicaciones_visibles_para_usuario(usuario, request=request).filter(paciente=paciente)


def obtener_indicacion_visible(*, paciente_pk, indicacion_uuid, usuario, request=None):
    return get_object_or_404(
        indicaciones_visibles_para_usuario(usuario, request=request),
        paciente_id=paciente_pk,
        uuid=indicacion_uuid,
    )


def plantillas_activas():
    return PlantillaIndicacion.objects.filter(activa=True).order_by("nombre")


def indicaciones_pendientes_de_email(*, max_intentos):
    queryset = indicaciones_base().filter(
        estado=IndicacionPaciente.Estado.EMITIDA,
        email_estado__in=[
            IndicacionPaciente.EstadoEmail.PENDIENTE,
            IndicacionPaciente.EstadoEmail.ERROR,
        ],
        email_intentos__lt=max_intentos,
    )
    if connection.vendor == "postgresql":
        return queryset.select_for_update(skip_locked=True)
    return queryset.select_for_update()
