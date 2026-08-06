/**
 * Convierte los anclajes calculados por `edgeRouting` en coordenadas de lienzo,
 * leyendo la caja del nodo directamente del store de React Flow.
 *
 * Es el puente que permite que el anclaje sea continuo. React Flow, por defecto,
 * toma los extremos de una arista de la posición del `<Handle>` medida en el DOM,
 * y solo la vuelve a medir si se llama a `updateNodeInternals` — inviable en cada
 * fotograma de un arrastre, y con un fotograma de retraso. Al calcular el punto
 * aquí, a partir de `position` + `measured`, la arista y el nodo se mueven
 * exactamente a la vez y el extremo puede estar en cualquier fracción del lado.
 */
import { useInternalNode } from "@xyflow/react";
import { SIDE_POSITION, type Anchor, type Side } from "./anchors";

/** Lo que `DiagramView` mete en `edge.data` para que la arista sepa dónde nace. */
export interface AnchorData {
  sourceAnchor?: Anchor;
  targetAnchor?: Anchor;
}

interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

type MaybeNode = ReturnType<typeof useInternalNode>;

function boxOf(node: MaybeNode): Box | null {
  if (!node) return null;
  const p = node.internals.positionAbsolute;
  return {
    x: p.x,
    y: p.y,
    w: node.measured.width ?? 230,
    h: node.measured.height ?? 150,
  };
}

/** Punto absoluto de un anclaje sobre el contorno de la caja. */
function pointOf(b: Box, a: Anchor): [number, number] {
  const f = Math.min(1, Math.max(0, a.frac));
  const x = a.side === "left" ? b.x : a.side === "right" ? b.x + b.w : b.x + b.w * f;
  const y = a.side === "top" ? b.y : a.side === "bottom" ? b.y + b.h : b.y + b.h * f;
  return [x, y];
}

export interface EdgeEnds {
  sourceX: number;
  sourceY: number;
  targetX: number;
  targetY: number;
  sourcePosition: (typeof SIDE_POSITION)[Side];
  targetPosition: (typeof SIDE_POSITION)[Side];
}

/**
 * Extremos de la arista. Si por lo que sea no hay anclaje o el nodo aún no está
 * medido, se cae a lo que React Flow haya calculado (`fallback`), que es lo que
 * se hacía antes: peor colocado, pero nunca roto.
 */
export function useEdgeEnds(
  sourceId: string,
  targetId: string,
  data: AnchorData | undefined,
  fallback: EdgeEnds
): EdgeEnds {
  const sourceBox = boxOf(useInternalNode(sourceId));
  const targetBox = boxOf(useInternalNode(targetId));
  const sa = data?.sourceAnchor;
  const ta = data?.targetAnchor;
  if (!sourceBox || !targetBox || !sa || !ta) return fallback;

  const [sourceX, sourceY] = pointOf(sourceBox, sa);
  const [targetX, targetY] = pointOf(targetBox, ta);
  return {
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition: SIDE_POSITION[sa.side],
    targetPosition: SIDE_POSITION[ta.side],
  };
}
