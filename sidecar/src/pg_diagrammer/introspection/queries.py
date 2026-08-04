"""Consultas en bloque sobre pg_catalog.

Cinco queries por base de datos, independientes del número de tablas
(evita N+1 y cumple la meta de 500 tablas < 15 s).
"""

# Excluye schemas de sistema; los temporales se filtran por patrón.
SCHEMA_FILTER = """
      n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
  AND n.nspname NOT LIKE 'pg\\_temp\\_%'
  AND n.nspname NOT LIKE 'pg\\_toast\\_temp\\_%'
"""

SCHEMAS = f"""
SELECT n.nspname,
       pg_catalog.obj_description(n.oid, 'pg_namespace') AS comment
FROM pg_catalog.pg_namespace n
WHERE {SCHEMA_FILTER}
ORDER BY n.nspname
"""

RELATIONS = f"""
SELECT c.oid, n.nspname, c.relname, c.relkind,
       c.reltuples::bigint AS estimated_rows,
       pg_catalog.obj_description(c.oid, 'pg_class') AS comment,
       CASE WHEN c.relkind IN ('v', 'm')
            THEN pg_catalog.pg_get_viewdef(c.oid, true)
       END AS definition
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND {SCHEMA_FILTER}
ORDER BY n.nspname, c.relname
"""

COLUMNS = f"""
SELECT a.attrelid, a.attnum, a.attname,
       pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
       NOT a.attnotnull AS is_nullable,
       pg_catalog.pg_get_expr(d.adbin, d.adrelid) AS default_expr,
       pg_catalog.col_description(a.attrelid, a.attnum) AS comment
FROM pg_catalog.pg_attribute a
JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_catalog.pg_attrdef d
       ON d.adrelid = a.attrelid AND d.adnum = a.attnum
WHERE a.attnum > 0
  AND NOT a.attisdropped
  AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND {SCHEMA_FILTER}
ORDER BY a.attrelid, a.attnum
"""

CONSTRAINTS = f"""
SELECT con.conname, con.contype, con.conrelid, con.conkey,
       con.confrelid, con.confkey, con.confupdtype, con.confdeltype,
       pg_catalog.pg_get_constraintdef(con.oid, true) AS definition
FROM pg_catalog.pg_constraint con
JOIN pg_catalog.pg_class c ON c.oid = con.conrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE con.contype IN ('p', 'u', 'f', 'c')
  AND {SCHEMA_FILTER}
ORDER BY con.conrelid, con.conname
"""

INDEXES = f"""
SELECT i.indrelid, ic.relname AS index_name, i.indisunique,
       am.amname AS method, i.indkey::text AS attnums
FROM pg_catalog.pg_index i
JOIN pg_catalog.pg_class ic ON ic.oid = i.indexrelid
JOIN pg_catalog.pg_class tc ON tc.oid = i.indrelid
JOIN pg_catalog.pg_namespace n ON n.oid = tc.relnamespace
JOIN pg_catalog.pg_am am ON am.oid = ic.relam
WHERE {SCHEMA_FILTER}
ORDER BY i.indrelid, ic.relname
"""

# --- Variantes por tabla, para el refresh granular (menú «Actualizar») ---
# Mismas columnas que sus equivalentes en bloque; el filtro es por objeto.

RELATION_ONE = """
SELECT c.oid, n.nspname, c.relname, c.relkind,
       c.reltuples::bigint AS estimated_rows,
       pg_catalog.obj_description(c.oid, 'pg_class') AS comment,
       CASE WHEN c.relkind IN ('v', 'm')
            THEN pg_catalog.pg_get_viewdef(c.oid, true)
       END AS definition
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND n.nspname = %s AND c.relname = %s
"""

COLUMNS_ONE = """
SELECT a.attrelid, a.attnum, a.attname,
       pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
       NOT a.attnotnull AS is_nullable,
       pg_catalog.pg_get_expr(d.adbin, d.adrelid) AS default_expr,
       pg_catalog.col_description(a.attrelid, a.attnum) AS comment
FROM pg_catalog.pg_attribute a
LEFT JOIN pg_catalog.pg_attrdef d
       ON d.adrelid = a.attrelid AND d.adnum = a.attnum
WHERE a.attrelid = %s
  AND a.attnum > 0
  AND NOT a.attisdropped
ORDER BY a.attnum
"""

CONSTRAINTS_ONE = """
SELECT con.conname, con.contype, con.conrelid, con.conkey,
       con.confrelid, con.confkey, con.confupdtype, con.confdeltype,
       pg_catalog.pg_get_constraintdef(con.oid, true) AS definition
FROM pg_catalog.pg_constraint con
WHERE con.conrelid = %s
  AND con.contype IN ('p', 'u', 'f', 'c')
ORDER BY con.conname
"""

INDEXES_ONE = """
SELECT i.indrelid, ic.relname AS index_name, i.indisunique,
       am.amname AS method, i.indkey::text AS attnums
FROM pg_catalog.pg_index i
JOIN pg_catalog.pg_class ic ON ic.oid = i.indexrelid
JOIN pg_catalog.pg_am am ON am.oid = ic.relam
WHERE i.indrelid = %s
ORDER BY ic.relname
"""

# Nombres de las relaciones referenciadas por las FKs de la tabla refrescada.
REF_TABLE_NAMES = """
SELECT c.oid, n.nspname, c.relname
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE c.oid = ANY(%s)
"""

# Columnas (attnum → nombre) de esas relaciones referenciadas.
REF_TABLE_COLUMNS = """
SELECT a.attrelid, a.attnum, a.attname
FROM pg_catalog.pg_attribute a
WHERE a.attrelid = ANY(%s)
  AND a.attnum > 0
  AND NOT a.attisdropped
"""

# Funciones y procedimientos de usuario (excluye los que pertenecen a extensiones).
ROUTINES = f"""
SELECT n.nspname, p.proname, p.prokind,
       l.lanname,
       pg_catalog.pg_get_function_identity_arguments(p.oid) AS args,
       p.prosrc,
       p.proconfig
FROM pg_catalog.pg_proc p
JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
JOIN pg_catalog.pg_language l ON l.oid = p.prolang
WHERE p.prokind IN ('f', 'p')
  AND NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_depend d
      WHERE d.objid = p.oid AND d.deptype = 'e'
  )
  AND {SCHEMA_FILTER}
ORDER BY n.nspname, p.proname
"""

# Vistas (normales y materializadas) que dependen de cada tabla, según las
# dependencias reales registradas en pg_rewrite/pg_depend (no matching de texto).
VIEW_DEPS = """
SELECT DISTINCT
       vn.nspname AS view_schema,
       v.relname  AS view_name,
       tn.nspname AS table_schema,
       t.relname  AS table_name
FROM pg_catalog.pg_depend d
JOIN pg_catalog.pg_rewrite r ON r.oid = d.objid
JOIN pg_catalog.pg_class v ON v.oid = r.ev_class
JOIN pg_catalog.pg_class t ON t.oid = d.refobjid
JOIN pg_catalog.pg_namespace vn ON vn.oid = v.relnamespace
JOIN pg_catalog.pg_namespace tn ON tn.oid = t.relnamespace
WHERE d.classid = 'pg_rewrite'::regclass
  AND d.refclassid = 'pg_class'::regclass
  AND d.deptype = 'n'
  AND v.relkind IN ('v', 'm')
  AND t.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND v.oid <> t.oid
  AND vn.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
  AND tn.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
ORDER BY tn.nspname, t.relname, vn.nspname, v.relname
"""
