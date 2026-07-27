"""Punto de entrada del sidecar.

Protocolo de handshake con el shell Tauri:
1. El shell exporta PG_DIAGRAMMER_TOKEN antes de lanzar este proceso.
2. Aquí abrimos un socket en 127.0.0.1 con puerto efímero (0) y publicamos
   una única línea JSON por stdout: {"port": N, "pid": M}.
3. El shell la lee y expone {port, token} al frontend.

Ejecución manual (desarrollo):
    PG_DIAGRAMMER_TOKEN=dev python -m pg_diagrammer
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import sys
from pathlib import Path

import uvicorn

from pg_diagrammer.api.app import create_app

HOST = "127.0.0.1"


def _detect_portable_diagrams() -> None:
    """Modo portable: si el ejecutable empaquetado (PyInstaller) tiene una
    carpeta `diagrams` al lado, se usa como biblioteca por defecto. No pisa
    la elección explícita del usuario guardada en settings.json."""
    if not getattr(sys, "frozen", False):
        return
    candidate = Path(sys.executable).resolve().parent / "diagrams"
    if candidate.is_dir():
        os.environ.setdefault("PG_DIAGRAMMER_DIAGRAMS_DIR", str(candidate))


def main() -> None:
    _detect_portable_diagrams()
    token = os.environ.get("PG_DIAGRAMMER_TOKEN")
    if not token:
        # Arranque manual: generamos token y lo mostramos por stderr.
        token = secrets.token_urlsafe(32)
        print(f"[pg-diagrammer] token de sesión generado: {token}", file=sys.stderr)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, 0))
    port = sock.getsockname()[1]

    # Handshake: única línea JSON en stdout, luego flush inmediato.
    print(json.dumps({"port": port, "pid": os.getpid()}), flush=True)

    app = create_app(session_token=token)
    config = uvicorn.Config(app, log_level="warning")
    server = uvicorn.Server(config)
    server.run(sockets=[sock])


if __name__ == "__main__":
    main()
