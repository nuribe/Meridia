/**
 * Sticky note del lienzo: texto editable, redimensionable (al seleccionarla)
 * y con paleta de colores.
 */
import { NodeResizer, type Node, type NodeProps } from "@xyflow/react";

export const NOTE_PALETTE = ["#fff9b1", "#ffd6a5", "#c8f7c5", "#bde0fe", "#ffc9de", "#e8e8e8"];

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
      <NodeResizer isVisible={!!selected} minWidth={120} minHeight={80} lineStyle={{ borderColor: "#b8a200" }} />
      <div
        style={{
          width: "100%",
          height: "100%",
          background: data.color,
          borderRadius: 4,
          boxShadow: "0 3px 8px rgba(0,0,0,.18)",
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
            color: "#333",
          }}
        />
      </div>
    </div>
  );
}
