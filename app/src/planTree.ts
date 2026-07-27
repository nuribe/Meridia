/**
 * Reconstrucción del árbol del plan de ejecución a partir de la lista plana
 * que devuelve el sidecar. Sin dependencias (ni React, ni React Flow) para
 * poder razonarlo y probarlo aislado: es la pieza de la que depende que el
 * diagrama dibuje las flechas correctas.
 */

export interface PlanTreeEdge {
  /** Índice del operador hijo: el que produce las filas. */
  child: number;
  /** Índice del operador padre: el que las consume. */
  parent: number;
}

/**
 * El padre de un nodo es el último visto con profundidad inmediatamente
 * menor. Al subir de nivel se olvidan los descendientes más profundos, para
 * que una rama no herede el padre de la anterior.
 */
export function treeEdges(depths: number[]): PlanTreeEdge[] {
  const lastAtDepth = new Map<number, number>();
  const edges: PlanTreeEdge[] = [];
  depths.forEach((depth, i) => {
    const parent = lastAtDepth.get(depth - 1);
    if (depth > 0 && parent !== undefined) edges.push({ child: i, parent });
    for (const d of [...lastAtDepth.keys()]) if (d >= depth) lastAtDepth.delete(d);
    lastAtDepth.set(depth, i);
  });
  return edges;
}

/** Ancho fijo del nodo del diagrama (px). Lo comparten el layout y el render. */
export const PLAN_NODE_WIDTH = 300;

/** Margen (px) alrededor del plan en la imagen exportada. */
export const EXPORT_PADDING = 60;

/**
 * Lado máximo (px) de la imagen exportada. Muy por debajo del límite de canvas
 * del webview (~16 k px en Chromium), donde el `toDataURL` devuelve una imagen
 * en blanco sin avisar.
 */
export const EXPORT_MAX_SIDE = 12000;

/**
 * Densidad de píxeles del PNG exportado.
 *
 * Por defecto x2 para que el texto se lea nítido, pero un plan largo puede dar
 * una imagen de decenas de miles de píxeles: en ese caso se REDUCE por debajo
 * de 1 para no pasarse del límite del canvas. Se pone un suelo para no acabar
 * exportando algo ilegible.
 */
export function exportPixelRatio(longestSide: number): number {
  if (longestSide <= 0) return 2;
  return Math.min(2, Math.max(0.25, EXPORT_MAX_SIDE / longestSide));
}

/** Caracteres que caben por línea en el bloque de detalle (monoespaciada 10px). */
const DETAIL_CHARS_PER_LINE = 46;

/**
 * Líneas de detalle a mostrar dentro del nodo, sin repeticiones.
 *
 * Los motores solapan información: en SQL Server `text` es
 * `Nested Loops(Inner Join, OUTER REFERENCES:([p].[IdCiudad]))` y el detalle
 * trae por separado el operador lógico y ese mismo `OUTER REFERENCES`. En
 * PostgreSQL `text` es la línea del plan, que empieza por `op` y solo añade
 * los costes, ya visibles en las métricas del nodo.
 *
 * Reglas: se descarta `text` si no aporta nada sobre `op`, y se descarta toda
 * línea ya contenida en otra anterior. Si aun así no queda nada y `text` decía
 * algo distinto (p. ej. la sentencia completa en el nodo raíz), se conserva.
 */
export function detailLines(op: string, text: string, detail: string[]): string[] {
  const candidates = text && !text.startsWith(op) ? [text, ...detail] : [...detail];
  const kept: string[] = [];
  for (const line of candidates) {
    const clean = line.trim();
    if (clean && !kept.some((k) => k.includes(clean))) kept.push(clean);
  }
  if (kept.length === 0 && text && text !== op) kept.push(text);
  return kept;
}

/**
 * Alto que ocupará el nodo (px). Dagre necesita el tamaño ANTES de renderizar,
 * así que hay que estimar cuántas líneas ocupará el detalle al ajustarse al
 * ancho; si se quedara corto, los nodos se solaparían en el lienzo.
 */
export function planNodeHeight(lines: string[], showActualRows: boolean): number {
  const metrics = (showActualRows ? 4 : 3) * 18;
  const base = 30 + metrics + 5 + 10; // cabecera + métricas + barra + relleno
  if (lines.length === 0) return base;
  const wrapped = lines.reduce(
    (n, l) => n + Math.max(1, Math.ceil(l.length / DETAIL_CHARS_PER_LINE)),
    0
  );
  return base + 8 + wrapped * 14;
}

/**
 * Coste PROPIO de cada operador = su coste total menos el de sus hijos.
 *
 * Tanto PostgreSQL (`cost=…..total`) como SQL Server (`TotalSubtreeCost`)
 * devuelven el coste ACUMULADO del subárbol, así que la raíz siempre es la
 * más cara y colorear por ese número no dice nada. El coste propio es el que
 * señala el verdadero cuello de botella (es lo que SSMS muestra como
 * porcentaje bajo cada operador).
 */
export function selfCosts(costs: (number | null)[], edges: PlanTreeEdge[]): number[] {
  const childTotal = costs.map(() => 0);
  for (const { child, parent } of edges) childTotal[parent] += costs[child] ?? 0;
  return costs.map((c, i) => Math.max(0, (c ?? 0) - childTotal[i]));
}

/**
 * Grosor de la flecha en px según las filas que circulan por ella. Escala
 * logarítmica y acotada: entre una fila y un millón hay 6 órdenes de
 * magnitud, y el objetivo es que se distingan, no que una tape el lienzo.
 */
export function edgeWidth(rows: number | null): number {
  if (rows == null || rows <= 0) return 1.5;
  return Math.max(1.5, Math.min(14, 1.5 + Math.log10(rows + 1) * 2.6));
}

/** Etiqueta compacta de volumen para las aristas. */
export function formatRows(rows: number | null): string {
  if (rows == null) return "";
  if (rows >= 1_000_000) return `${(rows / 1_000_000).toFixed(1)} M filas`;
  if (rows >= 1_000) return `${(rows / 1_000).toFixed(1)} k filas`;
  return `${rows.toLocaleString()} filas`;
}
