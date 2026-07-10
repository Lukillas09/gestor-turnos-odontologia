import logging

from django.core.exceptions import ValidationError
from django.db import OperationalError, ProgrammingError, transaction
from django.db.models.deletion import ProtectedError

from config.storage_backends import SupabaseStorageError

from .models import (
    ANTICIPACION_MINIMA_RESERVA_PUBLICA_MINUTOS_DEFAULT,
    COLOR_PRINCIPAL_DEFAULT,
    CONFIGURACION_CONSULTORIO_PK,
    NOMBRE_COMERCIAL_DEFAULT,
    TEXTO_BIENVENIDA_DEFAULT,
    TITULO_PORTADA_DEFAULT,
    VENTANA_RESERVA_PUBLICA_DIAS_DEFAULT,
    ConfiguracionConsultorio,
)

logger = logging.getLogger(__name__)

ERRORES_LIMPIEZA_LOGO = (
    SupabaseStorageError,
    OSError,
    ValueError,
    ProtectedError,
    ValidationError,
)


def obtener_configuracion_consultorio():
    try:
        return ConfiguracionConsultorio.objects.get(pk=CONFIGURACION_CONSULTORIO_PK)
    except ConfiguracionConsultorio.DoesNotExist:
        return ConfiguracionConsultorio(pk=CONFIGURACION_CONSULTORIO_PK)
    except (OperationalError, ProgrammingError):
        return ConfiguracionConsultorio(pk=CONFIGURACION_CONSULTORIO_PK)


def obtener_o_crear_configuracion_consultorio():
    configuracion, _ = ConfiguracionConsultorio.objects.get_or_create(
        pk=CONFIGURACION_CONSULTORIO_PK,
        defaults={},
    )
    return configuracion


def guardar_configuracion_consultorio(configuracion, form, usuario):
    logo_anterior = _nombre_logo(
        ConfiguracionConsultorio.objects.filter(pk=CONFIGURACION_CONSULTORIO_PK).first()
    )
    quitar_logo = form.cleaned_data.get("quitar_logo", False)

    with transaction.atomic():
        configuracion = form.save(commit=False)
        configuracion.pk = CONFIGURACION_CONSULTORIO_PK
        configuracion.actualizado_por = usuario if usuario.is_authenticated else None

        if quitar_logo:
            configuracion.logo = ""

        configuracion.save()
        form.save_m2m()

        logo_nuevo = _nombre_logo(configuracion)
        _programar_borrado_logo_anterior(logo_anterior, logo_nuevo)

    return configuracion


def restaurar_configuracion_consultorio(usuario):
    with transaction.atomic():
        configuracion = obtener_o_crear_configuracion_consultorio()
        logo_anterior = _nombre_logo(configuracion)
        configuracion.nombre_comercial = NOMBRE_COMERCIAL_DEFAULT
        configuracion.nombre_corto = ""
        configuracion.logo = ""
        configuracion.direccion = ""
        configuracion.localidad = ""
        configuracion.provincia = ""
        configuracion.telefono = ""
        configuracion.whatsapp = ""
        configuracion.email = ""
        configuracion.horario_atencion = ""
        configuracion.titulo_portada = TITULO_PORTADA_DEFAULT
        configuracion.texto_bienvenida = TEXTO_BIENVENIDA_DEFAULT
        configuracion.politica_cancelacion = ""
        configuracion.color_principal = COLOR_PRINCIPAL_DEFAULT
        configuracion.mostrar_direccion = True
        configuracion.mostrar_telefono = True
        configuracion.mostrar_whatsapp = True
        configuracion.mostrar_email = True
        configuracion.mostrar_horario_atencion = True
        configuracion.ventana_reserva_publica_dias = VENTANA_RESERVA_PUBLICA_DIAS_DEFAULT
        configuracion.permitir_reserva_publica_mismo_dia = True
        configuracion.anticipacion_minima_reserva_publica_minutos = (
            ANTICIPACION_MINIMA_RESERVA_PUBLICA_MINUTOS_DEFAULT
        )
        configuracion.actualizado_por = usuario if usuario.is_authenticated else None
        configuracion.save()

        _programar_borrado_logo_anterior(logo_anterior, "")

    return configuracion


def _nombre_logo(configuracion):
    logo = getattr(configuracion, "logo", None)
    return logo.name if logo else ""


def _programar_borrado_logo_anterior(logo_anterior, logo_nuevo):
    if not logo_anterior or logo_anterior == logo_nuevo:
        return

    def borrar_logo_anterior(nombre=logo_anterior):
        _borrar_logo_seguro(nombre)

    transaction.on_commit(borrar_logo_anterior)


def _borrar_logo_seguro(nombre):
    if not nombre:
        return True

    storage = ConfiguracionConsultorio._meta.get_field("logo").storage

    try:
        existe = storage.exists(nombre)
    except ERRORES_LIMPIEZA_LOGO as error:
        _registrar_warning_limpieza_logo("exists", storage, error)
        return False
    except Exception as error:
        # La limpieza del archivo anterior no debe invalidar una configuracion ya confirmada.
        _registrar_warning_limpieza_logo("exists", storage, error)
        return False

    if not existe:
        return True

    try:
        storage.delete(nombre)
    except ERRORES_LIMPIEZA_LOGO as error:
        _registrar_warning_limpieza_logo("delete", storage, error)
        return False
    except Exception as error:
        # La limpieza del archivo anterior no debe invalidar una configuracion ya confirmada.
        _registrar_warning_limpieza_logo("delete", storage, error)
        return False

    return True


def _registrar_warning_limpieza_logo(etapa, storage, error):
    logger.warning(
        "No se pudo eliminar el logo anterior. etapa=%s storage=%s error_type=%s",
        etapa,
        storage.__class__.__name__,
        error.__class__.__name__,
    )
