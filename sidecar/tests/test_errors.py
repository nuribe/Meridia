"""Tests de clasificación de errores psycopg/pytds → ApiError."""
import psycopg
import pytds

from pg_diagrammer.domain.models import Engine
from pg_diagrammer.errors import classify_db_error, classify_mssql_error, classify_pg_error


def test_auth_failed():
    err = classify_pg_error(
        psycopg.OperationalError('password authentication failed for user "x"')
    )
    assert err.code == "AUTH_FAILED"
    assert err.retriable is False
    assert err.hint


def test_timeout_es_retriable():
    err = classify_pg_error(psycopg.OperationalError("connection timed out"))
    assert err.code == "TIMEOUT"
    assert err.retriable is True


def test_host_not_found():
    err = classify_pg_error(
        psycopg.OperationalError('could not translate host name "nope" to address')
    )
    assert err.code == "HOST_NOT_FOUND"


def test_connection_refused():
    err = classify_pg_error(psycopg.OperationalError("connection refused"))
    assert err.code == "NETWORK_UNREACHABLE"


def test_ssl():
    err = classify_pg_error(psycopg.OperationalError("SSL error: handshake failure"))
    assert err.code == "SSL_ERROR"


def test_desconocido():
    err = classify_pg_error(ValueError("algo raro"))
    assert err.code == "UNEXPECTED"


# --- SQL Server (pytds) → mismos códigos de envelope ---


def test_mssql_login_failed():
    err = classify_mssql_error(pytds.LoginError("Login failed for user 'sa'."))
    assert err.code == "AUTH_FAILED"
    assert err.hint


def test_mssql_database_not_found():
    err = classify_mssql_error(
        pytds.LoginError("Cannot open database \"nope\" requested by the login.")
    )
    assert err.code == "DATABASE_NOT_FOUND"


def test_mssql_timeout_es_retriable():
    err = classify_mssql_error(TimeoutError("timed out"))
    assert err.code == "TIMEOUT"
    assert err.retriable is True


def test_mssql_host_not_found():
    err = classify_mssql_error(OSError("[Errno -2] Name or service not known"))
    assert err.code == "HOST_NOT_FOUND"


def test_mssql_connection_refused():
    err = classify_mssql_error(ConnectionRefusedError("Connection refused"))
    assert err.code == "NETWORK_UNREACHABLE"
    assert err.retriable is True


def test_dispatcher_por_motor():
    exc = TimeoutError("timed out")
    assert classify_db_error(Engine.sqlserver, exc).code == "TIMEOUT"
    assert classify_db_error(Engine.postgresql, ValueError("x")).code == "UNEXPECTED"
