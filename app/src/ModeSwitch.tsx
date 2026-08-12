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
  /**
   * Activo = relleno sólido (`--pg-accentN`) con su color de texto encima.
   * Inactivo = texto y borde sobre el fondo del tema, que es un requisito de
   * contraste distinto y por eso usa la variante `-text`.
   */
  function btnStyle(active: boolean, n: "" | "2"): React.CSSProperties {
    return active
      ? {
          background: `var(--pg-accent${n})`,
          borderColor: `var(--pg-accent${n})`,
          color: `var(--pg-accent${n}-fg)`,
          fontWeight: 700,
        }
      : {
          background: "transparent",
          borderColor: `var(--pg-accent${n}-text)`,
          color: `var(--pg-accent${n}-text)`,
        };
  }
  return (
    <div className="btn-group btn-group-sm flex-shrink-0" role="group" aria-label="Modo de trabajo">
      <button
        className="btn"
        style={btnStyle(mode === "explorer", "")}
        onClick={() => mode !== "explorer" && onChange("explorer")}
        title="Explorador: navegar objetos, metadata y datos"
      >
        🔎 Explorador
      </button>
      <button
        className="btn"
        style={btnStyle(mode === "diagram", "2")}
        onClick={() => mode !== "diagram" && onChange("diagram")}
        title="Diagramas: lienzo ER con pestañas"
      >
        ◇ Diagramas
      </button>
    </div>
  );
}
