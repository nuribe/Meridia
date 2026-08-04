/**
 * Explorador de objetos de una base de datos.
 * Árbol compartido (ObjectTree) + panel de detalle con historial de
 * navegación (regresar/avanzar paso a paso y breadcrumb).
 */
import { useEffect, useRef, useState } from "react";
import {
  api,
  type ApiError,
  type DbEngine,
  type IntrospectSummary,
  type RelationshipInfo,
  type RoutineInfo,
  type SnapshotDiff,
  type TableDetail,
} from "./api/client";
import ObjectTree, { KIND_ICON, KIND_LABEL } from "./ObjectTree";
import ModeSwitch from "./ModeSwitch";
import ThemeMenu from "./ThemeMenu";
import QueryTab, { copyText } from "./QueryTab";
import { SetBuilderSessionContext, type BuilderSession } from "./builderBridge";
import { currentTheme, THEMES } from "./theme";

function errText(e: unknown): string {
  const err = e as ApiError;
  return `${err.code ?? "ERROR"}: ${err.message ?? String(e)}${err.hint ? ` — ${err.hint}` : ""}`;
}

interface Detail {
  table: TableDetail;
  referenced_by: RelationshipInfo[];
  routines: RoutineInfo[];
  views: string[];
}

interface HistEntry {
  schema: string;
  name: string;
}

interface DetailTab {
  kind: "detail";
  id: number;
  key: string; // "schema.nombre" actual de la pestaña
  title: string;
  detail: Detail | null;
  hist: HistEntry[];
  pos: number;
}

interface QueryTabState {
  kind: "query";
  id: number;
  title: string;
}

type Tab = DetailTab | QueryTabState;

interface Props {
  profileId: string;
  /** Motor del perfil: cambia el dialecto SQL del editor y el plan de ejecución. */
  engine: DbEngine;
  dbname: string;
  onBack: () => void;
  onOpenDiagram: () => void;
}

export default function Explorer({ profileId, engine, dbname, onBack, onOpenDiagram }: Props) {
  const [summary, setSummary] = useState<IntrospectSummary | null>(null);
  const [tabs, setTabs] = useState<Tab[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const nextId = useRef(1);
  const [status, setStatus] = useState("Cargando schemas…");
  const [error, setError] = useState<string | null>(null);
  const [diff, setDiff] = useState<SnapshotDiff | null>(null);
  // Sesión activa del constructor gráfico de consultas. Cuando no es null, el
  // árbol compartido de la izquierda pasa a modo arrastrar-al-lienzo en vez de
  // montar un segundo explorador dentro del builder.
  const [builderSession, setBuilderSession] = useState<BuilderSession | null>(null);

  const activeTab = tabs.find((t) => t.id === activeId) ?? null;
  const activeDetail = activeTab?.kind === "detail" ? activeTab : null;

  async function load(refresh = false) {
    setStatus(refresh ? "Re-introspectando…" : "Cargando schemas…");
    setError(null);
    setTabs([]);
    setActiveId(null);
    try {
      const s = refresh
        ? await api.refresh(profileId, dbname)
        : await api.introspect(profileId, dbname);
      setSummary(s);
      const d = s.diff;
      setDiff(d && (d.added.length || d.removed.length || d.changed.length) ? d : null);
      setStatus("");
    } catch (e) {
      setError(errText(e));
      setStatus("");
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profileId, dbname]);

  /** Al reemplazar el contenido de una pestaña, volver al inicio de la página. */
  function scrollTabTop(tabId: number) {
    setTimeout(() => {
      document.getElementById(`pgdetail-tab-${tabId}`)?.scrollTo({ top: 0 });
    }, 0);
  }

  async function fetchDetail(schema: string, name: string): Promise<Detail | null> {
    try {
      const r = await api.tableDetail(profileId, dbname, schema, name);
      return { table: r.table, referenced_by: r.referenced_by, routines: r.routines, views: r.views };
    } catch (e) {
      setError(errText(e));
      return null;
    }
  }

  /** Desde el árbol: abre una pestaña nueva (o activa la ya abierta de esa tabla). */
  async function openFromTree(schema: string, name: string) {
    const key = `${schema}.${name}`;
    const existing = tabs.find((t) => t.kind === "detail" && t.key === key);
    if (existing) {
      setActiveId(existing.id);
      return;
    }
    const detail = await fetchDetail(schema, name);
    if (!detail) return;
    const id = nextId.current++;
    setTabs((ts) => [
      ...ts,
      { kind: "detail", id, key, title: name, detail, hist: [{ schema, name }], pos: 0 },
    ]);
    setActiveId(id);
  }

  /** Nueva pestaña de consulta SQL. */
  function openQueryTab() {
    const id = nextId.current++;
    const count = tabs.filter((t) => t.kind === "query").length + 1;
    setTabs((ts) => [...ts, { kind: "query", id, title: `Consulta ${count}` }]);
    setActiveId(id);
  }

  /** Navegación interna (FK, referencias, breadcrumb): misma pestaña. */
  async function navigateTo(schema: string, name: string) {
    if (!activeDetail) {
      void openFromTree(schema, name);
      return;
    }
    const key = `${schema}.${name}`;
    if (activeDetail.key === key) return;
    const detail = await fetchDetail(schema, name);
    if (!detail) return;
    setTabs((ts) =>
      ts.map((t) =>
        t.id === activeDetail.id && t.kind === "detail"
          ? {
              ...t,
              key,
              title: name,
              detail,
              hist: [...t.hist.slice(0, t.pos + 1), { schema, name }],
              pos: t.pos + 1,
            }
          : t
      )
    );
    scrollTabTop(activeDetail.id);
  }

  /** Saltar a una posición del historial de la pestaña activa. */
  async function jumpTo(index: number) {
    if (!activeDetail || index < 0 || index >= activeDetail.hist.length || index === activeDetail.pos) return;
    const h = activeDetail.hist[index];
    const detail = await fetchDetail(h.schema, h.name);
    if (!detail) return;
    setTabs((ts) =>
      ts.map((t) =>
        t.id === activeDetail.id && t.kind === "detail"
          ? { ...t, key: `${h.schema}.${h.name}`, title: h.name, detail, pos: index }
          : t
      )
    );
    scrollTabTop(activeDetail.id);
  }

  /** Carga las columnas de una tabla para el autocompletado del editor SQL. */
  async function loadColumns(schema: string, table: string): Promise<string[]> {
    const r = await api.tableDetail(profileId, dbname, schema, table);
    return r.table.columns.map((c) => c.name);
  }

  function closeTab(id: number) {
    setTabs((ts) => {
      const rest = ts.filter((t) => t.id !== id);
      if (activeId === id) setActiveId(rest.length > 0 ? rest[rest.length - 1].id : null);
      return rest;
    });
  }

  return (
    <SetBuilderSessionContext.Provider value={setBuilderSession}>
    <div className="d-flex flex-column vh-100 bg-body-tertiary">
      <header
        className="d-flex align-items-center gap-2 px-3 py-2 bg-body flex-wrap"
        style={{ borderBottom: "3px solid var(--pg-accent)" }}
      >
        <button
          className="btn btn-sm btn-outline-secondary"
          onClick={onBack}
          title="Volver a la lista de bases de datos"
        >
          ←
        </button>
        <ModeSwitch mode="explorer" onChange={() => onOpenDiagram()} />
        <span className="fw-semibold fs-6 ms-1">🗄 {dbname}</span>
        {summary && (
          <small className="text-body-secondary">
            {summary.schemas.length} schemas · {summary.object_count} objetos ·{" "}
            {summary.relationship_count} relaciones · snapshot{" "}
            {new Date(summary.created_at).toLocaleTimeString()}
          </small>
        )}
        <span className="flex-grow-1" />
        <button
          className="btn btn-sm btn-outline-secondary"
          onClick={() => void load(true)}
          title="Re-introspectar la base de datos (refresca el snapshot)"
        >
          ⟳
        </button>
        <ThemeMenu />
      </header>

      {error && (
        <div className="alert alert-danger rounded-0 py-2 px-3 mb-0 d-flex">
          <span className="flex-grow-1">{error}</span>
          <button className="btn-close" onClick={() => setError(null)} />
        </div>
      )}
      {status && <div className="px-3 py-2 text-body-secondary">{status}</div>}

      {diff && (
        <div className="alert alert-info rounded-0 py-2 px-3 mb-0 d-flex align-items-center gap-3 flex-wrap">
          <span className="fw-semibold">Cambios desde el último snapshot:</span>
          {diff.added.length > 0 && (
            <span><span className="badge text-bg-success">nueva</span> {diff.added.length} objetos</span>
          )}
          {diff.changed.length > 0 && (
            <span><span className="badge text-bg-warning">±</span> {diff.changed.length} modificados</span>
          )}
          {diff.removed.length > 0 && (
            <span title={diff.removed.join(", ")}>
              <span className="badge text-bg-danger">−</span> {diff.removed.length} eliminados
            </span>
          )}
          <span className="text-body-secondary small">Los marcados aparecen señalados en el árbol.</span>
          <button className="btn-close ms-auto" onClick={() => setDiff(null)} title="Descartar" />
        </div>
      )}

      <div className="d-flex flex-grow-1" style={{ minHeight: 0 }}>
        <ObjectTree
          profileId={profileId}
          dbname={dbname}
          schemas={summary?.schemas ?? []}
          selectedKey={builderSession ? null : activeDetail?.key ?? null}
          // Con un builder activo, el mismo árbol pasa a modo arrastrar-al-lienzo;
          // si no, sigue abriendo pestañas de detalle al hacer clic.
          draggable={!!builderSession}
          presentKeys={builderSession?.presentKeys}
          hint={
            builderSession
              ? "Arrastra tablas al lienzo (o doble clic). Une una columna con otra para crear un JOIN. ⇲ trae relacionadas."
              : undefined
          }
          onItemClick={
            builderSession ? undefined : (o) => void openFromTree(o.schema_name, o.name)
          }
          onItemDoubleClick={
            builderSession
              ? (o) => builderSession.addTable(`${o.schema_name}.${o.name}`)
              : undefined
          }
          badges={
            diff
              ? Object.fromEntries([
                  ...diff.added.map((k) => [k, "added"] as const),
                  ...diff.changed.map((k) => [k, "changed"] as const),
                ])
              : undefined
          }
          onError={setError}
        />

        <section className="flex-grow-1 d-flex flex-column" style={{ minWidth: 0 }}>
          {/* Pestañas: detalles de tablas + consultas SQL */}
          <ul
            className="nav nav-tabs px-3 pt-2 bg-body border-bottom flex-nowrap flex-shrink-0 align-items-end"
            style={{ overflowX: "auto", overflowY: "hidden" }}
          >
            {tabs.map((t) => {
                const isActive = t.id === activeId;
                const icon = t.kind === "query" ? "🔧" : KIND_ICON[t.detail?.table.kind ?? "table"];
                return (
                  <li key={t.id} className="nav-item">
                    <button
                      className="nav-link py-2 px-3 d-flex align-items-center gap-2"
                      onClick={() => setActiveId(t.id)}
                      title={t.kind === "query" ? "Consulta SQL" : t.key}
                      style={{
                        whiteSpace: "nowrap",
                        background: isActive ? "var(--pg-accent)" : "transparent",
                        color: isActive ? "#fff" : "var(--bs-secondary-color)",
                        fontWeight: isActive ? 700 : 400,
                        border: isActive ? "1px solid var(--pg-accent)" : "1px solid transparent",
                        borderBottom: "none",
                        borderRadius: "8px 8px 0 0",
                        boxShadow: isActive ? "0 -2px 6px color-mix(in srgb, var(--pg-accent) 30%, transparent)" : undefined,
                      }}
                    >
                      {icon} {t.title}
                      <span
                        className={`btn-close ${isActive ? "btn-close-white" : ""}`}
                        style={{ fontSize: 8 }}
                        title="Cerrar pestaña"
                        onClick={(e) => {
                          e.stopPropagation();
                          closeTab(t.id);
                        }}
                      />
                    </button>
                  </li>
                );
              })}
            <li className="nav-item align-self-center">
              <button
                className="btn btn-sm btn-outline-primary border-0 ms-1"
                onClick={openQueryTab}
                title="Nueva pestaña de consulta SQL"
              >
                ＋ Consulta
              </button>
            </li>
          </ul>

          {tabs.length === 0 && (
            <div className="text-body-secondary mt-5 text-center">
              <p className="fs-5 mb-1">Selecciona una tabla o vista</p>
              <p className="small">
                Cada objeto que elijas en el panel izquierdo se abre en su propia pestaña,
                o pulsa <strong>＋ Consulta</strong> para escribir SQL.
              </p>
            </div>
          )}

          {tabs.map((t) => {
            if (t.kind === "query") {
              return (
                <div
                  key={t.id}
                  className="flex-grow-1"
                  style={{ display: t.id === activeId ? "flex" : "none", minHeight: 0 }}
                >
                  <QueryTab
                    profileId={profileId}
                    engine={engine}
                    dbname={dbname}
                    summary={summary}
                    loadColumns={loadColumns}
                    active={t.id === activeId}
                  />
                </div>
              );
            }
            return (
            <div
              key={t.id}
              id={`pgdetail-tab-${t.id}`}
              className="flex-grow-1 overflow-y-auto p-3"
              style={{ display: t.id === activeId ? "block" : "none", minHeight: 0 }}
            >
              {/* Historial de navegación de esta pestaña */}
              {t.hist.length > 1 && (
                <div className="d-flex align-items-center gap-2 mb-3 flex-wrap">
                  <div className="btn-group btn-group-sm" role="group">
                    <button
                      className="btn btn-outline-secondary"
                      disabled={t.pos <= 0}
                      onClick={() => void jumpTo(t.pos - 1)}
                      title="Regresar"
                    >
                      ← Regresar
                    </button>
                    <button
                      className="btn btn-outline-secondary"
                      disabled={t.pos >= t.hist.length - 1}
                      onClick={() => void jumpTo(t.pos + 1)}
                      title="Avanzar"
                    >
                      Avanzar →
                    </button>
                  </div>
                  <nav aria-label="breadcrumb" className="mb-0">
                    <ol className="breadcrumb mb-0 small">
                      {t.hist.map((h, i) => (
                        <li
                          key={`${i}:${h.schema}.${h.name}`}
                          className={`breadcrumb-item ${i === t.pos ? "active fw-semibold" : ""}`}
                        >
                          {i === t.pos ? (
                            <>{h.schema}.{h.name}</>
                          ) : (
                            <a
                              className="link-primary"
                              style={{ cursor: "pointer" }}
                              onClick={() => void jumpTo(i)}
                            >
                              {h.schema}.{h.name}
                            </a>
                          )}
                        </li>
                      ))}
                    </ol>
                  </nav>
                </div>
              )}

              {t.detail && (
                <TableDetailView
                  key={`${t.id}:${t.key}`}
                  profileId={profileId}
                  dbname={dbname}
                  table={t.detail.table}
                  referencedBy={t.detail.referenced_by}
                  routines={t.detail.routines}
                  views={t.detail.views}
                  onNavigate={(sc, tb) => void navigateTo(sc, tb)}
                />
              )}
            </div>
            );
          })}
        </section>
      </div>
    </div>
    </SetBuilderSessionContext.Provider>
  );
}

/** Cabecera destacada de sección del detalle: icono + título + contador. */
function SectionHeader({
  icon,
  title,
  count,
  actions,
}: {
  icon: string;
  title: string;
  count?: number;
  /** Controles alineados a la derecha (p. ej. el botón de copiar). */
  actions?: React.ReactNode;
}) {
  return (
    <div
      className="card-header py-2 d-flex align-items-center gap-2 text-white"
      style={{ background: "var(--pg-grad)" }}
    >
      <span style={{ opacity: 0.9 }}>{icon}</span>
      <span className="fw-semibold text-uppercase" style={{ fontSize: 13, letterSpacing: ".6px" }}>
        {title}
      </span>
      {count !== undefined && (
        <span className="badge rounded-pill text-bg-light text-dark">{count}</span>
      )}
      {actions && <span className="ms-auto d-flex align-items-center gap-2">{actions}</span>}
    </div>
  );
}

/** Botón de copiar al portapapeles con confirmación temporal. */
function CopyButton({
  text,
  title = "Copiar al portapapeles",
  className = "btn btn-sm btn-light py-0 px-2",
}: {
  text: string;
  title?: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      className={className}
      title={title}
      onClick={() => {
        void copyText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        });
      }}
    >
      {copied ? "✓ Copiado" : "⧉ Copiar"}
    </button>
  );
}

/** Indica cómo se detectó el uso de la tabla; sólo se muestra si no es directo. */
function MatchBadge({ kind }: { kind?: string }) {
  if (kind === "search_path") {
    return (
      <span
        className="badge text-bg-light border fw-normal ms-2"
        title="Referencia sin calificar; resuelta a esta tabla por el search_path de la rutina"
      >
        sin calificar
      </span>
    );
  }
  if (kind === "dinamico") {
    return (
      <span
        className="badge text-bg-warning-subtle border fw-normal ms-2"
        title="Sólo aparece dentro de SQL dinámico (EXECUTE): uso probable, no verificable"
      >
        probable · SQL dinámico
      </span>
    );
  }
  return null;
}

function TableDetailView({
  profileId,
  dbname,
  table,
  referencedBy,
  routines,
  views,
  onNavigate,
}: {
  profileId: string;
  dbname: string;
  table: TableDetail;
  referencedBy: RelationshipInfo[];
  routines: RoutineInfo[];
  views: string[];
  onNavigate: (schema: string, table: string) => void;
}) {
  const [codeModal, setCodeModal] = useState<{
    title: string;
    content: string;
    loading: boolean;
  } | null>(null);

  async function openCode(r: RoutineInfo) {
    setCodeModal({ title: `${r.schema_name}.${r.name}`, content: "", loading: true });
    try {
      const res = await api.routineDefinition(profileId, dbname, r.schema_name, r.name, r.args);
      setCodeModal({ title: `${r.schema_name}.${r.name}`, content: res.definition, loading: false });
    } catch (e) {
      setCodeModal({ title: `${r.schema_name}.${r.name}`, content: `-- Error: ${errText(e)}`, loading: false });
    }
  }
  const [activeTab, setActiveTab] = useState<"meta" | "data">("meta");
  // Parámetros de rutinas colapsados por defecto; expansión por fila.
  const [expandedRoutines, setExpandedRoutines] = useState<Set<string>>(new Set());
  const toggleRoutine = (key: string) =>
    setExpandedRoutines((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <div className="d-flex flex-column gap-3">
      <div>
        <h4 className="mb-1">
          {KIND_ICON[table.kind]} {table.schema_name}.{table.name}{" "}
          <span className="badge text-bg-secondary align-middle fw-normal">{KIND_LABEL[table.kind]}</span>
        </h4>
        <div className="text-body-secondary small">
          {table.estimated_rows != null && <>~{table.estimated_rows} filas</>}
          {table.comment && <> · {table.comment}</>}
        </div>
      </div>

      {/* Pestañas Metadata / Datos */}
      <ul className="nav nav-tabs">
        <li className="nav-item">
          <button
            className={`nav-link ${activeTab === "meta" ? "active fw-semibold" : ""}`}
            onClick={() => setActiveTab("meta")}
          >
            Metadata
          </button>
        </li>
        <li className="nav-item">
          <button
            className={`nav-link ${activeTab === "data" ? "active fw-semibold" : ""}`}
            onClick={() => setActiveTab("data")}
          >
            Datos
          </button>
        </li>
      </ul>

      {activeTab === "data" && (
        <DataTab
          profileId={profileId}
          dbname={dbname}
          schema={table.schema_name}
          table={table.name}
        />
      )}

      {activeTab === "meta" && (
      <>
      <div className="card shadow-sm">
        <SectionHeader icon="▦" title="Columnas" count={table.columns.length} />
        <div className="table-responsive">
          <table className="table table-sm table-hover align-middle mb-0">
            <thead className="table-light">
              <tr>
                <th style={{ width: 36 }}></th>
                <th>Nombre</th>
                <th>Tipo</th>
                <th>Nullable</th>
                <th>Default</th>
              </tr>
            </thead>
            <tbody>
              {table.columns.map((c) => (
                <tr key={c.name}>
                  <td className="text-center">{c.is_pk ? "🔑" : ""}</td>
                  <td className={c.is_pk ? "fw-semibold" : ""}>{c.name}</td>
                  <td><code>{c.data_type}</code></td>
                  <td>
                    {c.is_nullable
                      ? <span className="badge text-bg-success">sí</span>
                      : <span className="badge text-bg-secondary">no</span>}
                  </td>
                  <td className="text-body-secondary text-truncate" style={{ maxWidth: 220 }}>
                    {c.default ?? ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {(table.kind === "view" || table.kind === "matview") && table.definition && (
        <div className="card shadow-sm">
          <SectionHeader
            icon="⌨"
            title="Definición SQL"
            actions={
              <CopyButton
                text={table.definition}
                title="Copiar la definición SQL de la vista"
              />
            }
          />
          <pre
            className="mb-0"
            style={{
              background: "var(--bs-tertiary-bg)",
              color: "var(--bs-body-color)",
              padding: "14px 18px",
              fontSize: 13,
              lineHeight: 1.55,
              overflowX: "auto",
              borderRadius: "0 0 6px 6px",
              fontFamily: "Consolas, 'Cascadia Code', Menlo, monospace",
              tabSize: 4,
            }}
          >
            <code>{highlightSql(table.definition, sqlPalette())}</code>
          </pre>
        </div>
      )}

      {table.foreign_keys.length > 0 && (
        <div className="card shadow-sm">
          <SectionHeader icon="→" title="Claves foráneas" count={table.foreign_keys.length} />
          <ul className="list-group list-group-flush">
            {table.foreign_keys.map((fk) => (
              <li key={fk.name} className="list-group-item py-2">
                <code>({fk.columns.join(", ")})</code> →{" "}
                <a
                  className="link-primary fw-semibold"
                  style={{ cursor: "pointer" }}
                  onClick={() => onNavigate(fk.ref_schema, fk.ref_table)}
                  title={`Abrir detalle de ${fk.ref_schema}.${fk.ref_table}`}
                >
                  {fk.ref_schema}.{fk.ref_table}
                </a>
                <code>({fk.ref_columns.join(", ")})</code>{" "}
                <small className="text-body-secondary">
                  ON DELETE {fk.on_delete} · ON UPDATE {fk.on_update}
                </small>
              </li>
            ))}
          </ul>
        </div>
      )}

      {referencedBy.length > 0 && (
        <div className="card shadow-sm">
          <SectionHeader icon="←" title="Referenciada por" count={referencedBy.length} />
          <ul className="list-group list-group-flush">
            {referencedBy.map((r) => {
              const [sc, ...rest] = r.source.split(".");
              const tb = rest.join(".");
              return (
                <li key={`${r.fk_name}:${r.source}`} className="list-group-item py-2">
                  <a
                    className="link-primary fw-semibold"
                    style={{ cursor: "pointer" }}
                    onClick={() => onNavigate(sc, tb)}
                    title={`Abrir detalle de ${r.source}`}
                  >
                    {r.source}
                  </a>{" "}
                  <code>({r.columns.join(", ")})</code> → <code>({r.ref_columns.join(", ")})</code>{" "}
                  <span className="badge text-bg-info-subtle border text-dark">{r.cardinality}</span>{" "}
                  <small className="text-body-secondary">{r.fk_name}</small>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {views.length > 0 && (
        <div className="card shadow-sm">
          <SectionHeader icon="◉" title="Vistas que la referencian" count={views.length} />
          <ul className="list-group list-group-flush">
            {views.map((v) => {
              const [sc, ...rest] = v.split(".");
              const vn = rest.join(".");
              return (
                <li key={v} className="list-group-item py-2 d-flex align-items-center gap-2">
                  <span>◉</span>
                  <span
                    className="badge text-bg-light border font-monospace fw-normal"
                    title="Esquema al que pertenece la vista"
                  >
                    {sc}
                  </span>
                  <a
                    className="link-primary fw-semibold"
                    style={{ cursor: "pointer" }}
                    onClick={() => onNavigate(sc, vn)}
                    title={`Abrir detalle de ${v}`}
                  >
                    {vn}
                  </a>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {routines.length > 0 && (
        <div className="card shadow-sm">
          <SectionHeader icon="ƒ" title="Funciones y procedimientos que la usan" count={routines.length} />
          <div className="table-responsive">
            <table className="table table-sm table-hover align-middle mb-0">
              <thead className="table-light">
                <tr>
                  <th style={{ width: 36 }}></th>
                  <th style={{ width: 120 }}>Esquema</th>
                  <th>Nombre</th>
                  <th>Parámetros</th>
                  <th>Tipo</th>
                  <th>Lenguaje</th>
                  <th style={{ width: 90 }}>Código</th>
                </tr>
              </thead>
              <tbody>
                {routines.map((r) => {
                  const params = parseRoutineArgs(r.args);
                  return (
                    <tr key={`${r.schema_name}.${r.name}(${r.args})`}>
                      <td className="text-center fs-6">{r.kind === "procedure" ? "⚙" : "ƒ"}</td>
                      <td>
                        <span
                          className="badge text-bg-light border font-monospace fw-normal"
                          title="Esquema al que pertenece la rutina"
                        >
                          {r.schema_name}
                        </span>
                      </td>
                      <td>
                        <div className="fw-semibold">
                          {r.name}
                          <MatchBadge kind={r.match_kind} />
                        </div>
                        <small className="text-body-secondary font-monospace">
                          {r.schema_name}.{r.name}
                        </small>
                      </td>
                      <td>
                        {params.length === 0 ? (
                          <span className="text-body-secondary small">— sin parámetros</span>
                        ) : !expandedRoutines.has(`${r.schema_name}.${r.name}(${r.args})`) ? (
                          <button
                            className="btn btn-sm btn-outline-secondary py-0 px-2"
                            onClick={() => toggleRoutine(`${r.schema_name}.${r.name}(${r.args})`)}
                            title="Mostrar parámetros"
                          >
                            {params.length} {params.length === 1 ? "parámetro" : "parámetros"} ▸
                          </button>
                        ) : (
                          <div className="d-flex flex-column gap-1 py-1">
                            {params.map((prm, i) => (
                              <div key={i} className="d-flex align-items-center gap-2 flex-wrap">
                                {prm.mode && prm.mode !== "IN" && (
                                  <span className="badge text-bg-warning">{prm.mode}</span>
                                )}
                                <span className="badge text-bg-primary-subtle border border-primary-subtle text-dark font-monospace">
                                  {prm.name ?? `$${i + 1}`}
                                </span>
                                <code className="text-body-secondary small">{prm.type}</code>
                              </div>
                            ))}
                            <a
                              className="link-secondary small"
                              style={{ cursor: "pointer" }}
                              onClick={() => toggleRoutine(`${r.schema_name}.${r.name}(${r.args})`)}
                            >
                              ▴ Ocultar parámetros
                            </a>
                          </div>
                        )}
                      </td>
                      <td>
                        <span className="badge text-bg-light border">
                          {r.kind === "procedure" ? "procedimiento" : "función"}
                        </span>
                      </td>
                      <td>
                        <span className="badge text-bg-light border">{r.language}</span>
                      </td>
                      <td>
                        <button
                          className="btn btn-sm btn-outline-primary py-0 px-2 font-monospace"
                          onClick={() => void openCode(r)}
                          title="Ver código fuente"
                        >
                          {"</>"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="card-footer py-1">
            <small className="text-body-secondary">
              Detección sobre el código de cada rutina, ignorando comentarios y literales. Los
              nombres sin calificar se resuelven con el <code>search_path</code> de la rutina, así
              que las tablas homónimas de otros esquemas no se cuentan. El SQL dinámico
              (<code>EXECUTE</code>) sólo puede marcarse como probable.
            </small>
          </div>
        </div>
      )}

      {table.indexes.length > 0 && (
        <div className="card shadow-sm">
          <SectionHeader icon="≡" title="Índices" count={table.indexes.length} />
          <ul className="list-group list-group-flush">
            {table.indexes.map((ix) => (
              <li key={ix.name} className="list-group-item py-2">
                <code>{ix.name}</code> ({ix.columns.join(", ")}){" "}
                <span className="badge text-bg-light border">{ix.method}</span>
                {ix.is_unique && <span className="badge text-bg-warning ms-1">UNIQUE</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {codeModal && (
        <CodeModal
          title={codeModal.title}
          content={codeModal.content}
          loading={codeModal.loading}
          onClose={() => setCodeModal(null)}
        />
      )}

      {table.checks.length > 0 && (
        <div className="card shadow-sm">
          <SectionHeader icon="✓" title="Checks" count={table.checks.length} />
          <ul className="list-group list-group-flush">
            {table.checks.map((chk, i) => (
              <li key={i} className="list-group-item py-2">
                <code>{chk}</code>
              </li>
            ))}
          </ul>
        </div>
      )}
      </>
      )}
    </div>
  );
}

/** Pestaña "Datos": página de filas con paginación real en la BD. */
function DataTab({
  profileId,
  dbname,
  schema,
  table,
}: {
  profileId: string;
  dbname: string;
  schema: string;
  table: string;
}) {
  const [columns, setColumns] = useState<string[]>([]);
  const [rows, setRows] = useState<unknown[][]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(50);
  const [sort, setSort] = useState<{ col: string; dir: "asc" | "desc" } | null>(null);
  const [filterInputs, setFilterInputs] = useState<Record<string, string>>({});
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  /** Aplica los filtros escritos (solo al pulsar Enter): página 0 y re-conteo. */
  function applyFilters() {
    setFilters(filterInputs);
    setPage(0);
    setTotal(null);
  }

  function clearFilters() {
    setFilterInputs({});
    setFilters({});
    setPage(0);
    setTotal(null);
  }

  /** Clic en un título: asc → desc → sin orden (PK). */
  function toggleSort(col: string) {
    setSort((s) =>
      s?.col !== col ? { col, dir: "asc" } : s.dir === "asc" ? { col, dir: "desc" } : null
    );
    setPage(0);
  }

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setLoading(true);
      setError("");
      try {
        const r = await api.tableData(profileId, dbname, schema, table, {
          limit: pageSize,
          offset: page * pageSize,
          withTotal: total === null, // count(*) solo cuando cambia el filtro
          orderBy: sort?.col ?? null,
          orderDir: sort?.dir,
          filters,
        });
        if (cancelled) return;
        setColumns(r.columns);
        setRows(r.rows);
        if (r.total !== null) setTotal(r.total);
      } catch (e) {
        if (!cancelled) setError(errText(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, pageSize, sort, filters]);

  const pages = total !== null ? Math.max(1, Math.ceil(total / pageSize)) : null;
  const from = page * pageSize + 1;
  const to = page * pageSize + rows.length;

  return (
    <div className="card shadow-sm">
      <div className="d-flex align-items-center gap-2 px-3 py-2 border-bottom flex-wrap">
        <span className="fw-semibold">Datos</span>
        {total !== null && (
          <small className="text-body-secondary">
            {total === 0 ? "0 filas" : `${from}–${to} de ${total} filas`}
          </small>
        )}
        {Object.values(filters).some((v) => v.trim() !== "") && (
          <a
            className="link-secondary small"
            style={{ cursor: "pointer" }}
            onClick={clearFilters}
          >
            ✕ Limpiar filtros
          </a>
        )}
        <span className="flex-grow-1" />
        <select
          className="form-select form-select-sm"
          style={{ width: "auto" }}
          value={pageSize}
          onChange={(e) => {
            setPageSize(Number(e.target.value));
            setPage(0);
          }}
          title="Filas por página"
        >
          <option value={25}>25 / pág</option>
          <option value={50}>50 / pág</option>
          <option value={100}>100 / pág</option>
          <option value={200}>200 / pág</option>
        </select>
        <div className="btn-group btn-group-sm">
          <button className="btn btn-outline-secondary" disabled={page === 0 || loading} onClick={() => setPage(0)} title="Primera página">«</button>
          <button className="btn btn-outline-secondary" disabled={page === 0 || loading} onClick={() => setPage(page - 1)} title="Anterior">‹</button>
          <span className="btn btn-outline-secondary disabled" style={{ minWidth: 90 }}>
            {pages !== null ? `${page + 1} / ${pages}` : page + 1}
          </span>
          <button
            className="btn btn-outline-secondary"
            disabled={loading || (pages !== null ? page + 1 >= pages : rows.length < pageSize)}
            onClick={() => setPage(page + 1)}
            title="Siguiente"
          >
            ›
          </button>
          <button
            className="btn btn-outline-secondary"
            disabled={loading || pages === null || page + 1 >= pages}
            onClick={() => pages !== null && setPage(pages - 1)}
            title="Última página"
          >
            »
          </button>
        </div>
      </div>

      {error && <div className="alert alert-danger m-3 py-2">{error}</div>}

      {loading ? (
        <div className="d-flex align-items-center gap-2 text-body-secondary p-4">
          <span className="spinner-border spinner-border-sm" /> Cargando datos…
        </div>
      ) : rows.length === 0 && !error ? (
        <div className="text-body-secondary p-4 text-center">
          {Object.values(filters).some((v) => v.trim() !== "")
            ? "Sin filas que coincidan con los filtros."
            : "Sin filas en esta página."}
        </div>
      ) : (
        <div className="table-responsive" style={{ maxHeight: "calc(100vh - 340px)", minHeight: 160 }}>
          <table className="table table-striped table-hover table-sm align-middle mb-0" style={{ fontSize: 13 }}>
            <thead className="table-light" style={{ position: "sticky", top: 0, zIndex: 1 }}>
              <tr>
                <th className="fw-bold text-end text-body-secondary" style={{ width: 46 }}>#</th>
                {columns.map((c) => (
                  <th
                    key={c}
                    className="fw-bold text-nowrap user-select-none"
                    style={{ cursor: "pointer" }}
                    onClick={() => toggleSort(c)}
                    title="Clic para ordenar (asc → desc → sin orden)"
                  >
                    {c}
                    <span className={sort?.col === c ? "text-primary ms-1" : "text-body-tertiary ms-1"}>
                      {sort?.col === c ? (sort.dir === "asc" ? "▲" : "▼") : "↕"}
                    </span>
                  </th>
                ))}
              </tr>
              <tr>
                <th className="bg-body-tertiary"></th>
                {columns.map((c) => (
                  <th key={c} className="bg-body-tertiary p-1">
                    <input
                      className="form-control form-control-sm"
                      style={{ minWidth: 90, fontWeight: 400, fontSize: 12 }}
                      placeholder="Filtrar ⏎"
                      title="Escribe y pulsa Enter para aplicar el filtro"
                      value={filterInputs[c] ?? ""}
                      onChange={(e) =>
                        setFilterInputs((f) => ({ ...f, [c]: e.target.value }))
                      }
                      onKeyDown={(e) => e.key === "Enter" && applyFilters()}
                    />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, ri) => (
                <tr key={ri}>
                  <td className="text-end text-body-secondary">{from + ri}</td>
                  {row.map((v, ci) => (
                    <td
                      key={ci}
                      className="text-truncate"
                      style={{ maxWidth: 280 }}
                      title={v === null ? "NULL" : String(v)}
                    >
                      {v === null ? (
                        <span className="fst-italic text-body-tertiary">NULL</span>
                      ) : (
                        String(v)
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// --- Parseo de parámetros de rutinas ---
// La firma viene de pg_get_function_identity_arguments: "p_id integer, OUT total numeric, ..."
// (sin typmod ni defaults, así que separar por ", " es seguro).

const PARAM_MODES = new Set(["IN", "OUT", "INOUT", "VARIADIC"]);

const TYPE_STARTERS = new Set([
  "integer", "bigint", "smallint", "int", "int2", "int4", "int8", "serial",
  "text", "boolean", "bool", "numeric", "decimal", "character", "varchar",
  "char", "timestamp", "timestamptz", "date", "time", "timetz", "interval",
  "double", "real", "float4", "float8", "money", "json", "jsonb", "uuid",
  "bytea", "xml", "inet", "cidr", "macaddr", "bit", "void", "record",
  "anyelement", "anyarray", "regclass", "name", "oid", "tsvector", "tsquery",
]);

interface ParsedParam {
  mode: string | null;
  name: string | null;
  type: string;
}

function parseRoutineArgs(args: string): ParsedParam[] {
  const trimmed = args.trim();
  if (!trimmed) return [];
  return trimmed.split(", ").map((part) => {
    const tokens = part.trim().split(/\s+/);
    let mode: string | null = null;
    if (tokens.length > 1 && PARAM_MODES.has(tokens[0].toUpperCase())) {
      mode = tokens.shift()!.toUpperCase();
    }
    if (tokens.length === 1) {
      return { mode, name: null, type: tokens[0] };
    }
    const first = tokens[0].toLowerCase().replace(/"/g, "").replace(/\[\]$/, "");
    if (TYPE_STARTERS.has(first) || first.includes(".")) {
      // Parámetro sin nombre: todo es el tipo (p. ej. "timestamp with time zone")
      return { mode, name: null, type: tokens.join(" ") };
    }
    return { mode, name: tokens[0], type: tokens.slice(1).join(" ") };
  });
}

/** Modal redimensionable con el código fuente de una rutina, estilo editor. */
function CodeModal({
  title,
  content,
  loading,
  onClose,
}: {
  title: string;
  content: string;
  loading: boolean;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const dark = THEMES.find((x) => x.id === currentTheme())?.bs === "dark";
  const pal = sqlPalette();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(content);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = content;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  const lines = content.replace(/\r\n/g, "\n").split("\n");

  // Cerrar solo si el clic EMPEZÓ en el fondo: al terminar un redimensionado,
  // el navegador emite un click que puede caer sobre el fondo y no debe cerrar.
  const downOnBackdrop = useRef(false);

  return (
    <div
      style={{ position: "fixed", inset: 0, zIndex: 1060, background: "rgba(0,0,0,.6)" }}
      onMouseDown={(e) => {
        downOnBackdrop.current = e.target === e.currentTarget;
      }}
      onClick={(e) => {
        if (downOnBackdrop.current && e.target === e.currentTarget) onClose();
        downOnBackdrop.current = false;
      }}
    >
      {/* Panel redimensionable, anclado arriba-izquierda para que el resize
          crezca hacia abajo/derecha siguiendo el cursor */}
      <div
        className="d-flex flex-column shadow-lg"
        onClick={(e) => e.stopPropagation()}
        style={{
          position: "absolute",
          top: "7vh",
          left: "max(2vw, calc(50vw - 500px))",
          resize: "both",
          overflow: "hidden",
          width: "min(1000px, 92vw)",
          height: "min(700px, 80vh)",
          minWidth: 480,
          minHeight: 260,
          maxWidth: "96vw",
          maxHeight: "90vh",
          background: "var(--bs-body-bg)",
          border: "1px solid var(--bs-border-color)",
          borderRadius: 10,
        }}
      >
        {/* Cabecera */}
        <div
          className="d-flex align-items-center gap-2 px-3 py-2 flex-shrink-0"
          style={{ background: "var(--bs-tertiary-bg)", borderBottom: "1px solid var(--bs-border-color)" }}
        >
          <span style={{ color: "var(--bs-warning)", fontSize: 15 }}>ƒ</span>
          <span
            className="font-monospace fw-semibold text-truncate"
            style={{ color: "var(--bs-body-color)", fontSize: 15 }}
            title={title}
          >
            {title}
          </span>
          <span className="flex-grow-1" />
          <button
            className={`btn btn-sm ${copied ? "btn-success" : dark ? "btn-outline-light" : "btn-outline-secondary"}`}
            onClick={() => void copy()}
            disabled={loading}
            title="Copiar todo el código"
          >
            {copied ? "✓ Copiado" : "⧉ Copiar"}
          </button>
          <button className={`btn-close${dark ? " btn-close-white" : ""}`} onClick={onClose} title="Cerrar (Esc)" />
        </div>

        {/* Cuerpo: editor con números de línea */}
        <div className="flex-grow-1" style={{ overflow: "auto" }}>
          {loading ? (
            <div className="d-flex align-items-center gap-2 p-4 text-body-secondary">
              <span className="spinner-border spinner-border-sm" /> Cargando código…
            </div>
          ) : (
            <div
              className="d-flex font-monospace"
              style={{ fontSize: 13, lineHeight: 1.6, minWidth: "fit-content" }}
            >
              <div
                className="text-end flex-shrink-0 py-2 pe-3 ps-3 user-select-none"
                style={{
                  color: "var(--bs-secondary-color)",
                  background: "var(--bs-body-bg)",
                  borderRight: "1px solid var(--bs-border-color)",
                  position: "sticky",
                  left: 0,
                }}
              >
                {lines.map((_, i) => (
                  <div key={i}>{i + 1}</div>
                ))}
              </div>
              <pre
                className="mb-0 py-2 ps-3 pe-4 flex-grow-1"
                style={{ color: "var(--bs-body-color)", overflow: "visible", tabSize: 4 }}
              >
                {lines.map((l, i) => (
                  <div key={i}>{l ? highlightSql(l, pal) : "\u00A0"}</div>
                ))}
              </pre>
            </div>
          )}
        </div>

        {/* Pie con pista de redimensionado */}
        <div
          className="d-flex justify-content-between px-3 py-1 flex-shrink-0 text-body-secondary"
          style={{ background: "var(--bs-tertiary-bg)", borderTop: "1px solid var(--bs-border-color)", fontSize: 11 }}
        >
          <span>{lines.length} líneas</span>
          <span>⤡ arrastra la esquina para redimensionar</span>
        </div>
      </div>
    </div>
  );
}

// --- Resaltado de sintaxis SQL (vistas y código de rutinas) ---

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
]);

const SQL_TYPES = new Set([
  "integer", "bigint", "smallint", "int", "int2", "int4", "int8", "serial",
  "bigserial", "text", "boolean", "bool", "numeric", "decimal", "character",
  "varying", "varchar", "char", "timestamp", "timestamptz", "date", "time",
  "timetz", "interval", "double", "precision", "real", "float4", "float8",
  "money", "json", "jsonb", "uuid", "bytea", "xml", "inet", "cidr", "macaddr",
  "bit", "void", "record", "anyelement", "anyarray", "regclass", "name", "oid",
  "tsvector", "tsquery", "zone", "without",
]);

// Paletas de sintaxis: una para temas oscuros y otra para claros, para que el
// resaltado concuerde con el tema seleccionado (los fondos/bordes usan las
// variables de Bootstrap --bs-*, que ya cambian con data-bs-theme).
interface SqlPalette {
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

const SQL_DARK: SqlPalette = {
  comment: "#6a9955",
  string: "#e5c07b",
  identQ: "#9cdcfe",
  cast: "#4ec9b0",
  number: "#d19a66",
  keyword: "#f14c4c",
  type: "#ffa657",
  func: "#56b6c2",
  dollar: "#c586c0",
};

const SQL_LIGHT: SqlPalette = {
  comment: "#008000",
  string: "#a31515",
  identQ: "#0070c1",
  cast: "#267f99",
  number: "#098658",
  keyword: "#0000ff",
  type: "#267f99",
  func: "#795e26",
  dollar: "#af00db",
};

/** Devuelve la paleta acorde al tema activo (claro/oscuro). */
function sqlPalette(): SqlPalette {
  const t = THEMES.find((x) => x.id === currentTheme());
  return t?.bs === "dark" ? SQL_DARK : SQL_LIGHT;
}

function highlightSql(sql: string, p: SqlPalette = SQL_DARK): React.ReactNode[] {
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
