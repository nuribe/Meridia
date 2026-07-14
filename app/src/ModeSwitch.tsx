/**
 * Conmutador Explorador / Diagramas: siempre visible, el modo activo queda
 * resaltado con su color propio (Explorador = acento; Diagramas = acento 2).
 */
export default function ModeSwitch({
  mode,
  onChange,
}: {
  mode: "explorer" | "diagram";
  onChange: (m: "explorer" | "diagram") => void;
}) {
  function btnStyle(active: boolean, colorVar: string): React.CSSProperties {
    return active
      ? {
          background: `var(${colorVar})`,
          borderColor: `var(${colorVar})`,
          color: "#fff",
          fontWeight: 700,
        }
      : {
          background: "transparent",
          borderColor: `var(${colorVar})`,
          color: `var(${colorVar})`,
        };
  }
  return (
    <div className="btn-group btn-group-sm flex-shrink-0" role="group" aria-label="Modo de trabajo">
      <button
        className="btn"
        style={btnStyle(mode === "explorer", "--pg-accent")}
        onClick={() => mode !== "explorer" && onChange("explorer")}
        title="Explorador: navegar objetos, metadata y datos"
      >
        🔎 Explorador
      </button>
      <button
        className="btn"
        style={btnStyle(mode === "diagram", "--pg-accent2")}
        onClick={() => mode !== "diagram" && onChange("diagram")}
        title="Diagramas: lienzo ER con pestañas"
      >
        ◇ Diagramas
      </button>
    </div>
  );
}
