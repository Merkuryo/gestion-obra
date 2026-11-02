from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "empresa"

urlpatterns = [
    path("ajustes/", RedirectView.as_view(pattern_name="empresa:ficha"), name="inicio"),
    path("ajustes/empresa/", views.FichaEmpresa.as_view(), name="ficha"),
    path("ajustes/secciones/", views.ListaSecciones.as_view(), name="secciones"),
    path("ajustes/secciones/nueva/", views.FichaSeccion.as_view(), name="seccion_nueva"),
    path("ajustes/secciones/<int:pk>/", views.FichaSeccion.as_view(), name="seccion"),
    path(
        "ajustes/secciones/<int:pk>/archivar/",
        views.CambiarEstadoSeccion.as_view(accion="archivar"),
        name="seccion_archivar",
    ),
    path(
        "ajustes/secciones/<int:pk>/reactivar/",
        views.CambiarEstadoSeccion.as_view(accion="reactivar"),
        name="seccion_reactivar",
    ),
]
