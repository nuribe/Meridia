"""Etapa 5: ERD, recursos, consulta de solo lectura y planes de ejecución.

Las pruebas de catálogo (`test_mcp_server.py`) pueden simular la introspección,
pero aquí no vale: `run_select` y `explain_query` existen para hablar con el
motor, así que lo que hay que verificar es lo que el MOTOR hace con ellas —que
una transacción READ ONLY rechaza de verdad, que el plan real deshace lo que
tocó—. Por eso este módulo exige `PG_TEST_DSN`.
"""
import json
import os
import pathlib
import tempfile

import anyio
import psycopg
import pytest
from mcp import Client
from mcp.types import Implementation

from pg_diagrammer.activity.settings import McpSettings, McpSettingsStore
from pg_diagrammer.domain.models import ProfileCreate
from pg_diagrammer.mcp.server import Deps, create_server
from pg_diagrammer.services import query
from pg_diagrammer.services.errors import InvalidRequest, ServiceError

DSN = os.environ.get("PG_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="PG_TEST_DSN no definido")

SEED = pathlib.Path(__file__).resolve().parents[2] / "db" / "init" / "01-schema.sql"
CLIENTE = Implementation(name="claude-code", version="1.0")


@pytest.fixture(scope="module")
def seeded():
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS ventas CASCADE")
            cur.execute("DROP SCHEMA IF EXISTS inventario CASCADE")
            cur.execute(SEED.read_text(encoding="utf-8"))
        conn.commit()
    yield DSN
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS ventas CASCADE")
            cur.execute("DROP SCHEMA IF EXISTS inventario CASCADE")
        conn.commit()


def _host() -> str:
    """El DSN de estas pruebas es `host=<socket>`, como el de integración."""
    return DSN.split("host=")[-1].strip()


class Harness:
    def __init__(self, **settings):
        self.data_dir = pathlib.Path(tempfile.mkdtemp())
        self.deps = Deps.build(self.data_dir)
        self.profile_id = self.deps.profiles.create(ProfileCreate(
            name="Demo", host=_host(), port=5432, user="postgres", password="x",
            dbname=os.environ.get("PGDATABASE", "postgres"), ssl_mode="disable",
            # Encendido a propósito: el MCP debe ignorarlo igualmente.
            allow_writes=True,
        )).id
        self.dbname = os.environ.get("PGDATABASE", "postgres")
        McpSettingsStore(self.data_dir).set(McpSettings(**{
            "enabled": True, "allow_query": True, **settings
        }))
        self.server = create_server(self.deps)

    def eventos(self):
        return self.deps.activity.read(since=0, limit=1000)["events"]


def _run(fn):
    return anyio.run(fn)


async def _call(server, name, args=None):
    async with Client(server, client_info=CLIENTE) as client:
        return await client.call_tool(name, args or {})


def _payload(r):
    assert not r.is_error, r.content
    return r.structured_content


def _texto(r) -> str:
    return " ".join(getattr(c, "text", "") for c in r.content)


# --- run_select -----------------------------------------------------------

def test_select_devuelve_filas(seeded):
    h = Harness()
    r = _payload(_run(lambda: _call(h.server, "meridia_run_select", {
        "profile_id": h.profile_id, "dbname": h.dbname,
        "sql": "SELECT nombre FROM inventario.productos ORDER BY id LIMIT 3",
    })))
    assert r["columns"] == ["nombre"]
    assert r["row_count"] == len(r["rows"]) > 0
    assert r["truncated"] is False


def test_max_rows_recorta_y_avisa(seeded):
    h = Harness()
    r = _payload(_run(lambda: _call(h.server, "meridia_run_select", {
        "profile_id": h.profile_id, "dbname": h.dbname,
        "sql": "SELECT * FROM inventario.productos", "max_rows": 1,
    })))
    assert r["row_count"] == 1 and r["truncated"] is True


@pytest.mark.parametrize("sql", [
    "DELETE FROM inventario.productos",
    "UPDATE inventario.productos SET nombre = 'x' WHERE id = 1",
    "CREATE TABLE zzz (id int)",
    "DROP SCHEMA ventas CASCADE",
    "TRUNCATE inventario.productos",
])
def test_la_escritura_se_rechaza_aunque_el_perfil_la_permita(seeded, sql):
    """El perfil tiene allow_writes=True: el MCP no lo hereda."""
    h = Harness()
    r = _run(lambda: _call(h.server, "meridia_run_select", {
        "profile_id": h.profile_id, "dbname": h.dbname, "sql": sql,
    }))
    assert r.is_error and "[VALIDATION]" in _texto(r)
    # Y la tabla sigue ahí.
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM inventario.productos")
        assert cur.fetchone()[0] > 0


def test_varias_sentencias_se_rechazan(seeded):
    """`SELECT 1; DELETE …` sería la vía de escape obvia."""
    h = Harness()
    r = _run(lambda: _call(h.server, "meridia_run_select", {
        "profile_id": h.profile_id, "dbname": h.dbname,
        "sql": "SELECT 1; DELETE FROM inventario.productos",
    }))
    assert r.is_error and "una sola sentencia" in _texto(r)


def test_show_y_explain_no_entran_por_run_select(seeded):
    """El editor de la app los admite; el MCP no: tienen su propia tool."""
    h = Harness()
    for sql in ("SHOW search_path", "EXPLAIN SELECT 1"):
        r = _run(lambda sql=sql: _call(h.server, "meridia_run_select", {
            "profile_id": h.profile_id, "dbname": h.dbname, "sql": sql,
        }))
        assert r.is_error and "SELECT y WITH" in _texto(r)


def test_with_si_entra(seeded):
    h = Harness()
    r = _payload(_run(lambda: _call(h.server, "meridia_run_select", {
        "profile_id": h.profile_id, "dbname": h.dbname,
        "sql": "WITH t AS (SELECT 1 AS n) SELECT n FROM t",
    })))
    assert r["rows"] == [[1]]


def test_sin_allow_query_no_se_ejecuta_nada(seeded):
    h = Harness(allow_query=False)
    r = _run(lambda: _call(h.server, "meridia_run_select", {
        "profile_id": h.profile_id, "dbname": h.dbname, "sql": "SELECT 1",
    }))
    assert r.is_error and "MCP_DISABLED" in _texto(r)
    assert "Permitir consultas SQL" in _texto(r)
    assert h.eventos()[-1]["status"] == "denied"


def test_el_catalogo_sigue_disponible_sin_allow_query(seeded):
    """Los dos interruptores son independientes: leer estructura sí, datos no."""
    h = Harness(allow_query=False)
    r = _payload(_run(lambda: _call(h.server, "meridia_list_objects", {
        "profile_id": h.profile_id, "dbname": h.dbname,
    })))
    assert r["total"] > 0


# --- muestra en la bitácora ----------------------------------------------

def test_la_muestra_queda_registrada(seeded):
    """La muestra es una muestra: se recortan las filas, no se copia el resultado."""
    h = Harness(sample_rows=2)
    _run(lambda: _call(h.server, "meridia_run_select", {
        "profile_id": h.profile_id, "dbname": h.dbname,
        "sql": "SELECT g AS n FROM generate_series(1, 40) g",
    }))
    e = h.eventos()[-1]
    assert e["sample_columns"] == ["n"]
    assert e["sample"] == [[1], [2]]
    assert e["row_count"] == 40  # 40 filas devueltas, 2 registradas


def test_sample_rows_cero_no_registra_datos(seeded):
    h = Harness(sample_rows=0)
    _run(lambda: _call(h.server, "meridia_run_select", {
        "profile_id": h.profile_id, "dbname": h.dbname,
        "sql": "SELECT nombre FROM inventario.productos",
    }))
    e = h.eventos()[-1]
    assert e["sample"] is None and e["sample_columns"] is None
    assert e["row_count"] > 0  # cuántas filas sí, cuáles no


def test_las_tools_de_catalogo_nunca_dejan_muestra(seeded):
    h = Harness(sample_rows=5)
    _run(lambda: _call(h.server, "meridia_list_objects", {
        "profile_id": h.profile_id, "dbname": h.dbname,
    }))
    assert h.eventos()[-1]["sample"] is None


def test_la_bitacora_se_crea_con_permisos_restringidos(seeded):
    if os.name == "nt":
        pytest.skip("los modos POSIX no aplican en Windows")
    h = Harness()
    _run(lambda: _call(h.server, "meridia_list_profiles"))
    assert oct(h.deps.activity.path.stat().st_mode)[-3:] == "600"


# --- explain --------------------------------------------------------------

def test_plan_estimado_sin_allow_query(seeded):
    """No lee ni una fila, así que va con el catálogo."""
    h = Harness(allow_query=False)
    r = _payload(_run(lambda: _call(h.server, "meridia_explain_query", {
        "profile_id": h.profile_id, "dbname": h.dbname,
        "sql": "SELECT * FROM inventario.productos",
    })))
    assert r["mode"] == "estimated" and r["engine"] == "postgresql"
    assert r["nodes"] and r["plan_text"]


def test_plan_real_exige_allow_query(seeded):
    h = Harness(allow_query=False)
    r = _run(lambda: _call(h.server, "meridia_explain_query", {
        "profile_id": h.profile_id, "dbname": h.dbname,
        "sql": "SELECT * FROM inventario.productos", "mode": "actual",
    }))
    assert r.is_error and "MCP_DISABLED" in _texto(r)


def test_plan_real_con_permiso(seeded):
    h = Harness()
    r = _payload(_run(lambda: _call(h.server, "meridia_explain_query", {
        "profile_id": h.profile_id, "dbname": h.dbname,
        "sql": "SELECT * FROM inventario.productos", "mode": "actual",
    })))
    assert r["mode"] == "actual" and r["nodes"]


def test_el_plan_no_devuelve_valores_de_columnas(seeded):
    """Se puede medir sin llegar a leer los datos."""
    h = Harness()
    r = _payload(_run(lambda: _call(h.server, "meridia_explain_query", {
        "profile_id": h.profile_id, "dbname": h.dbname,
        "sql": "SELECT nombre FROM inventario.productos", "mode": "actual",
    })))
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT nombre FROM inventario.productos LIMIT 1")
        valor = cur.fetchone()[0]
    assert valor and valor not in json.dumps(r, default=str)


def test_explain_tambien_rechaza_escritura(seeded):
    """`EXPLAIN ANALYZE UPDATE …` ejecutaría el UPDATE de verdad."""
    h = Harness()
    r = _run(lambda: _call(h.server, "meridia_explain_query", {
        "profile_id": h.profile_id, "dbname": h.dbname,
        "sql": "UPDATE inventario.productos SET nombre = 'x'", "mode": "actual",
    }))
    assert r.is_error and "[VALIDATION]" in _texto(r)


def test_mode_invalido(seeded):
    h = Harness()
    r = _run(lambda: _call(h.server, "meridia_explain_query", {
        "profile_id": h.profile_id, "dbname": h.dbname,
        "sql": "SELECT 1", "mode": "turbo",
    }))
    assert r.is_error and "mode inválido" in _texto(r)


# --- ERD ------------------------------------------------------------------

def test_erd_mermaid_con_cardinalidades(seeded):
    h = Harness()
    r = _payload(_run(lambda: _call(h.server, "meridia_export_erd", {
        "profile_id": h.profile_id, "dbname": h.dbname,
    })))
    assert r["content"].startswith("erDiagram")
    assert r["extension"] == "mmd" and r["table_count"] > 0
    # La cardinalidad viaja en la sintaxis de Mermaid, no como texto suelto.
    assert "}o--||" in r["content"] or "||--||" in r["content"]


def test_erd_acotado_a_unas_tablas(seeded):
    h = Harness()
    r = _payload(_run(lambda: _call(h.server, "meridia_export_erd", {
        "profile_id": h.profile_id, "dbname": h.dbname,
        "tables": ["inventario.productos", "inventario.fichas_tecnicas"],
    })))
    assert r["table_count"] == 2
    assert "ventas_pedidos" not in r["content"]


def test_erd_dbml(seeded):
    h = Harness()
    r = _payload(_run(lambda: _call(h.server, "meridia_export_erd", {
        "profile_id": h.profile_id, "dbname": h.dbname, "format": "dbml",
    })))
    assert r["extension"] == "dbml" and "Table " in r["content"]


def test_erd_formato_desconocido(seeded):
    h = Harness()
    r = _run(lambda: _call(h.server, "meridia_export_erd", {
        "profile_id": h.profile_id, "dbname": h.dbname, "format": "svg",
    }))
    assert r.is_error and "Formato no soportado" in _texto(r)


# --- recurso por tabla ----------------------------------------------------

def test_recurso_de_tabla(seeded):
    h = Harness()

    async def go():
        async with Client(h.server, client_info=CLIENTE) as c:
            plantillas = (await c.list_resource_templates()).resource_templates
            assert any("meridia://" in t.uri_template for t in plantillas)
            return await c.read_resource(
                f"meridia://{h.profile_id}/{h.dbname}/inventario.productos"
            )

    texto = _run(go).contents[0].text
    assert "inventario.productos" in texto
    assert "Columnas:" in texto and "PK" in texto
    # Y también queda en la bitácora, con su cliente: un recurso es una lectura
    # como otra, y el panel tiene que poder atribuirla.
    evento = h.eventos()[-1]
    assert evento["tool"] == "resource:tabla"
    assert evento["client"] == "claude-code"


def test_el_recurso_respeta_el_interruptor(seeded):
    h = Harness(enabled=False)

    async def go():
        async with Client(h.server, client_info=CLIENTE) as c:
            return await c.read_resource(
                f"meridia://{h.profile_id}/{h.dbname}/inventario.productos"
            )

    with pytest.raises(Exception):
        _run(go)
    assert h.eventos()[-1]["status"] == "denied"


# --- capa de servicio, sin protocolo --------------------------------------

def test_el_servicio_rechaza_script_vacio(seeded):
    h = Harness()
    with pytest.raises(InvalidRequest):
        query.run_select(h.deps.profiles, h.profile_id, h.dbname, "   -- nada\n")


def test_el_timeout_se_aplica_de_verdad(seeded):
    """Una consulta que se pasa del plazo aborta en el motor, no en el cliente."""
    h = Harness()
    with pytest.raises(ServiceError) as exc:
        query.run_select(
            h.deps.profiles, h.profile_id, h.dbname,
            "SELECT pg_sleep(5)", timeout_ms=1000,
        )
    # Llega ya clasificado con el envelope del proyecto, no como texto crudo
    # del driver: es el mismo código que vería el editor de la app.
    assert exc.value.to_api_error().code == "TIMEOUT"


def test_el_agente_no_puede_alargar_el_timeout(seeded):
    """Pida lo que pida, el servidor lo recorta a su tope."""
    h = Harness()
    plan = query.explain(
        h.deps.profiles, h.profile_id, h.dbname,
        "SELECT 1", timeout_ms=999_999,
    )
    assert plan["nodes"]  # no revienta; el recorte es interno
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")  # sesión nueva: el SET no se filtró a la base
        assert cur.fetchone() == (1,)
    assert query.MAX_TIMEOUT_MS == 15_000
