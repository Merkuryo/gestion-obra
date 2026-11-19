from django.urls import path

from . import views, vistas_roles

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("ajustes/roles/", vistas_roles.ListaRoles.as_view(), name="roles"),
    path("ajustes/roles/nuevo/", vistas_roles.FichaRol.as_view(), name="rol_nuevo"),
    path("ajustes/roles/<int:pk>/", vistas_roles.FichaRol.as_view(), name="rol"),
    path(
        "ajustes/roles/<int:pk>/duplicar/",
        vistas_roles.DuplicarRol.as_view(),
        name="rol_duplicar",
    ),
]
