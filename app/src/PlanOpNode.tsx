/**
 * Nodo del diagrama de plan de ejecución: un operador (Seq Scan, Hash Join,
 * Clustered Index Seek…) dibujado como un paso de un proceso.
 *
 * Todo el detalle del operador (condición de join, predicados, referencias
 * externas, buffers…) se muestra DENTRO de la caja: en un lienzo, un tooltip
 * flotante tapa justo los datos del nodo que se está mirando.
 *
 * El color del borde y la barra inferior indican el coste propio del operador
 * dentro del plan, para que el cuello de botella salte a la vista sin tener
 * que leer los números.
 */
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { PLAN_NODE_WIDTH } from "./planTree";

export interface PlanOpData extends Record<string, unknown> {
  op: string;
  /** Líneas de detalle ya deduplicadas (ver planTree.detailLines). */
  lines: string[];
  estimateRows: number | null;
  actualRows: number | null;
  /** Coste acumulado del subárbol, tal cual lo da el motor. */
  cost: number | null;
  /** Coste propio del operador (total menos el de sus hijos). */
  selfCost: number;
  /** Coste propio como fracción del operador más caro del plan (0–1). */
  weight: number;
  /** ¿Es el operador con más coste propio del plan? */
  hottest: boolean;
  /** ¿Tiene hijos? Los nodos hoja son los que leen datos de verdad. */
  isLeaf: boolean;
  mode: "estimated" | "actual";
}

export type PlanOpNodeType = Node<PlanOpData, "planop">;

export { PLAN_NODE_WIDTH };

/** Icono por familia de operador, para reconocerlos de un vistazo. */
export function opIcon(op: string): string {
  const o = op.toLowerCase();
  if (o.includes("seq scan") || o.includes("table scan")) return "▤";
  if (o.includes("index seek") || o.includes("index scan") || o.includes("index only")) return "⌖";
  if (o.includes("scan")) return "▤";
  if (o.includes("hash match") || o.includes("hash")) return "⌗";
  if (o.includes("nested loop")) return "↻";
  if (o.includes("merge")) return "⋈";
  if (o.includes("join")) return "⋈";
  if (o.includes("sort")) return "⇅";
  if (o.includes("aggregate") || o.includes("group")) return "∑";
  if (o.includes("filter") || o.includes("where")) return "⧩";
  if (o.includes("top") || o.includes("limit")) return "⇱";
  if (o.includes("compute") || o.includes("scalar")) return "ƒ";
  if (o.includes("parallel") || o.includes("gather")) return "⑂";
  if (["select", "insert", "update", "delete", "with"].includes(o)) return "▶";
  return "◆";
}

/** Escala de color por peso: verde (barato) → ámbar → rojo (caro). */
export function weightColor(weight: number): string {
  if (weight >= 0.66) return "#e5484d";
  if (weight >= 0.33) return "#f5a524";
  return "#30a46c";
}

function fmt(v: number | null): string {
  if (v == null) return "—";
  if (Number.isInteger(v)) return v.toLocaleString();
  return v.toLocaleString(undefined, { maximumFractionDigits: 3 });
}

function Metric({ label, value, strong, color }: { label: string; value: string; strong?: boolean; color?: string }) {
  return (
    <div className="d-flex justify-content-between gap-2">
      <span className="text-body-secondary">{label}</span>
      <span className={`font-monospace${strong ? " fw-semibold" : ""}`} style={{ color }}>
        {value}
      </span>
    </div>
  );
}

export default function PlanOpNode({ data, selected }: NodeProps<PlanOpNodeType>) {
  const color = weightColor(data.weight);

  return (
    <div
      style={{
        width: PLAN_NODE_WIDTH,
        background: "var(--bs-body-bg)",
        color: "var(--bs-body-color)",
        border: `2px solid ${selected ? "#f59e0b" : color}`,
        borderRadius: 10,
        boxShadow: data.hottest
          ? `0 0 0 3px color-mix(in srgb, ${color} 30%, transparent)`
          : "0 1px 4px rgba(0,0,0,.18)",
        overflow: "hidden",
        fontSize: 12,
        fontVariantNumeric: "tabular-nums",
      }}
    >
      {/* Entrada: los datos llegan desde los operadores hijos (a la izquierda) */}
      {!data.isLeaf && (
        <Handle type="target" position={Position.Left} style={{ background: color, width: 8, height: 8 }} />
      )}

      <div
        className="d-flex align-items-center gap-2 px-2 py-1"
        style={{ background: `color-mix(in srgb, ${color} 16%, transparent)`, borderBottom: `1px solid ${color}` }}
      >
        <span style={{ fontSize: 15, color }}>{opIcon(data.op)}</span>
        <span className="fw-semibold text-truncate" title={data.op}>
          {data.op}
        </span>
        {data.hottest && (
          <span className="badge ms-auto" style={{ background: color, fontSize: 9 }} title="Operador con más coste propio del plan">
            más caro
          </span>
        )}
      </div>

      <div className="px-2 py-1 d-flex flex-column" style={{ gap: 2 }}>
        <Metric label="Filas est." value={fmt(data.estimateRows)} />
        {data.mode === "actual" && (
          <Metric label="Filas reales" value={fmt(data.actualRows)} strong />
        )}
        <Metric label="Coste propio" value={fmt(data.selfCost)} strong color={color} />
        <Metric label="Acumulado" value={fmt(data.cost)} />
      </div>

      {/* Detalle completo del operador, sin tooltip que tape el nodo */}
      {data.lines.length > 0 && (
        <div
          className="px-2 pb-1 font-monospace text-body-secondary"
          style={{
            fontSize: 10,
            lineHeight: "14px",
            borderTop: "1px dashed var(--bs-border-color)",
            paddingTop: 4,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {data.lines.map((l, i) => (
            <div key={i}>{l}</div>
          ))}
        </div>
      )}

      {/* Barra de peso propio dentro del plan */}
      <div
        style={{ height: 5, background: "var(--bs-tertiary-bg)" }}
        title={`${Math.round(data.weight * 100)} % del coste del operador más caro`}
      >
        <div style={{ height: "100%", width: `${Math.round(data.weight * 100)}%`, background: color }} />
      </div>

      {/* Salida: este operador alimenta a su padre (a la derecha) */}
      <Handle type="source" position={Position.Right} style={{ background: color, width: 8, height: 8 }} />
    </div>
  );
}
