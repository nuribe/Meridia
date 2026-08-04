"""Refresh granular de una tabla: assemble_one + apply_table_refresh + remove_table.

Pruebas puras (filas crudas → Table → snapshot mutado), sin base de datos,
igual que las de `assemble()` en test_introspector.py.
"""
from pg_diagrammer.introspection.introspector import (
    apply_table_refresh,
    assemble,
    assemble_one,
    remove_table,
)


def _base_snapshot():
    """Dos tablas y una FK aviso→cliente (N:1)."""
    return assemble(
        "demo",
        [("public", None)],
        [
            (1, "public", "cliente", "r", 5, None, None),
            (2, "public", "aviso", "r", 10, "viejo", None),
        ],
        [
            (1, 1, "id", "integer", False, None, None),
            (2, 1, "idaviso", "bigint", False, None, None),
            (2, 2, "idcliente", "bigint", True, None, None),
        ],
        [
            ("pk_cliente", "p", 1, [1], 0, None, "a", "a", None),
            ("pk_aviso", "p", 2, [1], 0, None, "a", "a", None),
            ("fk_aviso_cliente", "f", 2, [2], 1, [1], "a", "c", None),
        ],
        [],
    )


def test_refresh_reemplaza_tabla_y_rederiva_cardinalidad():
    snap = _base_snapshot()
    assert snap.relationships[0].cardinality.value == "N:1"

    # La tabla ganó una columna comentada, cambió su comentario y la FK ahora
    # está respaldada por un UNIQUE → la relación pasa a 1:1.
    rel = (2, "public", "aviso", "r", 12, "nuevo comentario", None)
    cols = [
        (2, 1, "idaviso", "bigint", False, None, "pk"),
        (2, 2, "idcliente", "bigint", True, None, None),
        (2, 3, "estado", "text", True, None, "estado del aviso"),
    ]
    cons = [
        ("pk_aviso", "p", 2, [1], 0, None, "a", "a", None),
        ("uq_idcliente", "u", 2, [2], 0, None, "a", "a", None),
        ("fk_aviso_cliente", "f", 2, [2], 1, [1], "a", "c", None),
    ]
    idx = [(2, "pk_aviso", True, "btree", "1")]
    table = assemble_one(rel, cols, cons, idx, [(1, "public", "cliente")], [(1, 1, "id")])
    apply_table_refresh(snap, "public.aviso", table)

    nt = snap.tables["public.aviso"]
    assert nt.comment == "nuevo comentario"
    assert [c.name for c in nt.columns] == ["idaviso", "idcliente", "estado"]
    assert nt.columns[2].comment == "estado del aviso"
    fk = nt.foreign_keys[0]
    assert fk.ref_table == "cliente" and fk.ref_columns == ["id"]
    assert nt.indexes[0].columns == ["idaviso"]

    salientes = [r for r in snap.relationships if r.source == "public.aviso"]
    assert len(salientes) == 1
    assert salientes[0].cardinality.value == "1:1"
    # La otra tabla no se toca.
    assert snap.tables["public.cliente"].columns[0].name == "id"


def test_remove_table_limpia_relaciones_y_view_usage():
    snap = _base_snapshot()
    snap.view_usage["public.aviso"] = ["public.v_avisos"]
    remove_table(snap, "public.aviso")
    assert "public.aviso" not in snap.tables
    assert all(
        r.source != "public.aviso" and r.target != "public.aviso"
        for r in snap.relationships
    )
    assert "public.aviso" not in snap.view_usage
