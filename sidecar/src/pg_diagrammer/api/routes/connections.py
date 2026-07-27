"""Rutas de conexión: prueba de conexión y listado de bases de datos."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from pg_diagrammer.connections import manager
from pg_diagrammer.domain.models import ConnectionParams
from pg_diagrammer.errors import DB_EXCEPTIONS, classify_db_error

router = APIRouter(tags=["connections"])


@router.post("/connections/test")
def test_connection(params: ConnectionParams):
    try:
        return {"ok": True, **manager.test_connection(params)}
    except DB_EXCEPTIONS as exc:
        err = classify_db_error(params.engine, exc)
        return JSONResponse(status_code=400, content={"ok": False, **err.model_dump()})


@router.post("/connections/databases")
def list_databases(params: ConnectionParams):
    """Lista las BDs disponibles usando parámetros explícitos.

    En Fase 1 esto pasará a GET /connections/{id}/databases con perfiles
    persistidos y credenciales en keychain.
    """
    try:
        return {"ok": True, "databases": manager.list_databases(params)}
    except DB_EXCEPTIONS as exc:
        err = classify_db_error(params.engine, exc)
        return JSONResponse(status_code=400, content={"ok": False, **err.model_dump()})
