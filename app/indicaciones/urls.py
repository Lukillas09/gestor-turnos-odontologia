from django.urls import path

from . import views

app_name = "indicaciones"

urlpatterns = [
    path("", views.IndicacionListView.as_view(), name="lista"),
    path("nueva/", views.IndicacionCreateView.as_view(), name="crear"),
    path("<uuid:indicacion_uuid>/", views.IndicacionDetailView.as_view(), name="detalle"),
    path(
        "<uuid:indicacion_uuid>/editar/",
        views.IndicacionUpdateView.as_view(),
        name="editar",
    ),
    path(
        "<uuid:indicacion_uuid>/revisar/",
        views.IndicacionReviewView.as_view(),
        name="revisar",
    ),
    path(
        "<uuid:indicacion_uuid>/emitir/",
        views.IndicacionIssueView.as_view(),
        name="emitir",
    ),
    path(
        "<uuid:indicacion_uuid>/pdf/",
        views.IndicacionPdfView.as_view(),
        name="pdf",
    ),
    path(
        "<uuid:indicacion_uuid>/reenviar/",
        views.IndicacionResendView.as_view(),
        name="reenviar",
    ),
    path(
        "<uuid:indicacion_uuid>/anular/",
        views.IndicacionVoidView.as_view(),
        name="anular",
    ),
    path(
        "<uuid:indicacion_uuid>/reemplazo/",
        views.IndicacionReplacementView.as_view(),
        name="reemplazo",
    ),
]
