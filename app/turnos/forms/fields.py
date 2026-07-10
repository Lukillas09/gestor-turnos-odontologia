from django import forms
from django.utils.dateparse import parse_time


def convertir_a_hora(valor):
    if hasattr(valor, "hour"):
        return valor

    hora = parse_time(str(valor))

    if hora is None:
        raise ValueError("Hora inválida")

    return hora


class HorarioDisponibleChoiceField(forms.TypedChoiceField):
    def prepare_value(self, value):
        hora = self._normalizar_hora(value)

        if hora:
            return hora.strftime("%H:%M")

        return super().prepare_value(value)

    def valid_value(self, value):
        return super().valid_value(self.prepare_value(value))

    @staticmethod
    def _normalizar_hora(value):
        if hasattr(value, "hour"):
            return value

        if value in (None, ""):
            return None

        return parse_time(str(value))
