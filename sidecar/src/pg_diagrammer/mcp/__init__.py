"""Servidor MCP de Meridia: segundo adaptador sobre `services/`.

No forma parte del sidecar. Es un proceso aparte que lanza el cliente MCP
(VS Code / Copilot, Claude Desktop, Claude Code) por stdio, y que lee la misma
configuración de perfiles y el mismo keychain que la app.

    python -m pg_diagrammer.mcp        # desarrollo
    meridia-mcp                        # entry point instalado

Nota para quien edite este paquete: se llama `mcp` igual que el SDK oficial,
pero Python 3 usa importaciones absolutas, así que dentro de estos módulos
`from mcp.server import ...` se refiere SIEMPRE al SDK instalado, nunca a este
paquete. Para importar de aquí, usa la ruta completa `pg_diagrammer.mcp.*`.
"""
from pg_diagrammer.mcp.server import main

__all__ = ["main"]
