"""CRUD de perfiles de conexión y operaciones sobre sus bases de datos."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from pg_diagrammer.domain.models import ProfileCreate
from pg_diagrammer.errors import ApiError
from pg_diagrammer.services import catalog

router = APIRouter(tags=["profiles"])


def _error(status: int, err: ApiError) -> JSONResponse:
    return JSONResponse(status_code=status, content={"ok": False, **err.model_dump()})


def password_missing_error(profile_id: str) -> JSONResponse:
    return _error(
        409,
        ApiError(
            code="PASSWORD_REQUIRED",
            message="No hay contraseña almacenada para este perfil en esta sesión.",
            hint="Sin keychain del SO la contraseña no persiste: reingresala (POST /profiles/{id}/password).",
        ),
    )


@router.post("/profiles", status_code=201)
def create_profile(data: ProfileCreate, request: Request):
    store = request.app.state.profiles
    profile = store.create(data)
    return {"ok": True, "profile": profile.model_dump(), "keychain": store.keyring_available}


@router.put("/profiles/{profile_id}")
def update_profile(profile_id: str, data: ProfileCreate, request: Request):
    store = request.app.state.profiles
    profile = store.update(profile_id, data)
    if profile is None:
        return _error(404, ApiError(code="NOT_FOUND", message="Perfil inexistente."))
    request.app.state.snapshots.invalidate(profile_id)
    return {"ok": True, "profile": profile.model_dump(), "keychain": store.keyring_available}


@router.get("/profiles")
def list_profiles(request: Request):
    store = request.app.state.profiles
    return {
        "ok": True,
        "profiles": [p.model_dump() for p in store.list()],
        "keychain": store.keyring_available,
    }


@router.delete("/profiles/{profile_id}")
def delete_profile(profile_id: str, request: Request):
    store = request.app.state.profiles
    if not store.delete(profile_id):
        return _error(404, ApiError(code="NOT_FOUND", message="Perfil inexistente."))
    request.app.state.snapshots.invalidate(profile_id)
    return {"ok": True}


@router.post("/profiles/{profile_id}/password")
def set_session_password(profile_id: str, body: dict, request: Request):
    """Reingreso de contraseña cuando no hay keychain disponible."""
    store = request.app.state.profiles
    if store.get(profile_id) is None:
        return _error(404, ApiError(code="NOT_FOUND", message="Perfil inexistente."))
    password = body.get("password", "")
    if not password:
        return _error(422, ApiError(code="VALIDATION", message="Falta el campo password."))
    store.set_session_password(profile_id, password)
    return {"ok": True}


@router.get("/profiles/{profile_id}/databases")
def list_databases(profile_id: str, request: Request):
    store = request.app.state.profiles
    return {"ok": True, "databases": catalog.list_databases(store, profile_id)}
