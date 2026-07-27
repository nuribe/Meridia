"""Conexiones a SQL Server vía python-tds (puro Python, sin ODBC).

Espejo de manager.py para el motor sqlserver. Soporta:
- SQL auth (usuario/contraseña, keychain — igual que PostgreSQL).
- Windows integrada: SSPI (usuario actual, solo Windows, sin contraseña)
  o NTLM explícito (DOMINIO\\usuario + contraseña) desde cualquier SO.
"""
from __future__ import annotations

import sys

import pytds
import pytds.login

from pg_diagrammer.domain.models import AuthMethod, ConnectionParams

# Bases de sistema que no tiene sentido diagramar.
SYSTEM_DATABASES = ("master", "tempdb", "model", "msdb")

LIST_DATABASES_SQL = """
    SELECT d.name,
           ISNULL(SUSER_SNAME(d.owner_sid), '') AS owner,
           ISNULL(d.collation_name, '') AS collation
    FROM sys.databases d
    WHERE d.state = 0
      AND HAS_DBACCESS(d.name) = 1
      AND d.name NOT IN ('master', 'tempdb', 'model', 'msdb')
    ORDER BY d.name
"""


def _auth_kwargs(user: str, password: str, auth_method: AuthMethod) -> dict:
    """Argumentos de autenticación para pytds.connect según el método."""
    if auth_method == AuthMethod.windows:
        if not password and sys.platform == "win32":
            # Usuario actual de Windows, sin contraseña (SSPI).
            return {"auth": pytds.login.SspiAuth()}
        # NTLM explícito: user "DOMINIO\\usuario" + contraseña.
        return {"auth": pytds.login.NtlmAuth(user_name=user, password=password)}
    return {"user": user, "password": password}


def connect(
    host: str,
    port: int,
    user: str,
    password: str,
    dbname: str,
    auth_method: AuthMethod = AuthMethod.sql,
    connect_timeout: int = 8,
    query_timeout: int = 0,
):
    """Abre una conexión DB-API a SQL Server (autocommit, uso de solo lectura)."""
    return pytds.connect(
        dsn=host,
        port=port,
        database=dbname,
        login_timeout=connect_timeout,
        timeout=query_timeout or None,
        autocommit=True,
        **_auth_kwargs(user, password, auth_method),
    )


def connect_params(params: ConnectionParams, query_timeout: int = 0):
    return connect(
        host=params.host,
        port=params.port,
        user=params.user,
        password=params.password,
        dbname=params.dbname,
        auth_method=params.auth_method,
        connect_timeout=params.connect_timeout,
        query_timeout=query_timeout,
    )


def test_connection(params: ConnectionParams) -> dict:
    """Abre una conexión efímera y devuelve datos básicos del servidor."""
    with connect_params(params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT @@VERSION, DB_NAME(), SUSER_SNAME()")
            version, dbname, user = cur.fetchone()
    return {"server_version": version, "database": dbname, "user": user}


def list_databases_conn(conn) -> list[dict]:
    """Bases de usuario accesibles y en línea (excluye las de sistema)."""
    with conn.cursor() as cur:
        cur.execute(LIST_DATABASES_SQL)
        rows = cur.fetchall()
    return [{"name": r[0], "owner": r[1], "encoding": r[2]} for r in rows]


def list_databases(params: ConnectionParams) -> list[dict]:
    with connect_params(params) as conn:
        return list_databases_conn(conn)
