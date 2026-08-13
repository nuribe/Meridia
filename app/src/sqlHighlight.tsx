/**
 * Resaltado de sintaxis SQL, compartido por toda la app.
 *
 * Vivía dentro de `Explorer.tsx`, que era su único consumidor. Al necesitarlo
 * también la vista «Actividad IA» —donde el objetivo de un `run_select` es SQL
 * y se leía como texto plano— se saca aquí en vez de duplicarlo: dos copias
 * divergen en cuanto alguien añade una palabra clave en una sola de ellas.
 *
 * Los nueve colores son tokens del tema (`--pg-sql-*`), no una paleta clara y
 * otra oscura: cada tema afina los suyos y el resaltado deja de depender de si
 * el tema es claro u oscuro.
 */
import React from "react";


const SQL_KEYWORDS = new Set([
  "select", "from", "where", "join", "left", "right", "inner", "outer", "full",
  "cross", "lateral", "on", "using", "group", "by", "order", "having", "union",
  "intersect", "except", "all", "distinct", "as", "and", "or", "not", "null",
  "case", "when", "then", "else", "end", "limit", "offset", "with", "recursive",
  "exists", "in", "is", "like", "ilike", "similar", "between", "asc", "desc",
  "nulls", "first", "last", "true", "false", "cast", "over", "partition",
  "window", "filter", "values", "returning",
  "insert", "into", "update", "delete", "set", "create", "replace", "function",
  "procedure", "returns", "return", "language", "declare", "begin", "loop",
  "while", "foreach", "raise", "notice", "exception", "perform", "execute",
  "immutable", "stable", "volatile", "strict", "security", "definer", "invoker",
  "cost", "setof", "out", "inout", "variadic", "default", "call", "commit",
  "rollback", "constant", "trigger", "before", "after", "each", "row",
  "if", "elsif", "then", "get", "stacked", "diagnostics", "others", "sqlerrm",
  // T-SQL. Meridia también habla con SQL Server, y sin estas un `SELECT TOP 10`
  // se pintaba a medias: la palabra que marca el límite quedaba sin resaltar.
  "top", "go", "output", "apply", "pivot", "unpivot", "merge", "matched",
  "try", "catch", "throw", "iif", "identity", "nolock", "rowlock", "readonly",
  "cross_apply", "outer_apply", "percent", "ties",
]);

const SQL_TYPES = new Set([
  "integer", "bigint", "smallint", "int", "int2", "int4", "int8", "serial",
  "bigserial", "text", "boolean", "bool", "numeric", "decimal", "character",
  "varying", "varchar", "char", "timestamp", "timestamptz", "date", "time",
  "timetz", "interval", "double", "precision", "real", "float4", "float8",
  "money", "json", "jsonb", "uuid", "bytea", "xml", "inet", "cidr", "macaddr",
  "bit", "void", "record", "anyelement", "anyarray", "regclass", "name", "oid",
  "tsvector", "tsquery", "zone", "without",
  // Tipos de SQL Server.
  "nvarchar", "nchar", "ntext", "datetime2", "smalldatetime", "datetimeoffset",
  "uniqueidentifier", "varbinary", "binary", "tinyint", "smallmoney", "image",
  "sysname", "sql_variant", "hierarchyid", "max",
]);

// Resaltado de sintaxis. Antes había dos paletas fijas en este archivo, una
// "clara" y otra "oscura", elegidas leyendo el modo de Bootstrap. Ahora los
// nueve colores son tokens: Océano y Violeta pueden afinar los suyos y el
// resaltado deja de estar acoplado a si el tema es claro u oscuro.
//
// Se mantiene la forma de objeto (y no `className`) porque highlightSql
// devuelve nodos con estilos en línea.
export interface SqlPalette {
  comment: string;
  string: string;
  identQ: string;
  cast: string;
  number: string;
  keyword: string;
  type: string;
  func: string;
  dollar: string;
}

const SQL_PALETTE: SqlPalette = {
  comment: "var(--pg-sql-comment)",
  string: "var(--pg-sql-string)",
  identQ: "var(--pg-sql-ident)",
  cast: "var(--pg-sql-cast)",
  number: "var(--pg-sql-number)",
  keyword: "var(--pg-sql-keyword)",
  type: "var(--pg-sql-type)",
  func: "var(--pg-sql-func)",
  dollar: "var(--pg-sql-dollar)",
};

/** Se conserva por compatibilidad con las llamadas existentes. */
export function sqlPalette(): SqlPalette {
  return SQL_PALETTE;
}

export function highlightSql(sql: string, p: SqlPalette = SQL_PALETTE): React.ReactNode[] {
  const parts = sql.split(
    /(--[^\n]*|'(?:[^']|'')*'|"[^"]*"|\$\w*\$|::\w+|\b[\w$]+\b)/g
  );
  return parts.map((tok, i) => {
    if (!tok) return null;
    if (tok.startsWith("--")) {
      return <span key={i} style={{ color: p.comment, fontStyle: "italic" }}>{tok}</span>;
    }
    if (tok.startsWith("'")) {
      return <span key={i} style={{ color: p.string }}>{tok}</span>;
    }
    if (tok.startsWith('"')) {
      return <span key={i} style={{ color: p.identQ }}>{tok}</span>;
    }
    if (/^\$\w*\$$/.test(tok)) {
      return <span key={i} style={{ color: p.dollar, fontWeight: 600 }}>{tok}</span>;
    }
    if (tok.startsWith("::")) {
      return <span key={i} style={{ color: p.cast }}>{tok}</span>;
    }
    if (/^\d+(\.\d+)?$/.test(tok)) {
      return <span key={i} style={{ color: p.number }}>{tok}</span>;
    }
    const lower = tok.toLowerCase();
    if (SQL_KEYWORDS.has(lower)) {
      return <span key={i} style={{ color: p.keyword, fontWeight: 600 }}>{tok}</span>;
    }
    if (SQL_TYPES.has(lower)) {
      return <span key={i} style={{ color: p.type }}>{tok}</span>;
    }
    // Llamada a función: identificador seguido de "(" en el tramo siguiente
    const next = parts[i + 1];
    if (/^[a-z_][\w$]*$/i.test(tok) && next && next.trimStart().startsWith("(")) {
      return <span key={i} style={{ color: p.func }}>{tok}</span>;
    }
    return tok;
  });
}
