"""Tests del diff entre snapshots (refresh visual)."""
from pg_diagrammer.introspection.introspector import assemble, diff_snapshots
from tests.test_introspector import SCHEMAS, RELATIONS, COLUMNS, CONSTRAINTS, INDEXES


def base():
    return assemble("d", SCHEMAS, RELATIONS, COLUMNS, CONSTRAINTS, INDEXES)


def test_sin_snapshot_previo():
    d = diff_snapshots(None, base())
    assert d == {"added": [], "removed": [], "changed": []}


def test_tabla_agregada_y_eliminada():
    old = base()
    rels2 = [r for r in RELATIONS if r[2] != "fichas"] + [
        (200, "ventas", "nueva_tabla", "r", 0, None, None),
    ]
    new = assemble("d", SCHEMAS, rels2, COLUMNS, CONSTRAINTS, INDEXES)
    d = diff_snapshots(old, new)
    assert d["added"] == ["ventas.nueva_tabla"]
    assert d["removed"] == ["inventario.fichas"]


def test_columna_modificada():
    old = base()
    cols2 = [
        (c[0], c[1], c[2], "bigint" if (c[0], c[2]) == (100, "id") else c[3], c[4], c[5], c[6])
        for c in COLUMNS
    ]
    new = assemble("d", SCHEMAS, RELATIONS, cols2, CONSTRAINTS, INDEXES)
    d = diff_snapshots(old, new)
    assert d["changed"] == ["inventario.productos"]
    assert d["added"] == [] and d["removed"] == []


def test_estimated_rows_no_cuenta_como_cambio():
    old = base()
    rels2 = [(r[0], r[1], r[2], r[3], (r[4] or 0) + 999, r[5], r[6]) for r in RELATIONS]
    new = assemble("d", SCHEMAS, rels2, COLUMNS, CONSTRAINTS, INDEXES)
    assert diff_snapshots(old, new)["changed"] == []
