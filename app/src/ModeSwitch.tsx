/**
 * Conmutador Explorador / Diagramas / Actividad IA: siempre visible, el modo
 * activo queda resaltado con su color propio (Explorador = acento; Diagramas =
 * acento 2; Actividad = acento 3).
 *
 * Va siempre dentro de `ConnectionBar`, que es una fila flexible: las tres
 * etiquetas necesitan 385 px y no caben en una columna fija, así que el
 * conmutador no debe volver a meterse en una.
 */
import { useUnseenActivity } from "./activityBadge";

export type WorkMode = "explorer" | "diagram" | "activity";

const MODES: { id: WorkMode; icon: string; label: string; accent: "" | "2" | "3"; title: string }[] = [
  { id: "explorer", icon: "🔎", label: "Explorador", accent: "",
    title: "Explorador: navegar objetos, metadata y datos" },
  { id: "diagram", icon: "◇", label: "Diagramas", accent: "2",
    title: "Diagramas: lienzo ER con pestañas" },
  { id: "activity", icon: "⬤", label: "Actividad IA", accent: "3",
    title: "Actividad IA: qué está consultando un cliente MCP, y cómo cortarlo" },
];

export default function ModeSwitch({
  mode,
  onChange,
}: {
  mode: WorkMode;
  onChange: (m: WorkMode) => void;
}) {
  // Llamadas MCP que han entrado desde la última vez que se miró el panel. El
  // contador lo mantiene un único sondeo compartido, no uno por instancia.
  const unseen = useUnseenActivity();

  /**
   * Activo = relleno sólido (`--pg-accentN`) con su color de texto encima.
   * Inactivo = texto y borde sobre el fondo del tema, que es un requisito de
   * contraste distinto y por eso usa la variante `-text`.
   */
  function btnStyle(active: boolean, n: "" | "2" | "3"): React.CSSProperties {
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
      {MODES.map((m) => {
        const active = mode === m.id;
        return (
          <button
            key={m.id}
            className="btn position-relative"
            style={btnStyle(active, m.accent)}
            onClick={() => !active && onChange(m.id)}
            title={m.title}
            aria-label={m.label}
            aria-pressed={active}
          >
            {`${m.icon} ${m.label}`}
            {m.id === "activity" && unseen > 0 && !active && (
              <span
                className="badge rounded-pill position-absolute top-0 start-100 translate-middle"
                style={{
                  background: "var(--pg-accent3)",
                  color: "var(--pg-accent3-fg)",
                  fontSize: "0.65rem",
                }}
                aria-label={`${unseen} llamadas MCP sin revisar`}
              >
                {unseen > 99 ? "99+" : unseen}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
