"""Prueba de humo del modo MCP del ejecutable empaquetado.

Existe por un fallo concreto y silencioso: PyInstaller decide qué módulos
incluir analizando los imports, y un import que solo ocurre por una rama poco
transitada —como el SDK de MCP, que solo se carga con `--mcp`— puede quedarse
fuera del binario. El sidecar seguiría arrancando perfecto y el modo MCP
fallaría solo en la máquina del usuario, al conectar su cliente.

Así que se comprueba en la build: se lanza el ejecutable con `--mcp`, se habla
el protocolo de verdad y se exige que declare sus herramientas.

    python scripts/smoke_mcp.py dist/pg-diagrammer-sidecar
    python scripts/smoke_mcp.py .venv/bin/python -m pg_diagrammer --mcp

Sirve también para depurar la configuración de un cliente: se le pasa el MISMO
`command` y los mismos `args` que tenga el cliente en su JSON y dice si eso
arranca. Si aquí funciona y en el cliente no, el problema es la configuración
del cliente; si aquí falla, el mensaje de error es el que hay que arreglar.

Sale con código 1 si algo falla, para poder engancharlo a CI.
"""
from __future__ import annotations

import os
import sys
import tempfile

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import Implementation

# Con menos de esto, algo se quedó fuera del empaquetado.
MIN_TOOLS = 5
TIMEOUT_S = 60
MCP_FLAG = "--mcp"


async def _check(cmd: list[str]) -> list[str]:
    """Habla el protocolo con el comando dado y devuelve los nombres de tool."""
    # Directorio de datos propio: la prueba no debe ver ni tocar los perfiles
    # reales de quien esté compilando.
    env = dict(os.environ, PG_DIAGRAMMER_DATA_DIR=tempfile.mkdtemp())
    params = StdioServerParameters(command=cmd[0], args=cmd[1:], env=env)
    # Un binario roto puede quedarse colgado en vez de fallar: sin plazo, la
    # build se quedaría esperando para siempre.
    with anyio.fail_after(TIMEOUT_S):
        async with stdio_client(params) as (read, write):
            async with ClientSession(
                read, write, client_info=Implementation(name="build-smoke", version="1.0")
            ) as session:
                init = await session.initialize()
                tools = (await session.list_tools()).tools
                print(f"  servidor: {init.server_info.name} {init.server_info.version}")
                print(f"  herramientas: {len(tools)}")
                sueltas = [
                    t.name for t in tools
                    if not (t.annotations and t.annotations.read_only_hint)
                ]
                if sueltas:
                    raise SystemExit(
                        "FALLA: estas herramientas no se declaran de solo lectura: "
                        f"{sueltas}"
                    )
                return [t.name for t in tools]


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(
            "uso: python scripts/smoke_mcp.py <comando> [args...]\n"
            "     los args por defecto son ['--mcp'] si no se dan otros"
        )
    cmd = sys.argv[1:]
    # Sin argumentos explícitos se asume el ejecutable empaquetado, que espera
    # `--mcp`. Con argumentos se respeta lo que venga: así se puede probar tal
    # cual la línea que tenga configurada un cliente.
    if len(cmd) == 1:
        cmd.append(MCP_FLAG)
    if not os.path.exists(cmd[0]):
        raise SystemExit(f"FALLA: no existe {cmd[0]}")
    print("Humo MCP sobre: " + " ".join(cmd))
    tools = anyio.run(_check, cmd)
    if len(tools) < MIN_TOOLS:
        raise SystemExit(f"FALLA: solo {len(tools)} herramientas (esperadas >= {MIN_TOOLS})")
    print("  ok — el modo MCP del ejecutable responde")


if __name__ == "__main__":
    main()
