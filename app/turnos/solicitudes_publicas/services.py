from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from pacientes.models import Paciente
from pacientes.normalizacion import normalizar_documento
from pacientes.services import asegurar_paciente_asociado_a_odontologo
from turnos.models import SolicitudTurnoPublica, Turno
from turnos.notifications import (
    notificar_solicitud_turno_contacto_existente,
    notificar_solicitud_turno_recibida,
)

from .comparaciones import construir_fotografia_solicitud, detectar_diferencias_datos_paciente


@dataclass(frozen=True)
class ResultadoSolicitudTurnoPublica:
    solicitud: SolicitudTurnoPublica
    turno: Turno
    paciente: Paciente
    paciente_creado: bool
    requiere_revision: bool


def crear_solicitud_publica_de_turno(datos):
    with transaction.atomic():
        documento = normalizar_documento(datos.get("documento"))

        if not documento:
            raise ValidationError({"documento": "Ingresá tu DNI."})

        datos = {**datos, "documento": documento}
        paciente, paciente_creado = _obtener_o_crear_paciente_publico(datos)
        odontologo = datos["odontologo"]
        turno = Turno.objects.create(
            paciente=paciente,
            odontologo=odontologo,
            fecha=datos["fecha"],
            hora_inicio=datos["hora_inicio"],
            duracion_minutos=30,
            motivo=datos.get("motivo", ""),
            estado=Turno.Estado.PENDIENTE,
        )
        asegurar_paciente_asociado_a_odontologo(
            paciente,
            odontologo,
            motivo="Solicitud publica de turno",
        )
        solicitud = _crear_fotografia_solicitud(
            paciente=paciente,
            turno=turno,
            datos=datos,
            paciente_existente=not paciente_creado,
        )
        transaction.on_commit(lambda solicitud_id=solicitud.id: _notificar_solicitud(solicitud_id))

    return ResultadoSolicitudTurnoPublica(
        solicitud=solicitud,
        turno=turno,
        paciente=paciente,
        paciente_creado=paciente_creado,
        requiere_revision=solicitud.requiere_revision,
    )


def _obtener_o_crear_paciente_publico(datos):
    documento = datos["documento"]
    paciente = Paciente.objects.select_for_update().filter(documento=documento).first()

    if paciente:
        return paciente, False

    try:
        with transaction.atomic():
            return (
                Paciente.objects.create(
                    nombre=datos["nombre"],
                    apellido=datos["apellido"],
                    documento=documento,
                    telefono=datos.get("telefono", ""),
                    email=datos.get("email", ""),
                    origen_alta=Paciente.OrigenAlta.SOLICITUD_PUBLICA,
                    estado_validacion_datos=Paciente.EstadoValidacionDatos.PENDIENTE,
                ),
                True,
            )
    except IntegrityError:
        paciente = Paciente.objects.select_for_update().get(documento=documento)
        return paciente, False


def _crear_fotografia_solicitud(paciente, turno, datos, paciente_existente):
    fotografia = construir_fotografia_solicitud(datos)
    diferencias = (
        detectar_diferencias_datos_paciente(paciente, datos)
        if paciente_existente
        else {}
    )
    requiere_revision = bool(diferencias) or not paciente_existente
    estado_revision = (
        SolicitudTurnoPublica.EstadoRevision.PENDIENTE
        if requiere_revision
        else SolicitudTurnoPublica.EstadoRevision.SIN_DIFERENCIAS
    )

    if paciente_existente and not paciente.email:
        requiere_revision = True
        estado_revision = SolicitudTurnoPublica.EstadoRevision.PENDIENTE

    return SolicitudTurnoPublica.objects.create(
        paciente=paciente,
        turno=turno,
        paciente_existente=paciente_existente,
        requiere_revision=requiere_revision,
        diferencias_detectadas=diferencias,
        estado_revision=estado_revision,
        notificacion_contacto_existente_error=(
            "Paciente existente sin email utilizable."
            if paciente_existente and not paciente.email
            else ""
        ),
        **fotografia,
    )


def _notificar_solicitud(solicitud_id):
    solicitud = SolicitudTurnoPublica.objects.select_related(
        "paciente",
        "turno",
        "turno__odontologo",
        "turno__odontologo__usuario",
    ).get(pk=solicitud_id)

    if solicitud.paciente_existente:
        _notificar_paciente_existente(solicitud)
        return

    notificar_solicitud_turno_recibida(solicitud.turno)


def _notificar_paciente_existente(solicitud):
    if not solicitud.paciente.email:
        return

    resultado = notificar_solicitud_turno_contacto_existente(solicitud)

    if resultado.enviada:
        solicitud.notificacion_contacto_existente_en = timezone.now()
        solicitud.notificacion_contacto_existente_error = ""
    else:
        solicitud.notificacion_contacto_existente_error = resultado.motivo[:1000]

    solicitud.save(
        update_fields=[
            "notificacion_contacto_existente_en",
            "notificacion_contacto_existente_error",
            "actualizado_en",
        ]
    )


def revisar_solicitud_publica(
    solicitud_id,
    usuario,
    accion,
    campos_a_actualizar=None,
    observaciones="",
):
    campos_a_actualizar = set(campos_a_actualizar or [])

    with transaction.atomic():
        solicitud = (
            SolicitudTurnoPublica.objects.select_for_update()
            .select_related("paciente")
            .get(pk=solicitud_id)
        )

        if solicitud.estado_revision != SolicitudTurnoPublica.EstadoRevision.PENDIENTE:
            raise ValidationError("Esta solicitud ya fue revisada.")

        paciente = Paciente.objects.select_for_update().get(pk=solicitud.paciente_id)
        campos_validos = {"nombre", "apellido", "telefono", "email"}
        campos_actualizados = []

        if accion == "aplicar_campos":
            for campo in campos_validos & campos_a_actualizar:
                setattr(paciente, campo, getattr(solicitud, f"{campo}_enviado"))
                campos_actualizados.append(campo)

            if campos_actualizados:
                paciente.save(update_fields=[*campos_actualizados, "actualizado_en"])
                solicitud.estado_revision = SolicitudTurnoPublica.EstadoRevision.CAMBIOS_APLICADOS
            else:
                solicitud.estado_revision = SolicitudTurnoPublica.EstadoRevision.REVISADA_SIN_CAMBIOS
        elif accion == "validar_paciente":
            paciente.estado_validacion_datos = Paciente.EstadoValidacionDatos.VALIDADO
            paciente.validado_por = usuario
            paciente.validado_en = timezone.now()
            paciente.save(
                update_fields=[
                    "estado_validacion_datos",
                    "validado_por",
                    "validado_en",
                    "actualizado_en",
                ]
            )
            solicitud.estado_revision = SolicitudTurnoPublica.EstadoRevision.REVISADA_SIN_CAMBIOS
        elif accion == "rechazar":
            solicitud.estado_revision = SolicitudTurnoPublica.EstadoRevision.RECHAZADA
        else:
            solicitud.estado_revision = SolicitudTurnoPublica.EstadoRevision.REVISADA_SIN_CAMBIOS

        solicitud.requiere_revision = False
        solicitud.revisada_por = usuario
        solicitud.revisada_en = timezone.now()
        solicitud.observaciones_revision = observaciones.strip()
        solicitud.campos_actualizados = sorted(campos_actualizados)
        solicitud.campos_descartados = sorted(campos_validos - set(campos_actualizados))
        solicitud.save(
            update_fields=[
                "estado_revision",
                "requiere_revision",
                "revisada_por",
                "revisada_en",
                "observaciones_revision",
                "campos_actualizados",
                "campos_descartados",
                "actualizado_en",
            ]
        )

    return solicitud
