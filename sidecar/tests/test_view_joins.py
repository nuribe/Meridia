"""Tests del parser de joins de vistas (formato de pg_get_viewdef pretty)."""
from pg_diagrammer.introspection.view_joins import parse_view_joins

KNOWN = {
    "ventas.pedidos": "ventas.pedidos", "pedidos": "ventas.pedidos",
    "ventas.clientes": "ventas.clientes", "clientes": "ventas.clientes",
    "ventas.pedido_items": "ventas.pedido_items", "pedido_items": "ventas.pedido_items",
}

SQL = """ SELECT p.id,
    c.nombre AS cliente,
    count(i.producto_id) AS lineas
   FROM ventas.pedidos p
     JOIN ventas.clientes c ON c.id = p.cliente_id
     LEFT JOIN ventas.pedido_items i ON (i.pedido_id, i.pedido_anio) =
        ((p.id, p.anio))
  GROUP BY p.id, c.nombre;"""


def test_joins_basicos():
    joins = {(j["source"], j["target"]): j for j in parse_view_joins(SQL, KNOWN)}
    inner = joins[("ventas.pedidos", "ventas.clientes")]
    assert inner["join_type"] == "INNER JOIN"
    assert inner["source_columns"] == ["cliente_id"]
    assert inner["target_columns"] == ["id"]
    left = joins[("ventas.pedidos", "ventas.pedido_items")]
    assert left["join_type"] == "LEFT JOIN"
    assert left["source_columns"] == ["id", "anio"]        # p.id, p.anio
    assert left["target_columns"] == ["pedido_id", "pedido_anio"]
    assert len(joins) == 2


def test_on_multilinea_y_outer():
    sql = """ SELECT 1
   FROM ventas.pedidos p
     LEFT OUTER JOIN ventas.clientes c ON c.id =
        p.cliente_id
  WHERE p.id > 0;"""
    joins = parse_view_joins(sql, KNOWN)
    assert len(joins) == 1
    assert joins[0]["join_type"] == "LEFT JOIN"
    assert joins[0]["source_columns"] == ["cliente_id"]
    assert joins[0]["target_columns"] == ["id"]


def test_sin_joins():
    assert parse_view_joins(" SELECT 1\n   FROM ventas.pedidos p;", KNOWN) == []
