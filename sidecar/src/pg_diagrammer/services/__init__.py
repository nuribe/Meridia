"""Capa de servicios: la lógica de negocio, sin HTTP.

Existe para que haya MÁS DE UN adaptador sobre el mismo dominio. Hoy son las
rutas FastAPI; el servidor MCP (ver docs/mcp.md) es el segundo. Nada de este
paquete debe importar `fastapi` ni construir respuestas HTTP: los fallos se
señalan con las excepciones de `services.errors`, y cada adaptador las traduce
a lo suyo (envelope JSON en REST, `isError` en MCP).
"""
