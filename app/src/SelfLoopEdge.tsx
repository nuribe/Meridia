/**
 * Arista para relaciones reflexivas (una tabla que se referencia a sí misma).
 * Los edges normales (smoothstep) colapsan cuando source y target son el mismo
 * nodo; aquí dibujamos un lazo visible en la esquina superior derecha, con la
 * flecha entrando por el borde superior.
 */
import { BaseEdge, EdgeLabelRenderer, type EdgeProps } from "@xyflow/react";
import type { CSSProperties } from "react";

export default function SelfLoopEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  markerEnd,
  style,
  label,
  labelStyle,
}: EdgeProps) {
  // Lazo: sale por la derecha (s-right) y vuelve por arriba (t-top).
  const path = `M ${sourceX} ${sourceY} C ${sourceX + 72} ${sourceY}, ${targetX} ${targetY - 72}, ${targetX} ${targetY}`;
  const labelX = Math.max(sourceX, targetX) + 40;
  const labelY = Math.min(sourceY, targetY) - 34;
  const color = (labelStyle as CSSProperties | undefined)?.color ?? "#12305c";

  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} />
      {label != null && (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan"
            style={{
              position: "absolute",
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              fontSize: 11,
              fontWeight: 600,
              color,
              background: "rgba(255,255,255,0.85)",
              padding: "0 4px",
              borderRadius: 3,
              pointerEvents: "all",
            }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}
