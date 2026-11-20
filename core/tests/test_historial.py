import pytest
from django.contrib.auth.models import Group

from core import servicios
from core.historial import OCULTO, historial
from core.models import Rol
from core.permisos import olvidar_permisos
from empresa import servicios as servicios_empresa
from empresa.models import Seccion


@pytest.fixture
def gerente(usuario_con_rol):
    return usuario_con_rol("Gerencia")


@pytest.fixture
def seccion(gerente):
    return servicios_empresa.guardar_seccion(Seccion(codigo="INS", nombre="Instalaciones"), gerente)


def cambiar(seccion, usuario, **datos):
    for campo, valor in datos.items():
        setattr(seccion, campo, valor)
    return servicios_empresa.guardar_seccion(seccion, usuario)


def cambios(entrada):
    return {c.campo: (c.antes, c.despues) for c in entrada.cambios}


def rol_solo_ver_ajustes(gerente, django_user_model):
    """Usuario con un rol que ve Ajustes sin «ver importes» ni nóminas."""
    rol = servicios.guardar_rol(Rol(nombre="Consulta"), {"ajustes": {"ver": True}}, None, gerente)
    usuario = django_user_model.objects.create_user("consulta", password="x")
    usuario.groups.add(rol.grupo)
    return usuario


# ── Contenido de las entradas ──────────────────────────────────────────────────


@pytest.mark.django_db
def test_alta_cambio_y_baja_con_antes_y_despues(gerente, seccion):
    cambiar(seccion, gerente, nombre="Instalaciones eléctricas", plantilla_fases=[])
    servicios_empresa.archivar_seccion(seccion, gerente)

    baja, cambio, alta = historial(seccion, gerente).entradas
    assert [alta.accion, cambio.accion, baja.accion] == ["alta", "cambio", "baja"]
    assert baja.texto_accion == "Baja / anulación"
    assert cambios(alta) == {"Código": ("—", "INS"), "Nombre": ("—", "Instalaciones")}
    assert cambios(cambio) == {"Nombre": ("Instalaciones", "Instalaciones eléctricas")}
    assert cambios(baja) == {"Activa": ("Sí", "No")}


@pytest.mark.django_db
def test_nombres_legibles_cifras_en_espanol_y_json(gerente, seccion):
    cambiar(
        seccion,
        gerente,
        pct_beneficio_defecto="20.50",
        plantilla_fases=[{"nombre": "Única", "pct": 100}],
    )
    entrada = historial(seccion, gerente).entradas[0]
    assert cambios(entrada)["% beneficio objetivo por defecto"] == ("15,00", "20,50")
    assert cambios(entrada)["Plantilla fases"] == ("—", '[{"pct": 100, "nombre": "Única"}]')


@pytest.mark.django_db
def test_guardar_sin_cambios_no_ensucia_el_historial(gerente, seccion):
    cambiar(seccion, gerente)
    assert len(historial(seccion, gerente).entradas) == 1


@pytest.mark.django_db
def test_fk_y_choices_como_texto_y_usuarios_del_rol(gerente, django_user_model):
    compras = django_user_model.objects.create_user("compras1", password="x")
    rol = servicios.guardar_rol(
        Rol(nombre="Compras"), {"ajustes": {"ver": True, "ambito": "asignadas"}}, {compras}, gerente
    )
    servicios.guardar_rol(rol, {"ajustes": {"editar": True}}, set(), gerente)
    servicios.guardar_rol(rol, {}, None, gerente)

    entradas = historial(rol, gerente).entradas
    quita, cambio_permiso, salen, alta_permiso, alta_rol = entradas
    assert cambios(alta_rol)["Grupo"] == ("—", "Compras")  # FK por su nombre
    assert alta_rol.motivo == "Usuarios: entran compras1"
    assert alta_permiso.titulo == "Permisos · Empresa, secciones y ajustes"
    assert cambios(alta_permiso) == {"Ver": ("—", "Sí"), "Ámbito": ("—", "Solo las asignadas")}
    assert salen.motivo == "Usuarios: salen compras1" and not salen.cambios
    assert cambios(cambio_permiso) == {
        "Editar": ("No", "Sí"),
        "Ámbito": ("Solo las asignadas", "Todas las obras"),  # choices por su texto
    }
    assert quita.accion == "baja" and quita.titulo.startswith("Permisos")
    assert not quita.motivo  # el motivo de un guardado no se repite en el siguiente


@pytest.mark.django_db
def test_usuario_por_nombre_completo(client, django_user_model, empresa, seccion):
    ana = django_user_model.objects.create_user(
        "ana", password="x", first_name="Ana", last_name="Ruiz"
    )
    ana.groups.add(Group.objects.get(name="Gerencia"))
    client.force_login(ana)
    datos = {"codigo": "INS", "nombre": "Otra", "pct_beneficio_defecto": "15.00", "activa": "on"}
    client.post(f"/ajustes/secciones/{seccion.pk}/", datos)
    entradas = historial(seccion, ana).entradas
    assert entradas[0].usuario == "Ana Ruiz"
    assert entradas[-1].usuario == "Sistema"  # alta hecha fuera de una petición


# ── Ocultación por permisos ────────────────────────────────────────────────────


@pytest.mark.django_db
def test_importes_ocultos_sin_ver_importes(gerente, seccion, django_user_model, monkeypatch):
    # Hoy ninguna ficha tiene costes: se marca un campo cualquiera como si fuera el coste hora
    monkeypatch.setattr(Seccion, "campos_importe", ("nombre",), raising=False)
    cambiar(seccion, gerente, nombre="Otra", activa=False)
    consulta = rol_solo_ver_ajustes(gerente, django_user_model)

    oculto = cambios(historial(seccion, consulta).entradas[0])
    assert oculto["Nombre"] == (OCULTO, OCULTO)
    assert oculto["Activa"] == ("Sí", "No")  # el resto se ve
    assert cambios(historial(seccion, gerente).entradas[0])["Nombre"] == ("Instalaciones", "Otra")

    rol = Rol.objects.get(nombre="Consulta")
    servicios.guardar_rol(rol, {"ajustes": {"ver_importes": True}}, None, gerente)
    olvidar_permisos(consulta)
    assert cambios(historial(seccion, consulta).entradas[0])["Nombre"] == ("Instalaciones", "Otra")


@pytest.mark.django_db
def test_beneficio_de_la_seccion_oculto_sin_ver_margenes(
    gerente, seccion, usuario_con_rol, django_user_model
):
    cambiar(seccion, gerente, pct_beneficio_defecto="20.00", nombre="Otra")
    campo = "% beneficio objetivo por defecto"
    consulta = rol_solo_ver_ajustes(gerente, django_user_model)  # ve ajustes, sin márgenes

    oculto = cambios(historial(seccion, consulta).entradas[0])
    assert oculto[campo] == (OCULTO, OCULTO)
    assert oculto["Nombre"] == ("Instalaciones", "Otra")
    for quien in (gerente, usuario_con_rol("Oficina técnica")):
        assert cambios(historial(seccion, quien).entradas[0])[campo] == ("15,00", "20,00")

    rol = Rol.objects.get(nombre="Consulta")
    rol.ver_margenes = True
    servicios.guardar_rol(rol, {"ajustes": {"ver": True}}, None, gerente)
    olvidar_permisos(consulta)
    assert cambios(historial(seccion, consulta).entradas[0])[campo] == ("15,00", "20,00")


@pytest.mark.django_db
def test_ficha_de_seccion_oculta_el_beneficio_en_el_historial(
    client, gerente, seccion, django_user_model, empresa
):
    cambiar(seccion, gerente, pct_beneficio_defecto="20.00")
    client.force_login(rol_solo_ver_ajustes(gerente, django_user_model))
    html = client.get(f"/ajustes/secciones/{seccion.pk}/").content.decode()
    assert "(oculto)</span> → (oculto)" in html


@pytest.mark.django_db
def test_nominas_ocultas_sin_permiso_de_nominas(gerente, seccion, usuario_con_rol, monkeypatch):
    monkeypatch.setattr(Seccion, "campos_nominas", ("nombre",), raising=False)
    cambiar(seccion, gerente, nombre="Otra")
    admin = usuario_con_rol("Administración")
    tecnico = usuario_con_rol("Oficina técnica")
    assert cambios(historial(seccion, admin).entradas[0])["Nombre"] == ("Instalaciones", "Otra")
    assert cambios(historial(seccion, tecnico).entradas[0])["Nombre"] == (OCULTO, OCULTO)


# ── Fichas ─────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_ficha_de_seccion_con_historial(cliente_con_rol, empresa):
    cliente = cliente_con_rol("Gerencia")
    cliente.post(
        "/ajustes/secciones/nueva/",
        {
            "codigo": "INS",
            "nombre": "Instalaciones",
            "pct_beneficio_defecto": "15.00",
            "activa": "on",
        },
    )
    seccion = Seccion.objects.get(codigo="INS")
    cliente.post(
        f"/ajustes/secciones/{seccion.pk}/",
        {"codigo": "INS", "nombre": "Fontanería", "pct_beneficio_defecto": "15.00", "activa": "on"},
    )
    html = cliente.get(f"/ajustes/secciones/{seccion.pk}/").content.decode()
    assert "Historial" in html and "u_Gerencia" in html
    assert "Instalaciones</span> → Fontanería" in html
    assert "Ver más" not in html


@pytest.mark.django_db
def test_ficha_nueva_sin_historial(cliente_con_rol, empresa):
    assert (
        "Historial"
        not in cliente_con_rol("Gerencia").get("/ajustes/secciones/nueva/").content.decode()
    )


@pytest.mark.django_db
def test_ficha_de_empresa_con_historial_para_quien_solo_ve(cliente_con_rol, empresa):
    html = cliente_con_rol("Administración").get("/ajustes/empresa/").content.decode()
    assert "Historial" in html and "Construcciones Ejemplo SL" in html


@pytest.mark.django_db
def test_ficha_de_rol_con_permisos_y_usuarios(cliente_con_rol, empresa, django_user_model):
    cliente = cliente_con_rol("Gerencia")
    compras = django_user_model.objects.create_user("compras1", password="x")
    cliente.post(
        "/ajustes/roles/nuevo/",
        {"nombre": "Compras", "descripcion": "", "activo": "on", "usuarios": [compras.pk],
         "ajustes__ver": "on", "ajustes__ambito": "todas", "roles__ambito": "todas"},
    )  # fmt: skip
    rol = Rol.objects.get(nombre="Compras")
    html = cliente.get(f"/ajustes/roles/{rol.pk}/").content.decode()
    assert "Permisos · Empresa, secciones y ajustes" in html
    assert "Usuarios: entran compras1" in html


# ── «Ver más» ──────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_veinte_ultimas_y_ver_mas(cliente_con_rol, empresa):
    cliente = cliente_con_rol("Gerencia")
    gerente = Group.objects.get(name="Gerencia").user_set.get()
    seccion = servicios_empresa.guardar_seccion(Seccion(codigo="INS", nombre="v0"), gerente)
    for n in range(1, 25):
        cambiar(seccion, gerente, nombre=f"v{n}")

    pagina = historial(seccion, gerente)
    assert len(pagina.entradas) == 20 and pagina.url_mas
    assert cambios(pagina.entradas[0])["Nombre"] == ("v23", "v24")  # lo último, primero

    html = cliente.get(f"/ajustes/secciones/{seccion.pk}/").content.decode()
    assert html.count('<span class="badge text-bg-') - html.count("Activa</span>") == 20
    assert 'hx-get="/historial/empresa/seccion/' in html

    resto = cliente.get(pagina.url_mas)
    assert resto.status_code == 200
    filas = resto.content.decode()
    assert "<html" not in filas and filas.count("<tr>") == 5 and "Ver más" not in filas
    assert "Alta" in filas


@pytest.mark.django_db
def test_ver_mas_respeta_los_permisos(cliente_con_rol, empresa, gerente):
    servicios.guardar_rol(Rol(nombre="Compras"), {}, None, gerente)
    rol = Rol.objects.get(nombre="Compras")
    seccion = servicios_empresa.guardar_seccion(Seccion(codigo="INS", nombre="x"), gerente)
    antes = "?antes=2100-01-01T00:00:00%2B00:00"

    sin_rol = cliente_con_rol(None)
    assert sin_rol.get(f"/historial/empresa/seccion/{seccion.pk}/{antes}").status_code == 403
    admin = cliente_con_rol("Administración")
    assert admin.get(f"/historial/empresa/seccion/{seccion.pk}/{antes}").status_code == 200
    assert admin.get(f"/historial/core/rol/{rol.pk}/{antes}").status_code == 403
    assert admin.get(f"/historial/core/usuario/1/{antes}").status_code == 404
    assert admin.get(f"/historial/nada/nada/1/{antes}").status_code == 404
    assert admin.get(f"/historial/empresa/seccion/{seccion.pk}/").status_code == 404


def test_ver_mas_exige_login(client, db):
    respuesta = client.get("/historial/empresa/seccion/1/?antes=2100-01-01")
    assert respuesta.status_code == 302
