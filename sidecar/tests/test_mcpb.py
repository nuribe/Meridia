"""La extensión `.mcpb`: que el bundle sea instalable de verdad.

Estas pruebas no son ceremonia. El modo de fallo de una extensión mal formada
es el peor posible para el usuario: Claude Desktop la rechaza o la carga y no
arranca nada, en ambos casos sin un error que diga qué falta. Aquí se valida
el manifiesto contra el esquema oficial de mcpb —el mismo JSON Schema que
publica el CLI— y se comprueba que dentro del ZIP están los archivos en las
rutas que el cliente busca.
"""
import json
import zipfile

import pytest

from pg_diagrammer.mcp import bundle

# El esquema oficial viene con el CLI de mcpb (npm i -g @anthropic-ai/mcpb).
# Si no está instalado se salta esa prueba en vez de fallar: no queremos que
# la suite dependa de Node, pero sí aprovecharlo cuando está.
jsonschema = pytest.importorskip("jsonschema", reason="jsonschema no instalado")


def _esquema(version: str):
    """El esquema de esa versión, buscado donde npm instale los globales.

    Se valida contra el esquema que declara el propio manifiesto, no contra el
    último: subir `manifest_version` deja fuera a los clientes que aún no lo
    entienden, y eso se decide a propósito, no por arrastre del CLI.
    """
    import pathlib
    import subprocess

    raices = []
    try:
        salida = subprocess.run(["npm", "root", "-g"], capture_output=True,
                                text=True, timeout=30)
        if salida.returncode == 0 and salida.stdout.strip():
            raices.append(pathlib.Path(salida.stdout.strip()))
    except (OSError, subprocess.SubprocessError):
        pass
    raices.append(pathlib.Path("/usr/lib/node_modules"))
    for raiz in raices:
        ruta = raiz / f"@anthropic-ai/mcpb/schemas/mcpb-manifest-v{version}.schema.json"
        if ruta.is_file():
            return json.loads(ruta.read_text(encoding="utf-8"))
    return None


@pytest.fixture()
def bundle_creado(tmp_path):
    """Un .mcpb real, con un ejecutable e icono de mentira dentro."""
    exe = tmp_path / "pg-diagrammer-sidecar.exe"
    exe.write_bytes(b"MZ" + b"\0" * 64)  # cabecera PE mínima, basta para el ZIP
    icono = tmp_path / "icon.png"
    icono.write_bytes(b"\x89PNG\r\n\x1a\n")
    return bundle.build(exe, tmp_path / "meridia.mcpb", icono=icono, version="1.2.3")


def test_manifiesto_cumple_el_esquema_oficial():
    datos = bundle.manifest()
    esquema = _esquema(datos["manifest_version"])
    if esquema is None:
        pytest.skip("el esquema de mcpb no está disponible (falta el CLI)")
    jsonschema.validate(datos, esquema)


def test_el_zip_tiene_los_archivos_donde_el_cliente_los_busca(bundle_creado):
    with zipfile.ZipFile(bundle_creado.ruta) as z:
        nombres = set(z.namelist())
        manifiesto = json.loads(z.read("manifest.json"))
    # manifest.json debe estar en la RAÍZ del archivo, no dentro de una carpeta:
    # si se empaqueta la carpeta contenedora, el cliente no lo encuentra.
    assert "manifest.json" in nombres
    assert "server/pg-diagrammer-sidecar.exe" in nombres
    assert "icon.png" in nombres
    assert manifiesto["version"] == "1.2.3"


def test_el_comando_de_windows_apunta_al_exe_con_su_extension():
    cfg = bundle.manifest()["server"]["mcp_config"]
    win = cfg["platform_overrides"]["win32"]
    assert win["command"].endswith("/server/pg-diagrammer-sidecar.exe")
    assert win["command"].startswith("${__dirname}/")
    # Sin --mcp el mismo ejecutable arranca el sidecar HTTP y se queda mudo
    # por stdio: el cliente esperaría el handshake para siempre.
    assert win["args"] == ["--mcp"]
    assert cfg["args"] == ["--mcp"]


def test_sin_icono_no_queda_una_referencia_rota(tmp_path):
    exe = tmp_path / "pg-diagrammer-sidecar.exe"
    exe.write_bytes(b"MZ")
    creado = bundle.build(exe, tmp_path / "b.mcpb", icono=tmp_path / "no-existe.png")
    with zipfile.ZipFile(creado.ruta) as z:
        assert "icon.png" not in z.namelist()
    assert "icon" not in creado.manifiesto


def test_sin_ejecutable_falla_diciendo_qué_compilar(tmp_path):
    with pytest.raises(FileNotFoundError, match="PyInstaller"):
        bundle.build(tmp_path / "no-existe.exe", tmp_path / "b.mcpb")


def test_declara_las_ocho_herramientas_del_servidor():
    """El manifiesto y el servidor no pueden divergir: es lo que el usuario lee
    en la pantalla de instalación para decidir si concede el acceso."""
    import anyio
    from mcp import Client

    from pg_diagrammer.mcp.server import create_server

    servidor = create_server()

    async def go():
        async with Client(servidor) as client:
            return {t.name for t in (await client.list_tools()).tools}

    declaradas = {h["name"] for h in bundle.manifest()["tools"]}
    assert declaradas == anyio.run(go)
