/**
 * Ruteo de aristas: por qué lado y en qué punto exacto del contorno entra y sale
 * cada relación.
 *
 * Dos objetivos, y el segundo es tan importante como el primero:
 *
 * A. Que la arista salga RECTA siempre que la geometría lo permita.
 * B. Que al arrastrar una tabla el punto de enlace se DESLICE, no salte.
 *
 * Por eso aquí no hay rejilla de slots: todas las posiciones son coordenadas
 * reales en píxeles y todas las operaciones (elegir el punto ideal, separar los
 * que chocan, realinear) son funciones continuas de la posición de los nodos.
 * Mover una tabla un píxel mueve los anclajes como mucho un píxel.
 *
 * Lo único discreto que queda es la elección de lado: al rodear una tabla con
 * otra, en algún momento la arista tiene que cambiarse de lado. Eso es inevitable
 * y además es lo que el usuario espera ver.
 *
 * Está en su propio módulo para poder razonarlo y medirlo sin arrastrar el
 * lienzo entero (ver `edgeRouting.check.ts`).
 */
import { isVertical, type Anchor, type AnchorUsage, type Side } from "./anchors";

/** Caja del nodo en coordenadas del lienzo. */
export type Rect = { x: number; y: number; w: number; h: number };

/** Lo mínimo que el ruteo necesita saber de una conexión. */
export interface RoutedConn {
  id: string;
  source: string;
  target: string;
}

/** Los dos extremos ya resueltos de una conexión. */
export interface RoutedEnds {
  source: Anchor;
  target: Anchor;
}

export const FALLBACK_RECT: Rect = { x: 0, y: 0, w: 230, h: 150 };

const midX = (r: Rect) => r.x + r.w / 2;
const midY = (r: Rect) => r.y + r.h / 2;

/** Franja común mínima (px) para dar por buena una recta entre dos nodos. */
const MIN_BAND = 28;
/** Separación mínima entre dos anclajes del mismo lado. */
const MIN_GAP = 13;
/** Separación residual justo en el cruce de dos aristas (ver `separate`). */
const SWAP_FLOOR = 3;
/** Distancia mínima a las esquinas: un anclaje en la esquina se lee fatal. */
const CORNER_MARGIN = 12;

/** Recorrido útil de un lado, en coordenadas absolutas del eje que lo recorre. */
function sideRange(r: Rect, side: Side): [number, number] {
  const [a, b] = isVertical(side) ? [r.y, r.y + r.h] : [r.x, r.x + r.w];
  const margin = Math.min(CORNER_MARGIN, (b - a) / 4);
  return [a + margin, b - margin];
}

const clamp = (v: number, [lo, hi]: [number, number]) => Math.min(hi, Math.max(lo, v));

/**
 * Franja en la que los dos nodos "se miran de frente" sobre el eje del lado:
 * por ahí, y solo por ahí, cabe una línea recta.
 */
function overlapBand(a: Rect, b: Rect, side: Side): [number, number] {
  const [a0, a1] = isVertical(side) ? [a.y, a.y + a.h] : [a.x, a.x + a.w];
  const [b0, b1] = isVertical(side) ? [b.y, b.y + b.h] : [b.x, b.x + b.w];
  return [Math.max(a0, b0), Math.min(a1, b1)];
}

/**
 * Elige por qué lado sale y entra la arista.
 *
 * Antes solo se miraba la dirección entre centros, así que dos tablas que se
 * miran de frente pero con centros desplazados acababan enlazadas por lados que
 * obligaban al trazado a dar un rodeo. Ahora manda la geometría de las cajas: si
 * hay hueco en X y las alturas se solapan, van derecha↔izquierda (y ahí cabe una
 * recta horizontal); si hay hueco en Y y los anchos se solapan, van abajo↔arriba.
 * Solo cuando no hay ninguna franja común se recurre a los centros.
 */
function pickSides(a: Rect, b: Rect): [Side, Side] {
  const dx = midX(b) - midX(a);
  const dy = midY(b) - midY(a);
  const gapX = Math.max(b.x - (a.x + a.w), a.x - (b.x + b.w));
  const gapY = Math.max(b.y - (a.y + a.h), a.y - (b.y + b.h));
  const bandY = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
  const bandX = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);

  // Las dos condiciones son excluyentes: si hay hueco en X hay solape en Y.
  let horizontal: boolean;
  if (gapX > 0 && bandY >= MIN_BAND) horizontal = true;
  else if (gapY > 0 && bandX >= MIN_BAND) horizontal = false;
  else horizontal = Math.abs(dy) <= Math.abs(dx) * 1.15;

  if (horizontal) return dx >= 0 ? ["right", "left"] : ["left", "right"];
  return dy >= 0 ? ["bottom", "top"] : ["top", "bottom"];
}

/**
 * Punto ideal de los dos extremos, en coordenadas absolutas.
 *
 * Los dos piden EL MISMO punto: el centro de la franja común. Si hay franja, ese
 * punto está dentro de los dos nodos y la arista sale recta exacta. Si no la hay
 * (tablas en diagonal), `overlapBand` devuelve un intervalo invertido cuyo centro
 * cae entre los dos bordes enfrentados, y el `clamp` lo lleva en cada nodo a la
 * esquina que mira hacia el otro — que es justo donde debe engancharse.
 *
 * Que sea la misma fórmula en los dos casos no es elegancia gratuita: cualquier
 * `if` aquí sería un umbral, y al arrastrar una tabla cruzándolo el anclaje
 * pegaría un tirón. Así es continuo por construcción.
 */
function idealPositions(a: Rect, aSide: Side, b: Rect, bSide: Side): [number, number] {
  const [lo, hi] = overlapBand(a, b, aSide);
  const mid = (lo + hi) / 2;
  return [clamp(mid, sideRange(a, aSide)), clamp(mid, sideRange(b, bSide))];
}

/**
 * Separa los anclajes de un mismo lado dejando al menos `MIN_GAP` entre ellos,
 * moviendo cada uno lo mínimo posible respecto a donde quería estar.
 *
 * Es el reemplazo del antiguo reparto en abanico, y la diferencia importa por
 * partida doble: no desplaza a quien no lo necesita (así las rectas siguen
 * rectas) y es una función continua de `want` (así no hay saltos al arrastrar).
 *
 * `want` debe venir ordenado; la salida es monótona, de modo que las aristas de
 * un mismo lado nunca se cruzan entre sí.
 */
export function separate(want: number[], [lo, hi]: [number, number]): number[] {
  const n = want.length;
  if (n === 0) return [];
  if (n === 1) return [clamp(want[0], [lo, hi])];

  // Si no caben con la separación nominal, se reparten por igual: es el único
  // caso en el que alguien acaba lejos de su sitio, y no hay alternativa.
  const nominal = Math.min(MIN_GAP, (hi - lo) / (n - 1));

  // Separación exigida a cada par vecino. Es la nominal mientras están lejos,
  // pero se relaja según se acercan sus posiciones ideales. Ese detalle es lo
  // que evita el tirón cuando dos aristas se cruzan: en vez de intercambiar de
  // golpe sus sitios (un salto de `nominal`), se juntan, se cruzan y se vuelven
  // a separar. `SWAP_FLOOR` deja un mínimo para que los puntos nunca lleguen a
  // fundirse del todo.
  const gaps = Array.from({ length: n - 1 }, (_, i) => {
    const d = Math.abs(want[i + 1] - want[i]);
    return d >= nominal ? nominal : Math.max(SWAP_FLOOR, Math.sqrt(d * nominal));
  });

  const out = want.map((w) => clamp(w, [lo, hi]));
  for (let i = 1; i < n; i++) out[i] = Math.max(out[i], out[i - 1] + gaps[i - 1]);
  if (out[n - 1] > hi) {
    out[n - 1] = hi;
    for (let i = n - 2; i >= 0; i--) out[i] = Math.min(out[i], out[i + 1] - gaps[i]);
  }
  return out;
}

interface EndPoint {
  connId: string;
  role: "s" | "t";
  side: Side;
  rect: Rect;
  /** Dónde querría estar (px absolutos) antes de resolver choques. */
  want: number;
  /** Dónde acaba (px absolutos). */
  pos: number;
  /** Los lazos reflexivos tienen sitio fijo y no se realinean. */
  fixed: boolean;
}

/** Fracción 0..1 del lado que corresponde a una coordenada absoluta. */
function toFrac(r: Rect, side: Side, pos: number): number {
  const [origin, size] = isVertical(side) ? [r.y, r.h] : [r.x, r.w];
  return size > 0 ? (pos - origin) / size : 0.5;
}

/**
 * Resuelve los dos extremos de cada conexión.
 *
 * 1. `pickSides` elige los lados con la geometría de las cajas, no solo con los
 *    centros, para que la recta sea posible siempre que quepa.
 * 2. `idealPositions` da a los dos extremos la misma coordenada cuando hay
 *    franja común → recta exacta, sin redondeos.
 * 3. `separate` aparta solo lo justo a los que se pisan dentro de un mismo lado.
 * 4. Una segunda pasada realinea cada extremo con el punto donde quedó de verdad
 *    el otro: si a un extremo lo apartó un vecino, el de enfrente lo sigue y la
 *    arista vuelve a ser recta.
 *
 * Devuelve también los anclajes por nodo, para que TableNode pinte sus puntos.
 */
export function routeConns(conns: RoutedConn[], rects: Record<string, Rect>) {
  const ends: Record<string, { source: EndPoint; target: EndPoint }> = {};
  const buckets = new Map<string, EndPoint[]>();

  const put = (node: string, e: EndPoint) => {
    const k = `${node}|${e.side}`;
    const arr = buckets.get(k);
    if (arr) arr.push(e);
    else buckets.set(k, [e]);
    return e;
  };

  for (const c of conns) {
    const a = rects[c.source] ?? FALLBACK_RECT;
    const b = rects[c.target] ?? FALLBACK_RECT;
    const selfRef = c.source === c.target;
    let sSide: Side;
    let tSide: Side;
    let sWant: number;
    let tWant: number;
    if (selfRef) {
      // Lazo visible: sale por la derecha y vuelve por arriba, los dos extremos
      // cerca de la esquina superior derecha, para que quede compacto y fuera
      // del nodo en vez de escondido pegado al borde.
      sSide = "right";
      tSide = "top";
      sWant = sideRange(a, "right")[0];
      tWant = sideRange(a, "top")[1];
    } else {
      [sSide, tSide] = pickSides(a, b);
      [sWant, tWant] = idealPositions(a, sSide, b, tSide);
    }
    ends[c.id] = {
      source: put(c.source, {
        connId: c.id, role: "s", side: sSide, rect: a, want: sWant, pos: sWant, fixed: selfRef,
      }),
      target: put(c.target, {
        connId: c.id, role: "t", side: tSide, rect: b, want: tWant, pos: tWant, fixed: selfRef,
      }),
    };
  }

  /** Aplica `separate` lado a lado. */
  const spread = () => {
    for (const arr of buckets.values()) {
      arr.sort((x, y) => x.want - y.want || x.connId.localeCompare(y.connId));
      const range = sideRange(arr[0].rect, arr[0].side);
      const pos = separate(arr.map((e) => e.want), range);
      arr.forEach((e, i) => (e.pos = pos[i]));
    }
  };

  spread();
  // Realineado: cada extremo apunta ahora al punto real del otro. Es estable —
  // si ya estaban alineados, `want` no cambia y esta pasada no mueve nada.
  for (const { source, target } of Object.values(ends)) {
    if (source.fixed || target.fixed) continue;
    const sPos = source.pos;
    const tPos = target.pos;
    source.want = clamp(tPos, sideRange(source.rect, source.side));
    target.want = clamp(sPos, sideRange(target.rect, target.side));
  }
  spread();

  const anchorOf = (e: EndPoint): Anchor => ({ side: e.side, frac: toFrac(e.rect, e.side, e.pos) });
  const anchors: Record<string, RoutedEnds> = {};
  for (const [connId, { source, target }] of Object.entries(ends)) {
    anchors[connId] = { source: anchorOf(source), target: anchorOf(target) };
  }
  const usage: AnchorUsage = {};
  for (const [k, arr] of buckets) {
    const node = k.slice(0, k.lastIndexOf("|"));
    const list = usage[node] ?? (usage[node] = []);
    for (const e of arr) list.push(anchorOf(e));
  }
  return { anchors, usage };
}
