/**
 * Plan de ejecución dibujado como un diagrama de proceso (izquierda → derecha).
 *
 * El sidecar devuelve el plan como una lista PLANA de nodos con su
 * profundidad; aquí se reconstruye el árbol y se convierte en un grafo de
 * React Flow donde cada arista va del operador hijo al padre: los datos entran
 * por la izquierda (los scans que leen las tablas), pasan por los operadores
 * que los transforman y salen por la derecha con el resultado final.
 *
 * El grosor de cada flecha es proporcional (en escala logarítmica) al número
 * de filas que circulan por ella, igual que en los planes gráficos de SSMS:
 * una flecha gruesa entrando a un operador caro es el síntoma habitual de un
 * problema de rendimiento.
 */
import { useCallback, useEffect, useMemo, useRef } from "react";
import {
  Background,
  Controls,
  MarkerType,
  ReactFlow,
  ReactFlowProvider,
  getNodesBounds,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Edge,
} from "@xyflow/react";
import dagre from "@dagrejs/dagre";
import { toPng } from "html-to-image";
import "@xyflow/react/dist/style.css";
import type { ExplainMode, ExplainNode } from "./api/client";
import { saveFile } from "./files";
import PlanOpNode, { type PlanOpNodeType, weightColor } from "./PlanOpNode";
import {
  EXPORT_PADDING,
  PLAN_NODE_WIDTH,
  detailLines,
  edgeWidth,
  exportPixelRatio,
  formatRows,
  planNodeHeight,
  selfCosts,
  treeEdges,
} from "./planTree";

const nodeTypes = { planop: PlanOpNode };

/**
 * Exporta el diagrama a PNG y devuelve la ruta guardada (null si se canceló).
 * La expone el lienzo hacia arriba porque el botón vive en la barra del panel
 * del plan, fuera del ReactFlowProvider.
 */
export type PlanExporter = () => Promise<string | null | undefined>;

/** Filas que salen de un operador: reales si las hay, estimadas si no. */
function rowsOut(n: ExplainNode): number | null {
  return n.actual_rows ?? n.estimate_rows;
}

function Canvas({
  nodes: plan,
  mode,
  fileName,
  onExporterReady,
}: {
  nodes: ExplainNode[];
  mode: ExplainMode;
  fileName: string;
  /** Publica hacia arriba la función de export (vive dentro del proveedor). */
  onExporterReady: (fn: PlanExporter | null) => void;
}) {
  const { fitView, getNodes } = useReactFlow();
  const wrapper = useRef<HTMLDivElement>(null);

  // Solo los operadores forman el árbol; Planning/Execution Time son resúmenes.
  const ops = useMemo(() => plan.filter((n) => n.kind !== "summary"), [plan]);
  const links = useMemo(() => treeEdges(ops.map((n) => n.depth)), [ops]);
  // El color señala el coste PROPIO, no el acumulado (ver planTree.selfCosts).
  const own = useMemo(() => selfCosts(ops.map((n) => n.cost), links), [ops, links]);
  const maxOwn = useMemo(() => Math.max(0, ...own), [own]);
  const weightOf = useCallback(
    (i: number) => (maxOwn > 0 ? Math.min(1, own[i] / maxOwn) : 0),
    [own, maxOwn]
  );

  const { initialNodes, initialEdges } = useMemo(() => {
    const withChildren = new Set(links.map((l) => l.parent));
    // El alto depende del detalle de cada operador: se calcula una sola vez y
    // se usa tanto para el layout de dagre como para el render.
    const heights = ops.map((n) =>
      planNodeHeight(detailLines(n.op, n.text, n.detail), mode === "actual")
    );
    const rfNodes: PlanOpNodeType[] = ops.map((n, i) => ({
      id: String(i),
      type: "planop",
      position: { x: 0, y: 0 },
      data: {
        op: n.op,
        lines: detailLines(n.op, n.text, n.detail),
        estimateRows: n.estimate_rows,
        actualRows: n.actual_rows,
        cost: n.cost,
        selfCost: own[i],
        weight: weightOf(i),
        hottest: maxOwn > 0 && own[i] === maxOwn,
        isLeaf: !withChildren.has(i),
        mode,
      },
    }));

    const rfEdges: Edge[] = links.map(({ child, parent }) => {
      const rows = rowsOut(ops[child]);
      const width = edgeWidth(rows);
      const color = weightColor(weightOf(child));
      return {
        id: `${child}->${parent}`,
        source: String(child),
        target: String(parent),
        type: "smoothstep",
        label: formatRows(rows),
        labelStyle: { fontSize: 10, fill: "var(--bs-body-color)" },
        labelBgStyle: { fill: "var(--bs-body-bg)", fillOpacity: 0.85 },
        labelBgPadding: [3, 1] as [number, number],
        style: { strokeWidth: width, stroke: color },
        markerEnd: { type: MarkerType.ArrowClosed, color, width: 14, height: 14 },
      };
    });

    // Layout de izquierda a derecha: las hojas (sin entradas) quedan al
    // principio y el operador raíz —el resultado— al final.
    const g = new dagre.graphlib.Graph();
    g.setGraph({ rankdir: "LR", nodesep: 30, ranksep: 110, marginx: 20, marginy: 20 });
    g.setDefaultEdgeLabel(() => ({}));
    rfNodes.forEach((n, i) => g.setNode(n.id, { width: PLAN_NODE_WIDTH, height: heights[i] }));
    for (const e of rfEdges) g.setEdge(e.source, e.target);
    dagre.layout(g);
    rfNodes.forEach((n, i) => {
      const p = g.node(n.id);
      if (p) n.position = { x: p.x - PLAN_NODE_WIDTH / 2, y: p.y - heights[i] / 2 };
    });

    return { initialNodes: rfNodes, initialEdges: rfEdges };
  }, [ops, links, own, maxOwn, weightOf, mode]);

  const [nodes, setNodes, onNodesChange] = useNodesState<PlanOpNodeType>(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(initialEdges);

  // Un plan nuevo reemplaza el grafo entero y reencuadra.
  useEffect(() => {
    setNodes(initialNodes);
    setEdges(initialEdges);
    const t = setTimeout(() => void fitView({ padding: 0.15 }), 60);
    return () => clearTimeout(t);
  }, [initialNodes, initialEdges, setNodes, setEdges, fitView]);

  const onInit = useCallback(() => void fitView({ padding: 0.15 }), [fitView]);

  /**
   * Exporta el plan COMPLETO a PNG, no solo lo que se ve.
   *
   * Se captura `.react-flow__viewport` (la capa que contiene nodos y aristas)
   * forzando un lienzo del tamaño de los nodos y una transformación propia a
   * zoom 1: así la imagen sale a resolución nativa y no depende del encuadre
   * que tenga el usuario en pantalla.
   */
  const exportPng = useCallback(async () => {
    const viewport = wrapper.current?.querySelector(
      ".react-flow__viewport"
    ) as HTMLElement | null;
    const current = getNodes();
    if (!viewport || current.length === 0) return;

    const bounds = getNodesBounds(current);
    const width = Math.ceil(bounds.width) + EXPORT_PADDING * 2;
    const height = Math.ceil(bounds.height) + EXPORT_PADDING * 2;
    // Un plan largo puede dar una imagen enorme: se baja la densidad antes que
    // pasarse del límite de canvas del webview (devolvería un PNG en blanco).
    const pixelRatio = exportPixelRatio(Math.max(width, height));
    // Fondo real del tema activo, para que el PNG se vea como en pantalla.
    const backgroundColor = getComputedStyle(document.body).backgroundColor || "#ffffff";

    const dataUrl = await toPng(viewport, {
      backgroundColor,
      width,
      height,
      pixelRatio,
      style: {
        width: `${width}px`,
        height: `${height}px`,
        transform: `translate(${EXPORT_PADDING - bounds.x}px, ${EXPORT_PADDING - bounds.y}px) scale(1)`,
      },
    });
    return saveFile(fileName, dataUrl);
  }, [getNodes, fileName]);

  // Al desmontarse (cambio a la vista de tabla, plan nuevo…) se retira el
  // exportador para que nadie dispare un export sobre un lienzo que ya no existe.
  useEffect(() => {
    onExporterReady(exportPng);
    return () => onExporterReady(null);
  }, [exportPng, onExporterReady]);

  return (
    <div ref={wrapper} className="w-100 h-100">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        onInit={onInit}
        minZoom={0.15}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
        nodesConnectable={false}
        edgesFocusable={false}
      >
        <Background gap={18} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}

export default function PlanDiagram({
  nodes,
  mode,
  fileName = "plan-de-ejecucion",
  onExporterReady,
}: {
  nodes: ExplainNode[];
  mode: ExplainMode;
  /** Nombre base del archivo al exportar (sin extensión). */
  fileName?: string;
  /** Recibe la función de export; el botón vive en la barra del panel. */
  onExporterReady: (fn: PlanExporter | null) => void;
}) {
  const summary = nodes.filter((n) => n.kind === "summary");

  return (
    <div className="d-flex flex-column w-100 h-100" style={{ minHeight: 0 }}>
      <div className="flex-grow-1" style={{ minHeight: 0 }}>
        <ReactFlowProvider>
          <Canvas
            nodes={nodes}
            mode={mode}
            fileName={`${fileName}-${mode === "actual" ? "real" : "estimado"}.png`}
            onExporterReady={onExporterReady}
          />
        </ReactFlowProvider>
      </div>
      <div className="d-flex align-items-center gap-3 px-3 py-1 border-top bg-body flex-wrap" style={{ fontSize: 11 }}>
        <span className="text-body-secondary">
          Los datos fluyen de izquierda a derecha · el grosor de la flecha es el volumen de filas
        </span>
        <span className="d-flex align-items-center gap-1">
          <span style={{ width: 10, height: 10, borderRadius: 2, background: weightColor(0) }} /> barato
          <span style={{ width: 10, height: 10, borderRadius: 2, background: weightColor(0.5), marginLeft: 6 }} /> medio
          <span style={{ width: 10, height: 10, borderRadius: 2, background: weightColor(1), marginLeft: 6 }} /> caro
        </span>
        <span className="flex-grow-1" />
        {summary.map((s, i) => (
          <span key={i} className="text-body-secondary font-monospace">{s.text}</span>
        ))}
      </div>
    </div>
  );
}
