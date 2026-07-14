"""Tests de clasificación de errores psycopg → ApiError."""
import psycopg

from pg_diagrammer.errors import classify_pg_error


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
