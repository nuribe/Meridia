/**
 * Nodo del lienzo que representa una tabla/vista con sus columnas.
 * Personalizable: color de cabecera, colapsar, ocultar columnas (modo ✎).
 * PK marcada con llave, columnas FK con flecha.
 *
 * Conectores: todo el contorno es zona de conexión (ver anchors.ts). Cada
 * relación engancha en un punto continuo del perímetro, así una tabla con muchas
 * FKs muestra puntos separados en vez de un nudo, y al arrastrarla se deslizan.
 *
 * Clic en una columna: pide a DiagramView que trace sus relaciones.
 */
import { memo, useContext, useState } from "react";
import { Handle, type NodeProps, type Node } from "@xyflow/react";
import type { TableDetail } from "./api/client";
import {
  AnchorUsageContext,
  SIDES,
  SIDE_POSITION,
  handleId,
  type Anchor,
  type HandleRole,
  type Side,
} from "./anchors";

const MAX_COLS = 14;

/**
 * Handles: uno por lado y rol, y nada más.
 *
 * Son el mínimo que React Flow necesita para enlazar source con target. Están
 * siempre en el centro del lado y son invisibles a propósito, porque el punto
 * real de la arista no sale de aquí: lo calcula la propia arista (`RelEdge`) a
 * partir del anclaje continuo. Antes había una rejilla de ~140 handles por nodo
 * intentando hacer este trabajo, y el precio era que el enlace saltaba de slot
 * en slot al arrastrar.
 */
const HANDLE_SPECS: { id: string; role: HandleRole; side: Side }[] = SIDES.flatMap((side) =>
  (["t", "s"] as HandleRole[]).map((role) => ({ id: handleId(role, side), role, side }))
);

/** Puntito del contorno que marca dónde engancha una relación. */
function dotStyle(a: Anchor, color: string): React.CSSProperties {
  const along = `${Math.min(1, Math.max(0, a.frac)) * 100}%`;
  const base: React.CSSProperties = {
    position: "absolute",
    width: 8,
    height: 8,
    boxSizing: "border-box",
    borderRadius: "50%",
    background: color,
    border: "1.5px solid var(--pg-node-bg)",
    transform: "translate(-50%, -50%)",
    pointerEvents: "none",
    zIndex: 2,
  };
  if (a.side === "left") return { ...base, left: 0, top: along };
  if (a.side === "right") return { ...base, left: "100%", top: along };
  if (a.side === "top") return { ...base, top: 0, left: along };
  return { ...base, top: "100%", left: along };
}

/**
 * Puntos del contorno + halo del perímetro.
 *
 * Va aparte y memoizado por una razón concreta: los anclajes cambian en CADA
 * fotograma de un arrastre (para eso son continuos). Si `TableNode` consumiera
 * el contexto, cada fotograma re-renderizaría la lista de columnas de todas las
 * tablas del lienzo. Aislado aquí, lo que se repinta son cuatro `div`.
 */
const AnchorDots = memo(function AnchorDots({ nodeKey, color }: { nodeKey: string; color: string }) {
  const usage = useContext(AnchorUsageContext);
  const anchors = usage[nodeKey];
  if (!anchors?.length) return null;
  return (
    <>
      <div style={perimeterStyle(color)} />
      {anchors.map((a, i) => (
        <div key={`${a.side}-${i}`} style={dotStyle(a, color)} />
      ))}
    </>
  );
});

/** Contorno sutil que indica que todo el perímetro admite conexiones. */
function perimeterStyle(color: string): React.CSSProperties {
  return {
    position: "absolute",
    inset: -4,
    border: `1px solid ${color}`,
    borderRadius: 11,
    opacity: 0.16,
    pointerEvents: "none",
  };
}

/**
 * Colores ciclables de la cabecera (botón ◐).
 *
 * Son tokens y no literales porque un azul marino que funciona sobre una
 * tarjeta blanca se hunde en el fondo sobre una oscura. Cada tema define sus
 * seis variantes; todas garantizan texto blanco legible encima.
 *
 * Los diagramas guardados antes de este cambio llevan un hex literal: sigue
 * pintándose tal cual, solo que al pulsar ◐ el ciclo vuelve a empezar por c1.
 */
export const NODE_PALETTE = [
  "var(--pg-node-c1)",
  "var(--pg-node-c2)",
  "var(--pg-node-c3)",
  "var(--pg-node-c4)",
  "var(--pg-node-c5)",
  "var(--pg-node-c6)",
];

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
    /** Columna sobre la que el usuario hizo clic para trazar su relación. */
    pickedColumn?: string;
    onRemove: (key: string) => void;
    onCustomChange: (key: string, patch: Partial<NodeCustom>) => void;
    onAddRelated: (key: string, direction: "in" | "out" | "both") => void;
    onColumnClick?: (key: string, column: string) => void;
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
  const {
    table: t,
    custom,
    highlight,
    joinHighlight,
    pickedColumn,
    onRemove,
    onCustomChange,
    onAddRelated,
    onColumnClick,
  } = data;
  const highlighted = new Set(highlight ?? []);
  const joinHl = new Set(joinHighlight ?? []);
  const key = `${t.schema_name}.${t.name}`;
  const [editCols, setEditCols] = useState(false);
  const [relMenu, setRelMenu] = useState(false);
  const color = custom.color ?? NODE_PALETTE[0];
  const hidden = new Set(custom.hidden ?? []);
  const collapsed = custom.collapsed ?? false;

  const fkCols = new Set(t.foreign_keys.flatMap((fk) => fk.columns));
  // FKs que apuntan a la propia tabla: se marcan con ↻ en vez de →, si no la
  // relación reflexiva pasa desapercibida dentro de la lista de columnas.
  const selfFkCols = new Set(
    t.foreign_keys
      .filter((fk) => fk.ref_schema === t.schema_name && fk.ref_table === t.name)
      .flatMap((fk) => fk.columns)
  );
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
        background: "var(--pg-node-bg)",
        // Fijar el fondo obliga a fijar también el texto: heredarlo era lo que
        // dejaba gris claro sobre blanco (1.30:1) en los temas oscuros.
        color: "var(--pg-node-fg)",
        border: `1.5px solid ${color}`,
        borderRadius: 8,
        minWidth: 200,
        fontSize: 12,
        fontFamily: "system-ui",
        boxShadow: "0 2px 6px var(--pg-node-shadow)",
      }}
    >
      {/* Halo del perímetro + punto de enganche de cada relación. */}
      <AnchorDots nodeKey={key} color={color} />

      {/* Handles de enganche, invisibles: la posición real la pone la arista. */}
      {HANDLE_SPECS.map(({ id, role, side }) => (
        <Handle
          key={id}
          id={id}
          type={role === "s" ? "source" : "target"}
          position={SIDE_POSITION[side]}
          isConnectable={false}
          style={{
            width: 1,
            height: 1,
            minWidth: 1,
            minHeight: 1,
            background: "transparent",
            border: "none",
            opacity: 0,
            zIndex: 0,
          }}
        />
      ))}

      <div
        style={{
          background: color,
          color: "var(--pg-node-header-fg)",
          padding: "5px 8px",
          borderRadius: collapsed ? 6 : "6px 6px 0 0",
          display: "flex",
          alignItems: "center",
          gap: 4,
        }}
      >
        <span style={{ color: "color-mix(in srgb, var(--pg-node-header-fg) 70%, transparent)", fontSize: 11 }}>{t.schema_name}.</span>
        <strong style={{ flex: 1 }}>{t.name}</strong>
        <span
          onClick={(e) => {
            e.stopPropagation();
            setRelMenu(!relMenu);
          }}
          title="Añadir tablas relacionadas…"
          style={{ ...btn, background: relMenu ? "color-mix(in srgb, var(--pg-node-header-fg) 25%, transparent)" : undefined, borderRadius: 3 }}
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
            style={{ ...btn, background: editCols ? "color-mix(in srgb, var(--pg-node-header-fg) 25%, transparent)" : undefined, borderRadius: 3 }}
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
            background: "var(--pg-menu-bg)",
            border: "1px solid var(--pg-menu-border)",
            borderRadius: 6,
            boxShadow: "0 4px 12px var(--pg-node-shadow)",
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
              onMouseEnter={(e) => ((e.target as HTMLElement).style.background = "var(--pg-menu-hover)")}
              onMouseLeave={(e) => ((e.target as HTMLElement).style.background = "")}
              style={{ padding: "6px 10px", cursor: "pointer", color: "var(--pg-menu-fg)" }}
            >
              {label}
            </div>
          ))}
        </div>
      )}

      {collapsed ? (
        <div style={{ padding: "3px 10px", color: "var(--pg-node-muted)", fontSize: 11 }}>
          {t.columns.length} columnas{hidden.size > 0 ? ` · ${hidden.size} ocultas` : ""}
        </div>
      ) : (
        <div style={{ padding: "4px 0" }}>
          {editCols && (
            <div style={{ padding: "2px 10px", color: "var(--pg-node-muted)", fontSize: 11, fontStyle: "italic" }}>
              Clic en una columna para ocultarla/mostrarla
            </div>
          )}
          {visible.map((c) => {
            const isHidden = hidden.has(c.name);
            const isFk = fkCols.has(c.name);
            const picked = pickedColumn === c.name;
            const marked = highlighted.has(c.name) || joinHl.has(c.name);
            return (
              <div
                key={c.name}
                title={
                  editCols
                    ? undefined
                    : selfFkCols.has(c.name)
                      ? "Relación reflexiva (apunta a esta misma tabla). Clic para seguirla."
                      : isFk || c.is_pk
                        ? "Clic: seguir la relación de esta columna"
                        : undefined
                }
                onClick={
                  editCols
                    ? (e) => {
                        e.stopPropagation();
                        const next = isHidden
                          ? (custom.hidden ?? []).filter((n) => n !== c.name)
                          : [...(custom.hidden ?? []), c.name];
                        onCustomChange(key, { hidden: next });
                      }
                    : onColumnClick
                      ? (e) => {
                          e.stopPropagation();
                          onColumnClick(key, c.name);
                        }
                      : undefined
                }
                style={{
                  display: "flex",
                  gap: 6,
                  padding: "1.5px 10px",
                  alignItems: "baseline",
                  cursor: editCols || onColumnClick ? "pointer" : undefined,
                  opacity: editCols && isHidden ? 0.35 : 1,
                  textDecoration: editCols && isHidden ? "line-through" : undefined,
                  background: picked
                    ? "var(--pg-hl-pick)"
                    : highlighted.has(c.name)
                      ? "var(--pg-hl-mark)"
                      : joinHl.has(c.name)
                        ? "var(--pg-hl-join)"
                        : undefined,
                  // Un resaltado cambia el fondo, así que tiene que traer su
                  // propio color de texto o el contraste depende del azar.
                  color: picked
                    ? "var(--pg-hl-pick-fg)"
                    : highlighted.has(c.name)
                      ? "var(--pg-hl-mark-fg)"
                      : joinHl.has(c.name)
                        ? "var(--pg-hl-join-fg)"
                        : undefined,
                  boxShadow: picked ? "inset 0 0 0 1.5px var(--pg-hl-pick-ring)" : undefined,
                  borderRadius: picked || marked ? 3 : undefined,
                  fontWeight: picked || marked ? 600 : undefined,
                }}
              >
                <span
                  style={{
                    width: 14,
                    textAlign: "center",
                    color: !editCols && selfFkCols.has(c.name) ? "var(--pg-hl-selffk)" : undefined,
                    fontWeight: !editCols && selfFkCols.has(c.name) ? 700 : undefined,
                  }}
                >
                  {editCols
                    ? isHidden
                      ? "◻"
                      : "◼"
                    : c.is_pk
                      ? "🔑"
                      : selfFkCols.has(c.name)
                        ? "↻"
                        : fkCols.has(c.name)
                          ? "→"
                          : ""}
                </span>
                <span style={{ fontWeight: c.is_pk ? 600 : 400 }}>{c.name}</span>
                <span style={{ color: "var(--pg-node-muted)", marginLeft: "auto", fontSize: 11 }}>{c.data_type}</span>
              </div>
            );
          })}
          {!editCols && (
            <div
              className="nodrag"
              style={{ display: "flex", gap: 12, padding: "3px 10px 1px", fontSize: 11, borderTop: "1px solid var(--pg-node-divider)" }}
            >
              {truncated > 0 && (
                <a
                  onClick={(e) => {
                    e.stopPropagation();
                    onCustomChange(key, { display: "all" });
                  }}
                  style={{ cursor: "pointer", color: "var(--pg-node-link)" }}
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
                  style={{ cursor: "pointer", color: "var(--pg-node-link)" }}
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
                style={{ cursor: "pointer", color: display === "keys" ? "var(--pg-node-link-on)" : "var(--pg-node-link)" }}
                title={display === "keys" ? "Mostrar columnas normales" : "Contraer a solo claves (PK/FK)"}
              >
                {display === "keys" ? "▦ Por defecto" : "🔑 Solo claves"}
              </a>
              {hidden.size > 0 && <span style={{ color: "var(--pg-node-faint)" }}>{hidden.size} ocultas</span>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
