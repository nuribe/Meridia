"""Introspección de una base de datos: ejecuta las queries en bloque
y ensambla el Snapshot del dominio.

`assemble()` es una función pura (filas → Snapshot) para poder testearla
sin base de datos; `introspect()` es el envoltorio con conexión real.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

import psycopg

from pg_diagrammer.domain.models import (
    Cardinality,
    Routine,
    Column,
    ForeignKey,
    Index,
    Relationship,
    SchemaInfo,
    Snapshot,
    Table,
    TableKind,
)
from pg_diagrammer.introspection import queries

RELKIND_MAP = {
    "r": TableKind.table,
    "p": TableKind.partitioned,
    "v": TableKind.view,
    "m": TableKind.matview,
    "f": TableKind.foreign,
}

FK_ACTION_MAP = {
    "a": "NO ACTION",
    "r": "RESTRICT",
    "c": "CASCADE",
    "n": "SET NULL",
    "d": "SET DEFAULT",
}


def introspect(conninfo: str, dbname: str) -> Snapshot:
    """Ejecuta las 5 queries en bloque en una sola conexión."""
    with psycopg.connect(conninfo) as conn:
        with conn.cursor() as cur:
            cur.execute(queries.SCHEMAS)
            schema_rows = cur.fetchall()
            cur.execute(queries.RELATIONS)
            rel_rows = cur.fetchall()
            cur.execute(queries.COLUMNS)
            col_rows = cur.fetchall()
            cur.execute(queries.CONSTRAINTS)
            con_rows = cur.fetchall()
            cur.execute(queries.INDEXES)
            idx_rows = cur.fetchall()
            cur.execute(queries.ROUTINES)
            routine_rows = cur.fetchall()
            cur.execute(queries.VIEW_DEPS)
            view_rows = cur.fetchall()
    return assemble(
        dbname, schema_rows, rel_rows, col_rows, con_rows, idx_rows, routine_rows, view_rows
    )


def assemble(
    dbname: str,
    schema_rows: list[tuple],
    rel_rows: list[tuple],
    col_rows: list[tuple],
    con_rows: list[tuple],
    idx_rows: list[tuple],
    routine_rows: list[tuple] | None = None,
    view_rows: list[tuple] | None = None,
) -> Snapshot:
    """Construye el Snapshot a partir de las filas crudas de pg_catalog."""
    # --- tablas ---
    by_oid: dict[int, Table] = {}
    for oid, schema, name, relkind, est_rows, comment, definition in rel_rows:
        by_oid[oid] = Table(
            schema_name=schema,
            name=name,
            oid=oid,
            kind=RELKIND_MAP.get(relkind, TableKind.table),
            comment=comment,
            estimated_rows=est_rows if est_rows is not None and est_rows >= 0 else None,
            definition=definition,
        )

    # --- columnas (attnum → nombre por tabla, para resolver conkey/confkey) ---
    attnames: dict[int, dict[int, str]] = {}
    for attrelid, attnum, attname, data_type, is_nullable, default, comment in col_rows:
        table = by_oid.get(attrelid)
        if table is None:
            continue
        attnames.setdefault(attrelid, {})[attnum] = attname
        table.columns.append(
            Column(
                name=attname,
                position=attnum,
                data_type=data_type,
                is_nullable=is_nullable,
                default=default,
                comment=comment,
            )
        )

    def colnames(oid: int, attnums: list[int] | None) -> list[str]:
        mapping = attnames.get(oid, {})
        return [mapping[n] for n in (attnums or []) if n in mapping]

    # --- constraints ---
    for conname, contype, conrelid, conkey, confrelid, confkey, upd, dele, definition in con_rows:
        table = by_oid.get(conrelid)
        if table is None:
            continue
        cols = colnames(conrelid, conkey)
        if contype == "p":
            table.pk = cols
            pk_set = set(cols)
            for col in table.columns:
                if col.name in pk_set:
                    col.is_pk = True
        elif contype == "u":
            table.unique_sets.append(cols)
        elif contype == "c":
            table.checks.append(definition)
        elif contype == "f":
            ref = by_oid.get(confrelid)
            if ref is None:
                continue  # referencia a schema filtrado/sin permiso
            table.foreign_keys.append(
                ForeignKey(
                    name=conname,
                    columns=cols,
                    ref_schema=ref.schema_name,
                    ref_table=ref.name,
                    ref_columns=colnames(confrelid, confkey),
                    on_update=FK_ACTION_MAP.get(upd, "NO ACTION"),
                    on_delete=FK_ACTION_MAP.get(dele, "NO ACTION"),
                )
            )

    # --- índices (indkey llega como int2vector en texto: "1 2") ---
    for indrelid, index_name, is_unique, method, attnums_text in idx_rows:
        table = by_oid.get(indrelid)
        if table is None:
            continue
        nums = [int(x) for x in str(attnums_text).split() if x.isdigit() and int(x) > 0]
        table.indexes.append(
            Index(
                name=index_name,
                columns=colnames(indrelid, nums),
                is_unique=is_unique,
                method=method,
            )
        )

    # --- relaciones con cardinalidad derivada ---
    tables = {t.key: t for t in by_oid.values()}
    relationships: list[Relationship] = []
    for table in by_oid.values():
        for fk in table.foreign_keys:
            relationships.append(
                Relationship(
                    source=table.key,
                    target=f"{fk.ref_schema}.{fk.ref_table}",
                    fk_name=fk.name,
                    columns=fk.columns,
                    ref_columns=fk.ref_columns,
                    cardinality=derive_cardinality(table, fk),
                )
            )

    # --- schemas con conteos ---
    schemas = []
    for name, comment in schema_rows:
        t_count = sum(
            1 for t in by_oid.values()
            if t.schema_name == name
            and t.kind in (TableKind.table, TableKind.partitioned, TableKind.foreign)
        )
        v_count = sum(
            1 for t in by_oid.values()
            if t.schema_name == name and t.kind in (TableKind.view, TableKind.matview)
        )
        schemas.append(
            SchemaInfo(name=name, comment=comment, table_count=t_count, view_count=v_count)
        )

    routines = [
        Routine(
            schema_name=schema,
            name=name,
            kind="procedure" if prokind == "p" else "function",
            language=lang,
            args=args or "",
            body=body or "",
        )
        for schema, name, prokind, lang, args, body in (routine_rows or [])
    ]

    view_usage: dict[str, list[str]] = {}
    for view_schema, view_name, table_schema, table_name in view_rows or []:
        view_usage.setdefault(f"{table_schema}.{table_name}", []).append(
            f"{view_schema}.{view_name}"
        )
    for views in view_usage.values():
        views.sort()

    return Snapshot(
        snapshot_id=uuid.uuid4().hex,
        dbname=dbname,
        created_at=datetime.now(timezone.utc),
        schemas=schemas,
        tables=tables,
        relationships=relationships,
        routines=routines,
        view_usage=view_usage,
    )


def derive_cardinality(table: Table, fk: ForeignKey) -> Cardinality:
    """FK cuyas columnas coinciden exactamente con la PK o un UNIQUE propio → 1:1.
    En cualquier otro caso → N:1 (muchas filas origen por fila destino)."""
    fk_cols = set(fk.columns)
    if fk_cols and fk_cols == set(table.pk):
        return Cardinality.one_to_one
    for unique in table.unique_sets:
        if fk_cols == set(unique):
            return Cardinality.one_to_one
    return Cardinality.many_to_one


def routines_using(snapshot: Snapshot, key: str) -> list[Routine]:
    """Rutinas cuyo cuerpo referencia la tabla (por "schema.tabla" o nombre suelto).

    Matching textual sobre el fuente: cubre SQL y PL/pgSQL. Puede dar algún
    falso positivo con nombres muy genéricos, pero no falsos negativos
    razonables (mismo enfoque que las vistas de dependientes de pgAdmin).
    """
    schema, _, table = key.partition(".")
    qualified = re.compile(
        rf'(?<![\w."]){re.escape(schema)}\s*\.\s*"?{re.escape(table)}"?(?![\w"])',
        re.IGNORECASE,
    )
    bare = re.compile(rf'(?<![\w."]){re.escape(table)}(?![\w"])', re.IGNORECASE)
    return [
        r for r in snapshot.routines
        if qualified.search(r.body) or bare.search(r.body)
    ]


def diff_snapshots(old: Snapshot | None, new: Snapshot) -> dict:
    """Diferencias estructurales entre dos snapshots (para el refresh visual).

    Ignora estimated_rows y oid (cambian sin que cambie la estructura).
    """
    if old is None:
        return {"added": [], "removed": [], "changed": []}
    exclude = {"estimated_rows", "oid"}
    old_keys, new_keys = set(old.tables), set(new.tables)
    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    changed = sorted(
        k
        for k in (old_keys & new_keys)
        if old.tables[k].model_dump(exclude=exclude) != new.tables[k].model_dump(exclude=exclude)
    )
    return {"added": added, "removed": removed, "changed": changed}
