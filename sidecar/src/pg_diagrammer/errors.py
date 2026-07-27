"""Envelope de errores uniforme y clasificación de excepciones de los drivers.

Toda respuesta de error de la API tiene la forma:
    {"code": str, "message": str, "hint": str | None, "retriable": bool}
Los códigos son los mismos para PostgreSQL (psycopg) y SQL Server (pytds):
AUTH_FAILED, TIMEOUT, HOST_NOT_FOUND, NETWORK_UNREACHABLE, SSL_ERROR,
DATABASE_NOT_FOUND, PERMISSION_DENIED, CONNECTION_ERROR, UNEXPECTED.
"""
from __future__ import annotations

from pydantic import BaseModel

import psycopg
import pytds

from pg_diagrammer.domain.models import Engine

# Tupla única para los `except` de las rutas: cubre ambos motores.
DB_EXCEPTIONS = (psycopg.Error, pytds.Error, OSError)


class ApiError(BaseModel):
    code: str
    message: str
    hint: str | None = None
    retriable: bool = False


def classify_pg_error(exc: Exception) -> ApiError:
    """Traduce excepciones de conexión/consulta a errores accionables."""
    text = str(exc).lower()

    if isinstance(exc, psycopg.OperationalError):
        if "password authentication failed" in text or "authentication" in text:
            return ApiError(
                code="AUTH_FAILED",
                message="Autenticación rechazada por el servidor.",
                hint="Verifica usuario y contraseña. Si usas SCRAM, confirma que el rol existe.",
            )
        if "timeout" in text or "timed out" in text:
            return ApiError(
                code="TIMEOUT",
                message="La conexión superó el tiempo de espera.",
                hint="Comprueba red/VPN y que el puerto sea accesible desde esta máquina.",
                retriable=True,
            )
        if (
            "could not translate host name" in text
            or "name or service not known" in text
            or "failed to resolve host" in text
            or "name resolution" in text
        ):
            return ApiError(
                code="HOST_NOT_FOUND",
                message="No se pudo resolver el nombre del host.",
                hint="Revisa el valor de host (¿typo?, ¿DNS interno que requiere VPN?).",
            )
        if "connection refused" in text or "unreachable" in text or ("failed" in text and "connect" in text):
            # Incluimos el motivo real de psycopg para no ocultar la causa
            # (timeout, reset, refused, VPN caída, pgbouncer, etc.).
            reason = str(exc).strip().splitlines()[0]
            return ApiError(
                code="NETWORK_UNREACHABLE",
                message=f"No se pudo conectar al servidor: {reason}",
                hint="Verifica host/puerto, VPN/firewall y que el servidor (o pgbouncer) esté accesible desde esta máquina.",
                retriable=True,
            )
        if "ssl" in text:
            return ApiError(
                code="SSL_ERROR",
                message="Fallo en la negociación SSL.",
                hint="Revisa sslmode y el certificado CA configurado.",
            )
        if "does not exist" in text and "database" in text:
            return ApiError(
                code="DATABASE_NOT_FOUND",
                message="La base de datos indicada no existe.",
                hint="Lista las bases disponibles y elige una válida.",
            )
        return ApiError(
            code="CONNECTION_ERROR",
            message=f"Error de conexión: {exc}",
            retriable=True,
        )

    if isinstance(exc, psycopg.errors.InsufficientPrivilege):
        return ApiError(
            code="PERMISSION_DENIED",
            message="El rol no tiene permisos sobre el objeto consultado.",
            hint="Solicita GRANT USAGE/SELECT sobre el schema u objeto afectado.",
        )

    return ApiError(code="UNEXPECTED", message=f"Error inesperado: {exc}")


def classify_mssql_error(exc: Exception) -> ApiError:
    """Traduce excepciones de pytds/red a los mismos códigos que PostgreSQL."""
    text = str(exc).lower()

    if isinstance(exc, pytds.LoginError) or "login failed" in text:
        if "cannot open database" in text:
            return ApiError(
                code="DATABASE_NOT_FOUND",
                message="La base de datos indicada no existe o no es accesible.",
                hint="Lista las bases disponibles y elige una válida.",
            )
        return ApiError(
            code="AUTH_FAILED",
            message="Autenticación rechazada por el servidor.",
            hint="Verifica usuario y contraseña. Con auth de Windows, confirma dominio y que el login exista en SQL Server.",
        )
    if isinstance(exc, (TimeoutError, pytds.TimeoutError)) or "timed out" in text or "timeout" in text:
        return ApiError(
            code="TIMEOUT",
            message="La conexión superó el tiempo de espera.",
            hint="Comprueba red/VPN y que el puerto (1433 por defecto) sea accesible.",
            retriable=True,
        )
    if (
        "getaddrinfo" in text
        or "name or service not known" in text
        or "name resolution" in text
        or "nodename nor servname" in text
    ):
        return ApiError(
            code="HOST_NOT_FOUND",
            message="No se pudo resolver el nombre del host.",
            hint="Revisa el valor de host (¿typo?, ¿DNS interno que requiere VPN?).",
        )
    if "refused" in text or "unreachable" in text or isinstance(exc, ConnectionError):
        reason = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
        return ApiError(
            code="NETWORK_UNREACHABLE",
            message=f"No se pudo conectar al servidor: {reason}",
            hint="Verifica host/puerto, que TCP/IP esté habilitado en SQL Server y el firewall.",
            retriable=True,
        )
    if "ssl" in text or "tls" in text or "certificate" in text:
        return ApiError(
            code="SSL_ERROR",
            message="Fallo en la negociación TLS.",
            hint="Revisa el certificado del servidor y la configuración de cifrado.",
        )
    if "permission" in text or "denied" in text:
        return ApiError(
            code="PERMISSION_DENIED",
            message="El login no tiene permisos sobre el objeto consultado.",
            hint="Solicita GRANT VIEW DEFINITION/SELECT sobre el objeto afectado.",
        )
    if isinstance(exc, (pytds.OperationalError, pytds.InterfaceError, OSError)):
        return ApiError(
            code="CONNECTION_ERROR",
            message=f"Error de conexión: {exc}",
            retriable=True,
        )
    return ApiError(code="UNEXPECTED", message=f"Error inesperado: {exc}")


def classify_db_error(engine: Engine, exc: Exception) -> ApiError:
    """Punto único de clasificación: elige el clasificador según el motor."""
    if engine == Engine.sqlserver:
        return classify_mssql_error(exc)
    return classify_pg_error(exc)
