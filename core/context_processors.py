from .secciones import secciones_del_selector


def selector_seccion(request):
    """Selector de sección de la barra superior: las secciones activas y la elegida.

    Solo se muestra si la empresa tiene secciones activas. Una sección elegida que ya no está
    activa cuenta como «Todas».
    """
    usuario = getattr(request, "user", None)
    if usuario is None or not usuario.is_authenticated:
        return {}
    secciones = secciones_del_selector()
    elegida = next((s for s in secciones if s.pk == usuario.seccion_elegida_id), None)
    return {"selector_seccion": {"secciones": secciones, "elegida": elegida}}
