/**
 * Árbol de objetos de la BD, compartido por el Explorador y el Diagrama.
 *
 * El estado de la interfaz (búsqueda, filtros, schemas expandidos, grupos
 * colapsados) se guarda en un almacén por conexión+BD que sobrevive a los
 * cambios de vista y a las pestañas nuevas: los filtros nunca se pierden.
 *
 * Dentro de cada schema los objetos se agrupan por tipo (Tablas, Vistas, …)
 * en secciones colapsables.
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type CSSProperties,
} from "react";
import { api, type ApiError, type ObjectSummary, type SchemaInfo } from "./api/client";

export const DND_MIME = "application/pgdiag-table";

export const KIND_ICON: Record<string, string> = {
  table: "▦",
  partitioned: "▤",
  view: "◉",
  matview: "◈",
  foreign: "⇄",
};

export const KIND_COLOR: Record<string, string> = {
  table: "#2a5ca8",
  partitioned: "#1c7ea0",
  view: "#7a4dbf",
  matview: "#a04d8f",
  foreign: "#3a8a62",
};

export const KIND_LABEL: Record<string, string> = {
  table: "Tabla",
  partitioned: "Tabla particionada",
  view: "Vista",
  matview: "Vista materializada",
  foreign: "Tabla foránea",
};

const KIND_GROUP: Record<string, string> = {
  table: "Tablas",
  partitioned: "Particionadas",
  view: "Vistas",
  matview: "Vistas materializadas",
  foreign: "Foráneas",
};

const KIND_ORDER = ["table", "partitioned", "view", "matview", "foreign"];

export interface TreeMenuItem {
  label: string;
  onClick: () => void;
}

// --- Almacén de estado del árbol por conexión+BD ---
//
// Es un store *reactivo* (patrón useSyncExternalStore): varios ObjectTree pueden
// estar montados a la vez (el Explorador y la vista de Diagramas viven ambos en
// el DOM, ocultándose con display:none). Un store de solo-lectura-al-montar no
// bastaba: al cambiar el filtro en uno, el otro árbol ya montado no se enteraba
// y mostraba el filtro «perdido». Con suscripción, cualquier cambio se propaga
// en vivo a todas las instancias y sobrevive a los cambios de vista.

interface TreeUiState {
  query: string;
  schemaFilters: string[]; // vacío = todos
  kindFilter: string;
  expanded: Record<string, boolean>;
  collapsedGroups: Record<string, boolean>; // clave: "schema|kind"
}

interface TreeStoreEntry {
  state: TreeUiState;
  listeners: Set<() => void>;
}

const treeStateStore = new Map<string, TreeStoreEntry>();

function getEntry(key: string): TreeStoreEntry {
  let e = treeStateStore.get(key);
  if (!e) {
    e = {
      state: { query: "", schemaFilters: [], kindFilter: "", expanded: {}, collapsedGroups: {} },
      listeners: new Set(),
    };
    treeStateStore.set(key, e);
  }
  return e;
}

function subscribeTreeState(key: string, cb: () => void): () => void {
  const e = getEntry(key);
  e.listeners.add(cb);
  return () => {
    e.listeners.delete(cb);
  };
}

/** Snapshot estable: la misma referencia mientras el estado no cambie. */
function getTreeSnapshot(key: string): TreeUiState {
  return getEntry(key).state;
}

/** Aplica un parche y notifica a todas las instancias suscritas. */
function updateTreeState(key: string, patch: Partial<TreeUiState>): void {
  const e = getEntry(key);
  e.state = { ...e.state, ...patch };
  e.listeners.forEach((l) => l());
}

// --- Caché compartida de objetos por schema (por conexión+BD) ---
//
// Antes cada ObjectTree tenía su propia caché local de objetos. Como el
// Explorador y la vista de Diagramas están montados a la vez y ahora comparten
// qué schemas están expandidos, cada expansión disparaba la MISMA consulta dos
// veces en paralelo. Esa ráfaga de conexiones simultáneas desincronizaba al
// servidor (peor con pgbouncer). Con una caché compartida + un guard global de
// «en vuelo», cada schema se consulta una sola vez y ambos árboles la reutilizan.

interface TreeDataEntry {
  loaded: Record<string, ObjectSummary[]>;
  listeners: Set<() => void>;
}

const treeDataStore = new Map<string, TreeDataEntry>();
// Peticiones en curso, clave `${storeKey}::${schema}`: evita consultas dobles.
const dataInFlight = new Set<string>();

function getDataEntry(key: string): TreeDataEntry {
  let e = treeDataStore.get(key);
  if (!e) {
    e = { loaded: {}, listeners: new Set() };
    treeDataStore.set(key, e);
  }
  return e;
}

function subscribeTreeData(key: string, cb: () => void): () => void {
  const e = getDataEntry(key);
  e.listeners.add(cb);
  return () => {
    e.listeners.delete(cb);
  };
}

function getTreeDataSnapshot(key: string): Record<string, ObjectSummary[]> {
  return getDataEntry(key).loaded;
}

function setSchemaObjects(key: string, schema: string, items: ObjectSummary[]): void {
  const e = getDataEntry(key);
  e.loaded = { ...e.loaded, [schema]: items };
  e.listeners.forEach((l) => l());
}

// Búsqueda global compartida: como el término de búsqueda también es común a
// todos los árboles, dos árboles montados dispararían la misma consulta a la
// vez. Se comparte la promesa en vuelo para que solo salga una petición; ambos
// árboles reciben el mismo resultado.
const searchInFlight = new Map<string, Promise<ObjectSummary[]>>();

function fetchSearchObjects(
  key: string,
  profileId: string,
  dbname: string,
  needle: string,
  schemaFilters: string[]
): Promise<ObjectSummary[]> {
  const fk = `${key}::search::${needle}::${schemaFilters.join(",")}`;
  let p = searchInFlight.get(fk);
  if (!p) {
    p = api
      .listObjects(profileId, dbname, {
        q: needle,
        schema: schemaFilters.length > 0 ? schemaFilters.join(",") : undefined,
      })
      .then((r) => r.items);
    searchInFlight.set(fk, p);
    void p.finally(() => searchInFlight.delete(fk));
  }
  return p;
}

const TREE_CSS = `
.pgtree-item { border-radius: 6px; transition: background .12s; }
.pgtree-item:hover { background: #eef3fb; }
.pgtree-item.pgtree-selected { background: #dbe7fb; }
.pgtree-schema { border-radius: 6px; transition: background .12s; }
.pgtree-schema:hover { background: #f0f2f5; }
.pgtree-group { border-radius: 6px; transition: background .12s; }
.pgtree-group:hover { background: #f5f6f8; }
`;

function groupByKind(items: ObjectSummary[]): [string, ObjectSummary[]][] {
  const groups = new Map<string, ObjectSummary[]>();
  for (const o of items) {
    const list = groups.get(o.kind) ?? [];
    list.push(o);
    groups.set(o.kind, list);
  }
  return KIND_ORDER.filter((k) => groups.has(k)).map((k) => [k, groups.get(k)!]);
}

function errText(e: unknown): string {
  const err = e as ApiError;
  return `${err.code ?? "ERROR"}: ${err.message ?? String(e)}${err.hint ? ` — ${err.hint}` : ""}`;
}

interface Props {
  profileId: string;
  dbname: string;
  schemas: SchemaInfo[];
  width?: number;
  hint?: string;
  selectedKey?: string | null;
  draggable?: boolean;
  presentKeys?: Set<string>;
  onItemClick?: (o: ObjectSummary) => void;
  onItemDoubleClick?: (o: ObjectSummary) => void;
  /** Ítems del menú contextual (clic derecho) para un objeto; [] = sin menú. */
  contextMenuFor?: (o: ObjectSummary) => TreeMenuItem[];
  /** Marcas del último refresh: clave → "added" | "changed". */
  badges?: Record<string, "added" | "changed">;
  onError?: (msg: string) => void;
}

export default function ObjectTree({
  profileId,
  dbname,
  schemas,
  width = 340,
  hint,
  selectedKey,
  draggable = false,
  presentKeys,
  onItemClick,
  onItemDoubleClick,
  contextMenuFor,
  badges,
  onError,
}: Props) {
  // Render incremental en grupos enormes (schemas de miles de objetos)
  const GROUP_CHUNK = 200;
  const [groupLimits, setGroupLimits] = useState<Record<string, number>>({});
  const storeKey = `${profileId}|${dbname}`;
  // Estado de filtros/expansión compartido y reactivo entre todas las instancias
  // del árbol (Explorador y Diagramas). Cualquier cambio aquí re-renderiza a los
  // demás árboles montados, así el filtro nunca se «pierde» al cambiar de vista.
  const subscribe = useCallback((cb: () => void) => subscribeTreeState(storeKey, cb), [storeKey]);
  const getSnapshot = useCallback(() => getTreeSnapshot(storeKey), [storeKey]);
  const treeState = useSyncExternalStore(subscribe, getSnapshot);
  const { query, schemaFilters, kindFilter, expanded, collapsedGroups } = treeState;

  // Objetos por schema: caché compartida y reactiva (una sola consulta por schema
  // para todos los árboles montados).
  const dataSubscribe = useCallback((cb: () => void) => subscribeTreeData(storeKey, cb), [storeKey]);
  const dataSnapshot = useCallback(() => getTreeDataSnapshot(storeKey), [storeKey]);
  const loaded = useSyncExternalStore(dataSubscribe, dataSnapshot);

  const [schemaMenuOpen, setSchemaMenuOpen] = useState(false);
  const [schemaSearch, setSchemaSearch] = useState("");
  const [loadingSchemas, setLoadingSchemas] = useState<Set<string>>(new Set());
  const [searchResults, setSearchResults] = useState<ObjectSummary[] | null>(null);
  const [menu, setMenu] = useState<{ x: number; y: number; items: TreeMenuItem[] } | null>(null);

  // Setters: escriben en el store reactivo (notifican a todas las instancias).
  const setQuery = (v: string) => updateTreeState(storeKey, { query: v });
  const setSchemaFilters = (v: string[]) => updateTreeState(storeKey, { schemaFilters: v });
  const toggleSchemaFilter = (name: string) => {
    setSchemaFilters(
      schemaFilters.includes(name)
        ? schemaFilters.filter((n) => n !== name)
        : [...schemaFilters, name]
    );
  };
  const setKindFilter = (v: string) => updateTreeState(storeKey, { kindFilter: v });
  const setExpanded = (updater: (prev: Record<string, boolean>) => Record<string, boolean>) => {
    updateTreeState(storeKey, { expanded: updater(getTreeSnapshot(storeKey).expanded) });
  };
  const setCollapsedGroups = (
    updater: (prev: Record<string, boolean>) => Record<string, boolean>
  ) => {
    updateTreeState(storeKey, {
      collapsedGroups: updater(getTreeSnapshot(storeKey).collapsedGroups),
    });
  };

  async function loadSchema(name: string) {
    // Ya cacheado por cualquier árbol: nada que hacer.
    if (getTreeDataSnapshot(storeKey)[name]) return;
    // Guard global: si otro árbol ya lo está pidiendo, no dupliques la consulta.
    const flightKey = `${storeKey}::${name}`;
    if (dataInFlight.has(flightKey)) return;
    dataInFlight.add(flightKey);
    setLoadingSchemas((s) => new Set(s).add(name));
    try {
      const r = await api.listObjects(profileId, dbname, { schema: name });
      setSchemaObjects(storeKey, name, r.items);
    } catch (e) {
      onError?.(errText(e));
    } finally {
      dataInFlight.delete(flightKey);
      setLoadingSchemas((s) => {
        const next = new Set(s);
        next.delete(name);
        return next;
      });
    }
  }

  // Cargar objetos de los schemas que quedaron expandidos de una sesión anterior
  useEffect(() => {
    for (const name of Object.keys(expanded)) {
      if (expanded[name] && !loaded[name]) void loadSchema(name);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded, schemas]);

  // Búsqueda global (debounce) sobre el snapshot cacheado en el sidecar
  useEffect(() => {
    const needle = query.trim();
    if (!needle) {
      setSearchResults(null);
      return;
    }
    const t = setTimeout(async () => {
      try {
        // El filtro de schemas se aplica en el servidor: así el límite de
        // resultados no recorta coincidencias de los schemas elegidos.
        const items = await fetchSearchObjects(storeKey, profileId, dbname, needle, schemaFilters);
        setSearchResults(items);
      } catch (e) {
        onError?.(errText(e));
      }
    }, 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, schemaFilters]);

  const visibleSchemas = useMemo(
    () =>
      schemaFilters.length > 0
        ? schemas.filter((s) => schemaFilters.includes(s.name))
        : schemas,
    [schemas, schemaFilters]
  );

  const filtersActive = query !== "" || schemaFilters.length > 0 || kindFilter !== "";

  function toggleSchema(schema: SchemaInfo) {
    const isOpen = expanded[schema.name];
    setExpanded((e) => ({ ...e, [schema.name]: !isOpen }));
    if (!isOpen && !loaded[schema.name]) void loadSchema(schema.name);
  }

  function objectRow(o: ObjectSummary, indent: number) {
    const key = `${o.schema_name}.${o.name}`;
    const inCanvas = presentKeys?.has(key) ?? false;
    const isSel = selectedKey === key;
    return (
      <div
        key={key}
        className={`pgtree-item ${isSel ? "pgtree-selected" : ""}`}
        onClick={() => onItemClick?.(o)}
        onDoubleClick={() => !inCanvas && onItemDoubleClick?.(o)}
        onContextMenu={
          contextMenuFor
            ? (e) => {
                const items = contextMenuFor(o);
                if (items.length === 0) return;
                e.preventDefault();
                setMenu({ x: e.clientX, y: e.clientY, items });
              }
            : undefined
        }
        draggable={draggable && !inCanvas}
        onDragStart={
          draggable
            ? (e) => {
                e.dataTransfer.setData(DND_MIME, key);
                e.dataTransfer.effectAllowed = "move";
              }
            : undefined
        }
        title={inCanvas ? "Ya está en el lienzo" : o.comment ?? key}
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: 6,
          cursor: inCanvas ? "default" : draggable ? "grab" : "pointer",
          opacity: inCanvas ? 0.45 : 1,
          padding: `3px 8px 3px ${indent}px`,
          whiteSpace: "nowrap",
          overflow: "hidden",
          fontSize: 13,
        }}
      >
        <span style={{ color: KIND_COLOR[o.kind] ?? "#2a5ca8", width: 14, textAlign: "center", flexShrink: 0 }}>
          {inCanvas ? "✓" : KIND_ICON[o.kind] ?? "▦"}
        </span>
        <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{o.name}</span>
        {badges?.[key] === "added" && (
          <span className="badge text-bg-success" style={{ fontSize: 9 }} title="Nueva desde el último refresh">nueva</span>
        )}
        {badges?.[key] === "changed" && (
          <span className="badge text-bg-warning" style={{ fontSize: 9 }} title="Estructura modificada desde el último refresh">±</span>
        )}
        {o.estimated_rows != null && (
          <span style={{ color: "#adb5bd", fontSize: 11, marginLeft: "auto", flexShrink: 0 }}>
            ~{o.estimated_rows}
          </span>
        )}
      </div>
    );
  }

  function kindGroups(items: ObjectSummary[], schema: string, indentBase: number, collapsible: boolean) {
    const visible = kindFilter ? items.filter((o) => o.kind === kindFilter) : items;
    return groupByKind(visible).map(([kind, objs]) => {
      const gkey = `${schema}|${kind}`;
      const isCollapsed = collapsible && (collapsedGroups[gkey] ?? false);
      return (
        <div key={gkey}>
          <div
            className="pgtree-group"
            onClick={
              collapsible
                ? () => setCollapsedGroups((c) => ({ ...c, [gkey]: !isCollapsed }))
                : undefined
            }
            style={{
              display: "flex",
              alignItems: "center",
              gap: 5,
              padding: `3px 8px 2px ${indentBase}px`,
              cursor: collapsible ? "pointer" : "default",
              userSelect: "none",
            }}
          >
            {collapsible && (
              <span style={{ fontSize: 9, color: "#868e96", width: 10 }}>
                {isCollapsed ? "▶" : "▼"}
              </span>
            )}
            <span
              style={{
                color: KIND_COLOR[kind] ?? "#495057",
                fontSize: 11,
                fontWeight: 700,
                textTransform: "uppercase",
                letterSpacing: 0.4,
              }}
            >
              {KIND_GROUP[kind]}
            </span>
            <span className="badge rounded-pill text-bg-light border" style={{ fontSize: 10 }}>
              {objs.length}
            </span>
          </div>
          {!isCollapsed && (
            <>
              {objs.slice(0, groupLimits[gkey] ?? GROUP_CHUNK).map((o) => objectRow(o, indentBase + 16))}
              {objs.length > (groupLimits[gkey] ?? GROUP_CHUNK) && (
                <a
                  className="link-primary small d-block"
                  style={{ cursor: "pointer", padding: `2px 8px 4px ${indentBase + 16}px` }}
                  onClick={() =>
                    setGroupLimits((l) => ({
                      ...l,
                      [gkey]: (l[gkey] ?? GROUP_CHUNK) + GROUP_CHUNK,
                    }))
                  }
                >
                  ▾ Mostrar {Math.min(GROUP_CHUNK, objs.length - (groupLimits[gkey] ?? GROUP_CHUNK))} más
                  ({objs.length - (groupLimits[gkey] ?? GROUP_CHUNK)} restantes)
                </a>
              )}
            </>
          )}
        </div>
      );
    });
  }

  const searchBySchema = useMemo(() => {
    if (!searchResults) return null;
    const groups = new Map<string, ObjectSummary[]>();
    for (const o of searchResults) {
      const list = groups.get(o.schema_name) ?? [];
      list.push(o);
      groups.set(o.schema_name, list);
    }
    return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [searchResults]);

  const select: CSSProperties = { maxWidth: 140, flex: 1 };

  return (
    <aside className="bg-body border-end d-flex flex-column" style={{ width }}>
      <style>{TREE_CSS}</style>

      <div style={{ padding: 8, display: "flex", gap: 6, flexWrap: "wrap" }}>
        <div className="position-relative" style={{ flex: "1 1 100%" }}>
          <input
            className="form-control form-control-sm"
            placeholder="🔍 Buscar en toda la BD…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            style={{ paddingRight: 26 }}
          />
          {query && (
            <button
              className="btn-close position-absolute top-50 end-0 translate-middle-y me-2"
              style={{ fontSize: 9 }}
              onClick={() => setQuery("")}
              title="Limpiar búsqueda"
            />
          )}
        </div>
        <div className="position-relative" style={select}>
          <button
            className="form-select form-select-sm text-start w-100"
            onClick={() => {
              setSchemaMenuOpen(!schemaMenuOpen);
              setSchemaSearch("");
            }}
            title="Filtrar por schemas (selección múltiple)"
            style={{
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              fontWeight: schemaFilters.length > 0 ? 600 : 400,
            }}
          >
            {schemaFilters.length === 0
              ? "Schemas: todos"
              : schemaFilters.length === 1
                ? schemaFilters[0]
                : `${schemaFilters.length} schemas`}
          </button>
          {schemaMenuOpen && (
            <>
              <div
                style={{ position: "fixed", inset: 0, zIndex: 1049 }}
                onClick={() => {
                  setSchemaMenuOpen(false);
                  setSchemaSearch("");
                }}
              />
              <div
                className="shadow border rounded bg-body"
                style={{
                  position: "absolute",
                  top: "calc(100% + 2px)",
                  left: 0,
                  zIndex: 1050,
                  minWidth: 230,
                  maxHeight: 320,
                  overflowY: "auto",
                  padding: "4px 0",
                }}
              >
                <div className="px-2 pt-1 pb-2 border-bottom mb-1">
                  <input
                    className="form-control form-control-sm"
                    placeholder="🔍 Buscar schema…"
                    value={schemaSearch}
                    autoFocus
                    onChange={(e) => setSchemaSearch(e.target.value)}
                    onKeyDown={(e) => e.key === "Escape" && setSchemaMenuOpen(false)}
                  />
                  <div
                    className="d-flex justify-content-between align-items-center mt-1"
                    style={{ fontSize: 12 }}
                  >
                    <span className="text-body-secondary">
                      {schemaFilters.length === 0 ? "Mostrando todos" : `${schemaFilters.length} seleccionados`}
                    </span>
                    {schemaFilters.length > 0 && (
                      <a
                        className="link-primary"
                        style={{ cursor: "pointer" }}
                        onClick={() => setSchemaFilters([])}
                      >
                        Todos
                      </a>
                    )}
                  </div>
                </div>
                {schemas.filter((s) =>
                  s.name.toLowerCase().includes(schemaSearch.trim().toLowerCase())
                ).length === 0 && (
                  <div className="px-3 py-2 text-body-secondary" style={{ fontSize: 12 }}>
                    Sin schemas que coincidan.
                  </div>
                )}
                {schemas
                  .filter((s) => s.name.toLowerCase().includes(schemaSearch.trim().toLowerCase()))
                  .map((s) => (
                  <label
                    key={s.name}
                    className="d-flex align-items-center gap-2 px-3 py-1 pgtree-item"
                    style={{ cursor: "pointer", fontSize: 13, margin: 0 }}
                  >
                    <input
                      type="checkbox"
                      className="form-check-input m-0 flex-shrink-0"
                      checked={schemaFilters.includes(s.name)}
                      onChange={() => toggleSchemaFilter(s.name)}
                    />
                    <span className="text-truncate">{s.name}</span>
                    <span className="badge rounded-pill text-bg-light border ms-auto" style={{ fontSize: 10 }}>
                      {s.table_count + s.view_count}
                    </span>
                  </label>
                ))}
              </div>
            </>
          )}
        </div>
        <select
          className="form-select form-select-sm"
          style={select}
          value={kindFilter}
          onChange={(e) => setKindFilter(e.target.value)}
          title="Filtrar por tipo"
        >
          <option value="">Tipo: todos</option>
          <option value="table">Tablas</option>
          <option value="partitioned">Particionadas</option>
          <option value="view">Vistas</option>
          <option value="matview">Vistas mat.</option>
          <option value="foreign">Foráneas</option>
        </select>
        {filtersActive && (
          <a
            className="link-secondary small"
            style={{ cursor: "pointer", flexBasis: "100%" }}
            onClick={() => {
              setQuery("");
              setSchemaFilters([]);
              setKindFilter("");
            }}
          >
            ✕ Limpiar filtros
          </a>
        )}
        {hint && (
          <p className="text-body-secondary small m-0" style={{ flexBasis: "100%" }}>{hint}</p>
        )}
      </div>

      <div style={{ overflowY: "auto", flex: 1, padding: "0 6px 12px" }}>
        {searchBySchema ? (
          searchBySchema.length === 0 ? (
            <p style={{ padding: 8, color: "#868e96" }}>Sin resultados para “{query}”.</p>
          ) : (
            searchBySchema.map(([schema, items]) => (
              <div key={schema}>
                <div style={{ padding: "6px 8px 2px", fontWeight: 600, fontSize: 13 }}>{schema}</div>
                {kindGroups(items, `search:${schema}`, 18, false)}
              </div>
            ))
          )
        ) : (
          visibleSchemas.map((s) => {
            const isOpen = expanded[s.name] ?? false;
            return (
              <div key={s.name}>
                <div
                  className="pgtree-schema"
                  onClick={() => toggleSchema(s)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    padding: "6px 8px",
                    cursor: "pointer",
                    userSelect: "none",
                  }}
                >
                  <span style={{ fontSize: 10, color: "#868e96", width: 12 }}>
                    {isOpen ? "▼" : "▶"}
                  </span>
                  <span style={{ fontWeight: 600, fontSize: 13.5 }}>{s.name}</span>
                  <span
                    className="badge rounded-pill text-bg-light border ms-auto"
                    style={{ fontSize: 10 }}
                    title={`${s.table_count} tablas · ${s.view_count} vistas`}
                  >
                    {s.table_count + s.view_count}
                  </span>
                </div>
                {isOpen &&
                  (loadingSchemas.has(s.name) && !loaded[s.name] ? (
                    <div className="d-flex align-items-center gap-2 text-body-secondary small" style={{ padding: "2px 8px 6px 30px" }}>
                      <span className="spinner-border spinner-border-sm" style={{ width: 12, height: 12 }} />
                      Cargando…
                    </div>
                  ) : (
                    kindGroups(loaded[s.name] ?? [], s.name, 26, true)
                  ))}
              </div>
            );
          })
        )}
        {!searchBySchema && visibleSchemas.length === 0 && (
          <p style={{ padding: 8, color: "#868e96" }}>Sin schemas visibles para este rol.</p>
        )}
      </div>

      {menu && (
        <>
          <div
            style={{ position: "fixed", inset: 0, zIndex: 1050 }}
            onClick={() => setMenu(null)}
            onContextMenu={(e) => {
              e.preventDefault();
              setMenu(null);
            }}
          />
          <div
            className="shadow"
            style={{
              position: "fixed",
              top: menu.y,
              left: menu.x,
              zIndex: 1051,
              background: "var(--bs-body-bg)",
              border: "1px solid var(--bs-border-color)",
              borderRadius: 6,
              minWidth: 230,
              overflow: "hidden",
            }}
          >
            {menu.items.map((it, i) => (
              <div
                key={i}
                onClick={() => {
                  setMenu(null);
                  it.onClick();
                }}
                onMouseEnter={(e) => ((e.target as HTMLElement).style.background = "#eef3fb")}
                onMouseLeave={(e) => ((e.target as HTMLElement).style.background = "")}
                style={{ padding: "7px 12px", cursor: "pointer", fontSize: 13 }}
              >
                {it.label}
              </div>
            ))}
          </div>
        </>
      )}
    </aside>
  );
}
