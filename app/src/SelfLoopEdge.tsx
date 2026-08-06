/**
 * Arista para relaciones reflexivas (una tabla que se referencia a sí misma).
 *
 * Los edges normales (smoothstep) colapsan cuando source y target son el mismo
 * nodo. Antes se dibujaba una curva Bézier que quedaba pegada al borde derecho
 * y casi invisible, con la etiqueta suelta a varios cientos de píxeles del lazo.
 *
 * Ahora se traza un lazo ORTOGONAL con esquinas redondeadas que rodea la
 * esquina superior derecha del nodo, con separación real respecto a él:
 *
 *     ┌────────────┐ ← carril horizontal (por encima del nodo)
 *     ↓            │
 *   ╔═════════╗    │
 *   ║  tabla  ╟────┘ ← carril vertical (a la derecha del nodo)
 *   ╚═════════╝
 *
 * La etiqueta (↻ N:1) va sobre el carril vertical, fuera del nodo, para que se
 * lea sin ambigüedad. Varias reflexivas en la misma tabla se separan con
 * `data.ring`, dibujándose como lazos concéntricos.
 */
import { BaseEdge, EdgeLabelRenderer, type EdgeProps } from "@xyflow/react";
import type { CSSProperties } from "react";
import { useEdgeEnds, type AnchorData } from "./edgeAnchors";

/** Separación base del lazo respecto al nodo, y salto entre lazos concéntricos. */
const CLEAR_X = 48;
const CLEAR_Y = 36;
const RING_STEP = 18;
const CORNER = 11;

export default function SelfLoopEdge({
  id,
  source,
  target,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerEnd,
  style,
  label,
  labelStyle,
  data,
}: EdgeProps) {
  const ring = Number((data as { ring?: number } | undefined)?.ring ?? 0);
  const selected = Boolean((data as { selected?: boolean } | undefined)?.selected);
  // Mismo criterio que el resto de aristas: los extremos salen del anclaje
  // calculado, no del handle. Aquí además es lo que mantiene el lazo pegado a la
  // esquina superior derecha aunque la tabla cambie de alto al colapsarse.
  const ends = useEdgeEnds(source, target, data as AnchorData | undefined, {
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  });

  const { sourceX: sx, sourceY: sy, targetX: tx, targetY: ty } = ends;

  // Carriles del lazo: uno vertical a la derecha, otro horizontal por encima.
  const railX = sx + CLEAR_X + ring * RING_STEP;
  const railY = ty - CLEAR_Y - ring * RING_STEP;

  // Radio de esquina acotado, por si el nodo es muy pequeño o los carriles
  // quedan cerca: evita que las curvas se crucen sobre sí mismas.
  const r = Math.max(3, Math.min(CORNER, Math.abs(sy - railY) / 2, Math.abs(railX - tx) / 2));

  const path = [
    `M ${sx} ${sy}`,
    `L ${railX - r} ${sy}`,
    `Q ${railX} ${sy} ${railX} ${sy - r}`,
    `L ${railX} ${railY + r}`,
    `Q ${railX} ${railY} ${railX - r} ${railY}`,
    `L ${tx + r} ${railY}`,
    `Q ${tx} ${railY} ${tx} ${railY + r}`,
    `L ${tx} ${ty}`,
  ].join(" ");

  const color = (labelStyle as CSSProperties | undefined)?.color ?? "#6d28d9";
  // Etiqueta en mitad del carril vertical: siempre fuera del nodo.
  const labelX = railX;
  const labelY = (sy + railY) / 2;

  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} />
      {label != null && (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan"
            title="Relación reflexiva: la tabla se referencia a sí misma"
            style={{
              position: "absolute",
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              fontSize: 11,
              fontWeight: 700,
              whiteSpace: "nowrap",
              color,
              background: "rgba(255,255,255,.94)",
              border: `1px solid ${color}`,
              padding: "0 5px",
              borderRadius: 9,
              boxShadow: selected ? `0 0 0 2px ${color}55` : "0 1px 3px rgba(0,0,0,.15)",
              pointerEvents: "all",
            }}
          >
            ↻ {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}
