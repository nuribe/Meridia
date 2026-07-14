"""Generadores de formatos editables a partir del snapshot.

- Mermaid erDiagram (.mmd): para documentación (GitHub, Notion, etc.).
- DBML (.dbml): para dbdiagram.io y herramientas compatibles.

SVG/PNG se generan en el frontend (WYSIWYG del lienzo); aquí solo texto.
"""
from __future__ import annotations

import re

from pg_diagrammer.domain.models import Snapshot, Table

MERMAID_CARD = {"1:1": "||--||", "N:1": "}o--||", "N:M": "}o--o{"}


def _mermaid_ident(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", key)


def _mermaid_type(data_type: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", data_type).strip("_") or "unknown"


def _fk_columns(table: Table) -> set[str]:
    return {col for fk in table.foreign_keys for col in fk.columns}


def to_mermaid(snapshot: Snapshot, tables: list[str]) -> str:
    lines = ["erDiagram"]
    for key in tables:
        t = snapshot.tables.get(key)
        if t is None:
            continue
        lines.append(f"    {_mermaid_ident(key)} {{")
        fk_cols = _fk_columns(t)
        for c in t.columns:
            flags = []
            if c.is_pk:
                flags.append("PK")
            if c.name in fk_cols:
                flags.append("FK")
            flag = f" {','.join(flags)}" if flags else ""
            comment = f' "{c.comment}"' if c.comment else ""
            lines.append(f"        {_mermaid_type(c.data_type)} {c.name}{flag}{comment}")
        lines.append("    }")
    wanted = set(tables)
    for r in snapshot.relationships:
        if r.source in wanted and r.target in wanted:
            card = MERMAID_CARD.get(r.cardinality.value, "}o--||")
            lines.append(f'    {_mermaid_ident(r.source)} {card} {_mermaid_ident(r.target)} : "{r.fk_name}"')
    return "\n".join(lines) + "\n"


def to_dbml(snapshot: Snapshot, tables: list[str]) -> str:
    lines = []
    for key in tables:
        t = snapshot.tables.get(key)
        if t is None:
            continue
        lines.append(f'Table "{t.schema_name}"."{t.name}" {{')
        for c in t.columns:
            settings = []
            if c.is_pk:
                settings.append("pk")
            if not c.is_nullable:
                settings.append("not null")
            if c.default:
                settings.append(f"default: `{c.default}`")
            s = f" [{', '.join(settings)}]" if settings else ""
            dt = c.data_type if re.fullmatch(r"[A-Za-z0-9_()\[\],]+", c.data_type) else f'"{c.data_type}"'
            lines.append(f'  "{c.name}" {dt}{s}')
        if t.comment:
            note = t.comment.replace("'", "\\'")
            lines.append(f"  Note: '{note}'")
        lines.append("}")
        lines.append("")
    wanted = set(tables)
    for r in snapshot.relationships:
        if r.source not in wanted or r.target not in wanted:
            continue
        op = "-" if r.cardinality.value == "1:1" else ">"
        src_schema, src_table = r.source.split(".", 1)
        tgt_schema, tgt_table = r.target.split(".", 1)
        if len(r.columns) == 1:
            src_cols = f'"{r.columns[0]}"'
            tgt_cols = f'"{r.ref_columns[0]}"'
        else:
            src_cols = "(" + ", ".join(f'"{c}"' for c in r.columns) + ")"
            tgt_cols = "(" + ", ".join(f'"{c}"' for c in r.ref_columns) + ")"
        lines.append(
            f'Ref: "{src_schema}"."{src_table}".{src_cols} {op} "{tgt_schema}"."{tgt_table}".{tgt_cols}'
        )
    return "\n".join(lines) + "\n"
