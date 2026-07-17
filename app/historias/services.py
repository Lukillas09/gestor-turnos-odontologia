import hashlib
import unicodedata
from datetime import UTC

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from pacientes.models import Paciente

from .access_policy import (
    obtener_politica_escritura,
    obtener_politica_lectura,
    registrar_evento_acceso_clinico,
)
from .integrity import (
    IntegridadClinicaError,
    crear_sello_enmienda,
    crear_sello_version,
    sellos_coinciden,
    serializar_json_canonico,
)
from .models import (
    AccesoClinicoAuditoria,
    HistoriaClinica,
    HistoriaClinicaAdjunto,
    HistoriaClinicaEnmienda,
    HistoriaClinicaVersion,
)
from .permissions import (
    puede_crear_historia_de_paciente,
    puede_editar_historia_clinica,
    puede_enmendar_historia_clinica,
)

MOTIVOS_GENERICOS = {
    "actualizacion",
    "cambio",
    "correccion",
    "edicion",
    "modificacion",
}
MOTIVO_CREACION = "Creación inicial del borrador."
MOTIVO_FINALIZACION = "Finalización del asiento clínico."
MOTIVO_INICIALIZACION_LEGACY = (
    "Inicialización de integridad del registro migrado; no existe trazabilidad previa."
)
CAMPOS_EDITABLES = (
    "fecha_hora_atencion",
    "motivo_consulta",
    "diagnostico",
    "tratamiento_realizado",
    "pieza_dental",
    "observaciones",
    "proximo_control",
)


class HistoriaClinicaFinalizadaError(ValidationError):
    pass


def validar_motivo_cambio(motivo):
    motivo = (motivo or "").strip()
    if len(motivo) < 10:
        raise ValidationError("Explicá el cambio con al menos 10 caracteres.")

    normalizado = "".join(
        caracter
        for caracter in unicodedata.normalize("NFKD", motivo.casefold())
        if not unicodedata.combining(caracter)
    )
    palabras = [palabra.strip(".,;:()[]{}") for palabra in normalizado.split()]
    if len(palabras) <= 2 and any(palabra in MOTIVOS_GENERICOS for palabra in palabras):
        raise ValidationError("Indicá qué información se corrigió o completó.")

    return motivo


@transaction.atomic
def crear_historia_borrador(
    *,
    paciente,
    odontologo,
    usuario,
    datos,
    adjuntos=(),
    estados_odontograma=(),
    request=None,
):
    if odontologo.usuario_id != usuario.pk or not puede_crear_historia_de_paciente(
        usuario, paciente
    ):
        raise PermissionDenied("No tenés permiso para crear esta entrada clínica.")

    valores = _extraer_datos_clinicos(datos)
    historia = HistoriaClinica(
        paciente=paciente,
        odontologo=odontologo,
        creado_por=usuario,
        actualizado_por=usuario,
        **valores,
    )
    historia.save()
    _guardar_adjuntos(historia, adjuntos, usuario)
    _guardar_estados_odontograma(historia, estados_odontograma, usuario)
    version = _crear_version_bloqueada(
        historia,
        usuario=usuario,
        motivo=MOTIVO_CREACION,
        request=request,
    )
    _auditar_escritura(
        request,
        AccesoClinicoAuditoria.Accion.CREAR_BORRADOR,
        historia,
        "Borrador clínico creado.",
    )
    return historia, version


@transaction.atomic
def actualizar_historia_borrador(
    *,
    historia,
    usuario,
    datos,
    motivo_cambio,
    adjuntos=(),
    estados_odontograma=(),
    request=None,
):
    motivo_cambio = validar_motivo_cambio(motivo_cambio)
    historia = (
        HistoriaClinica.objects.select_for_update()
        .select_related("paciente", "odontologo", "odontologo__usuario")
        .get(pk=historia.pk)
    )
    _validar_edicion_borrador(historia, usuario)

    if not historia.versiones.exists():
        _crear_version_bloqueada(
            historia,
            usuario=usuario,
            motivo="Inicialización técnica del borrador existente.",
            request=request,
        )

    valores = _extraer_datos_clinicos(datos)
    campos_cambiados = [
        campo for campo, valor in valores.items() if getattr(historia, campo) != valor
    ]
    hay_adjuntos = bool(adjuntos)
    hay_odontograma = bool(estados_odontograma) and settings.ODONTOGRAMA_FEATURE_ENABLED

    if not campos_cambiados and not hay_adjuntos and not hay_odontograma:
        return historia, None, False

    for campo in campos_cambiados:
        setattr(historia, campo, valores[campo])

    historia.actualizado_por = usuario
    historia.save()
    _guardar_adjuntos(historia, adjuntos, usuario)
    _guardar_estados_odontograma(historia, estados_odontograma, usuario)
    version = _crear_version_bloqueada(
        historia,
        usuario=usuario,
        motivo=motivo_cambio,
        request=request,
    )
    _auditar_escritura(
        request,
        AccesoClinicoAuditoria.Accion.EDITAR_BORRADOR,
        historia,
        f"Borrador clínico actualizado; versión {version.numero_version} creada.",
    )
    return historia, version, True


@transaction.atomic
def crear_version_historia(*, historia, usuario, motivo, request=None):
    historia = (
        HistoriaClinica.objects.select_for_update()
        .select_related("paciente", "odontologo", "odontologo__usuario")
        .get(pk=historia.pk)
    )
    if not puede_editar_historia_clinica(usuario, historia):
        raise PermissionDenied("No tenés permiso para versionar esta entrada clínica.")
    return _crear_version_bloqueada(
        historia,
        usuario=usuario,
        motivo=motivo,
        request=request,
    )


@transaction.atomic
def finalizar_historia_clinica(*, historia, usuario, request=None):
    referencia = HistoriaClinica.objects.only("paciente_id").get(pk=historia.pk)
    Paciente.objects.select_for_update().get(pk=referencia.paciente_id)
    historia = (
        HistoriaClinica.objects.select_for_update()
        .select_related("paciente", "odontologo", "odontologo__usuario")
        .get(pk=historia.pk)
    )
    _validar_edicion_borrador(historia, usuario)

    if not historia.versiones.exists():
        _crear_version_bloqueada(
            historia,
            usuario=usuario,
            motivo="Inicialización técnica del borrador existente.",
            request=request,
        )

    ultimo_folio = (
        HistoriaClinica.objects.filter(
            paciente_id=historia.paciente_id,
            numero_asiento__isnull=False,
        ).aggregate(maximo=Max("numero_asiento"))["maximo"]
        or 0
    )
    historia.numero_asiento = ultimo_folio + 1
    historia.borrador = False
    historia.bloqueada_para_edicion = True
    historia.finalizada_en = timezone.now()
    historia.finalizada_por = usuario
    historia.actualizado_por = usuario
    historia.save()
    version = _crear_version_bloqueada(
        historia,
        usuario=usuario,
        motivo=MOTIVO_FINALIZACION,
        request=request,
    )
    _auditar_escritura(
        request,
        AccesoClinicoAuditoria.Accion.FINALIZAR_HISTORIA,
        historia,
        "Entrada clínica finalizada.",
    )
    return historia, version


@transaction.atomic
def crear_enmienda_historia(
    *,
    historia,
    usuario,
    odontologo,
    texto,
    motivo,
    request=None,
):
    historia = (
        HistoriaClinica.objects.select_for_update()
        .select_related("paciente", "odontologo", "odontologo__usuario")
        .get(pk=historia.pk)
    )
    if historia.borrador or not historia.bloqueada_para_edicion:
        raise ValidationError("Solo se pueden enmendar entradas finalizadas.")
    if odontologo.usuario_id != usuario.pk or not puede_enmendar_historia_clinica(
        usuario, historia
    ):
        raise PermissionDenied("No tenés permiso para agregar esta enmienda.")

    texto = (texto or "").strip()
    motivo = validar_motivo_cambio(motivo)
    if not texto:
        raise ValidationError("El texto de la enmienda es obligatorio.")

    ultima_version = historia.versiones.order_by("-numero_version").first()
    ultima_enmienda = historia.enmiendas.order_by("-numero_enmienda").first()
    if ultima_version is None:
        raise IntegridadClinicaError(
            "El registro legacy debe inicializar su integridad antes de recibir enmiendas."
        )

    numero = (ultima_enmienda.numero_enmienda if ultima_enmienda else 0) + 1
    hash_anterior = (
        ultima_enmienda.hash_integridad if ultima_enmienda else ultima_version.hash_integridad
    )
    creado_en = timezone.now()
    hash_integridad = crear_sello_enmienda(
        historia_id=historia.pk,
        numero_enmienda=numero,
        texto=texto,
        motivo=motivo,
        odontologo_id=odontologo.pk,
        creado_por_id=usuario.pk,
        creado_en=creado_en,
        hash_anterior=hash_anterior,
    )
    enmienda = HistoriaClinicaEnmienda.objects.create(
        historia=historia,
        numero_enmienda=numero,
        texto=texto,
        motivo=motivo,
        odontologo=odontologo,
        creado_por=usuario,
        creado_en=creado_en,
        hash_anterior=hash_anterior,
        hash_integridad=hash_integridad,
    )
    _auditar_escritura(
        request,
        AccesoClinicoAuditoria.Accion.CREAR_ENMIENDA,
        historia,
        f"Enmienda {numero} agregada.",
    )
    return enmienda


@transaction.atomic
def inicializar_integridad_historia_legacy(*, historia, usuario, request=None):
    historia = (
        HistoriaClinica.objects.select_for_update()
        .select_related("paciente", "odontologo", "odontologo__usuario")
        .get(pk=historia.pk)
    )
    if not historia.migrada_desde_legacy or historia.borrador:
        raise ValidationError("La inicialización especial solo corresponde a registros legacy.")
    version_existente = historia.versiones.order_by("numero_version").first()
    if version_existente:
        return version_existente, False

    version = _crear_version_bloqueada(
        historia,
        usuario=usuario,
        motivo=MOTIVO_INICIALIZACION_LEGACY,
        request=request,
    )
    return version, True


def construir_snapshot_historia(historia):
    paciente = historia.paciente
    odontologo = historia.odontologo
    usuario_odontologo = odontologo.usuario
    adjuntos = [
        {
            "id": adjunto.pk,
            "nombre": adjunto.nombre_archivo,
            "content_type": adjunto.content_type,
            "tamano_bytes": adjunto.tamano_bytes,
            "sha256": adjunto.sha256,
        }
        for adjunto in historia.adjuntos.order_by("pk")
    ]
    referencias_odontograma = [
        {
            "id": estado.pk,
            "diente": estado.diente,
            "cara": estado.cara,
            "estado_clinico": estado.estado_clinico,
            "realizado": estado.realizado,
            "creado_en": _instante_iso(estado.creado_en),
        }
        for estado in historia.estados_dentales.order_by("pk")
    ]

    return {
        "schema_version": 1,
        "historia_id": historia.pk,
        "numero_asiento": historia.numero_asiento,
        "estado": "borrador" if historia.borrador else "finalizada",
        "migrada_desde_legacy": historia.migrada_desde_legacy,
        "trazabilidad_previa_disponible": not historia.migrada_desde_legacy,
        "paciente": {
            "id": paciente.pk,
            "nombre": paciente.nombre,
            "apellido": paciente.apellido,
            "documento": paciente.documento or "",
        },
        "profesional": {
            "odontologo_id": odontologo.pk,
            "usuario_id": usuario_odontologo.pk,
            "nombre": usuario_odontologo.get_full_name() or usuario_odontologo.username,
            "matricula": odontologo.matricula,
            "especialidad": odontologo.especialidad,
        },
        "fecha_hora_atencion": _instante_iso(historia.fecha_hora_atencion),
        "hora_atencion_historica_disponible": not historia.migrada_desde_legacy,
        "fecha_compatibilidad": historia.fecha.isoformat(),
        "motivo_consulta": historia.motivo_consulta,
        "diagnostico": historia.diagnostico,
        "tratamiento_realizado": historia.tratamiento_realizado,
        "pieza_dental": historia.pieza_dental,
        "observaciones": historia.observaciones,
        "proximo_control": (
            historia.proximo_control.isoformat() if historia.proximo_control else None
        ),
        "finalizacion": {
            "finalizada_en": (
                _instante_iso(historia.finalizada_en) if historia.finalizada_en else None
            ),
            "finalizada_por_id": historia.finalizada_por_id,
        },
        "creado_por_id": historia.creado_por_id,
        "actualizado_por_id": historia.actualizado_por_id,
        "creado_en": _instante_iso(historia.creado_en),
        "actualizado_en": _instante_iso(historia.actualizado_en),
        "adjuntos": adjuntos,
        "referencias_odontograma": referencias_odontograma,
    }


def verificar_integridad_historia(historia, *, verificar_adjuntos=False):
    historia = (
        HistoriaClinica.objects.select_related(
            "paciente",
            "odontologo",
            "odontologo__usuario",
        )
        .prefetch_related("adjuntos", "estados_dentales", "versiones", "enmiendas")
        .get(pk=historia.pk)
    )
    errores = []
    tipos_error = []

    def agregar_error(tipo, mensaje):
        errores.append(mensaje)
        tipos_error.append(tipo)

    estado_borrador_incoherente = historia.borrador and (
        historia.bloqueada_para_edicion
        or historia.finalizada_en is not None
        or historia.finalizada_por_id is not None
        or historia.numero_asiento is not None
        or historia.migrada_desde_legacy
    )
    estado_final_incoherente = not historia.borrador and (
        not historia.bloqueada_para_edicion
        or historia.finalizada_en is None
        or historia.numero_asiento is None
        or (not historia.migrada_desde_legacy and historia.finalizada_por_id is None)
    )
    if estado_borrador_incoherente or estado_final_incoherente:
        agregar_error("ESTADO_INCOHERENTE", "El estado del asiento clínico es incoherente.")

    folios = list(
        HistoriaClinica._base_manager.filter(
            paciente_id=historia.paciente_id,
            borrador=False,
        )
        .exclude(numero_asiento__isnull=True)
        .order_by("numero_asiento")
        .values_list("numero_asiento", flat=True)
    )
    if folios != list(range(1, len(folios) + 1)):
        agregar_error(
            "FOLIOS_NO_CONTIGUOS",
            "La secuencia de folios del paciente no es continua.",
        )

    versiones = list(historia.versiones.order_by("numero_version", "creado_en"))
    hash_anterior = ""

    if not versiones:
        agregar_error(
            "VERSION_INICIAL_AUSENTE",
            "No existe una versión inicial de integridad.",
        )

    for esperado, version in enumerate(versiones, start=1):
        if version.numero_version != esperado:
            agregar_error(
                "VERSIONES_NO_CONTIGUAS",
                "La secuencia de versiones no es continua.",
            )
        if version.hash_anterior != hash_anterior:
            agregar_error(
                "CADENA_VERSION_INVALIDA",
                f"La versión {version.numero_version} no enlaza con la anterior.",
            )
        sello = crear_sello_version(
            historia_id=version.historia_id,
            numero_version=version.numero_version,
            snapshot=version.snapshot,
            creado_por_id=version.creado_por_id,
            creado_en=version.creado_en,
            motivo=version.motivo,
            hash_anterior=version.hash_anterior,
        )
        if not sellos_coinciden(sello, version.hash_integridad):
            agregar_error(
                "SELLO_VERSION_INVALIDO",
                f"El sello de la versión {version.numero_version} no coincide.",
            )
        hash_anterior = version.hash_integridad

    enmiendas = list(historia.enmiendas.order_by("numero_enmienda", "creado_en"))
    if enmiendas and historia.borrador:
        agregar_error(
            "ENMIENDA_EN_BORRADOR",
            "Un borrador no puede contener enmiendas.",
        )
    for esperado, enmienda in enumerate(enmiendas, start=1):
        if enmienda.numero_enmienda != esperado:
            agregar_error(
                "ENMIENDAS_NO_CONTIGUAS",
                "La secuencia de enmiendas no es continua.",
            )
        if enmienda.hash_anterior != hash_anterior:
            agregar_error(
                "CADENA_ENMIENDA_INVALIDA",
                f"La enmienda {enmienda.numero_enmienda} no enlaza con la anterior.",
            )
        sello = crear_sello_enmienda(
            historia_id=enmienda.historia_id,
            numero_enmienda=enmienda.numero_enmienda,
            texto=enmienda.texto,
            motivo=enmienda.motivo,
            odontologo_id=enmienda.odontologo_id,
            creado_por_id=enmienda.creado_por_id,
            creado_en=enmienda.creado_en,
            hash_anterior=enmienda.hash_anterior,
        )
        if not sellos_coinciden(sello, enmienda.hash_integridad):
            agregar_error(
                "SELLO_ENMIENDA_INVALIDO",
                f"El sello de la enmienda {enmienda.numero_enmienda} no coincide.",
            )
        hash_anterior = enmienda.hash_integridad

    if versiones:
        snapshot_actual = _construir_snapshot_actual_para_verificacion(
            historia,
            snapshot_sellado=versiones[-1].snapshot,
        )
        if serializar_json_canonico(snapshot_actual) != serializar_json_canonico(
            versiones[-1].snapshot
        ):
            agregar_error(
                "SNAPSHOT_ACTUAL_NO_COINCIDE",
                "El asiento actual no coincide con su última versión.",
            )

    adjuntos = list(historia.adjuntos.order_by("pk"))
    for adjunto in adjuntos:
        if not adjunto.sha256:
            agregar_error(
                "ADJUNTO_SIN_SHA256",
                f"El adjunto {adjunto.pk} no tiene SHA-256 registrado.",
            )
            continue
        if not verificar_adjuntos:
            continue
        try:
            observado = _sha256_adjunto(adjunto)
        except Exception:
            agregar_error(
                "ADJUNTO_NO_LEIBLE",
                f"El adjunto {adjunto.pk} no pudo verificarse.",
            )
            continue
        if not sellos_coinciden(observado, adjunto.sha256):
            agregar_error(
                "ADJUNTO_SHA256_INVALIDO",
                f"El SHA-256 del adjunto {adjunto.pk} no coincide.",
            )

    return {
        "valida": not errores,
        "errores": errores,
        "tipos_error": sorted(set(tipos_error)),
        "versiones_verificadas": len(versiones),
        "enmiendas_verificadas": len(enmiendas),
        "adjuntos_verificados": len(adjuntos) if verificar_adjuntos else 0,
    }


def verificar_integridad_historia_auditada(
    historia,
    *,
    request,
    verificar_adjuntos=False,
):
    try:
        resultado = verificar_integridad_historia(
            historia,
            verificar_adjuntos=verificar_adjuntos,
        )
    except Exception:
        _auditar_lectura(
            request,
            AccesoClinicoAuditoria.Accion.VERIFICAR_INTEGRIDAD,
            historia,
            "La verificación de integridad no pudo completarse.",
            resultado=AccesoClinicoAuditoria.Resultado.ERROR,
        )
        raise

    estado = (
        AccesoClinicoAuditoria.Resultado.PERMITIDO
        if resultado["valida"]
        else AccesoClinicoAuditoria.Resultado.ERROR
    )
    _auditar_lectura(
        request,
        AccesoClinicoAuditoria.Accion.VERIFICAR_INTEGRIDAD,
        historia,
        "Verificación de integridad completada.",
        resultado=estado,
    )
    return resultado


def _crear_version_bloqueada(historia, *, usuario, motivo, request=None):
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError("El motivo de la versión es obligatorio.")

    anterior = historia.versiones.order_by("-numero_version").first()
    numero = (anterior.numero_version if anterior else 0) + 1
    hash_anterior = anterior.hash_integridad if anterior else ""
    creado_en = timezone.now()
    snapshot = construir_snapshot_historia(historia)
    hash_integridad = crear_sello_version(
        historia_id=historia.pk,
        numero_version=numero,
        snapshot=snapshot,
        creado_por_id=usuario.pk,
        creado_en=creado_en,
        motivo=motivo,
        hash_anterior=hash_anterior,
    )
    version = HistoriaClinicaVersion.objects.create(
        historia=historia,
        numero_version=numero,
        snapshot=snapshot,
        creado_por=usuario,
        creado_en=creado_en,
        motivo=motivo,
        hash_anterior=hash_anterior,
        hash_integridad=hash_integridad,
    )
    _auditar_escritura(
        request,
        AccesoClinicoAuditoria.Accion.CREAR_VERSION,
        historia,
        f"Versión {numero} creada.",
    )
    return version


def _extraer_datos_clinicos(datos):
    valores = {campo: datos.get(campo) for campo in CAMPOS_EDITABLES}
    if not valores["fecha_hora_atencion"]:
        raise ValidationError("La fecha y hora de atención son obligatorias.")
    return valores


def _validar_edicion_borrador(historia, usuario):
    if historia.bloqueada_para_edicion or not historia.borrador:
        raise HistoriaClinicaFinalizadaError(
            "Una entrada finalizada no puede editarse; agregá una enmienda."
        )
    if not puede_editar_historia_clinica(usuario, historia):
        raise PermissionDenied("Solo el odontólogo responsable puede modificar este borrador.")


def _guardar_adjuntos(historia, adjuntos, usuario):
    for archivo in adjuntos or ():
        HistoriaClinicaAdjunto.objects.create(
            historia=historia,
            archivo=archivo,
            subido_por=usuario,
        )


def _guardar_estados_odontograma(historia, estados, usuario):
    if not settings.ODONTOGRAMA_FEATURE_ENABLED or not estados:
        return

    from odontogramas.services import obtener_o_crear_odontograma, registrar_estado_dental

    odontograma = obtener_o_crear_odontograma(historia.paciente)
    for estado in estados:
        registrar_estado_dental(
            odontograma=odontograma,
            diente=estado["diente"],
            cara=estado["cara"],
            estado_clinico=estado["estado_clinico"],
            observacion=estado["observacion"],
            realizado=estado["realizado"],
            usuario=usuario,
            historia_clinica=historia,
        )


def _sha256_adjunto(adjunto):
    digest = hashlib.sha256()
    with adjunto.archivo.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


def _construir_snapshot_actual_para_verificacion(historia, *, snapshot_sellado):
    snapshot_actual = construir_snapshot_historia(historia)

    # Nombre, documento y perfil profesional quedan congelados como evidencia histórica.
    # Sus cambios legítimos posteriores no deben confundirse con una mutación del asiento.
    campos_historicos = {
        "paciente": ("nombre", "apellido", "documento"),
        "profesional": ("nombre", "matricula", "especialidad"),
    }
    for seccion, campos in campos_historicos.items():
        valores_sellados = snapshot_sellado.get(seccion)
        valores_actuales = snapshot_actual.get(seccion)
        if not isinstance(valores_sellados, dict) or not isinstance(valores_actuales, dict):
            continue
        for campo in campos:
            if campo in valores_sellados:
                valores_actuales[campo] = valores_sellados[campo]

    return snapshot_actual


def _instante_iso(instante):
    return instante.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _auditar_escritura(request, accion, historia, motivo, *, resultado=None):
    if request is None:
        return
    registrar_evento_acceso_clinico(
        request=request,
        accion=accion,
        resultado=resultado or AccesoClinicoAuditoria.Resultado.PERMITIDO,
        politica=(
            obtener_politica_escritura(request.user, historia.paciente)
            or AccesoClinicoAuditoria.Politica.SIN_PERMISO
        ),
        paciente=historia.paciente,
        historia=historia,
        motivo=motivo,
    )


def _auditar_lectura(request, accion, historia, motivo, *, resultado=None):
    if request is None:
        return
    registrar_evento_acceso_clinico(
        request=request,
        accion=accion,
        resultado=resultado or AccesoClinicoAuditoria.Resultado.PERMITIDO,
        politica=(
            obtener_politica_lectura(
                request.user,
                historia.paciente,
                request=request,
            )
            or AccesoClinicoAuditoria.Politica.SIN_PERMISO
        ),
        paciente=historia.paciente,
        historia=historia,
        motivo=motivo,
    )
