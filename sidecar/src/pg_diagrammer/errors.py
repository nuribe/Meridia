"""Envelope de errores uniforme y clasificación de excepciones de psycopg.

Toda respuesta de error de la API tiene la forma:
    {"code": str, "message": str, "hint": str | None, "retriable": bool}
"""
from __future__ import annotations

from pydantic import BaseModel

import psycopg


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
