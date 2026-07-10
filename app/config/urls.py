"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from turnos.views import LandingPublicaPacientesView
from usuarios.views import InicioView, LoginInternoView, PerfilUsuarioView

urlpatterns = [
    path("", LandingPublicaPacientesView.as_view(), name="landing_publica"),
    path("inicio/", InicioView.as_view(), name="inicio"),
    path("cuentas/login/", LoginInternoView.as_view(), name="login"),
    path("cuentas/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("perfil/", PerfilUsuarioView.as_view(), name="perfil"),
    path("admin/", admin.site.urls),
    path("pacientes/", include("pacientes.urls")),
    path("turnos/", include("turnos.urls")),
    path("historias/", include("historias.urls")),
    path("odontogramas/", include("odontogramas.urls")),
    path("configuracion/", include("consultorio.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
