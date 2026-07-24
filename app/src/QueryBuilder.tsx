/**
 * Constructor gráfico de consultas SQL (bidireccional) para el QueryTab.
 *
 * Flujo A (Diagrama -> SQL): el usuario arrastra tablas desde el explorador,
 * trae relacionadas (⇲), y crea JOINs uniendo columnas entre tablas. Cada
 * relación permite elegir tipo (INNER/LEFT/RIGHT/CROSS) y se sugiere uno según
 * la nulabilidad de las columnas. «Listo» traduce el lienzo a SQL (sidecar).
 *
 * Flujo B (SQL -> Diagrama): recibe un análisis (`initial`) de una sentencia y
 * reconstruye el lienzo editable; «Listo» regenera SQL consistente.
 *
 * Reutiliza ObjectTree, el cliente API y el patrón de lienzo de DiagramView.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useNodesState,
  useReactFlow,
  type Connection,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import dagre from "@dagrejs/dagre";
import {
  api,
  diagramsApi,
  type ApiError,
  type JoinType,
  type ParsedQuery,
  type QueryJoinSpec,
  type RelationshipInfo,
  type TableDetail,
} from "./api/client";
import QueryTableNode from "./QueryTableNode";
import { DND_MIME } from "./ObjectTree";
import { useSetBuilderSession } from "./builderBridge";

const nodeTypes = { querytable: QueryTableNode };

const JOIN_TYPES: JoinType[] = ["INNER JOIN", "LEFT JOIN", "RIGHT JOIN", "CROSS JOIN"];

/** Regla de sugerencia (espejo de domain/query_builder.suggest_join_type). */
function suggestJoinType(sourceNullable: boolean, targetNullable: boolean): JoinType {
  return sourceNullable || targetNullable ? "LEFT JOIN" : "INNER JOIN";
}

function errText(e: unknown): string {
  const err = e as ApiError;
  return `${err.code ?? "ERROR"}: ${err.message ?? String(e)}${err.hint ? ` — ${err.hint}` : ""}`;
}

interface QJoin extends QueryJoinSpec {
  id: string;
}

function joinId(source: string, target: string): string {
  return `qjoin:${[source, target].sort().join("|")}`;
}

interface Props {
  profileId: string;
  dbname: string;
  /** ¿Es la pestaña visible? Solo la activa toma control del árbol compartido. */
  active: boolean;
  initial?: ParsedQuery | null;
  onDone: (sql: string) => void;
  onCancel: () => void;
}

function Canvas({ profileId, dbname, active, initial, onDone, onCancel }: Props) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [joins, setJoins] = useState<QJoin[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const selectSql = useRef<string | null>(null);
  const tailSql = useRef<string | null>(null);
  const aliasesRef = useRef<Record<string, string>>({});
  const wrapper = useRef<HTMLDivElement>(null);
  const { screenToFlowPosition, fitView } = useReactFlow();

  const tableKeys = useMemo(() => nodes.map((n) => n.id), [nodes]);
  const tableKeysStr = tableKeys.join("|");
  const present = useMemo(() => new Set(tableKeys), [tableKeys]);
  // Pares (joinId) ya considerados para auto-join: evita re-crear un join que
  // el usuario borró a mano y no duplicar los que ya existen.
  const autoDone = useRef<Set<string>>(new Set());
  const relSeq = useRef(0);

  // Columnas por tabla que participan en algún join (para resaltar).
  const joinColsByTable = useMemo(() => {
    const m: Record<string, Set<string>> = {};
    for (const j of joins) {
      (m[j.source] ??= new Set());
      (m[j.target] ??= new Set());
      j.source_columns.forEach((c) => m[j.source].add(c));
      j.target_columns.forEach((c) => m[j.target].add(c));
    }
    return m;
  }, [joins]);

  const removeTable = useCallback(
    (key: string) => {
      setNodes((ns) => ns.filter((n) => n.id !== key));
      setJoins((js) => js.filter((j) => j.source !== key && j.target !== key));
      // Si la tabla vuelve a añadirse, que se re-detecten sus relaciones.
      for (const id of [...autoDone.current]) if (id.includes(key)) autoDone.current.delete(id);
    },
    [setNodes]
  );

  const buildNode = useCallback(
    (key: string, table: TableDetail, pos: { x: number; y: number }): Node =>
      ({
        id: key,
        type: "querytable",
        position: pos,
        data: {
          table,
          onRemove: removeTable,
          onAddRelated: (k: string, d: "in" | "out" | "both") => void addRelatedRef.current(k, d),
        },
      }) as Node,
    [removeTable]
  );

  const addTable = useCallback(
    async (key: string, pos: { x: number; y: number }) => {
      const [schema, ...rest] = key.split(".");
      try {
        const r = await api.tableDetail(profileId, dbname, schema, rest.join("."));
        setNodes((ns) => (ns.some((n) => n.id === key) ? ns : [...ns, buildNode(key, r.table, pos)]));
      } catch (e) {
        setError(errText(e));
      }
    },
    [profileId, dbname, setNodes, buildNode]
  );

  // Añadir por doble clic desde el árbol compartido (sin coordenadas del ratón):
  // posición ligeramente aleatoria para no apilar tablas.
  const addTableFromTree = useCallback(
    (key: string) => {
      void addTable(key, { x: 80 + Math.random() * 240, y: 60 + Math.random() * 240 });
    },
    [addTable]
  );

  // Registra esta sesión con el Explorador para que su único árbol pase a modo
  // arrastrar-al-lienzo. Al desmontar (Cancelar/Listo) o dejar de ser visible,
  // se limpia y el árbol vuelve a su modo de navegación normal.
  const setBuilderSession = useSetBuilderSession();
  useEffect(() => {
    if (!active) return;
    setBuilderSession({ addTable: addTableFromTree, presentKeys: present });
    return () => setBuilderSession(null);
  }, [active, addTableFromTree, present, setBuilderSession]);

  // Traer tablas relacionadas (mismo comportamiento que DiagramView).
  const addRelated = useCallback(
    async (key: string, direction: "in" | "out" | "both") => {
      const [schema, ...rest] = key.split(".");
      setStatus("Buscando tablas relacionadas…");
      try {
        const r = await diagramsApi.relatedTables(profileId, dbname, schema, rest.join("."), direction);
        const missing = r.related.filter((k) => !present.has(k));
        if (missing.length === 0) {
          setStatus(r.related.length === 0 ? "Sin tablas relacionadas" : "Ya están todas en el lienzo");
          setTimeout(() => setStatus(""), 1800);
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
        setTimeout(() => setStatus(""), 1800);
      } catch (e) {
        setError(errText(e));
        setStatus("");
      }
    },
    [profileId, dbname, present, nodes, addTable]
  );
  const addRelatedRef = useRef(addRelated);
  useEffect(() => {
    addRelatedRef.current = addRelated;
  }, [addRelated]);

  // Nulabilidad de una columna concreta (para sugerir el tipo de join).
  const columnNullable = useCallback(
    (tableKey: string, col: string): boolean => {
      const n = nodes.find((x) => x.id === tableKey);
      const table = (n?.data as { table?: TableDetail } | undefined)?.table;
      return table?.columns.find((c) => c.name === col)?.is_nullable ?? true;
    },
    [nodes]
  );

  // Espejo de `joins` para leer el estado actual sin recrear efectos.
  const joinsRef = useRef<QJoin[]>([]);
  useEffect(() => {
    joinsRef.current = joins;
  }, [joins]);

  // Auto-join: cuando el conjunto de tablas cambia, deriva las relaciones (FKs)
  // desde el sidecar y crea automáticamente los JOINs entre tablas presentes.
  // Solo añade pares nuevos: respeta lo que el usuario ya editó o borró a mano.
  useEffect(() => {
    if (tableKeys.length < 2) return;
    const seq = ++relSeq.current;
    diagramsApi
      .relationships(profileId, dbname, tableKeys)
      .then((r) => {
        if (seq !== relSeq.current) return;
        const rels = (r.relationships as RelationshipInfo[]).filter(
          (rel) => rel.source !== rel.target && present.has(rel.source) && present.has(rel.target)
        );
        const have = new Set(joinsRef.current.map((j) => j.id));
        const additions: QJoin[] = [];
        for (const rel of rels) {
          const id = joinId(rel.source, rel.target);
          if (autoDone.current.has(id)) continue;
          autoDone.current.add(id);
          if (have.has(id)) continue; // ya existe un join (manual) para ese par
          const srcNull = rel.columns.some((c) => columnNullable(rel.source, c));
          const tgtNull = rel.ref_columns.some((c) => columnNullable(rel.target, c));
          additions.push({
            id,
            source: rel.source,
            target: rel.target,
            join_type: suggestJoinType(srcNull, tgtNull),
            source_columns: rel.columns,
            target_columns: rel.ref_columns,
          });
        }
        if (additions.length === 0) return;
        setJoins((js) => [...js, ...additions]);
        setStatus(`${additions.length} relación(es) unida(s) automáticamente`);
        setTimeout(() => setStatus(""), 1800);
      })
      .catch((e) => setError(errText(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tableKeysStr, profileId, dbname]);

  // Crear/ampliar un join al unir dos columnas.
  const onConnect = useCallback(
    (conn: Connection) => {
      const src = conn.source;
      const tgt = conn.target;
      if (!src || !tgt || src === tgt) return;
      const sCol = conn.sourceHandle?.startsWith("cs-") ? conn.sourceHandle.slice(3) : null;
      const tCol = conn.targetHandle?.startsWith("ct-") ? conn.targetHandle.slice(3) : null;
      if (!sCol || !tCol) return;
      const id = joinId(src, tgt);
      setJoins((js) => {
        const existing = js.find((j) => j.id === id);
        if (!existing) {
          const jt = suggestJoinType(columnNullable(src, sCol), columnNullable(tgt, tCol));
          return [
            ...js,
            {
              id,
              source: src,
              target: tgt,
              join_type: jt,
              source_columns: [sCol],
              target_columns: [tCol],
            },
          ];
        }
        // Une a la relación existente respetando la orientación guardada.
        const flip = existing.source !== src;
        const scol = flip ? tCol : sCol;
        const tcol = flip ? sCol : tCol;
        if (existing.source_columns.includes(scol) && existing.target_columns.includes(tcol)) {
          return js;
        }
        return js.map((j) =>
          j.id === id
            ? {
                ...j,
                source_columns: [...j.source_columns, scol],
                target_columns: [...j.target_columns, tcol],
              }
            : j
        );
      });
      setSelected(id);
    },
    [columnNullable]
  );

  const setJoinType = useCallback((id: string, jt: JoinType) => {
    setJoins((js) => js.map((j) => (j.id === id ? { ...j, join_type: jt } : j)));
  }, []);

  const swapJoin = useCallback((id: string) => {
    setJoins((js) =>
      js.map((j) =>
        j.id === id
          ? {
              ...j,
              source: j.target,
              target: j.source,
              source_columns: j.target_columns,
              target_columns: j.source_columns,
            }
          : j
      )
    );
  }, []);

  const removeJoin = useCallback((id: string) => {
    setJoins((js) => js.filter((j) => j.id !== id));
    setSelected((s) => (s === id ? null : s));
  }, []);

  // Resaltado de columnas del join en cada nodo.
  useEffect(() => {
    setNodes((ns) =>
      ns.map((n) => {
        const cols = joinColsByTable[n.id];
        const next = cols ? [...cols] : [];
        const prev = (n.data as { joinCols?: string[] }).joinCols ?? [];
        if (prev.length === 0 && next.length === 0) return n;
        return { ...n, data: { ...n.data, joinCols: next } };
      })
    );
  }, [joinColsByTable, setNodes]);

  // Aristas de join, ancladas a las columnas unidas.
  const edges: Edge[] = useMemo(
    () =>
      joins.map((j) => {
        const isSel = j.id === selected;
        const isCross = j.join_type === "CROSS JOIN";
        const sCol = j.source_columns[0];
        const tCol = j.target_columns[0];
        return {
          id: j.id,
          source: j.source,
          target: j.target,
          sourceHandle: sCol ? `cs-${sCol}` : undefined,
          targetHandle: tCol ? `ct-${tCol}` : undefined,
          type: "smoothstep",
          className: isSel ? "pgdiag-flow" : undefined,
          label: isCross ? "CROSS JOIN" : j.join_type,
          labelStyle: { fontSize: 11, fontWeight: 700, fill: isSel ? "#b45309" : "#6d28d9" },
          labelBgStyle: { fill: "#fff", fillOpacity: 0.9 },
          style: {
            stroke: isSel ? "#f59e0b" : "#8a63d2",
            strokeWidth: 2,
            strokeDasharray: isCross ? "6 4" : undefined,
          },
          markerEnd: { type: MarkerType.ArrowClosed, color: isSel ? "#f59e0b" : "#8a63d2" },
        };
      }),
    [joins, selected]
  );

  const autoLayout = useCallback(
    (fit = true) => {
      const g = new dagre.graphlib.Graph();
      g.setGraph({ rankdir: "LR", nodesep: 60, ranksep: 140 });
      g.setDefaultEdgeLabel(() => ({}));
      for (const n of nodes) {
        const table = (n.data as { table: TableDetail }).table;
        g.setNode(n.id, { width: 230, height: 58 + Math.min(table.columns.length, 40) * 19 });
      }
      for (const e of edges) if (g.hasNode(e.source) && g.hasNode(e.target)) g.setEdge(e.source, e.target);
      dagre.layout(g);
      setNodes((ns) =>
        ns.map((n) => {
          const p = g.node(n.id);
          return p ? { ...n, position: { x: p.x - 115, y: p.y - p.height / 2 } } : n;
        })
      );
      if (fit) setTimeout(() => void fitView({ padding: 0.15 }), 60);
    },
    [nodes, edges, setNodes, fitView]
  );
  const autoLayoutRef = useRef(autoLayout);
  useEffect(() => {
    autoLayoutRef.current = autoLayout;
  }, [autoLayout]);

  // Inicialización desde un análisis SQL (Flujo B).
  const didInit = useRef(false);
  useEffect(() => {
    if (didInit.current || !initial) return;
    didInit.current = true;
    void (async () => {
      setBusy(true);
      setStatus("Reconstruyendo diagrama…");
      selectSql.current = initial.select_sql;
      tailSql.current = initial.tail_sql;
      aliasesRef.current = { ...initial.aliases };
      try {
        const details = await Promise.all(
          initial.tables.map(async (k) => {
            const [schema, ...rest] = k.split(".");
            const r = await api.tableDetail(profileId, dbname, schema, rest.join("."));
            return { k, table: r.table };
          })
        );
        setNodes(
          details.map(({ k, table }, i) =>
            buildNode(k, table, { x: (i % 3) * 320 + 40, y: Math.floor(i / 3) * 320 + 40 })
          )
        );
        setJoins(
          initial.joins.map((j) => {
            const id = joinId(j.source, j.target);
            autoDone.current.add(id); // el join ya viene del SQL: no re-detectar
            return { ...j, id };
          })
        );
        if (initial.warnings.length > 0) setError(initial.warnings.join(" "));
        setTimeout(() => autoLayoutRef.current(true), 220);
      } catch (e) {
        setError(errText(e));
      } finally {
        setBusy(false);
        setStatus("");
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initial]);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const key = e.dataTransfer.getData(DND_MIME);
      if (!key) return;
      const zoomStr = getComputedStyle(document.documentElement).zoom;
      const cssZoom = zoomStr && zoomStr !== "normal" ? parseFloat(zoomStr) || 1 : 1;
      const pos = screenToFlowPosition({ x: e.clientX / cssZoom, y: e.clientY / cssZoom });
      void addTable(key, pos);
    },
    [addTable, screenToFlowPosition]
  );

  async function done() {
    if (nodes.length === 0 || busy) return;
    setBusy(true);
    setStatus("Generando SQL…");
    setError("");
    try {
      // Conserva los alias analizados de las tablas aún presentes (fidelidad
      // en el ida y vuelta SQL -> diagrama -> SQL); el resto los genera el sidecar.
      const aliases: Record<string, string> = {};
      for (const k of tableKeys) if (aliasesRef.current[k]) aliases[k] = aliasesRef.current[k];
      const r = await api.buildQuery(profileId, dbname, {
        tables: tableKeys,
        aliases,
        joins: joins.map(({ source, target, join_type, source_columns, target_columns }) => ({
          source,
          target,
          join_type,
          source_columns,
          target_columns,
        })),
        select_sql: selectSql.current,
        tail_sql: tailSql.current,
      });
      onDone(r.sql);
    } catch (e) {
      setError(errText(e));
      setStatus("");
    } finally {
      setBusy(false);
    }
  }

  const sel = joins.find((j) => j.id === selected) ?? null;
  const empty = nodes.length === 0;

  return (
    <div className="d-flex flex-grow-1" style={{ minHeight: 0 }}>
      {/* Sin árbol propio: el builder usa el mismo explorador del Explorer
          (ver builderBridge). Aquí solo va el lienzo. */}
      <div className="d-flex flex-column flex-grow-1" style={{ minWidth: 0 }}>
        <div className="d-flex align-items-center gap-2 px-3 py-2 bg-body border-bottom flex-wrap">
          <span className="fw-semibold">◇ Constructor de consulta</span>
          <button className="btn btn-sm btn-outline-secondary" onClick={() => autoLayout(true)} disabled={empty}>
            ⬡ Auto-layout
          </button>
          {status && <small className="text-body-secondary">{status}</small>}
          <span className="flex-grow-1" />
          <button className="btn btn-sm btn-outline-secondary" onClick={onCancel}>
            Cancelar
          </button>
          <button className="btn btn-sm btn-success" onClick={() => void done()} disabled={empty || busy}>
            {busy ? (
              <>
                <span className="spinner-border spinner-border-sm me-1" /> …
              </>
            ) : (
              "✓ Listo"
            )}
          </button>
        </div>

        {error && (
          <div className="alert alert-warning rounded-0 py-2 px-3 mb-0 d-flex">
            <span className="flex-grow-1 small">{error}</span>
            <button className="btn-close" onClick={() => setError("")} />
          </div>
        )}

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
          {empty && (
            <div
              className="position-absolute top-50 start-50 translate-middle text-center text-body-secondary"
              style={{ zIndex: 5, pointerEvents: "none", maxWidth: 440 }}
            >
              <div style={{ fontSize: 42 }}>◇</div>
              <p className="fs-5 mb-2 fw-semibold">Construye tu consulta</p>
              <p className="small mb-1">🖱 Arrastra tablas desde el panel izquierdo.</p>
              <p className="small mb-1">🔗 Une una columna con otra para crear un JOIN.</p>
              <p className="small mb-0">✓ Pulsa «Listo» para generar el SQL.</p>
            </div>
          )}

          {/* Editor del join seleccionado */}
          {sel && (
            <div
              className="position-absolute bg-body border rounded shadow-sm p-2"
              style={{ zIndex: 6, top: 12, left: "50%", transform: "translateX(-50%)", minWidth: 340 }}
            >
              <div className="small mb-2">
                <span className="text-body-secondary">Relación:</span>{" "}
                <strong>{sel.source.split(".").pop()}</strong>
                {" ⇄ "}
                <strong>{sel.target.split(".").pop()}</strong>
                {sel.join_type !== "CROSS JOIN" && sel.source_columns.length > 0 && (
                  <span className="text-body-secondary">
                    {" "}
                    · {sel.source_columns.map((c, i) => `${c}=${sel.target_columns[i] ?? "?"}`).join(", ")}
                  </span>
                )}
              </div>
              <div className="d-flex align-items-center gap-1 flex-wrap">
                <div className="btn-group btn-group-sm" role="group">
                  {JOIN_TYPES.map((jt) => (
                    <button
                      key={jt}
                      className={`btn ${sel.join_type === jt ? "btn-primary" : "btn-outline-primary"}`}
                      onClick={() => setJoinType(sel.id, jt)}
                      title={jt === "CROSS JOIN" ? "Producto cartesiano (sin ON)" : jt}
                    >
                      {jt.replace(" JOIN", "")}
                    </button>
                  ))}
                </div>
                <button
                  className="btn btn-sm btn-outline-secondary"
                  onClick={() => swapJoin(sel.id)}
                  title="Invertir origen/destino"
                >
                  ⇄
                </button>
                <button
                  className="btn btn-sm btn-outline-danger"
                  onClick={() => removeJoin(sel.id)}
                  title="Eliminar la relación"
                >
                  🗑
                </button>
              </div>
            </div>
          )}

          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onConnect={onConnect}
            isValidConnection={(c) => c.source !== c.target}
            onEdgeClick={(_, edge) => setSelected((cur) => (cur === edge.id ? null : edge.id))}
            onPaneClick={() => setSelected(null)}
            nodeTypes={nodeTypes}
            fitView
            proOptions={{ hideAttribution: true }}
            minZoom={0.1}
          >
            <Background gap={18} />
            <Controls />
            <MiniMap pannable zoomable />
          </ReactFlow>
        </div>
      </div>
    </div>
  );
}

const FLOW_CSS = `
.react-flow__edge.pgdiag-flow .react-flow__edge-path {
  stroke: #f59e0b !important;
  stroke-width: 3.5 !important;
  stroke-dasharray: 10 7;
  animation: pgdiagFlow .45s linear infinite;
  filter: drop-shadow(0 0 4px rgba(245, 158, 11, .85));
}
@keyframes pgdiagFlow { to { stroke-dashoffset: -17; } }
`;

export default function QueryBuilder(props: Props) {
  return (
    <div className="d-flex flex-column w-100 h-100" style={{ minHeight: 0 }}>
      <style>{FLOW_CSS}</style>
      <ReactFlowProvider>
        <Canvas {...props} />
      </ReactFlowProvider>
    </div>
  );
}
