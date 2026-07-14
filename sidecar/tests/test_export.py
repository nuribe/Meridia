"""Tests de los generadores Mermaid y DBML."""
from pg_diagrammer.export.generators import to_dbml, to_mermaid
from tests.test_introspector import snap

TABLES = ["inventario.productos", "inventario.fichas", "ventas.pedido_items", "ventas.pedidos"]


def test_mermaid_estructura():
    out = to_mermaid(snap(), TABLES)
    assert out.startswith("erDiagram")
    assert "inventario_productos {" in out
    assert "integer id PK" in out
    # FK compuesta y cardinalidades
    assert 'ventas_pedido_items }o--|| ventas_pedidos : "items_pedido_fk"' in out
    assert 'inventario_fichas ||--|| inventario_productos : "fichas_producto_fk"' in out


def test_mermaid_solo_tablas_pedidas():
    out = to_mermaid(snap(), ["inventario.productos"])
    assert "inventario_productos {" in out
    assert "ventas_pedidos" not in out
    assert "}o--||" not in out  # sin pares presentes no hay aristas


def test_dbml_estructura():
    out = to_dbml(snap(), TABLES)
    assert 'Table "inventario"."productos" {' in out
    assert '"id" integer [pk, not null' in out
    # Ref compuesta con paréntesis y N:1 como ">"
    assert 'Ref: "ventas"."pedido_items".("pedido_id", "pedido_anio") > "ventas"."pedidos".("id", "anio")' in out
    # 1:1 como "-"
    assert 'Ref: "inventario"."fichas"."producto_id" - "inventario"."productos"."id"' in out


def test_dbml_nullable_y_default():
    out = to_dbml(snap(), ["inventario.fichas"])
    assert '"peso" numeric(8,3)' in out
    assert "[pk, not null" in out  # producto_id
