from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied

from .roles import puede_gestionar_consultorio, puede_ver_turnos


class RolRequeridoMixin(LoginRequiredMixin, UserPassesTestMixin):
    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            raise PermissionDenied(self.get_permission_denied_message())

        return super().handle_no_permission()


class GestionConsultorioRequeridaMixin(RolRequeridoMixin):
    def test_func(self):
        return puede_gestionar_consultorio(self.request.user)


class VerTurnosRequeridoMixin(RolRequeridoMixin):
    def test_func(self):
        return puede_ver_turnos(self.request.user)
