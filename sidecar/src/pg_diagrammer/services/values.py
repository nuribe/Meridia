"""Conversión de valores de la base a algo serializable, legible y acotado.

Vive en la capa de servicios porque lo usan los dos adaptadores: la vista de
datos y el editor de consultas de la app, y la tool `meridia_run_select` del
servidor MCP. El tope de tamaño no es cosmético: sin él, una tabla de bitácora
con payloads grandes genera cientos de MB de JSON por página y agota la memoria
del sidecar antes de responder.
"""
from __future__ import annotations

import json
import math

MAX_CELL_CHARS = 4000


def clip(text: str, limit: int = MAX_CELL_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + "… (truncado)"


def jsonable(v, limit: int = MAX_CELL_CHARS):
    """Convierte un valor de la BD a algo serializable, legible y acotado."""
    if v is None or isinstance(v, (bool, int)):
        return v
    if isinstance(v, str):
        return clip(v, limit)
    if isinstance(v, float):
        return str(v) if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(v, (bytes, memoryview)):
        h = bytes(v).hex()
        return f"\\x{h[:120]}{'…' if len(h) > 120 else ''}"
    if isinstance(v, (dict, list)):
        return clip(json.dumps(v, ensure_ascii=False, default=str), limit)
    return clip(str(v), limit)
