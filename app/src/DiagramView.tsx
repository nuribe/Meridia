/**
 * Lienzo de diagramas ER con pestañas (múltiples diagramas abiertos).
 *
 * - Arrastra tablas desde el árbol; las relaciones (con cardinalidad) se
 *   derivan automáticamente del conjunto de tablas presentes — las aristas
 *   se recalculan ante cualquier cambio (sin carreras, incluye inter-schema).
 * - Clic en una relación: resalta las columnas implicadas en ambas tablas y
 *   anima el flujo desde la tabla origen (FK) hacia la destino.
 * - Clic en una COLUMNA: traza todas las relaciones en las que participa,
 *   marca las tablas destino y ofrece un panel para saltar a ellas.
 * - Los conectores se reparten por todo el contorno del nodo (edgeRouting.ts):
 *   una tabla con muchas relaciones ya no las concentra en un solo punto, y el
 *   punto de enganche se desliza al arrastrar en vez de saltar.
 * - ⇲ en un nodo añade todas sus tablas relacionadas.
 * - Sticky notes redimensionables y con colores.
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useNodesState,
  useReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import dagre from "@dagrejs/dagre";
import { toPng, toSvg } from "html-to-image";
import {
  api,
  diagramsApi,
  type ApiError,
  type DiagramNote,
  type DiagramSummary,
  type PgDiagFile,
  type RelationshipInfo,
  type SchemaInfo,
  type ViewJoin,
  type TableDetail,
} from "./api/client";
import TableNode, { type NodeCustom } from "./TableNode";
import { AnchorUsageContext, handleId } from "./anchors";
import { routeConns, type Rect } from "./edgeRouting";
import RelEdge from "./RelEdge";
import SelfLoopEdge from "./SelfLoopEdge";
import NoteNode, { NOTE_PALETTE } from "./NoteNode";
import ObjectTree, { DND_MIME } from "./ObjectTree";
import ModeSwitch from "./ModeSwitch";
import ThemeMenu from "./ThemeMenu";
import { openTextFile, saveFile, saveTextFile, pickDirectory } from "./files";

const nodeTypes = { table: TableNode, note: NoteNode };
const edgeTypes = { selfloop: SelfLoopEdge, rel: RelEdge };

const FLOW_CSS = `
.react-flow__edge.pgdiag-flow .react-flow__edge-path {
  stroke: #f59e0b !important;
  stroke-width: 3.5 !important;
  stroke-dasharray: 10 7;
  animation: pgdiagFlow .45s linear infinite;
  filter: drop-shadow(0 0 4px rgba(245, 158, 11, .85));
}
@keyframes pgdiagFlow { to { stroke-dashoffset: -17; } }
.react-flow__edge.pgdiag-flow .react-flow__edge-text { font-weight: 700; }
.react-flow__node.pgdiag-hit { animation: pgdiagHit 1.2s ease-out 2; }
@keyframes pgdiagHit {
  0%, 100% { filter: none; }
  30% { filter: drop-shadow(0 0 0 3px #f59e0b) drop-shadow(0 0 10px rgba(245,158,11,.9)); }
}
/* Trazado de relación por columna: origen en azul, destinos en ámbar. */
.react-flow__node.pgdiag-origin {
  filter: drop-shadow(0 0 5px rgba(37,99,235,.9)) drop-shadow(0 0 11px rgba(37,99,235,.45));
}
.react-flow__node.pgdiag-dest {
  filter: drop-shadow(0 0 5px rgba(245,158,11,.95)) drop-shadow(0 0 12px rgba(245,158,11,.5));
}
`;

function errText(e: unknown): string {
  const err = e as ApiError;
  return `${err.code ?? "ERROR"}: ${err.message ?? String(e)}${err.hint ? ` — ${err.hint}` : ""}`;
}

function relId(r: RelationshipInfo): string {
  return `${r.fk_name}:${r.source}->${r.target}`;
}

function nodeHeight(t: TableDetail, custom: NodeCustom): number {
  if (custom.collapsed) return 52;
  let visible = t.columns.filter((c) => !(custom.hidden ?? []).includes(c.name)).length;
  if (custom.display === "keys") {
    const fkCols = new Set(t.foreign_keys.flatMap((fk) => fk.columns));
    visible = t.columns.filter((c) => c.is_pk || fkCols.has(c.name)).length;
  } else if (custom.display !== "all") {
    visible = Math.min(visible, 14);
  }
  return 58 + visible * 17;
}

/** Conexión visible del lienzo: FK derivada del catálogo o join de una vista. */
interface Conn {
  id: string;
  source: string;
  target: string;
  rel?: RelationshipInfo;
  join?: ViewJoin;
}

/** Columnas que la conexión toca en cada extremo. */
function connColumns(c: Conn): { source: string[]; target: string[] } {
  if (c.rel) return { source: c.rel.columns, target: c.rel.ref_columns };
  return { source: c.join?.source_columns ?? [], target: c.join?.target_columns ?? [] };
}

const EXPORT_FILTER = (el: HTMLElement) => {
  const cls = el.classList;
  return !(
    cls?.contains("react-flow__minimap") ||
    cls?.contains("react-flow__controls") ||
    cls?.contains("react-flow__panel") ||
    cls?.contains("react-flow__attribution")
  );
};

interface CanvasProps {
  profileId: string;
  dbname: string;
  schemas: SchemaInfo[];
  /** Si está definido, la pestaña se inicializa con el diagrama de esta vista. */
  initialViewKey?: string;
  /** Modo sin conexión: sin árbol ni APIs; todo sale del documento. */
  offline?: boolean;
  /** Documento .pgdiag v2 a cargar al montar. */
  initialDoc?: PgDiagFile | null;
  onCreateViewDiagram: (viewKey: string) => void;
  onTitleChange: (title: string) => void;
  onError: (msg: string) => void;
}

function DiagramCanvas({
  profileId,
  dbname,
  schemas,
  initialViewKey,
  offline = false,
  initialDoc = null,
  onCreateViewDiagram,
  onTitleChange,
  onError,
}: CanvasProps) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [rels, setRels] = useState<RelationshipInfo[]>([]);
  const [selectedRel, setSelectedRel] = useState<string | null>(null);
  const [selectedCol, setSelectedCol] = useState<{ node: string; col: string } | null>(null);
  const [status, setStatus] = useState("");
  const [diagramId, setDiagramId] = useState<string | null>(null);
  const [diagramName, setDiagramNameRaw] = useState("Nuevo diagrama");
  const [saved, setSaved] = useState<DiagramSummary[]>([]);
  const [dir, setDir] = useState<string>("");
  const [viewJoins, setViewJoins] = useState<ViewJoin[]>([]);
  const wrapper = useRef<HTMLDivElement>(null);
  const pendingLayout = useRef(false);
  const fileRels = useRef<RelationshipInfo[]>([]);
  const relSeq = useRef(0);
  const noteSeq = useRef(1);
  const { screenToFlowPosition, fitView, setCenter, getNode, getZoom } = useReactFlow();
  const [search, setSearch] = useState("");

  function setDiagramName(name: string) {
    setDiagramNameRaw(name);
    onTitleChange(name);
  }

  useEffect(() => {
    void reloadSaved();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profileId, dbname]);

  async function reloadSaved() {
    if (offline) return;
    try {
      // Todos los diagramas de la carpeta configurada (sin filtrar por conexión).
      const [r, d] = await Promise.all([diagramsApi.listAll(), diagramsApi.getDir()]);
      setSaved(r.diagrams);
      setDir(d.dir);
    } catch {
      /* no crítico */
    }
  }

  /** Elige una carpeta como directorio por defecto de diagramas y recarga. */
  async function chooseDir() {
    try {
      const picked = await pickDirectory();
      if (!picked) return;
      await diagramsApi.setDir(picked);
      await reloadSaved();
      setStatus("Carpeta de diagramas actualizada ✔");
      setTimeout(() => setStatus(""), 1800);
    } catch (e) {
      onError(errText(e));
    }
  }

  const tableKeys = useMemo(
    () => nodes.filter((n) => n.type === "table").map((n) => n.id).sort(),
    [nodes]
  );
  const tableKeysStr = tableKeys.join("|");
  const present = useMemo(() => new Set(tableKeys), [tableKeys]);

  // --- Relaciones: única fuente de verdad, recalculada ante cambios del conjunto ---
  useEffect(() => {
    if (offline) {
      const present = new Set(tableKeys);
      setRels(fileRels.current.filter((r) => present.has(r.source) && present.has(r.target)));
      return;
    }
    const seq = ++relSeq.current;
    if (tableKeys.length === 0) {
      setRels([]);
      return;
    }
    diagramsApi
      .relationships(profileId, dbname, tableKeys)
      .then((r) => {
        if (seq === relSeq.current) setRels(r.relationships);
      })
      .catch((e) => onError(errText(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tableKeysStr, profileId, dbname]);

  // Caja de cada nodo. No basta el centro: para saber si dos tablas se miran de
  // frente (y por tanto si cabe una arista recta) hacen falta alto y ancho.
  const rects = useMemo(() => {
    const m: Record<string, Rect> = {};
    for (const n of nodes) {
      if (n.type === "table") {
        m[n.id] = {
          x: n.position.x,
          y: n.position.y,
          w: n.measured?.width ?? 230,
          h: n.measured?.height ?? 150,
        };
      }
    }
    return m;
  }, [nodes]);

  // --- Conexiones visibles: FKs + joins de vista ---
  // Los joins de una vista reemplazan a las FK del mismo par de tablas, para
  // que el diagrama se lea como la consulta.
  const conns = useMemo<Conn[]>(() => {
    const joinPairs = new Set(viewJoins.map((j) => [j.source, j.target].sort().join("|")));
    const out: Conn[] = [];
    for (const r of rels) {
      if (joinPairs.has([r.source, r.target].sort().join("|"))) continue;
      out.push({ id: relId(r), source: r.source, target: r.target, rel: r });
    }
    viewJoins.forEach((j, i) => {
      if (!present.has(j.source) || !present.has(j.target)) return;
      out.push({ id: `join:${i}:${j.source}->${j.target}`, source: j.source, target: j.target, join: j });
    });
    return out;
  }, [rels, viewJoins, present]);

  // Lado y punto exacto de cada extremo. Se recalcula al mover las tablas: la
  // relación "se mueve" con ellas y los anclajes se deslizan por el contorno.
  const routing = useMemo(() => routeConns(conns, rects), [conns, rects]);

  // --- Selección: una arista (clic en la relación) o una columna (clic en el
  // campo, que traza todas las relaciones en las que participa). ---
  const activeEdgeIds = useMemo(() => {
    if (selectedRel) return new Set([selectedRel]);
    if (!selectedCol) return new Set<string>();
    const ids = new Set<string>();
    for (const c of conns) {
      const cols = connColumns(c);
      const hit =
        (c.source === selectedCol.node && cols.source.includes(selectedCol.col)) ||
        (c.target === selectedCol.node && cols.target.includes(selectedCol.col));
      if (hit) ids.add(c.id);
    }
    return ids;
  }, [selectedRel, selectedCol, conns]);

  const allEdges: Edge[] = useMemo(() => {
    // Varias relaciones reflexivas en la misma tabla se dibujan como lazos
    // concéntricos (`ring`) para que no se solapen entre sí.
    const ring = new Map<string, number>();
    const perNode = new Map<string, number>();
    for (const c of conns) {
      if (c.source !== c.target) continue;
      const k = perNode.get(c.source) ?? 0;
      ring.set(c.id, k);
      perNode.set(c.source, k + 1);
    }
    return conns.map((c) => {
      const isSel = activeEdgeIds.has(c.id);
      const selfRef = c.source === c.target;
      const ends = routing.anchors[c.id];
      // Los anclajes viajan en `data`: la arista los necesita para calcular sus
      // extremos por su cuenta (ver RelEdge / edgeAnchors).
      const anchors = { sourceAnchor: ends?.source, targetAnchor: ends?.target };
      const base = {
        id: c.id,
        source: c.source,
        target: c.target,
        sourceHandle: ends && handleId("s", ends.source.side),
        targetHandle: ends && handleId("t", ends.target.side),
        className: isSel ? "pgdiag-flow" : undefined,
        labelBgStyle: { fill: "#fff", fillOpacity: 0.85 },
      };
      if (c.join) {
        return {
          ...base,
          type: "rel",
          label: c.join.join_type,
          labelStyle: { fontSize: 11, fontWeight: 700, fill: isSel ? "#b45309" : "#6d28d9" },
          style: { stroke: "#8a63d2", strokeWidth: 2 },
          markerEnd: { type: MarkerType.ArrowClosed, color: isSel ? "#f59e0b" : "#8a63d2" },
          data: anchors,
        };
      }
      const r = c.rel!;
      // Las reflexivas llevan color propio (índigo) y trazo algo más grueso:
      // sin eso se confunden con las aristas que solo pasan cerca del nodo.
      const stroke = selfRef ? (isSel ? "#f59e0b" : "#7c3aed") : "#5b8def";
      return {
        ...base,
        type: selfRef ? "selfloop" : "rel",
        label: r.inferred ? `${r.cardinality} (inferida)` : r.cardinality,
        labelStyle: {
          fontSize: 11,
          fontWeight: selfRef ? 700 : 600,
          fill: isSel ? "#b45309" : selfRef ? "#6d28d9" : "#12305c",
          color: isSel ? "#b45309" : selfRef ? "#6d28d9" : "#12305c",
        },
        style: { stroke, strokeWidth: selfRef ? 2.2 : 1.8 },
        markerEnd: { type: MarkerType.ArrowClosed, color: stroke },
        data: {
          ...anchors,
          columns: r.columns,
          ref_columns: r.ref_columns,
          ring: ring.get(c.id) ?? 0,
          selected: isSel,
        },
      };
    });
  }, [conns, routing, activeEdgeIds]);

  /** Destinos alcanzables desde la columna seleccionada (para el panel). */
  const colTrace = useMemo(() => {
    if (!selectedCol) return [];
    const out: { edgeId: string; other: string; otherCols: string; label: string; outgoing: boolean }[] = [];
    for (const c of conns) {
      if (!activeEdgeIds.has(c.id)) continue;
      const cols = connColumns(c);
      const outgoing = c.source === selectedCol.node && cols.source.includes(selectedCol.col);
      out.push({
        edgeId: c.id,
        other: outgoing ? c.target : c.source,
        otherCols: (outgoing ? cols.target : cols.source).join(", "),
        label: c.rel
          ? c.rel.inferred
            ? `${c.rel.cardinality} (inferida)`
            : c.rel.cardinality
          : (c.join?.join_type ?? ""),
        outgoing,
      });
    }
    return out;
  }, [selectedCol, conns, activeEdgeIds]);

  // Resaltado: columnas implicadas en las relaciones activas + marcado de las
  // tablas origen/destino, para poder seguir la relación de un vistazo.
  useEffect(() => {
    const hl = new Map<string, Set<string>>();
    const add = (node: string, cols: string[]) => {
      const s = hl.get(node) ?? new Set<string>();
      cols.forEach((c) => s.add(c));
      hl.set(node, s);
    };
    const involved = new Set<string>();
    for (const c of conns) {
      if (!activeEdgeIds.has(c.id)) continue;
      const cols = connColumns(c);
      add(c.source, cols.source);
      add(c.target, cols.target);
      involved.add(c.source);
      involved.add(c.target);
    }
    const origin = selectedCol?.node ?? null;
    setNodes((ns) => {
      let changed = false;
      const next = ns.map((n) => {
        if (n.type !== "table") return n;
        const hlCols = [...(hl.get(n.id) ?? [])];
        const picked = origin === n.id ? selectedCol!.col : undefined;
        const cls =
          origin === n.id
            ? "pgdiag-origin"
            : origin && involved.has(n.id)
              ? "pgdiag-dest"
              : undefined;
        const d = n.data as { highlight?: string[]; pickedColumn?: string };
        const prev = d.highlight ?? [];
        if (
          prev.length === hlCols.length &&
          prev.every((c, i) => c === hlCols[i]) &&
          d.pickedColumn === picked &&
          n.className === cls
        ) {
          return n;
        }
        changed = true;
        return { ...n, className: cls, data: { ...n.data, highlight: hlCols, pickedColumn: picked } };
      });
      // Devolver la MISMA referencia si nada cambió: `conns` depende de los
      // nodos, así que un array nuevo aquí dispararía un bucle infinito.
      return changed ? next : ns;
    });
  }, [activeEdgeIds, conns, selectedCol, setNodes]);

  const removeTable = useCallback(
    (key: string) => setNodes((ns) => ns.filter((n) => n.id !== key)),
    [setNodes]
  );

  /** Clic en una columna: traza sus relaciones (segundo clic, las suelta). */
  const pickColumn = useCallback((key: string, column: string) => {
    setSelectedRel(null);
    setSelectedCol((cur) => (cur && cur.node === key && cur.col === column ? null : { node: key, col: column }));
  }, []);

  const clearSelection = useCallback(() => {
    setSelectedRel(null);
    setSelectedCol(null);
  }, []);

  const updateCustom = useCallback(
    (key: string, patch: Partial<NodeCustom>) => {
      setNodes((ns) =>
        ns.map((n) =>
          n.id === key
            ? { ...n, data: { ...n.data, custom: { ...(n.data as { custom: NodeCustom }).custom, ...patch } } }
            : n
        )
      );
    },
    [setNodes]
  );

  // --- Notas ---
  const updateNote = useCallback(
    (id: string, patch: Partial<{ text: string; color: string }>) => {
      setNodes((ns) => ns.map((n) => (n.id === id ? { ...n, data: { ...n.data, ...patch } } : n)));
    },
    [setNodes]
  );

  const removeNote = useCallback(
    (id: string) => setNodes((ns) => ns.filter((n) => n.id !== id)),
    [setNodes]
  );

  const addNote = useCallback(
    (pos?: { x: number; y: number }, note?: DiagramNote) => {
      const id = note?.id ?? `note-${Date.now()}-${noteSeq.current++}`;
      setNodes((ns) => [
        ...ns,
        {
          id,
          type: "note",
          position: pos ?? { x: 120 + Math.random() * 200, y: 100 + Math.random() * 160 },
          style: { width: note?.width ?? 180, height: note?.height ?? 120 },
          data: {
            text: note?.text ?? "",
            color: note?.color ?? NOTE_PALETTE[0],
            onChange: updateNote,
            onRemove: removeNote,
          },
          zIndex: -1,
        } as Node,
      ]);
    },
    [setNodes, updateNote, removeNote]
  );

  // --- Tablas ---
  const buildTableNode = useCallback(
    (
      key: string,
      table: TableDetail,
      pos: { x: number; y: number },
      custom: NodeCustom,
      joinHighlight?: string[]
    ): Node =>
      ({
        id: key,
        type: "table",
        position: pos,
        data: {
          table,
          custom,
          joinHighlight,
          onRemove: removeTable,
          onCustomChange: updateCustom,
          onAddRelated: (k: string, d: "in" | "out" | "both") => void addRelatedRef.current(k, d),
          onColumnClick: pickColumn,
        },
      }) as Node,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [removeTable, updateCustom, pickColumn]
  );

  const addTable = useCallback(
    async (key: string, pos: { x: number; y: number }) => {
      const [schema, ...rest] = key.split(".");
      try {
        const r = await api.tableDetail(profileId, dbname, schema, rest.join("."));
        setNodes((ns) =>
          ns.some((n) => n.id === key) ? ns : [...ns, buildTableNode(key, r.table, pos, {})]
        );
      } catch (e) {
        onError(errText(e));
      }
    },
    [profileId, dbname, setNodes, buildTableNode, onError]
  );

  // Añadir tablas relacionadas con una dada (⇲ en el nodo), según dirección:
  // "in" = que la referencian (debajo), "out" = a las que apunta, "both" = todas.
  const addRelated = useCallback(
    async (key: string, direction: "in" | "out" | "both" = "both") => {
      if (offline) {
        setStatus("No disponible sin conexión");
        setTimeout(() => setStatus(""), 1800);
        return;
      }
      const [schema, ...rest] = key.split(".");
      setStatus("Buscando tablas relacionadas…");
      try {
        const r = await diagramsApi.relatedTables(profileId, dbname, schema, rest.join("."), direction);
        const missing = r.related.filter((k) => !present.has(k));
        if (missing.length === 0) {
          setStatus(r.related.length === 0 ? "Sin tablas relacionadas en esa dirección" : "Ya están todas en el lienzo");
          setTimeout(() => setStatus(""), 2000);
          return;
        }
        const origin = nodes.find((n) => n.id === key)?.position ?? { x: 200, y: 200 };
        await Promise.all(
          missing.map((k, i) => {
            const angle = (2 * Math.PI * i) / missing.length - Math.PI / 2;
            return addTable(k, {
              x: origin.x + Math.cos(angle) * 380,
              y: origin.y + Math.sin(angle) * 300,
            });
          })
        );
        setStatus(`${missing.length} tablas relacionadas añadidas`);
        setTimeout(() => setStatus(""), 2000);
      } catch (e) {
        onError(errText(e));
        setStatus("");
      }
    },
    [profileId, dbname, present, nodes, addTable, onError]
  );
  const addRelatedRef = useRef(addRelated);
  useEffect(() => {
    addRelatedRef.current = addRelated;
  }, [addRelated]);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const key = e.dataTransfer.getData(DND_MIME);
      if (!key) return;
      // Si hubiera un `zoom` CSS aplicado en la raíz (respaldo de navegador),
      // las coordenadas del drop quedan escaladas respecto a las que espera
      // React Flow; lo normalizamos. En Tauri se usa zoom nativo (sin `zoom`
      // CSS), así que el factor es 1 y esto no cambia nada.
      const zoomStr = getComputedStyle(document.documentElement).zoom;
      const cssZoom = zoomStr && zoomStr !== "normal" ? parseFloat(zoomStr) || 1 : 1;
      const pos = screenToFlowPosition({ x: e.clientX / cssZoom, y: e.clientY / cssZoom });
      void addTable(key, pos);
    },
    [addTable, screenToFlowPosition]
  );

  // --- Auto-layout ---
  const autoLayout = useCallback(() => {
    const g = new dagre.graphlib.Graph();
    g.setGraph({ rankdir: "LR", nodesep: 60, ranksep: 120 });
    g.setDefaultEdgeLabel(() => ({}));
    for (const n of nodes) {
      if (n.type === "table") {
        const d = n.data as { table: TableDetail; custom: NodeCustom };
        g.setNode(n.id, { width: 230, height: nodeHeight(d.table, d.custom) });
      }
    }
    for (const e of allEdges) {
      if (g.hasNode(e.source) && g.hasNode(e.target)) g.setEdge(e.source, e.target);
    }
    dagre.layout(g);
    setNodes((ns) =>
      ns.map((n) => {
        if (n.type !== "table") return n;
        const p = g.node(n.id);
        return { ...n, position: { x: p.x - 115, y: p.y - p.height / 2 } };
      })
    );
    setTimeout(() => void fitView({ padding: 0.15 }), 50);
  }, [nodes, allEdges, setNodes, fitView]);

  const autoLayoutRef = useRef(autoLayout);
  useEffect(() => {
    autoLayoutRef.current = autoLayout;
  }, [autoLayout]);

  // Layout diferido: tras inicializar desde una vista, esperar a que lleguen
  // las aristas y las medidas de los nodos antes de ordenar.
  useEffect(() => {
    if (pendingLayout.current && nodes.length > 0) {
      pendingLayout.current = false;
      setTimeout(() => autoLayoutRef.current(), 180);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allEdges, nodes.length]);

  // Inicialización de la pestaña "diagrama de una vista"
  useEffect(() => {
    if (!initialViewKey) return;
    void (async () => {
      setStatus("Creando diagrama de la vista…");
      try {
        const [schema, ...rest] = initialViewKey.split(".");
        const viewName = rest.join(".");
        const dep = await diagramsApi.viewDependsOn(profileId, dbname, schema, viewName);
        // Solo las tablas que componen la vista (la vista en sí no va al lienzo)
        const keys = dep.tables.filter((k) => k !== initialViewKey);
        const details = await Promise.all(
          keys.map(async (k) => {
            const [sc, ...r2] = k.split(".");
            const rr = await api.tableDetail(profileId, dbname, sc, r2.join("."));
            return { k, table: rr.table };
          })
        );
        setDiagramName(`Vista ${viewName}`);
        setViewJoins(dep.joins);
        // Columnas que intervienen en algún join, por tabla (resaltado permanente)
        const joinCols = new Map<string, Set<string>>();
        for (const j of dep.joins) {
          const sset = joinCols.get(j.source) ?? new Set<string>();
          (j.source_columns ?? []).forEach((c) => sset.add(c));
          joinCols.set(j.source, sset);
          const tset = joinCols.get(j.target) ?? new Set<string>();
          (j.target_columns ?? []).forEach((c) => tset.add(c));
          joinCols.set(j.target, tset);
        }
        setNodes(
          details.map(({ k, table }, i) =>
            buildTableNode(
              k,
              table,
              { x: (i % 3) * 300 + 60, y: Math.floor(i / 3) * 300 + 40 },
              {},
              [...(joinCols.get(k) ?? [])]
            )
          )
        );
        pendingLayout.current = true;
        setStatus("");
      } catch (e) {
        onError(errText(e));
        setStatus("");
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialViewKey]);

  // --- Serialización ---
  function collectNodes() {
    return nodes
      .filter((n) => n.type === "table")
      .map((n) => {
        const custom = (n.data as { custom: NodeCustom }).custom;
        return {
          table: n.id,
          x: n.position.x,
          y: n.position.y,
          color: custom.color ?? null,
          collapsed: custom.collapsed ?? false,
          hidden_columns: custom.hidden ?? [],
          display: custom.display ?? "default",
        };
      });
  }

  function collectNotes(): DiagramNote[] {
    return nodes
      .filter((n) => n.type === "note")
      .map((n) => {
        const d = n.data as { text: string; color: string };
        const style = (n.style ?? {}) as { width?: number | string; height?: number | string };
        return {
          id: n.id,
          text: d.text,
          x: n.position.x,
          y: n.position.y,
          width: Number(n.width ?? style.width ?? 180),
          height: Number(n.height ?? style.height ?? 120),
          color: d.color,
        };
      });
  }

  async function save() {
    setStatus("Guardando…");
    try {
      const payload = { name: diagramName, nodes: collectNodes(), notes: collectNotes() };
      if (diagramId) {
        await diagramsApi.update(diagramId, payload);
      } else {
        const r = await diagramsApi.create({ ...payload, profile_id: profileId, dbname });
        setDiagramId(r.diagram.id);
      }
      await reloadSaved();
      setStatus("Guardado ✔");
      setTimeout(() => setStatus(""), 1500);
    } catch (e) {
      onError(errText(e));
      setStatus("");
    }
  }

  async function open(id: string) {
    if (!id) return;
    // Aviso si el diagrama pertenece a otra base de datos: se intentará abrir
    // contra la conexión actual, pero puede que falten tablas.
    const meta = saved.find((s) => s.id === id);
    if (meta && meta.dbname !== dbname) {
      onError(
        `Este diagrama se creó para la base «${meta.dbname}» y estás en «${dbname}». ` +
          "Algunas tablas podrían no existir aquí."
      );
    }
    setStatus("Abriendo…");
    try {
      const { diagram } = await diagramsApi.get(id);
      const details = await Promise.all(
        diagram.nodes.map(async (np) => {
          const [schema, ...rest] = np.table.split(".");
          const r = await api.tableDetail(profileId, dbname, schema, rest.join("."));
          return { np, table: r.table };
        })
      );
      setNodes([]);
      clearSelection();
      setNodes(
        details.map(({ np, table }) =>
          buildTableNode(np.table, table, { x: np.x, y: np.y }, {
            color: np.color ?? undefined,
            collapsed: np.collapsed ?? false,
            hidden: np.hidden_columns ?? [],
            display: (np.display as NodeCustom["display"]) ?? "default",
          })
        )
      );
      for (const note of diagram.notes ?? []) {
        addNote({ x: note.x, y: note.y }, note);
      }
      setDiagramId(diagram.id);
      setDiagramName(diagram.name);
      setStatus("");
      setTimeout(() => void fitView({ padding: 0.15 }), 100);
    } catch (e) {
      onError(errText(e));
      setStatus("");
    }
  }

  function newDiagram() {
    setDiagramId(null);
    setDiagramName("Nuevo diagrama");
    setNodes([]);
    clearSelection();
  }

  // --- Buscador de nodos del diagrama ---
  const searchMatches = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return [] as { id: string; label: string }[];
    return nodes
      .filter((n) => n.type === "table" && n.id.toLowerCase().includes(q))
      .map((n) => ({ id: n.id, label: n.id }));
  }, [search, nodes]);

  /** Centra la vista en un nodo y lo resalta con un destello. */
  function focusNode(id: string) {
    const n = getNode(id);
    if (!n) return;
    const w = n.measured?.width ?? 230;
    const h = n.measured?.height ?? 150;
    void setCenter(n.position.x + w / 2, n.position.y + h / 2, {
      zoom: Math.max(getZoom(), 0.8),
      duration: 500,
    });
    const el = wrapper.current?.querySelector<HTMLElement>(
      `.react-flow__node[data-id="${CSS.escape(id)}"]`
    );
    if (el) {
      el.classList.remove("pgdiag-hit");
      void el.offsetWidth; // reinicia la animación
      el.classList.add("pgdiag-hit");
      setTimeout(() => el.classList.remove("pgdiag-hit"), 2600);
    }
  }

  // --- Export ---
  async function exportImage(format: "png" | "svg") {
    const el = wrapper.current?.querySelector(".react-flow") as HTMLElement | null;
    if (!el) return;
    setStatus(`Exportando ${format.toUpperCase()}…`);
    try {
      const opts = { backgroundColor: "#ffffff", filter: EXPORT_FILTER, pixelRatio: 2 };
      const dataUrl = format === "png" ? await toPng(el, opts) : await toSvg(el, opts);
      const saved = await saveFile(`${diagramName || "diagrama"}.${format}`, dataUrl);
      setStatus(saved ? "Exportado ✔" : "");
      if (saved) setTimeout(() => setStatus(""), 2000);
    } catch (e) {
      onError(`Export falló: ${String(e)}`);
      setStatus("");
    }
  }

  async function exportText(format: "mermaid" | "dbml") {
    setStatus(`Exportando ${format.toUpperCase()}…`);
    try {
      const r = await diagramsApi.exportModel(profileId, dbname, tableKeys, format);
      const saved = await saveTextFile(`${diagramName || "diagrama"}.${r.extension}`, r.content);
      setStatus(saved ? "Exportado ✔" : "");
      if (saved) setTimeout(() => setStatus(""), 2000);
    } catch (e) {
      onError(errText(e));
      setStatus("");
    }
  }

  /** Documento .pgdiag v2 autocontenido (visualizable sin conexión). */
  function buildDoc(): PgDiagFile {
    const tables: Record<string, TableDetail> = {};
    for (const n of nodes) {
      if (n.type === "table") tables[n.id] = (n.data as { table: TableDetail }).table;
    }
    return {
      format: "pgdiag",
      format_version: 2,
      name: diagramName,
      dbname,
      nodes: collectNodes(),
      notes: collectNotes(),
      tables,
      relationships: rels,
    };
  }

  /** Guardar como… (el usuario elige la ubicación). */
  async function saveToFile() {
    const saved = await saveTextFile(
      `${diagramName || "diagrama"}.pgdiag`,
      JSON.stringify(buildDoc(), null, 2),
      "application/json"
    );
    setStatus(saved ? "Archivo guardado ✔" : "");
    if (saved) setTimeout(() => setStatus(""), 2000);
  }

  /** Cargar un documento en esta pestaña (desde archivo o al montar). */
  const loadDoc = useCallback(
    (doc: PgDiagFile, layout: boolean) => {
      setDiagramName(doc.name || "Diagrama");
      setViewJoins([]);
      clearSelection();
      fileRels.current = doc.relationships ?? [];
      const nodesFromDoc: Node[] = [];
      for (const np of doc.nodes ?? []) {
        const table = doc.tables?.[np.table];
        if (!table) continue;
        nodesFromDoc.push(
          buildTableNode(np.table, table, { x: np.x, y: np.y }, {
            color: np.color ?? undefined,
            collapsed: np.collapsed ?? false,
            hidden: np.hidden_columns ?? [],
            display: (np.display as NodeCustom["display"]) ?? "default",
          })
        );
      }
      setNodes(nodesFromDoc);
      for (const note of doc.notes ?? []) {
        addNote({ x: note.x, y: note.y }, note);
      }
      if (layout) pendingLayout.current = true;
      setTimeout(() => void fitView({ padding: 0.15 }), 120);
    },
    [buildTableNode, setNodes, addNote, fitView]
  );

  /** Abrir… (el usuario elige el archivo). */
  async function openFromDisk() {
    const f = await openTextFile(["pgdiag", "json"]);
    if (!f) return;
    try {
      const doc = JSON.parse(f.content) as PgDiagFile;
      if (doc.format !== "pgdiag" || !doc.tables) {
        onError(
          "El archivo no es un .pgdiag v2. Si fue guardado con una versión anterior, ábrelo conectado y vuelve a guardarlo."
        );
        return;
      }
      loadDoc(doc, false);
    } catch {
      onError("Archivo .pgdiag inválido: no se pudo interpretar el JSON.");
    }
  }

  // Documento inicial (modo sin conexión o pestaña restaurada)
  useEffect(() => {
    if (initialDoc) loadDoc(initialDoc, false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const empty = nodes.length === 0;

  return (
    <div className="d-flex flex-grow-1" style={{ minHeight: 0 }}>
      {/* Árbol de objetos (o aviso en modo sin conexión) */}
      {offline ? (
        <aside
          className="bg-body border-end d-flex flex-column align-items-center text-center p-3"
          style={{ width: 220 }}
        >
          <div className="fs-1 mt-3">📄</div>
          <div className="fw-semibold mt-2">Modo sin conexión</div>
          <small className="text-body-secondary mt-2">
            Diagrama cargado desde archivo. Puedes mover tablas, editar notas y
            exportar. Conéctate a la BD para añadir objetos nuevos.
          </small>
        </aside>
      ) : (
      <ObjectTree
        profileId={profileId}
        dbname={dbname}
        schemas={schemas}
        width={300}
        draggable
        presentKeys={present}
        hint="Arrastra una tabla al lienzo (o doble clic). ⇲ en un nodo trae sus relacionadas. Clic en una columna para seguir su relación."
        onItemDoubleClick={(o) =>
          void addTable(`${o.schema_name}.${o.name}`, {
            x: 80 + Math.random() * 240,
            y: 60 + Math.random() * 240,
          })
        }
        contextMenuFor={(o) =>
          o.kind === "view" || o.kind === "matview"
            ? [
                {
                  label: "◇ Crear diagrama de esta vista",
                  onClick: () => onCreateViewDiagram(`${o.schema_name}.${o.name}`),
                },
              ]
            : []
        }
        onError={onError}
      />
      )}

      <div className="d-flex flex-column flex-grow-1" style={{ minWidth: 0 }}>
      {/* Menú del diagrama, alineado a la derecha sobre el lienzo */}
      <div className="d-flex align-items-center gap-2 px-3 py-2 bg-body border-bottom flex-wrap justify-content-end">
        <input
          className="form-control form-control-sm fw-semibold"
          value={diagramName}
          onChange={(e) => setDiagramName(e.target.value)}
          style={{ width: 180 }}
          title="Nombre del diagrama"
        />
        {!offline && (
          <>
            <button
              className="btn btn-sm btn-primary"
              onClick={() => void save()}
              disabled={empty}
              title="Guardar en la biblioteca local de la app"
            >
              💾
            </button>
            <select
              className="form-select form-select-sm"
              style={{ width: "auto" }}
              value=""
              onChange={(e) => void open(e.target.value)}
              title={dir ? `Carpeta de diagramas: ${dir}` : "Abrir de la biblioteca"}
            >
              <option value="">Biblioteca…</option>
              {saved.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.engine === "sqlserver" ? "🗄️ " : d.engine === "postgresql" ? "🐘 " : ""}
                  {d.name} · {d.dbname} ({d.node_count} tablas)
                  {d.engine === "sqlserver" ? " · SQL Server" : d.engine === "postgresql" ? " · PostgreSQL" : ""}
                </option>
              ))}
            </select>
            <button
              className="btn btn-sm btn-outline-secondary"
              onClick={() => void chooseDir()}
              title={
                dir
                  ? `Cambiar carpeta de diagramas (actual: ${dir})`
                  : "Elegir la carpeta donde se guardan y listan los diagramas"
              }
            >
              📁
            </button>
          </>
        )}
        <button
          className="btn btn-sm btn-outline-primary"
          onClick={() => void saveToFile()}
          disabled={empty}
          title="Guardar como archivo .pgdiag (eliges la ubicación; se puede abrir sin conexión)"
        >
          💾 Archivo
        </button>
        <button
          className="btn btn-sm btn-outline-primary"
          onClick={() => void openFromDisk()}
          title="Abrir un archivo .pgdiag"
        >
          📂 Archivo
        </button>
        {!offline && (
          <button className="btn btn-sm btn-outline-secondary" onClick={newDiagram} title="Vaciar el lienzo">✚ Nuevo</button>
        )}
        <button className="btn btn-sm btn-outline-secondary" onClick={() => addNote()} title="Añadir una sticky note">🗒 Nota</button>
        <button className="btn btn-sm btn-outline-secondary" onClick={autoLayout} disabled={empty}>⬡ Auto-layout</button>
        <div className="btn-group btn-group-sm" role="group">
          <button className="btn btn-outline-secondary" onClick={() => void exportImage("png")} disabled={empty}>PNG</button>
          <button className="btn btn-outline-secondary" onClick={() => void exportImage("svg")} disabled={empty}>SVG</button>
          {!offline && (
            <>
              <button className="btn btn-outline-secondary" onClick={() => void exportText("mermaid")} disabled={empty} title="Exportar erDiagram Mermaid">Mermaid</button>
              <button className="btn btn-outline-secondary" onClick={() => void exportText("dbml")} disabled={empty} title="Exportar DBML (dbdiagram.io)">DBML</button>
            </>
          )}
        </div>
        {status && <small className="text-body-secondary">{status}</small>}
      </div>

        <div
          ref={wrapper}
          className="position-relative"
          style={{ flex: 1 }}
          onDrop={onDrop}
          onDragEnter={(e) => e.preventDefault()}
          onDragOver={(e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = "move";
          }}
        >
          {!empty && (
            <div
              className="position-absolute"
              style={{ zIndex: 6, top: 10, left: 10, width: 260 }}
            >
              <div className="input-group input-group-sm shadow-sm">
                <span className="input-group-text bg-body">🔎</span>
                <input
                  className="form-control"
                  placeholder="Buscar tabla en el diagrama…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && searchMatches.length > 0) focusNode(searchMatches[0].id);
                    if (e.key === "Escape") setSearch("");
                  }}
                />
                {search && (
                  <button className="btn btn-outline-secondary" onClick={() => setSearch("")} title="Limpiar">
                    ✕
                  </button>
                )}
              </div>
              {search && (
                <div className="list-group shadow-sm mt-1" style={{ maxHeight: 240, overflowY: "auto" }}>
                  {searchMatches.length === 0 ? (
                    <div className="list-group-item py-1 small text-body-secondary">Sin coincidencias en el lienzo</div>
                  ) : (
                    searchMatches.slice(0, 12).map((m) => (
                      <button
                        key={m.id}
                        className="list-group-item list-group-item-action py-1 small text-truncate"
                        onClick={() => focusNode(m.id)}
                        title={m.label}
                      >
                        {m.label}
                      </button>
                    ))
                  )}
                </div>
              )}
            </div>
          )}
          {empty && !offline && (
            <div
              className="position-absolute top-50 start-50 translate-middle text-center text-body-secondary"
              style={{ zIndex: 5, pointerEvents: "none", maxWidth: 420 }}
            >
              <div style={{ fontSize: 42 }}>◇</div>
              <p className="fs-5 mb-2 fw-semibold">Lienzo vacío</p>
              <p className="small mb-1">🖱 Arrastra una tabla desde el panel izquierdo (o doble clic).</p>
              <p className="small mb-1">⇲ En cada nodo trae sus tablas relacionadas.</p>
              <p className="small mb-1">🖱 Clic en una columna: sigue su relación hasta la tabla destino.</p>
              <p className="small mb-1">🖱 Clic derecho en una vista: «Crear diagrama de esta vista».</p>
              <p className="small mb-0">📂 O abre un archivo .pgdiag con el botón «Archivo».</p>
            </div>
          )}
          {/* Trazado de la columna seleccionada: a dónde lleva la relación */}
          {selectedCol && (
            <div
              className="position-absolute shadow"
              style={{
                zIndex: 7,
                left: "50%",
                transform: "translateX(-50%)",
                bottom: 14,
                maxWidth: 520,
                background: "var(--bs-body-bg)",
                border: "1px solid var(--bs-border-color)",
                borderRadius: 8,
                overflow: "hidden",
              }}
            >
              <div className="d-flex align-items-center gap-2 px-3 py-1 border-bottom">
                <span className="badge text-bg-primary">🔗 {selectedCol.node}.{selectedCol.col}</span>
                <span className="flex-grow-1" />
                <button className="btn-close btn-sm" title="Cerrar" onClick={clearSelection} />
              </div>
              {colTrace.length === 0 ? (
                <div className="px-3 py-2 small text-body-secondary">
                  Esta columna no participa en ninguna relación con las tablas del lienzo.
                </div>
              ) : (
                <div style={{ maxHeight: 168, overflowY: "auto" }}>
                  {colTrace.map((t) => (
                    <button
                      key={t.edgeId}
                      className="btn btn-sm w-100 text-start d-flex align-items-center gap-2 px-3 py-1 border-0 rounded-0"
                      onClick={() => focusNode(t.other)}
                      title="Centrar la vista en esta tabla"
                    >
                      <span style={{ opacity: 0.7 }}>{t.outgoing ? "→" : "←"}</span>
                      <span className="fw-semibold text-truncate">{t.other}</span>
                      {t.otherCols && <span className="text-body-secondary small">.{t.otherCols}</span>}
                      <span className="badge text-bg-secondary ms-auto">{t.label}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          <AnchorUsageContext.Provider value={routing.usage}>
            <ReactFlow
              nodes={nodes}
              edges={allEdges}
              onNodesChange={onNodesChange}
              onEdgeClick={(_, edge) => {
                setSelectedCol(null);
                setSelectedRel((cur) => (cur === edge.id ? null : edge.id));
              }}
              onPaneClick={clearSelection}
              nodeTypes={nodeTypes}
              edgeTypes={edgeTypes}
              fitView
              proOptions={{ hideAttribution: true }}
              minZoom={0.1}
            >
              <Background gap={18} />
              <Controls />
              <MiniMap pannable zoomable />
            </ReactFlow>
          </AnchorUsageContext.Provider>
        </div>
      </div>
    </div>
  );
}

// --- Workspace con pestañas ---

interface Props {
  profileId: string;
  dbname: string;
  onBack: () => void;
}

interface Tab {
  id: number;
  title: string;
  initialViewKey?: string;
}

/** Visor de un .pgdiag sin conexión a la base de datos. */
export function OfflineDiagramView({ doc, onBack }: { doc: PgDiagFile; onBack: () => void }) {
  const [error, setError] = useState("");
  return (
    <div className="d-flex flex-column vh-100 bg-body-tertiary">
      <style>{FLOW_CSS}</style>
      <header
        className="d-flex align-items-center gap-2 px-3 py-2 bg-body border-bottom"
        style={{ borderBottom: "3px solid var(--pg-accent2)" }}
      >
        <button className="btn btn-sm btn-outline-secondary" onClick={onBack} title="Volver al inicio">
          ← Inicio
        </button>
        <span className="fw-semibold">📄 {doc.name}</span>
        <span className="badge text-bg-warning">Sin conexión</span>
        <small className="text-body-secondary">BD de origen: {doc.dbname}</small>
        <span className="flex-grow-1" />
        <ThemeMenu />
      </header>
      {error && (
        <div className="alert alert-danger rounded-0 py-2 px-3 mb-0 d-flex">
          <span className="flex-grow-1">{error}</span>
          <button className="btn-close" onClick={() => setError("")} />
        </div>
      )}
      <ReactFlowProvider>
        <DiagramCanvas
          profileId=""
          dbname={doc.dbname}
          schemas={[]}
          offline
          initialDoc={doc}
          onCreateViewDiagram={() => {}}
          onTitleChange={() => {}}
          onError={setError}
        />
      </ReactFlowProvider>
    </div>
  );
}

export default function DiagramView({ profileId, dbname, onBack }: Props) {
  const [tabs, setTabs] = useState<Tab[]>([{ id: 1, title: "Diagrama 1" }]);
  const [active, setActive] = useState(1);
  const [schemas, setSchemas] = useState<SchemaInfo[]>([]);
  const [error, setError] = useState("");
  const nextId = useRef(2);

  useEffect(() => {
    api
      .introspect(profileId, dbname)
      .then((s) => setSchemas(s.schemas))
      .catch((e) => setError(errText(e)));
  }, [profileId, dbname]);

  function addTab() {
    const id = nextId.current++;
    setTabs((ts) => [...ts, { id, title: `Diagrama ${id}` }]);
    setActive(id);
  }

  function addTabForView(viewKey: string) {
    const id = nextId.current++;
    setTabs((ts) => [...ts, { id, title: `Vista ${viewKey.split(".").pop()}`, initialViewKey: viewKey }]);
    setActive(id);
  }

  function closeTab(id: number) {
    setTabs((ts) => {
      const rest = ts.filter((t) => t.id !== id);
      if (rest.length === 0) return ts; // siempre queda al menos una pestaña
      if (active === id) setActive(rest[rest.length - 1].id);
      return rest;
    });
  }

  function renameTab(id: number, title: string) {
    setTabs((ts) => ts.map((t) => (t.id === id ? { ...t, title } : t)));
  }

  return (
    <div className="d-flex flex-column vh-100 bg-body-tertiary">
      <style>{FLOW_CSS}</style>
      <header className="d-flex align-items-end bg-body border-bottom pt-2">
        {/* Columna izquierda con el mismo ancho que el árbol: las pestañas
            comienzan alineadas con el lienzo */}
        <div className="px-3 pb-2 flex-shrink-0 border-end d-flex align-items-center" style={{ width: 300 }}>
          <ModeSwitch mode="diagram" onChange={() => onBack()} />
        </div>
        <ul
          className="nav nav-tabs border-bottom-0 flex-nowrap flex-grow-1 ps-3"
          style={{ overflowX: "auto", overflowY: "hidden" }}
        >
          {tabs.map((t) => {
            const isActive = active === t.id;
            return (
              <li key={t.id} className="nav-item">
                <button
                  className="nav-link py-2 px-3 d-flex align-items-center gap-2"
                  onClick={() => setActive(t.id)}
                  style={{
                    whiteSpace: "nowrap",
                    background: isActive ? "var(--pg-accent2)" : "transparent",
                    color: isActive ? "#fff" : "var(--bs-secondary-color)",
                    fontWeight: isActive ? 700 : 400,
                    border: isActive ? "1px solid var(--pg-accent2)" : "1px solid transparent",
                    borderBottom: "none",
                    borderRadius: "8px 8px 0 0",
                    boxShadow: isActive ? "0 -2px 6px color-mix(in srgb, var(--pg-accent2) 30%, transparent)" : undefined,
                  }}
                >
                  ◇ {t.title}
                  {tabs.length > 1 && (
                    <span
                      className={`btn-close ${isActive ? "btn-close-white" : ""}`}
                      style={{ fontSize: 9 }}
                      title="Cerrar pestaña"
                      onClick={(e) => {
                        e.stopPropagation();
                        closeTab(t.id);
                      }}
                    />
                  )}
                </button>
              </li>
            );
          })}
          <li className="nav-item align-self-center">
            <button
              className="btn btn-sm btn-outline-secondary border-0 ms-1"
              onClick={addTab}
              title="Nueva pestaña de diagrama"
            >
              ＋
            </button>
          </li>
        </ul>
        <div className="pb-2 pe-3">
          <ThemeMenu />
        </div>
      </header>

      {error && (
        <div className="alert alert-danger rounded-0 py-2 px-3 mb-0 d-flex">
          <span className="flex-grow-1">{error}</span>
          <button className="btn-close" onClick={() => setError("")} />
        </div>
      )}

      {tabs.map((t) => (
        <div
          key={t.id}
          style={{
            display: active === t.id ? "flex" : "none",
            flexDirection: "column",
            flex: 1,
            minHeight: 0,
          }}
        >
          <ReactFlowProvider>
            <DiagramCanvas
              profileId={profileId}
              dbname={dbname}
              schemas={schemas}
              initialViewKey={t.initialViewKey}
              onCreateViewDiagram={addTabForView}
              onTitleChange={(title) => renameTab(t.id, title)}
              onError={setError}
            />
          </ReactFlowProvider>
        </div>
      ))}
    </div>
  );
}
