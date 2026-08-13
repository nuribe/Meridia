"""Servidor MCP: guardas de acceso, bitácora y forma de las respuestas.

Las pruebas hablan el protocolo de verdad —un `mcp.Client` conectado en
memoria al `MCPServer`— en vez de llamar a las funciones Python sueltas. Así
se verifica también lo que el agente ve: los esquemas de entrada, las
anotaciones de solo lectura y el texto del error cuando se le deniega algo.

La introspección se sustituye por el snapshot de muestra de
`test_introspector`, que ya cubre 1:1, N:1, N:M, vista y self-reference.
"""
import pathlib
import tempfile

import anyio
import pytest
from mcp import Client
from mcp.types import Implementation

from pg_diagrammer.activity.log import ActivityLog
from pg_diagrammer.activity.settings import McpSettings, McpSettingsStore
from pg_diagrammer.domain.models import ProfileCreate
from pg_diagrammer.introspection import introspector
from pg_diagrammer.mcp.server import Deps, RegistroDeConexion, create_server
from tests.test_introspector import snap

CLIENTE = Implementation(name="vscode-copilot", version="1.0")


class Harness:
    """Servidor MCP con perfiles reales y la introspección simulada."""

    def __init__(self, monkeypatch, *, enabled=True, allowed=None, ttl=300.0):
        self.data_dir = pathlib.Path(tempfile.mkdtemp())
        self.deps = Deps.build(self.data_dir)
        self.deps.ttl_seconds = ttl
        self.introspecciones = 0

        def fake_introspect(conninfo, dbname):
            self.introspecciones += 1
            return snap()

        monkeypatch.setattr(introspector, "introspect", fake_introspect)

        self.p1 = self._profile("Prod RRHH")
        self.p2 = self._profile("Otra", allow_writes=True)
        McpSettingsStore(self.data_dir).set(
            McpSettings(enabled=enabled, allowed_profiles=allowed or [])
        )
        self.server = create_server(self.deps)

    def _profile(self, name: str, allow_writes: bool = False) -> str:
        return self.deps.profiles.create(ProfileCreate(
            name=name, host="127.0.0.1", user="u", password="p",
            dbname="demo", allow_writes=allow_writes,
        )).id

    def eventos(self) -> list[dict]:
        return self.deps.activity.read(since=0, limit=1000)["events"]


def _run(coro_fn):
    return anyio.run(coro_fn)


async def _call(server, name, args=None):
    async with Client(server, client_info=CLIENTE) as client:
        return await client.call_tool(name, args or {})


def _payload(result):
    assert not result.is_error, result.content
    return result.structured_content


def _texto(result) -> str:
    return " ".join(getattr(c, "text", "") for c in result.content)


# --- superficie -----------------------------------------------------------

def test_solo_hay_herramientas_de_lectura(monkeypatch):
    h = Harness(monkeypatch)

    async def go():
        async with Client(h.server, client_info=CLIENTE) as client:
            return (await client.list_tools()).tools

    tools = _run(go)
    # Inventario explícito: si aparece una tool nueva, este test obliga a
    # mirarla antes de que llegue a un cliente.
    assert {t.name for t in tools} == {
        "meridia_list_profiles",
        "meridia_list_databases",
        "meridia_list_objects",
        "meridia_describe_table",
        "meridia_get_relationships",
        "meridia_export_erd",
        "meridia_explain_query",
        "meridia_run_select",
    }
    # Ninguna escribe, y así se lo declara al cliente.
    assert all(t.annotations and t.annotations.read_only_hint for t in tools)


def test_las_credenciales_no_se_exponen(monkeypatch):
    h = Harness(monkeypatch)
    perfiles = _run(lambda: _call(h.server, "meridia_list_profiles"))
    campos = set(_payload(perfiles)["profiles"][0])
    assert campos == {"profile_id", "name", "engine", "host", "port", "dbname"}


def test_allow_writes_del_perfil_no_habilita_nada(monkeypatch):
    """El perfil `Otra` tiene allow_writes=True: aun así no hay por dónde escribir."""
    h = Harness(monkeypatch)

    async def go():
        async with Client(h.server, client_info=CLIENTE) as client:
            return [t.name for t in (await client.list_tools()).tools]

    nombres = _run(go)
    assert not any(
        v in n for n in nombres
        for v in ("insert", "update", "delete", "write", "execute", "run_query", "drop")
    )


# --- interruptor y allowlist ---------------------------------------------

def test_apagado_deniega_y_lo_registra(monkeypatch):
    h = Harness(monkeypatch, enabled=False)
    r = _run(lambda: _call(h.server, "meridia_list_profiles"))
    assert r.is_error
    assert "MCP_DISABLED" in _texto(r)
    assert "Actividad IA" in _texto(r)  # el error dice dónde encenderlo

    (evento,) = h.eventos()
    assert evento["status"] == "denied"
    assert evento["tool"] == "meridia_list_profiles"
    assert evento["error"]["code"] == "MCP_DISABLED"


def test_encender_desde_la_app_surte_efecto_sin_reiniciar(monkeypatch):
    """El proceso MCP ya está en marcha cuando la app cambia el ajuste."""
    h = Harness(monkeypatch, enabled=False)
    assert _run(lambda: _call(h.server, "meridia_list_profiles")).is_error

    McpSettingsStore(h.data_dir).set(McpSettings(enabled=True))  # lo hace la app

    r = _run(lambda: _call(h.server, "meridia_list_profiles"))
    assert _payload(r)["profiles"]


def test_allowlist_filtra_el_listado_de_perfiles(monkeypatch):
    h = Harness(monkeypatch, allowed=None)
    McpSettingsStore(h.data_dir).set(McpSettings(enabled=True, allowed_profiles=[h.p1]))
    perfiles = _payload(_run(lambda: _call(h.server, "meridia_list_profiles")))["profiles"]
    assert [p["profile_id"] for p in perfiles] == [h.p1]


def test_allowlist_bloquea_el_perfil_excluido(monkeypatch):
    h = Harness(monkeypatch, allowed=None)
    McpSettingsStore(h.data_dir).set(McpSettings(enabled=True, allowed_profiles=[h.p1]))
    r = _run(lambda: _call(h.server, "meridia_list_objects", {
        "profile_id": h.p2, "dbname": "demo",
    }))
    assert r.is_error and "MCP_DISABLED" in _texto(r)
    assert h.eventos()[-1]["status"] == "denied"
    # Denegado antes de tocar la base: no hubo introspección.
    assert h.introspecciones == 0


# --- catálogo -------------------------------------------------------------

def test_list_objects_filtra_y_pagina(monkeypatch):
    h = Harness(monkeypatch)
    todo = _payload(_run(lambda: _call(h.server, "meridia_list_objects", {
        "profile_id": h.p1, "dbname": "demo",
    })))
    assert todo["total"] == 5 and len(todo["items"]) == 5
    assert "snapshot_at" in todo

    ventas = _payload(_run(lambda: _call(h.server, "meridia_list_objects", {
        "profile_id": h.p1, "dbname": "demo", "schema": "ventas",
    })))
    assert {i["name"] for i in ventas["items"]} == {"pedidos", "pedido_items", "v_resumen"}

    vistas = _payload(_run(lambda: _call(h.server, "meridia_list_objects", {
        "profile_id": h.p1, "dbname": "demo", "kind": "view",
    })))
    assert [i["name"] for i in vistas["items"]] == ["v_resumen"]


def test_describe_table_trae_columnas_y_referencias(monkeypatch):
    h = Harness(monkeypatch)
    ficha = _payload(_run(lambda: _call(h.server, "meridia_describe_table", {
        "profile_id": h.p1, "dbname": "demo", "schema": "ventas", "table": "pedidos",
    })))
    assert [c["name"] for c in ficha["table"]["columns"]] == ["id", "anio", "cliente_id"]
    assert ficha["table"]["pk"] == ["id", "anio"]
    assert [r["source"] for r in ficha["referenced_by"]] == ["ventas.pedido_items"]


def test_relationships_completo_y_acotado(monkeypatch):
    h = Harness(monkeypatch)
    todas = _payload(_run(lambda: _call(h.server, "meridia_get_relationships", {
        "profile_id": h.p1, "dbname": "demo",
    })))["relationships"]
    assert len(todas) == 3
    # La cardinalidad la deriva Meridia de las FKs, no la anota nadie:
    # la FK de `fichas` cubre su propia PK → 1:1; las de `pedido_items` no → N:1.
    assert {(r["fk_name"], r["cardinality"]) for r in todas} == {
        ("fichas_producto_fk", "1:1"),
        ("items_pedido_fk", "N:1"),
        ("items_producto_fk", "N:1"),
    }

    acotadas = _payload(_run(lambda: _call(h.server, "meridia_get_relationships", {
        "profile_id": h.p1, "dbname": "demo",
        "tables": ["inventario.fichas", "inventario.productos"],
    })))["relationships"]
    assert [r["fk_name"] for r in acotadas] == ["fichas_producto_fk"]


def test_tabla_inexistente_devuelve_el_envelope_del_proyecto(monkeypatch):
    h = Harness(monkeypatch)
    r = _run(lambda: _call(h.server, "meridia_describe_table", {
        "profile_id": h.p1, "dbname": "demo", "schema": "ventas", "table": "fantasma",
    }))
    assert r.is_error
    assert "[NOT_FOUND]" in _texto(r)
    evento = h.eventos()[-1]
    assert evento["status"] == "error" and evento["error"]["code"] == "NOT_FOUND"


def test_perfil_inexistente(monkeypatch):
    h = Harness(monkeypatch)
    r = _run(lambda: _call(h.server, "meridia_list_databases", {"profile_id": "fantasma"}))
    assert r.is_error and "[NOT_FOUND]" in _texto(r)


# --- bitácora -------------------------------------------------------------

def test_cada_llamada_queda_registrada_con_su_cliente(monkeypatch):
    h = Harness(monkeypatch)
    _run(lambda: _call(h.server, "meridia_list_profiles"))
    _run(lambda: _call(h.server, "meridia_describe_table", {
        "profile_id": h.p1, "dbname": "demo", "schema": "ventas", "table": "pedidos",
    }))
    eventos = h.eventos()
    assert [e["tool"] for e in eventos] == [
        "meridia_list_profiles", "meridia_describe_table",
    ]
    assert {e["client"] for e in eventos} == {"vscode-copilot"}
    assert eventos[1]["target"] == "ventas.pedidos"
    assert eventos[1]["profile_name"] == "Prod RRHH"
    assert all(e["elapsed_ms"] >= 0 for e in eventos)


def test_la_bitacora_no_guarda_filas_ni_credenciales(monkeypatch):
    h = Harness(monkeypatch)
    _run(lambda: _call(h.server, "meridia_list_objects", {
        "profile_id": h.p1, "dbname": "demo",
    }))
    crudo = h.deps.activity.path.read_text(encoding="utf-8")
    assert "\"p\"" not in crudo and "password" not in crudo
    evento = h.eventos()[-1]
    assert evento["row_count"] == 5  # cuántas filas, no cuáles
    assert "items" not in evento


# --- caché propia del proceso --------------------------------------------

def test_el_snapshot_se_cachea_entre_llamadas(monkeypatch):
    h = Harness(monkeypatch, ttl=300.0)
    for _ in range(3):
        _run(lambda: _call(h.server, "meridia_list_objects", {
            "profile_id": h.p1, "dbname": "demo",
        }))
    assert h.introspecciones == 1


def test_el_ttl_fuerza_una_reintrospeccion(monkeypatch):
    h = Harness(monkeypatch, ttl=0.0)
    for _ in range(2):
        _run(lambda: _call(h.server, "meridia_list_objects", {
            "profile_id": h.p1, "dbname": "demo",
        }))
    assert h.introspecciones == 2


def test_force_refresh_reintrospecta(monkeypatch):
    h = Harness(monkeypatch, ttl=300.0)
    _run(lambda: _call(h.server, "meridia_list_objects", {
        "profile_id": h.p1, "dbname": "demo",
    }))
    _run(lambda: _call(h.server, "meridia_list_objects", {
        "profile_id": h.p1, "dbname": "demo", "force_refresh": True,
    }))
    assert h.introspecciones == 2


# --- empaquetado ----------------------------------------------------------

def test_el_paquete_mcp_no_se_importa_a_si_mismo():
    """`pg_diagrammer.mcp` se llama igual que el SDK: comprobamos que no colisionan."""
    import mcp as sdk

    import pg_diagrammer.mcp as propio

    assert sdk is not propio
    assert hasattr(sdk, "Client") and hasattr(propio, "main")


@pytest.mark.parametrize("tool", [
    "meridia_list_profiles", "meridia_list_databases", "meridia_list_objects",
    "meridia_describe_table", "meridia_get_relationships",
])
def test_toda_tool_pasa_por_la_bitacora(monkeypatch, tool):
    """Ninguna llamada puede quedar sin registrar, ni siquiera al fallar."""
    h = Harness(monkeypatch, enabled=False)
    _run(lambda: _call(h.server, tool, {"profile_id": "x", "dbname": "d",
                                        "schema": "s", "table": "t"}))
    assert [e["tool"] for e in h.eventos()] == [tool]


# --- conexión del cliente -------------------------------------------------
#
# Ojo con cómo se prueban: el `Client` en memoria usa un despachador directo
# que NO pasa por el middleware del servidor, así que estas rutas hay que
# verificarlas por separado — la lógica como unidad, y el cableado con un
# proceso real por stdio. Descubrirlo por las malas es fácil: los tests pasan
# en memoria y en producción no se registra nada.


class _CtxFalso:
    def __init__(self, method, params):
        self.method = method
        self.params = params


async def _pasa(ctx):
    return "resultado"


def test_el_middleware_registra_el_handshake(monkeypatch):
    """El caso que costó una tarde: cliente conectado pero panel en blanco.

    Un cliente recién configurado hace el handshake y se queda esperando. Si
    eso no se registrara, sería indistinguible de un servidor que nunca
    arrancó, que es justo la duda que uno tiene mientras lo configura.
    """
    h = Harness(monkeypatch)
    mw = RegistroDeConexion(h.deps)
    ctx = _CtxFalso("initialize", {
        "clientInfo": {"name": "claude-desktop", "version": "0.9.2"},
        "protocolVersion": "2025-11-25",
    })

    assert _run(lambda: mw(ctx, _pasa)) == "resultado"  # no interrumpe la cadena

    (evento,) = h.eventos()
    assert evento["tool"] == "conexión"
    assert evento["client"] == "claude-desktop"
    assert evento["target"] == "claude-desktop 0.9.2"
    assert evento["status"] == "ok"


def test_el_middleware_solo_mira_el_handshake(monkeypatch):
    """El resto de métodos ya los registra `_execute`; duplicarlos sería ruido."""
    h = Harness(monkeypatch)
    mw = RegistroDeConexion(h.deps)
    _run(lambda: mw(_CtxFalso("tools/call", {"name": "meridia_list_profiles"}), _pasa))
    assert h.eventos() == []


def test_la_conexion_se_registra_aunque_el_acceso_este_apagado(monkeypatch):
    """Con el interruptor apagado interesa MÁS ver que el cliente llegó."""
    h = Harness(monkeypatch, enabled=False)
    mw = RegistroDeConexion(h.deps)
    _run(lambda: mw(_CtxFalso("initialize", {"clientInfo": {"name": "x", "version": "1"}}), _pasa))

    (evento,) = h.eventos()
    # Deja anotado en qué estado estaban los interruptores en ese momento, que
    # es la primera pregunta al depurar «no me funciona».
    assert evento["args"]["acceso_mcp"] is False
    assert set(evento["args"]) == {"acceso_mcp", "consultas_sql", "protocolo"}


def test_un_cliente_real_por_stdio_deja_rastro_al_conectarse():
    """Comprueba el CABLEADO: que el middleware está puesto en el servidor.

    Lanza el servidor como proceso, hace solo el handshake y se va sin llamar
    a ninguna herramienta. Es lento comparado con el resto, pero es la única
    forma de cubrir el camino que recorre un cliente de verdad.
    """
    import os
    import sys

    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    data_dir = pathlib.Path(tempfile.mkdtemp())
    env = dict(os.environ, PG_DIAGRAMMER_DATA_DIR=str(data_dir))
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "pg_diagrammer", "--mcp"], env=env
    )

    async def go():
        with anyio.fail_after(60):
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w, client_info=CLIENTE) as s:
                    await s.initialize()  # y nada más

    _run(go)

    eventos = ActivityLog(data_dir).read(since=0, limit=10)["events"]
    assert [e["tool"] for e in eventos] == ["conexión"]
    assert eventos[0]["client"] == "vscode-copilot"
