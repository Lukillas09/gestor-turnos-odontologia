import base64
import hashlib
import json
import re
from dataclasses import dataclass
from tempfile import SpooledTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Prefetch
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.text import slugify

from pacientes.models import Paciente

from .access_policy import (
    obtener_politica_lectura,
    registrar_evento_acceso_clinico,
)
from .integrity import IntegridadClinicaError
from .models import (
    AccesoClinicoAuditoria,
    HistoriaClinica,
    HistoriaClinicaAdjunto,
    HistoriaClinicaEnmienda,
    HistoriaClinicaVersion,
)

MOTIVOS_EXPORTACION = {
    "solicitud_paciente": "Solicitud del paciente",
    "interconsulta": "Interconsulta autorizada",
    "auditoria": "Auditoría autorizada",
    "otro": "Otro procedimiento autorizado",
}

CLINICAL_EXPORT_INLINE_IMAGE_MAX_BYTES = 5 * 1024 * 1024
MIME_INLINE_POR_EXTENSION = {
    "image/jpeg": {".jpeg", ".jpg"},
    "image/png": {".png"},
    "image/webp": {".webp"},
}
ETIQUETAS_TIPO_ARCHIVO = {
    "application/dicom": "DICOM",
    "application/pdf": "PDF",
    "image/bmp": "BMP",
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/tiff": "TIFF",
    "image/webp": "WebP",
}
PATRON_CONTENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$")
PATRON_PREFIJO_STORAGE = re.compile(r"^[0-9a-fA-F]{32}_(?P<nombre>.+)$")
PATRON_CARACTERES_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class AdjuntoExportado:
    adjunto_id: int
    historia_id: int
    descripcion: str
    nombre_original: str
    nombre_exportado: str
    ruta_zip: str
    content_type: str
    tamano_bytes: int
    sha256: str
    sha256_registrado: str
    data_uri: str
    puede_mostrarse_inline: bool
    motivo_sin_vista_previa: str

    @property
    def titulo(self):
        if self.descripcion.strip():
            return self.descripcion.strip()
        if self.puede_mostrarse_inline:
            return "Fotografía clínica"
        return "Documento adjunto"

    @property
    def tipo_display(self):
        return ETIQUETAS_TIPO_ARCHIVO.get(
            self.content_type,
            _extension_nombre(self.nombre_original).lstrip(".").upper() or "Archivo",
        )

    @property
    def tamano_legible(self):
        if self.tamano_bytes < 1024:
            return f"{self.tamano_bytes} B"
        if self.tamano_bytes < 1024 * 1024:
            return f"{self.tamano_bytes / 1024:.1f} KB".replace(".", ",")
        return f"{self.tamano_bytes / (1024 * 1024):.1f} MB".replace(".", ",")

    def para_manifest(self):
        return {
            "id": self.adjunto_id,
            "adjunto_id": self.adjunto_id,
            "historia_id": self.historia_id,
            "nombre_original": self.nombre_original,
            "nombre_exportado": self.nombre_exportado,
            "ruta": self.ruta_zip,
            "content_type": self.content_type,
            "tamano_bytes": self.tamano_bytes,
            "bytes": self.tamano_bytes,
            "sha256": self.sha256,
            "sha256_registrado": self.sha256_registrado,
            "vista_previa_inline": self.puede_mostrarse_inline,
            "motivo_sin_vista_previa": self.motivo_sin_vista_previa or None,
        }


def exportar_historia_completa(*, historia_referencia, usuario, motivo, request):
    if motivo not in MOTIVOS_EXPORTACION:
        raise ValidationError("El motivo de exportación no es válido.")

    paciente = historia_referencia.paciente
    politica = obtener_politica_lectura(usuario, paciente, request=request)
    if not politica:
        raise PermissionDenied("No tenés permiso para exportar esta historia clínica.")

    archivo_zip = None
    try:
        with transaction.atomic():
            Paciente.objects.select_for_update().get(pk=paciente.pk)
            historias = _obtener_historias_bloqueadas(paciente)
            archivo_zip, nombre = _crear_zip(
                paciente=paciente,
                historias=historias,
                motivo=motivo,
            )
            _auditar_exportacion(
                request=request,
                historia=historia_referencia,
                resultado=AccesoClinicoAuditoria.Resultado.PERMITIDO,
                motivo=(
                    "Exportación clínica generada. "
                    f"Motivo operativo: {MOTIVOS_EXPORTACION[motivo]}."
                ),
            )
            return archivo_zip, nombre
    except Exception:
        if archivo_zip is not None:
            archivo_zip.close()
        _auditar_exportacion(
            request=request,
            historia=historia_referencia,
            resultado=AccesoClinicoAuditoria.Resultado.ERROR,
            motivo="La exportación clínica no pudo completarse.",
        )
        raise


def _obtener_historias_bloqueadas(paciente):
    return list(
        HistoriaClinica.objects.select_for_update(of=("self",))
        .filter(paciente=paciente)
        .select_related(
            "paciente",
            "odontologo",
            "odontologo__usuario",
            "creado_por",
            "actualizado_por",
            "finalizada_por",
        )
        .prefetch_related(
            Prefetch(
                "versiones",
                queryset=HistoriaClinicaVersion.objects.select_related("creado_por").order_by(
                    "numero_version"
                ),
            ),
            Prefetch(
                "enmiendas",
                queryset=HistoriaClinicaEnmienda.objects.select_related(
                    "creado_por",
                    "odontologo",
                    "odontologo__usuario",
                ).order_by("numero_enmienda"),
            ),
            Prefetch(
                "adjuntos",
                queryset=HistoriaClinicaAdjunto.objects.select_related("subido_por").order_by("pk"),
            ),
        )
        .order_by(
            F("numero_asiento").asc(nulls_last=True),
            "fecha_hora_atencion",
            "creado_en",
            "pk",
        )
    )


def _crear_zip(*, paciente, historias, motivo):
    generado_en = timezone.now()
    manifest = {
        "schema_version": 2,
        "generado_en": generado_en.isoformat(),
        "motivo_exportacion": MOTIVOS_EXPORTACION[motivo],
        "identificador_historia": f"HC-PACIENTE-{paciente.pk}",
        "paciente": {
            "id": paciente.pk,
            "nombre": paciente.nombre,
            "apellido": paciente.apellido,
            "documento": paciente.documento or "",
        },
        "advertencia": (
            "Esta exportación no constituye por sí sola una copia autenticada. "
            "La autenticación requiere el procedimiento institucional correspondiente."
        ),
        "asientos": [],
        "adjuntos": [],
    }
    asientos_exportados = []
    archivo_temporal = SpooledTemporaryFile(max_size=16 * 1024 * 1024, mode="w+b")
    try:
        with ZipFile(archivo_temporal, "w", compression=ZIP_DEFLATED) as archivo_zip:
            archivo_zip.writestr("versiones/", b"")
            archivo_zip.writestr("enmiendas/", b"")
            archivo_zip.writestr("adjuntos/", b"")

            for historia in historias:
                clave_asiento = _clave_asiento(historia)
                versiones = list(historia.versiones.all())
                enmiendas = list(historia.enmiendas.all())
                adjuntos = list(historia.adjuntos.all())

                for version in versiones:
                    ruta = (
                        f"versiones/{clave_asiento}/" f"version-{version.numero_version:04d}.json"
                    )
                    archivo_zip.writestr(
                        ruta,
                        _json_legible(
                            {
                                "historia_id": historia.pk,
                                "numero_version": version.numero_version,
                                "creado_en": version.creado_en.isoformat(),
                                "creado_por_id": version.creado_por_id,
                                "motivo": version.motivo,
                                "hash_anterior": version.hash_anterior,
                                "hash_integridad": version.hash_integridad,
                                "snapshot": version.snapshot,
                            }
                        ),
                    )

                for enmienda in enmiendas:
                    ruta = (
                        f"enmiendas/{clave_asiento}/"
                        f"enmienda-{enmienda.numero_enmienda:04d}.json"
                    )
                    archivo_zip.writestr(
                        ruta,
                        _json_legible(
                            {
                                "historia_id": historia.pk,
                                "numero_enmienda": enmienda.numero_enmienda,
                                "texto": enmienda.texto,
                                "motivo": enmienda.motivo,
                                "odontologo_id": enmienda.odontologo_id,
                                "creado_por_id": enmienda.creado_por_id,
                                "creado_en": enmienda.creado_en.isoformat(),
                                "hash_anterior": enmienda.hash_anterior,
                                "hash_integridad": enmienda.hash_integridad,
                            }
                        ),
                    )

                adjuntos_exportados = []
                for adjunto in adjuntos:
                    adjunto_exportado = _copiar_adjunto(
                        archivo_zip,
                        adjunto=adjunto,
                        clave_asiento=clave_asiento,
                    )
                    adjuntos_exportados.append(adjunto_exportado)
                    manifest["adjuntos"].append(adjunto_exportado.para_manifest())

                manifest["asientos"].append(
                    _serializar_asiento(
                        historia,
                        versiones,
                        enmiendas,
                        adjuntos_exportados,
                    )
                )
                asientos_exportados.append(
                    {
                        "historia": historia,
                        "adjuntos": adjuntos_exportados,
                    }
                )

            html = render_to_string(
                "historias/export_historia_clinica.html",
                {
                    "paciente": paciente,
                    "asientos_exportados": asientos_exportados,
                    "generado_en": generado_en,
                    "motivo_exportacion": MOTIVOS_EXPORTACION[motivo],
                },
            )
            archivo_zip.writestr("historia_clinica.html", html.encode("utf-8"))
            archivo_zip.writestr("manifest.json", _json_legible(manifest))

        archivo_temporal.seek(0)
        timestamp = generado_en.strftime("%Y%m%dT%H%M%SZ")
        nombre = f"historia-clinica-paciente-{paciente.pk}-{timestamp}.zip"
        return archivo_temporal, nombre
    except Exception:
        archivo_temporal.close()
        raise


def _serializar_asiento(historia, versiones, enmiendas, adjuntos_exportados):
    usuario = historia.odontologo.usuario
    return {
        "historia_id": historia.pk,
        "numero_asiento": historia.numero_asiento,
        "estado": historia.estado_display,
        "migrada_desde_legacy": historia.migrada_desde_legacy,
        "fecha_hora_atencion": historia.fecha_hora_atencion.isoformat(),
        "hora_atencion_historica_disponible": not historia.migrada_desde_legacy,
        "profesional": {
            "odontologo_id": historia.odontologo_id,
            "usuario_id": usuario.pk,
            "nombre": usuario.get_full_name() or usuario.username,
            "matricula": historia.odontologo.matricula,
        },
        "finalizada_en": (historia.finalizada_en.isoformat() if historia.finalizada_en else None),
        "finalizada_por_id": historia.finalizada_por_id,
        "versiones": [
            {
                "numero": version.numero_version,
                "creado_en": version.creado_en.isoformat(),
                "creado_por_id": version.creado_por_id,
                "hash_anterior": version.hash_anterior,
                "hash_integridad": version.hash_integridad,
            }
            for version in versiones
        ],
        "enmiendas": [
            {
                "numero": enmienda.numero_enmienda,
                "creado_en": enmienda.creado_en.isoformat(),
                "creado_por_id": enmienda.creado_por_id,
                "odontologo_id": enmienda.odontologo_id,
                "hash_anterior": enmienda.hash_anterior,
                "hash_integridad": enmienda.hash_integridad,
            }
            for enmienda in enmiendas
        ],
        "adjuntos": [adjunto.para_manifest() for adjunto in adjuntos_exportados],
    }


def _copiar_adjunto(archivo_zip, *, adjunto, clave_asiento):
    nombre_original = _nombre_original_visible(adjunto.archivo.name, adjunto.pk)
    nombre_exportado = _nombre_exportado_seguro(nombre_original, adjunto.pk)
    ruta = f"adjuntos/{clave_asiento}/{nombre_exportado}"
    content_type = _normalizar_content_type(adjunto.content_type)
    extension = _extension_nombre(nombre_original)
    puede_intentar_inline, motivo_sin_vista_previa = _evaluar_candidato_inline(
        content_type,
        extension,
    )
    contenido_inline = bytearray() if puede_intentar_inline else None
    digest = hashlib.sha256()
    bytes_escritos = 0

    try:
        with adjunto.archivo.open("rb") as origen, archivo_zip.open(ruta, "w") as destino:
            for bloque in iter(lambda: origen.read(1024 * 1024), b""):
                destino.write(bloque)
                digest.update(bloque)
                bytes_escritos += len(bloque)

                if contenido_inline is not None:
                    if len(contenido_inline) + len(bloque) <= (
                        CLINICAL_EXPORT_INLINE_IMAGE_MAX_BYTES
                    ):
                        contenido_inline.extend(bloque)
                    else:
                        contenido_inline = None
                        motivo_sin_vista_previa = "supera_limite"
    except IntegridadClinicaError:
        raise
    except Exception as error:
        raise IntegridadClinicaError(
            "Un adjunto no estuvo disponible durante la exportación; exportación cancelada."
        ) from error

    sha256_exportado = digest.hexdigest()
    if adjunto.sha256 and adjunto.sha256 != sha256_exportado:
        raise IntegridadClinicaError(
            "Un adjunto no coincide con el SHA-256 registrado; exportación cancelada."
        )

    data_uri = ""
    puede_mostrarse_inline = False
    if contenido_inline is not None and puede_intentar_inline:
        contenido = bytes(contenido_inline)
        if _detectar_mime_imagen_segura(contenido) == content_type:
            data_uri = f"data:{content_type};base64,{base64.b64encode(contenido).decode('ascii')}"
            puede_mostrarse_inline = True
            motivo_sin_vista_previa = ""
        else:
            motivo_sin_vista_previa = "tipo_no_coincidente"

    return AdjuntoExportado(
        adjunto_id=adjunto.pk,
        historia_id=adjunto.historia_id,
        descripcion=adjunto.descripcion,
        nombre_original=nombre_original,
        nombre_exportado=nombre_exportado,
        ruta_zip=ruta,
        content_type=content_type,
        tamano_bytes=bytes_escritos,
        sha256=sha256_exportado,
        sha256_registrado=adjunto.sha256,
        data_uri=data_uri,
        puede_mostrarse_inline=puede_mostrarse_inline,
        motivo_sin_vista_previa=motivo_sin_vista_previa,
    )


def _evaluar_candidato_inline(content_type, extension):
    extensiones_compatibles = MIME_INLINE_POR_EXTENSION.get(content_type)
    if not extensiones_compatibles:
        return False, "tipo_no_compatible"
    if extension not in extensiones_compatibles:
        return False, "tipo_no_coincidente"
    return True, ""


def _detectar_mime_imagen_segura(contenido):
    if contenido.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if contenido.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if contenido.startswith(b"RIFF") and contenido[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _normalizar_content_type(content_type):
    normalizado = (content_type or "").split(";", 1)[0].strip().lower()
    if PATRON_CONTENT_TYPE.fullmatch(normalizado):
        return normalizado
    return "application/octet-stream"


def _nombre_original_visible(nombre, adjunto_id):
    nombre_base = str(nombre or "").replace("\\", "/").rsplit("/", 1)[-1]
    nombre_base = PATRON_CARACTERES_CONTROL.sub("", nombre_base).strip()
    coincidencia = PATRON_PREFIJO_STORAGE.fullmatch(nombre_base)
    if coincidencia:
        nombre_base = coincidencia.group("nombre")
    return nombre_base[:255] or f"adjunto-{adjunto_id}"


def _nombre_exportado_seguro(nombre_original, adjunto_id):
    extension = _extension_nombre(nombre_original)
    nombre_sin_extension = nombre_original[: -len(extension)] if extension else nombre_original
    nombre_slug = slugify(nombre_sin_extension, allow_unicode=False).strip("-") or "archivo"
    extension_exportada = extension if extension else ".bin"
    return f"adjunto-{adjunto_id:08d}-{nombre_slug[:80]}{extension_exportada}"


def _extension_nombre(nombre):
    nombre_base = str(nombre or "").replace("\\", "/").rsplit("/", 1)[-1]
    _, separador, extension = nombre_base.rpartition(".")
    if not separador:
        return ""
    extension = f".{extension.lower()}"
    if re.fullmatch(r"\.[a-z0-9]{1,10}", extension):
        return extension
    return ""


def _clave_asiento(historia):
    if historia.numero_asiento:
        return f"asiento-{historia.numero_asiento:04d}"
    return f"borrador-{historia.pk:08d}"


def _json_legible(datos):
    return json.dumps(datos, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def _auditar_exportacion(*, request, historia, resultado, motivo):
    registrar_evento_acceso_clinico(
        request=request,
        accion=AccesoClinicoAuditoria.Accion.EXPORTAR_HISTORIA,
        resultado=resultado,
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
