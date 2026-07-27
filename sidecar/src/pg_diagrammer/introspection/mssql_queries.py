"""Consultas en bloque sobre los catálogos sys.* de SQL Server.

Espejo de queries.py (pg_catalog): un número fijo de queries por base,
independiente del número de tablas (sin N+1, misma meta de rendimiento).
Los comentarios se leen de la extended property estándar 'MS_Description'.
"""

# Schemas de sistema/roles que no aportan al diagrama.
SCHEMA_FILTER = """
      s.name NOT IN ('sys', 'INFORMATION_SCHEMA', 'guest')
  AND s.name NOT LIKE 'db[_]%'
"""

SCHEMAS = f"""
SELECT s.name,
       CAST(ep.value AS NVARCHAR(MAX)) AS comment
FROM sys.schemas s
LEFT JOIN sys.extended_properties ep
       ON ep.class = 3 AND ep.major_id = s.schema_id AND ep.name = 'MS_Description'
WHERE {SCHEMA_FILTER}
ORDER BY s.name
"""

# Tablas y vistas de usuario, con filas estimadas (sys.partitions) y, para
# vistas, el SQL completo del módulo (sys.sql_modules).
RELATIONS = """
SELECT o.object_id,
       SCHEMA_NAME(o.schema_id) AS schema_name,
       o.name,
       RTRIM(o.type) AS type,
       p.rows AS estimated_rows,
       CAST(ep.value AS NVARCHAR(MAX)) AS comment,
       sm.definition
FROM sys.objects o
LEFT JOIN (
    SELECT object_id, SUM(rows) AS rows
    FROM sys.partitions
    WHERE index_id IN (0, 1)
    GROUP BY object_id
) p ON p.object_id = o.object_id
LEFT JOIN sys.extended_properties ep
       ON ep.class = 1 AND ep.major_id = o.object_id AND ep.minor_id = 0
      AND ep.name = 'MS_Description'
LEFT JOIN sys.sql_modules sm ON sm.object_id = o.object_id
WHERE o.type IN ('U', 'V')
  AND o.is_ms_shipped = 0
  AND SCHEMA_NAME(o.schema_id) NOT IN ('sys', 'INFORMATION_SCHEMA', 'guest')
ORDER BY SCHEMA_NAME(o.schema_id), o.name
"""

# El tipo se devuelve descompuesto (nombre + longitudes) y se formatea en
# Python: evita un CASE gigante en SQL.
COLUMNS = """
SELECT c.object_id, c.column_id, c.name,
       t.name AS type_name, c.max_length, c.precision, c.scale,
       c.is_nullable,
       dc.definition AS default_expr,
       CAST(ep.value AS NVARCHAR(MAX)) AS comment
FROM sys.columns c
JOIN sys.objects o
  ON o.object_id = c.object_id AND o.type IN ('U', 'V') AND o.is_ms_shipped = 0
JOIN sys.types t ON t.user_type_id = c.user_type_id
LEFT JOIN sys.default_constraints dc
       ON dc.parent_object_id = c.object_id AND dc.parent_column_id = c.column_id
LEFT JOIN sys.extended_properties ep
       ON ep.class = 1 AND ep.major_id = c.object_id AND ep.minor_id = c.column_id
      AND ep.name = 'MS_Description'
ORDER BY c.object_id, c.column_id
"""

# PK y UNIQUE: una fila por columna, se agrupan en Python (kc.type: PK | UQ).
KEY_CONSTRAINTS = """
SELECT kc.name, RTRIM(kc.type) AS type, kc.parent_object_id,
       ic.column_id, ic.key_ordinal
FROM sys.key_constraints kc
JOIN sys.objects o
  ON o.object_id = kc.parent_object_id AND o.is_ms_shipped = 0
JOIN sys.index_columns ic
  ON ic.object_id = kc.parent_object_id AND ic.index_id = kc.unique_index_id
WHERE ic.is_included_column = 0
ORDER BY kc.parent_object_id, kc.name, ic.key_ordinal
"""

CHECK_CONSTRAINTS = """
SELECT cc.name, cc.parent_object_id, cc.definition
FROM sys.check_constraints cc
JOIN sys.objects o
  ON o.object_id = cc.parent_object_id AND o.is_ms_shipped = 0
ORDER BY cc.parent_object_id, cc.name
"""

# FKs: una fila por par de columnas, se agrupan en Python.
FOREIGN_KEYS = """
SELECT fk.name, fk.parent_object_id, fk.referenced_object_id,
       fk.update_referential_action_desc,
       fk.delete_referential_action_desc,
       fkc.parent_column_id, fkc.referenced_column_id, fkc.constraint_column_id
FROM sys.foreign_keys fk
JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
WHERE fk.is_ms_shipped = 0
ORDER BY fk.parent_object_id, fk.name, fkc.constraint_column_id
"""

INDEXES = """
SELECT i.object_id, i.name, i.is_unique, LOWER(i.type_desc) AS method,
       ic.column_id, ic.key_ordinal
FROM sys.indexes i
JOIN sys.objects o
  ON o.object_id = i.object_id AND o.type = 'U' AND o.is_ms_shipped = 0
JOIN sys.index_columns ic
  ON ic.object_id = i.object_id AND ic.index_id = i.index_id
WHERE i.index_id > 0
  AND i.is_hypothetical = 0
  AND i.name IS NOT NULL
  AND ic.is_included_column = 0
ORDER BY i.object_id, i.name, ic.key_ordinal
"""

# Procedimientos y funciones de usuario (P = proc; FN/IF/TF = funciones).
ROUTINES = """
SELECT o.object_id, SCHEMA_NAME(o.schema_id) AS schema_name, o.name,
       RTRIM(o.type) AS type, sm.definition
FROM sys.objects o
LEFT JOIN sys.sql_modules sm ON sm.object_id = o.object_id
WHERE o.type IN ('P', 'FN', 'IF', 'TF')
  AND o.is_ms_shipped = 0
  AND SCHEMA_NAME(o.schema_id) NOT IN ('sys', 'INFORMATION_SCHEMA', 'guest')
ORDER BY SCHEMA_NAME(o.schema_id), o.name
"""

ROUTINE_PARAMS = """
SELECT p.object_id, p.parameter_id, p.name, TYPE_NAME(p.user_type_id) AS type_name
FROM sys.parameters p
JOIN sys.objects o
  ON o.object_id = p.object_id AND o.type IN ('P', 'FN', 'IF', 'TF')
 AND o.is_ms_shipped = 0
WHERE p.parameter_id > 0
ORDER BY p.object_id, p.parameter_id
"""

# Vistas que dependen de cada tabla, según dependencias registradas
# (equivalente a pg_rewrite/pg_depend en PostgreSQL).
VIEW_DEPS = """
SELECT DISTINCT
       SCHEMA_NAME(v.schema_id) AS view_schema,
       v.name AS view_name,
       SCHEMA_NAME(t.schema_id) AS table_schema,
       t.name AS table_name
FROM sys.sql_expression_dependencies d
JOIN sys.objects v ON v.object_id = d.referencing_id AND v.type = 'V'
JOIN sys.objects t ON t.object_id = d.referenced_id AND t.type IN ('U', 'V')
WHERE d.referencing_id <> d.referenced_id
  AND v.is_ms_shipped = 0
ORDER BY SCHEMA_NAME(t.schema_id), t.name, SCHEMA_NAME(v.schema_id), v.name
"""
