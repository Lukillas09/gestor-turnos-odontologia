from django.shortcuts import redirect

from .selectors import obtener_paciente_id_verificado_desde_session


class AccesoPublicoTurnosRequeridoMixin:
    def dispatch(self, request, *args, **kwargs):
        self.paciente_id_publico = obtener_paciente_id_verificado_desde_session(request)

        if not self.paciente_id_publico:
            return redirect("turnos:acceso_publico_solicitar")

        return super().dispatch(request, *args, **kwargs)
