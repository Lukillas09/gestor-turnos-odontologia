MENSAJE_PROTECCION_PUBLICA_NO_DISPONIBLE = (
    "No pudimos completar la operación en este momento. Intentá nuevamente en unos minutos."
)
RETRY_AFTER_PROTECCION_PUBLICA_SECONDS = 60


class ProteccionPublicaNoDisponible(Exception):
    """Indica que la protección compartida no pudo garantizar la operación."""
