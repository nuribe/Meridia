"""Gestión de conexiones (PostgreSQL vía psycopg, SQL Server vía pytds).

Fase 1: conexiones efímeras por operación. Fase 4 evaluará pools
persistentes (psycopg_pool) si el perfil de uso lo justifica.

Las funciones públicas despachan según `engine`; el detalle de SQL Server
vive en mssql.py para que este módulo siga siendo legible.
"""
from __future__ import annotations

import sys

import psycopg

from pg_diagrammer.connections import mssql
from pg_diagrammer.domain.models import AuthMethod, ConnectionParams, ConnectionProfile, Engine

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
    if params.engine == Engine.sqlserver:
        return mssql.test_connection(params)
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
    if params.engine == Engine.sqlserver:
        return mssql.list_databases(params)
    return list_databases_conninfo(build_conninfo(params))


def open_profile_connection(store, profile: ConnectionProfile, dbname: str, query_timeout_ms: int = 0):
    """Conexión DB-API para un perfil, del motor que corresponda.

    Único punto por el que las rutas deben abrir conexiones de perfil.
    Lanza PasswordUnavailable si falta la credencial necesaria.
    """
    from pg_diagrammer.connections.profiles import PasswordUnavailable

    if profile.engine == Engine.sqlserver:
        password = store.get_password(profile.id) or ""
        if not password and not (
            profile.auth_method == AuthMethod.windows and sys.platform == "win32"
        ):
            # SQL auth (o NTLM fuera de Windows) sin contraseña: no se puede.
            raise PasswordUnavailable(profile.id)
        return mssql.connect(
            host=profile.host,
            port=profile.port,
            user=profile.user,
            password=password,
            dbname=dbname,
            auth_method=profile.auth_method,
            query_timeout=max(1, query_timeout_ms // 1000) if query_timeout_ms else 0,
        )
    return psycopg.connect(store.conninfo(profile, dbname))
