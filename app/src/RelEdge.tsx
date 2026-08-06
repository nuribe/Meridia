/**
 * Arista de relación (FK del catálogo o join de una vista).
 *
 * Es un `smoothstep` normal salvo en un punto: sus dos extremos no los pone
 * React Flow a partir del `<Handle>` más cercano, sino `useEdgeEnds`, que los
 * calcula desde la caja del nodo y la fracción de lado que le asignó
 * `edgeRouting`. Gracias a eso el punto de enlace puede caer en cualquier sitio
 * del contorno y se desliza al arrastrar, en vez de saltar entre posiciones
 * fijas.
 */
import { BaseEdge, EdgeText, getSmoothStepPath, type EdgeProps } from "@xyflow/react";
import { useEdgeEnds, type AnchorData } from "./edgeAnchors";

const CORNER = 8;

export default function RelEdge({
  id,
  source,
  target,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  style,
  markerEnd,
  label,
  labelStyle,
  labelBgStyle,
  interactionWidth,
}: EdgeProps) {
  const ends = useEdgeEnds(source, target, data as AnchorData | undefined, {
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  });

  const [path, labelX, labelY] = getSmoothStepPath({ ...ends, borderRadius: CORNER });

  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        style={style}
        markerEnd={markerEnd}
        interactionWidth={interactionWidth}
      />
      {label != null && (
        <EdgeText
          x={labelX}
          y={labelY}
          label={label}
          labelStyle={labelStyle}
          labelShowBg
          labelBgStyle={labelBgStyle}
          labelBgPadding={[2, 4]}
          labelBgBorderRadius={2}
        />
      )}
    </>
  );
}
