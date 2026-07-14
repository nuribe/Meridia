"""Tests del ensamblado de snapshot y derivación de cardinalidad,
con filas simuladas de pg_catalog (sin base de datos)."""
from pg_diagrammer.introspection.introspector import assemble
from pg_diagrammer.domain.models import Cardinality, TableKind

# Filas con el formato exacto de las queries en bloque:
# schemas: (nspname, comment)
# relations: (oid, schema, name, relkind, estimated_rows, comment)
# columns: (attrelid, attnum, name, type, is_nullable, default, comment)
# constraints: (conname, contype, conrelid, conkey, confrelid, confkey, upd, del, definition)
# indexes: (indrelid, index_name, is_unique, method, attnums_text)

SCHEMAS = [("ventas", None), ("inventario", "Inventario")]

RELATIONS = [
    (100, "inventario", "productos", "r", 250, None, None),
    (101, "inventario", "fichas", "r", -1, None, None),
    (102, "ventas", "pedidos", "r", 10, None, None),
    (103, "ventas", "pedido_items", "r", 30, None, None),
    (104, "ventas", "v_resumen", "v", -1, None, " SELECT p.id\n   FROM ventas.pedidos p;"),
]

COLUMNS = [
    (100, 1, "id", "integer", False, "nextval('...')", None),
    (100, 2, "sku", "text", False, None, None),
    (101, 1, "producto_id", "integer", False, None, None),
    (101, 2, "peso", "numeric(8,3)", True, None, None),
    (102, 1, "id", "integer", False, None, None),
    (102, 2, "anio", "integer", False, None, None),
    (102, 3, "cliente_id", "integer", False, None, None),
    (103, 1, "pedido_id", "integer", False, None, None),
    (103, 2, "pedido_anio", "integer", False, None, None),
    (103, 3, "producto_id", "integer", False, None, None),
    (104, 1, "id", "integer", True, None, None),
]

CONSTRAINTS = [
    ("productos_pkey", "p", 100, [1], 0, None, " ", " ", "PRIMARY KEY (id)"),
    ("productos_sku_key", "u", 100, [2], 0, None, " ", " ", "UNIQUE (sku)"),
    ("fichas_pkey", "p", 101, [1], 0, None, " ", " ", "PRIMARY KEY (producto_id)"),
    # FK 1:1 — las columnas FK son exactamente la PK de fichas
    ("fichas_producto_fk", "f", 101, [1], 100, [1], "a", "c", "FOREIGN KEY ..."),
    ("pedidos_pkey", "p", 102, [1, 2], 0, None, " ", " ", "PRIMARY KEY (id, anio)"),
    ("items_pkey", "p", 103, [1, 2, 3], 0, None, " ", " ", "PRIMARY KEY (...)"),
    # FK compuesta N:1 (subconjunto de la PK de items)
    ("items_pedido_fk", "f", 103, [1, 2], 102, [1, 2], "a", "c", "FOREIGN KEY ..."),
    ("items_producto_fk", "f", 103, [3], 100, [1], "a", "a", "FOREIGN KEY ..."),
    ("items_cantidad_chk", "c", 103, None, 0, None, " ", " ", "CHECK (cantidad > 0)"),
]

INDEXES = [
    (100, "productos_pkey", True, "btree", "1"),
    (100, "productos_sku_key", True, "btree", "2"),
    (102, "idx_pedidos_cliente", False, "btree", "3"),
]


def snap():
    return assemble("demo", SCHEMAS, RELATIONS, COLUMNS, CONSTRAINTS, INDEXES)


def test_tablas_y_vistas():
    s = snap()
    assert len(s.tables) == 5
    assert s.tables["ventas.v_resumen"].kind == TableKind.view
    assert "SELECT" in (s.tables["ventas.v_resumen"].definition or "")
    assert s.tables["inventario.productos"].definition is None
    assert s.tables["inventario.productos"].estimated_rows == 250
    assert s.tables["inventario.fichas"].estimated_rows is None  # -1 → sin analizar


def test_columnas_y_pk():
    productos = snap().tables["inventario.productos"]
    assert [c.name for c in productos.columns] == ["id", "sku"]
    assert productos.pk == ["id"]
    assert productos.columns[0].is_pk and not productos.columns[1].is_pk


def test_pk_compuesta():
    assert snap().tables["ventas.pedidos"].pk == ["id", "anio"]


def test_fk_compuesta_resuelve_nombres():
    items = snap().tables["ventas.pedido_items"]
    fk = next(f for f in items.foreign_keys if f.name == "items_pedido_fk")
    assert fk.columns == ["pedido_id", "pedido_anio"]
    assert fk.ref_columns == ["id", "anio"]
    assert fk.on_delete == "CASCADE"


def test_cardinalidad_1_a_1():
    s = snap()
    rel = next(r for r in s.relationships if r.fk_name == "fichas_producto_fk")
    assert rel.cardinality == Cardinality.one_to_one
    assert rel.source == "inventario.fichas"
    assert rel.target == "inventario.productos"


def test_cardinalidad_n_a_1():
    s = snap()
    for name in ("items_pedido_fk", "items_producto_fk"):
        rel = next(r for r in s.relationships if r.fk_name == name)
        assert rel.cardinality == Cardinality.many_to_one


def test_checks_e_indices():
    s = snap()
    assert s.tables["ventas.pedido_items"].checks == ["CHECK (cantidad > 0)"]
    idx = next(i for i in s.tables["ventas.pedidos"].indexes if i.name == "idx_pedidos_cliente")
    assert idx.columns == ["cliente_id"] and not idx.is_unique


def test_conteos_por_schema():
    s = snap()
    ventas = next(x for x in s.schemas if x.name == "ventas")
    assert ventas.table_count == 2 and ventas.view_count == 1


def test_fk_detectadas_100_por_ciento():
    """Meta del brief: >= 95% de FKs detectadas (aquí: todas las declaradas)."""
    declared = sum(1 for c in CONSTRAINTS if c[1] == "f")
    assert len(snap().relationships) == declared


ROUTINES = [
    ("ventas", "total_pedido", "f", "sql",
     "p_id integer, p_anio integer",
     "SELECT sum(x) FROM ventas.pedido_items WHERE pedido_id = p_id"),
    ("ventas", "cerrar_pedido", "p", "plpgsql",
     "p_id integer",
     "BEGIN UPDATE pedidos SET estado='c' WHERE id=p_id; END;"),
    ("public", "sin_relacion", "f", "sql", "", "SELECT 1"),
]


def snap_con_rutinas():
    from pg_diagrammer.introspection.introspector import assemble
    return assemble("demo", SCHEMAS, RELATIONS, COLUMNS, CONSTRAINTS, INDEXES, ROUTINES)


def test_rutinas_ensambladas():
    s = snap_con_rutinas()
    assert len(s.routines) == 3
    proc = next(r for r in s.routines if r.name == "cerrar_pedido")
    assert proc.kind == "procedure" and proc.language == "plpgsql"


def test_rutinas_que_usan_tabla_calificada():
    from pg_diagrammer.introspection.introspector import routines_using
    s = snap_con_rutinas()
    usos = routines_using(s, "ventas.pedido_items")
    assert [r.name for r in usos] == ["total_pedido"]


def test_rutinas_que_usan_tabla_sin_calificar():
    from pg_diagrammer.introspection.introspector import routines_using
    s = snap_con_rutinas()
    usos = routines_using(s, "ventas.pedidos")
    assert [r.name for r in usos] == ["cerrar_pedido"]


def test_body_excluido_de_la_serializacion():
    s = snap_con_rutinas()
    assert "SELECT sum" not in s.routines[0].model_dump_json()
