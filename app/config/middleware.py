import logging

from django.http import HttpResponse, JsonResponse

from .db_errors import es_espera_de_base_agotada, sqlstate_de

logger = logging.getLogger(__name__)

MENSAJE_BASE_OCUPADA = (
    "El sistema está ocupado en este momento. Intentá nuevamente en unos segundos."
)
RETRY_AFTER_BASE_OCUPADA_SECONDS = 5


class ErrorEsperaBaseMiddleware:
    """Convierte una espera de base agotada en un 503 neutral en lugar de un 500.

    Es una red de seguridad para las vistas que no manejan el caso por su cuenta (por ejemplo,
    un `lock_timeout` al tomar el bloqueo de agenda). No reintenta nada: la transacción ya se
    revirtió al salir del `atomic()` y la persona decide si vuelve a enviar. El log solo incluye
    el nombre de la vista, el tipo de error y el SQLSTATE: nunca la ruta (puede llevar
    identificadores), el SQL ni datos del request.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if not es_espera_de_base_agotada(exception):
            return None

        logger.warning(
            "Espera de base agotada. view=%s error_type=%s sqlstate=%s",
            request.resolver_match.view_name if request.resolver_match else "",
            exception.__class__.__name__,
            sqlstate_de(exception),
        )

        response: HttpResponse
        if "application/json" in request.headers.get("Accept", ""):
            response = JsonResponse({"ok": False, "mensaje": MENSAJE_BASE_OCUPADA}, status=503)
        else:
            response = HttpResponse(
                MENSAJE_BASE_OCUPADA,
                status=503,
                content_type="text/plain; charset=utf-8",
            )
        response["Retry-After"] = str(RETRY_AFTER_BASE_OCUPADA_SECONDS)
        return response
