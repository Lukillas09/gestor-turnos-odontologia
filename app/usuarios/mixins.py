from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied

from .roles import (
    puede_archivar_pacientes,
    puede_borrar_pacientes,
    puede_conectar_google_calendar,
    puede_gestionar_consultorio,
    puede_gestionar_historias_clinicas,
    puede_ver_pacientes,
    puede_ver_turnos,
)


class RolRequeridoMixin(LoginRequiredMixin, UserPassesTestMixin):
    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            raise PermissionDenied(self.get_permission_denied_message())

        return super().handle_no_permission()


class GestionConsultorioRequeridaMixin(RolRequeridoMixin):
    def test_func(self):
        return puede_gestionar_consultorio(self.request.user)


class VerPacientesRequeridoMixin(RolRequeridoMixin):
    def test_func(self):
        return puede_ver_pacientes(self.request.user)


class BorrarPacientesRequeridoMixin(RolRequeridoMixin):
    def test_func(self):
        return puede_borrar_pacientes(self.request.user)


class ArchivarPacientesRequeridoMixin(RolRequeridoMixin):
    def test_func(self):
        return puede_archivar_pacientes(self.request.user)


class VerTurnosRequeridoMixin(RolRequeridoMixin):
    def test_func(self):
        return puede_ver_turnos(self.request.user)


class GoogleCalendarRequeridoMixin(RolRequeridoMixin):
    def test_func(self):
        return puede_conectar_google_calendar(self.request.user)


class HistoriaClinicaOdontologoRequeridoMixin(RolRequeridoMixin):
    def test_func(self):
        return puede_gestionar_historias_clinicas(self.request.user)
