/**
 * Verificador de contraste de los temas.
 *
 * Lee app/src/themes.css, extrae los tokens --pg-* y --bs-* de cada tema y
 * comprueba los pares texto/fondo que la interfaz produce de verdad, contra
 * los umbrales de WCAG 2.1 AA:
 *
 *   4.5:1  texto normal
 *   3.0:1  texto grande y elementos de interfaz que comunican estado
 *          (bordes de pestaña activa, iconos de tipo de objeto, aristas)
 *
 * Uso:  node scripts/check-contrast.mjs
 * Sale con código 1 si algún par incumple, para poder engancharlo a CI.
 */
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const css = readFileSync(join(here, "..", "app", "src", "themes.css"), "utf8");

import {
  contrast,
  deltaE2000,
  CVD,
  simulate,
} from "./color.mjs";

// ---------------------------------------------------------------- temas ----

/** Extrae los tokens de un bloque `[data-pg-theme="id"] { ... }`. */
function tokensOf(selector) {
  const re = new RegExp(`${selector}\\s*\\{([^}]*)\\}`, "m");
  const body = css.match(re);
  if (!body) throw new Error(`No se encontró el bloque ${selector}`);
  const out = {};
  for (const line of body[1].split("\n")) {
    const m = line.match(/^\s*(--[\w-]+)\s*:\s*([^;]+);/);
    if (m) out[m[1]] = m[2].trim();
  }
  return out;
}

const THEMES = {
  Claro: tokensOf('\\[data-pg-theme="claro"\\]'),
  "Océano": tokensOf('\\[data-pg-theme="oceano"\\]'),
  Oscuro: tokensOf('\\[data-pg-theme="oscuro"\\]'),
  Violeta: tokensOf('\\[data-pg-theme="violeta"\\]'),
};

/**
 * Pares a verificar. `[etiqueta, tokenTexto, tokenFondo, mínimo]`.
 * El mínimo por defecto es 4.5 (texto); 3 para elementos de interfaz.
 */
const UI = 3;
const CHECKS = [
  // Cuerpo de la aplicación
  ["texto del cuerpo", "--bs-body-color", "--bs-body-bg"],
  ["texto secundario", "--bs-secondary-color", "--bs-body-bg"],
  ["texto sobre fondo terciario", "--bs-body-color", "--bs-tertiary-bg"],
  // Un separador decorativo no entra en WCAG 1.4.11 y forzarlo a 3:1 dejaría la
  // interfaz con aspecto de wireframe. Lo que sí debe llegar a 3:1 es el borde
  // de los controles con los que se interactúa: ese es --pg-border-strong.
  ["separador decorativo", "--bs-border-color", "--bs-body-bg", 1.25],
  ["borde de control", "--pg-border-strong", "--bs-body-bg", UI],
  // Utilidades neutras que sustituyen a .text-bg-light / .btn-outline-light
  ["pastilla neutra", "--bs-body-color", "--bs-secondary-bg"],
  ["borde de botón neutro", "--pg-border-strong", "--bs-secondary-bg", UI],

  // Acentos: relleno y variante de texto
  ["texto sobre relleno acento", "--pg-accent-fg", "--pg-accent"],
  ["texto sobre relleno acento2", "--pg-accent2-fg", "--pg-accent2"],
  ["texto sobre relleno acento3", "--pg-accent3-fg", "--pg-accent3"],
  ["enlace/acento sobre el cuerpo", "--pg-accent-text", "--bs-body-bg"],
  ["enlace/acento2 sobre el cuerpo", "--pg-accent2-text", "--bs-body-bg"],
  ["enlace/acento3 sobre el cuerpo", "--pg-accent3-text", "--bs-body-bg"],
  ["acento sobre fondo terciario", "--pg-accent-text", "--bs-tertiary-bg"],
  ["acento2 sobre fondo terciario", "--pg-accent2-text", "--bs-tertiary-bg"],
  ["acento3 sobre fondo terciario", "--pg-accent3-text", "--bs-tertiary-bg"],

  // Tarjetas del lienzo — aquí estaba el fallo de 1.30:1
  ["nombre de columna", "--pg-node-fg", "--pg-node-bg"],
  ["tipo de dato", "--pg-node-muted", "--pg-node-bg"],
  ["texto tenue (n.º ocultas)", "--pg-node-faint", "--pg-node-bg", UI],
  ["enlace del nodo", "--pg-node-link", "--pg-node-bg"],
  ["enlace activo del nodo", "--pg-node-link-on", "--pg-node-bg"],
  ["separador del nodo", "--pg-node-divider", "--pg-node-bg", 1.2],
  ["tarjeta sobre el lienzo", "--pg-node-bg", "--pg-canvas-bg", 1.05],

  // Cabeceras de tabla: siempre texto blanco encima
  ...[1, 2, 3, 4, 5, 6].map((i) => [`cabecera c${i}`, "#ffffff", `--pg-node-c${i}`]),

  // Menús del lienzo
  ["texto de menú", "--pg-menu-fg", "--pg-menu-bg"],
  ["texto de menú en hover", "--pg-menu-fg", "--pg-menu-hover"],
  ["borde de menú", "--pg-menu-border", "--pg-menu-bg", 1.2],

  // Resaltados de columna
  ["columna seleccionada", "--pg-hl-pick-fg", "--pg-hl-pick"],
  ["anillo de selección", "--pg-hl-pick-ring", "--pg-hl-pick", UI],
  ["columna resaltada", "--pg-hl-mark-fg", "--pg-hl-mark"],
  ["columna de join", "--pg-hl-join-fg", "--pg-hl-join"],
  ["marca de FK reflexiva", "--pg-hl-selffk", "--pg-node-bg", UI],

  // Aristas sobre el lienzo
  ["arista normal", "--pg-edge", "--pg-canvas-bg", UI],
  ["arista reflexiva", "--pg-edge-self", "--pg-canvas-bg", UI],
  ["arista seleccionada", "--pg-edge-sel", "--pg-canvas-bg", UI],
  ["etiqueta de arista", "--pg-edge-label-fg", "--pg-edge-label-bg"],

  // Árbol de objetos
  ["texto del árbol en hover", "--bs-body-color", "--pg-tree-hover"],
  ["texto del árbol seleccionado", "--bs-body-color", "--pg-tree-selected"],
  ["texto sobre hover de schema", "--bs-body-color", "--pg-tree-schema-hover"],
  ["texto sobre hover de grupo", "--bs-body-color", "--pg-tree-group-hover"],
  ["metadatos del árbol", "--pg-tree-meta", "--bs-body-bg"],
  ["icono tabla", "--pg-kind-table", "--bs-body-bg", UI],
  ["icono tabla particionada", "--pg-kind-partitioned", "--bs-body-bg", UI],
  ["icono vista", "--pg-kind-view", "--bs-body-bg", UI],
  ["icono vista materializada", "--pg-kind-matview", "--bs-body-bg", UI],
  ["icono tabla foránea", "--pg-kind-foreign", "--bs-body-bg", UI],
  ["icono tabla en selección", "--pg-kind-table", "--pg-tree-selected", UI],
  ["icono vista en selección", "--pg-kind-view", "--pg-tree-selected", UI],

  // Notas adhesivas
  ...[1, 2, 3, 4, 5, 6].map((i) => [`nota c${i}`, "--pg-note-fg", `--pg-note-c${i}`]),

  // Plan de ejecución
  ["severidad alta", "--pg-sev-high", "--bs-body-bg", UI],
  ["severidad media", "--pg-sev-mid", "--bs-body-bg", UI],
  ["severidad baja", "--pg-sev-low", "--bs-body-bg", UI],

  // Resaltado SQL sobre el fondo del editor
  ...["comment", "string", "ident", "cast", "number", "keyword", "type", "func", "dollar"].map(
    (k) => [`sql ${k}`, `--pg-sql-${k}`, "--bs-body-bg"]
  ),
];

/**
 * Además de contrastar contra el fondo, los tres acentos deben distinguirse
 * ENTRE SÍ: son los que codifican Explorador vs. Diagramas vs. Actividad IA.
 * Sin esta comprobación, Océano volvía a tener dos teals casi idénticos.
 *
 * Se comprueban los TRES pares, no solo uno: con un tercer acento en juego, el
 * error fácil es elegir un verde que se distinga del azul y olvidar que bajo
 * deuteranopia colisiona con el ámbar.
 *
 * ΔE2000 >= 20 es la frontera habitual de "colores claramente distintos" (por
 * comparar: ~2.3 es el umbral en el que un ojo entrenado empieza a notar
 * diferencia). Con daltonismo relajamos a 10 porque ahí el tono se colapsa y
 * lo que queda es la diferencia de claridad — suficiente como refuerzo, ya que
 * el modo activo además va rotulado y en negrita, nunca solo por color.
 */
const MIN_DELTA_E = 20;
const MIN_DELTA_E_CVD = 10;

// -------------------------------------------- cobertura de tokens usados ----

/**
 * Un `var(--pg-tipo-mal-escrito)` no rompe nada visible: CSS lo ignora y el
 * elemento se queda con el color heredado, que es justo el fallo que estamos
 * intentando erradicar. Así que se comprueba que todo token citado en los
 * componentes exista de verdad, y en LOS CUATRO temas — definirlo solo en
 * Claro dejaría los otros tres con el mismo fallo silencioso.
 */
function checkTokenCoverage() {
  const srcDir = join(here, "..", "app", "src");
  const used = new Map(); // token -> ficheros que lo usan
  for (const file of readdirSync(srcDir)) {
    if (!/\.(tsx?|css)$/.test(file) || file === "themes.css") continue;
    const text = readFileSync(join(srcDir, file), "utf8");
    for (const m of text.matchAll(/var\((--pg-[\w-]+)\)/g)) {
      if (!used.has(m[1])) used.set(m[1], new Set());
      used.get(m[1]).add(file);
    }
  }

  const root = tokensOf("^:root");
  const problems = [];
  for (const [token, files] of [...used].sort()) {
    if (root[token]) continue; // invariante, definido una sola vez
    const missing = Object.entries(THEMES)
      .filter(([, toks]) => !toks[token])
      .map(([name]) => name);
    if (missing.length) problems.push({ token, files: [...files], missing });
  }

  console.log(`\nCobertura de tokens (${used.size} tokens --pg-* usados en componentes)`);
  console.log("─".repeat(72));
  if (!problems.length) {
    console.log("  ok   todos están definidos en los cuatro temas");
    return 0;
  }
  for (const p of problems) {
    console.log(` FALLA ${p.token} — falta en: ${p.missing.join(", ")}  (usado en ${p.files.join(", ")})`);
  }
  return problems.length;
}

// ------------------------------------------------------------- ejecución ----

function resolve(theme, token) {
  if (token.startsWith("#")) return token;
  const v = THEMES[theme][token];
  if (!v) throw new Error(`${theme}: falta el token ${token}`);
  if (!v.startsWith("#")) throw new Error(`${theme}: ${token} no es un hex (${v})`);
  return v;
}

let failures = 0;

for (const [theme] of Object.entries(THEMES)) {
  console.log(`\n${theme}`);
  console.log("─".repeat(72));
  for (const [label, fgTok, bgTok, min = 4.5] of CHECKS) {
    const fg = resolve(theme, fgTok);
    const bg = resolve(theme, bgTok);
    const r = contrast(fg, bg);
    const ok = r >= min;
    if (!ok) failures++;
    console.log(
      `${ok ? "  ok  " : " FALLA"} ${label.padEnd(34)} ${fg} / ${bg}  ` +
        `${r.toFixed(2).padStart(6)}:1  (min ${min})`
    );
  }

  const PAIRS = [
    ["1↔2", "--pg-accent-text", "--pg-accent2-text"],
    ["1↔3", "--pg-accent-text", "--pg-accent3-text"],
    ["2↔3", "--pg-accent2-text", "--pg-accent3-text"],
  ];

  for (const [label, tokA, tokB] of PAIRS) {
    const a = resolve(theme, tokA);
    const b = resolve(theme, tokB);

    const de = deltaE2000(a, b);
    const okDe = de >= MIN_DELTA_E;
    if (!okDe) failures++;
    console.log(
      `${okDe ? "  ok  " : " FALLA"} ${`acentos ${label} distinguibles (ΔE2000)`.padEnd(34)} ${a} / ${b}  ` +
        `${de.toFixed(1).padStart(6)}    (min ${MIN_DELTA_E})`
    );

    for (const kind of Object.keys(CVD)) {
      const sa = simulate(a, kind);
      const sb = simulate(b, kind);
      const d = deltaE2000(sa, sb);
      const ok = d >= MIN_DELTA_E_CVD;
      if (!ok) failures++;
      console.log(
        `${ok ? "  ok  " : " FALLA"} ${`acentos ${label} con ${kind}`.padEnd(34)} ${sa} / ${sb}  ` +
          `${d.toFixed(1).padStart(6)}    (min ${MIN_DELTA_E_CVD})`
      );
    }
  }
}

failures += checkTokenCoverage();

console.log("");
if (failures > 0) {
  console.error(`${failures} comprobación(es) de contraste fallaron.`);
  process.exit(1);
}
console.log("Todos los pares cumplen WCAG AA.");
