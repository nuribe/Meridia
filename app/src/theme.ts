/** Sistema de temas: 4 variantes seleccionables, persistidas en localStorage. */

export interface ThemeDef {
  id: string;
  label: string;
  /** Modo base de Bootstrap (colores de fondo/texto). */
  bs: "light" | "dark";
  /** Muestra para el selector: es el acento del modo Explorador del tema. */
  swatch: string;
}

// Los swatches son los únicos colores literales que quedan fuera de themes.css,
// y a propósito: cada muestra representa a SU tema, no al que está activo. Un
// var(--pg-accent) pintaría las cuatro del mismo color. Se corresponden con el
// --pg-accent (modo Explorador) de cada bloque de themes.css.
export const THEMES: ThemeDef[] = [
  { id: "claro", label: "Claro", bs: "light", swatch: "#0b5ed7" },
  { id: "oceano", label: "Océano", bs: "light", swatch: "#0e7490" },
  { id: "oscuro", label: "Oscuro", bs: "dark", swatch: "#1d4ed8" },
  { id: "violeta", label: "Violeta", bs: "dark", swatch: "#6d28d9" },
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
