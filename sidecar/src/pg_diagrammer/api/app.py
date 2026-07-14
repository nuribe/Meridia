"""Factory de la aplicación FastAPI con autenticación por token de sesión."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from pg_diagrammer import __version__
from pg_diagrammer.api.routes import connections, db, diagrams, profiles
from pg_diagrammer.connections.profiles import ProfileStore
from pg_diagrammer.errors import ApiError, classify_pg_error
from pg_diagrammer.introspection.cache import SnapshotCache
from pg_diagrammer.projects.store import DiagramStore

# Orígenes permitidos: webview de Tauri (prod) y Vite (dev / modo navegador).
ALLOWED_ORIGINS = [
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "http://localhost:1420",
    "http://127.0.0.1:1420",
]


def create_app(session_token: str, data_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="pg-diagrammer sidecar", version=__version__, docs_url=None, redoc_url=None)
    app.state.session_token = session_token
    app.state.profiles = ProfileStore(data_dir=data_dir)
    app.state.snapshots = SnapshotCache()
    app.state.diagrams = DiagramStore(app.state.profiles.data_dir)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["*"],
        allow_headers=["Content-Type", "X-Session-Token"],
    )

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        # /health queda abierto (solo escuchamos en 127.0.0.1) para liveness.
        # OPTIONS (preflight CORS) no lleva headers custom: lo resuelve CORSMiddleware.
        if request.url.path.startswith("/api/") and request.method != "OPTIONS":
            if request.headers.get("X-Session-Token") != app.state.session_token:
                return JSONResponse(
                    status_code=401,
                    content=ApiError(
                        code="INVALID_TOKEN",
                        message="Token de sesión ausente o inválido.",
                        hint="El frontend debe usar el token entregado por el shell (comando sidecar_info).",
                    ).model_dump(),
                )
        return await call_next(request)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        return JSONResponse(status_code=500, content=classify_pg_error(exc).model_dump())

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": __version__}

    app.include_router(connections.router, prefix="/api/v1")
    app.include_router(profiles.router, prefix="/api/v1")
    app.include_router(db.router, prefix="/api/v1")
    app.include_router(diagrams.router, prefix="/api/v1")
    return app
