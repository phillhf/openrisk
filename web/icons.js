// Kartensymbole, zur Laufzeit auf ein Canvas gezeichnet (keine Bilddateien nötig).
// Alle Formen liegen in einem 32×32-Raster mit Mittelpunkt (16, 16).

const INK = '#0b0f14';
const SCALE = 2; // Symbole werden doppelt so groß gezeichnet, wie sie erscheinen (scharf auf HiDPI)

export const SEVERITY_COLOR = ['#7d8590', '#4cc38a', '#f5d90a', '#ff8b3e', '#ff4d4f'];

/** Ereignisarten in Reihenfolge ihres Gewichts: bei gleicher Stufe steht die spätere für ein Bündel. */
export const KINDS = [
  ['general', 'Allgemein'], ['economy', 'Wirtschaft'], ['infrastructure', 'Infrastruktur'], ['cyber', 'Cyber'],
  ['health', 'Gesundheit'], ['unrest', 'Unruhen/Politik'], ['military', 'Militär'], ['terror', 'Terror/Anschlag'],
  ['violence', 'Gewalt gegen Zivilisten'], ['strike', 'Beschuss/Luftangriff'], ['conflict', 'Krieg/Konflikt'], ['disaster', 'Gefahr/Katastrophe'], ['fog', 'Nebel'], ['snow', 'Schnee'],
  ['ice', 'Frost/Glätte'], ['rain', 'Starkregen'], ['heat', 'Hitze'], ['drought', 'Dürre'], ['wind', 'Sturm/Böen'],
  ['thunderstorm', 'Gewitter'], ['weather', 'Unwetter'], ['storm', 'Sturm'], ['fire', 'Brand'],
  ['wildfire', 'Waldbrand'],
  ['flood', 'Überschwemmung'], ['volcano', 'Vulkan'], ['cyclone', 'Tropensturm'], ['earthquake', 'Erdbeben'], ['radiation', 'Strahlung'],
];
export const KIND_LABEL = Object.fromEntries(KINDS);
const KIND_INDEX = Object.fromEntries(KINDS.map(([kind], i) => [kind, i]));
const ALIAS = { landslide: 'disaster', dust: 'disaster' };

/** Stufe und Art in einer Zahl: Bündel auf der Karte zeigen so ihr schwerstes Ereignis (max). */
export const rankOf = (ev) => ev.severity * 100 + (KIND_INDEX[ALIAS[ev.kind] ?? ev.kind] ?? KIND_INDEX.general);
export const kindOfRank = (rank) => KINDS[rank % 100]?.[0] ?? 'general';

const line = (c, ...pts) => {
  c.beginPath();
  pts.forEach(([x, y], i) => (i ? c.lineTo(x, y) : c.moveTo(x, y)));
  c.stroke();
};
const shape = (c, ...pts) => {
  c.beginPath();
  pts.forEach(([x, y], i) => (i ? c.lineTo(x, y) : c.moveTo(x, y)));
  c.closePath();
  c.fill();
};
const dot = (c, x, y, r) => { c.beginPath(); c.arc(x, y, r, 0, Math.PI * 2); c.fill(); };
const star = (c, points, outer, inner) => shape(c, ...Array.from({ length: points * 2 }, (_, i) => {
  const angle = (Math.PI * i) / points - Math.PI / 2;
  const r = i % 2 ? inner : outer;
  return [16 + r * Math.cos(angle), 16 + r * Math.sin(angle)];
}));
const bars = (c, ...rows) => rows.forEach(([y, x1, x2]) => line(c, [x1, y], [x2, y]));

const GLYPH = {
  general: (c) => dot(c, 16, 16, 3),
  economy: (c) => { line(c, [8.5, 11], [13, 16.5], [16.5, 13.5], [23, 20.5]); line(c, [18.5, 20.5], [23, 20.5], [23, 16]); },
  infrastructure: (c) => { line(c, [10.5, 24], [16, 8], [21.5, 24]); bars(c, [12.5, 9.5, 22.5], [18, 12.5, 19.5]); },
  cyber: (c) => { c.strokeRect(9, 9.5, 14, 9.5); line(c, [16, 19], [16, 22.5]); line(c, [12, 23], [20, 23]); },
  health: (c) => { c.lineWidth = 4.2; line(c, [16, 9.5], [16, 22.5]); line(c, [9.5, 16], [22.5, 16]); },
  unrest: (c) => { line(c, [10.5, 8.5], [10.5, 24]); shape(c, [10.5, 9], [23, 12.5], [10.5, 16.5]); },
  military: (c) => star(c, 5, 8, 3.4),
  terror: (c) => star(c, 8, 8.5, 4),
  strike: (c) => star(c, 8, 8.5, 4),
  violence: (c) => { dot(c, 16, 11, 3); line(c, [16, 14.5], [16, 20]); line(c, [10.5, 16.5], [21.5, 16.5]); line(c, [16, 20], [12, 24.5]); line(c, [16, 20], [20, 24.5]); },
  conflict: (c) => {
    line(c, [9.5, 9.5], [22.5, 22.5]); line(c, [22.5, 9.5], [9.5, 22.5]);
    line(c, [17, 21.5], [21.5, 17]); line(c, [10.5, 17], [15, 21.5]);
  },
  disaster: (c) => {
    c.beginPath(); c.moveTo(16, 8); c.lineTo(24.5, 22.5); c.lineTo(7.5, 22.5); c.closePath(); c.stroke();
    line(c, [16, 13], [16, 17.5]); dot(c, 16, 20, 1.2);
  },
  fog: (c) => bars(c, [11, 9, 23], [16, 9, 23], [21, 9, 23]),
  snow: (c) => [0, 60, 120].forEach((deg) => {
    const a = (deg * Math.PI) / 180;
    line(c, [16 - 8 * Math.cos(a), 16 - 8 * Math.sin(a)], [16 + 8 * Math.cos(a), 16 + 8 * Math.sin(a)]);
  }),
  ice: (c) => { GLYPH.snow(c); dot(c, 16, 16, 2.4); },
  rain: (c) => [[12, 9], [17, 9], [22, 9], [14.5, 16.5], [19.5, 16.5]].forEach(([x, y]) => line(c, [x, y], [x - 2.2, y + 5.5])),
  heat: (c) => { c.lineWidth = 3; line(c, [16, 8.5], [16, 18]); dot(c, 16, 20.5, 3.6); },
  drought: (c) => {
    dot(c, 16, 16, 3.6);
    for (let i = 0; i < 8; i += 1) {
      const a = (Math.PI * i) / 4;
      line(c, [16 + 5.8 * Math.cos(a), 16 + 5.8 * Math.sin(a)], [16 + 8.6 * Math.cos(a), 16 + 8.6 * Math.sin(a)]);
    }
  },
  wind: (c) => bars(c, [11, 8, 20], [16, 8, 24], [21, 8, 17]),
  thunderstorm: (c) => shape(c, [18.5, 7], [10.5, 17.5], [15.5, 17.5], [13.5, 25], [21.5, 14], [16.5, 14]),
  weather: (c) => GLYPH.thunderstorm(c),
  storm: (c) => GLYPH.cyclone(c),
  fire: (c) => GLYPH.wildfire(c),
  wildfire: (c) => {
    c.beginPath(); c.moveTo(16, 7);
    c.bezierCurveTo(20, 12, 24, 15.5, 22, 20.5); c.bezierCurveTo(20.5, 24.5, 11.5, 24.5, 10, 20.5);
    c.bezierCurveTo(8.5, 16.5, 12.5, 14.5, 13, 11); c.bezierCurveTo(14.5, 12.5, 15.5, 11.5, 16, 7);
    c.fill();
  },
  flood: (c) => [11, 16, 21].forEach((y) => {
    c.beginPath(); c.moveTo(8, y);
    c.quadraticCurveTo(10, y - 3, 12, y); c.quadraticCurveTo(14, y + 3, 16, y);
    c.quadraticCurveTo(18, y - 3, 20, y); c.quadraticCurveTo(22, y + 3, 24, y);
    c.stroke();
  }),
  volcano: (c) => { shape(c, [7.5, 23.5], [13, 13.5], [19, 13.5], [24.5, 23.5]); line(c, [14, 11], [12.5, 7.5]); line(c, [18, 11], [19.5, 7.5]); },
  cyclone: (c) => {
    dot(c, 16, 16, 2.3);
    c.beginPath(); c.arc(16, 16, 6.8, -Math.PI / 2, Math.PI / 5); c.stroke();
    c.beginPath(); c.arc(16, 16, 6.8, Math.PI / 2, Math.PI * 1.2); c.stroke();
  },
  radiation: (c) => {  // Strahlenwarnzeichen: drei Flügel um einen Punkt
    for (let i = 0; i < 3; i += 1) {
      const mid = -Math.PI / 2 + (i * 2 * Math.PI) / 3;
      c.beginPath();
      c.arc(16, 16, 8.5, mid - Math.PI / 6, mid + Math.PI / 6);
      c.arc(16, 16, 3.4, mid + Math.PI / 6, mid - Math.PI / 6, true);
      c.closePath();
      c.fill();
    }
    dot(c, 16, 16, 1.9);
  },
  earthquake: (c) => line(c, [7.5, 16], [10.5, 16], [13, 9.5], [16, 22.5], [19, 11.5], [21, 16], [24.5, 16]),
};

function canvas(size) {
  const el = Object.assign(document.createElement('canvas'), { width: size * SCALE, height: size * SCALE });
  const c = el.getContext('2d');
  c.scale((size * SCALE) / 32, (size * SCALE) / 32);
  c.lineCap = 'round';
  c.lineJoin = 'round';
  return [el, c];
}

/** Ereignissymbol: Scheibe in der Farbe der Stufe, Rand in der Farbe der Ebene, darin das Piktogramm der Art. */
export function eventIcon(kind, severity, ring, size = 28) {
  const [el, c] = canvas(size);
  c.beginPath();
  c.arc(16, 16, 13, 0, Math.PI * 2);
  c.fillStyle = SEVERITY_COLOR[severity] ?? SEVERITY_COLOR[0];
  c.fill();
  c.lineWidth = 2.4;
  c.strokeStyle = ring;
  c.stroke();
  c.fillStyle = c.strokeStyle = INK;
  c.lineWidth = 2.2;
  (GLYPH[ALIAS[kind] ?? kind] ?? GLYPH.general)(c);
  return el;
}

const PLANE = [[16, 2], [18, 9], [18, 13], [30, 20], [30, 23], [18, 20], [17.5, 26], [22, 29], [22, 31],
  [16, 29.5], [10, 31], [10, 29], [14.5, 26], [14, 20], [2, 23], [2, 20], [14, 13], [14, 9]];
const SHIP = [[16, 3], [22, 12], [22, 28], [10, 28], [10, 12]];
const DIAMOND = [[16, 4], [28, 16], [16, 28], [4, 16]];

function outline(points, fill, size) {
  const [el, c] = canvas(size);
  c.beginPath();
  points.forEach(([x, y], i) => (i ? c.lineTo(x, y) : c.moveTo(x, y)));
  c.closePath();
  c.fillStyle = fill;
  c.strokeStyle = INK;
  c.lineWidth = 1.8;
  c.fill();
  c.stroke();
  return el;
}

/** Flugzeug und Schiff zeigen mit der Spitze nach Norden; gedreht wird auf der Karte. */
export const planeIcon = (fill) => outline(PLANE, fill, 32);
export const shipIcon = (fill) => outline(SHIP, fill, 32);

export const BASES = {
  base: ['Militärbasis', '#a3e635'], airfield: ['Militärflugplatz', '#7dd3fc'],
  naval_base: ['Marinestützpunkt', '#818cf8'], barracks: ['Kaserne', '#a8a29e'],
};
export const baseIcon = (type) => outline(DIAMOND, (BASES[type] ?? BASES.base)[1], 18);

/** Canvas in das Format umwandeln, das die Karte als Bild annimmt. */
export const imageData = (el) => el.getContext('2d').getImageData(0, 0, el.width, el.height);
export const PIXEL_RATIO = SCALE;
