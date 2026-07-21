/**
 * Nodo del constructor gráfico de consultas: una tabla con sus columnas, donde
 * cada columna expone dos "handles" de conexión (izquierda = destino, derecha =
 * origen) para crear JOINs uniendo columnas entre tablas. Reutiliza el estilo
 * visual de TableNode (cabecera con color, PK/FK marcadas) pero simplificado a
 * lo que el constructor necesita: quitar la tabla y traer sus relacionadas.
 */
import { useState } from "react";
import { Handle, Position, type NodeProps, type Node } from "@xyflow/react";
import type { TableDetail } from "./api/client";
import { NODE_PALETTE } from "./TableNode";

const MAX_COLS = 40;

export type QueryTableNodeType = Node<
  {
    table: TableDetail;
    /** Columnas resaltadas por participar en algún join. */
    joinCols?: string[];
    onRemove: (key: string) => void;
    onAddRelated: (key: string, direction: "in" | "out" | "both") => void;
  },
  "querytable"
>;

const btn: React.CSSProperties = {
  cursor: "pointer",
  opacity: 0.85,
  padding: "0 3px",
  userSelect: "none",
  fontSize: 11,
};

const colHandle = (side: "left" | "right"): React.CSSProperties => ({
  width: 9,
  height: 9,
  background: "#8a63d2",
  border: "1.5px solid #fff",
  [side]: -5,
  borderRadius: 3,
});

export default function QueryTableNode({ data }: NodeProps<QueryTableNodeType>) {
  const { table: t, joinCols, onRemove, onAddRelated } = data;
  const key = `${t.schema_name}.${t.name}`;
  const color = NODE_PALETTE[0];
  const [relMenu, setRelMenu] = useState(false);
  const fkCols = new Set(t.foreign_keys.flatMap((fk) => fk.columns));
  const joined = new Set(joinCols ?? []);
  const visible = t.columns.slice(0, MAX_COLS);
  const truncated = t.columns.length - visible.length;

  return (
    <div
      style={{
        position: "relative",
        background: "#fff",
        border: `1.5px solid ${color}`,
        borderRadius: 8,
        minWidth: 210,
        fontSize: 12,
        fontFamily: "system-ui",
        boxShadow: "0 2px 6px rgba(0,0,0,.12)",
      }}
    >
      <div
        style={{
          background: color,
          color: "#fff",
          padding: "5px 8px",
          borderRadius: "6px 6px 0 0",
          display: "flex",
          alignItems: "center",
          gap: 4,
        }}
      >
        <span style={{ color: "rgba(255,255,255,.65)", fontSize: 11 }}>{t.schema_name}.</span>
        <strong style={{ flex: 1 }}>{t.name}</strong>
        <span
          onClick={(e) => {
            e.stopPropagation();
            setRelMenu(!relMenu);
          }}
          title="Añadir tablas relacionadas…"
          style={{ ...btn, background: relMenu ? "rgba(255,255,255,.25)" : undefined, borderRadius: 3 }}
        >
          ⇲
        </span>
        <span
          onClick={(e) => {
            e.stopPropagation();
            onRemove(key);
          }}
          title="Quitar del lienzo"
          style={btn}
        >
          ✕
        </span>
      </div>

      {relMenu && (
        <div
          className="nodrag"
          style={{
            position: "absolute",
            top: 26,
            right: 4,
            zIndex: 10,
            background: "#fff",
            border: "1px solid #ccc",
            borderRadius: 6,
            boxShadow: "0 4px 12px rgba(0,0,0,.2)",
            minWidth: 210,
            overflow: "hidden",
          }}
        >
          {(
            [
              ["in", "⬇ Que la referencian (debajo)"],
              ["out", "⬆ A las que referencia"],
              ["both", "⬌ Todas las relacionadas"],
            ] as const
          ).map(([dir, label]) => (
            <div
              key={dir}
              onClick={(e) => {
                e.stopPropagation();
                setRelMenu(false);
                onAddRelated(key, dir);
              }}
              onMouseEnter={(e) => ((e.target as HTMLElement).style.background = "#eef3fb")}
              onMouseLeave={(e) => ((e.target as HTMLElement).style.background = "")}
              style={{ padding: "6px 10px", cursor: "pointer", color: "#222" }}
            >
              {label}
            </div>
          ))}
        </div>
      )}

      <div style={{ padding: "4px 0" }}>
        {visible.map((c) => (
          <div
            key={c.name}
            style={{
              position: "relative",
              display: "flex",
              gap: 6,
              padding: "2px 12px",
              alignItems: "baseline",
              background: joined.has(c.name) ? "#ede9fe" : undefined,
              fontWeight: joined.has(c.name) ? 600 : undefined,
            }}
          >
            {/* Handles de conexión por columna: izquierda = destino, derecha = origen */}
            <Handle
              id={`ct-${c.name}`}
              type="target"
              position={Position.Left}
              style={colHandle("left")}
            />
            <Handle
              id={`cs-${c.name}`}
              type="source"
              position={Position.Right}
              style={colHandle("right")}
            />
            <span style={{ width: 14, textAlign: "center" }}>
              {c.is_pk ? "🔑" : fkCols.has(c.name) ? "→" : ""}
            </span>
            <span style={{ fontWeight: c.is_pk ? 600 : 400 }}>{c.name}</span>
            <span style={{ color: "#999", marginLeft: "auto", fontSize: 11 }}>
              {c.data_type}
              {c.is_nullable ? "" : " ·"}
            </span>
          </div>
        ))}
        {truncated > 0 && (
          <div style={{ padding: "2px 12px", color: "#999", fontSize: 11, fontStyle: "italic" }}>
            +{truncated} columnas más
          </div>
        )}
      </div>
    </div>
  );
}
