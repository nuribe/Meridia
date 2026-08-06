/**
 * Comprobación del ruteo sobre disposiciones reales.
 *
 * No es un test unitario formal (el frontend no tiene runner): es un script que
 * mide las dos propiedades que importan y que se ejecuta con
 *
 *   npx esbuild src/edgeRouting.check.ts --bundle --platform=node --outfile=/tmp/t.cjs
 *   node /tmp/t.cjs
 *
 * 1. RECTITUD — cuántas aristas que podían salir rectas salen rectas.
 * 2. FLUIDEZ  — cuánto se mueve un anclaje cuando la tabla se mueve 1 px. Este
 *    era el defecto de la versión con rejilla: el anclaje se quedaba clavado y
 *    de golpe pegaba un salto de decenas de píxeles.
 */
import { routeConns, type Rect, type RoutedConn } from "./edgeRouting";
import { isVertical, type Anchor } from "./anchors";

const R = (x: number, y: number, w: number, h: number): Rect => ({ x, y, w, h });

/** Punto absoluto de un anclaje, para poder medir en píxeles. */
function point(r: Rect, a: Anchor): { x: number; y: number } {
  return {
    x: a.side === "left" ? r.x : a.side === "right" ? r.x + r.w : r.x + r.w * a.frac,
    y: a.side === "top" ? r.y : a.side === "bottom" ? r.y + r.h : r.y + r.h * a.frac,
  };
}

// Cajas aproximadas del diagrama de nómina de la captura del usuario.
const rects: Record<string, Rect> = {
  descuento: R(670, 15, 330, 365),
  contingencia: R(1420, 245, 345, 365),
  estado: R(218, 485, 345, 185),
  nomina: R(720, 480, 300, 585),
  control: R(253, 752, 262, 365),
  silta: R(1258, 770, 355, 320),
  empleado: R(68, 1295, 305, 220),
  abono: R(925, 1248, 310, 270),
  periodo: R(1608, 1248, 330, 270),
};

const conns: RoutedConn[] = [
  { id: "descuento->nomina", source: "descuento", target: "nomina" },
  { id: "contingencia->nomina", source: "contingencia", target: "nomina" },
  { id: "nomina->estado", source: "nomina", target: "estado" },
  { id: "nomina->control", source: "nomina", target: "control" },
  { id: "nomina->empleado", source: "nomina", target: "empleado" },
  { id: "abono->nomina", source: "abono", target: "nomina" },
  { id: "periodo->nomina", source: "periodo", target: "nomina" },
  { id: "silta->nomina", source: "silta", target: "nomina" },
  { id: "abono->periodo", source: "abono", target: "periodo" },
  { id: "silta->periodo", source: "silta", target: "periodo" },
  { id: "contingencia->silta", source: "contingencia", target: "silta" },
  { id: "nomina->nomina", source: "nomina", target: "nomina" },
];

// --- 1. Rectitud -------------------------------------------------------------

console.log("=== rectitud ===");
console.log("conn".padEnd(24), "lados".padEnd(16), "desvío");
const { anchors, usage } = routeConns(conns, rects);
let straight = 0;
let possible = 0;
for (const c of conns) {
  const e = anchors[c.id];
  const label = `${e.source.side}->${e.target.side}`;
  if (c.source === c.target) {
    console.log(c.id.padEnd(24), label.padEnd(16), "(lazo)");
    continue;
  }
  const a = rects[c.source];
  const b = rects[c.target];
  const sp = point(a, e.source);
  const tp = point(b, e.target);
  const band = isVertical(e.source.side)
    ? Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y)
    : Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
  const opposed =
    (e.source.side === "right" && e.target.side === "left") ||
    (e.source.side === "left" && e.target.side === "right") ||
    (e.source.side === "top" && e.target.side === "bottom") ||
    (e.source.side === "bottom" && e.target.side === "top");
  const skew = isVertical(e.source.side) ? Math.abs(sp.y - tp.y) : Math.abs(sp.x - tp.x);
  if (opposed && band >= 28) {
    possible++;
    if (skew <= 0.01) straight++;
    console.log(c.id.padEnd(24), label.padEnd(16), `${skew.toFixed(2)} px`);
  } else {
    console.log(c.id.padEnd(24), label.padEnd(16), "(sin franja común)");
  }
}
console.log(`rectas: ${straight}/${possible} de las que podían serlo`);

// Ningún par de anclajes del mismo nodo puede quedar encima de otro.
let tooClose = 0;
for (const [node, list] of Object.entries(usage)) {
  for (let i = 0; i < list.length; i++) {
    for (let j = i + 1; j < list.length; j++) {
      if (list[i].side !== list[j].side) continue;
      const pi = point(rects[node], list[i]);
      const pj = point(rects[node], list[j]);
      if (Math.hypot(pi.x - pj.x, pi.y - pj.y) < 12) tooClose++;
    }
  }
}
console.log(`anclajes demasiado juntos: ${tooClose}`);

// --- 2. Fluidez --------------------------------------------------------------
//
// Se barre una tabla píxel a píxel y se mide el mayor salto de un anclaje entre
// dos posiciones consecutivas. Con la rejilla anterior estos saltos eran del
// orden de 25-30 px; ahora deberían ser ≈ 1 px (lo que se mueve la tabla).

function maxJump(
  base: Record<string, Rect>,
  cs: RoutedConn[],
  moving: string,
  axis: "x" | "y",
  span: number
): { jump: number; sideFlips: number } {
  let prev: Record<string, { p: { x: number; y: number }; side: string }> | null = null;
  let jump = 0;
  let sideFlips = 0;
  for (let d = 0; d <= span; d++) {
    const rs = { ...base, [moving]: { ...base[moving], [axis]: base[moving][axis] + d } };
    const res = routeConns(cs, rs);
    const now: Record<string, { p: { x: number; y: number }; side: string }> = {};
    for (const c of cs) {
      const e = res.anchors[c.id];
      now[`${c.id}|s`] = { p: point(rs[c.source], e.source), side: e.source.side };
      now[`${c.id}|t`] = { p: point(rs[c.target], e.target), side: e.target.side };
    }
    if (prev) {
      for (const k of Object.keys(now)) {
        if (prev[k].side !== now[k].side) {
          sideFlips++;
          continue; // cambiar de lado sí es un salto, y es intencionado
        }
        const dx = now[k].p.x - prev[k].p.x;
        const dy = now[k].p.y - prev[k].p.y;
        jump = Math.max(jump, Math.hypot(dx, dy));
      }
    }
    prev = now;
  }
  return { jump, sideFlips };
}

console.log("\n=== fluidez (salto máximo del anclaje por cada píxel de arrastre) ===");
for (const [name, moving, axis, span] of [
  ["nómina en vertical", "nomina", "y", 400],
  ["nómina en horizontal", "nomina", "x", 400],
  ["contingencia en vertical", "contingencia", "y", 500],
] as const) {
  const { jump, sideFlips } = maxJump(rects, conns, moving, axis, span);
  console.log(
    `${name.padEnd(26)} salto máx ${jump.toFixed(2)} px  (cambios de lado: ${sideFlips})`
  );
}

// Hub saturado: el caso donde `separate` sí tiene que apartar a la fuerza.
const hubRects: Record<string, Rect> = { hub: R(600, 0, 230, 900) };
const hubConns: RoutedConn[] = [];
for (let i = 0; i < 12; i++) {
  hubRects[`n${i}`] = R(100, i * 70, 230, 130);
  hubConns.push({ id: `n${i}->hub`, source: `n${i}`, target: "hub" });
}
const hub = maxJump(hubRects, hubConns, "hub", "y", 300);
console.log(
  `${"hub con 12 relaciones".padEnd(26)} salto máx ${hub.jump.toFixed(2)} px  (cambios de lado: ${hub.sideFlips})`
);
