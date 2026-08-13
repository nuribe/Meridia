"""Punto de entrada único del backend: sidecar HTTP o servidor MCP.

Un solo ejecutable con dos modos, en vez de dos binarios. La razón es práctica:
el sidecar empaquetado ya viaja en el instalador, en el ZIP portable y como
`externalBin` de Tauri, y contiene exactamente el mismo código Python que
necesitaría un binario MCP. Duplicarlo serían ~40 MB más por plataforma, otra
entrada en la matriz de CI y un segundo artefacto que firmar, para no ganar
nada: quien configura su cliente MCP escribe una ruta y unos argumentos, y le
da igual cuál sea el nombre del archivo.

    pg-diagrammer-sidecar              → sidecar HTTP (lo lanza el shell Tauri)
    pg-diagrammer-sidecar --mcp        → servidor MCP por stdio (lo lanza el
                                         cliente: VS Code, Claude Desktop…)

Esto **no cambia el mecanismo shell↔sidecar**: sin argumentos el
comportamiento es idéntico al de siempre, incluido el handshake por stdout.
Con `--mcp` no se abre ningún puerto ni se lee el token; son dos programas que
comparten envoltorio, no un proceso con dos personalidades a la vez.
"""
from __future__ import annotations

import sys

MCP_FLAG = "--mcp"

USAGE = """Meridia — backend (pg_diagrammer)

  (sin argumentos)   Sidecar HTTP en 127.0.0.1 con puerto efímero. Lo lanza el
                     shell de Tauri y publica {"port", "pid"} por stdout.
  --mcp              Servidor MCP por stdio. Lo lanza el cliente (VS Code,
                     Claude Desktop, Claude Code). Ver docs/mcp.md.
  --help             Esta ayuda.
"""


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if "--help" in args or "-h" in args:
        print(USAGE)
        return
    if MCP_FLAG in args:
        # Import perezoso: el sidecar no debe pagar el arranque del SDK de MCP,
        # ni el servidor MCP el de uvicorn.
        from pg_diagrammer.mcp.server import main as mcp_main

        mcp_main()
        return
    from pg_diagrammer.main import main as sidecar_main

    sidecar_main()


if __name__ == "__main__":
    main()
