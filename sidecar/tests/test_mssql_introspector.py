"""Tests de la introspección SQL Server: adaptadores de filas sys.* →
assemble(), con filas simuladas (sin servidor). Verifica que la derivación
de cardinalidad (código compartido) funciona igual que con PostgreSQL."""
from pg_diagrammer.domain.models import Cardinality, TableKind
from pg_diagrammer.introspection.mssql_introspector import (
    adapt_columns,
    adapt_constraints,
    adapt_indexes,
    adapt_relations,
    adapt_routines,
    format_type,
)
from pg_diagrammer.introspection.introspector import assemble

# Filas con el formato exacto de mssql_queries:
# schemas: (name, comment)
# relations: (object_id, schema, name, type, rows, comment, definition)
# columns: (object_id, column_id, name, type_name, max_length, precision, scale,
#           is_nullable, default, comment)
# keys: (name, type PK|UQ, parent_object_id, column_id, key_ordinal)
# checks: (name, parent_object_id, definition)
# fks: (name, parent_id, ref_id, upd_desc, del_desc, parent_col, ref_col, ord)
# indexes: (object_id, name, is_unique, method, column_id, key_ordinal)

SCHEMAS = [("dbo", None), ("ventas", "Ventas")]

RELATIONS = [
    (100, "dbo", "productos", "U", 250, None, None),
    (101, "dbo", "fichas", "U", 10, None, None),
    (102, "ventas", "pedidos", "U", 10, None, None),
    (103, "ventas", "pedido_items", "U", 30, None, None),
    (104, "ventas", "v_resumen", "V", None, None,
     "CREATE VIEW ventas.v_resumen AS SELECT p.id FROM ventas.pedidos p"),
]

COLUMNS = [
    (100, 1, "id", "int", 4, 10, 0, False, "(NEXT VALUE FOR ...)", None),
    (100, 2, "sku", "nvarchar", 100, 0, 0, False, None, None),
    (101, 1, "producto_id", "int", 4, 10, 0, False, None, None),
    (101, 2, "peso", "decimal", 5, 8, 3, True, None, None),
    (102, 1, "id", "int", 4, 10, 0, False, None, None),
    (102, 2, "anio", "int", 4, 10, 0, False, None, None),
    (102, 3, "cliente_id", "int", 4, 10, 0, False, None, None),
    (103, 1, "pedido_id", "int", 4, 10, 0, False, None, None),
    (103, 2, "pedido_anio", "int", 4, 10, 0, False, None, None),
    (103, 3, "producto_id", "int", 4, 10, 0, False, None, None),
    (104, 1, "id", "int", 4, 10, 0, True, None, None),
]

KEYS = [
    ("PK_productos", "PK", 100, 1, 1),
    ("UQ_productos_sku", "UQ", 100, 2, 1),
    ("PK_fichas", "PK", 101, 1, 1),
    ("PK_pedidos", "PK", 102, 1, 1),
    ("PK_pedidos", "PK", 102, 2, 2),
    ("PK_items", "PK", 103, 1, 1),
    ("PK_items", "PK", 103, 2, 2),
    ("PK_items", "PK", 103, 3, 3),
]

CHECKS = [("CK_items_cantidad", 103, "([cantidad]>(0))")]

FKS = [
    # FK 1:1 — columnas = PK propia de fichas
    ("FK_fichas_producto", 101, 100, "NO_ACTION", "CASCADE", 1, 1, 1),
    # FK compuesta N:1 (subconjunto de la PK de items)
    ("FK_items_pedido", 103, 102, "NO_ACTION", "CASCADE", 1, 1, 1),
    ("FK_items_pedido", 103, 102, "NO_ACTION", "CASCADE", 2, 2, 2),
    ("FK_items_producto", 103, 100, "SET_NULL", "NO_ACTION", 3, 1, 1),
]

INDEXES = [
    (100, "PK_productos", True, "clustered", 1, 1),
    (100, "UQ_productos_sku", True, "nonclustered", 2, 1),
    (102, "IX_pedidos_cliente", False, "nonclustered", 3, 1),
]

ROUTINES = [
    (200, "dbo", "sp_cerrar_pedido", "P",
     "CREATE PROCEDURE dbo.sp_cerrar_pedido AS UPDATE ventas.pedidos SET ..."),
    (201, "dbo", "fn_total", "FN", "CREATE FUNCTION dbo.fn_total() RETURNS int AS ..."),
]

ROUTINE_PARAMS = [
    (200, 1, "@pedido_id", "int"),
    (200, 2, "@anio", "int"),
]

VIEW_DEPS = [("ventas", "v_resumen", "ventas", "pedidos")]


def snap():
    return assemble(
        "demo",
        SCHEMAS,
        adapt_relations(RELATIONS),
        adapt_columns(COLUMNS),
        adapt_constraints(KEYS, CHECKS, FKS),
        adapt_indexes(INDEXES),
        adapt_routines(ROUTINES, ROUTINE_PARAMS),
        VIEW_DEPS,
    )


def test_tablas_y_vistas():
    s = snap()
    assert len(s.tables) == 5
    assert s.tables["ventas.v_resumen"].kind == TableKind.view
    assert "SELECT" in (s.tables["ventas.v_resumen"].definition or "")
    assert s.tables["dbo.productos"].estimated_rows == 250
    assert s.tables["ventas.v_resumen"].estimated_rows is None


def test_tipos_formateados():
    productos = snap().tables["dbo.productos"]
    # nvarchar: max_length en bytes → 100 bytes = nvarchar(50)
    assert productos.columns[1].data_type == "nvarchar(50)"
    fichas = snap().tables["dbo.fichas"]
    assert fichas.columns[1].data_type == "decimal(8,3)"


def test_format_type_casos():
    assert format_type("varchar", -1, 0, 0) == "varchar(max)"
    assert format_type("nvarchar", -1, 0, 0) == "nvarchar(max)"
    assert format_type("varchar", 50, 0, 0) == "varchar(50)"
    assert format_type("datetime2", 8, 27, 7) == "datetime2(7)"
    assert format_type("int", 4, 10, 0) == "int"


def test_pk_y_unique():
    s = snap()
    assert s.tables["dbo.productos"].pk == ["id"]
    assert s.tables["dbo.productos"].unique_sets == [["sku"]]
    assert s.tables["ventas.pedidos"].pk == ["id", "anio"]
    assert s.tables["ventas.pedido_items"].checks == ["([cantidad]>(0))"]


def test_fks_y_acciones():
    fichas = snap().tables["dbo.fichas"]
    fk = fichas.foreign_keys[0]
    assert fk.ref_schema == "dbo" and fk.ref_table == "productos"
    assert fk.on_delete == "CASCADE" and fk.on_update == "NO ACTION"
    items = snap().tables["ventas.pedido_items"]
    by_name = {f.name: f for f in items.foreign_keys}
    assert by_name["FK_items_pedido"].columns == ["pedido_id", "pedido_anio"]
    assert by_name["FK_items_producto"].on_update == "SET NULL"


def test_cardinalidad_compartida():
    """La derivación de cardinalidad es el mismo código que PostgreSQL."""
    s = snap()
    by_fk = {r.fk_name: r for r in s.relationships}
    # FK cuyas columnas = PK propia → 1:1
    assert by_fk["FK_fichas_producto"].cardinality == Cardinality.one_to_one
    # FK subconjunto de la PK → N:1
    assert by_fk["FK_items_pedido"].cardinality == Cardinality.many_to_one
    assert by_fk["FK_items_producto"].cardinality == Cardinality.many_to_one


def test_indices():
    productos = snap().tables["dbo.productos"]
    idx = {i.name: i for i in productos.indexes}
    assert idx["UQ_productos_sku"].is_unique
    assert idx["UQ_productos_sku"].columns == ["sku"]
    assert idx["PK_productos"].method == "clustered"


def test_rutinas_y_vistas_dependientes():
    s = snap()
    procs = {r.name: r for r in s.routines}
    assert procs["sp_cerrar_pedido"].kind == "procedure"
    assert procs["sp_cerrar_pedido"].args == "@pedido_id int, @anio int"
    assert procs["fn_total"].kind == "function"
    assert s.view_usage["ventas.pedidos"] == ["ventas.v_resumen"]


def test_schemas_con_conteos():
    s = snap()
    ventas = next(sc for sc in s.schemas if sc.name == "ventas")
    assert ventas.table_count == 2 and ventas.view_count == 1
