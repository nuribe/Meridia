/**
 * Actividad IA — qué está consultando un cliente MCP, y cómo cortarlo.
 *
 * El servidor MCP corre en OTRO proceso, lanzado por VS Code o Claude, y puede
 * haber varios a la vez. No comparte memoria con el sidecar: lo que se ve aquí
 * sale de una bitácora append-only en el directorio de datos, que el sidecar
 * lee y sirve por cursor (ver docs/mcp.md).
 *
 * El panel no es solo un espejo. Los controles de arriba son el freno de mano:
 * el proceso MCP relee los ajustes en cada llamada, así que apagar el
 * interruptor corta el acceso al instante, sin reiniciar el cliente.
 */
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  mcpApi,
  type ApiError,
  type McpEvent,
  type McpSettings,
  type Profile,
} from "./api/client";
import { markActivitySeen } from "./activityBadge";
import ModeSwitch, { type WorkMode } from "./ModeSwitch";
import ThemeMenu from "./ThemeMenu";
import { saveTextFile } from "./files";
import { highlightSql, sqlPalette } from "./sqlHighlight";

/** Con el panel abierto se sondea rápido; el badge global va a su propio ritmo. */
const POLL_MS = 1500;

/** Tope de eventos en memoria: el panel es un monitor, no un archivo histórico. */
const MAX_IN_MEMORY = 2000;

function errText(e: unknown): string {
  const err = e as ApiError;
  return `${err.code ?? "ERROR"}: ${err.message ?? String(e)}${err.hint ? ` — ${err.hint}` : ""}`;
}

const STATUS_META: Record<string, { icon: string; label: string; cls: string }> = {
  ok: { icon: "✅", label: "ok", cls: "text-body-secondary" },
  error: { icon: "⚠️", label: "error", cls: "text-danger-emphasis" },
  denied: { icon: "⛔", label: "denegado", cls: "text-warning-emphasis" },
};

function toolLabel(tool: string): string {
  return tool.replace(/^meridia_/, "");
}

function hora(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleTimeString();
}

/** Las tools cuyo objetivo es SQL y no el nombre de un objeto. */
const TOOLS_SQL = new Set(["meridia_run_select", "meridia_explain_query"]);

/**
 * El objetivo de la llamada, con el mismo resaltado que el resto de la app.
 *
 * Un `SELECT` de veinte columnas en monoespaciada plana es una tira gris en la
 * que no se distingue de un vistazo qué tabla se leyó. Con los tokens del tema
 * las palabras clave y los literales saltan solos, y es el mismo `highlightSql`
 * del editor de consultas: si un tema afina sus colores, este también cambia.
 *
 * Para las tools de SQL se pinta `args.sql`, no `target`. El servidor recorta
 * `target` a 120 caracteres porque nació como etiqueta corta de una columna
 * estrecha, y ahí se perdía justo el final —el `FROM`, el `WHERE`—, que es lo
 * que uno quiere leer al auditar. La bitácora ya guarda la sentencia entera en
 * los argumentos (hasta 2000 caracteres), así que esto no cambia el formato ni
 * escribe nada nuevo en disco: solo deja de tirar lo que ya estaba ahí. Se
 * conserva `white-space: pre-wrap` para respetar los saltos de línea con que
 * el agente la escribió.
 */
function Objetivo({
  tool,
  target,
  args,
}: {
  tool: string;
  target: string;
  args: Record<string, unknown>;
}) {
  if (!TOOLS_SQL.has(tool)) return <>{target}</>;
  const sql = typeof args.sql === "string" && args.sql ? args.sql : target;
  return (
    <span style={{ whiteSpace: "pre-wrap" }}>{highlightSql(sql, sqlPalette())}</span>
  );
}

export default function ActivityView({
  onChangeMode,
}: {
  onChangeMode: (m: WorkMode) => void;
}) {
  // La lista completa de perfiles, no solo el que está abierto: la allowlist
  // gobierna TODAS las conexiones, y una que no salga aquí quedaría expuesta
  // sin que nadie pudiera desmarcarla.
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [events, setEvents] = useState<McpEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [settings, setSettings] = useState<McpSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);

  // Filtros
  const [fClient, setFClient] = useState("");
  const [fProfile, setFProfile] = useState("");
  const [fTool, setFTool] = useState("");
  const [onlyProblems, setOnlyProblems] = useState(false);
  const [q, setQ] = useState("");

  const cursor = useRef(0);
  const inFlight = useRef(false);

  const load = useCallback(async () => {
    // Dos cargas simultáneas leerían el mismo cursor y traerían los mismos
    // eventos dos veces. Pasa con el StrictMode de desarrollo (monta, desmonta
    // y vuelve a montar) y pasaría en producción si una respuesta tardara más
    // que el intervalo de sondeo.
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const page = await mcpApi.activity(cursor.current, 500);
      cursor.current = page.next_cursor;
      setTotal(page.total);
      markActivitySeen(page.total);
      if (page.reset) {
        // El cursor caducó: la bitácora rotó o se limpió. Se repinta con lo que
        // haya en vez de dejar la tabla mintiendo con eventos que ya no existen.
        setEvents(page.events);
      } else if (page.events.length) {
        // `seq` es el índice de línea en la bitácora, así que sirve de clave
        // estable: fusionar por él deja la carga idempotente pase lo que pase.
        setEvents((prev) => {
          const known = new Set(prev.map((e) => e.seq));
          const fresh = page.events.filter((e) => !known.has(e.seq));
          return fresh.length ? [...prev, ...fresh].slice(-MAX_IN_MEMORY) : prev;
        });
      }
      setError(null);
    } catch (e) {
      setError(errText(e));
    } finally {
      inFlight.current = false;
    }
  }, []);

  useEffect(() => {
    void load();
    mcpApi.settings().then((r) => setSettings(r.settings)).catch((e) => setError(errText(e)));
    api.listProfiles().then((r) => setProfiles(r.profiles)).catch(() => setProfiles([]));
    const t = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  async function patch(data: Partial<McpSettings>) {
    try {
      const r = await mcpApi.setSettings(data);
      setSettings(r.settings);
      setError(null);
    } catch (e) {
      setError(errText(e));
    }
  }

  async function clear() {
    try {
      await mcpApi.clearActivity();
      cursor.current = 0;
      setEvents([]);
      setTotal(0);
      markActivitySeen(0);
    } catch (e) {
      setError(errText(e));
    }
  }

  function exportJsonl() {
    const text = filtered.map((e) => JSON.stringify(e)).join("\n") + "\n";
    void saveTextFile("actividad-mcp.jsonl", text, "application/x-ndjson");
  }

  const clients = useMemo(
    () => [...new Set(events.map((e) => e.client))].sort(),
    [events]
  );
  const tools = useMemo(() => [...new Set(events.map((e) => e.tool))].sort(), [events]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return events
      .filter((e) => !fClient || e.client === fClient)
      .filter((e) => !fProfile || e.profile_id === fProfile)
      .filter((e) => !fTool || e.tool === fTool)
      .filter((e) => !onlyProblems || e.status !== "ok")
      .filter(
        (e) =>
          !needle ||
          // e.args.sql además de e.target: el target viene recortado a 120
          // caracteres, así que buscar el nombre de una tabla que aparece en el
          // FROM de una consulta larga no encontraba nada.
          [e.tool, e.target, e.args?.sql, e.dbname, e.profile_name, e.client, e.error?.code]
            .filter(Boolean)
            .some((s) => String(s).toLowerCase().includes(needle))
      )
      .slice()
      .reverse(); // lo más reciente arriba
  }, [events, fClient, fProfile, fTool, onlyProblems, q]);

  /** Una "sesión" es un cliente MCP concreto: mismo nombre y mismo proceso. */
  const sessions = useMemo(() => {
    const map = new Map<string, { client: string; pid: number; n: number; bad: number; desde: string }>();
    for (const e of events) {
      const key = `${e.client}#${e.pid}`;
      const s = map.get(key) ?? { client: e.client, pid: e.pid, n: 0, bad: 0, desde: e.ts };
      s.n++;
      if (e.status !== "ok") s.bad++;
      map.set(key, s);
    }
    return [...map.values()].sort((a, b) => b.n - a.n);
  }, [events]);

  const enabled = settings?.enabled ?? false;
  const allowQuery = settings?.allow_query ?? false;
  const sampleRows = settings?.sample_rows ?? 0;
  const allowed = settings?.allowed_profiles ?? [];

  return (
    <div className="d-flex flex-column vh-100 bg-body">
      <header className="d-flex align-items-center gap-2 px-3 py-2 bg-body border-bottom flex-wrap">
        <ModeSwitch mode="activity" onChange={onChangeMode} />
        <span
          className="badge rounded-pill"
          style={{ background: "var(--pg-accent3)", color: "var(--pg-accent3-fg)" }}
          title="Ninguna herramienta MCP puede escribir: no existe código de escritura, y allow_writes del perfil se ignora"
        >
          solo lectura
        </span>
        <small className="text-body-secondary">
          {total} llamada{total === 1 ? "" : "s"} registrada{total === 1 ? "" : "s"}
        </small>
        <div className="ms-auto d-flex align-items-center gap-2">
          <button className="btn btn-sm btn-outline-secondary" onClick={exportJsonl} disabled={!filtered.length}>
            Exportar
          </button>
          <button className="btn btn-sm btn-outline-secondary" onClick={() => void clear()} disabled={!total}>
            Limpiar
          </button>
          <ThemeMenu />
        </div>
      </header>

      {error && <div className="alert alert-danger rounded-0 mb-0 py-2 small">{error}</div>}

      {/* --- Controles: esto es el freno de mano, no un ajuste cosmético --- */}
      <section className="px-3 py-2 border-bottom bg-body-tertiary">
        <div className="d-flex align-items-center gap-3 flex-wrap">
          <div className="form-check form-switch mb-0">
            <input
              className="form-check-input"
              type="checkbox"
              role="switch"
              id="mcp-enabled"
              checked={enabled}
              disabled={settings === null}
              onChange={(ev) => void patch({ enabled: ev.target.checked })}
            />
            <label className="form-check-label fw-semibold" htmlFor="mcp-enabled">
              Permitir acceso MCP
            </label>
          </div>
          <small className="text-body-secondary">
            {enabled
              ? "Los clientes conectados pueden consultar el catálogo y pedir el plan estimado. Apagarlo corta la siguiente llamada, sin reiniciar nada."
              : "Ningún cliente MCP puede leer nada. Los intentos quedan registrados abajo como «denegado»."}
          </small>
        </div>

        {/* Segundo interruptor, no un detalle del primero: leer el catálogo
            revela la estructura; ejecutar SQL toca los datos. */}
        {enabled && (
          <div className="d-flex align-items-center gap-3 flex-wrap mt-2">
            <div className="form-check form-switch mb-0">
              <input
                className="form-check-input"
                type="checkbox"
                role="switch"
                id="mcp-allow-query"
                checked={allowQuery}
                onChange={(ev) => void patch({ allow_query: ev.target.checked })}
              />
              <label className="form-check-label fw-semibold" htmlFor="mcp-allow-query">
                Permitir consultas SQL
              </label>
            </div>
            <small className="text-body-secondary">
              {allowQuery
                ? "El agente puede ejecutar SELECT (máx. 1000 filas, 15 s) y medir el plan real. Nunca escribir."
                : "Solo catálogo y plan estimado. El plan estimado no lee ninguna fila."}
            </small>
            {allowQuery && (
              <div className="d-flex align-items-center gap-2 ms-auto">
                <label className="form-label small mb-0" htmlFor="mcp-sample">
                  Filas de muestra en la bitácora
                </label>
                <input
                  className="form-control form-control-sm"
                  id="mcp-sample"
                  type="number"
                  min={0}
                  max={20}
                  style={{ width: 80 }}
                  value={sampleRows}
                  onChange={(ev) => {
                    const n = Number(ev.target.value);
                    if (Number.isFinite(n)) void patch({ sample_rows: Math.max(0, Math.min(20, n)) });
                  }}
                />
                <small className="text-body-secondary" style={{ maxWidth: 330 }}>
                  {sampleRows === 0
                    ? "Sin muestra: la bitácora no guardará ningún dato de tus tablas."
                    : "Deja datos reales en un archivo de texto del disco. Pon 0 con datos regulados."}
                </small>
              </div>
            )}
          </div>
        )}

        {enabled && (
          <div className="mt-2 d-flex align-items-center gap-3 flex-wrap">
            <small className="text-body-secondary">Perfiles visibles:</small>
            <div className="form-check form-check-inline mb-0">
              <input
                className="form-check-input"
                type="checkbox"
                id="mcp-prof-all"
                checked={allowed.length === 0}
                onChange={() => void patch({ allowed_profiles: [] })}
              />
              <label className="form-check-label small" htmlFor="mcp-prof-all">
                Todos
              </label>
            </div>
            {profiles.map((p) => (
              <div className="form-check form-check-inline mb-0" key={p.id}>
                <input
                  className="form-check-input"
                  type="checkbox"
                  id={`mcp-prof-${p.id}`}
                  checked={allowed.length === 0 || allowed.includes(p.id)}
                  onChange={(ev) => {
                    const base = allowed.length === 0 ? profiles.map((x) => x.id) : allowed;
                    const next = ev.target.checked
                      ? [...new Set([...base, p.id])]
                      : base.filter((id) => id !== p.id);
                    // Marcarlos todos equivale a «todos»: se guarda vacío para
                    // que un perfil nuevo no quede fuera sin que nadie lo note.
                    void patch({
                      allowed_profiles: next.length === profiles.length ? [] : next,
                    });
                  }}
                />
                <label className="form-check-label small" htmlFor={`mcp-prof-${p.id}`}>
                  {p.name}
                </label>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* --- Sesiones --- */}
      {sessions.length > 0 && (
        <section className="px-3 py-2 border-bottom d-flex gap-3 flex-wrap align-items-center">
          {sessions.map((s) => (
            <button
              key={`${s.client}#${s.pid}`}
              className="btn btn-sm btn-outline-secondary"
              onClick={() => setFClient(fClient === s.client ? "" : s.client)}
              style={fClient === s.client ? { borderColor: "var(--pg-accent3-text)" } : undefined}
              title={`PID ${s.pid}`}
            >
              <span className="fw-semibold">{s.client}</span>{" "}
              <span className="text-body-secondary">
                desde {hora(s.desde)} · {s.n} llamada{s.n === 1 ? "" : "s"}
                {s.bad > 0 && ` · ${s.bad} con problema`}
              </span>
            </button>
          ))}
        </section>
      )}

      {/* --- Filtros --- */}
      <section className="px-3 py-2 border-bottom d-flex gap-2 flex-wrap align-items-center">
        <input
          className="form-control form-control-sm"
          style={{ maxWidth: 260 }}
          placeholder="Buscar tabla, herramienta, código de error…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <select className="form-select form-select-sm" style={{ maxWidth: 180 }} value={fClient} onChange={(e) => setFClient(e.target.value)}>
          <option value="">Todos los clientes</option>
          {clients.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        <select className="form-select form-select-sm" style={{ maxWidth: 200 }} value={fProfile} onChange={(e) => setFProfile(e.target.value)}>
          <option value="">Todos los perfiles</option>
          {profiles.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
        <select className="form-select form-select-sm" style={{ maxWidth: 200 }} value={fTool} onChange={(e) => setFTool(e.target.value)}>
          <option value="">Todas las herramientas</option>
          {tools.map((t) => (
            <option key={t} value={t}>{toolLabel(t)}</option>
          ))}
        </select>
        <div className="form-check form-switch mb-0">
          <input className="form-check-input" type="checkbox" role="switch" id="mcp-only-bad" checked={onlyProblems} onChange={(e) => setOnlyProblems(e.target.checked)} />
          <label className="form-check-label small" htmlFor="mcp-only-bad">
            Solo errores y rechazos
          </label>
        </div>
        <small className="text-body-secondary ms-auto">
          {filtered.length} de {events.length} en pantalla
        </small>
      </section>

      {/* --- Tabla --- */}
      <div className="flex-grow-1 overflow-auto">
        {filtered.length === 0 ? (
          <div className="p-4 text-body-secondary">
            {total === 0 ? (
              <>
                <p className="mb-1">Todavía no hay actividad MCP.</p>
                <p className="mb-0 small">
                  Configura Meridia como servidor MCP en tu cliente (ver <code>docs/mcp.md</code>) y
                  enciende «Permitir acceso MCP». Cada consulta que haga el agente aparecerá aquí.
                </p>
              </>
            ) : (
              "Ningún evento coincide con los filtros."
            )}
          </div>
        ) : (
          // align-top y no align-middle: ahora hay celdas de dos y tres
          // renglones (perfil·base, y el SQL completo del objetivo), y con el
          // centrado vertical la hora quedaba flotando a media altura, lejos de
          // la línea a la que pertenece.
          <table className="table table-sm table-hover align-top mb-0">
            <thead className="sticky-top bg-body">
              <tr className="small text-body-secondary">
                <th style={{ width: 110 }}>Hora</th>
                <th style={{ width: 130 }}>Cliente</th>
                <th style={{ width: 170 }}>Herramienta</th>
                <th style={{ width: 220 }}>Perfil · Base</th>
                <th>Objetivo</th>
                <th style={{ width: 110 }}>Estado</th>
                <th style={{ width: 70 }} className="text-end">ms</th>
                <th style={{ width: 80 }} className="text-end">Filas</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((e) => {
                const meta = STATUS_META[e.status] ?? STATUS_META.ok;
                const open = expanded === e.seq;
                return (
                  <Fragment key={e.seq}>
                    <tr
                      onClick={() => setExpanded(open ? null : e.seq)}
                      style={{ cursor: "pointer" }}
                    >
                      <td className="small font-monospace text-nowrap">{hora(e.ts)}</td>
                      <td className="small">{e.client}</td>
                      <td className="small font-monospace">{toolLabel(e.tool)}</td>
                      {/* Perfil y base en dos renglones dentro de la misma celda.
                          Antes iban en uno solo con text-truncate, y como el
                          nombre del perfil se lleva casi todo el ancho, la base
                          —que es justo el dato que distingue una llamada de la
                          siguiente— acababa siempre en «GP_SISG…». */}
                      <td className="small">
                        <div>{e.profile_name ?? "—"}</div>
                        {e.dbname && (
                          <div className="text-body-secondary">{e.dbname}</div>
                        )}
                      </td>
                      <td className="small font-monospace" style={{ wordBreak: "break-word" }}>
                        {e.target
                          ? <Objetivo tool={e.tool} target={e.target} args={e.args} />
                          : "—"}
                      </td>
                      <td className={`small ${meta.cls}`}>
                        {meta.icon} {e.error?.code ?? meta.label}
                      </td>
                      <td className="small text-end font-monospace">{e.elapsed_ms}</td>
                      <td className="small text-end font-monospace">{e.row_count ?? "—"}</td>
                    </tr>
                    {open && (
                      <tr>
                        <td colSpan={8} className="bg-body-tertiary">
                          {e.error && (
                            <div className="small mb-2">
                              <strong>{e.error.code}</strong> — {e.error.message}
                              {e.error.hint && <div className="text-body-secondary">{e.error.hint}</div>}
                            </div>
                          )}
                          <div className="small text-body-secondary mb-1">
                            Argumentos · PID {e.pid} · {new Date(e.ts).toLocaleString()}
                          </div>
                          <pre className="small mb-0" style={{ whiteSpace: "pre-wrap" }}>
                            {JSON.stringify(e.args, null, 2)}
                          </pre>
                          {e.sample && e.sample_columns && (
                            <>
                              <div className="small text-body-secondary mt-2 mb-1">
                                Muestra de lo que vio el agente ({e.sample.length} de{" "}
                                {e.row_count ?? "?"} filas)
                              </div>
                              <table className="table table-sm table-bordered mb-0 bg-body">
                                <thead>
                                  <tr className="small">
                                    {e.sample_columns.map((c) => (
                                      <th key={c}>{c}</th>
                                    ))}
                                  </tr>
                                </thead>
                                <tbody>
                                  {e.sample.map((row, i) => (
                                    <tr key={i}>
                                      {row.map((v, j) => (
                                        <td key={j} className="small font-monospace">
                                          {v === null ? "NULL" : String(v)}
                                        </td>
                                      ))}
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
