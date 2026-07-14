/**
 * Zoom global de la interfaz (Ctrl/⌘ + '=', '-', '0'), persistido en
 * localStorage.
 *
 * En Tauri se usa el ZOOM NATIVO del webview (como Ctrl +/- del navegador):
 * escala toda la UI de forma uniforme y coherente, por lo que React Flow y
 * el drag-and-drop siguen funcionando. En modo navegador (sin Tauri) se cae a
 * la propiedad CSS `zoom` como respaldo, pero al 100% se quita por completo
 * porque dejarla puesta rompe el DnD de HTML5 en Chromium.
 */
import { invoke } from "@tauri-apps/api/core";

const KEY = "pg-zoom";
export const ZOOM_MIN = 0.5;
export const ZOOM_MAX = 2;
const STEP = 0.1;

const inTauri = () => "__TAURI_INTERNALS__" in window;

export function currentZoom(): number {
  const v = Number(localStorage.getItem(KEY));
  return v >= ZOOM_MIN && v <= ZOOM_MAX ? v : 1;
}

function apply(z: number): void {
  const root = document.documentElement.style as unknown as { zoom: string };
  if (inTauri()) {
    // Zoom nativo del webview (no rompe el drag-and-drop). Limpiamos cualquier
    // `zoom` CSS previo para que no se acumule ni interfiera.
    root.zoom = "";
    void invoke("set_ui_zoom", { factor: z });
  } else {
    // Respaldo en navegador. IMPORTANTE: al 100% se quita la propiedad `zoom`
    // por completo — dejar `zoom:1` puesto sigue rompiendo el DnD de HTML5.
    root.zoom = z === 1 ? "" : String(z);
  }
}

export function setZoom(z: number): number {
  const clamped = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(z * 10) / 10));
  localStorage.setItem(KEY, String(clamped));
  apply(clamped);
  window.dispatchEvent(new CustomEvent("pg-zoom", { detail: clamped }));
  return clamped;
}

export function zoomIn(): number {
  return setZoom(currentZoom() + STEP);
}

export function zoomOut(): number {
  return setZoom(currentZoom() - STEP);
}

export function zoomReset(): number {
  return setZoom(1);
}

export function initZoom(): void {
  apply(currentZoom());
}

/**
 * Registra los atajos de teclado globales. Devuelve la función para quitarlos.
 * Ctrl/⌘ + '=' o '+' aumenta, '-' o '_' reduce, '0' restablece.
 */
export function registerZoomShortcuts(): () => void {
  const onKey = (e: KeyboardEvent) => {
    if (!(e.ctrlKey || e.metaKey)) return;
    if (e.key === "=" || e.key === "+") {
      e.preventDefault();
      zoomIn();
    } else if (e.key === "-" || e.key === "_") {
      e.preventDefault();
      zoomOut();
    } else if (e.key === "0") {
      e.preventDefault();
      zoomReset();
    }
  };
  window.addEventListener("keydown", onKey);
  return () => window.removeEventListener("keydown", onKey);
}
