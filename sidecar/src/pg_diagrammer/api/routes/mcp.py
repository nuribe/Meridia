"""Rutas de la vista «Actividad IA»: bitácora y control del acceso MCP.

Son solo lectura de la bitácora y lectura/escritura de los ajustes. El sidecar
no lanza ni supervisa procesos MCP —los lanza el cliente— así que aquí no hay
nada que arrancar ni parar: el control es el archivo de ajustes, que el proceso
MCP relee en cada llamada.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from pg_diagrammer.activity.settings import McpSettings
from pg_diagrammer.api.routes.profiles import _error
from pg_diagrammer.errors import ApiError

router = APIRouter(tags=["mcp"])


@router.get("/mcp/activity")
def read_activity(
    request: Request,
    since: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
):
    """Eventos posteriores al cursor `since`.

    El panel sondea con el `next_cursor` de la respuesta anterior. Si llega
    `reset: true`, su cursor caducó (rotación o limpieza) y debe repintar
    desde cero con lo que viene en `events`.
    """
    return {"ok": True, **request.app.state.mcp_activity.read(since=since, limit=limit)}


@router.delete("/mcp/activity")
def clear_activity(request: Request):
    request.app.state.mcp_activity.clear()
    return {"ok": True}


@router.get("/mcp/settings")
def get_mcp_settings(request: Request):
    settings = request.app.state.mcp_settings.get()
    return {"ok": True, "settings": settings.model_dump()}


class McpSettingsUpdate(BaseModel):
    """Campos opcionales: se cambia solo lo que venga."""

    enabled: bool | None = None
    allowed_profiles: list[str] | None = None
    allow_query: bool | None = None
    sample_rows: int | None = Field(default=None, ge=0, le=20)


@router.put("/mcp/settings")
def set_mcp_settings(body: McpSettingsUpdate, request: Request):
    store = request.app.state.mcp_settings
    profiles = request.app.state.profiles
    current = store.get()
    allowed = current.allowed_profiles if body.allowed_profiles is None else body.allowed_profiles
    if allowed:
        known = {p.id for p in profiles.list()}
        unknown = [pid for pid in allowed if pid not in known]
        if unknown:
            return _error(422, ApiError(
                code="VALIDATION",
                message=f"Perfiles inexistentes: {', '.join(unknown)}.",
                hint="Refresca la lista de perfiles antes de guardar.",
            ))
    updated = store.set(McpSettings(
        enabled=current.enabled if body.enabled is None else body.enabled,
        allowed_profiles=allowed,
        allow_query=current.allow_query if body.allow_query is None else body.allow_query,
        sample_rows=current.sample_rows if body.sample_rows is None else body.sample_rows,
    ))
    return {"ok": True, "settings": updated.model_dump()}
