/**
 * Nodo del lienzo que representa una tabla/vista con sus columnas.
 * Personalizable: color de cabecera, colapsar, ocultar columnas (modo ✎).
 * PK marcada con llave, columnas FK con flecha.
 */
import { useState } from "react";
import { Handle, Position, type NodeProps, type Node } from "@xyflow/react";
import type { TableDetail } from "./api/client";

const MAX_COLS = 14;

export const NODE_PALETTE = ["#12305c", "#0e6b5c", "#5b3a8c", "#8a3a3a", "#3a6e2f", "#555555"];

export interface NodeCustom {
  color?: string | null;
  collapsed?: boolean;
  hidden?: string[];
  display?: "default" | "all" | "keys";
}

export type TableNodeType = Node<
  {
    table: TableDetail;
    custom: NodeCustom;
    highlight?: string[];
    joinHighlight?: string[];
    onRemove: (key: string) => void;
    onCustomChange: (key: string, patch: Partial<NodeCustom>) => void;
    onAddRelated: (key: string, direction: "in" | "out" | "both") => void;
  },
  "table"
>;

const btn: React.CSSProperties = {
  cursor: "pointer",
  opacity: 0.8,
  padding: "0 3px",
  userSelect: "none",
  fontSize: 11,
};

export default function TableNode({ data }: NodeProps<TableNodeType>) {
  const { table: t, custom, highlight, joinHighlight, onRemove, onCustomChange, onAddRelated } = data;
  const highlighted = new Set(highlight ?? []);
  const joinHl = new Set(joinHighlight ?? []);
  const key = `${t.schema_name}.${t.name}`;
  const [editCols, setEditCols] = useState(false);
  const [relMenu, setRelMenu] = useState(false);
  const color = custom.color ?? NODE_PALETTE[0];
  const hidden = new Set(custom.hidden ?? []);
  const collapsed = custom.collapsed ?? false;

  const fkCols = new Set(t.foreign_keys.flatMap((fk) => fk.columns));
  const display = custom.display ?? "default";
  const base = editCols ? t.columns : t.columns.filter((c) => !hidden.has(c.name));
  const afterKeys =
    !editCols && display === "keys"
      ? base.filter((c) => c.is_pk || fkCols.has(c.name))
      : base;
  const visible = editCols || display === "all" ? afterKeys : afterKeys.slice(0, MAX_COLS);
  const truncated = afterKeys.length - visible.length;

  function cycleColor(e: React.MouseEvent) {
    e.stopPropagation();
    const next = NODE_PALETTE[(NODE_PALETTE.indexOf(color) + 1) % NODE_PALETTE.length];
    onCustomChange(key, { color: next });
  }

  return (
    <div
      style={{
        position: "relative",
        background: "#fff",
        border: `1.5px solid ${color}`,
        borderRadius: 8,
        minWidth: 200,
        fontSize: 12,
        fontFamily: "system-ui",
        boxShadow: "0 2px 6px rgba(0,0,0,.12)",
      }}
    >
      {/* Handles en los cuatro lados: la arista elige el más cercano automáticamente */}
      <Handle id="t-left" type="target" position={Position.Left} style={{ background: color, width: 7, height: 7 }} />
      <Handle id="t-right" type="target" position={Position.Right} style={{ background: color, width: 7, height: 7 }} />
      <Handle id="t-top" type="target" position={Position.Top} style={{ background: color, width: 7, height: 7 }} />
      <Handle id="t-bottom" type="target" position={Position.Bottom} style={{ background: color, width: 7, height: 7 }} />
      <Handle id="s-left" type="source" position={Position.Left} style={{ background: color, width: 7, height: 7 }} />
      <Handle id="s-right" type="source" position={Position.Right} style={{ background: color, width: 7, height: 7 }} />
      <Handle id="s-top" type="source" position={Position.Top} style={{ background: color, width: 7, height: 7 }} />
      <Handle id="s-bottom" type="source" position={Position.Bottom} style={{ background: color, width: 7, height: 7 }} />
      <div
        style={{
          background: color,
          color: "#fff",
          padding: "5px 8px",
          borderRadius: collapsed ? 6 : "6px 6px 0 0",
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
        <span onClick={cycleColor} title="Cambiar color" style={btn}>◐</span>
        <span
          onClick={(e) => {
            e.stopPropagation();
            setEditCols(false);
            onCustomChange(key, { collapsed: !collapsed });
          }}
          title={collapsed ? "Expandir columnas" : "Colapsar"}
          style={btn}
        >
          {collapsed ? "▸" : "▾"}
        </span>
        {!collapsed && (
          <span
            onClick={(e) => {
              e.stopPropagation();
              setEditCols(!editCols);
            }}
            title={editCols ? "Terminar edición de columnas" : "Ocultar/mostrar columnas"}
            style={{ ...btn, background: editCols ? "rgba(255,255,255,.25)" : undefined, borderRadius: 3 }}
          >
            ✎
          </span>
        )}
        <span
          onClick={(e) => {
            e.stopPropagation();
            onRemove(key);
          }}
          title="Quitar del diagrama"
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

      {collapsed ? (
        <div style={{ padding: "3px 10px", color: "#888", fontSize: 11 }}>
          {t.columns.length} columnas{hidden.size > 0 ? ` · ${hidden.size} ocultas` : ""}
        </div>
      ) : (
        <div style={{ padding: "4px 0" }}>
          {editCols && (
            <div style={{ padding: "2px 10px", color: "#888", fontSize: 11, fontStyle: "italic" }}>
              Clic en una columna para ocultarla/mostrarla
            </div>
          )}
          {visible.map((c) => {
            const isHidden = hidden.has(c.name);
            return (
              <div
                key={c.name}
                onClick={
                  editCols
                    ? (e) => {
                        e.stopPropagation();
                        const next = isHidden
                          ? (custom.hidden ?? []).filter((n) => n !== c.name)
                          : [...(custom.hidden ?? []), c.name];
                        onCustomChange(key, { hidden: next });
                      }
                    : undefined
                }
                style={{
                  display: "flex",
                  gap: 6,
                  padding: "1.5px 10px",
                  alignItems: "baseline",
                  cursor: editCols ? "pointer" : undefined,
                  opacity: editCols && isHidden ? 0.35 : 1,
                  textDecoration: editCols && isHidden ? "line-through" : undefined,
                  background: highlighted.has(c.name)
                    ? "#fde68a"
                    : joinHl.has(c.name)
                      ? "#ede9fe"
                      : undefined,
                  borderRadius: highlighted.has(c.name) || joinHl.has(c.name) ? 3 : undefined,
                  fontWeight: highlighted.has(c.name) || joinHl.has(c.name) ? 600 : undefined,
                }}
              >
                <span style={{ width: 14, textAlign: "center" }}>
                  {editCols ? (isHidden ? "◻" : "◼") : c.is_pk ? "🔑" : fkCols.has(c.name) ? "→" : ""}
                </span>
                <span style={{ fontWeight: c.is_pk ? 600 : 400 }}>{c.name}</span>
                <span style={{ color: "#999", marginLeft: "auto", fontSize: 11 }}>{c.data_type}</span>
              </div>
            );
          })}
          {!editCols && (
            <div
              className="nodrag"
              style={{ display: "flex", gap: 12, padding: "3px 10px 1px", fontSize: 11, borderTop: "1px solid #f0f0f0" }}
            >
              {truncated > 0 && (
                <a
                  onClick={(e) => {
                    e.stopPropagation();
                    onCustomChange(key, { display: "all" });
                  }}
                  style={{ cursor: "pointer", color: "#1a56b0" }}
                  title="Mostrar todas las columnas"
                >
                  ▾ Todas (+{truncated})
                </a>
              )}
              {display === "all" && (
                <a
                  onClick={(e) => {
                    e.stopPropagation();
                    onCustomChange(key, { display: "default" });
                  }}
                  style={{ cursor: "pointer", color: "#1a56b0" }}
                  title="Volver a la vista por defecto"
                >
                  ▴ Menos
                </a>
              )}
              <a
                onClick={(e) => {
                  e.stopPropagation();
                  onCustomChange(key, { display: display === "keys" ? "default" : "keys" });
                }}
                style={{ cursor: "pointer", color: display === "keys" ? "#b45309" : "#1a56b0" }}
                title={display === "keys" ? "Mostrar columnas normales" : "Contraer a solo claves (PK/FK)"}
              >
                {display === "keys" ? "▦ Por defecto" : "🔑 Solo claves"}
              </a>
              {hidden.size > 0 && <span style={{ color: "#bbb" }}>{hidden.size} ocultas</span>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
