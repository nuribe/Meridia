"""Separación de un script SQL en sentencias individuales.

Partir por ``;`` a lo bruto se rompe en cuanto aparece un literal, un cuerpo
``$$…$$`` o un comentario con punto y coma dentro. Este lexer recorre el texto
una sola vez y solo corta en los ``;`` que están fuera de todo contexto citado:
literales ``'…'``, identificadores ``"…"`` y ``[…]``, dollar-quoting de
PostgreSQL y comentarios de línea y de bloque (anidables).

Límite conocido: un cuerpo de rutina de T-SQL (``CREATE PROCEDURE … AS BEGIN
…; …; END``) no va entrecomillado, así que se parte por sus puntos y coma
internos. En PostgreSQL no ocurre porque el cuerpo va en dollar-quoting.
"""
from __future__ import annotations

import re

_DOLLAR_TAG = re.compile(r"\$([A-Za-z_]\w*)?\$")
_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)


def _skip_line_comment(s: str, i: int, n: int) -> int:
    j = s.find("\n", i)
    return n if j < 0 else j + 1


def _skip_block_comment(s: str, i: int, n: int) -> int:
    """Comentario /* … */, teniendo en cuenta que PostgreSQL los anida."""
    depth, j = 1, i + 2
    while j < n and depth:
        if s.startswith("/*", j):
            depth += 1
            j += 2
        elif s.startswith("*/", j):
            depth -= 1
            j += 2
        else:
            j += 1
    return j


def _skip_quoted(s: str, i: int, n: int, closer: str) -> int:
    """Avanza hasta cerrar la comilla; el cierre duplicado la escapa."""
    j = i + 1
    while j < n:
        if s[j] == closer:
            if j + 1 < n and s[j + 1] == closer:
                j += 2
                continue
            return j + 1
        j += 1
    return n


def is_only_comments(stmt: str) -> bool:
    """¿La sentencia es solo comentarios o espacios?"""
    return _COMMENTS.sub(" ", stmt).strip() == ""


def split_statements(script: str) -> list[str]:
    """Sentencias del script, sin las vacías ni las que solo son comentarios."""
    out: list[str] = []
    buf: list[str] = []
    i, n = 0, len(script)

    def flush() -> None:
        stmt = "".join(buf).strip()
        if stmt and not is_only_comments(stmt):
            out.append(stmt)
        buf.clear()

    while i < n:
        ch = script[i]
        if script.startswith("--", i):
            j = _skip_line_comment(script, i, n)
        elif script.startswith("/*", i):
            j = _skip_block_comment(script, i, n)
        elif ch in ("'", '"'):
            j = _skip_quoted(script, i, n, ch)
        elif ch == "[":
            j = _skip_quoted(script, i, n, "]")
        elif ch == "$" and _DOLLAR_TAG.match(script, i):
            m = _DOLLAR_TAG.match(script, i)
            assert m is not None
            end = script.find(m.group(0), m.end())
            j = n if end < 0 else end + len(m.group(0))
        elif ch == ";":
            flush()
            i += 1
            continue
        else:
            buf.append(ch)
            i += 1
            continue
        buf.append(script[i:j])
        i = j

    flush()
    return out
