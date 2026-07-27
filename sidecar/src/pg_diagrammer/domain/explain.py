"""Normalización de planes de ejecución a un árbol común.

PostgreSQL (`EXPLAIN`, formato texto) y SQL Server (`SHOWPLAN_ALL` /
`STATISTICS PROFILE`) describen el plan de formas muy distintas. Este módulo
las traduce a la MISMA lista de nodos planos:

    {depth, kind, op, text, estimate_rows, cost, actual_rows, actual_time, detail}

`depth` es la profundidad en el árbol (0 = raíz); el frontend la usa para
indentar la tabla y para reconstruir el árbol del diagrama. `kind` distingue
los operadores reales (`"operator"`) de las líneas de resumen que PostgreSQL
añade al final (`"summary"`: Planning Time, Execution Time…), que no son nodos
del plan y no deben dibujarse como tales.

Todo aquí es texto puro y sin dependencias de driver, así que se puede probar
sin base de datos.
"""
from __future__ import annotations

import re

# --- PostgreSQL ---

# "  ->  Hash Join  (cost=1.09..2.23 rows=5 width=64) (actual time=0.01..0.02 rows=5 loops=1)"
_PG_ARROW = re.compile(r"^(?P<indent> *)->  (?P<body>.*)$")
_PG_COST = re.compile(r"\(cost=[\d.]+\.\.(?P<cost>[\d.]+) rows=(?P<rows>\d+)")
_PG_ACTUAL = re.compile(
    r"\(actual time=[\d.]+\.\.(?P<time>[\d.]+) rows=(?P<rows>\d+)"
)
# Líneas de resumen que no son nodos ni detalle de un nodo.
_PG_SUMMARY = re.compile(r"^(Planning|Execution|JIT|Trigger|Settings)\b", re.I)

# Ancho de la sangría que PostgreSQL añade por nivel en las líneas "->".
_PG_LEVEL_WIDTH = 6


def parse_postgres_plan(lines: list[str]) -> list[dict]:
    """Convierte la salida de `EXPLAIN` (formato texto) en nodos planos."""
    nodes: list[dict] = []
    for raw in lines:
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        arrow = _PG_ARROW.match(line)
        if arrow:
            depth = len(arrow.group("indent")) // _PG_LEVEL_WIDTH + 1
            nodes.append(_pg_node(depth, arrow.group("body")))
            continue
        stripped = line.strip()
        if not nodes and not _PG_SUMMARY.match(stripped):
            # Primera línea: nodo raíz.
            nodes.append(_pg_node(0, stripped))
            continue
        if nodes and line.startswith(" ") and not _PG_SUMMARY.match(stripped):
            # Línea de detalle del último nodo (Filter, Buffers, Index Cond…).
            nodes[-1]["detail"].append(stripped)
            continue
        # Resumen final (Planning Time, Execution Time…): no es un operador.
        nodes.append(_pg_node(0, stripped, kind="summary"))
    return nodes


def _pg_node(depth: int, body: str, kind: str = "operator") -> dict:
    cost = _PG_COST.search(body)
    actual = _PG_ACTUAL.search(body)
    op = body.split("  (cost=")[0].strip() if "(cost=" in body else body.strip()
    return {
        "depth": depth,
        "kind": kind,
        "op": op,
        "text": body.strip(),
        "estimate_rows": float(cost.group("rows")) if cost else None,
        "cost": float(cost.group("cost")) if cost else None,
        "actual_rows": float(actual.group("rows")) if actual else None,
        "actual_time": float(actual.group("time")) if actual else None,
        "detail": [],
    }


# --- SQL Server ---

# Cada nivel de SHOWPLAN_ALL antepone 5 caracteres ("  |--").
_MS_LEVEL_WIDTH = 5


def parse_mssql_plan(columns: list[str], rows: list[tuple]) -> list[dict]:
    """Convierte el rowset de SHOWPLAN_ALL / STATISTICS PROFILE en nodos planos.

    Ambos comandos devuelven las mismas columnas; STATISTICS PROFILE añade
    `Rows` y `Executes` con los valores reales de la ejecución.
    """
    idx = {name.lower(): i for i, name in enumerate(columns)}

    def value(row, name):
        pos = idx.get(name.lower())
        return row[pos] if pos is not None else None

    nodes: list[dict] = []
    for row in rows:
        stmt = str(value(row, "StmtText") or "")
        marker = stmt.find("|--")
        if marker >= 0:
            depth = marker // _MS_LEVEL_WIDTH + 1
            text = stmt[marker + 3 :].strip()
        else:
            depth = 0
            text = stmt.strip()
        physical = value(row, "PhysicalOp")
        logical = value(row, "LogicalOp")
        if physical:
            op = str(physical)
        elif depth == 0:
            # Fila raíz: el texto es la sentencia entera. Como nodo del plan
            # basta con su verbo (SELECT), igual que hace SSMS.
            op = (text.split(None, 1) or ["SELECT"])[0].upper()
        else:
            op = text.split("(")[0].strip()
        detail = []
        argument = value(row, "Argument")
        if logical and physical and str(logical) != str(physical):
            detail.append(f"Lógico: {logical}")
        if argument:
            detail.append(str(argument))
        nodes.append(
            {
                "depth": depth,
                "kind": "operator",
                "op": op,
                "text": text,
                "estimate_rows": _num(value(row, "EstimateRows")),
                "cost": _num(value(row, "TotalSubtreeCost")),
                "actual_rows": _num(value(row, "Rows")),
                "actual_time": None,
                "detail": detail,
            }
        )
    return nodes


def _num(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def nodes_to_text(nodes: list[dict]) -> str:
    """Representación textual del árbol (para copiar al portapapeles)."""
    out = []
    for n in nodes:
        pad = "  " * n["depth"]
        arrow = "-> " if n["depth"] else ""
        out.append(f"{pad}{arrow}{n['text']}")
        for d in n["detail"]:
            out.append(f"{pad}   {d}")
    return "\n".join(out)
