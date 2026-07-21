/**
 * Pestaña de consulta SQL, dividida en dos: editor (arriba) y resultados (abajo).
 * El editor usa CodeMirror con lenguaje SQL — resaltado de palabras reservadas
 * y autocompletado (IntelliSense) alimentado con los schemas/tablas/columnas
 * del snapshot. La ejecución es de solo lectura (transacción READ ONLY en el
 * servidor). Ctrl/Cmd+Enter ejecuta.
 */
import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import CodeMirror, { type ReactCodeMirrorRef } from "@uiw/react-codemirror";
import { sql, PostgreSQL, type SQLNamespace } from "@codemirror/lang-sql";
import { oneDark } from "@codemirror/theme-one-dark";
import { keymap } from "@codemirror/view";
import { Prec } from "@codemirror/state";
import { autocompletion, completionKeymap, acceptCompletion } from "@codemirror/autocomplete";
import { api, type ApiError, type IntrospectSummary, type ParsedQuery } from "./api/client";
import { currentTheme, THEMES } from "./theme";
import QueryBuilder from "./QueryBuilder";

interface Props {
  profileId: string;
  dbname: string;
  summary: IntrospectSummary | null;
  /** Detalle de columnas por tabla, para autocompletar (se carga bajo demanda). */
  loadColumns: (schema: string, table: string) => Promise<string[]>;
}

interface Result {
  columns: string[];
  rows: unknown[][];
  rowCount: number;
  truncated: boolean;
  elapsedMs: number;
}

export default function QueryTab({ profileId, dbname, summary, loadColumns }: Props) {
  const [sqlText, setSqlText] = useState(
    "-- Escribe tu consulta (solo lectura). Ctrl+Enter para ejecutar.\nSELECT * FROM "
  );
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<{ message: string; hint?: string | null } | null>(null);
  const [colCache, setColCache] = useState<Record<string, string[]>>({});
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
      dialect: PostgreSQL,
      schema: ns,
      upperCaseKeywords: true,
    });
  }, [objectsBySchema, colCache]);

  async function run() {
    const text = sqlText.trim();
    if (!text || running) return;
    setRunning(true);
    setError(null);
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
          schemas={summary?.schemas ?? []}
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
            {result.rowCount} filas{result.truncated ? " (primeras 1000)" : ""} · {result.elapsedMs} ms
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

      {/* Resultados: ocupan el resto del espacio, a ancho completo */}
      <div className="flex-grow-1 overflow-auto" style={{ minHeight: 0 }}>
        {error ? (
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
            </thead>
            <tbody>
              {result.rows.map((row, ri) => (
                <tr key={ri}>
                  <td className="text-end text-body-secondary">{ri + 1}</td>
                  {row.map((v, ci) => (
                    <td key={ci} style={{ ...cell, maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis" }} title={v === null ? "NULL" : String(v)}>
                      {v === null ? <span className="fst-italic text-body-tertiary">NULL</span> : String(v)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
