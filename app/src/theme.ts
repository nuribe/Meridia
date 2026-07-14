/** Sistema de temas: 4 variantes seleccionables, persistidas en localStorage. */

export interface ThemeDef {
  id: string;
  label: string;
  /** Modo base de Bootstrap (colores de fondo/texto). */
  bs: "light" | "dark";
  /** Muestra para el selector. */
  swatch: string;
}

export const THEMES: ThemeDef[] = [
  { id: "claro", label: "Claro", bs: "light", swatch: "#0d6efd" },
  { id: "oceano", label: "Océano", bs: "light", swatch: "#0e7490" },
  { id: "oscuro", label: "Oscuro", bs: "dark", swatch: "#3b82f6" },
  { id: "violeta", label: "Violeta", bs: "dark", swatch: "#8b5cf6" },
];

const KEY = "pg-theme";

export function currentTheme(): string {
  return localStorage.getItem(KEY) ?? "claro";
}

export function applyTheme(id: string) {
  const theme = THEMES.find((t) => t.id === id) ?? THEMES[0];
  document.documentElement.setAttribute("data-bs-theme", theme.bs);
  document.documentElement.setAttribute("data-pg-theme", theme.id);
  localStorage.setItem(KEY, theme.id);
}

export function initTheme() {
  applyTheme(currentTheme());
}
