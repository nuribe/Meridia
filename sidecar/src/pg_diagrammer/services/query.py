"""Consulta de solo lectura y planes de ejecución, para el adaptador MCP.

Deliberadamente más estrecho que el editor de consultas de la app:

- **Solo `SELECT` y `WITH`.** El editor acepta además `SHOW`, `TABLE`,
  `VALUES`… y, con `allow_writes`, DDL/DML. Aquí nada de eso llega: la lista
  de palabras permitidas es `MCP_KEYWORDS` y `allow_writes` se ignora.
- **Un solo statement.** Un script de varias sentencias es una vía de escape
  (`SELECT 1; DELETE …`) que en SQL Server no ataja la transacción READ ONLY.
- **Topes propios**: filas, timeout y tamaño de celda.

Los parsers de plan (`domain/explain.py`) y las mecánicas de conexión se
comparten con las rutas HTTP; lo que no se comparte son las políticas.
"""
from __future__ import annotations

import time

import psycopg
import pytds

from pg_diagrammer.connections import manager
from pg_diagrammer.connections.profiles import PasswordUnavailable, ProfileStore
from pg_diagrammer.domain import explain as explain_plan
from pg_diagrammer.domain.models import Engine
from pg_diagrammer.domain.sql_script import (
    MCP_KEYWORDS,
    first_keyword,
    is_read_statement,
    split_statements,
)
from pg_diagrammer.errors import ApiError, DB_EXCEPTIONS, classify_db_error
from pg_diagrammer.services.catalog import get_profile
from pg_diagrammer.services.errors import (
    DatabaseUnavailable,
    InvalidRequest,
    PasswordRequired,
    ServiceError,
)
from pg_diagrammer.services.values import jsonable

MAX_ROWS = 1000
MAX_TIMEOUT_MS = 15_000

# Con STATISTICS PROFILE el servidor devuelve TODAS las filas de la consulta
# antes del plan. No se materializan (se leen y descartan por lotes), pero sí
# hay que acotar cuánto se lee: por encima de este tope se aborta y se sugiere
# el plan estimado.
MSSQL_PLAN_DISCARD_CHUNK = 1000
MSSQL_PLAN_MAX_DISCARDED_ROWS = 200_000
# Tope de nodos del plan (los planes patológicos pueden tener miles).
MSSQL_PLAN_MAX_NODES = 5_000


class PlanTooLarge(Exception):
    """El conjunto de resultados es demasiado grande para medir el plan real."""


class SqlError(ServiceError):
    """Error de SQL del usuario: el mensaje del servidor, sin adornos."""

    code = "SQL_ERROR"
    status = 400


def _sole_statement(sql: str, what: str) -> str:
    """Valida el script y devuelve LA sentencia, ya comprobada de lectura."""
    statements = split_statements(sql)
    if not statements:
        raise InvalidRequest("Consulta vacía.")
    if len(statements) > 1:
        raise InvalidRequest(
            f"{what} admite una sola sentencia; llegaron {len(statements)}.",
            "Envía las consultas de una en una.",
        )
    stmt = statements[0]
    if not is_read_statement(stmt, MCP_KEYWORDS):
        raise InvalidRequest(
            f"Solo se admiten SELECT y WITH; esta sentencia empieza por "
            f"{first_keyword(stmt) or '(vacío)'}.",
            "El acceso MCP es de solo lectura y no hereda «Permitir escritura» "
            "del perfil.",
        )
    return stmt


def _db_error(engine: Engine, exc: Exception) -> ServiceError:
    """Traduce una excepción del driver a una excepción de servicio."""
    err: ApiError = classify_db_error(engine, exc)
    if isinstance(exc, (psycopg.Error, pytds.Error)) and err.code == "UNEXPECTED":
        return SqlError(str(exc).strip())
    return DatabaseUnavailable(err)


def run_select(
    store: ProfileStore,
    profile_id: str,
    dbname: str,
    sql: str,
    *,
    max_rows: int = MAX_ROWS,
    timeout_ms: int = MAX_TIMEOUT_MS,
) -> dict:
    """Ejecuta un `SELECT`/`WITH` y devuelve columnas y filas acotadas."""
    profile = get_profile(store, profile_id)
    stmt = _sole_statement(sql, "run_select")
    rows_cap = max(1, min(max_rows, MAX_ROWS))
    timeout = max(1000, min(timeout_ms, MAX_TIMEOUT_MS))
    is_mssql = profile.engine == Engine.sqlserver
    started = time.perf_counter()
    try:
        with manager.open_profile_connection(
            store, profile, dbname, query_timeout_ms=timeout if is_mssql else 0
        ) as conn:
            if not is_mssql:
                # Segunda barrera, además de la lista de palabras: aunque algo
                # se colara, el motor lo rechaza.
                conn.read_only = True
            with conn.cursor() as cur:
                if not is_mssql:
                    cur.execute(f"SET statement_timeout = {int(timeout)}")
                cur.execute(stmt)
                columns = [d[0] for d in cur.description or []]
                fetched = cur.fetchmany(rows_cap + 1)
                truncated = len(fetched) > rows_cap
                rows = [[jsonable(v) for v in row] for row in fetched[:rows_cap]]
    except PasswordUnavailable:
        raise PasswordRequired(profile_id) from None
    except MemoryError:
        raise DatabaseUnavailable(
            ApiError(
                code="RESULT_TOO_LARGE",
                message="El resultado no cabe en memoria.",
                hint="Acota la consulta con WHERE o con menos columnas.",
            )
        ) from None
    except DB_EXCEPTIONS as exc:
        raise _db_error(profile.engine, exc) from exc
    return {
        "sql": stmt,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    }


def postgres_plan(conn, sql_text: str, mode: str, timeout: int) -> list[dict]:
    options = (
        "ANALYZE, BUFFERS, COSTS, TIMING, FORMAT TEXT"
        if mode == "actual"
        else "COSTS, FORMAT TEXT"
    )
    with conn.cursor() as cur:
        cur.execute(f"SET statement_timeout = {int(timeout)}")
        cur.execute(f"EXPLAIN ({options}) {sql_text}")
        lines = [r[0] for r in cur.fetchall()]
    return explain_plan.parse_postgres_plan(lines)


def mssql_plan(conn, sql_text: str, mode: str) -> list[dict]:
    """Recoge el rowset del plan tras ejecutar la consulta con SET ... ON.

    Con SHOWPLAN_ALL la sentencia se compila pero NO se ejecuta: el único
    rowset es el plan. Con STATISTICS PROFILE la consulta sí se ejecuta y el
    plan llega DESPUÉS de sus filas, así que hay que recorrer los rowsets.

    Dos reglas que no se pueden relajar:
    - Las filas de datos jamás se materializan enteras (`fetchall` sobre una
      tabla grande revienta la memoria del sidecar); se leen por lotes y se
      descartan, con un tope duro de filas.
    - No se envía ninguna sentencia más por esta conexión mientras queden
      resultados pendientes: hacerlo desincroniza el protocolo TDS y produce
      un "Invalid TDS marker". La conexión es efímera y se cierra al salir,
      así que no hace falta un `SET ... OFF` de limpieza.
    """
    setting = "STATISTICS PROFILE" if mode == "actual" else "SHOWPLAN_ALL"
    columns: list[str] = []
    rows: list[tuple] = []
    with conn.cursor() as cur:
        cur.execute(f"SET {setting} ON")
        cur.execute(sql_text)
        discarded = 0
        while True:
            names = [d[0] for d in cur.description or []]
            if names and any(n.lower() == "stmttext" for n in names):
                columns = names
                rows = list(cur.fetchmany(MSSQL_PLAN_MAX_NODES))
            elif names:
                # Filas de la consulta: se leen por lotes y se tiran.
                while True:
                    chunk = cur.fetchmany(MSSQL_PLAN_DISCARD_CHUNK)
                    if not chunk:
                        break
                    discarded += len(chunk)
                    if discarded > MSSQL_PLAN_MAX_DISCARDED_ROWS:
                        raise PlanTooLarge(discarded)
            if not cur.nextset():
                break
    return explain_plan.parse_mssql_plan(columns, rows)


def explain(
    store: ProfileStore,
    profile_id: str,
    dbname: str,
    sql: str,
    *,
    mode: str = "estimated",
    timeout_ms: int = MAX_TIMEOUT_MS,
) -> dict:
    """Plan de ejecución de un `SELECT`/`WITH`, estimado o real.

    El estimado no ejecuta nada: PostgreSQL solo planifica y `SHOWPLAN_ALL`
    compila. El real sí ejecuta la consulta, y por eso el llamante debe
    exigirle al usuario el permiso de consulta antes de pedirlo. Aun así la
    transacción termina en rollback, como en la app.
    """
    if mode not in ("estimated", "actual"):
        raise InvalidRequest(f"mode inválido: {mode}", "Valores permitidos: estimated, actual.")
    profile = get_profile(store, profile_id)
    stmt = _sole_statement(sql, "explain_query")
    timeout = max(1000, min(timeout_ms, MAX_TIMEOUT_MS))
    is_mssql = profile.engine == Engine.sqlserver
    started = time.perf_counter()
    try:
        with manager.open_profile_connection(
            store, profile, dbname, query_timeout_ms=timeout if is_mssql else 0
        ) as conn:
            if is_mssql:
                nodes = mssql_plan(conn, stmt, mode)
            else:
                conn.read_only = True
                nodes = postgres_plan(conn, stmt, mode, timeout)
            # El plan nunca deja rastro: aunque la sentencia sea de lectura, el
            # modo real la ejecutó y la transacción se deshace igual.
            conn.rollback()
    except PasswordUnavailable:
        raise PasswordRequired(profile_id) from None
    except PlanTooLarge as exc:
        raise DatabaseUnavailable(
            ApiError(
                code="PLAN_TOO_LARGE",
                message=(
                    "La consulta devuelve demasiadas filas para medir el plan "
                    f"real (más de {exc.args[0]:,} filas)."
                ),
                hint="Usa el plan estimado, o acota la consulta antes de medirla.",
            )
        ) from None
    except DB_EXCEPTIONS as exc:
        raise _db_error(profile.engine, exc) from exc
    return {
        "sql": stmt,
        "engine": "sqlserver" if is_mssql else "postgresql",
        "mode": mode,
        "nodes": nodes,
        "plan_text": explain_plan.nodes_to_text(nodes),
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    }
