import hashlib
import logging

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from consultorio.services import obtener_configuracion_consultorio
from historias.access_policy import obtener_politica_escritura
from historias.models import AccesoClinicoAuditoria

from .audit import registrar_evento_indicacion
from .integrity import crear_referencia_integridad, crear_sello_indicacion
from .models import IndicacionPaciente, PlantillaIndicacion, PlantillaIndicacionVersion
from .pdf import generar_pdf_indicacion, obtener_logo_bytes
from .permissions import (
    obtener_odontologo_activo,
    puede_anular_indicacion,
    puede_crear_indicacion,
    puede_editar_indicacion,
    puede_emitir_indicacion,
)

logger = logging.getLogger(__name__)

CAMPOS_BORRADOR = (
    "historia_clinica",
    "turno",
    "titulo",
    "procedimiento",
    "contenido",
    "pautas_alarma",
    "recomendaciones_control",
    "observaciones_personalizadas",
    "proximo_control_en",
)
CAMPOS_PLANTILLA = (
    "nombre",
    "procedimiento",
    "titulo_documento",
    "contenido",
    "pautas_alarma",
    "recomendaciones_control",
    "activa",
)


def crear_borrador_indicacion(
    *,
    paciente,
    usuario,
    datos,
    request=None,
    reemplaza_a=None,
    permitir_plantilla_inactiva=False,
):
    if not puede_crear_indicacion(usuario, paciente):
        raise PermissionDenied("No tenés permiso para crear indicaciones para este paciente.")
    odontologo = obtener_odontologo_activo(usuario)
    plantilla = datos.get("plantilla")
    if plantilla and not plantilla.activa and not permitir_plantilla_inactiva:
        raise ValidationError("La plantilla seleccionada ya no está activa.")

    valores = dict(datos)
    if plantilla:
        valores.setdefault("titulo", plantilla.titulo_documento)
        valores.setdefault("procedimiento", plantilla.procedimiento)
        valores.setdefault("contenido", plantilla.contenido)
        valores.setdefault("pautas_alarma", plantilla.pautas_alarma)
        valores.setdefault("recomendaciones_control", plantilla.recomendaciones_control)

    _validar_relaciones_borrador(
        paciente=paciente,
        odontologo=odontologo,
        historia=valores.get("historia_clinica"),
        turno=valores.get("turno"),
        reemplaza_a=reemplaza_a,
    )
    with transaction.atomic():
        indicacion = IndicacionPaciente(
            paciente=paciente,
            odontologo=odontologo,
            plantilla=plantilla,
            plantilla_version=plantilla.version if plantilla else None,
            reemplaza_a=reemplaza_a,
            creado_por=usuario,
            actualizado_por=usuario,
            **{campo: valores.get(campo) for campo in CAMPOS_BORRADOR},
        )
        indicacion.save()
        registrar_evento_indicacion(
            request=request,
            usuario=usuario,
            accion=AccesoClinicoAuditoria.Accion.CREAR_BORRADOR_INDICACION,
            indicacion=indicacion,
            politica=obtener_politica_escritura(usuario, paciente),
            motivo="Borrador de indicación creado.",
        )
    return indicacion


def actualizar_borrador_indicacion(*, indicacion, usuario, datos, request=None):
    with transaction.atomic():
        bloqueada = _obtener_indicacion_bloqueada(indicacion.pk)
        if bloqueada.estado != IndicacionPaciente.Estado.BORRADOR:
            registrar_evento_indicacion(
                request=request,
                usuario=usuario,
                accion=AccesoClinicoAuditoria.Accion.INTENTO_EDITAR_INDICACION_EMITIDA,
                indicacion=bloqueada,
                resultado=AccesoClinicoAuditoria.Resultado.DENEGADO,
                politica=obtener_politica_escritura(usuario, bloqueada.paciente),
                motivo="Intento de edición de una indicación no editable.",
            )
            raise PermissionDenied("Una indicación emitida no puede modificarse.")
        if not puede_editar_indicacion(usuario, bloqueada):
            raise PermissionDenied("No tenés permiso para editar esta indicación.")
        _validar_relaciones_borrador(
            paciente=bloqueada.paciente,
            odontologo=bloqueada.odontologo,
            historia=datos.get("historia_clinica"),
            turno=datos.get("turno"),
            reemplaza_a=bloqueada.reemplaza_a,
        )
        for campo in CAMPOS_BORRADOR:
            setattr(bloqueada, campo, datos.get(campo))
        bloqueada.actualizado_por = usuario
        bloqueada.save()
        registrar_evento_indicacion(
            request=request,
            usuario=usuario,
            accion=AccesoClinicoAuditoria.Accion.EDITAR_BORRADOR_INDICACION,
            indicacion=bloqueada,
            politica=obtener_politica_escritura(usuario, bloqueada.paciente),
            motivo="Borrador de indicación actualizado.",
        )
    return bloqueada


def emitir_indicacion(*, indicacion, usuario, request=None):
    nombre_pdf_guardado = ""
    storage_pdf = indicacion._meta.get_field("pdf").storage
    try:
        with transaction.atomic():
            bloqueada = _obtener_indicacion_bloqueada(indicacion.pk)
            if bloqueada.estado == IndicacionPaciente.Estado.EMITIDA:
                return bloqueada
            if bloqueada.estado != IndicacionPaciente.Estado.BORRADOR:
                raise PermissionDenied("La indicación ya no puede emitirse.")
            if not puede_emitir_indicacion(usuario, bloqueada):
                raise PermissionDenied("No tenés permiso para emitir esta indicación.")

            ahora = timezone.now()
            configuracion = obtener_configuracion_consultorio()
            bloqueada.emitida_en = ahora
            bloqueada.emitida_por = usuario
            bloqueada.snapshot_paciente = _snapshot_paciente(bloqueada.paciente)
            bloqueada.snapshot_profesional = _snapshot_profesional(
                bloqueada.odontologo,
                ahora,
            )
            bloqueada.snapshot_consultorio = _snapshot_consultorio(configuracion)
            bloqueada.snapshot_documento = _snapshot_documento(bloqueada)
            bloqueada.referencia_integridad = crear_referencia_integridad(
                indicacion_uuid=bloqueada.uuid,
                snapshots={
                    "paciente": bloqueada.snapshot_paciente,
                    "profesional": bloqueada.snapshot_profesional,
                    "consultorio": bloqueada.snapshot_consultorio,
                    "documento": bloqueada.snapshot_documento,
                },
            )
            bloqueada.snapshot_documento["referencia_integridad"] = bloqueada.referencia_integridad

            pdf_bytes = generar_pdf_indicacion(
                bloqueada,
                logo_bytes=obtener_logo_bytes(configuracion),
            )
            if len(pdf_bytes) > settings.INDICACIONES_PDF_MAX_BYTES:
                raise ValidationError("El PDF generado supera el tamaño máximo configurado.")
            if not pdf_bytes.startswith(b"%PDF-"):
                raise ValidationError("No se pudo generar un PDF válido.")

            bloqueada.pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
            bloqueada.sello_integridad = crear_sello_indicacion(
                indicacion_uuid=bloqueada.uuid,
                snapshots={
                    "paciente": bloqueada.snapshot_paciente,
                    "profesional": bloqueada.snapshot_profesional,
                    "consultorio": bloqueada.snapshot_consultorio,
                    "documento": bloqueada.snapshot_documento,
                },
                pdf_sha256=bloqueada.pdf_sha256,
            )
            contenido_pdf = ContentFile(pdf_bytes, name="documento.pdf")
            contenido_pdf.content_type = "application/pdf"
            bloqueada.pdf.save(
                "documento.pdf",
                contenido_pdf,
                save=False,
            )
            nombre_pdf_guardado = bloqueada.pdf.name
            bloqueada.estado = IndicacionPaciente.Estado.EMITIDA
            bloqueada.email_destino = _email_verificado(bloqueada.paciente)
            if bloqueada.email_destino:
                bloqueada.email_estado = IndicacionPaciente.EstadoEmail.PENDIENTE
                bloqueada.email_clave_idempotencia = f"indicacion-{bloqueada.uuid}-envio-inicial"
            else:
                bloqueada.email_estado = IndicacionPaciente.EstadoEmail.SIN_DESTINO
            bloqueada.actualizado_por = usuario
            bloqueada.save(permitir_emision=True)
            registrar_evento_indicacion(
                request=request,
                usuario=usuario,
                accion=AccesoClinicoAuditoria.Accion.GENERAR_PDF_INDICACION,
                indicacion=bloqueada,
                politica=obtener_politica_escritura(usuario, bloqueada.paciente),
                motivo="PDF de indicación generado.",
            )
            registrar_evento_indicacion(
                request=request,
                usuario=usuario,
                accion=AccesoClinicoAuditoria.Accion.EMITIR_INDICACION,
                indicacion=bloqueada,
                politica=obtener_politica_escritura(usuario, bloqueada.paciente),
                motivo="Indicación emitida.",
            )
            if bloqueada.email_estado == IndicacionPaciente.EstadoEmail.PENDIENTE:
                transaction.on_commit(
                    lambda indicacion_id=bloqueada.pk: _enviar_email_inicial(indicacion_id)
                )
        return bloqueada
    except Exception:
        if nombre_pdf_guardado:
            _limpiar_pdf_fallido(storage_pdf, nombre_pdf_guardado)
        raise


def anular_indicacion(*, indicacion, usuario, motivo, request=None):
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError({"motivo_anulacion": "El motivo es obligatorio."})
    with transaction.atomic():
        bloqueada = _obtener_indicacion_bloqueada(indicacion.pk)
        if not puede_anular_indicacion(usuario, bloqueada):
            raise PermissionDenied("No tenés permiso para anular esta indicación.")
        bloqueada.estado = IndicacionPaciente.Estado.ANULADA
        bloqueada.anulada_en = timezone.now()
        bloqueada.anulada_por = usuario
        bloqueada.motivo_anulacion = motivo
        bloqueada.actualizado_por = usuario
        bloqueada.save(permitir_anulacion=True)
        registrar_evento_indicacion(
            request=request,
            usuario=usuario,
            accion=AccesoClinicoAuditoria.Accion.ANULAR_INDICACION,
            indicacion=bloqueada,
            politica=obtener_politica_escritura(usuario, bloqueada.paciente),
            motivo="Indicación anulada.",
        )
    return bloqueada


def crear_reemplazo_indicacion(*, indicacion, usuario, request=None):
    with transaction.atomic():
        original = _obtener_indicacion_bloqueada(indicacion.pk)
        if original.estado != IndicacionPaciente.Estado.ANULADA:
            raise PermissionDenied("Primero debés anular la indicación a reemplazar.")
        if not puede_crear_indicacion(usuario, original.paciente):
            raise PermissionDenied("No tenés permiso para crear el reemplazo.")
        odontologo = obtener_odontologo_activo(usuario)
        misma_identidad_profesional = original.odontologo_id == odontologo.pk
        existente = (
            IndicacionPaciente.objects.filter(
                reemplaza_a=original,
                odontologo=odontologo,
                estado=IndicacionPaciente.Estado.BORRADOR,
            )
            .order_by("creado_en")
            .first()
        )
        if existente:
            return existente
        reemplazo = crear_borrador_indicacion(
            paciente=original.paciente,
            usuario=usuario,
            datos={
                "plantilla": original.plantilla,
                "historia_clinica": (
                    original.historia_clinica if misma_identidad_profesional else None
                ),
                "turno": original.turno if misma_identidad_profesional else None,
                "titulo": original.titulo,
                "procedimiento": original.procedimiento,
                "contenido": original.contenido,
                "pautas_alarma": original.pautas_alarma,
                "recomendaciones_control": original.recomendaciones_control,
                "observaciones_personalizadas": original.observaciones_personalizadas,
                "proximo_control_en": original.proximo_control_en,
            },
            request=request,
            reemplaza_a=original,
            permitir_plantilla_inactiva=True,
        )
        registrar_evento_indicacion(
            request=request,
            usuario=usuario,
            accion=AccesoClinicoAuditoria.Accion.CREAR_REEMPLAZO_INDICACION,
            indicacion=original,
            politica=obtener_politica_escritura(usuario, original.paciente),
            motivo="Borrador de reemplazo creado.",
        )
    return reemplazo


def crear_plantilla_indicacion(*, usuario, datos):
    if obtener_odontologo_activo(usuario) is None:
        raise PermissionDenied("Se requiere una identidad profesional activa.")
    plantilla = PlantillaIndicacion(
        creado_por=usuario,
        actualizado_por=usuario,
        **{campo: datos.get(campo) for campo in CAMPOS_PLANTILLA},
    )
    plantilla.save()
    return plantilla


def crear_version_plantilla(*, plantilla, usuario, datos, motivo):
    motivo = (motivo or "").strip()
    if obtener_odontologo_activo(usuario) is None:
        raise PermissionDenied("Se requiere una identidad profesional activa.")
    if not motivo:
        raise ValidationError({"motivo_modificacion": "El motivo es obligatorio."})
    with transaction.atomic():
        bloqueada = PlantillaIndicacion.objects.select_for_update().get(pk=plantilla.pk)
        PlantillaIndicacionVersion.objects.create(
            plantilla=bloqueada,
            numero_version=bloqueada.version,
            snapshot=_snapshot_plantilla(bloqueada),
            motivo=motivo,
            creado_por=usuario,
        )
        for campo in CAMPOS_PLANTILLA:
            setattr(bloqueada, campo, datos.get(campo))
        bloqueada.version += 1
        bloqueada.actualizado_por = usuario
        bloqueada.save(actualizacion_versionada=True)
    return bloqueada


def _obtener_indicacion_bloqueada(pk):
    return (
        IndicacionPaciente.objects.select_for_update()
        .select_related(
            "paciente",
            "odontologo",
            "odontologo__usuario",
            "historia_clinica",
            "turno",
            "plantilla",
            "reemplaza_a",
        )
        .get(pk=pk)
    )


def _validar_relaciones_borrador(*, paciente, odontologo, historia, turno, reemplaza_a):
    errors = {}
    if historia and (
        historia.paciente_id != paciente.pk or historia.odontologo_id != odontologo.pk
    ):
        errors["historia_clinica"] = "La historia seleccionada no está dentro de tu alcance."
    if turno and (turno.paciente_id != paciente.pk or turno.odontologo_id != odontologo.pk):
        errors["turno"] = "El turno seleccionado no está dentro de tu alcance."
    if reemplaza_a and reemplaza_a.paciente_id != paciente.pk:
        errors["reemplaza_a"] = "El documento reemplazado pertenece a otro paciente."
    if errors:
        raise ValidationError(errors)


def _snapshot_paciente(paciente):
    return {
        "id": paciente.pk,
        "nombre": paciente.nombre,
        "apellido": paciente.apellido,
        "nombre_completo": f"{paciente.nombre} {paciente.apellido}".strip(),
        "documento": paciente.documento or "",
        "fecha_nacimiento": (
            paciente.fecha_nacimiento.isoformat() if paciente.fecha_nacimiento else None
        ),
        "email_utilizado": _email_verificado(paciente),
        "obra_social": paciente.obra_social or "",
    }


def _snapshot_profesional(odontologo, emitida_en):
    usuario = odontologo.usuario
    return {
        "id": odontologo.pk,
        "nombre_completo": usuario.get_full_name().strip() or usuario.username,
        "matricula": odontologo.matricula,
        "especialidad": odontologo.especialidad or "",
        "usuario": usuario.username,
        "emitida_en": timezone.localtime(emitida_en).isoformat(),
    }


def _snapshot_consultorio(configuracion):
    return {
        "nombre": configuracion.nombre_visible,
        "iniciales": configuracion.iniciales,
        "logo_referencia": configuracion.logo.name if configuracion.logo else "",
        "direccion": configuracion.direccion_completa,
        "telefono": configuracion.telefono or "",
        "whatsapp": configuracion.whatsapp or "",
        "email": configuracion.email or "",
        "localidad": configuracion.localidad or "",
        "provincia": configuracion.provincia or "",
        "color_principal": configuracion.color_principal,
    }


def _snapshot_documento(indicacion):
    proximo_control = indicacion.proximo_control_en
    if proximo_control:
        proximo_control = timezone.localtime(proximo_control)
    return {
        "titulo": indicacion.titulo,
        "procedimiento": indicacion.procedimiento,
        "contenido": indicacion.contenido,
        "pautas_alarma": indicacion.pautas_alarma,
        "recomendaciones_control": indicacion.recomendaciones_control,
        "observaciones_personalizadas": indicacion.observaciones_personalizadas,
        "proximo_control": proximo_control.isoformat() if proximo_control else None,
        "proximo_control_display": (
            proximo_control.strftime("%d/%m/%Y %H:%M") if proximo_control else ""
        ),
        "plantilla_id": indicacion.plantilla_id,
        "plantilla_version": indicacion.plantilla_version,
        "turno_id": indicacion.turno_id,
        "historia_clinica_id": indicacion.historia_clinica_id,
        "referencia_integridad": indicacion.referencia_integridad,
    }


def _snapshot_plantilla(plantilla):
    return {
        campo: getattr(plantilla, campo)
        for campo in (
            "nombre",
            "procedimiento",
            "titulo_documento",
            "contenido",
            "pautas_alarma",
            "recomendaciones_control",
            "version",
            "activa",
            "creado_por_id",
            "actualizado_por_id",
        )
    }


def _email_verificado(paciente):
    if paciente.activo and paciente.email and paciente.email_verificado_en:
        return paciente.email.strip()
    return ""


def _enviar_email_inicial(indicacion_id):
    from .emails import enviar_indicacion_por_email

    enviar_indicacion_por_email(indicacion_id=indicacion_id, automatico=True)


def _limpiar_pdf_fallido(storage, nombre):
    try:
        storage.delete(nombre)
    except Exception as error:
        logger.warning(
            "No se pudo limpiar un PDF de indicación tras una emisión fallida. "
            "storage=%s error_type=%s",
            storage.__class__.__name__,
            error.__class__.__name__,
        )
