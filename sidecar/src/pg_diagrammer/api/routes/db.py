"""Exploración de metadatos de una base de datos concreta (vía perfil).

Estas rutas son el **adaptador HTTP** de `services/catalog.py`: traducen la
petición, llaman al servicio y envuelven el resultado en el contrato REST. La
lógica de catálogo no vive aquí — vive en el servicio, para que el servidor
MCP pueda usar exactamente la misma (ver docs/mcp.md).

Los fallos de negocio se lanzan como `ServiceError` y los convierte en
respuesta el manejador registrado en `api/app.py`; por eso las rutas de
catálogo no llevan try/except. La ejecución de consultas, el plan y la página
de datos sí conservan el suyo: son operaciones sobre la base del usuario, con
sus propios errores de SQL y sus propias reglas de escritura.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

import json

import psycopg
from psycopg import sql as pgsql
import pytds
from pydantic import BaseModel

from pg_diagrammer.api.routes.profiles import _error, password_missing_error
from pg_diagrammer.connections import manager
from pg_diagrammer.connections.profiles import PasswordUnavailable as _PU  # noqa: F401
from pg_diagrammer.connections.profiles import PasswordUnavailable
from pg_diagrammer.domain.models import Engine, QuerySpec, Snapshot, TableKind
from pg_diagrammer.domain import explain as explain_plan
from pg_diagrammer.domain import query_builder
from pg_diagrammer.domain.sql_script import (
    first_keyword,
    is_read_statement,
    missing_where,
    split_statements,
)
from pg_diagrammer.errors import ApiError, DB_EXCEPTIONS, classify_db_error, classify_pg_error  # noqa: F401
from pg_diagrammer.services import catalog
from pg_diagrammer.services.query import PlanTooLarge, mssql_plan, postgres_plan
from pg_diagrammer.services.values import MAX_CELL_CHARS, clip, jsonable  # noqa: F401

router = APIRouter(tags=["db"])


def _stores(request: Request):
    """Los dos stores que necesita casi toda ruta de catálogo."""
    return request.app.state.profiles, request.app.state.snapshots


def _ms_ident(name: str) -> str:
    """Cita un identificador de SQL Server con corchetes."""
    return "[" + name.replace("]", "]]") + "]"


# Las guardas de lectura y la conversión de celdas se comparten con el servidor
# MCP, así que viven en el dominio y en la capa de servicios. Se reexportan con
# el nombre de siempre: son parte de la superficie que ya usaban las pruebas.
_first_keyword = first_keyword


def _read_only_error() -> JSONResponse:
    return _error(400, ApiError(
        code="READ_ONLY",
        message="Este perfil está en modo solo lectura.",
        hint="Activa «Permitir escritura» al editar el perfil de conexión "
             "para poder ejecutar DDL/DML.",
    ))


def _confirm_error(sql_text: str) -> JSONResponse:
    verbo = _first_keyword(sql_text)
    return _error(400, ApiError(
        code="CONFIRM_REQUIRED",
        message=f"{verbo} sin WHERE: afectará a TODAS las filas de la tabla.",
        hint="Si es intencionado, vuelve a ejecutar para confirmar.",
        retriable=True,
    ))


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


def _detail_payload(detail: catalog.TableDetail) -> dict:
    return {
        "table": detail.table.model_dump(),
        "referenced_by": [r.model_dump() for r in detail.referenced_by],
        "routines": [r.model_dump() for r in detail.routines],
        "views": detail.views,
    }


@router.post("/profiles/{profile_id}/db/{dbname}/introspect")
def introspect_db(profile_id: str, dbname: str, request: Request):
    store, cache = _stores(request)
    return _summary(catalog.get_snapshot(store, cache, profile_id, dbname))


@router.post("/profiles/{profile_id}/db/{dbname}/refresh")
def refresh_db(profile_id: str, dbname: str, request: Request):
    store, cache = _stores(request)
    snapshot, diff = catalog.refresh_snapshot(store, cache, profile_id, dbname)
    return {**_summary(snapshot), "diff": diff}


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
    store, cache = _stores(request)
    snapshot = catalog.get_snapshot(store, cache, profile_id, dbname)
    total, page = catalog.list_objects(
        snapshot, schema=schema, kind=kind, q=q, limit=limit, offset=offset
    )
    return {"ok": True, "total": total, "items": [o.model_dump() for o in page]}


@router.get("/profiles/{profile_id}/db/{dbname}/tables/{schema}/{table}")
def table_detail(profile_id: str, dbname: str, schema: str, table: str, request: Request):
    store, cache = _stores(request)
    snapshot = catalog.get_snapshot(store, cache, profile_id, dbname)
    return {"ok": True, **_detail_payload(catalog.table_detail(snapshot, schema, table))}


@router.post("/profiles/{profile_id}/db/{dbname}/tables/{schema}/{table}/refresh")
def refresh_table(profile_id: str, dbname: str, schema: str, table: str, request: Request):
    """Refresh granular de una tabla (menú «Actualizar» del árbol)."""
    store, cache = _stores(request)
    detail = catalog.refresh_table(store, cache, profile_id, dbname, schema, table)
    if detail is None:
        return {"ok": True, "removed": True}
    return {"ok": True, "removed": False, **_detail_payload(detail)}


@router.get("/profiles/{profile_id}/db/{dbname}/tables/{schema}/{table}/related")
def related_tables(
    profile_id: str,
    dbname: str,
    schema: str,
    table: str,
    request: Request,
    direction: str = "both",
):
    """Tablas relacionadas con la dada (in / out / both)."""
    store, cache = _stores(request)
    snapshot = catalog.get_snapshot(store, cache, profile_id, dbname)
    return {"ok": True, "related": catalog.related(snapshot, schema, table, direction)}


@router.get("/profiles/{profile_id}/db/{dbname}/views/{schema}/{view}/depends-on")
def view_depends_on(profile_id: str, dbname: str, schema: str, view: str, request: Request):
    """Tablas/vistas de las que depende una vista (pg_rewrite/pg_depend)."""
    store, cache = _stores(request)
    snapshot = catalog.get_snapshot(store, cache, profile_id, dbname)
    tables, joins = catalog.view_dependencies(snapshot, schema, view)
    return {"ok": True, "tables": tables, "joins": joins}


ROUTINE_DEF_SQL = """
    SELECT pg_catalog.pg_get_functiondef(p.oid)
    FROM pg_catalog.pg_proc p
    JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = %s AND p.proname = %s
      AND pg_catalog.pg_get_function_identity_arguments(p.oid) = %s
"""

# En SQL Server no hay sobrecarga de rutinas: schema + nombre identifican.
MSSQL_ROUTINE_DEF_SQL = """
    SELECT sm.definition
    FROM sys.sql_modules sm
    JOIN sys.objects o ON o.object_id = sm.object_id
    WHERE SCHEMA_NAME(o.schema_id) = %s AND o.name = %s
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
    profile = catalog.get_profile(store, profile_id)
    try:
        with manager.open_profile_connection(store, profile, dbname) as conn:
            with conn.cursor() as cur:
                if profile.engine == Engine.sqlserver:
                    cur.execute(MSSQL_ROUTINE_DEF_SQL, (schema, routine))
                else:
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
    except DB_EXCEPTIONS as exc:
        return _error(400, classify_db_error(profile.engine, exc))


_clip = clip
_jsonable = jsonable


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
    store, cache = _stores(request)
    snapshot = catalog.get_snapshot(store, cache, profile_id, dbname)
    key = f"{schema}.{table}"
    found = snapshot.tables.get(key)
    if found is None:
        return _error(404, ApiError(
            code="NOT_FOUND",
            message=f"No existe {key} en el snapshot.",
            hint="Si el objeto es nuevo, ejecuta refresh.",
        ))

    profile = catalog.get_profile(store, profile_id)
    colnames = {c.name for c in found.columns}

    # Filtros por columna (texto, insensible a mayúsculas);
    # columnas validadas contra el snapshot, valores parametrizados.
    try:
        filter_map = json.loads(filters) if filters else {}
    except json.JSONDecodeError:
        filter_map = {}
    active_filters = [
        (col, f"%{val}%")
        for col, val in (filter_map.items() if isinstance(filter_map, dict) else [])
        if col in colnames and str(val).strip()
    ]
    order_col = order_by if (order_by and order_by in colnames and order_dir in ("asc", "desc")) else None

    if profile.engine == Engine.sqlserver:
        # SQL Server: corchetes, LIKE sobre NVARCHAR y OFFSET/FETCH
        # (que exige ORDER BY; sin PK se usa el orden neutro (SELECT NULL)).
        where_sql = (
            " WHERE " + " AND ".join(
                f"LOWER(CAST({_ms_ident(c)} AS NVARCHAR(MAX))) LIKE LOWER(%s)"
                for c, _ in active_filters
            )
            if active_filters else ""
        )
        if order_col:
            order_sql = f" ORDER BY {_ms_ident(order_col)} {'DESC' if order_dir == 'desc' else 'ASC'}"
        elif found.pk:
            order_sql = " ORDER BY " + ", ".join(_ms_ident(c) for c in found.pk)
        else:
            order_sql = " ORDER BY (SELECT NULL)"
        target_sql = f"{_ms_ident(schema)}.{_ms_ident(table)}"
        query = (
            f"SELECT * FROM {target_sql}{where_sql}{order_sql}"
            " OFFSET %s ROWS FETCH NEXT %s ROWS ONLY"
        )
        count_query = f"SELECT COUNT_BIG(*) FROM {target_sql}{where_sql}"
        params = [v for _, v in active_filters]
        exec_params = (*params, offset, limit)
    else:
        conditions = [
            pgsql.SQL("{}::text ILIKE %s").format(pgsql.Identifier(c))
            for c, _ in active_filters
        ]
        where = (
            pgsql.SQL(" WHERE ") + pgsql.SQL(" AND ").join(conditions)
            if conditions
            else pgsql.SQL("")
        )
        # Ordenación: columna pedida (validada) o PK como orden estable por defecto
        if order_col:
            order = pgsql.SQL(" ORDER BY {} {}").format(
                pgsql.Identifier(order_col), pgsql.SQL("DESC" if order_dir == "desc" else "ASC")
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
        params = [v for _, v in active_filters]
        exec_params = (*params, limit, offset)

    try:
        with manager.open_profile_connection(store, profile, dbname) as conn:
            with conn.cursor() as cur:
                cur.execute(query, exec_params)
                columns = [d[0] for d in cur.description or []]
                rows = [[_jsonable(v) for v in row] for row in cur.fetchall()]
                total = None
                if with_total:
                    cur.execute(count_query, tuple(params))
                    total = cur.fetchone()[0]
        return {"ok": True, "columns": columns, "rows": rows, "total": total}
    except PasswordUnavailable:
        return password_missing_error(profile_id)
    except DB_EXCEPTIONS as exc:
        return _error(400, classify_db_error(profile.engine, exc))
    except Exception as exc:  # red de seguridad: nunca dejar caer la conexión HTTP
        return _error(500, ApiError(
            code="UNEXPECTED",
            message=f"Error inesperado al leer los datos: {exc}",
        ))


class QueryRequest(BaseModel):
    sql: str
    max_rows: int = 1000
    timeout_ms: int = 15000
    # El cliente reenvía la consulta con confirm=True tras aceptar el aviso de
    # UPDATE/DELETE sin WHERE.
    confirm: bool = False


def _sql_error(profile, exc: Exception) -> ApiError:
    """Traduce una excepción del driver al envelope de error de la API."""
    err = classify_db_error(profile.engine, exc)
    # Errores de SQL del usuario: mensaje directo del servidor.
    if isinstance(exc, (psycopg.Error, pytds.Error)) and err.code == "UNEXPECTED":
        hint = None
        if isinstance(exc, psycopg.errors.ReadOnlySqlTransaction):
            hint = ("Este perfil está en modo solo lectura. Activa «Permitir "
                    "escritura» al editar el perfil para ejecutar DDL/DML.")
        return ApiError(code="SQL_ERROR", message=str(exc).strip(), hint=hint)
    return err


def _run_one(cur, conn, statement: str, max_rows: int, writes: bool) -> dict:
    """Ejecuta una sentencia y devuelve su bloque de resultado."""
    import time
    t0 = time.perf_counter()
    cur.execute(statement)
    if cur.description is None:
        # Sin filas de salida: DML, DDL o SET. rowcount vale -1 cuando el
        # driver no sabe cuántas filas se tocaron.
        affected = cur.rowcount
        if writes:
            # pytds no confirma al cerrar y psycopg sí; ser explícito evita
            # depender del driver.
            conn.commit()
        return {
            "statement": statement,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "affected_rows": affected if affected is not None and affected >= 0 else None,
            "truncated": False,
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        }
    columns = [d[0] for d in cur.description]
    fetched = cur.fetchmany(max_rows + 1)
    truncated = len(fetched) > max_rows
    rows = [[_jsonable(v) for v in row] for row in fetched[:max_rows]]
    if writes:
        conn.commit()  # p. ej. UPDATE … RETURNING
    return {
        "statement": statement,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "affected_rows": None,
        "truncated": truncated,
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
    }


@router.post("/profiles/{profile_id}/db/{dbname}/query")
def run_query(profile_id: str, dbname: str, body: QueryRequest, request: Request):
    """Ejecuta el script del usuario, sentencia a sentencia.

    El texto se separa por `;` (respetando literales, comentarios y
    dollar-quoting) y cada sentencia produce su propio bloque de resultado, de
    modo que el cliente puede mostrar una rejilla por consulta.

    Si una sentencia falla se **para ahí**: la respuesta lleva los resultados
    de las anteriores más `error` y `error_index`. Por eso el estado HTTP es
    200 aunque haya error: la ejecución sí ocurrió, parcialmente. Los rechazos
    previos a ejecutar nada (solo lectura, confirmación, script vacío) siguen
    siendo 4xx con el envelope de siempre.

    Por defecto el perfil es de SOLO LECTURA: PostgreSQL abre una transacción
    READ ONLY y en ambos motores se rechaza toda sentencia que no sea de
    lectura — **todas**, no solo la primera, porque `SELECT 1; DELETE …` sería
    si no una vía de escape en SQL Server. Con `allow_writes` activo se admite
    DDL/DML, con un único freno: un UPDATE o DELETE sin WHERE exige
    confirmación explícita.
    """
    store = request.app.state.profiles
    profile = catalog.get_profile(store, profile_id)
    statements = split_statements(body.sql)
    if not statements:
        return _error(422, ApiError(code="VALIDATION", message="Consulta vacía."))
    max_rows = max(1, min(body.max_rows, 5000))
    timeout = max(1000, min(body.timeout_ms, 60000))
    is_mssql = profile.engine == Engine.sqlserver
    writes = profile.allow_writes
    # SQL Server no tiene transacciones READ ONLY, así que la barrera se
    # comprueba aquí para los dos motores y el mensaje es el mismo.
    if not writes:
        for st in statements:
            if not is_read_statement(st):
                return _read_only_error()
    if writes and not body.confirm:
        for st in statements:
            if missing_where(st):
                return _confirm_error(st)

    results: list[dict] = []
    failed: ApiError | None = None
    try:
        import time
        t0 = time.perf_counter()
        with manager.open_profile_connection(
            store, profile, dbname, query_timeout_ms=timeout if is_mssql else 0
        ) as conn:
            if not is_mssql and not writes:
                conn.read_only = True  # transacción de solo lectura
            with conn.cursor() as cur:
                if not is_mssql:
                    cur.execute(f"SET statement_timeout = {int(timeout)}")
                for st in statements:
                    try:
                        results.append(_run_one(cur, conn, st, max_rows, writes))
                    except DB_EXCEPTIONS as exc:
                        failed = _sql_error(profile, exc)
                        break  # se conserva lo ya ejecutado
        return {
            "ok": failed is None,
            "results": results,
            "error": failed.model_dump() if failed else None,
            "error_index": len(results) if failed else None,
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        }
    except PasswordUnavailable:
        return password_missing_error(profile_id)
    except DB_EXCEPTIONS as exc:
        # Fallo al conectar o al preparar la sesión: no se ejecutó nada.
        return _error(400, _sql_error(profile, exc))
    except MemoryError:
        # Un SELECT sobre una tabla enorme puede agotar la memoria del sidecar
        # antes de recortar a max_rows: se responde en vez de morir.
        return _error(400, ApiError(
            code="RESULT_TOO_LARGE",
            message="El resultado no cabe en memoria.",
            hint="Acota la consulta con TOP/LIMIT o WHERE antes de ejecutarla.",
        ))
    except Exception as exc:  # red de seguridad: nunca dejar caer la conexión HTTP
        return _error(500, ApiError(
            code="UNEXPECTED",
            message=f"Error inesperado al ejecutar la consulta: {exc}",
        ))


class ExplainRequest(BaseModel):
    sql: str
    # "estimated" → no ejecuta la consulta; "actual" → la ejecuta y mide.
    mode: str = "estimated"
    timeout_ms: int = 15000


@router.post("/profiles/{profile_id}/db/{dbname}/query/explain")
def explain_query(profile_id: str, dbname: str, body: ExplainRequest, request: Request):
    """Plan de ejecución de una consulta, estimado o real.

    PostgreSQL: ``EXPLAIN`` (estimado) / ``EXPLAIN (ANALYZE, BUFFERS)`` (real).
    SQL Server: ``SET SHOWPLAN_ALL ON`` (estimado: compila pero no ejecuta) /
    ``SET STATISTICS PROFILE ON`` (real). Se aplican las mismas restricciones
    de escritura que en el editor.

    Pedir el plan nunca debe alterar datos: si la sentencia es de escritura
    (permitida por `allow_writes`), el plan se calcula dentro de una
    transacción que SIEMPRE termina en rollback. Es importante porque
    ``EXPLAIN ANALYZE UPDATE …`` ejecuta el UPDATE de verdad.
    """
    store = request.app.state.profiles
    profile = catalog.get_profile(store, profile_id)
    # Un plan describe UNA sentencia: si el script trae varias se explica la
    # primera. El editor manda solo la selección cuando la hay.
    statements = split_statements(body.sql)
    if not statements:
        return _error(422, ApiError(code="VALIDATION", message="Consulta vacía."))
    sql_text = statements[0]
    mode = body.mode if body.mode in ("estimated", "actual") else "estimated"
    timeout = max(1000, min(body.timeout_ms, 60000))
    is_mssql = profile.engine == Engine.sqlserver
    is_read = is_read_statement(sql_text)
    if not profile.allow_writes and not is_read:
        return _read_only_error()
    try:
        import time
        t0 = time.perf_counter()
        with manager.open_profile_connection(
            store, profile, dbname, query_timeout_ms=timeout if is_mssql else 0
        ) as conn:
            if is_mssql:
                nodes = _mssql_plan(conn, sql_text, mode)
            else:
                # Una escritura no cabe en una transacción READ ONLY ni
                # siquiera para planificarla, así que va en una normal.
                conn.read_only = is_read
                nodes = _postgres_plan(conn, sql_text, mode, timeout)
            if not is_read:
                conn.rollback()  # el plan nunca deja cambios en la base
        elapsed = int((time.perf_counter() - t0) * 1000)
        return {
            "ok": True,
            "engine": "sqlserver" if is_mssql else "postgresql",
            "mode": mode,
            "nodes": nodes,
            "plan_text": explain_plan.nodes_to_text(nodes),
            "elapsed_ms": elapsed,
        }
    except PasswordUnavailable:
        return password_missing_error(profile_id)
    except PlanTooLarge as exc:
        return _error(400, ApiError(
            code="PLAN_TOO_LARGE",
            message=(
                "La consulta devuelve demasiadas filas para medir el plan real "
                f"(más de {exc.args[0]:,} filas)."
            ),
            hint=(
                "El plan real obliga a ejecutar la consulta entera. Usa el plan "
                "estimado, o acota la consulta con TOP / WHERE antes de medirla."
            ),
        ))
    except DB_EXCEPTIONS as exc:
        err = classify_db_error(profile.engine, exc)
        if isinstance(exc, (psycopg.Error, pytds.Error)) and err.code == "UNEXPECTED":
            return _error(400, ApiError(code="SQL_ERROR", message=str(exc).strip()))
        return _error(400, err)
    except Exception as exc:  # red de seguridad: nunca dejar caer la conexión HTTP
        return _error(500, ApiError(
            code="UNEXPECTED",
            message=f"Error inesperado al calcular el plan: {exc}",
            hint="Reintenta con el plan estimado; si persiste, revisa el log del sidecar.",
        ))


_postgres_plan = postgres_plan
_mssql_plan = mssql_plan


class RelationshipsRequest(BaseModel):
    tables: list[str]  # ["schema.tabla", ...]


@router.post("/profiles/{profile_id}/db/{dbname}/relationships")
def relationships(profile_id: str, dbname: str, body: RelationshipsRequest, request: Request):
    store, cache = _stores(request)
    snapshot = catalog.get_snapshot(store, cache, profile_id, dbname)
    edges = catalog.relationships_between(snapshot, body.tables)
    return {"ok": True, "relationships": [r.model_dump() for r in edges]}


@router.post("/profiles/{profile_id}/db/{dbname}/query/build")
def build_query(profile_id: str, dbname: str, body: QuerySpec, request: Request):
    """Traduce el diagrama del constructor (tablas + joins) a SQL.

    Valida que cada tabla y columna exista en el snapshot: así el SQL generado
    solo referencia objetos reales (coherente con la ejecución de solo lectura).
    """
    store, cache = _stores(request)
    snapshot = catalog.get_snapshot(store, cache, profile_id, dbname)
    if not body.tables:
        return _error(422, ApiError(
            code="VALIDATION",
            message="Se requiere al menos una tabla para construir la consulta.",
            hint="Arrastra tablas al lienzo antes de pulsar «Listo».",
        ))
    for key in body.tables:
        if snapshot.tables.get(key) is None:
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
    profile = store.get(profile_id)
    dialect = (
        "sqlserver"
        if getattr(profile, "engine", None) == Engine.sqlserver
        else "postgresql"
    )
    try:
        sql_text = query_builder.build_query_sql(model, dialect=dialect)
    except ValueError as exc:
        return _error(422, ApiError(code="VALIDATION", message=str(exc)))
    return {"ok": True, "sql": sql_text}


class ParseQueryRequest(BaseModel):
    sql: str


@router.post("/profiles/{profile_id}/db/{dbname}/query/parse")
def parse_query(profile_id: str, dbname: str, body: ParseQueryRequest, request: Request):
    """Analiza una sentencia SQL y devuelve el diagrama equivalente."""
    store, cache = _stores(request)
    snapshot = catalog.get_snapshot(store, cache, profile_id, dbname)
    if not body.sql.strip():
        return _error(422, ApiError(
            code="VALIDATION",
            message="No hay SQL que analizar.",
            hint="Escribe una consulta en el editor antes de pulsar «Diagrama».",
        ))
    model = query_builder.parse_query_sql(body.sql, catalog.known_names(snapshot))
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
    store, cache = _stores(request)
    snapshot = catalog.get_snapshot(store, cache, profile_id, dbname)
    content, extension = catalog.export_model(snapshot, body.tables, body.format)
    return {"ok": True, "content": content, "extension": extension}
