from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from historias.access_policy import registrar_evento_acceso_clinico
from historias.models import AccesoClinicoAuditoria
from pacientes.models import Paciente
from pacientes.normalizacion import normalizar_documento
from pacientes.services import asegurar_paciente_asociado_a_odontologo
from turnos.excepciones import (
    obtener_horarios_publicos_disponibles,
    validar_intervalo_reserva_publica,
)
from turnos.models import SolicitudTurnoPublica, Turno, bloquear_agendas_de_turnos
from turnos.notifications import (
    notificar_solicitud_turno_contacto_existente,
    notificar_solicitud_turno_recibida,
)

from .comparaciones import construir_fotografia_solicitud, detectar_diferencias_datos_paciente


@dataclass(frozen=True)
class ResultadoSolicitudTurnoPublica:
    solicitud: SolicitudTurnoPublica
    turno: Turno | None
    paciente: Paciente
    paciente_creado: bool
    requiere_revision: bool


def crear_solicitud_publica_de_turno(datos):
    with transaction.atomic():
        documento = normalizar_documento(datos.get("documento"))

        if not documento:
            raise ValidationError({"documento": "Ingresá tu DNI."})

        datos = {**datos, "documento": documento}
        validar_intervalo_reserva_publica(
            datos["fecha"],
            datos["hora_inicio"],
            30,
        )
        horarios = obtener_horarios_publicos_disponibles(
            odontologo=datos["odontologo"],
            fecha=datos["fecha"],
            duracion_minutos=30,
        )

        if datos["hora_inicio"] not in horarios:
            raise ValidationError("Ese horario ya no está disponible. Elegí otro horario.")

        paciente, paciente_creado = _obtener_o_crear_paciente_publico(datos)

        if paciente.esta_archivado:
            turno = None
            solicitud = _crear_fotografia_solicitud(
                paciente=paciente,
                turno=None,
                datos=datos,
                paciente_existente=True,
                paciente_archivado=True,
            )
            registrar_evento_acceso_clinico(
                accion=AccesoClinicoAuditoria.Accion.SOLICITUD_PUBLICA_ARCHIVADO,
                resultado=AccesoClinicoAuditoria.Resultado.PERMITIDO,
                politica=AccesoClinicoAuditoria.Politica.PACIENTE_ARCHIVADO,
                paciente=paciente,
                motivo="Solicitud publica recibida para paciente archivado.",
            )
        else:
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
            transaction.on_commit(
                lambda solicitud_id=solicitud.id: _notificar_solicitud(solicitud_id)
            )

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


def _crear_fotografia_solicitud(
    paciente,
    turno,
    datos,
    paciente_existente,
    paciente_archivado=False,
):
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

    if paciente_archivado:
        requiere_revision = True
        estado_revision = SolicitudTurnoPublica.EstadoRevision.PENDIENTE
        diferencias["estado_operativo"] = {
            "actual": "archivado",
            "enviado": "solicitud_publica",
        }

    return SolicitudTurnoPublica.objects.create(
        paciente=paciente,
        turno=turno,
        paciente_existente=paciente_existente,
        requiere_revision=requiere_revision,
        diferencias_detectadas=diferencias,
        estado_revision=estado_revision,
        notificacion_contacto_existente_error=(
            "Paciente archivado: requiere revision administrativa."
            if paciente_archivado
            else "Paciente existente sin email utilizable."
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

    if not solicitud.turno_id:
        return

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

        paciente = Paciente.objects.select_for_update().get(pk=solicitud.paciente_id)
        _aplicar_revision_solicitud_bloqueada(
            solicitud=solicitud,
            paciente=paciente,
            usuario=usuario,
            accion=accion,
            campos_a_actualizar=campos_a_actualizar,
            observaciones=observaciones,
        )

    return solicitud


def revisar_y_confirmar_solicitud_publica(
    solicitud_id,
    usuario,
    accion,
    campos_a_actualizar=None,
    observaciones="",
    duracion_minutos=30,
):
    campos_a_actualizar = set(campos_a_actualizar or [])

    try:
        duracion = int(duracion_minutos)
    except (TypeError, ValueError):
        raise ValidationError("La duración seleccionada no es válida.")

    if duracion <= 0:
        raise ValidationError("La duración debe ser mayor a cero.")

    with transaction.atomic():
        solicitud = (
            SolicitudTurnoPublica.objects.select_for_update()
            .select_related("paciente", "turno", "turno__odontologo", "turno__odontologo__usuario")
            .get(pk=solicitud_id)
        )

        if not solicitud.turno_id:
            raise ValidationError("Esta solicitud no tiene un turno asociado.")

        bloquear_agendas_de_turnos([(solicitud.turno.odontologo_id, solicitud.turno.fecha)])
        turno = (
            Turno.objects.select_for_update()
            .select_related("paciente", "odontologo", "odontologo__usuario")
            .get(pk=solicitud.turno_id)
        )
        paciente = Paciente.objects.select_for_update().get(pk=solicitud.paciente_id)

        if solicitud.estado_revision != SolicitudTurnoPublica.EstadoRevision.PENDIENTE:
            raise ValidationError("Esta solicitud ya fue revisada.")

        if turno.estado != Turno.Estado.PENDIENTE:
            raise ValidationError("Solo se pueden confirmar turnos pendientes.")

        _aplicar_revision_solicitud_bloqueada(
            solicitud=solicitud,
            paciente=paciente,
            usuario=usuario,
            accion=accion,
            campos_a_actualizar=campos_a_actualizar,
            observaciones=observaciones,
        )

        turno.duracion_minutos = duracion
        turno.estado = Turno.Estado.CONFIRMADO
        turno.save(update_fields=["duracion_minutos", "estado", "actualizado_en"])

    from turnos.google_calendar_sync import sincronizar_turno_actualizado
    from turnos.notifications import notificar_turno_confirmado

    sincronizar_turno_actualizado(turno)
    notificar_turno_confirmado(turno)
    return turno, solicitud


def rechazar_solicitud_publica_y_cancelar_turno(solicitud_id, usuario, motivo):
    motivo = (motivo or "").strip()

    if not motivo:
        raise ValidationError("Indicá un motivo para rechazar la solicitud.")

    with transaction.atomic():
        solicitud = (
            SolicitudTurnoPublica.objects.select_for_update()
            .select_related("paciente", "turno", "turno__odontologo")
            .get(pk=solicitud_id)
        )

        if solicitud.estado_revision != SolicitudTurnoPublica.EstadoRevision.PENDIENTE:
            raise ValidationError("Esta solicitud ya fue revisada.")

        solicitud.estado_revision = SolicitudTurnoPublica.EstadoRevision.RECHAZADA
        solicitud.requiere_revision = False
        solicitud.revisada_por = usuario
        solicitud.revisada_en = timezone.now()
        solicitud.observaciones_revision = motivo
        solicitud.campos_actualizados = []
        solicitud.campos_descartados = sorted(
            set((solicitud.diferencias_detectadas or {}).keys())
            & _campos_actualizables_revision()
        )
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

        turno = None

        if solicitud.turno_id:
            bloquear_agendas_de_turnos([(solicitud.turno.odontologo_id, solicitud.turno.fecha)])
            turno = Turno.objects.select_for_update().get(pk=solicitud.turno_id)
            if turno.estado != Turno.Estado.CANCELADO:
                turno.estado = Turno.Estado.CANCELADO
                turno.motivo_cancelacion_paciente = motivo
                turno.save(update_fields=["estado", "motivo_cancelacion_paciente", "actualizado_en"])

    if turno:
        from turnos.google_calendar_sync import sincronizar_turno_cancelado
        from turnos.notifications import notificar_turno_cancelado
        from turnos.public_access.services import revocar_acciones_publicas_de_turno

        revocar_acciones_publicas_de_turno(turno)
        sincronizar_turno_cancelado(turno)
        notificar_turno_cancelado(turno)

    return solicitud


def cerrar_revision_por_cancelacion_de_turno(turno, usuario=None, motivo=""):
    try:
        solicitud = turno.solicitud_publica
    except SolicitudTurnoPublica.DoesNotExist:
        return None

    if solicitud.estado_revision != SolicitudTurnoPublica.EstadoRevision.PENDIENTE:
        return solicitud

    solicitud.estado_revision = SolicitudTurnoPublica.EstadoRevision.RECHAZADA
    solicitud.requiere_revision = False
    solicitud.revisada_por = usuario if getattr(usuario, "is_authenticated", False) else None
    solicitud.revisada_en = timezone.now()
    solicitud.observaciones_revision = (motivo or "Turno cancelado antes de revisar la solicitud.").strip()
    solicitud.campos_actualizados = []
    solicitud.campos_descartados = sorted(
        set((solicitud.diferencias_detectadas or {}).keys())
        & _campos_actualizables_revision()
    )
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


def _aplicar_revision_solicitud_bloqueada(
    solicitud,
    paciente,
    usuario,
    accion,
    campos_a_actualizar,
    observaciones="",
):
    if solicitud.estado_revision != SolicitudTurnoPublica.EstadoRevision.PENDIENTE:
        raise ValidationError("Esta solicitud ya fue revisada.")

    if not paciente.activo and accion not in {"mantener_pendiente", "rechazar"}:
        raise ValidationError(
            "El paciente esta archivado. Reactivalo manualmente antes de validar o modificar datos."
        )

    campos_validos = _campos_actualizables_revision()
    campos_con_diferencias = set((solicitud.diferencias_detectadas or {}).keys()) & campos_validos
    campos_a_actualizar = set(campos_a_actualizar or [])

    if accion == "mantener_pendiente":
        solicitud.estado_revision = SolicitudTurnoPublica.EstadoRevision.PENDIENTE
        solicitud.requiere_revision = True
        solicitud.observaciones_revision = observaciones.strip()
        solicitud.save(
            update_fields=[
                "estado_revision",
                "requiere_revision",
                "observaciones_revision",
                "actualizado_en",
            ]
        )
        return solicitud

    campos_no_permitidos = campos_a_actualizar - campos_validos
    if campos_no_permitidos:
        raise ValidationError("La selección de campos a actualizar no es válida.")

    if accion == "aplicar_campos":
        campos_sin_diferencias = campos_a_actualizar - campos_con_diferencias
        if campos_sin_diferencias:
            raise ValidationError("Solo se pueden aplicar campos con diferencias detectadas.")

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
    solicitud.campos_descartados = sorted(campos_con_diferencias - set(campos_actualizados))
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


def _campos_actualizables_revision():
    return {"nombre", "apellido", "telefono", "email"}
