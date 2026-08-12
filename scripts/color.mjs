/**
 * Matemática de color compartida por las herramientas de tema.
 *
 * Todo lo que hay aquí es estándar y sin dependencias: sRGB -> luminancia
 * relativa (WCAG 2.1), sRGB -> CIELAB, ΔE CIEDE2000 y simulación de las tres
 * dicromacías con las matrices de Viénot-Brettel-Mollon.
 */

// ---------------------------------------------------------------- color ----

function parseHex(c) {
  let s = c.trim().replace("#", "");
  if (s.length === 3) s = [...s].map((x) => x + x).join("");
  return [0, 2, 4].map((i) => parseInt(s.slice(i, i + 2), 16));
}

function channel(v) {
  const x = v / 255;
  return x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
}

function luminance(hex) {
  const [r, g, b] = parseHex(hex);
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

function contrast(a, b) {
  const [x, y] = [luminance(a), luminance(b)].sort((m, n) => n - m);
  return (x + 0.05) / (y + 0.05);
}

// ------------------------------------------------- distinguibilidad (ΔE) ----
// La razón de contraste NO sirve para preguntar "¿se distinguen estos dos
// colores?": mide solo luminancia, y azul y violeta pueden ser igual de claros
// dando ~1.0:1 pese a ser obviamente distintos. Para eso se usa ΔE en un
// espacio perceptual. CIEDE2000 en vez de CIE76 porque este último exagera las
// diferencias justo en la zona azul-violeta, que es la de nuestros acentos.

function toXyz(hex) {
  const [r, g, b] = parseHex(hex).map(channel);
  return [
    r * 0.4124 + g * 0.3576 + b * 0.1805,
    r * 0.2126 + g * 0.7152 + b * 0.0722,
    r * 0.0193 + g * 0.1192 + b * 0.9505,
  ];
}

function toLab(hex) {
  const white = [0.95047, 1, 1.08883];
  const [x, y, z] = toXyz(hex).map((v, i) => {
    const t = v / white[i];
    return t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116;
  });
  return [116 * y - 16, 500 * (x - y), 200 * (y - z)];
}

function deltaE2000(hexA, hexB) {
  const [L1, a1, b1] = toLab(hexA);
  const [L2, a2, b2] = toLab(hexB);
  const rad = Math.PI / 180;
  const deg = 180 / Math.PI;

  const C1 = Math.hypot(a1, b1);
  const C2 = Math.hypot(a2, b2);
  const Cbar = (C1 + C2) / 2;
  const G = 0.5 * (1 - Math.sqrt(Cbar ** 7 / (Cbar ** 7 + 25 ** 7)));
  const ap1 = (1 + G) * a1;
  const ap2 = (1 + G) * a2;
  const Cp1 = Math.hypot(ap1, b1);
  const Cp2 = Math.hypot(ap2, b2);
  const hp = (b, ap) => {
    if (b === 0 && ap === 0) return 0;
    const h = Math.atan2(b, ap) * deg;
    return h >= 0 ? h : h + 360;
  };
  const hp1 = hp(b1, ap1);
  const hp2 = hp(b2, ap2);

  const dLp = L2 - L1;
  const dCp = Cp2 - Cp1;
  let dhp = 0;
  if (Cp1 * Cp2 !== 0) {
    dhp = hp2 - hp1;
    if (dhp > 180) dhp -= 360;
    else if (dhp < -180) dhp += 360;
  }
  const dHp = 2 * Math.sqrt(Cp1 * Cp2) * Math.sin((dhp * rad) / 2);

  const Lbar = (L1 + L2) / 2;
  const Cpbar = (Cp1 + Cp2) / 2;
  let hbar = hp1 + hp2;
  if (Cp1 * Cp2 !== 0) {
    if (Math.abs(hp1 - hp2) > 180) hbar += hp1 + hp2 < 360 ? 360 : -360;
    hbar /= 2;
  }

  const T =
    1 -
    0.17 * Math.cos((hbar - 30) * rad) +
    0.24 * Math.cos(2 * hbar * rad) +
    0.32 * Math.cos((3 * hbar + 6) * rad) -
    0.2 * Math.cos((4 * hbar - 63) * rad);
  const Sl = 1 + (0.015 * (Lbar - 50) ** 2) / Math.sqrt(20 + (Lbar - 50) ** 2);
  const Sc = 1 + 0.045 * Cpbar;
  const Sh = 1 + 0.015 * Cpbar * T;
  const Rt =
    -2 *
    Math.sqrt(Cpbar ** 7 / (Cpbar ** 7 + 25 ** 7)) *
    Math.sin(60 * Math.exp(-(((hbar - 275) / 25) ** 2)) * rad);

  return Math.sqrt(
    (dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2 + Rt * (dCp / Sc) * (dHp / Sh)
  );
}

// ------------------------------------------------------ daltonismo (CVD) ----
// Matrices de Viénot-Brettel-Mollon sobre RGB lineal. Los acentos codifican
// modo Explorador vs. Diagramas, así que conviene saber si un usuario con
// deuteranopia (≈6 % de los hombres) los sigue distinguiendo.

const CVD = {
  protanopia: [0.152286, 1.052583, -0.204868, 0.114503, 0.786281, 0.099216, -0.003882, -0.048116, 1.051998],
  deuteranopia: [0.367322, 0.860646, -0.227968, 0.280085, 0.672501, 0.047413, -0.01182, 0.04294, 0.968881],
  tritanopia: [1.255528, -0.076749, -0.178779, -0.078411, 0.930809, 0.147602, 0.004733, 0.691367, 0.303898],
};

function simulate(hex, kind) {
  const m = CVD[kind];
  const lin = parseHex(hex).map(channel);
  const out = [0, 1, 2].map((i) => {
    const v = m[i * 3] * lin[0] + m[i * 3 + 1] * lin[1] + m[i * 3 + 2] * lin[2];
    const c = Math.min(1, Math.max(0, v));
    const s = c <= 0.0031308 ? c * 12.92 : 1.055 * c ** (1 / 2.4) - 0.055;
    return Math.round(s * 255);
  });
  return "#" + out.map((v) => v.toString(16).padStart(2, "0")).join("");
}

export {
  parseHex,
  channel,
  luminance,
  contrast,
  toXyz,
  toLab,
  deltaE2000,
  CVD,
  simulate,
};
