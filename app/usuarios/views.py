from django.contrib.auth.views import LoginView
from django.shortcuts import redirect
from django.urls import reverse
from django.views import View

from .roles import (
    puede_configurar_disponibilidad,
    puede_gestionar_consultorio,
    puede_ver_turnos,
)


class LoginInternoView(LoginView):
    template_name = "registration/login.html"

    def get_success_url(self):
        return obtener_url_inicio_para_usuario(self.request.user)


class InicioView(View):
    def get(self, request):
        if not request.user.is_authenticated:
            return redirect("login")

        return redirect(obtener_url_inicio_para_usuario(request.user))


def obtener_url_inicio_para_usuario(usuario):
    if puede_gestionar_consultorio(usuario):
        return reverse("pacientes:lista")

    if puede_ver_turnos(usuario):
        return reverse("turnos:lista")

    if usuario.is_staff and puede_configurar_disponibilidad(usuario):
        return reverse("admin:index")

    return reverse("turnos:lista")
