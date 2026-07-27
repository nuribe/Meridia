"""Introspección de SQL Server: ejecuta las queries en bloque sobre sys.*
y adapta las filas a la forma que espera `introspector.assemble()`.

Así el ensamblado del Snapshot, la derivación de cardinalidad y el diff son
EXACTAMENTE el mismo código para ambos motores (una sola fuente de verdad).
Correspondencias: object_id ↔ oid, column_id ↔ attnum, 'U'/'V' ↔ relkind.
"""
from __future__ import annotations

from pg_diagrammer.domain.models import Snapshot
from pg_diagrammer.introspection import mssql_queries as q
from pg_diagrammer.introspection.introspector import assemble

# type de sys.objects → relkind de pg_class (lo que espera assemble()).
TYPE_TO_RELKIND = {"U": "r", "V": "v"}

# referential_action_desc → carácter de pg_constraint (con*type).
ACTION_TO_CHAR = {
    "NO_ACTION": "a",
    "CASCADE": "c",
    "SET_NULL": "n",
    "SET_DEFAULT": "d",
}


def format_type(name: str, max_length: int, precision: int, scale: int) -> str:
    """Reconstruye el tipo legible (varchar(50), decimal(10,2), …)."""
    n = (name or "").lower()
    if n in ("varchar", "char", "varbinary", "binary"):
        return f"{n}(max)" if max_length == -1 else f"{n}({max_length})"
    if n in ("nvarchar", "nchar"):
        # max_length está en bytes; NCHAR usa 2 bytes por carácter.
        return f"{n}(max)" if max_length == -1 else f"{n}({max_length // 2})"
    if n in ("decimal", "numeric"):
        return f"{n}({precision},{scale})"
    if n in ("datetime2", "datetimeoffset", "time"):
        return f"{n}({scale})"
    return n


def introspect(conn, dbname: str) -> Snapshot:
    """Ejecuta las queries en bloque en una sola conexión y arma el Snapshot."""
    with conn.cursor() as cur:
        cur.execute(q.SCHEMAS)
        schema_rows = cur.fetchall()
        cur.execute(q.RELATIONS)
        raw_rels = cur.fetchall()
        cur.execute(q.COLUMNS)
        raw_cols = cur.fetchall()
        cur.execute(q.KEY_CONSTRAINTS)
        raw_keys = cur.fetchall()
        cur.execute(q.CHECK_CONSTRAINTS)
        raw_checks = cur.fetchall()
        cur.execute(q.FOREIGN_KEYS)
        raw_fks = cur.fetchall()
        cur.execute(q.INDEXES)
        raw_idx = cur.fetchall()
        cur.execute(q.ROUTINES)
        raw_routines = cur.fetchall()
        cur.execute(q.ROUTINE_PARAMS)
        raw_params = cur.fetchall()
        cur.execute(q.VIEW_DEPS)
        view_rows = [tuple(r) for r in cur.fetchall()]
    return assemble(
        dbname,
        [tuple(r) for r in schema_rows],
        adapt_relations(raw_rels),
        adapt_columns(raw_cols),
        adapt_constraints(raw_keys, raw_checks, raw_fks),
        adapt_indexes(raw_idx),
        adapt_routines(raw_routines, raw_params),
        view_rows,
    )


# --------------------------------------------------------------------------- #
# Adaptadores fila-a-fila (puros, testeables sin servidor)                     #
# --------------------------------------------------------------------------- #
def adapt_relations(rows) -> list[tuple]:
    """(object_id, schema, name, type, rows, comment, definition) → forma pg."""
    out = []
    for object_id, schema, name, otype, est_rows, comment, definition in rows:
        relkind = TYPE_TO_RELKIND.get(otype, "r")
        out.append((
            object_id,
            schema,
            name,
            relkind,
            int(est_rows) if (relkind == "r" and est_rows is not None) else None,
            comment,
            definition if relkind == "v" else None,
        ))
    return out


def adapt_columns(rows) -> list[tuple]:
    """Formatea el tipo y deja la forma (attrelid, attnum, nombre, tipo, …)."""
    return [
        (
            object_id,
            column_id,
            name,
            format_type(type_name, max_length, precision, scale),
            bool(is_nullable),
            default_expr,
            comment,
        )
        for object_id, column_id, name, type_name, max_length, precision, scale,
            is_nullable, default_expr, comment in rows
    ]


def adapt_constraints(key_rows, check_rows, fk_rows) -> list[tuple]:
    """Agrupa filas por-columna en la forma de pg_constraint:
    (conname, contype, conrelid, conkey, confrelid, confkey, upd, dele, def)."""
    out: list[tuple] = []

    # PK / UNIQUE (kc.type: 'PK' | 'UQ')
    keys: dict[tuple, dict] = {}
    for name, ktype, parent_id, column_id, _ordinal in key_rows:
        entry = keys.setdefault((parent_id, name), {"type": ktype, "cols": []})
        entry["cols"].append(column_id)
    for (parent_id, name), entry in keys.items():
        contype = "p" if entry["type"] == "PK" else "u"
        out.append((name, contype, parent_id, entry["cols"], 0, None, "a", "a", None))

    # CHECK
    for name, parent_id, definition in check_rows:
        out.append((name, "c", parent_id, None, 0, None, "a", "a", definition))

    # FK (agrupado por constraint, preservando el orden de columnas)
    fks: dict[tuple, dict] = {}
    for name, parent_id, ref_id, upd, dele, parent_col, ref_col, _ord in fk_rows:
        entry = fks.setdefault(
            (parent_id, name),
            {"ref": ref_id, "upd": upd, "dele": dele, "cols": [], "ref_cols": []},
        )
        entry["cols"].append(parent_col)
        entry["ref_cols"].append(ref_col)
    for (parent_id, name), entry in fks.items():
        out.append((
            name,
            "f",
            parent_id,
            entry["cols"],
            entry["ref"],
            entry["ref_cols"],
            ACTION_TO_CHAR.get(entry["upd"], "a"),
            ACTION_TO_CHAR.get(entry["dele"], "a"),
            None,
        ))
    return out


def adapt_indexes(rows) -> list[tuple]:
    """Agrupa por índice: (indrelid, nombre, is_unique, método, "colids")."""
    grouped: dict[tuple, dict] = {}
    order: list[tuple] = []
    for object_id, name, is_unique, method, column_id, _ordinal in rows:
        key = (object_id, name)
        if key not in grouped:
            grouped[key] = {"unique": bool(is_unique), "method": method, "cols": []}
            order.append(key)
        grouped[key]["cols"].append(column_id)
    return [
        (
            object_id,
            name,
            grouped[(object_id, name)]["unique"],
            grouped[(object_id, name)]["method"],
            " ".join(str(c) for c in grouped[(object_id, name)]["cols"]),
        )
        for object_id, name in order
    ]


def adapt_routines(routine_rows, param_rows) -> list[tuple]:
    """(schema, nombre, prokind, lang, args, body) — lang siempre 'tsql'."""
    params: dict[int, list[str]] = {}
    for object_id, _pid, name, type_name in param_rows:
        params.setdefault(object_id, []).append(f"{name} {type_name or ''}".strip())
    return [
        (
            schema,
            name,
            "p" if otype == "P" else "f",
            "tsql",
            ", ".join(params.get(object_id, [])),
            definition or "",
        )
        for object_id, schema, name, otype, definition in routine_rows
    ]
