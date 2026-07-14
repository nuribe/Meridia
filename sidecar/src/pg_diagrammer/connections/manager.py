"""Gestión de conexiones a PostgreSQL.

Fase 1: conexiones efímeras por operación. Fase 4 evaluará pools
persistentes (psycopg_pool) si el perfil de uso lo justifica.
"""
from __future__ import annotations

import psycopg

from pg_diagrammer.domain.models import ConnectionParams

LIST_DATABASES_SQL = """
    SELECT datname,
           pg_catalog.pg_get_userbyid(datdba) AS owner,
           pg_catalog.pg_encoding_to_char(encoding) AS encoding
    FROM pg_catalog.pg_database
    WHERE NOT datistemplate
      AND has_database_privilege(datname, 'CONNECT')
    ORDER BY datname
"""


def build_conninfo(params: ConnectionParams) -> str:
    return psycopg.conninfo.make_conninfo(
        host=params.host,
        port=params.port,
        user=params.user,
        password=params.password,
        dbname=params.dbname,
        sslmode=params.ssl_mode.value,
        connect_timeout=params.connect_timeout,
    )


def test_connection(params: ConnectionParams) -> dict:
    """Abre una conexión efímera y devuelve datos básicos del servidor."""
    with psycopg.connect(build_conninfo(params)) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version(), current_database(), current_user")
            version, dbname, user = cur.fetchone()
    return {"server_version": version, "database": dbname, "user": user}


def list_databases_conninfo(conninfo: str) -> list[dict]:
    """Bases de datos no-template a las que el rol puede conectarse."""
    with psycopg.connect(conninfo) as conn:
        with conn.cursor() as cur:
            cur.execute(LIST_DATABASES_SQL)
            rows = cur.fetchall()
    return [{"name": r[0], "owner": r[1], "encoding": r[2]} for r in rows]


def list_databases(params: ConnectionParams) -> list[dict]:
    return list_databases_conninfo(build_conninfo(params))
