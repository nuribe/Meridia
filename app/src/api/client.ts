/**
 * Cliente HTTP hacia el sidecar Python.
 * Obtiene {port, token} del shell Tauri (comando sidecar_info) y añade
 * el header X-Session-Token a toda petición /api/v1.
 */
import { invoke } from "@tauri-apps/api/core";

export interface SidecarInfo {
  port: number;
  token: string;
}

export interface ApiError {
  code: string;
  message: string;
  hint?: string | null;
  retriable: boolean;
}

export type DbEngine = "postgresql" | "sqlserver";

/** Método de autenticación (solo relevante para SQL Server). */
export type AuthMethod = "sql" | "windows";

export interface Profile {
  id: string;
  name: string;
  host: string;
  port: number;
  user: string;
  ssl_mode: string;
  dbname: string;
  engine: DbEngine;
  auth_method: AuthMethod;
  /** Permite DDL/DML desde el editor. Apagado = la conexión es un visor. */
  allow_writes: boolean;
}

export interface ProfileInput {
  name: string;
  host: string;
  port: number;
  user: string;
  password: string;
  dbname: string;
  ssl_mode?: string;
  engine?: DbEngine;
  auth_method?: AuthMethod;
  allow_writes?: boolean;
}

/** Resultado de UNA sentencia del script. */
export interface StatementResult {
  /** La sentencia tal cual se ejecutó, para rotular su pestaña. */
  statement: string;
  columns: string[];
  rows: unknown[][];
  row_count: number;
  /** Filas modificadas por un DML sin resultados; null cuando hubo lectura. */
  affected_rows: number | null;
  truncated: boolean;
  elapsed_ms: number;
}

/** Respuesta de ejecutar un script: un bloque por sentencia. */
export interface QueryRun {
  ok: boolean;
  results: StatementResult[];
  /** Error de la sentencia que cortó la ejecución; null si fueron todas bien. */
  error: ApiError | null;
  /** Índice de la sentencia que falló dentro del script. */
  error_index: number | null;
  elapsed_ms: number;
}

export interface DatabaseInfo {
  name: string;
  owner: string;
  encoding: string;
}

export interface SchemaInfo {
  name: string;
  comment: string | null;
  table_count: number;
  view_count: number;
}

export interface SnapshotDiff {
  added: string[];
  removed: string[];
  changed: string[];
}

export interface IntrospectSummary {
  snapshot_id: string;
  created_at: string;
  dbname: string;
  schemas: SchemaInfo[];
  object_count: number;
  relationship_count: number;
  /** Solo presente en /refresh: cambios respecto al snapshot anterior. */
  diff?: SnapshotDiff;
}

export type TableKind = "table" | "view" | "matview" | "partitioned" | "foreign";

export interface ObjectSummary {
  schema_name: string;
  name: string;
  kind: TableKind;
  comment: string | null;
  estimated_rows: number | null;
}

export interface ColumnInfo {
  name: string;
  position: number;
  data_type: string;
  is_nullable: boolean;
  default: string | null;
  is_pk: boolean;
  comment: string | null;
}

export interface ForeignKeyInfo {
  name: string;
  columns: string[];
  ref_schema: string;
  ref_table: string;
  ref_columns: string[];
  on_delete: string;
  on_update: string;
}

export interface IndexInfo {
  name: string;
  columns: string[];
  is_unique: boolean;
  method: string;
}

export interface RoutineInfo {
  schema_name: string;
  name: string;
  kind: string; // "function" | "procedure"
  language: string;
  args: string;
  /** Cómo se detectó el uso: "calificada" | "search_path" | "dinamico". */
  match_kind: string;
}

export interface TableDetail {
  schema_name: string;
  name: string;
  kind: TableKind;
  comment: string | null;
  estimated_rows: number | null;
  definition: string | null; // SQL de la vista (solo view/matview)
  columns: ColumnInfo[];
  pk: string[];
  unique_sets: string[][];
  checks: string[];
  foreign_keys: ForeignKeyInfo[];
  indexes: IndexInfo[];
}

/** Plan estimado (no ejecuta la consulta) o real (la ejecuta y mide). */
export type ExplainMode = "estimated" | "actual";

/** Nodo del árbol del plan, ya normalizado por el sidecar para ambos motores. */
export interface ExplainNode {
  depth: number;
  /** "operator" = nodo del árbol; "summary" = línea final (Planning/Execution Time). */
  kind: "operator" | "summary";
  op: string;
  text: string;
  estimate_rows: number | null;
  cost: number | null;
  actual_rows: number | null;
  actual_time: number | null;
  detail: string[];
}

export interface ExplainPlan {
  engine: DbEngine;
  mode: ExplainMode;
  nodes: ExplainNode[];
  plan_text: string;
  elapsed_ms: number;
}

let cached: SidecarInfo | null = null;

function isTauri(): boolean {
  return "__TAURI_INTERNALS__" in window;
}

/**
 * Modo navegador (sin Tauri/Rust): lanza el sidecar a mano y abre
 *   http://localhost:1420/?port=PUERTO&token=TOKEN
 * Los valores se recuerdan en localStorage para recargas.
 */
function browserSidecarInfo(): SidecarInfo {
  const params = new URLSearchParams(window.location.search);
  const port = params.get("port") ?? localStorage.getItem("sidecar_port");
  const token = params.get("token") ?? localStorage.getItem("sidecar_token");
  if (!port || !token) {
    throw new Error(
      "Modo navegador: lanza el sidecar (python -m pg_diagrammer) y abre la app con ?port=PUERTO&token=TOKEN"
    );
  }
  localStorage.setItem("sidecar_port", port);
  localStorage.setItem("sidecar_token", token);
  return { port: Number(port), token };
}

export async function sidecarInfo(): Promise<SidecarInfo> {
  if (!cached) {
    cached = isTauri() ? await invoke<SidecarInfo>("sidecar_info") : browserSidecarInfo();
  }
  return cached;
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { port, token } = await sidecarInfo();
  let res: Response;
  try {
    res = await fetch(`http://127.0.0.1:${port}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        "X-Session-Token": token,
        ...init.headers,
      },
    });
  } catch (e) {
    // fetch solo falla así cuando no hay respuesta HTTP: el sidecar no está
    // escuchando o murió a mitad de la petición. "Failed to fetch" a secas no
    // dice nada, así que se traduce al envelope de siempre.
    throw {
      code: "SIDECAR_UNREACHABLE",
      message: `No se pudo contactar con el motor local (${String(e)}).`,
      hint: "El proceso del sidecar no respondió. Reinicia la aplicación; si acabas de ejecutar una consulta muy grande, acótala con TOP/LIMIT o WHERE.",
      retriable: true,
    } as ApiError;
  }
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    throw {
      code: res.ok ? "BAD_RESPONSE" : "HTTP_ERROR",
      message: `Respuesta inesperada del motor local (HTTP ${res.status}).`,
      retriable: false,
    } as ApiError;
  }
  if (!res.ok && path.startsWith("/api/")) {
    throw body as ApiError;
  }
  return body as T;
}

export async function health(): Promise<{ status: string; version: string }> {
  const { port } = await sidecarInfo();
  const res = await fetch(`http://127.0.0.1:${port}/health`);
  return res.json();
}

// --- Perfiles ---

export const api = {
  listProfiles: () =>
    apiFetch<{ profiles: Profile[]; keychain: boolean }>("/api/v1/profiles"),

  createProfile: (data: ProfileInput) =>
    apiFetch<{ profile: Profile; keychain: boolean }>("/api/v1/profiles", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  updateProfile: (id: string, data: ProfileInput) =>
    apiFetch<{ profile: Profile; keychain: boolean }>(`/api/v1/profiles/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  deleteProfile: (id: string) =>
    apiFetch<{ ok: boolean }>(`/api/v1/profiles/${id}`, { method: "DELETE" }),

  setSessionPassword: (id: string, password: string) =>
    apiFetch<{ ok: boolean }>(`/api/v1/profiles/${id}/password`, {
      method: "POST",
      body: JSON.stringify({ password }),
    }),

  listDatabases: (id: string) =>
    apiFetch<{ databases: DatabaseInfo[] }>(`/api/v1/profiles/${id}/databases`),

  // --- Metadatos ---

  introspect: (id: string, dbname: string) =>
    apiFetch<IntrospectSummary>(
      `/api/v1/profiles/${id}/db/${encodeURIComponent(dbname)}/introspect`,
      { method: "POST" }
    ),

  refresh: (id: string, dbname: string) =>
    apiFetch<IntrospectSummary>(
      `/api/v1/profiles/${id}/db/${encodeURIComponent(dbname)}/refresh`,
      { method: "POST" }
    ),

  listObjects: (
    id: string,
    dbname: string,
    opts: { schema?: string; kind?: string; q?: string; limit?: number; offset?: number } = {}
  ) => {
    const params = new URLSearchParams();
    if (opts.schema) params.set("schema", opts.schema);
    if (opts.kind) params.set("kind", opts.kind);
    if (opts.q) params.set("q", opts.q);
    params.set("limit", String(opts.limit ?? 1000));
    params.set("offset", String(opts.offset ?? 0));
    return apiFetch<{ total: number; items: ObjectSummary[] }>(
      `/api/v1/profiles/${id}/db/${encodeURIComponent(dbname)}/objects?${params}`
    );
  },

  tableDetail: (id: string, dbname: string, schema: string, table: string) =>
    apiFetch<{ table: TableDetail; referenced_by: RelationshipInfo[]; routines: RoutineInfo[]; views: string[] }>(
      `/api/v1/profiles/${id}/db/${encodeURIComponent(dbname)}/tables/${encodeURIComponent(schema)}/${encodeURIComponent(table)}`
    ),

  tableData: (
    id: string,
    dbname: string,
    schema: string,
    table: string,
    opts: {
      limit: number;
      offset: number;
      withTotal?: boolean;
      orderBy?: string | null;
      orderDir?: "asc" | "desc";
      filters?: Record<string, string>;
    }
  ) => {
    const params = new URLSearchParams({
      limit: String(opts.limit),
      offset: String(opts.offset),
      with_total: opts.withTotal ? "true" : "false",
    });
    if (opts.orderBy) {
      params.set("order_by", opts.orderBy);
      params.set("order_dir", opts.orderDir ?? "asc");
    }
    const active = Object.fromEntries(
      Object.entries(opts.filters ?? {}).filter(([, v]) => v.trim() !== "")
    );
    if (Object.keys(active).length > 0) params.set("filters", JSON.stringify(active));
    return apiFetch<{ columns: string[]; rows: unknown[][]; total: number | null }>(
      `/api/v1/profiles/${id}/db/${encodeURIComponent(dbname)}/tables/${encodeURIComponent(schema)}/${encodeURIComponent(table)}/data?${params}`
    );
  },

  /**
   * Ejecuta el script, sentencia a sentencia. `confirm` se reenvía a true tras
   * aceptar el aviso de UPDATE/DELETE sin WHERE (error CONFIRM_REQUIRED).
   *
   * Responde 200 aunque una sentencia falle: llegan los resultados de las
   * anteriores junto a `error` y `error_index`.
   */
  runQuery: (id: string, dbname: string, sql: string, maxRows = 1000, confirm = false) =>
    apiFetch<QueryRun>(`/api/v1/profiles/${id}/db/${encodeURIComponent(dbname)}/query`, {
      method: "POST",
      body: JSON.stringify({ sql, max_rows: maxRows, confirm }),
    }),

  /** Plan de ejecución de una consulta: "estimated" no la ejecuta, "actual" sí. */
  explainQuery: (
    id: string,
    dbname: string,
    sql: string,
    mode: ExplainMode = "estimated"
  ) =>
    apiFetch<ExplainPlan>(
      `/api/v1/profiles/${id}/db/${encodeURIComponent(dbname)}/query/explain`,
      { method: "POST", body: JSON.stringify({ sql, mode }) }
    ),

  routineDefinition: (id: string, dbname: string, schema: string, routine: string, args: string) =>
    apiFetch<{ definition: string }>(
      `/api/v1/profiles/${id}/db/${encodeURIComponent(dbname)}/routines/${encodeURIComponent(schema)}/${encodeURIComponent(routine)}/definition?args=${encodeURIComponent(args)}`
    ),

  relationships: (id: string, dbname: string, tables: string[]) =>
    apiFetch<{ relationships: unknown[] }>(
      `/api/v1/profiles/${id}/db/${encodeURIComponent(dbname)}/relationships`,
      { method: "POST", body: JSON.stringify({ tables }) }
    ),

  // --- Constructor gráfico de consultas (diagrama <-> SQL) ---

  /** Traduce un diagrama de consulta (tablas + joins) a SQL PostgreSQL. */
  buildQuery: (id: string, dbname: string, spec: QuerySpec) =>
    apiFetch<{ sql: string }>(
      `/api/v1/profiles/${id}/db/${encodeURIComponent(dbname)}/query/build`,
      { method: "POST", body: JSON.stringify(spec) }
    ),

  /** Analiza una sentencia SQL y devuelve el diagrama equivalente. */
  parseQuery: (id: string, dbname: string, sql: string) =>
    apiFetch<ParsedQuery>(
      `/api/v1/profiles/${id}/db/${encodeURIComponent(dbname)}/query/parse`,
      { method: "POST", body: JSON.stringify({ sql }) }
    ),
};

// --- Tipos del constructor de consultas ---

export type JoinType = "INNER JOIN" | "LEFT JOIN" | "RIGHT JOIN" | "CROSS JOIN";

export interface QueryJoinSpec {
  source: string; // "schema.tabla"
  target: string; // "schema.tabla"
  join_type: JoinType;
  source_columns: string[];
  target_columns: string[];
}

export interface QuerySpec {
  tables: string[];
  aliases?: Record<string, string>;
  joins: QueryJoinSpec[];
  select_sql?: string | null;
  tail_sql?: string | null;
}

export interface ParsedQuery {
  tables: string[];
  aliases: Record<string, string>;
  joins: QueryJoinSpec[];
  select_sql: string | null;
  tail_sql: string | null;
  unresolved: string[];
  warnings: string[];
}

// --- Diagramas (.pgdiag) ---

export interface DiagramNodePos {
  table: string; // "schema.nombre"
  x: number;
  y: number;
  color?: string | null;
  collapsed?: boolean;
  hidden_columns?: string[];
  display?: string; // "default" | "all" | "keys"
}

export interface DiagramNote {
  id: string;
  text: string;
  x: number;
  y: number;
  width?: number;
  height?: number;
  color?: string;
}

export interface DiagramSummary {
  id: string;
  name: string;
  profile_id: string;
  dbname: string;
  /** Motor del perfil del diagrama (null si el perfil ya no existe). */
  engine: DbEngine | null;
  node_count: number;
  updated_at: string;
}

export interface DiagramDoc {
  id: string;
  name: string;
  profile_id: string;
  dbname: string;
  format_version: number;
  nodes: DiagramNodePos[];
  notes: DiagramNote[];
  updated_at: string;
}

export interface ViewJoin {
  source: string;
  target: string;
  join_type: string; // "INNER JOIN" | "LEFT JOIN" | ...
  source_columns: string[];
  target_columns: string[];
}

export interface RelationshipInfo {
  source: string;
  target: string;
  fk_name: string;
  columns: string[];
  ref_columns: string[];
  cardinality: string;
  inferred: boolean;
}

/** Archivo .pgdiag v2: autocontenido, visualizable sin conexión a la BD. */
export interface PgDiagFile {
  format: "pgdiag";
  format_version: 2;
  name: string;
  dbname: string;
  nodes: DiagramNodePos[];
  notes: DiagramNote[];
  tables: Record<string, TableDetail>;
  relationships: RelationshipInfo[];
}

export const diagramsApi = {
  list: (profileId: string, dbname: string) =>
    apiFetch<{ diagrams: DiagramSummary[] }>(
      `/api/v1/diagrams?profile_id=${encodeURIComponent(profileId)}&dbname=${encodeURIComponent(dbname)}`
    ),

  /** Lista TODOS los diagramas del directorio, sin filtrar por conexión/base. */
  listAll: () => apiFetch<{ diagrams: DiagramSummary[] }>("/api/v1/diagrams"),

  /** Directorio actual donde se listan/guardan los diagramas. */
  getDir: () =>
    apiFetch<{ dir: string; default: string; is_default: boolean }>(
      "/api/v1/settings/diagrams-dir"
    ),

  /** Establece el directorio por defecto de diagramas. */
  setDir: (dir: string) =>
    apiFetch<{ dir: string }>("/api/v1/settings/diagrams-dir", {
      method: "PUT",
      body: JSON.stringify({ dir }),
    }),

  create: (data: { name: string; profile_id: string; dbname: string; nodes: DiagramNodePos[]; notes: DiagramNote[] }) =>
    apiFetch<{ diagram: DiagramDoc }>("/api/v1/diagrams", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  get: (id: string) => apiFetch<{ diagram: DiagramDoc }>(`/api/v1/diagrams/${id}`),

  update: (id: string, data: { name: string; nodes: DiagramNodePos[]; notes: DiagramNote[] }) =>
    apiFetch<{ diagram: DiagramDoc }>(`/api/v1/diagrams/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  remove: (id: string) =>
    apiFetch<{ ok: boolean }>(`/api/v1/diagrams/${id}`, { method: "DELETE" }),

  viewDependsOn: (profileId: string, dbname: string, schema: string, view: string) =>
    apiFetch<{ tables: string[]; joins: ViewJoin[] }>(
      `/api/v1/profiles/${profileId}/db/${encodeURIComponent(dbname)}/views/${encodeURIComponent(schema)}/${encodeURIComponent(view)}/depends-on`
    ),

  relatedTables: (
    profileId: string,
    dbname: string,
    schema: string,
    table: string,
    direction: "in" | "out" | "both" = "both"
  ) =>
    apiFetch<{ related: string[] }>(
      `/api/v1/profiles/${profileId}/db/${encodeURIComponent(dbname)}/tables/${encodeURIComponent(schema)}/${encodeURIComponent(table)}/related?direction=${direction}`
    ),

  relationships: (profileId: string, dbname: string, tables: string[]) =>
    apiFetch<{ relationships: RelationshipInfo[] }>(
      `/api/v1/profiles/${profileId}/db/${encodeURIComponent(dbname)}/relationships`,
      { method: "POST", body: JSON.stringify({ tables }) }
    ),

  exportModel: (profileId: string, dbname: string, tables: string[], format: "mermaid" | "dbml") =>
    apiFetch<{ content: string; extension: string }>(
      `/api/v1/profiles/${profileId}/db/${encodeURIComponent(dbname)}/export`,
      { method: "POST", body: JSON.stringify({ tables, format }) }
    ),
};
