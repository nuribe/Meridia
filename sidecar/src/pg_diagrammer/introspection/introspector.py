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

    routines = []
    for row in routine_rows or []:
        # La 7ª columna (proconfig) sólo la aporta PostgreSQL; SQL Server envía 6.
        schema, name, prokind, lang, args, body = row[:6]
        routines.append(
            Routine(
                schema_name=schema,
                name=name,
                kind="procedure" if prokind == "p" else "function",
                language=lang,
                args=args or "",
                body=body or "",
                search_path=_search_path_of(row[6] if len(row) > 6 else None),
            )
        )

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


def refresh_table(conninfo: str, snapshot: Snapshot, schema: str, name: str) -> Table | None:
    """Refresh granular: re-introspecta UNA tabla y actualiza el snapshot en sitio.

    Complementa (no sustituye) la introspección en bloque: evita re-leer toda la
    base cuando el usuario sabe qué tabla cambió. Devuelve la tabla nueva, o None
    si ya no existe (en cuyo caso también se retira del snapshot).
    """
    with psycopg.connect(conninfo) as conn:
        with conn.cursor() as cur:
            cur.execute(queries.RELATION_ONE, (schema, name))
            rel = cur.fetchone()
            if rel is None:
                remove_table(snapshot, f"{schema}.{name}")
                return None
            oid = rel[0]
            cur.execute(queries.COLUMNS_ONE, (oid,))
            col_rows = cur.fetchall()
            cur.execute(queries.CONSTRAINTS_ONE, (oid,))
            con_rows = cur.fetchall()
            cur.execute(queries.INDEXES_ONE, (oid,))
            idx_rows = cur.fetchall()
            ref_oids = sorted({r[4] for r in con_rows if r[1] == "f" and r[4]})
            ref_name_rows: list[tuple] = []
            ref_col_rows: list[tuple] = []
            if ref_oids:
                cur.execute(queries.REF_TABLE_NAMES, (ref_oids,))
                ref_name_rows = cur.fetchall()
                cur.execute(queries.REF_TABLE_COLUMNS, (ref_oids,))
                ref_col_rows = cur.fetchall()
    table = assemble_one(rel, col_rows, con_rows, idx_rows, ref_name_rows, ref_col_rows)
    apply_table_refresh(snapshot, f"{schema}.{name}", table)
    return table


def assemble_one(
    rel: tuple,
    col_rows: list[tuple],
    con_rows: list[tuple],
    idx_rows: list[tuple],
    ref_name_rows: list[tuple],
    ref_col_rows: list[tuple],
) -> Table:
    """Ensambla una sola Table a partir de sus filas crudas (función pura).

    Espeja el tramo correspondiente de `assemble()`; las referencias de las FKs
    se resuelven con las filas auxiliares en vez del resto del snapshot, para no
    depender de oids potencialmente obsoletos.
    """
    oid, schema, name, relkind, est_rows, comment, definition = rel
    table = Table(
        schema_name=schema,
        name=name,
        oid=oid,
        kind=RELKIND_MAP.get(relkind, TableKind.table),
        comment=comment,
        estimated_rows=est_rows if est_rows is not None and est_rows >= 0 else None,
        definition=definition,
    )

    attnames: dict[int, str] = {}
    for _attrelid, attnum, attname, data_type, is_nullable, default, col_comment in col_rows:
        attnames[attnum] = attname
        table.columns.append(
            Column(
                name=attname,
                position=attnum,
                data_type=data_type,
                is_nullable=is_nullable,
                default=default,
                comment=col_comment,
            )
        )

    ref_names = {roid: (rschema, rname) for roid, rschema, rname in ref_name_rows}
    ref_attnames: dict[int, dict[int, str]] = {}
    for attrelid, attnum, attname in ref_col_rows:
        ref_attnames.setdefault(attrelid, {})[attnum] = attname

    def colnames(attnums: list[int] | None) -> list[str]:
        return [attnames[n] for n in (attnums or []) if n in attnames]

    def ref_colnames(roid: int, attnums: list[int] | None) -> list[str]:
        mapping = ref_attnames.get(roid, {})
        return [mapping[n] for n in (attnums or []) if n in mapping]

    for conname, contype, _conrelid, conkey, confrelid, confkey, upd, dele, definition in con_rows:
        cols = colnames(conkey)
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
            ref = ref_names.get(confrelid)
            if ref is None:
                continue
            table.foreign_keys.append(
                ForeignKey(
                    name=conname,
                    columns=cols,
                    ref_schema=ref[0],
                    ref_table=ref[1],
                    ref_columns=ref_colnames(confrelid, confkey),
                    on_update=FK_ACTION_MAP.get(upd, "NO ACTION"),
                    on_delete=FK_ACTION_MAP.get(dele, "NO ACTION"),
                )
            )

    for _indrelid, index_name, is_unique, method, attnums_text in idx_rows:
        nums = [int(x) for x in str(attnums_text).split() if x.isdigit() and int(x) > 0]
        table.indexes.append(
            Index(
                name=index_name,
                columns=colnames(nums),
                is_unique=is_unique,
                method=method,
            )
        )
    return table


def apply_table_refresh(snapshot: Snapshot, key: str, table: Table) -> None:
    """Sustituye la tabla en el snapshot y re-deriva sus relaciones salientes.

    Las relaciones entrantes (FKs de otras tablas hacia esta) no cambian: su
    cardinalidad se deriva de los constraints de la tabla ORIGEN, que no se tocó.
    """
    snapshot.tables[key] = table
    snapshot.relationships = [r for r in snapshot.relationships if r.source != key]
    for fk in table.foreign_keys:
        snapshot.relationships.append(
            Relationship(
                source=key,
                target=f"{fk.ref_schema}.{fk.ref_table}",
                fk_name=fk.name,
                columns=fk.columns,
                ref_columns=fk.ref_columns,
                cardinality=derive_cardinality(table, fk),
            )
        )


def remove_table(snapshot: Snapshot, key: str) -> None:
    """Retira del snapshot una tabla que ya no existe en la base."""
    snapshot.tables.pop(key, None)
    snapshot.relationships = [
        r for r in snapshot.relationships if r.source != key and r.target != key
    ]
    snapshot.view_usage.pop(key, None)


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


# --- detección de rutinas que usan una tabla --------------------------------

_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_DOLLAR_QUOTED = re.compile(r"\$(?P<tag>[A-Za-z_]\w*|)\$.*?\$(?P=tag)\$", re.S)
_SINGLE_QUOTED = re.compile(r"'(?:[^']|'')*'", re.S)

# Palabras clave tras las que un identificador es, con seguridad, una relación.
# Exigirlas descarta variables, parámetros y textos sueltos con el mismo nombre.
_REL_CONTEXT = r"from|join|into|update|delete\s+from|truncate|table|using|references"


def _strip_noise(src: str) -> str:
    """Quita comentarios, literales y bloques $$...$$ (SQL dinámico).

    Es de donde salían casi todos los falsos positivos: un RAISE NOTICE 'aviso…'
    o un `-- aviso` contaban como uso de la tabla.
    """
    s = _COMMENTS.sub(" ", src)
    s = _DOLLAR_QUOTED.sub(" ", s)
    return _SINGLE_QUOTED.sub(" ", s)


def _search_path_of(proconfig: list[str] | None) -> str:
    """Extrae el valor de `SET search_path` de pg_proc.proconfig."""
    for entry in proconfig or []:
        if entry.startswith("search_path="):
            return entry.split("=", 1)[1]
    return ""


def _path_schemas(routine: Routine) -> list[str]:
    """Esquemas donde se resuelve un nombre sin calificar, en orden de prioridad.

    Sin `SET search_path` en la rutina asumimos su propio esquema y luego public:
    es lo que hace el código de aplicación en bases multi-esquema, y es lo que
    evita atribuir un `INSERT INTO aviso` de incidencia.* a rrhh.aviso.
    """
    if routine.search_path:
        out = [
            p
            for p in (x.strip().strip('"') for x in routine.search_path.split(","))
            if p and not p.startswith("$") and not p.startswith("pg_")
        ]
        if out:
            return out
    return [routine.schema_name, "public"]


def _bare_resolves_to(
    routine: Routine, target_schema: str, table: str, owners: set[str], snapshot: Snapshot
) -> bool:
    """¿Un `table` sin calificar dentro de `routine` apunta a `target_schema`?"""
    if len(owners) == 1:
        return target_schema in owners  # nombre único en la BD: no hay ambigüedad
    for sch in _path_schemas(routine):
        if f"{sch}.{table}" in snapshot.tables:
            return sch == target_schema  # gana el primer esquema del search_path
    return False


def routines_using(snapshot: Snapshot, key: str) -> list[Routine]:
    """Rutinas cuyo cuerpo referencia realmente la tabla `key`.

    Reglas, de mayor a menor fiabilidad (se anotan en `match_kind`):

    1. "calificada"  — aparece `esquema.tabla` en el código efectivo.
    2. "search_path" — aparece `tabla` sin calificar, en posición de relación
       (tras FROM/JOIN/INTO/UPDATE/…) y resolviendo a `esquema` según el
       search_path de la rutina.
    3. "dinamico"    — `esquema.tabla` sólo aparece dentro de un literal o de
       un bloque $$…$$, es decir SQL dinámico. Es un uso probable, no seguro.

    Antes de aplicarlas se eliminan comentarios, literales y $$…$$, que eran la
    fuente principal de falsos positivos.
    """
    schema, _, table = key.partition(".")
    t = re.escape(table)
    qualified = re.compile(
        rf'(?<![\w."]){re.escape(schema)}\s*\.\s*"?{t}"?(?![\w"])', re.IGNORECASE
    )
    bare = re.compile(
        rf'(?<![\w.])(?:{_REL_CONTEXT})\s+(?:only\s+)?"?{t}"?(?![\w"(])', re.IGNORECASE
    )

    # Esquemas que tienen una relación con ese nombre: si sólo hay uno, cualquier
    # referencia suelta es inequívoca.
    owners = {
        k.split(".", 1)[0]
        for k in snapshot.tables
        if k.split(".", 1)[1].lower() == table.lower()
    }

    found: list[Routine] = []
    for r in snapshot.routines:
        clean = _strip_noise(r.body)
        if qualified.search(clean):
            kind = "calificada"
        elif bare.search(clean) and _bare_resolves_to(r, schema, table, owners, snapshot):
            kind = "search_path"
        elif qualified.search(_COMMENTS.sub(" ", r.body)):
            kind = "dinamico"
        else:
            continue
        found.append(r.model_copy(update={"match_kind": kind}))
    return found


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
