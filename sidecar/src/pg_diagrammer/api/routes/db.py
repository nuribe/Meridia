"""Exploración de metadatos de una base de datos concreta (vía perfil)."""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

import json
import math

import psycopg
from psycopg import sql as pgsql
from pydantic import BaseModel

from pg_diagrammer.api.routes.profiles import _error, password_missing_error
from pg_diagrammer.connections.profiles import PasswordUnavailable as _PU  # noqa: F401
from pg_diagrammer.connections.profiles import PasswordUnavailable
from pg_diagrammer.domain.models import ObjectSummary, QuerySpec, Snapshot, TableKind
from pg_diagrammer.domain import query_builder
from pg_diagrammer.errors import ApiError, classify_pg_error
from pg_diagrammer.export.generators import to_dbml, to_mermaid
from pg_diagrammer.introspection import introspector
from pg_diagrammer.introspection.introspector import diff_snapshots, routines_using
from pg_diagrammer.introspection.view_joins import collect_relations, parse_view_joins

router = APIRouter(tags=["db"])


def _snapshot(request: Request, profile_id: str, dbname: str, force: bool = False):
    """Devuelve el snapshot cacheado o introspecta bajo demanda.

    Retorna (snapshot, None) o (None, JSONResponse de error).
    """
    store = request.app.state.profiles
    cache = request.app.state.snapshots
    profile = store.get(profile_id)
    if profile is None:
        return None, _error(404, ApiError(code="NOT_FOUND", message="Perfil inexistente."))
    if not force:
        cached = cache.get(profile_id, dbname)
        if cached is not None:
            return cached, None
    try:
        conninfo = store.conninfo(profile, dbname)
        snapshot = introspector.introspect(conninfo, dbname)
    except PasswordUnavailable:
        return None, password_missing_error(profile_id)
    except (psycopg.Error, OSError) as exc:
        return None, _error(400, classify_pg_error(exc))
    cache.set(profile_id, dbname, snapshot)
    return snapshot, None


def _summary(snapshot: Snapshot) -> dict:
    return {
        "ok": True,
        "snapshot_id": snapshot.snapshot_id,
        "created_at": snapshot.created_at.isoformat(),
        "dbname": snapshot.dbname,
        "schemas": [s.model_dump() for s in snapshot.schemas],
        "object_count": len(snapshot.tables),
        "relationship_count": len(snapshot.relationships),
    }


@router.post("/profiles/{profile_id}/db/{dbname}/introspect")
def introspect_db(profile_id: str, dbname: str, request: Request):
    snapshot, err = _snapshot(request, profile_id, dbname)
    return err if err else _summary(snapshot)


@router.post("/profiles/{profile_id}/db/{dbname}/refresh")
def refresh_db(profile_id: str, dbname: str, request: Request):
    old = request.app.state.snapshots.get(profile_id, dbname)
    snapshot, err = _snapshot(request, profile_id, dbname, force=True)
    if err:
        return err
    return {**_summary(snapshot), "diff": diff_snapshots(old, snapshot)}


@router.get("/profiles/{profile_id}/db/{dbname}/objects")
def list_objects(
    profile_id: str,
    dbname: str,
    request: Request,
    schema: str | None = None,
    kind: TableKind | None = None,
    q: str | None = None,
    limit: int = Query(default=200, le=1000),
    offset: int = Query(default=0, ge=0),
):
    snapshot, err = _snapshot(request, profile_id, dbname)
    if err:
        return err
    items = list(snapshot.tables.values())
    if schema:
        # Acepta uno o varios schemas separados por comas
        allowed = {sc.strip() for sc in schema.split(",") if sc.strip()}
        items = [t for t in items if t.schema_name in allowed]
    if kind:
        items = [t for t in items if t.kind == kind]
    if q:
        needle = q.lower()
        items = [
            t for t in items
            if needle in t.name.lower()
            or any(needle in c.name.lower() for c in t.columns)
        ]
    total = len(items)
    page = items[offset : offset + limit]
    return {
        "ok": True,
        "total": total,
        "items": [
            ObjectSummary(
                schema_name=t.schema_name,
                name=t.name,
                kind=t.kind,
                comment=t.comment,
                estimated_rows=t.estimated_rows,
            ).model_dump()
            for t in page
        ],
    }


@router.get("/profiles/{profile_id}/db/{dbname}/tables/{schema}/{table}")
def table_detail(profile_id: str, dbname: str, schema: str, table: str, request: Request):
    snapshot, err = _snapshot(request, profile_id, dbname)
    if err:
        return err
    key = f"{schema}.{table}"
    found = snapshot.tables.get(key)
    if found is None:
        return _error(404, ApiError(
            code="NOT_FOUND",
            message=f"No existe {schema}.{table} en el snapshot.",
            hint="Si la tabla es nueva, ejecuta refresh para re-introspectar.",
        ))
    referenced_by = [
        r.model_dump()
        for r in snapshot.relationships
        if r.target == key and r.source != key
    ]
    routines = [r.model_dump() for r in routines_using(snapshot, key)]
    return {
        "ok": True,
        "table": found.model_dump(),
        "referenced_by": referenced_by,
        "routines": routines,
        "views": snapshot.view_usage.get(key, []),
    }


@router.get("/profiles/{profile_id}/db/{dbname}/tables/{schema}/{table}/related")
def related_tables(
    profile_id: str,
    dbname: str,
    schema: str,
    table: str,
    request: Request,
    direction: str = "both",
):
    """Tablas relacionadas con la dada.

    direction:
      - "in"   → tablas que la referencian (dependientes, "debajo")
      - "out"  → tablas a las que apunta con sus FKs (referenciadas, "arriba")
      - "both" → ambas (por defecto)
    """
    if direction not in ("in", "out", "both"):
        return _error(422, ApiError(
            code="VALIDATION",
            message=f"direction inválida: {direction}",
            hint="Valores permitidos: in, out, both.",
        ))
    snapshot, err = _snapshot(request, profile_id, dbname)
    if err:
        return err
    key = f"{schema}.{table}"
    related: set[str] = set()
    for r in snapshot.relationships:
        if direction in ("out", "both") and r.source == key:
            related.add(r.target)
        if direction in ("in", "both") and r.target == key:
            related.add(r.source)
    related.discard(key)
    return {"ok": True, "related": sorted(related)}


@router.get("/profiles/{profile_id}/db/{dbname}/views/{schema}/{view}/depends-on")
def view_depends_on(profile_id: str, dbname: str, schema: str, view: str, request: Request):
    """Tablas/vistas de las que depende una vista (pg_rewrite/pg_depend)."""
    snapshot, err = _snapshot(request, profile_id, dbname)
    if err:
        return err
    key = f"{schema}.{view}"
    found = snapshot.tables.get(key)
    if found is None:
        return _error(404, ApiError(code="NOT_FOUND", message=f"No existe {key} en el snapshot."))
    if found.kind not in (TableKind.view, TableKind.matview):
        return _error(422, ApiError(
            code="VALIDATION",
            message=f"{key} no es una vista.",
            hint="Este endpoint solo aplica a vistas y vistas materializadas.",
        ))
    # Resolución de nombres contra TODO el snapshot: claves completas siempre,
    # nombres sueltos solo si no son ambiguos entre schemas.
    known: dict[str, str] = {}
    name_counts: dict[str, int] = {}
    for t in snapshot.tables:
        known[t] = t
        bare = t.split(".", 1)[1]
        name_counts[bare] = name_counts.get(bare, 0) + 1
    for t in snapshot.tables:
        bare = t.split(".", 1)[1]
        if name_counts[bare] == 1:
            known.setdefault(bare, t)

    # Unión de dos fuentes: dependencias registradas (pg_depend) y relaciones
    # que aparecen textualmente en el FROM/JOIN del SQL de la vista.
    dep_tables = {t for t, views in snapshot.view_usage.items() if key in views}
    definition = found.definition or ""
    sql_rels = collect_relations(definition, known)
    sql_rels.discard(key)
    tables = sorted(dep_tables | sql_rels)

    joins = parse_view_joins(definition, known)
    return {"ok": True, "tables": tables, "joins": joins}


ROUTINE_DEF_SQL = """
    SELECT pg_catalog.pg_get_functiondef(p.oid)
    FROM pg_catalog.pg_proc p
    JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = %s AND p.proname = %s
      AND pg_catalog.pg_get_function_identity_arguments(p.oid) = %s
"""


@router.get("/profiles/{profile_id}/db/{dbname}/routines/{schema}/{routine}/definition")
def routine_definition(
    profile_id: str,
    dbname: str,
    schema: str,
    routine: str,
    request: Request,
    args: str = "",
):
    """Código fuente completo (CREATE ...) de una función o procedimiento."""
    store = request.app.state.profiles
    profile = store.get(profile_id)
    if profile is None:
        return _error(404, ApiError(code="NOT_FOUND", message="Perfil inexistente."))
    try:
        conninfo = store.conninfo(profile, dbname)
        with psycopg.connect(conninfo) as conn:
            with conn.cursor() as cur:
                cur.execute(ROUTINE_DEF_SQL, (schema, routine, args))
                row = cur.fetchone()
        if row is None:
            return _error(404, ApiError(
                code="NOT_FOUND",
                message=f"No existe {schema}.{routine}({args}).",
                hint="Si la rutina cambió de firma, refresca el snapshot.",
            ))
        return {"ok": True, "definition": row[0]}
    except PasswordUnavailable:
        return password_missing_error(profile_id)
    except (psycopg.Error, OSError) as exc:
        return _error(400, classify_pg_error(exc))


def _jsonable(v):
    """Convierte un valor de PostgreSQL a algo serializable y legible."""
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return str(v) if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(v, (bytes, memoryview)):
        h = bytes(v).hex()
        return f"\\x{h[:120]}{'…' if len(h) > 120 else ''}"
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=str)
    return str(v)


@router.get("/profiles/{profile_id}/db/{dbname}/tables/{schema}/{table}/data")
def table_data(
    profile_id: str,
    dbname: str,
    schema: str,
    table: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    with_total: bool = False,
    order_by: str | None = None,
    order_dir: str = "asc",
    filters: str = "",  # JSON {"columna": "texto"} → col::text ILIKE %texto%
):
    """Página de datos de una tabla/vista, con paginación real en la BD.

    El orden es estable (por PK si existe) para que las páginas no bailen.
    Solo se permite sobre objetos presentes en el snapshot (evita inyección:
    los identificadores se citan con psycopg.sql.Identifier).
    """
    snapshot, err = _snapshot(request, profile_id, dbname)
    if err:
        return err
    key = f"{schema}.{table}"
    found = snapshot.tables.get(key)
    if found is None:
        return _error(404, ApiError(
            code="NOT_FOUND",
            message=f"No existe {key} en el snapshot.",
            hint="Si el objeto es nuevo, ejecuta refresh.",
        ))

    store = request.app.state.profiles
    profile = store.get(profile_id)
    colnames = {c.name for c in found.columns}

    # Filtros por columna (texto, ILIKE); columnas validadas, valores parametrizados
    try:
        filter_map = json.loads(filters) if filters else {}
    except json.JSONDecodeError:
        filter_map = {}
    conditions = []
    params: list = []
    if isinstance(filter_map, dict):
        for col, val in filter_map.items():
            if col in colnames and str(val).strip():
                conditions.append(
                    pgsql.SQL("{}::text ILIKE %s").format(pgsql.Identifier(col))
                )
                params.append(f"%{val}%")
    where = (
        pgsql.SQL(" WHERE ") + pgsql.SQL(" AND ").join(conditions)
        if conditions
        else pgsql.SQL("")
    )

    # Ordenación: columna pedida (validada) o PK como orden estable por defecto
    if order_by and order_by in colnames and order_dir in ("asc", "desc"):
        order = pgsql.SQL(" ORDER BY {} {}").format(
            pgsql.Identifier(order_by), pgsql.SQL("DESC" if order_dir == "desc" else "ASC")
        )
    elif found.pk:
        order = pgsql.SQL(" ORDER BY {}").format(
            pgsql.SQL(", ").join(pgsql.Identifier(c) for c in found.pk)
        )
    else:
        order = pgsql.SQL("")

    query = pgsql.SQL("SELECT * FROM {}.{}{}{} LIMIT %s OFFSET %s").format(
        pgsql.Identifier(schema), pgsql.Identifier(table), where, order
    )
    count_query = pgsql.SQL("SELECT count(*) FROM {}.{}{}").format(
        pgsql.Identifier(schema), pgsql.Identifier(table), where
    )
    try:
        conninfo = store.conninfo(profile, dbname)
        with psycopg.connect(conninfo) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (*params, limit, offset))
                columns = [d.name for d in cur.description or []]
                rows = [[_jsonable(v) for v in row] for row in cur.fetchall()]
                total = None
                if with_total:
                    cur.execute(count_query, params)
                    total = cur.fetchone()[0]
        return {"ok": True, "columns": columns, "rows": rows, "total": total}
    except PasswordUnavailable:
        return password_missing_error(profile_id)
    except (psycopg.Error, OSError) as exc:
        return _error(400, classify_pg_error(exc))


class QueryRequest(BaseModel):
    sql: str
    max_rows: int = 1000
    timeout_ms: int = 15000


@router.post("/profiles/{profile_id}/db/{dbname}/query")
def run_query(profile_id: str, dbname: str, body: QueryRequest, request: Request):
    """Ejecuta una consulta del usuario en una transacción de SOLO LECTURA.

    Protecciones: SET TRANSACTION READ ONLY (rechaza INSERT/UPDATE/DELETE/DDL),
    statement_timeout, y límite de filas devueltas. La ejecución usa la misma
    conexión/credenciales del perfil.
    """
    store = request.app.state.profiles
    profile = store.get(profile_id)
    if profile is None:
        return _error(404, ApiError(code="NOT_FOUND", message="Perfil inexistente."))
    sql_text = body.sql.strip().rstrip(";")
    if not sql_text:
        return _error(422, ApiError(code="VALIDATION", message="Consulta vacía."))
    max_rows = max(1, min(body.max_rows, 5000))
    timeout = max(1000, min(body.timeout_ms, 60000))
    try:
        conninfo = store.conninfo(profile, dbname)
        import time
        t0 = time.perf_counter()
        with psycopg.connect(conninfo) as conn:
            conn.read_only = True  # transacción de solo lectura
            with conn.cursor() as cur:
                cur.execute(f"SET statement_timeout = {int(timeout)}")
                cur.execute(sql_text)
                if cur.description is None:
                    # Sentencia sin resultados (p. ej. SET); no debería ocurrir en READ ONLY
                    return {"ok": True, "columns": [], "rows": [], "row_count": cur.rowcount, "truncated": False, "elapsed_ms": 0}
                columns = [d.name for d in cur.description]
                fetched = cur.fetchmany(max_rows + 1)
                truncated = len(fetched) > max_rows
                rows = [[_jsonable(v) for v in row] for row in fetched[:max_rows]]
        elapsed = int((time.perf_counter() - t0) * 1000)
        return {
            "ok": True,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "elapsed_ms": elapsed,
        }
    except PasswordUnavailable:
        return password_missing_error(profile_id)
    except (psycopg.Error, OSError) as exc:
        err = classify_pg_error(exc)
        # Errores de SQL del usuario: mensaje directo de PostgreSQL
        if isinstance(exc, psycopg.Error) and err.code == "UNEXPECTED":
            msg = str(exc).strip()
            hint = None
            if isinstance(exc, psycopg.errors.ReadOnlySqlTransaction):
                hint = "Solo se permiten consultas de lectura (SELECT, WITH, SHOW…)."
            return _error(400, ApiError(code="SQL_ERROR", message=msg, hint=hint))
        return _error(400, err)


class RelationshipsRequest(BaseModel):
    tables: list[str]  # ["schema.tabla", ...]


@router.post("/profiles/{profile_id}/db/{dbname}/relationships")
def relationships(profile_id: str, dbname: str, body: RelationshipsRequest, request: Request):
    snapshot, err = _snapshot(request, profile_id, dbname)
    if err:
        return err
    wanted = set(body.tables)
    edges = [
        r.model_dump()
        for r in snapshot.relationships
        if r.source in wanted and r.target in wanted
    ]
    return {"ok": True, "relationships": edges}


def _known_names(snapshot: Snapshot) -> dict[str, str]:
    """Mapa de resolución de nombres: claves completas + nombres sueltos no
    ambiguos -> "schema.tabla". Mismo criterio que la dependencia de vistas."""
    known: dict[str, str] = {}
    name_counts: dict[str, int] = {}
    for t in snapshot.tables:
        known[t] = t
        bare = t.split(".", 1)[1]
        name_counts[bare] = name_counts.get(bare, 0) + 1
    for t in snapshot.tables:
        bare = t.split(".", 1)[1]
        if name_counts[bare] == 1:
            known.setdefault(bare, t)
    return known


@router.post("/profiles/{profile_id}/db/{dbname}/query/build")
def build_query(profile_id: str, dbname: str, body: QuerySpec, request: Request):
    """Traduce el diagrama del constructor (tablas + joins) a SQL PostgreSQL.

    Valida que cada tabla y columna exista en el snapshot: así el SQL generado
    solo referencia objetos reales (coherente con la ejecución de solo lectura).
    """
    snapshot, err = _snapshot(request, profile_id, dbname)
    if err:
        return err
    if not body.tables:
        return _error(422, ApiError(
            code="VALIDATION",
            message="Se requiere al menos una tabla para construir la consulta.",
            hint="Arrastra tablas al lienzo antes de pulsar «Listo».",
        ))
    for key in body.tables:
        found = snapshot.tables.get(key)
        if found is None:
            return _error(422, ApiError(
                code="VALIDATION",
                message=f"La tabla {key} no existe en el snapshot.",
                hint="Refresca la introspección si la tabla es nueva.",
            ))
    # Validación de columnas referenciadas en los joins.
    cols_by_table = {
        key: {c.name for c in t.columns} for key, t in snapshot.tables.items()
    }
    for j in body.joins:
        jt = (j.join_type or "").strip().upper()
        if jt == "CROSS JOIN":
            continue
        for key, cols in ((j.source, j.source_columns), (j.target, j.target_columns)):
            for col in cols:
                if col not in cols_by_table.get(key, set()):
                    return _error(422, ApiError(
                        code="VALIDATION",
                        message=f"La columna {key}.{col} no existe.",
                        hint="Revisa las columnas unidas en la relación del lienzo.",
                    ))
    model = query_builder.QueryModel(
        tables=list(body.tables),
        aliases=dict(body.aliases),
        joins=[
            query_builder.Join(
                source=j.source,
                target=j.target,
                join_type=j.join_type,
                source_columns=list(j.source_columns),
                target_columns=list(j.target_columns),
            )
            for j in body.joins
        ],
        select_sql=body.select_sql,
        tail_sql=body.tail_sql,
    )
    try:
        sql_text = query_builder.build_query_sql(model)
    except ValueError as exc:
        return _error(422, ApiError(code="VALIDATION", message=str(exc)))
    return {"ok": True, "sql": sql_text}


class ParseQueryRequest(BaseModel):
    sql: str


@router.post("/profiles/{profile_id}/db/{dbname}/query/parse")
def parse_query(profile_id: str, dbname: str, body: ParseQueryRequest, request: Request):
    """Analiza una sentencia SQL y devuelve el diagrama equivalente."""
    snapshot, err = _snapshot(request, profile_id, dbname)
    if err:
        return err
    if not body.sql.strip():
        return _error(422, ApiError(
            code="VALIDATION",
            message="No hay SQL que analizar.",
            hint="Escribe una consulta en el editor antes de pulsar «Diagrama».",
        ))
    known = _known_names(snapshot)
    model = query_builder.parse_query_sql(body.sql, known)
    if not model.tables:
        return _error(422, ApiError(
            code="SQL_ERROR",
            message="No se reconocieron tablas del snapshot en el FROM/JOIN.",
            hint="Comprueba que la consulta referencie tablas existentes.",
        ))
    return {
        "ok": True,
        "tables": model.tables,
        "aliases": model.aliases,
        "joins": [
            {
                "source": j.source,
                "target": j.target,
                "join_type": j.join_type,
                "source_columns": j.source_columns,
                "target_columns": j.target_columns,
            }
            for j in model.joins
        ],
        "select_sql": model.select_sql,
        "tail_sql": model.tail_sql,
        "unresolved": model.unresolved,
        "warnings": model.warnings,
    }


class ExportRequest(BaseModel):
    tables: list[str]
    format: str  # "mermaid" | "dbml"


@router.post("/profiles/{profile_id}/db/{dbname}/export")
def export_model(profile_id: str, dbname: str, body: ExportRequest, request: Request):
    """Exporta las tablas indicadas a un formato editable de texto."""
    snapshot, err = _snapshot(request, profile_id, dbname)
    if err:
        return err
    if body.format == "mermaid":
        return {"ok": True, "content": to_mermaid(snapshot, body.tables), "extension": "mmd"}
    if body.format == "dbml":
        return {"ok": True, "content": to_dbml(snapshot, body.tables), "extension": "dbml"}
    return _error(422, ApiError(
        code="VALIDATION",
        message=f"Formato no soportado: {body.format}",
        hint="Formatos disponibles: mermaid, dbml.",
    ))
