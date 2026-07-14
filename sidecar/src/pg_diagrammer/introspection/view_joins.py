"""Extracción de joins del SQL de una vista.

Opera sobre la salida de pg_get_viewdef(oid, true), cuyo formato canónico
coloca el FROM y cada JOIN en líneas propias con alias explícitos, lo que
hace fiable un análisis línea a línea. El "otro lado" de cada join se
resuelve buscando en su condición ON los alias de tablas ya vistas.
"""
from __future__ import annotations

import re

FROM_LINE = re.compile(
    r'^\s*FROM\s+(?P<rel>(?:"[^"]+"|\w+)(?:\.(?:"[^"]+"|\w+))?)'
    r"(?:\s+(?:AS\s+)?(?P<alias>\w+))?\s*,?\s*$",
    re.IGNORECASE,
)

JOIN_LINE = re.compile(
    r"^\s*(?P<type>LEFT|RIGHT|FULL|CROSS|INNER)?\s*(?:OUTER\s+)?JOIN\s+"
    r'(?P<rel>(?:"[^"]+"|\w+)(?:\.(?:"[^"]+"|\w+))?)'
    r"(?:\s+(?:AS\s+)?(?P<alias>\w+))?"
    r"(?:\s+ON\s+(?P<on>.*))?$",
    re.IGNORECASE,
)

CLAUSE_BREAK = re.compile(r"^\s*(WHERE|GROUP|ORDER|HAVING|LIMIT|OFFSET|UNION|SELECT)\b", re.IGNORECASE)


def _norm(rel: str) -> str:
    return rel.replace('"', "")


def parse_view_joins(definition: str, known: dict[str, str]) -> list[dict]:
    """Devuelve joins [{source, target, join_type}] con claves "schema.tabla".

    `known` mapea tanto claves completas como nombres sueltos a la clave
    completa (limitado a las tablas de las que depende la vista).
    """

    def resolve(rel: str) -> str | None:
        r = _norm(rel)
        return known.get(r) or known.get(r.split(".")[-1])

    alias_map: dict[str, str] = {}
    joins: list[dict] = []
    pending: dict | None = None

    def register(rel: str, alias: str | None, key: str | None) -> str:
        name = _norm(rel).split(".")[-1]
        final_alias = alias or name
        if key:
            alias_map[final_alias] = key
            alias_map.setdefault(name, key)
        return final_alias

    for raw in definition.splitlines():
        line = raw.rstrip()
        m = FROM_LINE.match(line)
        if m:
            register(m.group("rel"), m.group("alias"), resolve(m.group("rel")))
            pending = None
            continue
        m = JOIN_LINE.match(line)
        if m:
            key = resolve(m.group("rel"))
            alias = register(m.group("rel"), m.group("alias"), key)
            pending = {
                "key": key,
                "alias": alias,
                "type": (m.group("type") or "INNER").upper(),
                "on": m.group("on") or "",
            }
            if key:
                joins.append(pending)
            continue
        if pending is not None and line.strip() and not CLAUSE_BREAK.match(line):
            pending["on"] += " " + line.strip()
        elif CLAUSE_BREAK.match(line):
            pending = None

    result: list[dict] = []
    for j in joins:
        other = None
        for al in re.findall(r"(\w+)\s*\.", j["on"]):
            if al != j["alias"] and al in alias_map and alias_map[al] != j["key"]:
                other = alias_map[al]
                break
        if other is None and j["type"] == "CROSS":
            other = next((k for k in alias_map.values() if k != j["key"]), None)
        if other and j["key"]:
            source_cols: list[str] = []
            target_cols: list[str] = []
            for al, col in re.findall(r"(\w+)\s*\.\s*(\w+)", j["on"]):
                mapped = alias_map.get(al)
                if mapped == other and col not in source_cols:
                    source_cols.append(col)
                elif mapped == j["key"] and col not in target_cols:
                    target_cols.append(col)
            result.append({
                "source": other,
                "target": j["key"],
                "join_type": f'{j["type"]} JOIN',
                "source_columns": source_cols,
                "target_columns": target_cols,
            })
    return result


def collect_relations(definition: str, known: dict[str, str]) -> set[str]:
    """Todas las relaciones (tablas/vistas) referenciadas en FROM/JOIN,
    resueltas contra `known`. Complementa a pg_depend por robustez."""
    rels: set[str] = set()
    for raw in definition.splitlines():
        line = raw.rstrip()
        m = FROM_LINE.match(line) or JOIN_LINE.match(line)
        if m:
            r = _norm(m.group("rel"))
            key = known.get(r) or known.get(r.split(".")[-1])
            if key:
                rels.add(key)
    return rels
