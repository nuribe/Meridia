"""Constructor bidireccional de consultas SQL (diagrama <-> SQL).

Lógica de dominio pura (sin acceso a la BD) para el constructor gráfico de
consultas del QueryTab:

- ``suggest_join_type``: sugiere el tipo de JOIN según la nulabilidad de las
  columnas unidas (ambas NOT NULL -> INNER; alguna permite nulos -> LEFT).
- ``build_query_sql``: traduce un conjunto de tablas + joins (el diagrama) a
  una sentencia SELECT válida para PostgreSQL.
- ``parse_query_sql``: analiza una sentencia SELECT existente y reconstruye el
  diagrama equivalente (tablas, alias y joins con sus columnas).

Diseño robusto: cubre alias de tabla (con/sin AS), múltiples joins, joins
compuestos (varias columnas en el ON), identificadores citados con comillas y
CROSS JOIN (que no lleva ON). Las cláusulas posteriores al encadenado de joins
(WHERE/GROUP BY/...) se preservan intactas para que el ida y vuelta no rompa la
consulta del usuario.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Tipos de JOIN soportados por el constructor gráfico.
JOIN_TYPES = ("INNER JOIN", "LEFT JOIN", "RIGHT JOIN", "CROSS JOIN")

# Palabras clave que cierran el encadenado FROM/JOIN a nivel superior.
_TAIL_KEYWORDS = (
    "WHERE",
    "GROUP",
    "ORDER",
    "HAVING",
    "LIMIT",
    "OFFSET",
    "WINDOW",
    "FETCH",
    "UNION",
    "INTERSECT",
    "EXCEPT",
    "FOR",
)


# --------------------------------------------------------------------------- #
# Sugerencia de tipo de join                                                   #
# --------------------------------------------------------------------------- #
def suggest_join_type(source_nullable: bool, target_nullable: bool) -> str:
    """Sugiere el tipo de JOIN según la nulabilidad de ambos extremos.

    - Ambos extremos NOT NULL  -> ``INNER JOIN``.
    - Alguno permite nulos     -> ``LEFT JOIN`` (conserva las filas del lado
      izquierdo aunque no encuentren pareja).
    """
    if source_nullable or target_nullable:
        return "LEFT JOIN"
    return "INNER JOIN"


# --------------------------------------------------------------------------- #
# Estructuras de trabajo                                                       #
# --------------------------------------------------------------------------- #
@dataclass
class Join:
    source: str  # clave "schema.tabla" del lado ya presente en el encadenado
    target: str  # clave "schema.tabla" de la tabla que se une
    join_type: str = "INNER JOIN"
    source_columns: list[str] = field(default_factory=list)
    target_columns: list[str] = field(default_factory=list)


@dataclass
class QueryModel:
    """Representación intermedia común a build y parse."""

    tables: list[str] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)
    joins: list[Join] = field(default_factory=list)
    select_sql: str | None = None
    tail_sql: str | None = None
    unresolved: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _norm_join_type(jt: str | None) -> str:
    t = (jt or "INNER JOIN").strip().upper()
    if not t.endswith("JOIN"):
        t = f"{t} JOIN".strip()
    t = re.sub(r"\s+", " ", t)
    # OUTER es redundante para LEFT/RIGHT/FULL en la salida.
    t = t.replace("LEFT OUTER JOIN", "LEFT JOIN").replace("RIGHT OUTER JOIN", "RIGHT JOIN")
    t = t.replace("FULL OUTER JOIN", "FULL JOIN")
    if t == "JOIN":
        t = "INNER JOIN"
    return t


def _flip_join_type(jt: str) -> str:
    """Invierte LEFT<->RIGHT al invertir el sentido source/target del join."""
    if jt == "LEFT JOIN":
        return "RIGHT JOIN"
    if jt == "RIGHT JOIN":
        return "LEFT JOIN"
    return jt


# --------------------------------------------------------------------------- #
# Citado de identificadores                                                    #
# --------------------------------------------------------------------------- #
def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


_PLAIN_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Palabras reservadas comunes de T-SQL que obligan a citar con corchetes.
_TSQL_RESERVED = {
    "add", "all", "alter", "and", "any", "as", "asc", "backup", "begin",
    "between", "by", "case", "check", "column", "constraint", "create",
    "cross", "current", "database", "default", "delete", "desc", "distinct",
    "drop", "else", "end", "escape", "except", "exec", "exists", "file",
    "for", "foreign", "from", "full", "function", "grant", "group", "having",
    "in", "index", "inner", "insert", "intersect", "into", "is", "join",
    "key", "left", "like", "merge", "not", "null", "on", "or", "order",
    "outer", "over", "percent", "plan", "primary", "procedure", "public",
    "return", "right", "rule", "select", "set", "table", "then", "to", "top",
    "transaction", "trigger", "union", "unique", "update", "user", "values",
    "view", "when", "where", "while", "with",
}


def _quote_ident_tsql(name: str) -> str:
    """SQL Server: sin citar salvo que el nombre lo exija (menos ruido).

    Solo se usan [corchetes] si el identificador no es "regular" (espacios,
    símbolos, empieza por dígito) o es una palabra reservada de T-SQL.
    """
    if _PLAIN_IDENT.match(name) and name.lower() not in _TSQL_RESERVED:
        return name
    return "[" + name.replace("]", "]]") + "]"


def _ident_quoter(dialect: str):
    return _quote_ident_tsql if dialect == "sqlserver" else _quote_ident


def _quote_table(key: str, quote=_quote_ident) -> str:
    """"schema.tabla" -> '"schema"."tabla"' (o schema.tabla en T-SQL)."""
    parts = key.split(".", 1)
    return ".".join(quote(p) for p in parts)


_SAFE_ALIAS = re.compile(r"[^A-Za-z0-9_]")


def _make_aliases(tables: list[str], given: dict[str, str] | None) -> dict[str, str]:
    """Alias único por tabla, reutilizando los provistos cuando sean válidos."""
    given = given or {}
    used: set[str] = set()
    aliases: dict[str, str] = {}
    for key in tables:
        proposed = given.get(key)
        if proposed:
            proposed = _SAFE_ALIAS.sub("", proposed) or ""
        if not proposed:
            bare = key.split(".")[-1]
            proposed = (_SAFE_ALIAS.sub("", bare[:1]) or "t").lower()
        candidate = proposed
        i = 1
        while candidate in used or not candidate:
            i += 1
            candidate = f"{proposed}{i}"
        used.add(candidate)
        aliases[key] = candidate
    return aliases


# --------------------------------------------------------------------------- #
# Diagrama -> SQL                                                              #
# --------------------------------------------------------------------------- #
def build_query_sql(model: QueryModel, dialect: str = "postgresql") -> str:
    """Traduce tablas + joins a una sentencia SELECT del dialecto indicado.

    dialect: "postgresql" (identificadores entre comillas dobles) o
    "sqlserver" (sin citar; [corchetes] solo cuando el nombre lo exige).

    Ordena las tablas en un encadenado FROM + JOIN coherente: la tabla base es
    la que nunca aparece como destino de un join (o la primera). Cada join se
    emite cuando uno de sus extremos ya está en el encadenado; si hay que
    invertir el sentido, LEFT/RIGHT se intercambian para preservar la semántica.
    Las relaciones que cerrarían un ciclo se conservan como condiciones extra en
    un WHERE para no perder ningún join.
    """
    tables = list(dict.fromkeys(model.tables))  # únicas, orden estable
    if not tables:
        raise ValueError("Se requiere al menos una tabla para construir la consulta.")

    quote = _ident_quoter(dialect)
    aliases = _make_aliases(tables, model.aliases)
    joins = [
        Join(
            source=j.source,
            target=j.target,
            join_type=_norm_join_type(j.join_type),
            source_columns=list(j.source_columns),
            target_columns=list(j.target_columns),
        )
        for j in model.joins
        if j.source in aliases and j.target in aliases and j.source != j.target
    ]

    targets = {j.target for j in joins}
    base = next((t for t in tables if t not in targets), tables[0])
    included: list[str] = [base]
    lines: list[str] = [f"FROM {_quote_table(base, quote)} {aliases[base]}"]

    remaining = list(joins)
    extra_conditions: list[str] = []
    progress = True
    while remaining and progress:
        progress = False
        for j in list(remaining):
            s_in = j.source in included
            t_in = j.target in included
            if s_in and t_in:
                # Ciclo: no se puede volver a unir la tabla; se preserva el ON
                # como condición extra para no perder el join.
                cond = _on_condition(j, aliases, flip=False, quote=quote)
                if cond:
                    extra_conditions.append(cond)
                remaining.remove(j)
                progress = True
                continue
            if not (s_in or t_in):
                continue
            flip = not s_in  # el nuevo es el source -> invertimos el sentido
            new_key = j.source if flip else j.target
            jt = _flip_join_type(j.join_type) if flip else j.join_type
            if jt == "CROSS JOIN" or not (j.source_columns and j.target_columns):
                lines.append(f"CROSS JOIN {_quote_table(new_key, quote)} {aliases[new_key]}")
            else:
                on = _on_condition(j, aliases, flip=flip, quote=quote)
                lines.append(f"{jt} {_quote_table(new_key, quote)} {aliases[new_key]} ON {on}")
            included.append(new_key)
            remaining.remove(j)
            progress = True

    # Tablas sin ningún join (o joins que quedaron desconectados): CROSS JOIN.
    for t in tables:
        if t not in included:
            lines.append(f"CROSS JOIN {_quote_table(t, quote)} {aliases[t]}")
            included.append(t)
    for j in remaining:
        cond = _on_condition(j, aliases, flip=False, quote=quote)
        if cond:
            extra_conditions.append(cond)

    select = (model.select_sql or "").strip() or "*"
    sql = f"SELECT {select}\n" + "\n".join(lines)
    if extra_conditions:
        sql += "\nWHERE " + " AND ".join(extra_conditions)
    tail = (model.tail_sql or "").strip()
    if tail:
        sql += "\n" + tail
    return sql


def _on_condition(j: Join, aliases: dict[str, str], flip: bool, quote=_quote_ident) -> str:
    sa = aliases[j.source]
    ta = aliases[j.target]
    pairs = zip(j.source_columns, j.target_columns)
    return " AND ".join(
        f"{sa}.{quote(sc)} = {ta}.{quote(tc)}" for sc, tc in pairs
    )


# --------------------------------------------------------------------------- #
# SQL -> Diagrama                                                             #
# --------------------------------------------------------------------------- #
_COMMENT_LINE = re.compile(r"--[^\n]*")
_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)

_QUALIFIED = r'(?:"[^"]+"|\[[^\]]+\]|\w+)(?:\.(?:"[^"]+"|\[[^\]]+\]|\w+))?'
_TABLE_REF = re.compile(
    r"(?P<rel>" + _QUALIFIED + r")"
    r'(?:\s+(?:AS\s+)?(?P<alias>"[^"]+"|\[[^\]]+\]|\w+))?',
    re.IGNORECASE,
)

# Palabras que nunca son un alias de tabla (evita confundir "ON"/"JOIN" con alias).
_ALIAS_STOP = {
    "on", "using", "join", "left", "right", "inner", "full", "cross",
    "outer", "where", "group", "order", "having", "limit", "offset",
    "union", "intersect", "except", "natural", "lateral",
}

_JOIN_HEAD = re.compile(
    r"(?P<type>LEFT|RIGHT|FULL|INNER|CROSS)?\s*(?:OUTER\s+)?JOIN\s+", re.IGNORECASE
)

_COLREF = re.compile(
    r'(?:"([^"]+)"|\[([^\]]+)\]|(\w+))\s*\.\s*(?:"([^"]+)"|\[([^\]]+)\]|(\w+))'
)


def _strip_comments(sql: str) -> str:
    sql = _COMMENT_BLOCK.sub(" ", sql)
    sql = _COMMENT_LINE.sub(" ", sql)
    return sql


def _unquote(ident: str) -> str:
    ident = ident.strip()
    if ident.startswith('"') and ident.endswith('"'):
        return ident[1:-1].replace('""', '"')
    if ident.startswith("[") and ident.endswith("]"):
        return ident[1:-1].replace("]]", "]")
    return ident


def _norm_rel(rel: str) -> str:
    return ".".join(_unquote(p) for p in _split_qualified(rel))


def _split_qualified(rel: str) -> list[str]:
    """Divide "a.b" respetando comillas y corchetes ([esquema.raro].[tabla])."""
    parts: list[str] = []
    buf = ""
    in_q = False
    in_b = False
    for ch in rel:
        if ch == '"' and not in_b:
            in_q = not in_q
            buf += ch
        elif ch == "[" and not in_q:
            in_b = True
            buf += ch
        elif ch == "]" and in_b:
            in_b = False
            buf += ch
        elif ch == "." and not in_q and not in_b:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    parts.append(buf)
    return parts


def _find_top_level(sql: str, pattern: re.Pattern) -> int | None:
    """Primera coincidencia de `pattern` a profundidad de paréntesis 0."""
    depth = 0
    in_s = False  # comilla simple
    in_d = False  # comilla doble
    in_b = False  # corchetes de T-SQL
    i = 0
    while i < len(sql):
        ch = sql[i]
        if in_s:
            if ch == "'":
                in_s = False
        elif in_d:
            if ch == '"':
                in_d = False
        elif in_b:
            if ch == "]":
                in_b = False
        elif ch == "'":
            in_s = True
        elif ch == '"':
            in_d = True
        elif ch == "[":
            in_b = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            m = pattern.match(sql, i)
            if m:
                return i
        i += 1
    return None


def _iter_top_level(sql: str, pattern: re.Pattern):
    """Todas las posiciones de `pattern` a nivel superior (depth 0)."""
    depth = 0
    in_s = in_d = in_b = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if in_s:
            if ch == "'":
                in_s = False
        elif in_d:
            if ch == '"':
                in_d = False
        elif in_b:
            if ch == "]":
                in_b = False
        elif ch == "'":
            in_s = True
        elif ch == '"':
            in_d = True
        elif ch == "[":
            in_b = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            m = pattern.match(sql, i)
            if m:
                yield m
                i = m.end()
                continue
        i += 1


_FROM_KW = re.compile(r"\bFROM\b", re.IGNORECASE)
_SELECT_KW = re.compile(r"\bSELECT\b", re.IGNORECASE)
_TAIL_KW = re.compile(r"\b(?:%s)\b" % "|".join(_TAIL_KEYWORDS), re.IGNORECASE)


def parse_query_sql(sql: str, known: dict[str, str]) -> QueryModel:
    """Analiza una sentencia SELECT y reconstruye el modelo del diagrama.

    `known` mapea claves completas ("schema.tabla") y nombres sueltos no
    ambiguos a la clave completa, tal como hace la introspección de vistas.
    """
    model = QueryModel()
    clean = _strip_comments(sql).strip().rstrip(";")
    if not clean:
        return model

    def resolve(rel: str) -> str | None:
        r = _norm_rel(rel)
        return known.get(r) or known.get(r.split(".")[-1])

    from_m = _find_top_level(clean, _FROM_KW)
    if from_m is None:
        model.warnings.append("La consulta no tiene cláusula FROM analizable.")
        return model

    select_m = _find_top_level(clean, _SELECT_KW)
    if select_m is not None and select_m < from_m:
        model.select_sql = clean[select_m + len("SELECT") : from_m].strip() or None

    # Fin del encadenado FROM/JOIN: primera palabra de cola a nivel superior.
    rest = clean[from_m + len("FROM") :]
    tail_rel = _find_top_level(rest, _TAIL_KW)
    if tail_rel is not None:
        chain = rest[:tail_rel]
        model.tail_sql = rest[tail_rel:].strip() or None
    else:
        chain = rest

    chain = chain.strip()

    # Posiciones de los JOIN a nivel superior dentro del encadenado.
    join_positions = [(m.start(), m) for m in _iter_top_level(chain, _JOIN_HEAD)]

    alias_map: dict[str, str] = {}  # alias/nombre -> clave completa

    def register(rel: str, alias: str | None) -> tuple[str | None, str]:
        key = resolve(rel)
        bare = _norm_rel(rel).split(".")[-1]
        if alias and _unquote(alias).lower() in _ALIAS_STOP:
            alias = None  # era una palabra clave, no un alias real
        final_alias = _unquote(alias) if alias else bare
        if key:
            if key not in model.tables:
                model.tables.append(key)
            model.aliases.setdefault(key, final_alias)
            alias_map[final_alias] = key
            alias_map.setdefault(bare, key)
        else:
            raw = _norm_rel(rel)
            if raw not in model.unresolved:
                model.unresolved.append(raw)
                model.warnings.append(
                    f"Tabla no encontrada en el snapshot: {raw}."
                )
        return key, final_alias

    # Tabla base (desde FROM hasta el primer JOIN, o hasta el final).
    base_end = join_positions[0][0] if join_positions else len(chain)
    base_seg = chain[:base_end].strip().rstrip(",")
    base_key: str | None = None
    tref = _TABLE_REF.match(base_seg)
    if tref:
        base_key, _ = register(tref.group("rel"), tref.group("alias"))
    prev_key = base_key

    # Cada segmento de JOIN.
    for idx, (start, m) in enumerate(join_positions):
        seg_end = join_positions[idx + 1][0] if idx + 1 < len(join_positions) else len(chain)
        jtype = _norm_join_type(m.group("type"))
        after = chain[m.end() : seg_end].strip()
        tref = _TABLE_REF.match(after)
        if not tref:
            continue
        key, alias = register(tref.group("rel"), tref.group("alias"))
        alias_grp = tref.group("alias")
        # Si lo capturado como alias es una palabra clave (p. ej. ON), no lo
        # consumimos: el texto del ON empieza ahí.
        if alias_grp and _unquote(alias_grp).lower() in _ALIAS_STOP:
            consumed_end = tref.end("rel")
        else:
            consumed_end = tref.end()
        on_text = after[consumed_end:].strip()
        on_m = re.match(r"ON\b(?P<on>.*)$", on_text, re.IGNORECASE | re.DOTALL)
        on = on_m.group("on") if on_m else ""
        if key is None:
            prev_key = key or prev_key
            continue
        if jtype == "CROSS JOIN" or not on.strip():
            # CROSS JOIN (o join sin ON legible): sin columnas.
            model.joins.append(
                Join(source=prev_key or key, target=key, join_type="CROSS JOIN")
            )
            prev_key = key
            continue
        other, src_cols, tgt_cols = _analyze_on(on, alias, key, alias_map)
        if other and other != key:
            model.joins.append(
                Join(
                    source=other,
                    target=key,
                    join_type=jtype,
                    source_columns=src_cols,
                    target_columns=tgt_cols,
                )
            )
        else:
            model.warnings.append(
                f"No se pudo determinar el otro extremo del ON de {alias}; "
                "se creó un join sin columnas."
            )
            model.joins.append(
                Join(source=prev_key or key, target=key, join_type=jtype)
            )
        prev_key = key
    return model


def _analyze_on(
    on: str, alias: str, key: str, alias_map: dict[str, str]
) -> tuple[str | None, list[str], list[str]]:
    """Extrae (otro_extremo, columnas_source, columnas_target) de un ON.

    El "otro extremo" es la primera clave, distinta de la tabla del join, cuyo
    alias aparece en el ON. Las columnas se agrupan por a qué lado pertenecen.
    """
    other: str | None = None
    for q1, b1, w1, _q2, _b2, _w2 in _COLREF.findall(on):
        al = q1 or b1 or w1
        mapped = alias_map.get(al)
        if al != alias and mapped and mapped != key:
            other = mapped
            break
    if other is None:
        return None, [], []

    src_cols: list[str] = []
    tgt_cols: list[str] = []
    for q1, b1, w1, q2, b2, w2 in _COLREF.findall(on):
        al = q1 or b1 or w1
        col = q2 or b2 or w2
        mapped = alias_map.get(al)
        if mapped == other and col not in src_cols:
            src_cols.append(col)
        elif (mapped == key or al == alias) and col not in tgt_cols:
            tgt_cols.append(col)
    return other, src_cols, tgt_cols
