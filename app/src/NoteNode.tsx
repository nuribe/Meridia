/**
 * Sticky note del lienzo: texto editable, redimensionable (al seleccionarla)
 * y con paleta de colores.
 */
import { NodeResizer, type Node, type NodeProps } from "@xyflow/react";

/** Colores de nota. Los temas oscuros bajan el brillo de los pasteles para que
 *  no deslumbren sobre el lienzo, manteniendo la metáfora de papel: fondo claro
 *  y texto oscuro en los cuatro temas. */
export const NOTE_PALETTE = [
  "var(--pg-note-c1)",
  "var(--pg-note-c2)",
  "var(--pg-note-c3)",
  "var(--pg-note-c4)",
  "var(--pg-note-c5)",
  "var(--pg-note-c6)",
];

export interface NoteData {
  text: string;
  color: string;
  onChange: (id: string, patch: Partial<{ text: string; color: string }>) => void;
  onRemove: (id: string) => void;
  [key: string]: unknown;
}

export type NoteNodeType = Node<NoteData, "note">;

export default function NoteNode({ id, data, selected }: NodeProps<NoteNodeType>) {
  function cycleColor(e: React.MouseEvent) {
    e.stopPropagation();
    const next = NOTE_PALETTE[(NOTE_PALETTE.indexOf(data.color) + 1) % NOTE_PALETTE.length];
    data.onChange(id, { color: next });
  }

  return (
    <div style={{ width: "100%", height: "100%", minWidth: 120, minHeight: 80 }}>
      <NodeResizer isVisible={!!selected} minWidth={120} minHeight={80} lineStyle={{ borderColor: "var(--pg-note-resize)" }} />
      <div
        style={{
          width: "100%",
          height: "100%",
          background: data.color,
          borderRadius: 4,
          boxShadow: "0 3px 8px var(--pg-node-shadow)",
          display: "flex",
          flexDirection: "column",
          fontFamily: "system-ui",
        }}
      >
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 2, padding: "3px 6px 0" }}>
          <span onClick={cycleColor} title="Cambiar color" style={{ cursor: "pointer", opacity: 0.6, fontSize: 11 }}>◐</span>
          <span
            onClick={(e) => {
              e.stopPropagation();
              data.onRemove(id);
            }}
            title="Eliminar nota"
            style={{ cursor: "pointer", opacity: 0.6, fontSize: 11 }}
          >
            ✕
          </span>
        </div>
        <textarea
          value={data.text}
          placeholder="Escribe una nota…"
          onChange={(e) => data.onChange(id, { text: e.target.value })}
          className="nodrag"
          style={{
            flex: 1,
            background: "transparent",
            border: "none",
            outline: "none",
            resize: "none",
            padding: "2px 8px 8px",
            fontSize: 13,
            fontFamily: "inherit",
            color: "var(--pg-note-fg)",
          }}
        />
      </div>
    </div>
  );
}
