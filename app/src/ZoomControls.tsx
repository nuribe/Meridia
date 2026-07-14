/** Controles de zoom de la interfaz: −, porcentaje (clic = 100%), +. */
import { useEffect, useState } from "react";
import { currentZoom, zoomIn, zoomOut, zoomReset, ZOOM_MIN, ZOOM_MAX } from "./zoom";

export default function ZoomControls() {
  const [z, setZ] = useState(currentZoom());

  useEffect(() => {
    const onZoom = (e: Event) => setZ((e as CustomEvent<number>).detail);
    window.addEventListener("pg-zoom", onZoom);
    return () => window.removeEventListener("pg-zoom", onZoom);
  }, []);

  return (
    <div className="btn-group btn-group-sm" role="group" aria-label="Zoom de la interfaz">
      <button
        className="btn btn-outline-secondary"
        onClick={() => setZ(zoomOut())}
        disabled={z <= ZOOM_MIN}
        title="Reducir (Ctrl −)"
      >
        −
      </button>
      <button
        className="btn btn-outline-secondary font-monospace"
        onClick={() => setZ(zoomReset())}
        title="Restablecer al 100% (Ctrl 0)"
        style={{ minWidth: 52 }}
      >
        {Math.round(z * 100)}%
      </button>
      <button
        className="btn btn-outline-secondary"
        onClick={() => setZ(zoomIn())}
        disabled={z >= ZOOM_MAX}
        title="Aumentar (Ctrl +)"
      >
        +
      </button>
    </div>
  );
}
