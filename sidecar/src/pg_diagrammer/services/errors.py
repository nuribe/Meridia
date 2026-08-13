"""Excepciones de la capa de servicios.

Cada una lleva ya el envelope `{code, message, hint, retriable}` de la API
—que es el único formato de error del proyecto— más el estado HTTP que le
corresponde. El adaptador REST lo serializa tal cual; el adaptador MCP usa el
mismo envelope dentro del resultado de la tool. Así el usuario ve el mismo
código de error por los dos caminos.
"""
from __future__ import annotations

from pg_diagrammer.errors import ApiError


class ServiceError(Exception):
    """Fallo de negocio ya clasificado.

    `status` es el código HTTP que usa el adaptador REST; no implica que la
    capa de servicios sepa de HTTP, es solo la severidad expresada en el
    vocabulario que ya usaba el proyecto.
    """

    code = "UNEXPECTED"
    status = 500

    def __init__(
        self,
        message: str,
        hint: str | None = None,
        *,
        code: str | None = None,
        status: int | None = None,
        retriable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.retriable = retriable
        if code is not None:
            self.code = code
        if status is not None:
            self.status = status

    def to_api_error(self) -> ApiError:
        return ApiError(
            code=self.code,
            message=self.message,
            hint=self.hint,
            retriable=self.retriable,
        )


class ProfileNotFound(ServiceError):
    """El perfil de conexión no existe."""

    code = "NOT_FOUND"
    status = 404

    def __init__(self, profile_id: str) -> None:
        super().__init__("Perfil inexistente.")
        self.profile_id = profile_id


class ObjectNotFound(ServiceError):
    """La tabla, vista o rutina no está en el snapshot."""

    code = "NOT_FOUND"
    status = 404


class InvalidRequest(ServiceError):
    """Los parámetros no son válidos (equivale al 422 de la API)."""

    code = "VALIDATION"
    status = 422


class PasswordRequired(ServiceError):
    """No hay contraseña para el perfil en esta sesión.

    Reproduce literalmente el error que las rutas ya devolvían, para que el
    contrato REST no cambie con el refactor.
    """

    code = "PASSWORD_REQUIRED"
    status = 409

    def __init__(self, profile_id: str) -> None:
        super().__init__(
            "No hay contraseña almacenada para este perfil en esta sesión.",
            "Sin keychain del SO la contraseña no persiste: reingresala "
            "(POST /profiles/{id}/password).",
        )
        self.profile_id = profile_id


class DatabaseUnavailable(ServiceError):
    """Fallo del motor ya clasificado por `errors.classify_db_error`.

    La clasificación (AUTH_FAILED, NETWORK_UNREACHABLE, TIMEOUT, SSL_ERROR…)
    sigue siendo responsabilidad de `errors.py`: aquí solo se transporta, para
    que la capa de servicios no obligue a cada adaptador a conocer el motor.
    """

    status = 400

    def __init__(self, api_error: ApiError, status: int = 400) -> None:
        super().__init__(
            api_error.message,
            api_error.hint,
            code=api_error.code,
            status=status,
            retriable=api_error.retriable,
        )
        self.api_error = api_error

    def to_api_error(self) -> ApiError:
        return self.api_error
