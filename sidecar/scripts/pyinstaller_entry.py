"""Punto de entrada para PyInstaller.

PyInstaller no acepta `python -m paquete`, así que este script mínimo importa
y ejecuta el dispatcher real. Se empaqueta con scripts/build-standalone.ps1
(o el workflow de CI) en un único ejecutable `pg-diagrammer-sidecar`, que sirve
para los dos modos: sin argumentos arranca el sidecar HTTP y con `--mcp` el
servidor MCP por stdio (ver pg_diagrammer/cli.py y docs/mcp.md).
"""
from pg_diagrammer.cli import main

if __name__ == "__main__":
    main()
