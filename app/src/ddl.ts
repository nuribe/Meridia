/**
 * Generación del script DDL a partir del modo edición del detalle de tabla.
 *
 * La app NUNCA ejecuta este SQL por sí sola: el script se abre en una pestaña
 * de consulta (única vía de escritura, sujeta a «Permitir escritura») o se
 * copia al portapapeles. Aquí solo se construye el texto.
 */
import type { ColumnInfo, DbEngine, TableDetail } from "./api/client";

/** Fila del editor de columnas: original (o null si es nueva) + valores editados. */
export interface DraftColumn {
  /** Columna original; null cuando la fila se añadió en la edición. */
  orig: ColumnInfo | null;
  name: string;
  data_type: string;
  nullable: boolean;
  /** Expresión DEFAULT como texto; "" = sin default. */
  def: string;
  /** Comentario; "" = sin comentario. */
  comment: string;
  /** Marcada para eliminar (solo aplica a columnas existentes). */
  deleted: boolean;
}

const PG_SAFE = /^[a-z_][a-z0-9_$]*$/;

/** Identificador PostgreSQL: solo se entrecomilla si hace falta. */
function qpg(id: string): string {
  return PG_SAFE.test(id) ? id : `"${id.replace(/"/g, '""')}"`;
}

/** Identificador SQL Server, siempre entre corchetes. */
function qms(id: string): string {
  return `[${id.replace(/]/g, "]]")}]`;
}

/** Literal de texto SQL (comillas simples escapadas). */
function lit(s: string): string {
  return `'${s.replace(/'/g, "''")}'`;
}

/** Normaliza una expresión DEFAULT o comentario para comparar ("" = ausente). */
function normDef(s: string | null | undefined): string {
  return (s ?? "").trim();
}

/** Extended property MS_Description de SQL Server (tabla o columna). */
function msComment(
  action: "add" | "update" | "drop",
  schema: string,
  table: string,
  column: string | null,
  value: string
): string {
  const proc = {
    add: "sp_addextendedproperty",
    update: "sp_updateextendedproperty",
    drop: "sp_dropextendedproperty",
  }[action];
  const parts = [
    `EXEC ${proc} @name = N'MS_Description'`,
    ...(action === "drop" ? [] : [`     @value = N${lit(value)}`]),
    `     @level0type = N'SCHEMA', @level0name = N${lit(schema)}`,
    `     @level1type = N'TABLE',  @level1name = N${lit(table)}`,
    ...(column !== null
      ? [`     @level2type = N'COLUMN', @level2name = N${lit(column)}`]
      : []),
  ];
  return parts.join(",\n") + ";";
}

/**
 * Construye el script con los cambios (borrador vs tabla original).
 * Devuelve "" si no hay ninguno.
 */
export function buildAlterScript(
  engine: DbEngine,
  table: TableDetail,
  cols: DraftColumn[],
  tableComment: string
): string {
  const stmts: string[] = [];
  const pg = engine !== "sqlserver";
  const schema = table.schema_name;
  const tname = table.name;
  const t = pg ? `${qpg(schema)}.${qpg(tname)}` : `${qms(schema)}.${qms(tname)}`;
  const qcol = (c: string) => (pg ? qpg(c) : qms(c));

  // 1) Renombrados primero: el resto de sentencias usa ya el nombre nuevo.
  for (const c of cols) {
    if (c.orig && !c.deleted && c.name !== c.orig.name) {
      stmts.push(
        pg
          ? `ALTER TABLE ${t} RENAME COLUMN ${qpg(c.orig.name)} TO ${qpg(c.name)};`
          : `EXEC sp_rename ${lit(`${schema}.${tname}.${c.orig.name}`)}, ${lit(c.name)}, 'COLUMN';`
      );
    }
  }

  // 2) Columnas eliminadas.
  for (const c of cols) {
    if (c.orig && c.deleted) {
      if (!pg && normDef(c.orig.default)) {
        stmts.push(
          `-- ${qcol(c.orig.name)} tiene DEFAULT: elimina antes su constraint (sys.default_constraints).`
        );
      }
      stmts.push(`ALTER TABLE ${t} DROP COLUMN ${qcol(c.orig.name)};`);
    }
  }

  // 3) Columnas nuevas (con default, nulabilidad y comentario incluidos).
  for (const c of cols) {
    if (c.orig || c.deleted) continue;
    const def = normDef(c.def);
    if (pg) {
      stmts.push(
        `ALTER TABLE ${t} ADD COLUMN ${qpg(c.name)} ${c.data_type}` +
          (def ? ` DEFAULT ${def}` : "") +
          (c.nullable ? "" : " NOT NULL") +
          ";"
      );
    } else {
      stmts.push(
        `ALTER TABLE ${t} ADD ${qms(c.name)} ${c.data_type}` +
          (def ? ` DEFAULT ${def}` : "") +
          (c.nullable ? " NULL" : " NOT NULL") +
          ";"
      );
    }
    if (c.comment.trim()) {
      stmts.push(
        pg
          ? `COMMENT ON COLUMN ${t}.${qpg(c.name)} IS ${lit(c.comment.trim())};`
          : msComment("add", schema, tname, c.name, c.comment.trim())
      );
    }
  }

  // 4) Modificaciones de columnas existentes.
  for (const c of cols) {
    if (!c.orig || c.deleted) continue;
    const typeChanged = c.data_type.trim() !== c.orig.data_type;
    const nullChanged = c.nullable !== c.orig.is_nullable;
    const defChanged = normDef(c.def) !== normDef(c.orig.default);
    const commentChanged = c.comment.trim() !== (c.orig.comment ?? "").trim();
    const col = qcol(c.name);

    if (pg) {
      if (typeChanged) {
        stmts.push(
          `ALTER TABLE ${t} ALTER COLUMN ${col} TYPE ${c.data_type.trim()};` +
            ` -- añade USING ${col}::${c.data_type.trim()} si el cast no es implícito`
        );
      }
      if (nullChanged) {
        stmts.push(
          `ALTER TABLE ${t} ALTER COLUMN ${col} ${c.nullable ? "DROP NOT NULL" : "SET NOT NULL"};`
        );
      }
      if (defChanged) {
        const def = normDef(c.def);
        stmts.push(
          def
            ? `ALTER TABLE ${t} ALTER COLUMN ${col} SET DEFAULT ${def};`
            : `ALTER TABLE ${t} ALTER COLUMN ${col} DROP DEFAULT;`
        );
      }
    } else {
      if (typeChanged || nullChanged) {
        stmts.push(
          `ALTER TABLE ${t} ALTER COLUMN ${col} ${c.data_type.trim()} ${c.nullable ? "NULL" : "NOT NULL"};`
        );
      }
      if (defChanged) {
        const def = normDef(c.def);
        if (normDef(c.orig.default)) {
          stmts.push(
            `-- El DEFAULT actual de ${col} es un constraint: elimínalo antes (sys.default_constraints).`
          );
        }
        if (def) {
          stmts.push(
            `ALTER TABLE ${t} ADD CONSTRAINT ${qms(`DF_${tname}_${c.name}`)} DEFAULT ${def} FOR ${col};`
          );
        }
      }
    }

    if (commentChanged) {
      const v = c.comment.trim();
      if (pg) {
        stmts.push(`COMMENT ON COLUMN ${t}.${col} IS ${v ? lit(v) : "NULL"};`);
      } else {
        const had = (c.orig.comment ?? "").trim() !== "";
        stmts.push(
          msComment(v ? (had ? "update" : "add") : "drop", schema, tname, c.name, v)
        );
      }
    }
  }

  // 5) Comentario de la tabla.
  if (tableComment.trim() !== (table.comment ?? "").trim()) {
    const v = tableComment.trim();
    if (pg) {
      stmts.push(`COMMENT ON TABLE ${t} IS ${v ? lit(v) : "NULL"};`);
    } else {
      const had = (table.comment ?? "").trim() !== "";
      stmts.push(msComment(v ? (had ? "update" : "add") : "drop", schema, tname, null, v));
    }
  }

  if (stmts.length === 0) return "";
  return (
    `-- Cambios sobre ${schema}.${tname} generados por Meridia.\n` +
    `-- Revisa el script antes de ejecutarlo.\n\n` +
    stmts.join("\n")
  );
}

/** Validación mínima del borrador: nombres presentes, sin duplicados, tipo en las nuevas. */
export function draftProblems(cols: DraftColumn[]): string | null {
  const seen = new Set<string>();
  for (const c of cols) {
    if (c.deleted) continue;
    const n = c.name.trim().toLowerCase();
    if (!n) return "Hay columnas sin nombre.";
    if (seen.has(n)) return `Nombre de columna repetido: ${c.name.trim()}`;
    seen.add(n);
    if (!c.data_type.trim()) return `La columna ${c.name.trim()} no tiene tipo.`;
  }
  return null;
}
