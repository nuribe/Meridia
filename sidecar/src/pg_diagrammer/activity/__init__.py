"""Bitácora y ajustes del acceso MCP.

Este paquete es el **punto de encuentro entre procesos**. El servidor MCP no
corre dentro de Meridia: lo lanza el cliente (VS Code, Claude Desktop), puede
haber varios a la vez y pueden correr con la app cerrada. No comparten memoria
con el sidecar ni pueden hablar con su API —el puerto es efímero y el token
rota—, así que la comunicación va por dos archivos en el directorio de datos:

- `mcp-activity.jsonl`  — append-only, lo escriben los procesos MCP y lo lee
  el sidecar para pintar la vista «Actividad IA».
- `mcp-settings.json`   — lo escribe la app y lo relee el MCP en cada llamada,
  de modo que apagar el interruptor corta el acceso al instante.
"""
