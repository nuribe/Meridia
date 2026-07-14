"""Integración contra un PostgreSQL real (si PG_TEST_DSN está definido).

Siembra db/init/01-schema.sql y verifica la introspección completa:
FKs compuestas, tabla puente, 1:1, self-reference, vista y meta de >=95% FKs.
"""
import os
import pathlib

import pytest

psycopg = pytest.importorskip("psycopg")

DSN = os.environ.get("PG_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="PG_TEST_DSN no definido")

SEED = pathlib.Path(__file__).resolve().parents[2] / "db" / "init" / "01-schema.sql"


@pytest.fixture(scope="module")
def seeded_db():
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


def test_introspeccion_completa(seeded_db):
    from pg_diagrammer.introspection.introspector import introspect
    from pg_diagrammer.domain.models import Cardinality, TableKind

    snap = introspect(seeded_db, "test")

    assert {"ventas", "inventario"} <= {s.name for s in snap.schemas}
    assert snap.tables["ventas.pedidos"].pk == ["id", "anio"]
    assert snap.tables["ventas.v_resumen_pedidos"].kind == TableKind.view
    view_def = snap.tables["ventas.v_resumen_pedidos"].definition or ""
    assert "SELECT" in view_def and "pedido_items" in view_def

    # FK compuesta de la tabla puente
    items = snap.tables["ventas.pedido_items"]
    fk = next(f for f in items.foreign_keys if set(f.columns) == {"pedido_id", "pedido_anio"})
    assert fk.ref_columns == ["id", "anio"] and fk.on_delete == "CASCADE"

    # 1:1 fichas_tecnicas → productos
    rel = next(r for r in snap.relationships if r.source == "inventario.fichas_tecnicas")
    assert rel.cardinality == Cardinality.one_to_one

    # self-reference categorías
    assert any(
        r.source == "inventario.categorias" and r.target == "inventario.categorias"
        for r in snap.relationships
    )

    # Meta: todas las FK declaradas en la semilla (6) detectadas
    assert len(snap.relationships) == 6


def test_tablas_relacionadas(seeded_db):
    """El endpoint /related incluye destinos de FK y referencias entrantes, inter-schema."""
    import pathlib
    import tempfile
    from fastapi.testclient import TestClient
    from pg_diagrammer.api.app import create_app

    c = TestClient(create_app("t", data_dir=pathlib.Path(tempfile.mkdtemp())))
    H = {"X-Session-Token": "t"}
    host = seeded_db.split("host=")[-1] if "host=" in seeded_db else None
    if not host:
        import pytest
        pytest.skip("DSN sin host de socket")
    pid = c.post("/api/v1/profiles", headers=H, json={
        "name": "it", "host": host, "user": "postgres", "password": "x", "ssl_mode": "disable",
    "dbname": "postgres"}).json()["profile"]["id"]
    r = c.get(f"/api/v1/profiles/{pid}/db/postgres/tables/inventario/productos/related", headers=H).json()
    # salientes: categorias; entrantes: fichas_tecnicas y pedido_items (inter-schema)
    assert set(r["related"]) == {
        "inventario.categorias", "inventario.fichas_tecnicas", "ventas.pedido_items",
    }
    # direction=in → solo las que la referencian ("debajo")
    r = c.get(
        f"/api/v1/profiles/{pid}/db/postgres/tables/inventario/productos/related?direction=in",
        headers=H,
    ).json()
    assert set(r["related"]) == {"inventario.fichas_tecnicas", "ventas.pedido_items"}
    # direction=out → solo a las que apunta
    r = c.get(
        f"/api/v1/profiles/{pid}/db/postgres/tables/inventario/productos/related?direction=out",
        headers=H,
    ).json()
    assert set(r["related"]) == {"inventario.categorias"}


def test_detalle_con_referencias_y_rutinas(seeded_db):
    """El detalle incluye tablas que referencian y rutinas que usan la tabla."""
    import pathlib
    import tempfile
    from fastapi.testclient import TestClient
    from pg_diagrammer.api.app import create_app

    c = TestClient(create_app("t", data_dir=pathlib.Path(tempfile.mkdtemp())))
    H = {"X-Session-Token": "t"}
    host = seeded_db.split("host=")[-1] if "host=" in seeded_db else None
    if not host:
        import pytest
        pytest.skip("DSN sin host de socket")
    pid = c.post("/api/v1/profiles", headers=H, json={
        "name": "it2", "host": host, "user": "postgres", "password": "x", "ssl_mode": "disable",
    "dbname": "postgres"}).json()["profile"]["id"]

    r = c.get(f"/api/v1/profiles/{pid}/db/postgres/tables/ventas/pedidos", headers=H).json()
    # referencias entrantes: pedido_items
    assert [x["source"] for x in r["referenced_by"]] == ["ventas.pedido_items"]
    assert r["referenced_by"][0]["columns"] == ["pedido_id", "pedido_anio"]
    # rutinas: cerrar_pedido (UPDATE pedidos) y total_pedido NO (usa pedido_items)
    names = {x["name"] for x in r["routines"]}
    assert "cerrar_pedido" in names
    assert "body" not in r["routines"][0]

    # búsqueda con múltiples schemas en el servidor
    multi = c.get(
        f"/api/v1/profiles/{pid}/db/postgres/objects?schema=ventas,inventario&q=ped",
        headers=H,
    ).json()
    assert {o["schema_name"] for o in multi["items"]} <= {"ventas", "inventario"}
    assert any(o["name"] == "pedidos" for o in multi["items"])

    # datos paginados en BD (LIMIT/OFFSET con orden por PK)
    d = c.get(
        f"/api/v1/profiles/{pid}/db/postgres/tables/inventario/productos/data?limit=1&with_total=true",
        headers=H,
    ).json()
    assert d["total"] == 2 and len(d["rows"]) == 1
    assert d["columns"][:2] == ["id", "sku"]
    first_id = d["rows"][0][0]
    d2 = c.get(
        f"/api/v1/profiles/{pid}/db/postgres/tables/inventario/productos/data?limit=1&offset=1",
        headers=H,
    ).json()
    assert d2["total"] is None and d2["rows"][0][0] != first_id
    missing_t = c.get(
        f"/api/v1/profiles/{pid}/db/postgres/tables/inventario/nada/data", headers=H
    )
    assert missing_t.status_code == 404

    # ordenación en BD
    ds = c.get(
        f"/api/v1/profiles/{pid}/db/postgres/tables/inventario/productos/data"
        "?order_by=id&order_dir=desc",
        headers=H,
    ).json()
    assert ds["rows"][0][0] > ds["rows"][1][0]
    # filtro ILIKE por columna, con total filtrado
    import urllib.parse
    f = urllib.parse.quote('{"sku": "sku-001"}')
    df = c.get(
        f"/api/v1/profiles/{pid}/db/postgres/tables/inventario/productos/data"
        f"?filters={f}&with_total=true",
        headers=H,
    ).json()
    assert df["total"] == 1 and df["rows"][0][1] == "SKU-001"

    # código completo de una rutina
    code = c.get(
        f"/api/v1/profiles/{pid}/db/postgres/routines/ventas/total_pedido/definition"
        "?args=p_id integer, p_anio integer",
        headers=H,
    ).json()
    assert "CREATE OR REPLACE FUNCTION" in code["definition"]
    assert "pedido_items" in code["definition"]
    missing = c.get(
        f"/api/v1/profiles/{pid}/db/postgres/routines/ventas/no_existe/definition",
        headers=H,
    )
    assert missing.status_code == 404

    r2 = c.get(f"/api/v1/profiles/{pid}/db/postgres/tables/inventario/productos", headers=H).json()
    assert {x["source"] for x in r2["referenced_by"]} == {
        "inventario.fichas_tecnicas", "ventas.pedido_items",
    }

    # vistas que referencian la tabla (dependencias reales de pg_rewrite)
    assert r["views"] == ["ventas.v_resumen_pedidos"]      # pedidos
    assert r2["views"] == ["ventas.v_resumen_ext"]         # productos: la usa v_resumen_ext

    # inverso: de qué depende la vista (para "crear diagrama de la vista")
    dep = c.get(
        f"/api/v1/profiles/{pid}/db/postgres/views/ventas/v_resumen_pedidos/depends-on",
        headers=H,
    ).json()
    assert set(dep["tables"]) == {"ventas.pedidos", "ventas.clientes", "ventas.pedido_items"}
    join_map = {(j["source"], j["target"]): j["join_type"] for j in dep["joins"]}
    assert join_map[("ventas.pedidos", "ventas.clientes")] == "INNER JOIN"
    assert join_map[("ventas.pedidos", "ventas.pedido_items")] == "LEFT JOIN"
    bad = c.get(f"/api/v1/profiles/{pid}/db/postgres/views/ventas/pedidos/depends-on", headers=H)
    assert bad.status_code == 422

    # vista sobre vista: la vista interna debe aparecer entre las dependencias
    dep2 = c.get(
        f"/api/v1/profiles/{pid}/db/postgres/views/ventas/v_resumen_ext/depends-on",
        headers=H,
    ).json()
    assert "ventas.v_resumen_pedidos" in dep2["tables"]
    assert "inventario.productos" in dep2["tables"]
    jm2 = {(j["source"], j["target"]): j for j in dep2["joins"]}
    lj = jm2[("ventas.v_resumen_pedidos", "inventario.productos")]
    assert lj["join_type"] == "LEFT JOIN"
    assert lj["source_columns"] == ["id"] and lj["target_columns"] == ["id"]


def test_query_solo_lectura(seeded_db):
    """La pestaña de consultas ejecuta SELECT y bloquea escrituras."""
    import pathlib
    import tempfile
    from fastapi.testclient import TestClient
    from pg_diagrammer.api.app import create_app

    c = TestClient(create_app("t", data_dir=pathlib.Path(tempfile.mkdtemp())))
    H = {"X-Session-Token": "t"}
    host = seeded_db.split("host=")[-1] if "host=" in seeded_db else None
    if not host:
        import pytest
        pytest.skip("DSN sin host de socket")
    pid = c.post("/api/v1/profiles", headers=H, json={
        "name": "q", "host": host, "user": "postgres", "password": "x", "ssl_mode": "disable",
    "dbname": "postgres"}).json()["profile"]["id"]

    # SELECT válido
    r = c.post(f"/api/v1/profiles/{pid}/db/postgres/query", headers=H, json={
        "sql": "SELECT id, nombre FROM inventario.productos ORDER BY id",
    }).json()
    assert r["ok"] and r["columns"] == ["id", "nombre"]
    assert r["row_count"] == 2 and r["truncated"] is False

    # límite de filas y truncado
    r = c.post(f"/api/v1/profiles/{pid}/db/postgres/query", headers=H, json={
        "sql": "SELECT * FROM generate_series(1, 100) g", "max_rows": 10,
    }).json()
    assert r["row_count"] == 10 and r["truncated"] is True

    # escritura rechazada (transacción de solo lectura)
    w = c.post(f"/api/v1/profiles/{pid}/db/postgres/query", headers=H, json={
        "sql": "UPDATE inventario.productos SET nombre = 'x'",
    })
    assert w.status_code == 400
    assert w.json()["code"] in {"SQL_ERROR", "PERMISSION_DENIED"}

    # SQL inválido → error accionable
    bad = c.post(f"/api/v1/profiles/{pid}/db/postgres/query", headers=H, json={
        "sql": "SELECT * FROM tabla_que_no_existe",
    })
    assert bad.status_code == 400 and bad.json()["message"]
