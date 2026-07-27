/**
 * Pestaña de consulta SQL, dividida en dos: editor (arriba) y resultados (abajo).
 * El editor usa CodeMirror con lenguaje SQL — resaltado de palabras reservadas
 * y autocompletado (IntelliSense) alimentado con los schemas/tablas/columnas
 * del snapshot. La ejecución es de solo lectura (transacción READ ONLY en el
 * servidor). Ctrl/Cmd+Enter ejecuta.
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
  dbname: string;
  summary: IntrospectSummary | null;
  /** Detalle de columnas por tabla, para autocompletar (se carga bajo demanda). */
  loadColumns: (schema: string, table: string) => Promise<string[]>;
  /** ¿Es la pestaña visible? Solo la activa registra su builder en el árbol. */
  active: boolean;
}

interface Result {
  columns: string[];
  rows: unknown[][];
  rowCount: number;
  truncated: boolean;
  elapsedMs: number;
}

/** Texto con el que se compara un valor de celda en los filtros de columna. */
function cellText(v: unknown): string {
  return v === null || v === undefined ? "null" : String(v);
}

export default function QueryTab({ profileId, engine, dbname, summary, loadColumns, active }: Props) {
  const [sqlText, setSqlText] = useState(
    "-- Escribe tu consulta (solo lectura). Ctrl+Enter para ejecutar.\nSELECT * FROM "
  );
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<{ message: string; hint?: string | null } | null>(null);
  const [colCache, setColCache] = useState<Record<string, string[]>>({});
  // Panel inferior: tabla de datos o plan de ejecución.
  const [resultTab, setResultTab] = useState<"data" | "plan">("data");
  // Filtros por columna, aplicados en cliente sobre las filas ya traídas.
  const [colFilters, setColFilters] = useState<Record<number, string>>({});
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

  const sqlExtension = useMemo(() => {
    const ns: SQLNamespace = {};
    for (const [schema, tables] of Object.entries(objectsBySchema)) {
      const tableMap: Record<string, string[]> = {};
      for (const t of tables) {
        tableMap[t] = colCache[`${schema}.${t}`] ?? [];
      }
      ns[schema] = tableMap;
    }
    return sql({
      dialect: engine === "sqlserver" ? MSSQL_CI : PostgreSQL,
      schema: ns,
      upperCaseKeywords: true,
    });
  }, [objectsBySchema, colCache, engine]);

  async function run() {
    const text = sqlText.trim();
    if (!text || running) return;
    setRunning(true);
    setError(null);
    // Los resultados anteriores (y su plan) dejan de ser válidos.
    setColFilters({});
    setPlan(null);
    setPlanError(null);
    planKey.current = "";
    try {
      const r = await api.runQuery(profileId, dbname, text, 1000);
      setResult({
        columns: r.columns,
        rows: r.rows,
        rowCount: r.row_count,
        truncated: r.truncated,
        elapsedMs: r.elapsed_ms,
      });
    } catch (e) {
      const err = e as ApiError;
      setError({ message: err.message ?? String(e), hint: err.hint });
      setResult(null);
    } finally {
      setRunning(false);
    }
  }

  /** Pide el plan de ejecución de la sentencia actual (una vez por sql+modo). */
  async function loadPlan(force = false) {
    const text = sqlText.trim();
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

  // Detecta menciones "schema.tabla" para precargar sus columnas
  useEffect(() => {
    const matches = sqlText.matchAll(/([a-z_][\w]*)\.([a-z_][\w]*)/gi);
    for (const m of matches) {
      if (objectsBySchema[m[1]]?.includes(m[2])) void ensureColumns(m[1], m[2]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sqlText, objectsBySchema]);

  const runKeymap = useMemo(
    () =>
      Prec.highest(
        keymap.of([
          { key: "Mod-Enter", run: () => (void run(), true) },
        ])
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sqlText, running]
  );

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

  const hasFilters = Object.values(colFilters).some((v) => v.trim() !== "");

  /** Filas visibles tras aplicar los filtros de columna (con su índice original). */
  const visibleRows = useMemo(() => {
    const rows = result?.rows ?? [];
    const active = Object.entries(colFilters)
      .filter(([, v]) => v.trim() !== "")
      .map(([ci, v]) => [Number(ci), v.trim().toLowerCase()] as const);
    const indexed = rows.map((row, i) => [i, row] as const);
    if (active.length === 0) return indexed;
    return indexed.filter(([, row]) =>
      active.every(([ci, needle]) => cellText(row[ci]).toLowerCase().includes(needle))
    );
  }, [result, colFilters]);

  // --- Constructor gráfico de consultas ---
  function openBuilderEmpty() {
    setError(null);
    setBuilder({ initial: null }); // Flujo A: Diagrama -> SQL (lienzo vacío)
  }

  async function openBuilderFromSql() {
    const text = sqlText.trim();
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
        <button className="btn btn-sm btn-primary" onClick={() => void run()} disabled={running}>
          {running ? (
            <>
              <span className="spinner-border spinner-border-sm me-1" /> Ejecutando…
            </>
          ) : (
            "▶ Ejecutar"
          )}
        </button>
        <small className="text-body-secondary">Ctrl/⌘ + Enter · solo lectura</small>
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
            {hasFilters ? `${visibleRows.length} de ${result.rowCount}` : `${result.rowCount}`} filas
            {result.truncated ? " (primeras 1000)" : ""} · {result.elapsedMs} ms
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
            <a className="link-secondary small" style={{ cursor: "pointer" }} onClick={() => setColFilters({})}>
              ✕ Limpiar filtros
            </a>
          </li>
        )}
      </ul>

      {/* Resultados: ocupan el resto del espacio, a ancho completo.
          El diagrama del plan gestiona su propio scroll (es un lienzo), así
          que en esa pestaña el contenedor no debe desbordar. */}
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
        ) : error ? (
          <div className="alert alert-danger m-3">
            <div className="fw-semibold mb-1">Error de consulta</div>
            <pre className="mb-0" style={{ whiteSpace: "pre-wrap", fontSize: 13 }}>{error.message}</pre>
            {error.hint && <div className="small mt-2">💡 {error.hint}</div>}
          </div>
        ) : !result ? (
          <div className="text-body-secondary p-4 text-center">
            Ejecuta una consulta para ver los resultados aquí.
          </div>
        ) : result.columns.length === 0 ? (
          <div className="text-body-secondary p-4 text-center">
            Consulta ejecutada sin conjunto de resultados.
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
                      value={colFilters[i] ?? ""}
                      onChange={(e) =>
                        setColFilters((f) => ({ ...f, [i]: e.target.value }))
                      }
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
      </div>
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
