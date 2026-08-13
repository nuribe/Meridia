/**
 * Barra de conexión: la franja superior común a Explorador y Diagramas.
 *
 * Es un componente compartido y no una copia por vista a propósito. Cuando cada
 * modo tenía su propio encabezado, Diagramas se quedó sin la información de la
 * conexión y metió el conmutador en una columna de 300 px donde ya no cabía.
 * Con una sola barra, lo que se añada aquí aparece en los dos sitios y el
 * conmutador vive en una fila flexible que no puede desbordarse.
 *
 * El color del borde inferior identifica el modo, con el mismo token que usa el
 * botón activo del conmutador.
 */
import type { ReactNode } from "react";
import ModeSwitch, { type WorkMode } from "./ModeSwitch";
import ThemeMenu from "./ThemeMenu";
import type { IntrospectSummary } from "./api/client";

const ACCENT: Record<WorkMode, string> = {
  explorer: "var(--pg-accent-text)",
  diagram: "var(--pg-accent2-text)",
  activity: "var(--pg-accent3-text)",
};

function WriteModeBadge({ allowWrites }: { allowWrites: boolean }) {
  // Ojo: .badge fija color:#fff, así que todo badge necesita una utilidad de
  // color de texto explícita o queda invisible sobre fondo claro.
  return allowWrites ? (
    <span
      className="badge bg-warning-subtle text-warning-emphasis border border-warning-subtle fw-semibold"
      title="Esta conexión permite INSERT, UPDATE, DELETE y DDL desde el editor de consultas. Un UPDATE o DELETE sin WHERE pedirá confirmación."
    >
      ✎ escritura
    </span>
  ) : (
    <span
      className="badge bg-body-secondary text-body-secondary border fw-normal"
      title="Esta conexión es un visor: solo SELECT y similares. Actívala con «Permitir escritura» al editar el perfil."
    >
      🔒 solo lectura
    </span>
  );
}

export default function ConnectionBar({
  mode,
  dbname,
  allowWrites,
  summary,
  onBack,
  onChangeMode,
  onRefresh,
  extra,
}: {
  mode: WorkMode;
  dbname: string;
  allowWrites: boolean;
  /** Resumen del snapshot; mientras carga se omite en vez de mostrar ceros. */
  summary: IntrospectSummary | null;
  /** Volver a la lista de bases de datos. */
  onBack: () => void;
  onChangeMode: (m: WorkMode) => void;
  /** Re-introspectar. Sin él no se pinta el botón. */
  onRefresh?: () => void;
  /** Controles propios del modo, antes del separador flexible. */
  extra?: ReactNode;
}) {
  return (
    <header
      className="d-flex align-items-center gap-2 px-3 py-2 bg-body flex-wrap"
      style={{ borderBottom: `3px solid ${ACCENT[mode]}` }}
    >
      <button
        className="btn btn-sm btn-outline-secondary"
        onClick={onBack}
        title="Volver a la lista de bases de datos"
      >
        ←
      </button>
      <ModeSwitch mode={mode} onChange={onChangeMode} />
      <span className="fw-semibold fs-6 ms-1">🗄 {dbname}</span>
      <WriteModeBadge allowWrites={allowWrites} />
      {summary && (
        <small className="text-body-secondary">
          {summary.schemas.length} schemas · {summary.object_count} objetos ·{" "}
          {summary.relationship_count} relaciones · snapshot{" "}
          {new Date(summary.created_at).toLocaleTimeString()}
        </small>
      )}
      {extra}
      <span className="flex-grow-1" />
      {onRefresh && (
        <button
          className="btn btn-sm btn-outline-secondary"
          onClick={onRefresh}
          title="Re-introspectar la base de datos (refresca el snapshot)"
        >
          ⟳
        </button>
      )}
      <ThemeMenu />
    </header>
  );
}
