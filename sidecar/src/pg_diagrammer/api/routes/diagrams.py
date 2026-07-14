"""CRUD de diagramas (.pgdiag) y ajuste del directorio donde se guardan."""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from pg_diagrammer.api.routes.profiles import _error
from pg_diagrammer.domain.models import DiagramCreate, DiagramNodePos, DiagramNote
from pg_diagrammer.errors import ApiError

router = APIRouter(tags=["diagrams"])


@router.get("/settings/diagrams-dir")
def get_diagrams_dir(request: Request):
    """Directorio actual donde se listan/guardan los diagramas."""
    store = request.app.state.diagrams
    return {
        "ok": True,
        "dir": str(store.dir),
        "default": str(store.default_dir),
        "is_default": str(store.dir) == str(store.default_dir),
    }


class DiagramsDir(BaseModel):
    dir: str


@router.put("/settings/diagrams-dir")
def set_diagrams_dir(data: DiagramsDir, request: Request):
    """Establece el directorio por defecto de diagramas (lo crea si no existe)."""
    store = request.app.state.diagrams
    if not data.dir.strip():
        return _error(422, ApiError(code="VALIDATION", message="Falta la ruta del directorio."))
    try:
        new_dir = store.set_dir(data.dir)
    except OSError as exc:
        return _error(
            400,
            ApiError(
                code="INVALID_DIR",
                message=f"No se pudo usar el directorio: {exc}",
                hint="Verifica que la ruta sea válida y tengas permisos de escritura.",
            ),
        )
    return {"ok": True, "dir": str(new_dir)}


@router.get("/diagrams")
def list_diagrams(request: Request, profile_id: str | None = None, dbname: str | None = None):
    store = request.app.state.diagrams
    return {
        "ok": True,
        "diagrams": [
            {
                "id": d.id,
                "name": d.name,
                "profile_id": d.profile_id,
                "dbname": d.dbname,
                "node_count": len(d.nodes),
                "updated_at": d.updated_at.isoformat(),
            }
            for d in store.list(profile_id=profile_id, dbname=dbname)
        ],
    }


@router.post("/diagrams", status_code=201)
def create_diagram(data: DiagramCreate, request: Request):
    doc = request.app.state.diagrams.create(data)
    return {"ok": True, "diagram": doc.model_dump(mode="json")}


@router.get("/diagrams/{diagram_id}")
def get_diagram(diagram_id: str, request: Request):
    doc = request.app.state.diagrams.get(diagram_id)
    if doc is None:
        return _error(404, ApiError(code="NOT_FOUND", message="Diagrama inexistente."))
    return {"ok": True, "diagram": doc.model_dump(mode="json")}


class DiagramUpdate(BaseModel):
    name: str
    nodes: list[DiagramNodePos] = []
    notes: list[DiagramNote] = []


@router.put("/diagrams/{diagram_id}")
def update_diagram(diagram_id: str, data: DiagramUpdate, request: Request):
    doc = request.app.state.diagrams.update(diagram_id, data.name, data.nodes, data.notes)
    if doc is None:
        return _error(404, ApiError(code="NOT_FOUND", message="Diagrama inexistente."))
    return {"ok": True, "diagram": doc.model_dump(mode="json")}


@router.delete("/diagrams/{diagram_id}")
def delete_diagram(diagram_id: str, request: Request):
    if not request.app.state.diagrams.delete(diagram_id):
        return _error(404, ApiError(code="NOT_FOUND", message="Diagrama inexistente."))
    return {"ok": True}
