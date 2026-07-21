"""Tests del constructor bidireccional de consultas (diagrama <-> SQL)."""
import re

import pytest

from pg_diagrammer.domain.query_builder import (
    Join,
    QueryModel,
    build_query_sql,
    parse_query_sql,
    suggest_join_type,
)

KNOWN = {
    "ventas.pedidos": "ventas.pedidos", "pedidos": "ventas.pedidos",
    "ventas.clientes": "ventas.clientes", "clientes": "ventas.clientes",
    "ventas.pedido_items": "ventas.pedido_items", "pedido_items": "ventas.pedido_items",
    "inventario.productos": "inventario.productos", "productos": "inventario.productos",
}


def _norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


# --------------------------------------------------------------------------- #
# Sugerencia de tipo de join                                                   #
# --------------------------------------------------------------------------- #
def test_sugerencia_ambos_not_null_inner():
    assert suggest_join_type(False, False) == "INNER JOIN"


def test_sugerencia_source_nullable_left():
    assert suggest_join_type(True, False) == "LEFT JOIN"


def test_sugerencia_target_nullable_left():
    assert suggest_join_type(False, True) == "LEFT JOIN"


def test_sugerencia_ambos_nullable_left():
    assert suggest_join_type(True, True) == "LEFT JOIN"


# --------------------------------------------------------------------------- #
# Diagrama -> SQL                                                              #
# --------------------------------------------------------------------------- #
def test_build_inner_simple():
    model = QueryModel(
        tables=["ventas.pedidos", "ventas.clientes"],
        aliases={"ventas.pedidos": "p", "ventas.clientes": "c"},
        joins=[Join("ventas.pedidos", "ventas.clientes", "INNER JOIN", ["cliente_id"], ["id"])],
    )
    sql = _norm(build_query_sql(model))
    assert sql == (
        'SELECT * FROM "ventas"."pedidos" p '
        'INNER JOIN "ventas"."clientes" c ON p."cliente_id" = c."id"'
    )


def test_build_multiples_joins_y_left():
    model = QueryModel(
        tables=["ventas.pedidos", "ventas.clientes", "ventas.pedido_items"],
        aliases={
            "ventas.pedidos": "p",
            "ventas.clientes": "c",
            "ventas.pedido_items": "i",
        },
        joins=[
            Join("ventas.pedidos", "ventas.clientes", "INNER JOIN", ["cliente_id"], ["id"]),
            Join("ventas.pedidos", "ventas.pedido_items", "LEFT JOIN", ["id"], ["pedido_id"]),
        ],
    )
    sql = _norm(build_query_sql(model))
    assert 'FROM "ventas"."pedidos" p' in sql
    assert 'INNER JOIN "ventas"."clientes" c ON p."cliente_id" = c."id"' in sql
    assert 'LEFT JOIN "ventas"."pedido_items" i ON p."id" = i."pedido_id"' in sql


def test_build_join_compuesto():
    model = QueryModel(
        tables=["ventas.pedidos", "ventas.pedido_items"],
        aliases={"ventas.pedidos": "p", "ventas.pedido_items": "i"},
        joins=[
            Join(
                "ventas.pedidos", "ventas.pedido_items", "LEFT JOIN",
                ["id", "anio"], ["pedido_id", "pedido_anio"],
            )
        ],
    )
    sql = _norm(build_query_sql(model))
    assert 'ON p."id" = i."pedido_id" AND p."anio" = i."pedido_anio"' in sql


def test_build_cross_join_sin_on():
    model = QueryModel(
        tables=["ventas.clientes", "inventario.productos"],
        aliases={"ventas.clientes": "c", "inventario.productos": "pr"},
        joins=[Join("ventas.clientes", "inventario.productos", "CROSS JOIN")],
    )
    sql = _norm(build_query_sql(model))
    assert 'CROSS JOIN "inventario"."productos" pr' in sql
    assert " ON " not in sql


def test_build_flip_left_a_right():
    # clientes es destino de dos joins. Tras encadenar pedidos->clientes, el
    # segundo join (pedido_items->clientes) debe añadir pedido_items invirtiendo
    # el sentido: LEFT pasa a RIGHT para preservar la semántica.
    model = QueryModel(
        tables=["ventas.pedidos", "ventas.clientes", "ventas.pedido_items"],
        aliases={
            "ventas.pedidos": "p",
            "ventas.clientes": "c",
            "ventas.pedido_items": "i",
        },
        joins=[
            Join("ventas.pedidos", "ventas.clientes", "INNER JOIN", ["cliente_id"], ["id"]),
            Join("ventas.pedido_items", "ventas.clientes", "LEFT JOIN", ["cliente_id"], ["id"]),
        ],
    )
    sql = _norm(build_query_sql(model))
    assert sql.startswith('SELECT * FROM "ventas"."pedidos" p')
    assert 'INNER JOIN "ventas"."clientes" c ON p."cliente_id" = c."id"' in sql
    assert 'RIGHT JOIN "ventas"."pedido_items" i ON i."cliente_id" = c."id"' in sql


def test_build_preserva_select_y_tail():
    model = QueryModel(
        tables=["ventas.pedidos", "ventas.clientes"],
        aliases={"ventas.pedidos": "p", "ventas.clientes": "c"},
        joins=[Join("ventas.pedidos", "ventas.clientes", "INNER JOIN", ["cliente_id"], ["id"])],
        select_sql="p.id, c.nombre",
        tail_sql="WHERE p.id > 0 ORDER BY p.id",
    )
    sql = build_query_sql(model)
    assert sql.startswith("SELECT p.id, c.nombre")
    assert sql.rstrip().endswith("WHERE p.id > 0 ORDER BY p.id")


def test_build_sin_tablas_error():
    with pytest.raises(ValueError):
        build_query_sql(QueryModel(tables=[]))


# --------------------------------------------------------------------------- #
# SQL -> Diagrama                                                             #
# --------------------------------------------------------------------------- #
def test_parse_inner_con_alias():
    sql = "SELECT * FROM ventas.pedidos p INNER JOIN ventas.clientes c ON p.cliente_id = c.id"
    model = parse_query_sql(sql, KNOWN)
    assert set(model.tables) == {"ventas.pedidos", "ventas.clientes"}
    assert len(model.joins) == 1
    j = model.joins[0]
    assert j.source == "ventas.pedidos"
    assert j.target == "ventas.clientes"
    assert j.join_type == "INNER JOIN"
    assert j.source_columns == ["cliente_id"]
    assert j.target_columns == ["id"]


def test_parse_multiples_joins_una_linea():
    sql = (
        "SELECT p.id, c.nombre FROM ventas.pedidos p "
        "JOIN ventas.clientes c ON c.id = p.cliente_id "
        "LEFT JOIN ventas.pedido_items i ON i.pedido_id = p.id "
        "WHERE p.id > 0"
    )
    model = parse_query_sql(sql, KNOWN)
    assert len(model.joins) == 2
    by_target = {j.target: j for j in model.joins}
    assert by_target["ventas.clientes"].join_type == "INNER JOIN"
    assert by_target["ventas.pedido_items"].join_type == "LEFT JOIN"
    assert model.select_sql == "p.id, c.nombre"
    assert model.tail_sql == "WHERE p.id > 0"


def test_parse_join_compuesto_y_as():
    sql = (
        "SELECT 1 FROM ventas.pedidos AS p "
        "LEFT OUTER JOIN ventas.pedido_items AS i "
        "ON i.pedido_id = p.id AND i.pedido_anio = p.anio"
    )
    model = parse_query_sql(sql, KNOWN)
    assert len(model.joins) == 1
    j = model.joins[0]
    assert j.join_type == "LEFT JOIN"
    assert j.source == "ventas.pedidos"
    assert j.target == "ventas.pedido_items"
    assert j.source_columns == ["id", "anio"]
    assert j.target_columns == ["pedido_id", "pedido_anio"]


def test_parse_cross_join():
    sql = "SELECT * FROM ventas.clientes c CROSS JOIN inventario.productos pr"
    model = parse_query_sql(sql, KNOWN)
    assert len(model.joins) == 1
    assert model.joins[0].join_type == "CROSS JOIN"
    assert model.joins[0].source_columns == []


def test_parse_tabla_desconocida_va_a_unresolved():
    sql = "SELECT * FROM ventas.pedidos p JOIN otro.desconocida d ON d.id = p.x"
    model = parse_query_sql(sql, KNOWN)
    assert "ventas.pedidos" in model.tables
    assert "otro.desconocida" in model.unresolved
    assert model.warnings


# --------------------------------------------------------------------------- #
# Round-trip: SQL -> diagrama -> edición -> SQL                                 #
# --------------------------------------------------------------------------- #
def test_roundtrip_preserva_joins():
    sql = (
        "SELECT * FROM ventas.pedidos p "
        "JOIN ventas.clientes c ON p.cliente_id = c.id "
        "LEFT JOIN ventas.pedido_items i ON i.pedido_id = p.id"
    )
    model = parse_query_sql(sql, KNOWN)
    rebuilt = build_query_sql(model)
    # Al reanalizar el SQL regenerado, los joins se conservan.
    again = parse_query_sql(rebuilt, KNOWN)
    assert {(j.source, j.target, j.join_type) for j in again.joins} == {
        ("ventas.pedidos", "ventas.clientes", "INNER JOIN"),
        ("ventas.pedidos", "ventas.pedido_items", "LEFT JOIN"),
    }


def test_edicion_cambia_tipo_join_y_regenera():
    sql = "SELECT * FROM ventas.pedidos p JOIN ventas.clientes c ON p.cliente_id = c.id"
    model = parse_query_sql(sql, KNOWN)
    # El usuario cambia el tipo de join en la relación.
    model.joins[0].join_type = "LEFT JOIN"
    rebuilt = _norm(build_query_sql(model))
    assert 'LEFT JOIN "ventas"."clientes" c ON p."cliente_id" = c."id"' in rebuilt


# --------------------------------------------------------------------------- #
# Endpoints (build/parse) con snapshot inyectado                               #
# --------------------------------------------------------------------------- #
from datetime import datetime, timezone  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from pg_diagrammer.api.app import create_app  # noqa: E402
from pg_diagrammer.domain.models import (  # noqa: E402
    Cardinality,
    Column,
    Relationship,
    Snapshot,
    Table,
)

_TOKEN = "t"
_H = {"X-Session-Token": _TOKEN}


def _make_snapshot() -> Snapshot:
    pedidos = Table(
        schema_name="ventas", name="pedidos",
        columns=[
            Column(name="id", position=1, data_type="int", is_nullable=False, is_pk=True),
            Column(name="cliente_id", position=2, data_type="int", is_nullable=True),
        ],
        pk=["id"],
    )
    clientes = Table(
        schema_name="ventas", name="clientes",
        columns=[
            Column(name="id", position=1, data_type="int", is_nullable=False, is_pk=True),
            Column(name="nombre", position=2, data_type="text", is_nullable=False),
        ],
        pk=["id"],
    )
    return Snapshot(
        snapshot_id="s1", dbname="tienda", created_at=datetime.now(timezone.utc),
        schemas=[], tables={"ventas.pedidos": pedidos, "ventas.clientes": clientes},
        relationships=[
            Relationship(
                source="ventas.pedidos", target="ventas.clientes",
                fk_name="fk_pedidos_cliente", columns=["cliente_id"], ref_columns=["id"],
                cardinality=Cardinality.many_to_one,
            )
        ],
    )


def _client_with_snapshot(tmp_path):
    client = TestClient(create_app(_TOKEN, data_dir=tmp_path), raise_server_exceptions=False)
    app = client.app
    app.state.snapshots.set("p1", "tienda", _make_snapshot())
    app.state.profiles.get = lambda pid: object()  # perfil ficticio (usa el snapshot cacheado)
    return client


BASE = "/api/v1/profiles/p1/db/tienda/query"


def test_endpoint_build(tmp_path):
    c = _client_with_snapshot(tmp_path)
    r = c.post(f"{BASE}/build", headers=_H, json={
        "tables": ["ventas.pedidos", "ventas.clientes"],
        "aliases": {"ventas.pedidos": "p", "ventas.clientes": "c"},
        "joins": [{
            "source": "ventas.pedidos", "target": "ventas.clientes",
            "join_type": "LEFT JOIN",
            "source_columns": ["cliente_id"], "target_columns": ["id"],
        }],
    })
    assert r.status_code == 200
    assert 'LEFT JOIN "ventas"."clientes" c ON p."cliente_id" = c."id"' in _norm(r.json()["sql"])


def test_endpoint_build_columna_invalida_devuelve_envelope(tmp_path):
    c = _client_with_snapshot(tmp_path)
    r = c.post(f"{BASE}/build", headers=_H, json={
        "tables": ["ventas.pedidos", "ventas.clientes"],
        "joins": [{
            "source": "ventas.pedidos", "target": "ventas.clientes",
            "join_type": "INNER JOIN",
            "source_columns": ["no_existe"], "target_columns": ["id"],
        }],
    })
    assert r.status_code == 422
    body = r.json()
    assert set(body) >= {"code", "message", "hint", "retriable"}
    assert body["code"] == "VALIDATION"


def test_endpoint_parse(tmp_path):
    c = _client_with_snapshot(tmp_path)
    r = c.post(f"{BASE}/parse", headers=_H, json={
        "sql": "SELECT * FROM ventas.pedidos p JOIN ventas.clientes c ON p.cliente_id = c.id",
    })
    assert r.status_code == 200
    body = r.json()
    assert set(body["tables"]) == {"ventas.pedidos", "ventas.clientes"}
    assert body["joins"][0]["join_type"] == "INNER JOIN"
    assert body["joins"][0]["source_columns"] == ["cliente_id"]
