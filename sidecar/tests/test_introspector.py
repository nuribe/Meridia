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


# --- regresión: tabla homónima en varios esquemas (caso real rrhh.aviso) -----
# Las rutinas llevan 7ª columna (proconfig), como la query ROUTINES de Postgres.

AMB_SCHEMAS = [("rrhh", None), ("incidencia", None), ("patrimonio", None)]

AMB_RELATIONS = [
    (200, "rrhh", "aviso", "r", 82446, None, None),
    (201, "incidencia", "aviso", "r", 100, None, None),
    (202, "rrhh", "zona", "r", 5, None, None),
]

AMB_COLUMNS = [
    (200, 1, "idaviso", "bigint", False, None, None),
    (201, 1, "idaviso", "bigint", False, None, None),
    (202, 1, "id", "bigint", False, None, None),
]

AMB_ROUTINES = [
    # (1) usa la tabla homónima de OTRO esquema, sin calificar
    ("incidencia", "add_noticias", "f", "plpgsql", "p_txt text",
     "BEGIN INSERT INTO aviso (mensaje) VALUES (p_txt); END;", None),
    # (2) la palabra sólo aparece en un literal
    ("patrimonio", "verif_componentes_fuera_epigrafe", "f", "plpgsql", "p_id bigint",
     "BEGIN RAISE NOTICE 'aviso: componente % fuera de epigrafe', p_id; END;", None),
    # (3) la palabra sólo aparece en un comentario
    ("padron", "del_zona", "f", "plpgsql", "p_id bigint",
     "BEGIN -- borra la zona, no toca aviso\n DELETE FROM zona WHERE id=p_id; END;", None),
    # (4) el nombre del esquema termina en 'rrhh' pero no es rrhh
    ("migracion_rrhh", "add_aviso", "f", "plpgsql", "p_txt text",
     "BEGIN INSERT INTO migracion_rrhh.aviso (mensaje) VALUES (p_txt); END;", None),
    # (5) usa la columna idaviso, no la tabla
    ("eadmin", "upd_notmensaje", "f", "plpgsql", "p_id bigint",
     "BEGIN UPDATE eadmin.nota SET visto = true WHERE idaviso = p_id; END;", None),
    # (6) verdadero positivo: referencia calificada
    ("rrhh", "add_aviso", "f", "plpgsql", "p_txt text",
     "BEGIN INSERT INTO rrhh.aviso (mensaje) VALUES (p_txt); END;", None),
    # (7) verdadero positivo: sin calificar, dentro del propio esquema rrhh
    ("rrhh", "purga_avisos", "p", "plpgsql", "",
     "BEGIN DELETE FROM aviso WHERE fecha < now() - interval '1 year'; END;", None),
    # (8) verdadero positivo: sin calificar, con SET search_path explícito
    ("util", "cuenta_avisos", "f", "plpgsql", "",
     "BEGIN RETURN (SELECT count(*) FROM aviso); END;", ["search_path=rrhh, public"]),
    # (9) uso probable: sólo dentro de SQL dinámico
    ("util", "vacia_tabla", "p", "plpgsql", "",
     "BEGIN EXECUTE 'TRUNCATE rrhh.aviso'; END;", None),
]


def snap_ambiguo():
    return assemble("demo", AMB_SCHEMAS, AMB_RELATIONS, AMB_COLUMNS, [], [], AMB_ROUTINES)


def usos_de_aviso():
    from pg_diagrammer.introspection.introspector import routines_using
    return routines_using(snap_ambiguo(), "rrhh.aviso")


def test_sin_falsos_positivos_por_homonimia_literales_y_comentarios():
    assert [r.name for r in usos_de_aviso()] == [
        "add_aviso", "purga_avisos", "cuenta_avisos", "vacia_tabla",
    ]


def test_nombre_suelto_de_otro_esquema_no_cuenta():
    """incidencia.add_noticias hace INSERT INTO aviso -> es incidencia.aviso."""
    assert "add_noticias" not in [r.name for r in usos_de_aviso()]


def test_literal_y_comentario_no_cuentan():
    nombres = [r.name for r in usos_de_aviso()]
    assert "verif_componentes_fuera_epigrafe" not in nombres  # RAISE NOTICE 'aviso…'
    assert "del_zona" not in nombres                           # -- … aviso


def test_columna_idaviso_no_cuenta():
    assert "upd_notmensaje" not in [r.name for r in usos_de_aviso()]


def test_prefijo_de_esquema_no_cuenta():
    """migracion_rrhh.aviso no es rrhh.aviso."""
    assert [r.schema_name for r in usos_de_aviso()].count("migracion_rrhh") == 0


def test_match_kind_anotado():
    por_nombre = {r.name: r.match_kind for r in usos_de_aviso()}
    assert por_nombre["add_aviso"] == "calificada"
    assert por_nombre["purga_avisos"] == "search_path"
    assert por_nombre["cuenta_avisos"] == "search_path"
    assert por_nombre["vacia_tabla"] == "dinamico"


def test_tabla_con_nombre_unico_se_detecta_desde_cualquier_esquema():
    """Si sólo un esquema tiene la tabla, un nombre suelto es inequívoco."""
    from pg_diagrammer.introspection.introspector import routines_using
    usos = routines_using(snap_ambiguo(), "rrhh.zona")
    assert [r.name for r in usos] == ["del_zona"]
