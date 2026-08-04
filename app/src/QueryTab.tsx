/**
 * Pestaña de consulta SQL, dividida en dos: editor (arriba) y resultados (abajo).
 * El editor usa CodeMirror con lenguaje SQL — resaltado de palabras reservadas
 * y autocompletado (IntelliSense) alimentado con los schemas/tablas/columnas
 * del snapshot, incluidas las tablas citadas sin calificar en el FROM/JOIN.
 * F5 y Ctrl/Cmd+Enter ejecutan; si hay texto seleccionado se ejecuta solo esa
 * selección, como en cualquier cliente SQL.
 *
 * El script se parte por sentencias en el servidor y cada una devuelve su
 * propio bloque, que aquí se muestra en una sub-pestaña. Si una falla, la
 * ejecución se detiene ahí y se conservan los resultados anteriores.
 *
 * Qué se puede ejecutar depende del perfil: por defecto solo lectura
 * (transacción READ ONLY en el servidor); con «Permitir escritura» activado,
 * cualquier sentencia. Un UPDATE o DELETE sin WHERE se rechaza la primera vez
 * y se reenvía con confirm=true si el usuario acepta el aviso.
 *
 * El panel inferior tiene dos pestañas: «Datos» (la tabla de resultados, con
 * un filtro por columna que actúa sobre las filas ya traídas) y «Plan de
 * ejecución» (EXPLAIN / SHOWPLAN, estimado o real).
 */
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import CodeMirror, { type ReactCodeMirrorRef } from "@uiw/react-codemirror";
import { sql, MSSQL, PostgreSQL, SQLDialect, type SQLNamespace } from "@codemirror/lang-sql";
import { oneDark } from "@codemirror/theme-one-dark";
import { keymap } from "@codemirror/view";
import { Prec } from "@codemirror/state";
import { autocompletion, completionKeymap, acceptCompletion } from "@codemirror/autocomplete";
import {
  api,
  type ApiError,
  type DbEngine,
  type ExplainMode,
  type ExplainPlan,
  type IntrospectSummary,
  type ParsedQuery,
  type StatementResult,
} from "./api/client";
import { currentTheme, THEMES } from "./theme";
import QueryBuilder from "./QueryBuilder";
import PlanDiagram, { type PlanExporter } from "./PlanDiagram";

/**
 * Dialecto de SQL Server para el autocompletado.
 *
 * El dialecto MSSQL de serie no marca `caseInsensitiveIdentifiers`, así que
 * @codemirror/lang-sql entrecomilla cualquier nombre con mayúsculas
 * (`dbo."Paneles"`). En SQL Server los identificadores NO distinguen
 * mayúsculas: esas comillas solo añaden ruido. Con la bandera activada los
 * nombres normales se insertan tal cual, y los corchetes quedan como comilla
 * preferente para los pocos nombres que sí la necesitan (`[mi tabla]`).
 */
const MSSQL_CI = SQLDialect.define({
  ...MSSQL.spec,
  identifierQuotes: '["',
  caseInsensitiveIdentifiers: true,
});

interface Props {
  profileId: string;
  /** Motor del perfil: fija el dialecto del editor y el tipo de plan. */
  engine: DbEngine;
  /** ¿El perfil admite DDL/DML? Cambia el texto de ayuda y el placeholder. */
  allowWrites: boolean;
  dbname: string;
  summary: IntrospectSummary | null;
  /** Detalle de columnas por tabla, para autocompletar (se carga bajo demanda). */
  loadColumns: (schema: string, table: string) => Promise<string[]>;
  /** ¿Es la pestaña visible? Solo la activa registra su builder en el árbol. */
  active: boolean;
}

/** Rótulo corto de una sub-pestaña de resultado, a partir de su sentencia. */
function statementLabel(statement: string, index: number): string {
  const oneLine = statement.replace(/--[^\n]*/g, " ").replace(/\s+/g, " ").trim();
  if (!oneLine) return `Resultado ${index + 1}`;
  return oneLine.length > 38 ? `${oneLine.slice(0, 38)}…` : oneLine;
}

/**
 * Nombres de relación citados en la sentencia, calificados o no.
 *
 * Sirve para precargar columnas: sin esto, escribir `FROM aviso` no ofrece
 * ninguna columna porque el autocompletado solo conocía `schema.tabla`.
 */
function referencedRelations(text: string): { schema: string | null; name: string }[] {
  const out: { schema: string | null; name: string }[] = [];
  const re =
    /\b(?:from|join|into|update|delete\s+from|truncate|table)\s+(?:only\s+)?([a-z_"[\]][\w"[\]]*)(?:\s*\.\s*([a-z_"[\]][\w"[\]]*))?/gi;
  for (const m of text.matchAll(re)) {
    const clean = (s: string) => s.replace(/["[\]]/g, "");
    if (m[2]) out.push({ schema: clean(m[1]), name: clean(m[2]) });
    else out.push({ schema: null, name: clean(m[1]) });
  }
  return out;
}

/** Texto con el que se compara un valor de celda en los filtros de columna. */
function cellText(v: unknown): string {
  return v === null || v === undefined ? "null" : String(v);
}

export default function QueryTab({ profileId, engine, allowWrites, dbname, summary, loadColumns, active }: Props) {
  const [sqlText, setSqlText] = useState(
    allowWrites
      ? "-- Escribe tu sentencia. Ctrl+Enter para ejecutar.\nSELECT * FROM "
      : "-- Escribe tu consulta (solo lectura). Ctrl+Enter para ejecutar.\nSELECT * FROM "
  );
  const [running, setRunning] = useState(false);
  // Un bloque por sentencia del script; la sub-pestaña activa se elige aparte.
  const [results, setResults] = useState<StatementResult[]>([]);
  const [activeResult, setActiveResult] = useState(0);
  /** Índice de la sentencia que cortó la ejecución; null si no hubo error. */
  const [errorIndex, setErrorIndex] = useState<number | null>(null);
  const [error, setError] = useState<{ message: string; hint?: string | null } | null>(null);
  // Aviso pendiente de aceptar (UPDATE/DELETE sin WHERE).
  const [pendingConfirm, setPendingConfirm] = useState<{ message: string; hint: string | null } | null>(null);
  const [colCache, setColCache] = useState<Record<string, string[]>>({});
  // Panel inferior: tabla de datos o plan de ejecución.
  const [resultTab, setResultTab] = useState<"data" | "plan">("data");
  // Filtros por columna, aplicados en cliente. Clave "resultado:columna" para
  // que cada sub-pestaña conserve los suyos.
  const [colFilters, setColFilters] = useState<Record<string, string>>({});
  // Plan de ejecución (se pide bajo demanda al abrir su pestaña).
  const [plan, setPlan] = useState<ExplainPlan | null>(null);
  const [planMode, setPlanMode] = useState<ExplainMode>("estimated");
  const [planLoading, setPlanLoading] = useState(false);
  const [planError, setPlanError] = useState<{ message: string; hint?: string | null } | null>(null);
  const planKey = useRef("");
  // Constructor gráfico: null = cerrado; overlay abierto (opcionalmente con un
  // análisis inicial de la sentencia actual — Flujo B: SQL -> Diagrama).
  const [builder, setBuilder] = useState<{ initial: ParsedQuery | null } | null>(null);
  const [opening, setOpening] = useState(false);
  const editorRef = useRef<ReactCodeMirrorRef>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  // Altura del editor (px). El divisor la ajusta; el resto lo ocupan los resultados.
  const [editorH, setEditorH] = useState(260);
  const dragging = useRef(false);

  function startDrag(e: React.MouseEvent) {
    e.preventDefault();
    dragging.current = true;
    document.body.style.cursor = "row-resize";
    document.body.style.userSelect = "none";
    const onMove = (ev: MouseEvent) => {
      if (!dragging.current || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      // 90 px reservados para la barra superior; límites razonables
      const h = ev.clientY - rect.top - 44;
      setEditorH(Math.max(90, Math.min(h, rect.height - 120)));
    };
    const onUp = () => {
      dragging.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }

  const dark = useMemo(() => {
    const t = THEMES.find((x) => x.id === currentTheme());
    return t?.bs === "dark";
  }, []);

  // Precarga de columnas de las tablas que se van mencionando (mejora IntelliSense)
  async function ensureColumns(schema: string, table: string) {
    const key = `${schema}.${table}`;
    if (colCache[key]) return;
    try {
      const cols = await loadColumns(schema, table);
      setColCache((c) => ({ ...c, [key]: cols }));
    } catch {
      /* silencioso */
    }
  }

  const [objectsBySchema, setObjectsBySchema] = useState<Record<string, string[]>>({});
  useEffect(() => {
    // Carga los nombres de objetos por schema (una vez) para el autocompletado
    void (async () => {
      const map: Record<string, string[]> = {};
      for (const sc of summary?.schemas ?? []) {
        try {
          const r = await api.listObjects(profileId, dbname, { schema: sc.name });
          map[sc.name] = r.items.map((o) => o.name);
        } catch {
          map[sc.name] = [];
        }
      }
      setObjectsBySchema(map);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [summary]);

  /**
   * Esquema en el que vive una tabla citada sin calificar.
   *
   * Se prefiere el esquema por defecto del motor; si no está ahí, vale con que
   * el nombre sea único en la base. Si es ambiguo se devuelve null: adivinar
   * daría columnas de otra tabla.
   */
  const resolveSchema = useCallback(
    (name: string): string | null => {
      const def = engine === "sqlserver" ? "dbo" : "public";
      if (objectsBySchema[def]?.includes(name)) return def;
      const owners = Object.entries(objectsBySchema)
        .filter(([, tables]) => tables.includes(name))
        .map(([schema]) => schema);
      return owners.length === 1 ? owners[0] : null;
    },
    [objectsBySchema, engine]
  );

  const sqlExtension = useMemo(() => {
    const ns: SQLNamespace = {};
    for (const [schema, tables] of Object.entries(objectsBySchema)) {
      const tableMap: Record<string, string[]> = {};
      for (const t of tables) {
        tableMap[t] = colCache[`${schema}.${t}`] ?? [];
      }
      ns[schema] = tableMap;
    }
    // Las tablas ya citadas sin calificar se registran también en la raíz del
    // namespace: así `aviso.` propone sus columnas aunque no lleve esquema.
    for (const { schema, name } of referencedRelations(sqlText)) {
      const owner = schema ?? resolveSchema(name);
      if (!owner) continue;
      const cols = colCache[`${owner}.${name}`];
      if (cols?.length && !(name in ns)) ns[name] = cols;
    }
    return sql({
      dialect: engine === "sqlserver" ? MSSQL_CI : PostgreSQL,
      schema: ns,
      defaultSchema: engine === "sqlserver" ? "dbo" : "public",
      upperCaseKeywords: true,
    });
  }, [objectsBySchema, colCache, engine, sqlText, resolveSchema]);

  /**
   * SQL que se va a ejecutar: la selección del editor si la hay, o todo.
   *
   * Es el comportamiento de cualquier cliente SQL — permite tener un script
   * largo y lanzar solo el trozo que interesa.
   */
  function sqlToRun(): string {
    const view = editorRef.current?.view;
    if (view) {
      const { from, to } = view.state.selection.main;
      if (from !== to) return view.state.sliceDoc(from, to).trim();
    }
    return sqlText.trim();
  }

  /**
   * Ejecuta el script. Con `confirm` se reintenta tras aceptar el aviso de
   * UPDATE/DELETE sin WHERE que devuelve el servidor.
   */
  async function run(confirm = false) {
    const text = sqlToRun();
    if (!text || running) return;
    setRunning(true);
    setError(null);
    setPendingConfirm(null);
    // Los resultados anteriores (y su plan) dejan de ser válidos.
    setColFilters({});
    setActiveResult(0);
    setPlan(null);
    setPlanError(null);
    planKey.current = "";
    try {
      const r = await api.runQuery(profileId, dbname, text, 1000, confirm);
      setResults(r.results);
      setError(r.error ? { message: r.error.message, hint: r.error.hint } : null);
      setErrorIndex(r.error_index);
      // Al fallar, se muestra la última que sí produjo resultado.
      setActiveResult(Math.max(0, r.results.length - 1));
    } catch (e) {
      const err = e as ApiError;
      if (err.code === "CONFIRM_REQUIRED") {
        setPendingConfirm({ message: err.message, hint: err.hint ?? null });
      } else {
        setError({ message: err.message ?? String(e), hint: err.hint });
      }
      setResults([]);
      setErrorIndex(null);
    } finally {
      setRunning(false);
    }
  }

  /** Pide el plan de ejecución de la sentencia actual (una vez por sql+modo). */
  async function loadPlan(force = false) {
    const text = sqlToRun();
    if (!text) return;
    const key = `${planMode}::${text}`;
    if (!force && planKey.current === key) return;
    planKey.current = key;
    setPlanLoading(true);
    setPlanError(null);
    try {
      const p = await api.explainQuery(profileId, dbname, text, planMode);
      setPlan(p);
    } catch (e) {
      const err = e as ApiError;
      setPlanError({ message: err.message ?? String(e), hint: err.hint });
      setPlan(null);
      planKey.current = ""; // permite reintentar
    } finally {
      setPlanLoading(false);
    }
  }

  // El plan estimado se pide solo al abrir la pestaña (es barato: el motor
  // compila pero no ejecuta). El REAL ejecuta la consulta entera, así que
  // nunca se lanza sin que el usuario lo pida explícitamente.
  useEffect(() => {
    if (resultTab !== "plan") return;
    if (planMode === "estimated") {
      void loadPlan();
    } else if (plan?.mode !== "actual") {
      // Se descarta el plan estimado que estuviera en pantalla y se espera
      // a que el usuario confirme la ejecución.
      setPlan(null);
      setPlanError(null);
      planKey.current = "";
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resultTab, planMode]);

  // Precarga columnas de las tablas mencionadas: tanto "schema.tabla" en
  // cualquier posición como las citadas sin calificar tras FROM/JOIN/UPDATE…
  useEffect(() => {
    for (const m of sqlText.matchAll(/([a-z_][\w]*)\.([a-z_][\w]*)/gi)) {
      if (objectsBySchema[m[1]]?.includes(m[2])) void ensureColumns(m[1], m[2]);
    }
    for (const { schema, name } of referencedRelations(sqlText)) {
      if (schema) continue; // ya cubierto por el bucle anterior
      const owner = resolveSchema(name);
      if (owner) void ensureColumns(owner, name);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sqlText, objectsBySchema, resolveSchema]);

  const runKeymap = useMemo(
    () =>
      Prec.highest(
        keymap.of([
          { key: "Mod-Enter", run: () => (void run(), true) },
          // F5 como en cualquier cliente SQL. preventDefault evita que el
          // WebView recargue la ventana.
          { key: "F5", preventDefault: true, run: () => (void run(), true) },
        ])
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sqlText, running]
  );

  // F5 también fuera del editor (p. ej. con el foco en la tabla de resultados).
  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "F5") return;
      e.preventDefault();
      void run();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, sqlText, running]);

  // Autocompletado (IntelliSense): TAB acepta la sugerencia resaltada; Enter NO
  // (siempre inserta salto de línea). Se desactiva el keymap por defecto y se
  // reconstruye sin el atajo de Enter, añadiendo Tab → acceptCompletion.
  const completionExt = useMemo(
    () => [
      autocompletion({ defaultKeymap: false }),
      Prec.highest(
        keymap.of([
          { key: "Tab", run: acceptCompletion },
          ...completionKeymap.filter((b) => b.key !== "Enter"),
        ])
      ),
    ],
    []
  );

  const cell: CSSProperties = { padding: "4px 10px", whiteSpace: "nowrap" };

  /** Resultado que se está mostrando; null si aún no se ha ejecutado nada. */
  const result = results[activeResult] ?? null;

  /** Filtro de una columna del resultado activo. */
  const filterOf = (ci: number) => colFilters[`${activeResult}:${ci}`] ?? "";
  const setFilterOf = (ci: number, v: string) =>
    setColFilters((f) => ({ ...f, [`${activeResult}:${ci}`]: v }));

  const hasFilters = Object.entries(colFilters).some(
    ([k, v]) => k.startsWith(`${activeResult}:`) && v.trim() !== ""
  );

  /** Filas visibles tras aplicar los filtros de columna (con su índice original). */
  const visibleRows = useMemo(() => {
    const rows = result?.rows ?? [];
    const active = Object.entries(colFilters)
      .filter(([k, v]) => k.startsWith(`${activeResult}:`) && v.trim() !== "")
      .map(([k, v]) => [Number(k.split(":")[1]), v.trim().toLowerCase()] as const);
    const indexed = rows.map((row, i) => [i, row] as const);
    if (active.length === 0) return indexed;
    return indexed.filter(([, row]) =>
      active.every(([ci, needle]) => cellText(row[ci]).toLowerCase().includes(needle))
    );
  }, [result, colFilters, activeResult]);

  // --- Constructor gráfico de consultas ---
  function openBuilderEmpty() {
    setError(null);
    setBuilder({ initial: null }); // Flujo A: Diagrama -> SQL (lienzo vacío)
  }

  async function openBuilderFromSql() {
    const text = sqlToRun();
    if (!text) {
      openBuilderEmpty();
      return;
    }
    setOpening(true);
    setError(null);
    try {
      const parsed = await api.parseQuery(profileId, dbname, text); // Flujo B
      setBuilder({ initial: parsed });
    } catch (e) {
      const err = e as ApiError;
      setError({ message: err.message ?? String(e), hint: err.hint });
    } finally {
      setOpening(false);
    }
  }

  if (builder) {
    return (
      <div className="d-flex flex-column w-100 h-100" style={{ minHeight: 0 }}>
        <QueryBuilder
          profileId={profileId}
          dbname={dbname}
          active={active}
          initial={builder.initial}
          onDone={(generated) => {
            setSqlText(generated);
            setBuilder(null);
          }}
          onCancel={() => setBuilder(null)}
        />
      </div>
    );
  }

  return (
    <div ref={containerRef} className="d-flex flex-column w-100 h-100" style={{ minHeight: 0 }}>
      {/* Barra de acciones */}
      <div className="d-flex align-items-center gap-2 px-3 py-2 border-bottom bg-body">
        <button
          className="btn btn-sm btn-primary"
          onClick={() => void run()}
          disabled={running}
          title="Ejecuta la selección si hay texto seleccionado; si no, todo el editor (F5)"
        >
          {running ? (
            <>
              <span className="spinner-border spinner-border-sm me-1" /> Ejecutando…
            </>
          ) : (
            "▶ Ejecutar"
          )}
        </button>
        <small className="text-body-secondary">
          F5 o Ctrl/⌘ + Enter · selección o todo ·{" "}
          {allowWrites ? (
            <span
              className="text-warning-emphasis fw-semibold"
              title="Este perfil puede ejecutar DDL/DML. Desactívalo editando la conexión."
            >
              escritura habilitada
            </span>
          ) : (
            "solo lectura"
          )}
        </small>
        <div className="vr mx-1" />
        <button
          className="btn btn-sm btn-outline-primary"
          onClick={openBuilderEmpty}
          title="Construir una consulta a partir de un diagrama (arrastrando tablas y uniendo columnas)"
        >
          ◇ Desde diagrama
        </button>
        <button
          className="btn btn-sm btn-outline-primary"
          onClick={() => void openBuilderFromSql()}
          disabled={opening}
          title="Convertir la consulta actual en un diagrama editable"
        >
          {opening ? (
            <>
              <span className="spinner-border spinner-border-sm me-1" /> …
            </>
          ) : (
            "◇ Diagrama"
          )}
        </button>
        <span className="flex-grow-1" />
        {result && (
          <small className="text-body-secondary">
            {hasFilters ? `${visibleRows.length} de ${result.row_count}` : `${result.row_count}`} filas
            {result.truncated ? " (primeras 1000)" : ""} · {result.elapsed_ms} ms
          </small>
        )}
      </div>

      {/* Editor: altura ajustable por el divisor */}
      <div style={{ height: editorH, flexShrink: 0, overflow: "auto" }}>
        <CodeMirror
          ref={editorRef}
          value={sqlText}
          onChange={setSqlText}
          height="100%"
          theme={dark ? oneDark : "light"}
          extensions={[runKeymap, completionExt, sqlExtension]}
          basicSetup={{ lineNumbers: true, foldGutter: true, autocompletion: false }}
          style={{ fontSize: 13, height: "100%" }}
        />
      </div>

      {/* Divisor arrastrable */}
      <div
        onMouseDown={startDrag}
        title="Arrastra para redimensionar editor y resultados"
        className="d-flex align-items-center justify-content-center bg-body-secondary border-top border-bottom"
        style={{ height: 8, cursor: "row-resize", flexShrink: 0 }}
      >
        <div style={{ width: 40, height: 3, borderRadius: 2, background: "var(--bs-secondary-color)", opacity: 0.5 }} />
      </div>

      {/* Pestañas del panel inferior: datos y plan de ejecución */}
      <ul className="nav nav-tabs px-3 pt-2 bg-body flex-shrink-0 align-items-end flex-nowrap">
        <li className="nav-item">
          <button
            className={`nav-link py-1 px-3 ${resultTab === "data" ? "active fw-semibold" : ""}`}
            onClick={() => setResultTab("data")}
          >
            ▦ Datos
          </button>
        </li>
        <li className="nav-item">
          <button
            className={`nav-link py-1 px-3 ${resultTab === "plan" ? "active fw-semibold" : ""}`}
            onClick={() => setResultTab("plan")}
            title="Plan de ejecución de la consulta del editor"
          >
            ⛭ Plan de ejecución
          </button>
        </li>
        {resultTab === "data" && hasFilters && (
          <li className="nav-item align-self-center ms-2">
            <a
              className="link-secondary small"
              style={{ cursor: "pointer" }}
              title="Limpia los filtros de este resultado"
              onClick={() =>
                setColFilters((f) =>
                  Object.fromEntries(
                    Object.entries(f).filter(([k]) => !k.startsWith(`${activeResult}:`))
                  )
                )
              }
            >
              ✕ Limpiar filtros
            </a>
          </li>
        )}
      </ul>

      {/* Resultados: ocupan el resto del espacio, a ancho completo.
          El diagrama del plan gestiona su propio scroll (es un lienzo), así
          que en esa pestaña el contenedor no debe desbordar. */}
      {/* Selector de resultado: FUERA del contenedor con scroll, para que no
          se pierda de vista al bajar por una rejilla larga. */}
      {resultTab === "data" && !pendingConfirm && results.length > 0 && (
        <ResultTabs
          results={results}
          activeIndex={activeResult}
          onSelect={setActiveResult}
          errorIndex={errorIndex}
        />
      )}

      <div
        className="flex-grow-1"
        style={{ minHeight: 0, overflow: resultTab === "plan" ? "hidden" : "auto" }}
      >
        {resultTab === "plan" ? (
          <PlanPanel
            plan={plan}
            dbname={dbname}
            mode={planMode}
            onModeChange={setPlanMode}
            loading={planLoading}
            error={planError}
            onRefresh={() => void loadPlan(true)}
            hasSql={sqlText.trim() !== ""}
          />
        ) : pendingConfirm ? (
          <div className="alert alert-warning m-3">
            <div className="fw-semibold mb-1">⚠ Confirma antes de ejecutar</div>
            <pre className="mb-0" style={{ whiteSpace: "pre-wrap", fontSize: 13 }}>
              {pendingConfirm.message}
            </pre>
            {pendingConfirm.hint && <div className="small mt-2">💡 {pendingConfirm.hint}</div>}
            <div className="d-flex gap-2 mt-3">
              <button
                className="btn btn-sm btn-danger"
                disabled={running}
                onClick={() => void run(true)}
              >
                Ejecutar de todos modos
              </button>
              <button className="btn btn-sm btn-outline-secondary" onClick={() => setPendingConfirm(null)}>
                Cancelar
              </button>
            </div>
          </div>
        ) : results.length === 0 && error ? (
          <div className="alert alert-danger m-3">
            <div className="fw-semibold mb-1">Error de consulta</div>
            <pre className="mb-0" style={{ whiteSpace: "pre-wrap", fontSize: 13 }}>{error.message}</pre>
            {error.hint && <div className="small mt-2">💡 {error.hint}</div>}
          </div>
        ) : results.length === 0 ? (
          <div className="text-body-secondary p-4 text-center">
            Ejecuta una consulta para ver los resultados aquí.
          </div>
        ) : (
          <>
            {error && (
              <div className="alert alert-danger m-3 mb-0">
                <div className="fw-semibold mb-1">
                  Error en la sentencia {(errorIndex ?? 0) + 1}; se detuvo ahí
                </div>
                <pre className="mb-0" style={{ whiteSpace: "pre-wrap", fontSize: 13 }}>{error.message}</pre>
                {error.hint && <div className="small mt-2">💡 {error.hint}</div>}
              </div>
            )}
            {!result ? null : result.columns.length === 0 ? (
              <div className="text-body-secondary p-4 text-center">
                {result.affected_rows === null
                  ? "Sentencia ejecutada sin conjunto de resultados."
                  : `Sentencia ejecutada: ${result.affected_rows} ${
                      result.affected_rows === 1 ? "fila afectada" : "filas afectadas"
                    }.`}
              </div>
            ) : (
          <table className="table table-striped table-hover table-sm align-middle mb-0" style={{ fontSize: 13 }}>
            <thead className="table-light" style={{ position: "sticky", top: 0, zIndex: 1 }}>
              <tr>
                <th className="fw-bold text-end text-body-secondary" style={{ width: 46 }}>#</th>
                {result.columns.map((c, i) => (
                  <th key={i} className="fw-bold text-nowrap">{c}</th>
                ))}
              </tr>
              {/* Filtros por columna: filtran las filas ya traídas, al escribir */}
              <tr>
                <th className="bg-body-tertiary"></th>
                {result.columns.map((c, i) => (
                  <th key={i} className="bg-body-tertiary p-1">
                    <input
                      className="form-control form-control-sm"
                      style={{ minWidth: 90, fontWeight: 400, fontSize: 12 }}
                      placeholder="Filtrar"
                      title={`Filtrar por ${c} (sobre las filas mostradas)`}
                      value={filterOf(i)}
                      onChange={(e) => setFilterOf(i, e.target.value)}
                    />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visibleRows.length === 0 ? (
                <tr>
                  <td colSpan={result.columns.length + 1} className="text-body-secondary text-center p-4">
                    Ninguna fila coincide con los filtros.
                  </td>
                </tr>
              ) : (
                visibleRows.map(([ri, row]) => (
                  <tr key={ri}>
                    <td className="text-end text-body-secondary">{ri + 1}</td>
                    {row.map((v, ci) => (
                      <td key={ci} style={{ ...cell, maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis" }} title={v === null ? "NULL" : String(v)}>
                        {v === null ? <span className="fst-italic text-body-tertiary">NULL</span> : String(v)}
                      </td>
                    ))}
                  </tr>
                ))
              )}
            </tbody>
          </table>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/**
 * Barra de selección de resultado, una entrada por sentencia ejecutada.
 *
 * Se renderiza fuera del contenedor con scroll y por eso permanece visible
 * mientras se recorre una rejilla larga: sin ella no se sabría qué resultado
 * se está viendo ni se podría cambiar sin volver arriba.
 */
function ResultTabs({
  results,
  activeIndex,
  onSelect,
  errorIndex,
}: {
  results: StatementResult[];
  activeIndex: number;
  onSelect: (i: number) => void;
  errorIndex: number | null;
}) {
  return (
    <div
      className="d-flex align-items-center gap-1 px-2 py-1 border-bottom bg-body-tertiary flex-nowrap"
      style={{ overflowX: "auto", overflowY: "hidden", flexShrink: 0 }}
    >
      <small className="text-body-secondary text-nowrap me-1 ps-1">Resultados</small>
      {results.map((r, i) => {
        const activo = i === activeIndex;
        return (
          <button
            key={i}
            onClick={() => onSelect(i)}
            title={r.statement}
            className={`btn btn-sm d-inline-flex align-items-center gap-2 text-nowrap py-0 ${
              activo ? "btn-primary" : "btn-outline-secondary border-secondary-subtle"
            }`}
            style={{ fontSize: 12, lineHeight: "22px" }}
          >
            <span className={activo ? "fw-bold" : "fw-bold text-body-secondary"}>{i + 1}</span>
            <span className="font-monospace">{statementLabel(r.statement, i)}</span>
            <span className={activo ? "opacity-75" : "text-body-secondary"}>
              {r.affected_rows === null
                ? `${r.row_count}${r.truncated ? "+" : ""} filas`
                : `${r.affected_rows} afectadas`}
            </span>
          </button>
        );
      })}
      {errorIndex !== null && (
        <span
          className="badge bg-danger-subtle text-danger-emphasis border border-danger-subtle fw-normal text-nowrap"
          title="La ejecución se detuvo en esta sentencia"
        >
          ✕ falló la {errorIndex + 1}
        </span>
      )}
    </div>
  );
}

/** Panel del plan de ejecución: selector estimado/real + árbol de operadores. */
function PlanPanel({
  plan,
  dbname,
  mode,
  onModeChange,
  loading,
  error,
  onRefresh,
  hasSql,
}: {
  plan: ExplainPlan | null;
  dbname: string;
  mode: ExplainMode;
  onModeChange: (m: ExplainMode) => void;
  loading: boolean;
  error: { message: string; hint?: string | null } | null;
  onRefresh: () => void;
  hasSql: boolean;
}) {
  const [copied, setCopied] = useState(false);
  // El diagrama es la vista por defecto; la tabla sigue disponible para leer
  // los números exactos de cada operador.
  const [view, setView] = useState<"diagram" | "table">("diagram");
  // El lienzo publica aquí su exportador (vive dentro del ReactFlowProvider);
  // es null mientras no haya diagrama montado.
  const exporter = useRef<PlanExporter | null>(null);
  const [exporting, setExporting] = useState<"idle" | "busy" | "done" | "error">("idle");
  // Estable: si cambiara en cada render, el lienzo se registraría y borraría
  // continuamente al repintarse esta barra.
  const handleExporterReady = useCallback((fn: PlanExporter | null) => {
    exporter.current = fn;
  }, []);

  async function copyPlan() {
    if (!plan) return;
    await copyText(plan.plan_text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  async function exportPng() {
    if (!exporter.current || exporting === "busy") return;
    setExporting("busy");
    try {
      const saved = await exporter.current();
      setExporting(saved ? "done" : "idle");
    } catch {
      setExporting("error");
    }
    setTimeout(() => setExporting("idle"), 2500);
  }

  // Coste máximo del plan: sirve de referencia para la barra de cada operador.
  const maxCost = plan
    ? Math.max(0, ...plan.nodes.map((n) => n.cost ?? 0))
    : 0;

  return (
    <div className="d-flex flex-column h-100" style={{ minHeight: 0 }}>
      <div className="d-flex align-items-center gap-2 px-3 py-2 border-bottom bg-body flex-wrap flex-shrink-0">
        <div className="btn-group btn-group-sm" role="group">
          <button
            className={`btn ${mode === "estimated" ? "btn-primary" : "btn-outline-primary"}`}
            onClick={() => onModeChange("estimated")}
            title="Plan estimado: el motor compila la consulta pero NO la ejecuta"
          >
            Estimado
          </button>
          <button
            className={`btn ${mode === "actual" ? "btn-primary" : "btn-outline-primary"}`}
            onClick={() => onModeChange("actual")}
            title="Plan real: ejecuta la consulta y mide filas y tiempos reales"
          >
            Real
          </button>
        </div>
        <small className="text-body-secondary">
          {mode === "estimated"
            ? "No ejecuta la consulta."
            : "Ejecuta la consulta para medir filas y tiempos reales."}
        </small>
        <span className="flex-grow-1" />
        {plan && <small className="text-body-secondary">{plan.elapsed_ms} ms</small>}
        <div className="btn-group btn-group-sm" role="group">
          <button
            className={`btn ${view === "diagram" ? "btn-secondary" : "btn-outline-secondary"}`}
            onClick={() => setView("diagram")}
            title="Ver el plan como diagrama de proceso"
          >
            ◇ Diagrama
          </button>
          <button
            className={`btn ${view === "table" ? "btn-secondary" : "btn-outline-secondary"}`}
            onClick={() => setView("table")}
            title="Ver el plan como tabla de operadores"
          >
            ▤ Tabla
          </button>
        </div>
        <button
          className="btn btn-sm btn-outline-secondary"
          onClick={onRefresh}
          disabled={loading || !hasSql}
          title="Volver a calcular el plan"
        >
          ⟳
        </button>
        <button
          className={`btn btn-sm ${copied ? "btn-success" : "btn-outline-secondary"}`}
          onClick={() => void copyPlan()}
          disabled={!plan}
          title="Copiar el plan como texto"
        >
          {copied ? "✓ Copiado" : "⧉ Copiar"}
        </button>
        <button
          className={`btn btn-sm ${
            exporting === "done"
              ? "btn-success"
              : exporting === "error"
                ? "btn-danger"
                : "btn-outline-secondary"
          }`}
          onClick={() => void exportPng()}
          disabled={view !== "diagram" || !plan || exporting === "busy"}
          title={
            view === "diagram"
              ? "Exportar el diagrama completo a PNG (no solo lo que se ve en pantalla)"
              : "Disponible en la vista de diagrama"
          }
        >
          {exporting === "busy"
            ? "Exportando…"
            : exporting === "done"
              ? "✓ Exportado"
              : exporting === "error"
                ? "✕ Falló"
                : "⭳ PNG"}
        </button>
      </div>

      <div
        className="flex-grow-1"
        style={{ minHeight: 0, overflow: view === "diagram" ? "hidden" : "auto" }}
      >
      {!hasSql ? (
        <div className="text-body-secondary p-4 text-center">
          Escribe una consulta para ver su plan de ejecución.
        </div>
      ) : mode === "actual" && !plan && !loading && !error ? (
        // El plan real ejecuta la consulta de verdad: nunca se lanza solo.
        <div className="p-4 text-center">
          <p className="text-body-secondary mb-1">
            El plan real <strong>ejecuta la consulta completa</strong> en el servidor.
          </p>
          <p className="text-body-secondary small">
            Si devuelve muchas filas puede tardar. Para una vista rápida usa el plan estimado.
          </p>
          <button className="btn btn-sm btn-primary" onClick={onRefresh}>
            ▶ Calcular plan real
          </button>
        </div>
      ) : loading ? (
        <div className="d-flex align-items-center gap-2 text-body-secondary p-4">
          <span className="spinner-border spinner-border-sm" /> Calculando el plan…
        </div>
      ) : error ? (
        <div className="alert alert-danger m-3">
          <div className="fw-semibold mb-1">No se pudo obtener el plan</div>
          <pre className="mb-0" style={{ whiteSpace: "pre-wrap", fontSize: 13 }}>{error.message}</pre>
          {error.hint && <div className="small mt-2">💡 {error.hint}</div>}
        </div>
      ) : !plan || plan.nodes.length === 0 ? (
        <div className="text-body-secondary p-4 text-center">Sin plan que mostrar.</div>
      ) : view === "diagram" ? (
        <PlanDiagram
          nodes={plan.nodes}
          mode={mode}
          fileName={`plan-${dbname}`.replace(/[^\w.-]+/g, "_")}
          onExporterReady={handleExporterReady}
        />
      ) : (
        <table className="table table-hover table-sm align-middle mb-0" style={{ fontSize: 13 }}>
          <thead className="table-light" style={{ position: "sticky", top: 0, zIndex: 1 }}>
            <tr>
              <th className="fw-bold">Operación</th>
              <th className="fw-bold text-end text-nowrap" style={{ width: 110 }}>Filas est.</th>
              {mode === "actual" && (
                <th className="fw-bold text-end text-nowrap" style={{ width: 110 }}>Filas reales</th>
              )}
              <th className="fw-bold text-end text-nowrap" style={{ width: 110 }}>Coste</th>
              <th className="fw-bold" style={{ width: 140 }}></th>
            </tr>
          </thead>
          <tbody>
            {plan.nodes.map((n, i) => (
              <tr key={i}>
                <td>
                  <div style={{ paddingLeft: n.depth * 18 }}>
                    <span className="text-body-tertiary me-1">{n.depth > 0 ? "└─" : ""}</span>
                    <span className="fw-semibold">{n.op}</span>
                    {n.text !== n.op && (
                      <div
                        className="text-body-secondary font-monospace"
                        style={{ fontSize: 11, paddingLeft: 18, whiteSpace: "pre-wrap" }}
                      >
                        {n.text}
                      </div>
                    )}
                    {n.detail.map((d, di) => (
                      <div
                        key={di}
                        className="text-body-tertiary font-monospace"
                        style={{ fontSize: 11, paddingLeft: 18, whiteSpace: "pre-wrap" }}
                      >
                        {d}
                      </div>
                    ))}
                  </div>
                </td>
                <td className="text-end text-nowrap">{fmtNum(n.estimate_rows)}</td>
                {mode === "actual" && (
                  <td className="text-end text-nowrap">{fmtNum(n.actual_rows)}</td>
                )}
                <td className="text-end text-nowrap">{fmtNum(n.cost)}</td>
                <td>
                  {n.cost != null && maxCost > 0 && (
                    <div className="progress" style={{ height: 6 }} title={`Coste ${n.cost}`}>
                      <div
                        className="progress-bar"
                        style={{
                          width: `${Math.min(100, (n.cost / maxCost) * 100)}%`,
                          background: "var(--pg-accent)",
                        }}
                      />
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      </div>
    </div>
  );
}

function fmtNum(v: number | null): string {
  if (v == null) return "";
  if (Number.isInteger(v)) return v.toLocaleString();
  return v.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

/** Copia al portapapeles con respaldo para entornos sin Clipboard API. */
export async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
}
