import { MapLibreMap, Marker, Popup, NavigationControl, ScaleControl, addProtocol } from './vendor/maplibre-gl/maplibre-gl.mjs';
import { Protocol } from './vendor/pmtiles/index.js';
import { layers as basemapLayers, namedFlavor } from './vendor/basemaps/index.js';
import { WeatherFilm } from './film.js';
import { BASES, KIND_LABEL, PIXEL_RATIO, SEVERITY_COLOR, baseIcon, eventIcon, imageData, kindOfRank, planeIcon,
  rankOf, shipIcon } from './icons.js';

// ---------------------------------------------------------------- Konstanten
const SEVERITY = ['Info', 'Gering', 'Mittel', 'Hoch', 'Kritisch'];

const LAYERS = {
  risk: { label: 'Länder-Risiko', color: '#ff4d4f', ids: ['risk-fill'] },
  terrain: { label: 'Gelände (Schummerung, Höhenlinien)', color: '#b08850', ids: ['hillshade', 'contours', 'contour-labels'] },
  news: { label: 'Nachrichten', color: '#5aa9ff', ids: ['news'] },
  conflict: { label: 'Konflikte (GDELT)', color: '#ff5d8f', ids: ['conflict'] },
  disaster: { label: 'Katastrophen & Warnungen', color: '#ff8b3e', ids: ['nina-areas', 'disaster'] },
  weather: { label: 'Wetter & Warnungen', color: '#3dd6d0', ids: ['dwd-warn', 'weather-alert', 'weather-temp'] },
  radar: { label: 'Regenradar mit Vorhersage (DWD)', color: '#38bdf8', ids: [] },
  clouds: { label: 'Wolken (Satellit)', color: '#e2e8f0', ids: [] },
  wind: { label: 'Windströmung', color: '#a7f3d0', ids: [] },
  stations: { label: 'Wetterstationen (DWD)', color: '#8bd3ff', ids: ['stations-dot', 'stations-temp'] },
  radiation: { label: 'Strahlung (Europa)', color: '#4cc38a', ids: ['radiation-dot', 'radiation-value'] },
  aircraft: { label: 'Militärflugzeuge', color: '#ffe066', ids: ['aircraft'] },
  ship: { label: 'Militärschiffe', color: '#c4a7ff', ids: ['ships'] },
  bases: { label: 'Militärbasen', color: '#a3e635', ids: ['bases'] },
  barracks: { label: 'Kasernen', color: '#a8a29e', ids: ['bases-barracks'] },
  pipelines: { label: 'Öl-Pipelines', color: '#e879f9', ids: ['pipelines'] },
  nuclear: { label: 'Kernkraftwerke (weltweit)', color: '#facc15', ids: ['nuclear', 'nuclear-label'] },
  radon: { label: 'Radon (BfS)', color: '#f5d90a', ids: ['radon-fill', 'radon-areas-line', 'radon-areas-hit'] },
  masts: { label: 'Mobilfunkmasten (DE)', color: '#f472b6', ids: ['masts', 'masts-label'] },
  bunkers: { label: 'Bunker (DE)', color: '#cbd5e1', ids: ['bunkers', 'bunkers-label'] },
  water: { label: 'Quellen & Wasserstellen (DE)', color: '#60a5fa', ids: ['springs', 'springs-label'] }, // nicht „water“: so heißen die Gewässer der Grundkarte
  cables: { label: 'Seekabel', color: '#22d3ee', ids: ['cables', 'cables-planned', 'landings'] },
  ixp: { label: 'Internetknoten (IXP)', color: '#facc15', ids: ['ixp', 'ixp-label'] },
  datacenters: { label: 'Rechenzentren', color: '#818cf8', ids: ['datacenters', 'datacenters-label'] },
  telecom: { label: 'Fernmelde-/Glasfaserleitungen (DE)', color: '#f97316', ids: ['telecom'] },
};
// Ebenen im Menü nach Themen; jede Gruppe lässt sich einklappen
const GROUPS = [
  ['lage', 'Lage & Konflikte', ['risk', 'news', 'conflict', 'disaster']],
  ['wetter', 'Wetter', ['weather', 'radar', 'clouds', 'wind', 'stations']],
  ['strahlung', 'Strahlung & Kernkraft', ['radiation', 'radon', 'nuclear']],
  ['militaer', 'Militär & Schutz', ['aircraft', 'ship', 'bases', 'barracks', 'bunkers']],
  ['infra', 'Infrastruktur', ['pipelines', 'cables', 'ixp', 'datacenters', 'telecom', 'masts', 'water']],
  ['karte', 'Karte & Gelände', ['terrain']],
];
const NUCLEAR = {
  operational: ['in Betrieb', '#facc15'], suspended: ['vorübergehend abgeschaltet', '#60a5fa'],
  construction: ['im Bau', '#fb923c'], planned: ['geplant', '#c084fc'], shutdown: ['abgeschaltet', '#64748b'],
  cancelled: ['nie in Betrieb gegangen', '#475569'], unknown: ['unbekannt', '#475569'],
};
const REACTOR_TYPES = { PWR: 'Druckwasserreaktor', BWR: 'Siedewasserreaktor', PHWR: 'Schwerwasserreaktor',
  LWGR: 'graphitmoderiert (RBMK)', GCR: 'gasgekühlt', AGR: 'fortgeschritten gasgekühlt', FBR: 'Schneller Brüter',
  HTGR: 'Hochtemperaturreaktor', HWGCR: 'schwerwassermoderiert, gasgekühlt', SGHWR: 'Dampferzeugender Schwerwasserreaktor' };
const MAST_COLORS = { mobile: '#f472b6', other: '#a78bfa' };
const BUNKER_COLORS = { intact: '#cbd5e1', lost: '#64748b' };
const WATER_COLORS = { spring: '#60a5fa', drinking: '#34d399', well: '#a78bfa', other: '#94a3b8' };
const WATER_COLOR = ['case', ['==', ['get', 'drinking'], true], WATER_COLORS.drinking,
  ['==', ['get', 'kind'], 'spring'], WATER_COLORS.spring, ['==', ['get', 'kind'], 'water_well'], WATER_COLORS.well,
  WATER_COLORS.other];
const ACCESS = { no: 'kein Zugang', private: 'privat', yes: 'öffentlich', permissive: 'geduldet',
  military: 'nur Militär', customers: 'nur Besucher' };
// Reihenfolge = Priorität beim Anklicken (oberste Kartenebene zuerst)
const CLICKABLE = ['track-dots', 'aircraft', 'ships', 'news', 'conflict', 'disaster', 'weather-alert', 'radiation-dot',
  'radiation-value', 'stations-temp', 'stations-dot',
  'weather-temp', 'ixp', 'landings', 'nuclear', 'masts', 'bunkers', 'springs', 'datacenters', 'bases', 'bases-barracks', 'cables',
  'cables-planned', 'telecom', 'pipelines', 'nina-areas', 'dwd-warn', 'radon-fill', 'country-hit'];
// Ebenen aus einmalig geladenen Dateien; sie werden erst beim Einschalten in die Karte geholt
// Ebene -> [Datei in /geo, Werkzeug zum Erzeugen]; Basen und Kasernen teilen sich eine Datei
const STATIC_FILES = { pipelines: ['pipelines', 'tools/fetch_pipelines.py'], bases: ['bases', 'tools/fetch_bases.py'],
  barracks: ['bases', 'tools/fetch_bases.py'], radon: ['radon', 'wird beim nächsten wöchentlichen Abruf geladen'],
  masts: ['masts', 'wird geladen (beim ersten Start einige Minuten)', 'osm_de'],
  bunkers: ['bunkers', 'wird geladen (beim ersten Start einige Minuten)', 'osm_de'],
  telecom: ['telecom', 'wird geladen (beim ersten Start einige Minuten)', 'osm_de'],
  water: ['water', 'wird geladen (beim ersten Start einige Minuten)', 'osm_de'],
  nuclear: ['nuclear', 'wird geladen (wenige Sekunden)', 'nuclear'],
  cables: ['cables', 'wird geladen (beim ersten Start etwa eine Minute)', 'internet'],
  ixp: ['ixp', 'wird geladen (beim ersten Start etwa eine Minute)', 'internet'],
  datacenters: ['datacenters', 'wird geladen (beim ersten Start etwa eine Minute)', 'internet'] };
const ATTRIBUTION = { cables: '© TeleGeography Submarine Cable Map (CC BY-NC-SA 3.0)', ixp: 'PeeringDB',
  datacenters: 'PeeringDB', nuclear: 'Kernkraftwerke: GeoNuclearData (ODbL), IAEA PRIS, WNA' };
const DWD_LEVEL_COLOR = ['match', ['coalesce', ['feature-state', 'level'], 0],
  1, '#f5d90a', 2, '#ff8b3e', 3, '#ff4d4f', 4, '#c026d3', '#000000'];
const TEMP_COLOR = ['interpolate', ['linear'], ['get', 'temp'], -20, '#7aa8ff', 0, '#cfe3ff', 15, '#e6f2e6',
  25, '#ffd27a', 35, '#ff8b3e', 45, '#ff4d4f'];
const GERMANY = { center: [10.4, 51.2], zoom: 5 };
const RADIATION_STATUS = ['kein Messwert', 'im üblichen Bereich', 'erhöht – meist Regen, beobachten',
  'deutlich erhöht – natürlich kaum zu erklären'];
const RADIATION_COLORS = ['#7d8590', '#4cc38a', '#f5d90a', '#ff4d4f'];
const RADON_STATUS = ['keine Angabe', 'unter 100 Bq/m³', '100–300 Bq/m³', 'über 300 Bq/m³'];
const RADIATION_COLOR = ['match', ['get', 'status'], 1, RADIATION_COLORS[1], 2, RADIATION_COLORS[2],
  3, RADIATION_COLORS[3], RADIATION_COLORS[0]];

const SOURCE_LABEL = {
  news: 'Nachrichten (RSS)', usgs: 'Erdbeben (USGS)', gdacs: 'Katastrophen (GDACS)', eonet: 'Naturereignisse (NASA)',
  weather: 'Wetter (Open-Meteo)', dwd_warnings: 'Wetterwarnungen (DWD)',
  nina: 'Warnungen (NINA, KATWARN)', gdelt: 'Konflikte (GDELT)', dwd_stations: 'Wetterstationen (DWD)',
  radiation: 'Strahlung Deutschland (BfS)', eurdep: 'Strahlung Europa (EURDEP)', radon: 'Radon (BfS)', places: 'Ortsverzeichnis (GeoNames)', countries: 'Länderdaten (Weltbank)', cityinfo: 'Stadtinfos (Destatis, Wikidata)', osm_de: 'Masten, Bunker, Leitungen, Wasser (OSM)',
  internet: 'Seekabel & Internetknoten (TeleGeography, PeeringDB)', nuclear: 'Kernkraftwerke (GeoNuclearData)',
  weathermaps: 'Radar & Satellit (DWD, EUMETSAT)', wind: 'Wind (Open-Meteo)',
  aircraft: 'Militärflugzeuge (ADS-B)', ships: 'Militärschiffe (AIS)',
};
const WMO = {
  0: 'klar', 1: 'überwiegend klar', 2: 'teils bewölkt', 3: 'bedeckt', 45: 'Nebel', 48: 'Reifnebel',
  51: 'leichter Niesel', 53: 'Niesel', 55: 'starker Niesel', 56: 'gefrierender Niesel', 57: 'gefrierender Niesel',
  61: 'leichter Regen', 63: 'Regen', 65: 'starker Regen', 66: 'gefrierender Regen', 67: 'gefrierender Regen',
  71: 'leichter Schneefall', 73: 'Schneefall', 75: 'starker Schneefall', 77: 'Schneegriesel',
  80: 'Regenschauer', 81: 'Regenschauer', 82: 'heftige Regenschauer', 85: 'Schneeschauer', 86: 'Schneeschauer',
  95: 'Gewitter', 96: 'Gewitter mit Hagel', 99: 'Gewitter mit Hagel',
};

// -------------------------------------------------------------------- Zustand
const LAYOUT_VERSION = 2; // ab hier gilt: alle Ebenen sind zunächst aus
let saved = {};
try { saved = JSON.parse(localStorage.getItem('openrisk') || '{}'); } catch { /* Speicher nicht verfügbar */ }
const state = {
  hours: saved.hours ?? 24,
  mapStyle: saved.mapStyle === 'light' ? 'light' : 'dark', // dunkel oder hell mit Gelände
  openGroups: saved.openGroups ?? null, // aufgeklappte Ebenen-Gruppen; null = Gruppen mit eingeschalteten Ebenen
  minSeverity: saved.minSeverity ?? 0,
  // Nur was eingeschaltet ist, wird geladen und gezeichnet - das spart auf dem Pi am meisten
  visible: { ...Object.fromEntries(Object.keys(LAYERS).map((name) => [name, false])),
    ...(saved.layout === LAYOUT_VERSION ? saved.visible : {}) },
  search: '',
  at: null, // Rückblick: betrachteter Zeitpunkt (Unix-Sekunden); null = live
  searchResults: null, // Treffer der Suche im gesamten Verlauf
  events: [], conflicts: [], vehicles: [], weather: [], stations: [], radiation: [], risk: [], status: null,
  byId: new Map(),
};
const persist = () => {
  try {
    localStorage.setItem('openrisk', JSON.stringify(
      { layout: LAYOUT_VERSION, hours: state.hours, minSeverity: state.minSeverity, visible: state.visible,
        mapStyle: state.mapStyle, openGroups: state.openGroups }));
  } catch { /* privater Modus: Auswahl gilt nur bis zum Neuladen */ }
};

// --------------------------------------------------------------------- Helfer
const $ = (selector) => document.querySelector(selector);

/** DOM-Knoten bauen; Texte landen immer als textContent (Feed-Inhalte sind nicht vertrauenswürdig). */
function h(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  const { style, ...rest } = props;
  Object.assign(node, rest);
  if (style) Object.assign(node.style, style);
  node.append(...children.filter((c) => c !== null && c !== undefined && c !== ''));
  return node;
}

const moment = () => state.at ?? Date.now() / 1000;
const atQuery = (sep = '&') => (state.at ? `${sep}at=${Math.round(state.at)}` : '');
const dateTime = (ts) => new Date(ts * 1000).toLocaleString('de-DE', { dateStyle: 'medium', timeStyle: 'short' });
const date = (ts) => new Date(ts * 1000).toLocaleDateString('de-DE');
/** Warnung (DWD, NINA) zum betrachteten Zeitpunkt schon aufgehoben? */
const lifted = (ev) => Boolean(ev.ended && ev.ended <= moment());

function ago(ts) {
  const s = Math.max(0, moment() - ts);
  if (s >= 3 * 86400) return date(ts); // ältere Einträge mit Datum - wichtig beim Blättern im Verlauf
  if (s < 90) return 'gerade eben';
  if (s < 3600) return `vor ${Math.round(s / 60)} Min.`;
  if (s < 86400) return `vor ${Math.round(s / 3600)} Std.`;
  return `vor ${Math.round(s / 86400)} Tg.`;
}

const safeUrl = (url) => (typeof url === 'string' && /^https?:\/\//i.test(url) ? url : null);
const link = (url, text) => h('a', { href: url, target: '_blank', rel: 'noopener noreferrer', textContent: text });
const badge = (severity) => h('span', { className: 'badge', textContent: SEVERITY[severity],
  style: { background: SEVERITY_COLOR[severity] } });
const num = (value, digits = 0) => (value === null || value === undefined ? '–'
  : value.toFixed(digits).replace('.', ','));

async function api(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}

/** Wie api(), aber null, wenn sich seit dem letzten Abruf nichts geändert hat (gleiches ETag).
 *  Dann bleibt die Karte unangetastet - das spart auf dem Pi das meiste Neuzeichnen. */
const seen = new Map();
async function apiIfChanged(key, path) {
  const response = await fetch(path, { cache: 'no-cache' }); // Browser fragt mit If-None-Match, Server antwortet 304
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  const tag = `${path}|${response.headers.get('etag')}`;
  if (seen.get(key) === tag) return null;
  seen.set(key, tag);
  return response.json();
}
const forgetSeen = () => seen.clear(); // nach Filter- oder Zeitwechsel alles neu zeichnen

/** Ändernder Aufruf; der Server nimmt ihn nur mit dieser Kopfzeile an. Fehlertexte kommen als {error}. */
async function send(method, path, body) {
  const response = await fetch(path, { method, body: body ? JSON.stringify(body) : undefined,
    headers: { 'X-OpenRisk': '1', 'Content-Type': 'application/json' } });
  return response.json();
}

const featureCollection = (items, props) => ({
  type: 'FeatureCollection',
  features: items.map((item) => ({
    type: 'Feature', geometry: { type: 'Point', coordinates: [item.lon, item.lat] }, properties: props(item),
  })),
});

// ---------------------------------------------------------------------- Karte
let map;
// closeOnClick aus: sonst schließt der Klick, der ein neues Popup öffnet, es gleich wieder
// focusAfterOpen aus: sonst springt der Fokus auf den ersten Link und das Popup scrollt nach unten
const popup = new Popup({ maxWidth: '360px', closeOnClick: false, focusAfterOpen: false });

let firstLabel; // Flächen und Linien kommen unter die Beschriftung der Grundkarte
let film; // Wetterfilm: Radar, Wolken, Wind
let ready = false; // Karte samt eigener Ebenen fertig aufgebaut
const EMPTY = { type: 'FeatureCollection', features: [] };
const isDwd = (ev) => ev.id.startsWith('dwd:');
const dwdCell = (ev) => ev.id.split(':')[1]; // Ereignis-ID: dwd:<Warngebiet>:<Ereignis>

/** Ereignissymbol ev-<Ebene>-<Rang> anlegen, sobald es zum ersten Mal vorkommt. */
function ensureIcon(category, rank) {
  const id = `ev-${category}-${rank}`;
  if (map.hasImage(id)) return;
  map.addImage(id, imageData(eventIcon(kindOfRank(rank), Math.floor(rank / 100), LAYERS[category].color)),
    { pixelRatio: PIXEL_RATIO });
}

/** Symbolebene für Ereignisse; gebündelte Punkte zeigen ihr schwerstes Ereignis und die Anzahl. */
function eventLayer(id, category) {
  return { id, type: 'symbol', source: id,
    layout: {
      'icon-image': ['concat', `ev-${category}-`, ['to-string', ['get', 'rank']]],
      'icon-size': ['step', ['get', 'rank'], 0.66, 100, 0.8, 200, 0.92, 300, 1.06, 400, 1.2],
      'icon-allow-overlap': true, 'icon-ignore-placement': true, 'symbol-sort-key': ['get', 'rank'],
      'text-field': ['case', ['has', 'point_count'], ['to-string', ['get', 'point_count']], ''],
      'text-font': ['Noto Sans Medium'], 'text-size': 11, 'text-offset': [1.1, -1.1],
      'text-allow-overlap': true, 'text-ignore-placement': true,
    },
    paint: { 'text-color': '#ffffff', 'text-halo-color': '#0b0f14', 'text-halo-width': 2 } };
}
// Meldungen liegen oft auf demselben Punkt (Landesmitte) -> bündeln
const clustered = { type: 'geojson', data: EMPTY, cluster: true, clusterRadius: 30, clusterMaxZoom: 11, maxzoom: 12,
  clusterProperties: { rank: ['max', ['get', 'rank']] } };

const MAX_ZOOM = 22; // keine Grenze: über der höchsten geladenen Stufe werden die Kacheln vergrößert

/** Weltkarte und heruntergeladene Länder. Länder liegen als eigene Quelle über der Welt; Flächen und Linien
 *  aller Karten kommen unter die Beschriftungen, damit Ortsnamen nicht von Länderkacheln verdeckt werden. */
/** Heller Kartenstil nach Art der Wanderkarten: Wald grün, Wasser blau, Gelände sichtbar. */
function topoFlavor() {
  const f = namedFlavor('light');
  return { ...f, background: '#f2efe9', earth: '#f2efe9', water: '#9fcfe0', wood_a: '#c5e0b6', wood_b: '#a3cc93',
    park_a: '#d3eac3', park_b: '#bfe0aa', scrub_a: '#dbe7c8', scrub_b: '#c4d6a6', sand: '#efe3c2', beach: '#efe3c2',
    glacier: '#f4fbff', buildings: '#d6ccc4', military: '#efd8d4', industrial: '#e8dbe4', boundaries: '#9b7fa6',
    city_label: '#2f2f2f', state_label: '#8a7a8a', country_label: '#6b5b6b',
    landcover: { ...f.landcover, forest: 'rgba(163,204,147,1)', grassland: 'rgba(206,232,180,1)',
      farmland: 'rgba(236,238,210,1)', scrub: 'rgba(214,228,184,1)', barren: 'rgba(238,230,210,1)',
      urban_area: 'rgba(224,222,220,1)' } };
}
const LIGHT = () => state.mapStyle === 'light';

async function createMap(tiles, regions = []) {
  const hasTiles = Boolean(tiles) || regions.length > 0;
  const land = LIGHT() ? '#f2efe9' : '#0a0d12';
  document.body.classList.toggle('light-map', LIGHT());
  const origin = location.origin;
  const style = { version: 8, glyphs: `${origin}/vendor/fonts/{fontstack}/{range}.pbf`, sources: {}, layers: [] };
  if (hasTiles) {
    const protocol = new Protocol();
    addProtocol('pmtiles', protocol.tile);
    style.sprite = `${origin}/vendor/sprites/${LIGHT() ? 'light' : 'dark'}`;
    const flavor = LIGHT() ? topoFlavor() : namedFlavor('dark');
    const shapes = [];
    const labels = [];
    const add = (source, layers, prefix) => {
      for (const layer of layers) {
        if (prefix && layer.type === 'background') continue; // Hintergrund nur einmal, sonst verdeckt er die Welt
        const copy = prefix ? { ...layer, id: `${prefix}-${layer.id}`, source } : layer;
        (layer.type === 'symbol' ? labels : shapes).push(copy);
      }
    };
    if (tiles) {
      style.sources.basemap = { type: 'vector', url: `pmtiles://${origin}/map/world.pmtiles`,
        attribution: '© OpenStreetMap · Protomaps' };
      add('basemap', basemapLayers('basemap', flavor, { lang: 'de' }), '');
    } else {
      shapes.push({ id: 'background', type: 'background', paint: { 'background-color': land } });
    }
    for (const region of regions) {
      const source = `region-${region.iso}`;
      // erst oberhalb der Weltkarte einblenden - darunter genügt die Welt und spart Arbeit
      style.sources[source] = { type: 'vector', url: `pmtiles://${origin}/map/regions/${region.iso}.pmtiles`,
        minzoom: tiles ? Math.min(tiles.max_zoom + 1, region.max_zoom) : 0, attribution: '© OpenStreetMap · Protomaps' };
      add(source, basemapLayers(source, flavor, { lang: 'de' }), region.iso);
    }
    style.layers = [...shapes, ...labels];
  } else {
    style.layers = [{ id: 'background', type: 'background', paint: { 'background-color': land } }];
  }
  const maxZoom = MAX_ZOOM;

  map = new MapLibreMap({
    container: 'map', style, center: [15, 30], zoom: 1.6, minZoom: 1, maxZoom,
    dragRotate: false, pitchWithRotate: false, touchPitch: false, attributionControl: { compact: true },
    // für den Raspberry Pi: keine Überblend-Animationen, Kantenglättung aus, höchstens doppelte Auflösung
    fadeDuration: 0, pixelRatio: Math.min(window.devicePixelRatio || 1, 2),
    canvasContextAttributes: { antialias: false }, cancelPendingTileRequestsWhileZooming: true,
  });
  map.touchZoomRotate.disableRotation();
  map.addControl(new NavigationControl({ showCompass: false }), 'bottom-right');
  map.addControl(new ScaleControl({ unit: 'metric' }), 'bottom-left');
  await new Promise((resolve) => map.once('load', resolve));

  firstLabel = map.getStyle().layers.find((l) => l.type === 'symbol')?.id;
  const empty = EMPTY;

  // Länder: Risiko-Einfärbung (und einzige Landdarstellung, falls die Offline-Karte fehlt)
  // maxzoom: bis zu dieser Stufe zerlegt die Karte die Daten in Kacheln, darüber wird nur vergrößert
  map.addSource('countries', { type: 'geojson', data: '/geo/countries.geojson', promoteId: 'iso', maxzoom: 6 });
  if (!hasTiles) {
    map.addLayer({ id: 'land', type: 'fill', source: 'countries',
      paint: { 'fill-color': LIGHT() ? '#e4e0d6' : '#1a232e' } });
    map.addLayer({ id: 'borders', type: 'line', source: 'countries',
      paint: { 'line-color': LIGHT() ? '#9b8a9e' : '#3a4a5e', 'line-width': 0.6 } });
  }
  await addTerrain(state.status?.terrain ?? []);
  const score = ['coalesce', ['feature-state', 'score'], 0];
  map.addLayer({
    id: 'risk-fill', type: 'fill', source: 'countries',
    paint: {
      'fill-color': ['interpolate', ['linear'], score, 0, '#f5d90a', 35, '#f5d90a', 60, '#ff8b3e', 85, '#ff4d4f'],
      'fill-opacity': ['interpolate', ['linear'], score, 0, 0, 10, 0.06, 50, 0.22, 100, 0.42],
    },
  }, firstLabel);
  // Unsichtbare Länderflächen: Klick auf ein Land zeigt Länderdaten - nur in der Übersicht, beim Hineinzoomen
  // schließt ein Klick ins Leere wie gewohnt das Popup
  map.addLayer({ id: 'country-hit', type: 'fill', source: 'countries', maxzoom: 7, paint: { 'fill-opacity': 0 } },
    firstLabel);

  map.addSource('track', { type: 'geojson', data: empty });
  map.addLayer({ id: 'track', type: 'line', source: 'track',
    paint: { 'line-color': '#ffffff', 'line-width': 1.5, 'line-opacity': 0.8, 'line-dasharray': [2, 1.5] } });
  map.addSource('track-points', { type: 'geojson', data: empty });

  map.addSource('weather', { type: 'geojson', data: empty, maxzoom: 8 });
  map.addLayer({ id: 'weather-temp', type: 'symbol', source: 'weather',
    layout: { 'text-field': ['concat', ['to-string', ['round', ['get', 'temp']]], '°'], 'text-font': ['Noto Sans Medium'],
      'text-size': 12, 'text-padding': 6 },
    paint: { 'text-halo-color': '#0b0f14', 'text-halo-width': 1.6, 'text-color': TEMP_COLOR } });

  map.addSource('stations', { type: 'geojson', data: empty, maxzoom: 10 });
  map.addLayer({ id: 'stations-dot', type: 'circle', source: 'stations', minzoom: 3.5,
    paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 3.5, 1.2, 7, 3], 'circle-color': LAYERS.stations.color,
      'circle-stroke-color': '#0b0f14', 'circle-stroke-width': 0.8 } });
  map.addLayer({ id: 'stations-temp', type: 'symbol', source: 'stations', minzoom: 4.5, filter: ['has', 'temp'],
    layout: { 'text-field': ['concat', ['to-string', ['round', ['get', 'temp']]], '°'], 'text-font': ['Noto Sans Medium'],
      'text-size': 11, 'text-offset': [0, -0.9], 'text-padding': 3 },
    paint: { 'text-halo-color': '#0b0f14', 'text-halo-width': 1.4, 'text-color': TEMP_COLOR } });

  // Strahlung: Punkt je Sonde in Ampelfarbe, Messwert ab Zoom 7
  map.addSource('radiation', { type: 'geojson', data: empty, maxzoom: 10,
    attribution: 'Strahlung: Bundesamt für Strahlenschutz' });
  map.addLayer({ id: 'radiation-dot', type: 'circle', source: 'radiation',
    layout: { 'circle-sort-key': ['get', 'status'] },
    paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 3, ['match', ['get', 'status'], 3, 6, 2, 4, 2],
      8, ['match', ['get', 'status'], 3, 10, 2, 7, 5]],
    'circle-color': RADIATION_COLOR, 'circle-stroke-color': '#0b0f14', 'circle-stroke-width': 0.8 } });
  map.addLayer({ id: 'radiation-value', type: 'symbol', source: 'radiation', minzoom: 7, filter: ['has', 'label'],
    layout: { 'text-field': ['get', 'label'], 'text-font': ['Noto Sans Regular'], 'text-size': 10,
      'text-offset': [0, 1], 'text-anchor': 'top', 'text-padding': 2 },
    paint: { 'text-color': RADIATION_COLOR, 'text-halo-color': '#0b0f14', 'text-halo-width': 1.4 } });

  map.addSource('weather-alert', clustered);
  map.addLayer(eventLayer('weather-alert', 'weather'));
  map.addSource('disaster', { type: 'geojson', data: empty, maxzoom: 12 });
  map.addLayer(eventLayer('disaster', 'disaster'));
  map.addSource('conflict', { ...clustered, clusterRadius: 38, clusterMaxZoom: 8 });
  map.addLayer(eventLayer('conflict', 'conflict'));
  map.addSource('news', clustered);
  map.addLayer(eventLayer('news', 'news'));

  map.addImage('ship', imageData(shipIcon(LAYERS.ship.color)), { pixelRatio: PIXEL_RATIO });
  map.addImage('plane', imageData(planeIcon(LAYERS.aircraft.color)), { pixelRatio: PIXEL_RATIO });
  map.addImage('plane-ground', imageData(planeIcon('#8a8466')), { pixelRatio: PIXEL_RATIO });
  map.addImage('plane-alert', imageData(planeIcon(SEVERITY_COLOR[4])), { pixelRatio: PIXEL_RATIO });
  for (const type of Object.keys(BASES)) {
    map.addImage(`base-${type}`, imageData(baseIcon(type)), { pixelRatio: PIXEL_RATIO });
  }
  const vehicleLayout = { 'icon-rotate': ['coalesce', ['get', 'heading'], 0], 'icon-rotation-alignment': 'map',
    'icon-allow-overlap': true, 'icon-ignore-placement': true,
    'icon-size': ['interpolate', ['linear'], ['zoom'], 1, 0.4, 5, 0.75, 9, 1.1] };
  map.addSource('ships', { type: 'geojson', data: empty });
  map.addLayer({ id: 'ships', type: 'symbol', source: 'ships', layout: { ...vehicleLayout, 'icon-image': 'ship' } });
  map.addSource('aircraft', { type: 'geojson', data: empty });
  map.addLayer({ id: 'aircraft', type: 'symbol', source: 'aircraft',
    layout: { ...vehicleLayout, 'icon-image': ['get', 'icon'] } });
  // Spurpunkte mit Uhrzeit: wo war das Fahrzeug wann (überlappende Zeiten blendet die Karte aus)
  map.addLayer({ id: 'track-dots', type: 'circle', source: 'track-points',
    paint: { 'circle-radius': 2.5, 'circle-color': '#ffffff', 'circle-stroke-color': '#0b0f14', 'circle-stroke-width': 1 } });
  map.addLayer({ id: 'track-times', type: 'symbol', source: 'track-points',
    layout: { 'text-field': ['get', 'label'], 'text-font': ['Noto Sans Regular'], 'text-size': 10,
      'text-offset': [0.6, 0], 'text-anchor': 'left', 'text-padding': 4 },
    paint: { 'text-color': '#ffffff', 'text-halo-color': '#0b0f14', 'text-halo-width': 1.4 } });

  for (const id of CLICKABLE.filter((layer) => !['country-hit', 'dwd-warn'].includes(layer))) {
    map.on('mouseenter', id, () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', id, () => { map.getCanvas().style.cursor = ''; });
  }
  map.on('click', onMapClick);
  film = new WeatherFilm(map, { light: LIGHT(), before: () => firstLabel });
  Object.keys(LAYERS).forEach(applyVisibility);
}

/** Schummerung und Höhenlinien aus den geladenen Höhendaten (Einstellungen → Höhendaten). Der Server liefert
 *  je Kachel das feinste vorhandene Land, sonst die grobe Welt. */
async function addTerrain(files) {
  if (!files.length) return;
  const maxzoom = Math.max(...files.map((f) => f.max_zoom));
  const tiles = `${location.origin}/terrain/{z}/{x}/{y}.webp`;
  map.addSource('terrain-dem', { type: 'raster-dem', tiles: [tiles], tileSize: 512, maxzoom, encoding: 'terrarium',
    attribution: '<a href="https://mapterhorn.com/attribution">© Mapterhorn</a>' });
  map.addLayer({ id: 'hillshade', type: 'hillshade', source: 'terrain-dem',
    paint: LIGHT()
      ? { 'hillshade-exaggeration': 0.45, 'hillshade-shadow-color': '#4a4636', 'hillshade-highlight-color': '#ffffff',
        'hillshade-accent-color': '#6b6450' }
      : { 'hillshade-exaggeration': 0.35, 'hillshade-shadow-color': '#000000', 'hillshade-highlight-color': '#46566b',
        'hillshade-accent-color': '#111111' } }, firstLabel);
  // Höhenlinien rechnet die Bibliothek im Browser aus denselben Kacheln; fehlt sie (altes Update), nur Schummerung
  let mlcontour;
  try {
    mlcontour = (await import('./vendor/maplibre-contour/index.mjs')).default;
  } catch {
    return;
  }
  const dem = new mlcontour.DemSource({ url: tiles, encoding: 'terrarium', maxzoom, worker: true });
  dem.setupMaplibre({ addProtocol });
  map.addSource('contour-lines', { type: 'vector', maxzoom: 15, tiles: [dem.contourProtocolUrl({
    thresholds: { 9: [100, 500], 10: [50, 250], 11: [50, 250], 12: [20, 100], 13: [10, 50], 14: [10, 50], 15: [5, 25] },
    elevationKey: 'ele', levelKey: 'level', contourLayer: 'contours' })] });
  const color = LIGHT() ? '#a0703c' : '#8a7356';
  map.addLayer({ id: 'contours', type: 'line', source: 'contour-lines', 'source-layer': 'contours', minzoom: 9,
    paint: { 'line-color': color, 'line-opacity': LIGHT() ? 0.6 : 0.45,
      'line-width': ['match', ['get', 'level'], 1, 1.1, 0.55] } }, firstLabel);
  map.addLayer({ id: 'contour-labels', type: 'symbol', source: 'contour-lines', 'source-layer': 'contours', minzoom: 11,
    filter: ['>', ['get', 'level'], 0],
    layout: { 'symbol-placement': 'line', 'text-field': ['concat', ['number-format', ['get', 'ele'], {}], ' m'],
      'text-font': ['Noto Sans Regular'], 'text-size': 10, 'text-padding': 30 },
    paint: { 'text-color': color, 'text-halo-color': LIGHT() ? '#f2efe9' : '#0b0f14', 'text-halo-width': 1.2 } });
}

/** Pipelines und Militärbasen liegen als Datei vor und werden erst beim Einschalten geladen. */
function addStaticLayer(name) {
  const [file] = STATIC_FILES[name];
  if (map.getSource(file) || !state.status?.[file]) return;
  map.addSource(file, { type: 'geojson', data: `/geo/${file}.geojson`, tolerance: 0.6,
    maxzoom: file === 'pipelines' ? 9 : file === 'telecom' ? 12 : 10,
    attribution: ATTRIBUTION[file] ?? '© OpenStreetMap' });
  if (file === 'radon') {
    // Gemeinden nach Radon in Wohnräumen eingefärbt; Vorsorgegebiete als gestrichelte Umrisse
    const bq = ['coalesce', ['get', 'bq'], -1];
    map.addLayer({ id: 'radon-fill', type: 'fill', source: 'radon',
      paint: { 'fill-color': ['case', ['<', bq, 0], RADIATION_COLORS[0], ['<', bq, 100], RADIATION_COLORS[1],
        ['<', bq, 300], RADIATION_COLORS[2], RADIATION_COLORS[3]], 'fill-opacity': 0.35, 'fill-antialias': false } },
    firstLabel);
    map.addSource('radon_areas', { type: 'geojson', data: '/geo/radon_areas.geojson', maxzoom: 10 });
    map.addLayer({ id: 'radon-areas-hit', type: 'fill', source: 'radon_areas', paint: { 'fill-opacity': 0 } }, firstLabel);
    map.addLayer({ id: 'radon-areas-line', type: 'line', source: 'radon_areas',
      paint: { 'line-color': RADIATION_COLORS[3], 'line-width': 1.6, 'line-dasharray': [2, 1] } }, firstLabel);
  } else if (file === 'masts' || file === 'bunkers') {
    // Zehntausende Punkte: klein in der Übersicht, Betreiber bzw. Name erst beim Hineinzoomen
    const color = file === 'masts' ? ['case', ['get', 'mobile'], MAST_COLORS.mobile, MAST_COLORS.other]
      : ['case', ['get', 'lost'], BUNKER_COLORS.lost, BUNKER_COLORS.intact];
    map.addLayer({ id: file, type: 'circle', source: file,
      paint: { 'circle-color': color, 'circle-stroke-color': '#0b0f14', 'circle-stroke-width': 0.6,
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 4, 1.4, 8, 2.6, 12, 4.5, 15, 6] } }, 'weather-temp');
    map.addLayer({ id: `${file}-label`, type: 'symbol', source: file, minzoom: 12,
      layout: { 'text-field': ['coalesce', ['get', 'name'], ['get', 'operator'], ''], 'text-font': ['Noto Sans Regular'],
        'text-size': 10, 'text-offset': [0, 0.9], 'text-anchor': 'top', 'text-optional': true },
      paint: { 'text-color': file === 'masts' ? MAST_COLORS.mobile : BUNKER_COLORS.intact,
        'text-halo-color': '#0b0f14', 'text-halo-width': 1.2 } }, 'weather-temp');
  } else if (file === 'nuclear') {
    const color = ['match', ['get', 'status'], ...Object.entries(NUCLEAR).flatMap(([key, [, c]]) => [key, c]), '#475569'];
    const size = ['interpolate', ['linear'], ['get', 'mw'], 0, 3.5, 1000, 5, 4000, 8, 8000, 11];
    map.addLayer({ id: 'nuclear', type: 'circle', source: 'nuclear',
      paint: { 'circle-color': color, 'circle-stroke-color': '#0b0f14', 'circle-stroke-width': 1.2,
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 2, ['*', 0.8, size], 8, ['*', 1.4, size]] } }, 'weather-temp');
    map.addLayer({ id: 'nuclear-label', type: 'symbol', source: 'nuclear', minzoom: 4.5,
      layout: { 'text-field': ['get', 'name'], 'text-font': ['Noto Sans Regular'], 'text-size': 10,
        'text-offset': [0, 1], 'text-anchor': 'top', 'text-optional': true },
      paint: { 'text-color': color, 'text-halo-color': '#0b0f14', 'text-halo-width': 1.2 } }, 'weather-temp');
  } else if (file === 'water') {
    map.addLayer({ id: 'springs', type: 'circle', source: 'water',
      paint: { 'circle-color': WATER_COLOR, 'circle-stroke-color': '#0b0f14', 'circle-stroke-width': 0.7,
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 5, 1.4, 9, 2.8, 13, 5, 16, 7] } }, 'weather-temp');
    map.addLayer({ id: 'springs-label', type: 'symbol', source: 'water', minzoom: 12,
      layout: { 'text-field': ['coalesce', ['get', 'name'], ''], 'text-font': ['Noto Sans Italic'], 'text-size': 10,
        'text-offset': [0, 0.9], 'text-anchor': 'top', 'text-optional': true },
      paint: { 'text-color': '#7cc4ff', 'text-halo-color': '#0b0f14', 'text-halo-width': 1.2 } }, 'weather-temp');
  } else if (file === 'cables') {
    // Farbe je Kabel wie auf der Submarine Cable Map, damit sich überlagernde Kabel unterscheiden lassen
    const width = ['interpolate', ['linear'], ['zoom'], 1, 0.8, 5, 1.6, 10, 2.6];
    map.addLayer({ id: 'cables', type: 'line', source: 'cables', filter: ['!', ['get', 'planned']],
      paint: { 'line-color': ['get', 'color'], 'line-opacity': 0.85, 'line-width': width } }, 'track');
    map.addLayer({ id: 'cables-planned', type: 'line', source: 'cables', filter: ['get', 'planned'],
      paint: { 'line-color': ['get', 'color'], 'line-opacity': 0.7, 'line-width': width, 'line-dasharray': [2, 2] } },
    'track');
    map.addSource('landings', { type: 'geojson', data: '/geo/landings.geojson', maxzoom: 10 });
    map.addLayer({ id: 'landings', type: 'circle', source: 'landings', minzoom: 2,
      paint: { 'circle-color': '#0b0f14', 'circle-stroke-color': LAYERS.cables.color, 'circle-stroke-width': 1.4,
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 2, 1.6, 6, 3, 10, 4.5] } }, 'weather-temp');
  } else if (file === 'ixp' || file === 'datacenters') {
    // nach Größe: Knoten nach angeschlossenen Netzen, Rechenzentren nach Netzen im Haus
    const size = file === 'ixp' ? ['get', 'members'] : ['get', 'networks'];
    const { color } = LAYERS[file];
    const below = file === 'datacenters' && map.getLayer('ixp') ? 'ixp' : 'weather-temp'; // Knoten über Rechenzentren
    map.addLayer({ id: file, type: 'circle', source: file,
      paint: { 'circle-color': color, 'circle-opacity': 0.85, 'circle-stroke-color': '#0b0f14', 'circle-stroke-width': 0.8,
        'circle-radius': ['interpolate', ['linear'], ['zoom'], ...[[2, 0.7], [8, 1], [12, 1.4]].flatMap(([zoom, factor]) =>
          [zoom, ['interpolate', ['linear'], size, ...[[0, 2.2], [50, 3.5], [300, 6], [1000, 9]]
            .flatMap(([n, r]) => [n, r * factor])]])] } }, below);
    map.addLayer({ id: `${file}-label`, type: 'symbol', source: file, minzoom: file === 'ixp' ? 4 : 9,
      layout: { 'text-field': ['get', 'name'], 'text-font': ['Noto Sans Regular'], 'text-size': 10,
        'text-offset': [0, 0.9], 'text-anchor': 'top', 'text-optional': true, 'symbol-sort-key': ['-', 0, size] },
      paint: { 'text-color': color, 'text-halo-color': '#0b0f14', 'text-halo-width': 1.2 } }, 'weather-temp');
  } else if (file === 'telecom') {
    map.addLayer({ id: 'telecom', type: 'line', source: 'telecom',
      paint: { 'line-color': LAYERS.telecom.color, 'line-opacity': 0.8,
        'line-width': ['interpolate', ['linear'], ['zoom'], 5, 0.8, 10, 1.6, 15, 3] } }, 'track');
  } else if (file === 'pipelines') {
    map.addLayer({ id: 'pipelines', type: 'line', source: 'pipelines',
      paint: { 'line-color': LAYERS.pipelines.color, 'line-opacity': 0.75,
        'line-width': ['interpolate', ['linear'], ['zoom'], 1, 0.6, 5, 1.2, 9, 2.2] } }, 'track');
  } else {
    // Tausende Standorte: die Karte blendet überlappende aus, wichtige Arten haben Vorrang
    const base = {
      type: 'symbol', source: 'bases',
      layout: {
        'icon-image': ['concat', 'base-', ['get', 'type']], 'icon-padding': 1,
        'icon-size': ['interpolate', ['linear'], ['zoom'], 1, 0.55, 6, 0.9, 10, 1.2],
        'symbol-sort-key': ['match', ['get', 'type'], 'naval_base', 0, 'airfield', 1, 2],
        'text-field': ['step', ['zoom'], '', 7, ['coalesce', ['get', 'name'], '']], 'text-font': ['Noto Sans Regular'],
        'text-size': 10, 'text-offset': [0, 1], 'text-anchor': 'top', 'text-optional': true,
      },
      paint: { 'text-color': '#c9d6a3', 'text-halo-color': '#0b0f14', 'text-halo-width': 1.2 } };
    map.addLayer({ ...base, id: 'bases', filter: ['!=', ['get', 'type'], 'barracks'] }, 'weather-temp');
    map.addLayer({ ...base, id: 'bases-barracks', filter: ['==', ['get', 'type'], 'barracks'] }, 'weather-temp');
    applyVisibility(name === 'bases' ? 'barracks' : 'bases'); // die zweite Ebene derselben Datei
  }
  applyVisibility(name);
}

/** Warngebiete des DWD als Flächen; die Datei legt der Sammler beim ersten Abruf an. */
function addDwdCells() {
  if (map.getSource('dwd-cells') || !state.status?.dwd_cells || !state.visible.weather) return;
  const level = ['coalesce', ['feature-state', 'level'], 0];
  map.addSource('dwd-cells', { type: 'geojson', data: '/geo/dwd_cells.geojson', promoteId: 'id',
    attribution: 'Warnungen: Deutscher Wetterdienst' });
  map.addLayer({ id: 'dwd-warn', type: 'fill', source: 'dwd-cells',
    paint: { 'fill-color': DWD_LEVEL_COLOR, 'fill-outline-color': DWD_LEVEL_COLOR,
      'fill-opacity': ['case', ['>', level, 0], 0.38, 0] } }, firstLabel);
  applyVisibility('weather');
}

/** Warngebiete aus NINA/KATWARN; die Datei schreibt der Sammler bei jedem Abruf neu. */
let ninaShown = '';
function updateNinaAreas() {
  if (!state.status?.nina_areas || !state.visible.disaster) return;
  const signature = state.events.filter((ev) => ev.id.startsWith('nina:')).map((ev) => ev.id).sort().join('|');
  if (map.getSource('nina-areas')) {
    if (signature !== ninaShown) map.getSource('nina-areas').setData(`/geo/nina_areas.geojson?v=${Date.now()}`);
  } else {
    const color = ['match', ['get', 'severity'], 2, SEVERITY_COLOR[2], 3, SEVERITY_COLOR[3], 4, SEVERITY_COLOR[4],
      SEVERITY_COLOR[1]];
    map.addSource('nina-areas', { type: 'geojson', data: '/geo/nina_areas.geojson',
      attribution: 'Warnungen: BBK (NINA)' });
    map.addLayer({ id: 'nina-areas', type: 'fill', source: 'nina-areas',
      paint: { 'fill-color': color, 'fill-outline-color': color, 'fill-opacity': 0.3 } }, firstLabel);
    applyVisibility('disaster');
  }
  ninaShown = signature;
}

/** Statische Datei ganz aus der Karte nehmen, wenn keine ihrer Ebenen mehr sichtbar ist (gibt Speicher frei). */
function dropStaticLayer(name) {
  const [file] = STATIC_FILES[name];
  const sharing = Object.keys(STATIC_FILES).filter((other) => STATIC_FILES[other][0] === file);
  if (!map.getSource(file) || sharing.some((other) => state.visible[other])) return;
  const sources = new Set([file]);
  for (const other of sharing) {
    for (const id of LAYERS[other].ids.filter((layer) => map.getLayer(layer))) {
      sources.add(map.getLayer(id).source);
      map.removeLayer(id);
    }
  }
  sources.forEach((source) => map.getSource(source) && map.removeSource(source));
}

function applyVisibility(name) {
  if (name in STATIC_FILES) {
    if (state.visible[name]) addStaticLayer(name);
    else dropStaticLayer(name);
  }
  if (name === 'weather' && state.visible.weather) addDwdCells();
  if (['radar', 'clouds', 'wind'].includes(name)) film?.setVisible(name, state.visible[name]);
  for (const id of LAYERS[name].ids) {
    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', state.visible[name] ? 'visible' : 'none');
  }
}

// --------------------------------------------------------------------- Popups
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
  'November', 'December'];
/** „2017 June“ (TeleGeography) -> „Juni 2017“ */
const rfsDate = (rfs) => rfs?.replace(/^(\d{4})(?: Q(\d))? ?(\w*)$/, (all, year, quarter, month) =>
  (MONTHS.includes(month) ? `${new Date(2000, MONTHS.indexOf(month)).toLocaleString('de-DE', { month: 'long' })} ${year}`
    : quarter ? `${quarter}. Quartal ${year}` : month ? all : year));
function cableNode(c) {
  return h('div', {},
    h('h3', { textContent: c.name }),
    h('table', {}, row('Status', c.planned ? `geplant / im Bau${c.rfs ? `, Inbetriebnahme ${rfsDate(c.rfs)}` : ''}`
      : `in Betrieb${c.rfs ? ` seit ${rfsDate(c.rfs)}` : ''}`), row('Länge', c.length?.replace(',', '.')),
    row('Eigentümer', c.owners), row('Anlandepunkte', c.landings)),
    safeUrl(c.url) ? h('p', {}, link(c.url, 'Webseite ↗')) : '');
}

function showPopup(lngLat, node) {
  popup.setLngLat(lngLat).setDOMContent(h('div', { className: 'popup' }, node)).addTo(map);
}

function eventNode(ev, compact = false) {
  const url = safeUrl(ev.url);
  const meta = [ev.source, ev.place, KIND_LABEL[ev.kind] || ev.kind, ago(ev.since),
    lifted(ev) ? `aufgehoben ${ago(ev.ended)}` : ''].filter(Boolean).join(' · ');
  return h('div', compact ? { className: 'item', style: { borderLeftColor: SEVERITY_COLOR[ev.severity] } } : {},
    compact ? h('div', {}, url ? link(url, ev.title) : ev.title) : h('h3', { textContent: ev.title }),
    h('div', { className: 'meta' }, compact ? '' : badge(ev.severity), meta),
    ev.summary && (!compact || isDwd(ev)) ? h('p', { textContent: ev.summary }) : '',
    !compact && url ? h('p', {}, link(url, 'Quelle öffnen ↗')) : '');
}

const row = (label, value) => (value === null || value === undefined || value === '' ? ''
  : h('tr', {}, h('td', { textContent: label }), h('td', { textContent: value })));

function vehicleNode(v) {
  const air = v.kind === 'aircraft';
  return h('div', {},
    h('h3', { textContent: v.callsign || v.reg || v.id }),
    h('div', { className: 'meta', textContent: `${air ? 'Militärflugzeug' : 'Militärschiff'} · ${ago(v.seen)}` }),
    h('table', {},
      row('Staat', v.country), row('Typ', v.type), row(air ? 'Kennzeichen' : 'MMSI', v.reg),
      air ? row('Höhe', v.info || (v.alt === null ? null : `${Math.round(v.alt)} ft (${Math.round(v.alt * 0.3048)} m)`)) : '',
      row('Geschwindigkeit', v.speed === null ? null : `${Math.round(v.speed)} kn (${Math.round(v.speed * 1.852)} km/h)`),
      row('Kurs', v.heading === null ? null : `${Math.round(v.heading)}°`),
      row('Squawk', v.squawk), air ? '' : row('Info', v.info)));
}

function weatherNode(w) {
  const day = (iso) => new Date(`${iso}T00:00:00Z`).toLocaleDateString('de-DE', { weekday: 'short', timeZone: 'UTC' });
  return h('div', {},
    h('h3', { textContent: w.place }),
    h('div', { className: 'meta' }, w.severity ? badge(w.severity) : '', `${WMO[w.code] ?? 'Wetter'} · ${ago(w.ts)}`),
    h('table', {},
      row('Temperatur', `${num(w.temp, 1)} °C`), row('Wind', `${num(w.wind)} km/h, Böen ${num(w.gust)} km/h`),
      row('Niederschlag', `${num(w.precip, 1)} mm/h`),
      ...w.forecast.map((f) => row(day(f.date),
        `${num(f.tmin)}…${num(f.tmax)} °C · ${WMO[f.code] ?? ''} · ${num(f.precip, 1)} mm · Böen ${num(f.gust)} km/h`))),
    historyChart('city', w.place));
}

function radiationNode(r) {
  const usv = (v) => (v === null || v === undefined ? null : `${num(v, 2)} µSv/h`);
  // Rot, aber schon seit Tagen so hoch: bekannter belasteter Ort statt neuer Freisetzung
  const lasting = r.status === 3 && r.baseline && r.value < 2 * r.baseline;
  const label = lasting ? 'dauerhaft erhöht – bekannter belasteter Ort' : RADIATION_STATUS[r.status];
  const station = `${r.network === 'EURDEP' ? 'EURDEP-Station' : 'BfS-Sonde'} ${r.id}`;
  return h('div', {},
    h('h3', { textContent: r.name }),
    h('div', { className: 'meta' }, h('span', { className: 'badge', textContent: label,
      style: { background: RADIATION_COLORS[r.status] } }), r.ts ? `${station} · ${ago(r.ts)}` : station),
    h('table', {},
      row('Ortsdosisleistung', usv(r.value)),
      row('üblich hier (7 Tage)', usv(r.baseline) ?? 'wird noch ermittelt'),
      row('davon Höhenstrahlung', usv(r.cosmic)), row('davon Boden/Luft', usv(r.terrestrial)),
      row('Höhe', r.elevation === null || r.elevation === undefined ? null : `${num(r.elevation)} m`)),
    h('p', { className: 'meta', textContent: 'Natürlich üblich: etwa 0,05–0,25 µSv/h, im Gebirge und auf Granit '
      + 'auch mehr. Gelb ab 0,30 µSv/h oder doppelt so hoch wie üblich, rot ab 1,00 µSv/h. Bei Regen kann der Wert kurzzeitig steigen. Grün/Gelb/Rot ist eine eigene Einordnung, keine '
      + 'amtliche Warnung.' }),
    historyChart('radiation', r.id, 'µSv/h', 2));
}

function stationNode(st) {
  return h('div', {},
    h('h3', { textContent: st.name }),
    h('div', { className: 'meta', textContent: `DWD-Station ${st.id} · ${ago(st.ts)}` }),
    h('table', {},
      row('Temperatur', st.temp === null ? null : `${num(st.temp, 1)} °C`),
      row('Luftfeuchte', st.humidity === null ? null : `${num(st.humidity)} %`),
      row('Wind', st.wind === null ? null : `${num(st.wind)} km/h aus ${num(st.wind_dir)}°`),
      row('Böen', st.gust === null ? null : `${num(st.gust)} km/h`),
      row('Niederschlag', st.precip === null ? null : `${num(st.precip, 1)} mm / 10 min`),
      row('Luftdruck', st.pressure === null ? null : `${num(st.pressure, 1)} hPa`),
      row('Höhe', st.elevation === null ? null : `${num(st.elevation)} m`)),
    historyChart('station', st.id));
}

/** Temperaturkurve aus dem gespeicherten Verlauf (Stadt oder DWD-Station). */
function historyChart(kind, id, unit = '°C', digits = 1) {
  const box = h('div', { className: 'chart' });
  const show = async (days) => {
    const { points } = await api(`/api/history?kind=${kind}&id=${encodeURIComponent(id)}&days=${days}${atQuery()}`);
    const ranges = h('div', { className: 'ranges' }, ...[[7, '7 Tage'], [30, '30 Tage'], [365, '1 Jahr']].map(([d, label]) =>
      h('button', { textContent: label, className: d === days ? 'on' : '', onclick: () => show(d) })));
    if (points.length < 2) {
      box.replaceChildren(h('div', { className: 'meta', textContent: 'Noch zu wenig Verlauf gespeichert.' }), ranges);
      return;
    }
    const temps = points.map((p) => p[1]);
    const [t0, t1] = [points[0][0], points[points.length - 1][0]];
    const [lo, hi] = [Math.min(...temps), Math.max(...temps)];
    const x = (t) => ((t - t0) / Math.max(1, t1 - t0)) * 296 + 2;
    const y = (v) => 66 - ((v - lo) / Math.max(hi - lo, digits > 1 ? 0.01 : 1)) * 60;
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 300 72');
    svg.setAttribute('preserveAspectRatio', 'none');
    const line = document.createElementNS(ns, 'polyline');
    line.setAttribute('points', points.map(([t, v]) => `${x(t).toFixed(1)},${y(v).toFixed(1)}`).join(' '));
    Object.entries({ fill: 'none', stroke: '#ffd27a', 'stroke-width': '1.5', 'vector-effect': 'non-scaling-stroke' })
      .forEach(([k, v]) => line.setAttribute(k, v));
    svg.append(line);
    box.replaceChildren(svg,
      h('div', { className: 'meta', textContent: `${date(t0)} – ${date(t1)} · min ${num(lo, digits)} ${unit} · `
        + `max ${num(hi, digits)} ${unit}` }),
      ranges);
  };
  show(7).catch(() => box.replaceChildren());
  return box;
}

const clock = (ts) => new Date(ts * 1000).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });

/** Spur zeichnen: Linie plus Punkte mit Uhrzeit; bleibt stehen, bis sie ausgeblendet wird. */
function showTrack(points) {
  map.getSource('track').setData({ type: 'Feature', properties: {},
    geometry: { type: 'LineString', coordinates: points.map((pt) => [pt.lon, pt.lat]) } });
  map.getSource('track-points').setData(featureCollection(points, (pt) => ({
    label: clock(pt.ts), ts: pt.ts, alt: pt.alt, speed: pt.speed, heading: pt.heading })));
  $('#track-clear').hidden = !points.length;
}

function clearTrack() {
  showTrack([]);
  document.querySelectorAll('#vresults li.on').forEach((li) => li.classList.remove('on'));
}

const eventList = (title, items) => h('div', {}, h('h3', { textContent: title }),
  ...items.sort((a, b) => b.severity - a.severity || b.since - a.since).map((ev) => eventNode(ev, true)));

// Beschriftungen von Städten, Orten und Ortsteilen in der Offline-Karte (Protomaps, Quellebene "places")
const PLACE_KINDS = new Set(['locality', 'neighbourhood', 'macrohood']);
function placeLabelAt(box) {
  const layers = map.getStyle().layers.filter((l) => l['source-layer'] === 'places' && l.type === 'symbol')
    .map((l) => l.id);
  return layers.length ? map.queryRenderedFeatures(box, { layers })
    .find((f) => PLACE_KINDS.has(f.properties.kind) && f.geometry.type === 'Point') : null;
}

async function onMapClick(e) {
  const box = [[e.point.x - 5, e.point.y - 5], [e.point.x + 5, e.point.y + 5]];
  const layers = CLICKABLE.filter((id) => map.getLayer(id));
  const hits = map.queryRenderedFeatures(box, { layers })
    .filter((hit) => hit.layer.id !== 'dwd-warn' || hit.state.level > 0); // Warngebiet ohne Warnung: durchlassen
  const feature = layers.map((id) => hits.find((hit) => hit.layer.id === id)).find(Boolean);
  if (!feature || feature.layer.id === 'country-hit') { // Ortsname der Grundkarte angeklickt? Dann Ortsinfos
    const label = placeLabelAt(box);
    if (label) {
      const [lon, lat] = label.geometry.coordinates;
      const name = label.properties['name:de'] || label.properties.name;
      showPlace(`name=${encodeURIComponent(name)}&lat=${lat}&lon=${lon}`, [lon, lat]).catch(failed);
      return;
    }
  }
  if (!feature) { popup.remove(); return; }
  let p = feature.properties; // bei mehreren Treffern am selben Punkt ggf. ersetzt (Internetknoten)
  const at = feature.geometry.type === 'Point' ? feature.geometry.coordinates : e.lngLat;

  switch (feature.layer.id) {
    case 'track-dots':
      showPopup(at, h('div', {},
        h('h3', { textContent: dateTime(p.ts) }),
        h('table', {},
          row('Höhe', p.alt === undefined ? null : `${Math.round(p.alt)} ft (${Math.round(p.alt * 0.3048)} m)`),
          row('Geschwindigkeit', p.speed === undefined ? null : `${Math.round(p.speed)} kn (${Math.round(p.speed * 1.852)} km/h)`),
          row('Kurs', p.heading === undefined ? null : `${Math.round(p.heading)}°`))));
      break;
    case 'aircraft':
    case 'ships': {
      const v = state.byId.get(p.id);
      showPopup(at, vehicleNode(v));
      const to = Math.round(moment());
      const { points } = await api(`/api/track?id=${encodeURIComponent(v.id)}&from=${to - 12 * 3600}&to=${to}`);
      showTrack(points);
      break;
    }
    case 'conflict':
      if (p.cluster) {
        const leaves = await map.getSource('conflict').getClusterLeaves(p.cluster_id, 40, 0);
        const items = leaves.map((leaf) => state.byId.get(leaf.properties.id)).filter(Boolean);
        showPopup(at, eventList(`${p.point_count} Konfliktereignisse`
          + (p.point_count > items.length ? ` (Auswahl von ${items.length})` : ''), items));
      } else {  // Volltext erst beim Anklicken holen
        const full = await api(`/api/event?id=${encodeURIComponent(p.id)}`);
        showPopup(at, eventNode({ ...full, since: full.ts }));
      }
      break;
    case 'news':
    case 'disaster':
    case 'weather-alert':
      if (p.cluster) {
        const leaves = await map.getSource(feature.layer.id).getClusterLeaves(p.cluster_id, 40, 0);
        const items = leaves.map((leaf) => state.byId.get(leaf.properties.id)).filter(Boolean);
        showPopup(at, eventList(`${p.point_count} Meldungen${items[0]?.place ? ` · ${items[0].place}` : ''}`, items));
      } else {
        showPopup(at, eventNode(state.byId.get(p.id)));
      }
      break;
    case 'nina-areas':
      showPopup(e.lngLat, eventNode(state.byId.get(p.id) ?? { title: 'Warnung', severity: p.severity, since: Date.now() / 1000 }));
      break;
    case 'dwd-warn':
      showPopup(e.lngLat, eventList(p.name, state.events.filter((ev) => isDwd(ev) && !lifted(ev) && dwdCell(ev) === p.id)));
      break;
    case 'radiation-dot':
    case 'radiation-value':
      showPopup(at, radiationNode(state.radiation.find((r) => r.id === p.id)));
      break;
    case 'stations-temp':
    case 'stations-dot':
      showPopup(at, stationNode(state.stations.find((st) => st.id === p.id)));
      break;
    case 'bases':
    case 'bases-barracks':
      showPopup(at, h('div', {},
        h('h3', { textContent: p.name || BASES[p.type]?.[0] || 'Militärstandort' }),
        h('table', {}, row('Art', BASES[p.type]?.[0]), row('Betreiber', p.operator)),
        h('div', { className: 'meta', textContent: 'Quelle: OpenStreetMap' })));
      break;
    case 'masts':
      showPopup(at, h('div', {},
        h('h3', { textContent: p.name || (p.mobile ? 'Mobilfunkmast' : 'Funkmast') }),
        h('table', {}, row('Art', `${p.kind}${p.mobile ? ' · Mobilfunk' : ' · Funk (Rundfunk, Richtfunk o. Ä.)'}`),
          row('Betreiber', p.operator), row('Höhe', p.height ? `${p.height.replace('.', ',')} m` : null),
          row('Netze', p.bands), row('Kennung', p.ref)),
        h('div', { className: 'meta', textContent: 'Quelle: OpenStreetMap – nicht vollständig, erfasst ist etwa '
          + 'ein Drittel der Mobilfunkstandorte.' })));
      break;
    case 'bunkers':
      showPopup(at, h('div', {},
        h('h3', { textContent: p.name || p.type }),
        h('table', {}, row('Art', [p.type, p.level].filter(Boolean).join(' · ')),
          row('Zustand', p.lost ? 'aufgegeben / Ruine' : null), row('Militärisch', p.military ? 'ja' : null),
          row('Betreiber', p.operator), row('Zugang', ACCESS[p.access] ?? p.access), row('Gebaut', p.built),
          row('Beschreibung', p.note)),
        h('div', { className: 'meta', textContent: 'Quelle: OpenStreetMap. Ob ein Bunker heute als Schutzraum '
          + 'nutzbar ist, geht daraus nicht hervor – öffentliche Schutzräume gibt es in Deutschland kaum noch.' })));
      break;
    case 'cables':
    case 'cables-planned': {
      // Seekabel liegen oft dicht nebeneinander: alle unter dem Mauszeiger nennen
      const all = [...new Map(map.queryRenderedFeatures(box, { layers: ['cables', 'cables-planned'] })
        .map((hit) => [hit.properties.id, hit.properties])).values()];
      showPopup(e.lngLat, h('div', {}, ...all.slice(0, 4).map(cableNode),
        all.length > 4 ? h('p', { className: 'meta', textContent: `und ${all.length - 4} ${all.length === 5 ? 'weiteres' : 'weitere'} Kabel – näher heranzoomen` }) : '',
        h('div', { className: 'meta', textContent: 'Quelle: TeleGeography Submarine Cable Map. Verlauf schematisch, '
          + 'nicht metergenau.' })));
      break;
    }
    case 'landings':
      showPopup(at, h('div', {},
        h('h3', { textContent: p.name }),
        h('table', {}, row('Art', `Seekabel-Anlandepunkt${p.planned ? ' (geplant)' : ''}`),
          row(p.count > 1 ? `${p.count} Kabel` : 'Kabel', p.cables)),
        h('div', { className: 'meta', textContent: 'Quelle: TeleGeography Submarine Cable Map' })));
      break;
    case 'ixp': {
      // Mehrere Knoten im selben Rechenzentrum: den größten zeigen, die anderen nennen
      const all = map.queryRenderedFeatures(box, { layers: ['ixp'] }).map((hit) => hit.properties)
        .sort((a, b) => b.members - a.members).filter((ix, i, list) => list.findIndex((o) => o.name === ix.name) === i);
      const others = all.filter((ix) => ix.name !== all[0].name);
      p = all[0] ?? p;
      showPopup(at, h('div', {},
        h('h3', { textContent: p.name }),
        h('table', {}, row('Art', 'Internetknoten (Internet Exchange Point)'), row('Name', p.long),
          row('Ort', [p.city, p.country && regionName.of(p.country)].filter(Boolean).join(', ')),
          row('Angeschlossene Netze', p.members.toLocaleString('de-DE')),
          row('Standorte', p.sites > 1 ? `${p.sites} Rechenzentren, eingezeichnet: ${p.site}` : p.site),
          row('Ebenfalls hier', others.map((ix) => `${ix.name} (${ix.members})`).join(' · '))),
        safeUrl(p.url) ? h('p', {}, link(p.url, 'Webseite ↗')) : '',
        h('div', { className: 'meta', textContent: 'Quelle: PeeringDB. Hier tauschen Netzbetreiber Datenverkehr '
          + 'aus – fällt ein großer Knoten aus, wird das Internet in der Region langsamer.' })));
      break;
    }
    case 'datacenters':
      showPopup(at, h('div', {},
        h('h3', { textContent: p.name }),
        h('table', {}, row('Art', 'Rechenzentrum (Kolokation)'), row('Betreiber', p.operator),
          row('Ort', [p.city, p.country && regionName.of(p.country)].filter(Boolean).join(', ')),
          row('Netze im Haus', p.networks.toLocaleString('de-DE')), row('Internetknoten', p.ixps || null)),
        safeUrl(p.url) ? h('p', {}, link(p.url, 'Webseite ↗')) : '',
        h('div', { className: 'meta', textContent: 'Quelle: PeeringDB – nur Rechenzentren, in denen sich Netzbetreiber '
          + 'zusammenschalten; reine Firmen-Rechenzentren fehlen.' })));
      break;
    case 'nuclear': {
      const blocks = JSON.parse(p.blocks || '[]');
      const years = (b) => (b.from ? `${b.from}${b.to ? `–${b.to}` : ''}` : b.built ? `Baubeginn ${b.built}` : '');
      showPopup(at, h('div', {},
        h('h3', { textContent: `Kernkraftwerk ${p.name}` }),
        h('div', { className: 'meta' }, h('span', { className: 'badge', textContent: NUCLEAR[p.status]?.[0] ?? p.status,
          style: { background: NUCLEAR[p.status]?.[1], color: '#0b0f14' } }),
        p.country ? regionName.of(p.country) : ''),
        h('table', {}, row('Reaktoren', `${p.reactors}${p.active ? `, davon ${p.active} in Betrieb` : ''}`),
          row('Leistung in Betrieb', p.mw ? `${p.mw.toLocaleString('de-DE')} MW` : null)),
        h('table', { className: 'blocks' }, ...blocks.map((b) => h('tr', {},
          h('td', { textContent: b.name }),
          h('td', {}, h('span', { className: 'symbol dot', style: { background: NUCLEAR[b.status]?.[1] } }),
            NUCLEAR[b.status]?.[0] ?? b.status),
          h('td', { textContent: [REACTOR_TYPES[b.type] ?? b.type, b.mw ? `${b.mw} MW` : ''].filter(Boolean).join(', ') }),
          h('td', { textContent: years(b) })))),
        h('div', { className: 'meta', textContent: `Quelle: GeoNuclearData (IAEA PRIS, WNA), Stand ${p.stand
          ? date(Date.parse(p.stand) / 1000) : 'unbekannt'} – spätere Änderungen (neue Blöcke, Abschaltungen) können `
          + 'fehlen.' })));
      break;
    }
    case 'springs':
      showPopup(at, h('div', {},
        h('h3', { textContent: p.name || p.type }),
        h('table', {}, row('Art', p.type),
          row('Trinkwasser', p.drinking === true ? 'ja (laut Eintrag)' : p.drinking === false ? 'nein – kein Trinkwasser'
            : 'unbekannt – vor dem Trinken abkochen oder filtern'),
          row('Schüttung', p.seasonal ? 'nicht ganzjährig, kann versiegen' : null), row('Pumpe', p.pump),
          row('Zugang', ACCESS[p.access] ?? p.access), row('Betreiber', p.operator), row('Beschreibung', p.note)),
        h('div', { className: 'meta', textContent: 'Quelle: OpenStreetMap. Natürliche Quellen sind nicht überwacht; '
          + 'Wasser aus der Natur im Zweifel immer abkochen.' })));
      break;
    case 'telecom':
      showPopup(e.lngLat, h('div', {},
        h('h3', { textContent: p.name || (p.fibre ? 'Glasfaserleitung' : 'Fernmeldeleitung') }),
        h('table', {}, row('Betreiber', p.operator), row('Leitung', p.medium), row('Lage', p.location),
          row('Kennung', p.ref)),
        h('div', { className: 'meta', textContent: 'Quelle: OpenStreetMap – nur ein kleiner Teil des Netzes ist '
          + 'erfasst; die Betreiber veröffentlichen ihre Trassen nicht.' })));
      break;
    case 'weather-temp':
      showPopup(at, weatherNode(state.weather.find((w) => w.place === p.place)));
      break;
    case 'radon-fill': {
      const level = p.bq === undefined ? 0 : p.bq < 100 ? 1 : p.bq < 300 ? 2 : 3;
      showPopup(e.lngLat, h('div', {},
        h('h3', { textContent: p.name }),
        h('div', { className: 'meta' }, h('span', { className: 'badge', textContent: RADON_STATUS[level],
          style: { background: RADIATION_COLORS[level] } }), 'Radon in Wohnräumen'),
        h('table', {}, row('Schätzwert Gemeinde', p.bq === undefined ? 'keine Angabe' : `${p.bq} Bq/m³`),
          row('Radon-Vorsorgegebiet', p.area ? `ja – ${p.area}` : 'nein')),
        h('p', { className: 'meta', textContent: 'Geschätzter Durchschnitt für Wohnräume der Gemeinde (BfS); im '
          + 'einzelnen Haus, vor allem im Keller, kann es deutlich mehr sein. Grün unter 100 Bq/m³ (Empfehlung der '
          + 'WHO), Gelb bis 300 Bq/m³ (Referenzwert in Deutschland), Rot darüber. Vorsorgegebiete (rot gestrichelt) '
          + 'sind nach Strahlenschutzgesetz ausgewiesen; dort gelten besondere Bau- und Messpflichten.' })));
      break;
    }
    case 'pipelines':
      showPopup(e.lngLat, h('div', {},
        h('h3', { textContent: p.name || 'Öl-Pipeline' }),
        h('table', {}, row('Betreiber', p.operator), row('Medium', p.substance))));
      break;
    case 'country-hit':
      showCountry(p.iso, e.lngLat);
      break;
  }
}

// ------------------------------------------------------------------ Länder
const regionName = new Intl.DisplayNames(['de'], { type: 'region' });
const languageName = new Intl.DisplayNames(['de'], { type: 'language' });
const currencyName = new Intl.DisplayNames(['de'], { type: 'currency' });
const intl = (names, code) => { try { return names.of(code); } catch { return code; } };
const big = (n) => (n >= 1e12 ? `${num(n / 1e12, 2)} Bio.` : n >= 1e9 ? `${num(n / 1e9, 1)} Mrd.`
  : n >= 1e6 ? `${num(n / 1e6, 1)} Mio.` : Math.round(n).toLocaleString('de-DE'));
const year = (c, key) => (c[`${key}_year`] ? ` (${c[`${key}_year`]})` : '');

async function showCountry(iso, lngLat) {
  showPopup(lngLat, h('div', { className: 'meta', textContent: 'Lade Länderdaten …' }));
  const d = await api(`/api/country?iso=${encodeURIComponent(iso)}${state.at ? `&at=${state.at}` : ''}`);
  const c = d.info;
  const facts = c ? h('table', {},
    row('Hauptstadt', c.capital), row('Kontinent', c.continent),
    row('Einwohner', c.population ? `${big(c.population)}${year(c, 'population')}` : null),
    row('Fläche', c.area_km2 ? `${Math.round(c.area_km2).toLocaleString('de-DE')} km²` : null),
    row('Einwohner je km²', c.population && c.area_km2 ? num(c.population / c.area_km2) : null),
    row('Wirtschaftsleistung', c.gdp ? `${big(c.gdp)} US-$${year(c, 'gdp')}` : null),
    row('je Einwohner', c.gdp_capita ? `${Math.round(c.gdp_capita).toLocaleString('de-DE')} US-$` : null),
    row('Lebenserwartung', c.life_expectancy ? `${num(c.life_expectancy, 1)} Jahre${year(c, 'life_expectancy')}` : null),
    row('Sprachen', c.languages.map((l) => intl(languageName, l.split('-')[0])).join(', ')),
    row('Währung', c.currency ? `${intl(currencyName, c.currency)} (${c.currency})` : null),
    row('Vorwahl · Internet', [c.phone, c.tld].filter(Boolean).join(' · ')),
    row('Nachbarländer', c.neighbours.map((n) => intl(regionName, n)).join(', ')))
    : h('div', { className: 'meta', textContent: 'Länderdaten werden beim nächsten Abruf geladen (Internet nötig).' });
  const r = d.risk;
  const situation = r ? `Risiko-Index ${num(r.score)} / 100 · ${r.events} ${r.events === 1 ? 'Ereignis' : 'Ereignisse'} in 7 Tagen · `
    + `höchste Stufe ${SEVERITY[r.max_severity]}` : 'Keine bewerteten Ereignisse in den letzten 7 Tagen.';
  if (!popup.isOpen()) return; // inzwischen geschlossen
  showPopup(lngLat, h('div', {}, h('h3', { textContent: d.name }), facts,
    h('h4', { textContent: 'Lage' }), h('div', { className: 'meta', textContent: situation }),
    ...d.events.map((ev) => eventNode({ ...ev, since: ev.started ?? ev.ts }, true)),
    c ? h('p', { className: 'meta', textContent: 'Daten: GeoNames, Weltbank' }) : ''));
}

// ------------------------------------------------------------------- Orte
// DWD schreibt Zusätze nach hinten: „Salzuflen, Bad“ -> „Bad Salzuflen“
const dwdName = (name) => name.replace(/^([^,]+), (Bad|St\.|Sankt|Hohe|Kap)$/, '$2 $1');
const shortName = (name) => (name || '').split(',')[0]; // „Spenge, Stadt“ -> „Spenge“
const yearText = (y) => (y < 0 ? `${-y} v. Chr.` : String(y));
const localTime = (zone) => { try {
  return new Date().toLocaleTimeString('de-DE', { timeZone: zone, hour: '2-digit', minute: '2-digit' });
} catch { return null; } };

/** Orts-Popup; query: "id=<GeoNames-Nummer>" oder "name=…&lat=…&lon=…" (Klick auf eine Kartenbeschriftung). */
async function showPlace(query, lngLat) {
  showPopup(lngLat, h('div', { className: 'meta', textContent: 'Lade Ortsdaten …' }));
  const response = await fetch(`/api/place?${query}${state.at ? `&at=${state.at}` : ''}`);
  const d = await response.json();
  if (!popup.isOpen()) return; // inzwischen geschlossen
  if (d.error) { showPopup(lngLat, h('div', { className: 'meta', textContent: d.error })); return; }
  const { place: p, gemeinde: g, kreis: k, city: c } = d;
  const district = k && g && k.kind !== 'kreisfreie Stadt' && k.kind !== 'Stadtkreis' ? `${k.kind} ${shortName(k.name)}` : '';
  const part = g && shortName(g.name) !== p.name && !['PPLA', 'PPLA2', 'PPLC'].includes(p.code)
    ? `Ortsteil von ${shortName(g.name)}` : '';
  const population = g?.population ?? p.population;
  const area = g?.area_km2 ?? c?.area_km2;
  const elevation = g?.elevation ?? p.elevation;
  const facts = h('table', {},
    row(part ? `Einwohner ${shortName(g.name)}` : 'Einwohner', population
      ? `${population.toLocaleString('de-DE')}${g ? ` (${d.stand})` : ''}` : null),
    row('davon', g?.male ? `${g.male.toLocaleString('de-DE')} Männer, ${g.female.toLocaleString('de-DE')} Frauen` : null),
    row('Fläche', area ? `${num(area, 1)} km²` : null),
    row('Einwohner je km²', population && area ? num(population / area) : null),
    row('Postleitzahl', g?.plz), row('Vorwahl', g?.phone?.join(', ')),
    row('Kfz-Kennzeichen', k?.plates?.length ? k.plates.join(', ') : null),
    row('Höhe', elevation !== null && elevation !== undefined ? `${num(elevation)} m` : null),
    row('Gegründet', c?.founded ? yearText(c.founded) : null),
    row('Bürgermeister/in', g?.mayor ?? c?.mayor),
    row('Partnerstädte', (g?.partners ?? c?.partners)?.join(', ')),
    row('Besiedlung', g?.urbanisation), row('Reisegebiet', g?.region),
    row('Ortszeit', p.timezone && p.iso !== 'DEU' ? `${localTime(p.timezone)} (${p.timezone})` : null),
    row('Lage', `${num(p.lat, 4)}, ${num(p.lon, 4)}`));
  const web = g?.web ?? c?.web;
  const nearby = [];
  if (d.station) {
    nearby.push(row('Temperatur', `${num(d.station.temp, 1)} °C – ${dwdName(d.station.name)} (${num(d.station.km)} km, ${ago(d.station.ts)})`));
  }
  if (d.radiation) {
    nearby.push(row('Strahlung', `${num(d.radiation.value, 2)} µSv/h, ${RADIATION_STATUS[d.radiation.status]} – `
      + `${d.radiation.name} (${num(d.radiation.km)} km)`));
  }
  if (d.radon) {
    nearby.push(row('Radon (Gemeinde)', `${d.radon.bq ?? '–'} Bq/m³${d.radon.area ? ' · Radon-Vorsorgegebiet' : ''}`));
  }
  showPopup(lngLat, h('div', {},
    h('h3', { textContent: p.name }),
    h('div', { className: 'meta', textContent: [part || g?.type || p.kind, district, p.region, p.country]
      .filter(Boolean).join(' · ') }),
    p.ags && !g ? h('p', { className: 'meta', textContent: d.cityinfo ? 'Für diesen Ort liegen keine Gemeindedaten vor.'
      : 'Gemeindedaten werden gerade geladen (beim ersten Start 2–3 Minuten) – gleich noch einmal anklicken.' }) : '',
    facts, web ? h('p', {}, link(web, `${web.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '')} ↗`)) : '',
    h('h4', { textContent: 'In der Nähe' }),
    nearby.length ? h('table', {}, ...nearby) : '',
    d.events.length ? h('div', {}, ...d.events.map((ev) => eventNode({ ...ev, since: ev.started ?? ev.ts }, true)))
      : h('div', { className: 'meta', textContent: 'Keine Meldungen im Umkreis von 30 km in den letzten 7 Tagen.' }),
    h('p', { className: 'meta', textContent: `Daten: ${g ? 'Statistisches Bundesamt, ' : ''}`
      + `${g || c ? 'Wikidata, ' : ''}GeoNames` })));
}

// ----------------------------------------------------------------- Seitenleiste
function renderLayers() {
  const counts = {
    news: state.events.filter((e) => e.category === 'news').length,
    conflict: state.conflicts.length,
    disaster: state.events.filter((e) => e.category === 'disaster').length,
    weather: state.events.filter((e) => e.category === 'weather').length,
    stations: state.stations.length,
    radiation: state.radiation.filter((r) => r.status === 3).length
      ? `${state.radiation.filter((r) => r.status === 3).length} rot` : state.radiation.filter((r) => r.status).length,
    aircraft: state.vehicles.filter((v) => v.kind === 'aircraft').length,
    ship: state.vehicles.filter((v) => v.kind === 'ship').length,
  };
  const hints = {
    conflict: 'letzte 7 Tage, unabhängig vom Zeitraum – automatisch aus Nachrichten erkannt',
    terrain: state.status?.terrain?.length ? '' : 'Höhendaten unter Einstellungen (Zahnrad) laden',
    radar: state.status?.weathermaps ? 'Deutschland, 2 Std. zurück bis 2 Std. voraus – Zeitleiste unten'
      : 'wird geladen (wenige Minuten)',
    clouds: state.status?.weathermaps ? 'Mitteleuropa, letzte 3 Stunden' : 'wird geladen (wenige Minuten)',
    wind: state.status?.wind ? 'Mitteleuropa, bis 48 Std. voraus' : 'wird geladen (wenige Minuten)',
    ship: state.status?.sources.find((s) => s.name === 'ships')?.ok ? ''
      : 'API-Schlüssel unter Einstellungen (Zahnrad) eintragen',
    ...Object.fromEntries(Object.entries(STATIC_FILES).map(([name, [file, tool, source]]) => {
      if (state.status?.[file]) return [name, ''];
      // Abruf fehlgeschlagen? Dann den Grund statt „wird geladen“
      const failedSource = state.status?.sources.find((s) => s.name === source && s.last_run && !s.ok);
      return [name, failedSource ? `noch nicht geladen – ${failedSource.message}`
        : tool.startsWith('tools/') ? `${tool} ausführen` : tool];
    })),
  };
  // erklärende Hinweise nur bei eingeschalteter Ebene; Hinweise, was fehlt oder zu tun ist, immer
  const infoOnly = new Set(['conflict', 'radar', 'clouds', 'wind']);
  const item = (name) => {
    const layer = LAYERS[name];
    const box = h('input', { type: 'checkbox', checked: state.visible[name] });
    box.addEventListener('change', () => setLayer(name, box.checked));
    const hint = hints[name] && (!infoOnly.has(name) || state.visible[name]) ? hints[name] : '';
    return h('li', {},
      h('label', {}, box, h('span', { className: 'swatch', style: { borderColor: layer.color } }), layer.label,
        h('span', { className: 'count', textContent: counts[name] ?? '' })),
      hint ? h('div', { className: 'hint', textContent: hint }) : '');
  };
  const grouped = new Set(GROUPS.flatMap(([, , names]) => names));
  const groups = [...GROUPS, ['weitere', 'Weitere', Object.keys(LAYERS).filter((name) => !grouped.has(name))]]
    .map(([key, label, names]) => [key, label, names.filter((name) => LAYERS[name])]).filter(([, , names]) => names.length);
  const open = new Set(state.openGroups
    ?? groups.filter(([, , names]) => names.some((name) => state.visible[name])).map(([key]) => key));
  $('#layers').replaceChildren(...groups.map(([key, label, names]) => {
    const active = names.filter((name) => state.visible[name]);
    const head = h('button', { type: 'button', className: 'group-head', ariaExpanded: String(open.has(key)) },
      h('span', { className: 'arrow', textContent: open.has(key) ? '▾' : '▸' }), label,
      h('span', { className: 'count', textContent: active.length ? `${active.length} an` : '' }));
    head.addEventListener('click', () => {
      if (open.has(key)) open.delete(key); else open.add(key);
      state.openGroups = [...open];
      persist();
      renderLayers();
    });
    const off = active.length > 1 ? h('button', { type: 'button', className: 'group-off', textContent: 'alle aus',
      title: `Alle Ebenen in „${label}“ ausschalten`, onclick: () => active.forEach((name) => setLayer(name, false)) }) : '';
    return h('li', { className: `group${open.has(key) ? ' open' : ''}` }, h('div', { className: 'group-row' }, head, off),
      open.has(key) ? h('ul', {}, ...names.map(item)) : '');
  }));
}

/** Ebene ein- oder ausschalten: Karte, Daten, Legende, Lage und Menü nachziehen. */
function setLayer(name, on) {
  state.visible[name] = on;
  persist();
  applyVisibility(name);
  const loader = LOADERS[name];
  if (loader && on) {
    seen.delete(loader.key);
    loader.load().catch(failed);
  }
  renderLegend();
  renderFeed();
  renderLayers();
}

/** Legende: welche Symbole gerade auf der Karte liegen und was sie bedeuten. */
const legendIcons = new Map();
function renderLegend() {
  const counts = new Map();
  for (const ev of [...state.events, ...state.conflicts]) {
    if (!state.visible[ev.category]) continue;
    const key = `${ev.category}:${kindOfRank(rankOf(ev))}`;
    const entry = counts.get(key) ?? { category: ev.category, kind: kindOfRank(rankOf(ev)), count: 0, severity: 0 };
    entry.count += 1;
    entry.severity = Math.max(entry.severity, ev.severity);
    counts.set(key, entry);
  }
  const small = (key, draw) => {
    if (!legendIcons.has(key)) legendIcons.set(key, draw().toDataURL());
    return h('img', { className: 'symbol', src: legendIcons.get(key), alt: '' });
  };
  const items = [...counts.values()].sort((a, b) => b.severity - a.severity || b.count - a.count).map((entry) =>
    h('li', {}, small(`${entry.kind}|${entry.severity}|${entry.category}`,
      () => eventIcon(entry.kind, entry.severity, LAYERS[entry.category].color)),
      h('span', { textContent: KIND_LABEL[entry.kind] }), h('span', { className: 'count', textContent: entry.count })));
  if (state.visible.radiation) {
    const dot = (color) => h('span', { className: 'symbol dot', style: { background: color } });
    items.push(...[1, 2, 3].map((s) => h('li', {}, dot(RADIATION_COLORS[s]), h('span', { textContent: `Strahlung: ${RADIATION_STATUS[s]}` }),
      h('span', { className: 'count', textContent: state.radiation.filter((r) => r.status === s).length }))));
  }
  if (state.visible.radon && state.status?.radon) {
    const box = (color) => h('span', { className: 'symbol dot', style: { background: color, borderRadius: '2px' } });
    items.push(...[1, 2, 3].map((s) => h('li', {}, box(RADIATION_COLORS[s]), h('span', { textContent: `Radon: ${RADON_STATUS[s]}` }))),
      h('li', {}, h('span', { className: 'symbol dot', style: { background: 'transparent', border: `2px dashed ${RADIATION_COLORS[3]}`,
        borderRadius: '2px' } }), h('span', { textContent: 'Radon-Vorsorgegebiet' })));
  }
  const dot = (color) => h('span', { className: 'symbol dot', style: { background: color } });
  if (state.visible.masts && state.status?.masts) {
    items.push(h('li', {}, dot(MAST_COLORS.mobile), h('span', { textContent: 'Mobilfunkmast' })),
      h('li', {}, dot(MAST_COLORS.other), h('span', { textContent: 'Funkmast (Rundfunk, Richtfunk, unbekannt)' })));
  }
  if (state.visible.bunkers && state.status?.bunkers) {
    items.push(h('li', {}, dot(BUNKER_COLORS.intact), h('span', { textContent: 'Bunker' })),
      h('li', {}, dot(BUNKER_COLORS.lost), h('span', { textContent: 'Bunker, aufgegeben / Ruine' })));
  }
  if (state.visible.radar && state.status?.weathermaps) {
    const shade = (color) => h('span', { className: 'symbol dot', style: { background: color, borderRadius: '2px' } });
    items.push(...[['#9be1ff', 'Regen leicht'], ['#1f6fd1', 'Regen mäßig'], ['#2fbf3b', 'Regen stark'],
      ['#f5d90a', 'Regen sehr stark'], ['#e53935', 'Starkregen, Hagel möglich']]
      .map(([color, label]) => h('li', {}, shade(color), h('span', { textContent: `Radar: ${label}` }))));
  }
  if (state.visible.wind && state.status?.wind) {
    const line = (color) => h('span', { className: 'symbol dot', style: { background: 'transparent', borderRadius: 0,
      height: 0, borderTop: `2px solid ${color}` } });
    items.push(...[[LIGHT() ? '#1e40af' : '#ffffff', 'Wind bis 8 m/s (Bft 4)'], ['#eab308', 'Wind bis 14 m/s (Bft 6)'],
      ['#f97316', 'Wind bis 20 m/s (Sturm, Bft 8)'], ['#ef4444', 'Wind über 20 m/s (schwerer Sturm)']]
      .map(([color, label]) => h('li', {}, line(color), h('span', { textContent: label }))));
  }
  if (state.visible.cables && state.status?.cables) {
    const line = (style) => h('span', { className: 'symbol dot', style: { background: 'transparent', borderRadius: 0,
      height: 0, borderTop: `2px ${style} ${LAYERS.cables.color}` } });
    items.push(h('li', {}, line('solid'), h('span', { textContent: 'Seekabel in Betrieb (Farbe je Kabel)' })),
      h('li', {}, line('dashed'), h('span', { textContent: 'Seekabel geplant / im Bau' })),
      h('li', {}, h('span', { className: 'symbol dot', style: { background: '#0b0f14',
        border: `2px solid ${LAYERS.cables.color}` } }), h('span', { textContent: 'Anlandepunkt' })));
  }
  if (state.visible.ixp && state.status?.ixp) {
    items.push(h('li', {}, dot(LAYERS.ixp.color), h('span', { textContent: 'Internetknoten – größer = mehr Netze' })));
  }
  if (state.visible.datacenters && state.status?.datacenters) {
    items.push(h('li', {}, dot(LAYERS.datacenters.color), h('span', { textContent: 'Rechenzentrum – größer = mehr Netze' })));
  }
  if (state.visible.nuclear && state.status?.nuclear) {
    items.push(...['operational', 'suspended', 'construction', 'planned', 'shutdown'].map((key) =>
      h('li', {}, dot(NUCLEAR[key][1]), h('span', { textContent: `Kernkraftwerk ${NUCLEAR[key][0]}` }))));
  }
  if (state.visible.water && state.status?.water) {
    items.push(...[[WATER_COLORS.drinking, 'Trinkwasser (laut Eintrag)'], [WATER_COLORS.spring, 'Quelle'],
      [WATER_COLORS.well, 'Brunnen'], [WATER_COLORS.other, 'sonstige Wasserstelle']]
      .map(([color, label]) => h('li', {}, dot(color), h('span', { textContent: label }))));
  }
  if (state.visible.telecom && state.status?.telecom) {
    items.push(h('li', {}, h('span', { className: 'symbol dot', style: { background: 'transparent', borderRadius: 0,
      height: 0, borderTop: `2px solid ${LAYERS.telecom.color}` } }), h('span', { textContent: 'Fernmelde-/Glasfaserleitung' })));
  }
  if (state.status?.bases) {
    items.push(...Object.entries(BASES).filter(([type]) => state.visible[type === 'barracks' ? 'barracks' : 'bases'])
      .map(([type, [label]]) => h('li', {}, small(`base|${type}`, () => baseIcon(type)), h('span', { textContent: label }))));
  }
  $('#legend').replaceChildren(...(items.length ? items
    : [h('li', { className: 'muted', textContent: 'Nichts eingeschaltet – Ebenen oben auswählen.' })]));
}

function renderRisk() {
  $('#risk').replaceChildren(...state.risk.slice(0, 12).map((r) => {
    const color = SEVERITY_COLOR[r.score >= 70 ? 4 : r.score >= 45 ? 3 : r.score >= 20 ? 2 : 1];
    const item = h('li', { title: `${r.events} Ereignisse` },
      h('span', { textContent: r.name }), h('span', { className: 'score', textContent: Math.round(r.score) }),
      h('span', { className: 'bar' }, h('b', { style: { width: `${r.score}%`, background: color } })));
    if (r.lat !== undefined) item.addEventListener('click', () => map.flyTo({ center: [r.lon, r.lat], zoom: 4 }));
    return item;
  }));
  if (!state.risk.length) {
    $('#risk').replaceChildren(h('li', { className: 'muted',
      textContent: state.visible.risk ? 'Noch keine Daten.' : 'Ebene „Länder-Risiko“ einschalten.' }));
  }
}

function renderStatus() {
  const s = state.status;
  const pill = $('#status');
  if (!s) {
    pill.className = 'pill offline';
    pill.textContent = 'Server nicht erreichbar';
    return;
  }
  const ran = s.sources.filter((x) => x.enabled && x.last_run);
  const lastOk = Math.max(0, ...s.sources.map((x) => x.last_ok || 0));
  const online = ran.some((x) => x.ok && x.name !== 'ships');
  pill.className = `pill ${state.at ? 'history' : online ? 'online' : 'offline'}`;
  pill.textContent = state.at ? `Rückblick · ${dateTime(state.at)}` : !s.collecting ? `Nur Anzeige · Stand ${lastOk ? ago(lastOk) : 'unbekannt'}`
    : online ? `Online · Stand ${ago(lastOk)}`
      : lastOk ? `Offline · gespeicherter Stand ${ago(lastOk)}` : 'Offline · noch keine Daten';

  $('#sources').replaceChildren(...s.sources.map((x) => h('li', {},
    h('span', { className: `dot ${!x.last_run ? '' : x.ok ? 'ok' : 'fail'}` }),
    h('span', { textContent: SOURCE_LABEL[x.name] || x.name }),
    h('span', { className: 'age', textContent: x.last_ok ? ago(x.last_ok) : '–' }),
    x.last_run && !x.ok ? h('span', { className: 'msg', textContent: x.message }) : '')));
  $('#dbinfo').textContent = `Datenbank: ${(s.db_bytes / 1e6).toFixed(1)} MB · ${s.events} Ereignisse`
    + `${s.history_since ? ` · Verlauf seit ${date(s.history_since)}` : ''}`
    + `${s.map ? '' : ' · Offline-Karte fehlt (tools/fetch_map.py)'}`;
  if (s.history_since) $('#at').min = new Date(s.history_since * 1000 - new Date().getTimezoneOffset() * 60000)
    .toISOString().slice(0, 16);
}

function renderFeed() {
  const query = state.search.trim().toLowerCase();
  const searching = Array.isArray(state.searchResults);
  const matching = searching ? state.searchResults : state.events.filter((ev) => state.visible[ev.category]
    && (!query || `${ev.title} ${ev.summary || ''} ${ev.place || ''} ${ev.source}`.toLowerCase().includes(query)));
  // DWD-Warnungen gelten je Landkreis: gleichartige zu einem Eintrag zusammenfassen
  const groups = new Map();
  const items = [];
  for (const ev of matching) {
    const key = isDwd(ev) && !searching ? `${ev.title.split(' – ')[0]}|${ev.severity}|${lifted(ev)}` : null;
    if (key && groups.has(key)) { groups.get(key).places.push(ev.place); continue; }
    const entry = key ? { ...ev, places: [ev.place] } : ev;
    if (key) groups.set(key, entry);
    items.push(entry);
  }
  for (const group of groups.values()) {
    if (group.places.length < 2) continue;
    group.title = `${group.title.split(' – ')[0]} – ${group.places.length} Warngebiete`;
    group.summary = `${group.places.slice(0, 12).join(', ')}${group.places.length > 12 ? ' …' : ''}`;
    group.place = 'Deutschland';
    Object.assign(group, { lon: GERMANY.center[0], lat: GERMANY.center[1], grouped: true });
  }
  $('#feed-count').textContent = searching ? '' : `${items.length} Einträge`;
  const note = searching ? h('li', { className: 'feed-note' },
    h('span', { textContent: `${items.length}${items.length >= 300 ? '+' : ''} Treffer im gesamten Verlauf` }),
    h('button', { textContent: 'Suche beenden', onclick: () => { state.searchResults = null; renderFeed(); } })) : null;
  const nodes = items.slice(0, 300).map((ev) => {
    const url = safeUrl(ev.url);
    const item = h('li', { className: `sev-${ev.severity}` },
      h('div', { className: 'title', textContent: ev.title }),
      h('div', { className: 'meta', textContent: [KIND_LABEL[ev.kind], ev.source, ev.place, ago(ev.since),
        lifted(ev) ? 'aufgehoben' : ''].filter(Boolean).join(' · ') }),
      h('div', { className: 'detail' }, badge(ev.severity),
        ev.summary ? h('p', { textContent: ev.summary }) : '', url ? h('p', {}, link(url, 'Quelle öffnen ↗')) : ''));
    item.addEventListener('click', (e) => {
      if (e.target.tagName === 'A') return;
      item.classList.toggle('open');
      if (!item.classList.contains('open')) return;
      if (ev.grouped) {
        map.flyTo(GERMANY);
      } else if (ev.lat !== null) {
        map.flyTo({ center: [ev.lon, ev.lat], zoom: Math.max(map.getZoom(), 4) });
        showPopup([ev.lon, ev.lat], eventNode(ev));
      }
    });
    return item;
  });
  $('#feed').replaceChildren(...[note, ...(nodes.length ? nodes
    : [h('li', { className: 'empty', textContent: searching ? 'Nichts gefunden.'
      : ['news', 'disaster', 'weather'].some((n) => state.visible[n]) ? 'Keine Einträge für diese Auswahl.'
        : 'Links Nachrichten, Katastrophen oder Wetter einschalten.' })])]
    .filter(Boolean));
}

// ------------------------------------------------------------------ Daten laden
function index() {
  state.byId = new Map([...state.events, ...state.conflicts, ...state.vehicles].map((x) => [x.id, x]));
}

/** Ebene aus: Daten verwerfen (gibt Speicher in Karte und Browser frei) und nichts abfragen. */
function unload(key, sources, field) {
  if (!state[field].length && !seen.has(key)) return;
  seen.delete(key);
  state[field] = [];
  for (const source of sources) map.getSource(source)?.setData(EMPTY);
  index();
  renderLayers();
  renderLegend();
}

async function loadConflicts() {
  if (!state.visible.conflict) return unload('conflicts', ['conflict'], 'conflicts');
  const data = await apiIfChanged('conflicts', `/api/conflicts${atQuery('?')}`);
  if (!data) return;
  const { columns, rows } = data;
  state.conflicts = rows.map((row) => {
    const ev = Object.fromEntries(columns.map((name, i) => [name, row[i]]));
    return { ...ev, source: 'GDELT', category: 'conflict', since: ev.ts };
  });
  index();
  state.conflicts.forEach((ev) => ensureIcon('conflict', rankOf(ev)));
  map.getSource('conflict').setData(featureCollection(state.conflicts, (ev) => ({ id: ev.id, rank: rankOf(ev) })));
  renderLayers();
  renderLegend();
}

async function loadRisk() {
  if (!state.visible.risk) {
    seen.delete('risk');
    state.risk = [];
    map.removeFeatureState({ source: 'countries' });
    renderRisk();
    return;
  }
  const risk = await apiIfChanged('risk', `/api/risk${atQuery('?')}`);
  if (!risk) return;
  state.risk = risk.countries;
  map.removeFeatureState({ source: 'countries' });
  for (const r of state.risk) map.setFeatureState({ source: 'countries', id: r.iso }, { score: r.score });
  renderRisk();
}

async function loadEvents() {
  // Meldungen speisen Karte und Lageliste von Nachrichten, Katastrophen und Wetter
  if (!['news', 'disaster', 'weather'].some((name) => state.visible[name])) {
    unload('events', ['news', 'disaster', 'weather-alert'], 'events');
    renderFeed();
    return;
  }
  const events = await apiIfChanged('events',
    `/api/events?hours=${state.hours}&min_severity=${state.minSeverity}${atQuery()}`);
  if (!events) return;
  // Warnungen, die seit Tagen gelten, stehen nach ihrem Beginn in der Liste - nicht nach dem letzten Abruf
  state.events = events.events.map((ev) => ({ ...ev, since: ev.started ?? ev.ts })).sort((a, b) => b.since - a.since);
  index();

  // DWD-Warnungen erscheinen als eingefärbte Warngebiete; nur ohne deren Umrisse als Punkte
  addDwdCells();
  updateNinaAreas();
  // Die Warnflächen der NINA-Datei zeigen nur den aktuellen Stand; im Rückblick bleiben die Punkte
  if (map.getLayer('nina-areas')) {
    map.setLayoutProperty('nina-areas', 'visibility', state.visible.disaster && !state.at ? 'visible' : 'none');
  }
  const cells = Boolean(map.getSource('dwd-cells'));
  const located = state.events.filter((e) => e.lat !== null && !lifted(e) && !(cells && isDwd(e)));
  const props = (e) => ({ id: e.id, rank: rankOf(e) });
  for (const [source, category] of [['news', 'news'], ['disaster', 'disaster'], ['weather-alert', 'weather']]) {
    const events = located.filter((e) => e.category === category);
    events.forEach((e) => ensureIcon(category, rankOf(e))); // Bündel tragen den Rang eines ihrer Mitglieder
    map.getSource(source).setData(featureCollection(events, props));
  }
  if (cells) {
    map.removeFeatureState({ source: 'dwd-cells' });
    const levels = new Map();
    for (const ev of state.events.filter((e) => isDwd(e) && !lifted(e))) levels.set(dwdCell(ev), Math.max(levels.get(dwdCell(ev)) ?? 0, ev.severity));
    for (const [id, level] of levels) map.setFeatureState({ source: 'dwd-cells', id }, { level });
  }

  renderLayers();
  renderLegend();
  renderFeed();
}

async function loadVehicles() {
  if (!state.visible.aircraft && !state.visible.ship) return unload('vehicles', ['aircraft', 'ships'], 'vehicles');
  // im Rückblick: letzte gespeicherte Position vor dem gewählten Zeitpunkt
  const data = await apiIfChanged('vehicles', `/api/vehicles${atQuery('?')}`);
  if (!data) return;
  state.vehicles = data.vehicles;
  index();
  const emergency = new Set(['7500', '7600', '7700']);
  for (const [source, kind] of [['aircraft', 'aircraft'], ['ships', 'ship']]) {
    map.getSource(source).setData(featureCollection(state.vehicles.filter((v) => v.kind === kind), (v) => ({
      id: v.id, heading: v.heading,
      icon: emergency.has(v.squawk) ? 'plane-alert' : v.alt === 0 ? 'plane-ground' : 'plane',
    })));
  }
  renderLayers();
}

async function loadWeather() {
  if (!state.visible.weather) return unload('weather', ['weather'], 'weather');
  const data = await apiIfChanged('weather', `/api/weather${atQuery('?')}`);
  if (!data) return;
  state.weather = data.weather;
  map.getSource('weather').setData(featureCollection(state.weather.filter((w) => w.temp !== null),
    (w) => ({ place: w.place, temp: w.temp })));
  renderLayers();
}

async function loadStations() {
  if (!state.visible.stations) return unload('stations', ['stations'], 'stations');
  const data = await apiIfChanged('stations', `/api/stations${atQuery('?')}`);
  if (!data) return;
  state.stations = data.stations;
  map.getSource('stations').setData(featureCollection(state.stations,
    (st) => (st.temp === null ? { id: st.id } : { id: st.id, temp: st.temp })));
  renderLayers();
}

async function loadRadiation() {
  if (!state.visible.radiation) return unload('radiation', ['radiation'], 'radiation');
  const data = await apiIfChanged('radiation', `/api/radiation${atQuery('?')}`);
  if (!data) return;
  state.radiation = data.radiation;
  map.getSource('radiation').setData(featureCollection(state.radiation, (r) => ({
    id: r.id, status: r.status, ...(r.value === null ? {} : { label: `${num(r.value, 2)} µSv/h` }) })));
  renderLayers();
  renderLegend();
}

async function loadStatus() {
  try {
    state.status = await api('/api/status');
    if (map) addDwdCells(); // Warngebiets-Datei kann erst nach dem ersten Abruf auftauchen
  } catch {
    state.status = null;
  }
  renderStatus();
}

/** Flug oder Schiff an einem Tag suchen und seine Spur zeigen. */
function bindVehicleSearch() {
  const today = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  $('#vday').value = today;
  $('#vday').max = today;
  const day = () => {
    const start = new Date(`${$('#vday').value || today}T00:00`).getTime() / 1000;
    return { from: start, to: start + 86400 };
  };
  const search = async () => {
    const query = $('#vsearch').value.trim();
    if (query.length < 2) return;
    const { from, to } = day();
    const { vehicles } = await api(`/api/vehicle_search?q=${encodeURIComponent(query)}&from=${from}&to=${to}`);
    $('#vresults').replaceChildren(...(vehicles.length ? vehicles.map((v) => {
      const item = h('li', {},
        h('div', { textContent: [v.callsign || v.reg || v.id, v.type, v.country].filter(Boolean).join(' · ') }),
        h('div', { className: 'meta', textContent: `${v.kind === 'ship' ? 'Schiff' : 'Flugzeug'} · ${clock(v.first)}–`
          + `${clock(v.last)} · ${v.points} Positionen${v.reg && v.callsign ? ` · ${v.reg}` : ''}` }));
      item.addEventListener('click', async () => {
        const { points } = await api(`/api/track?id=${encodeURIComponent(v.id)}&from=${from}&to=${to}`);
        if (!points.length) return;
        document.querySelectorAll('#vresults li.on').forEach((li) => li.classList.remove('on'));
        item.classList.add('on');
        showTrack(points);
        const lons = points.map((pt) => pt.lon);
        const lats = points.map((pt) => pt.lat);
        map.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]],
          { padding: 60, maxZoom: 9 });
        const last = points[points.length - 1];
        showPopup([last.lon, last.lat], h('div', {},
          h('h3', { textContent: v.callsign || v.reg || v.id }),
          h('div', { className: 'meta', textContent: `${dateTime(points[0].ts)} – ${clock(last.ts)}` }),
          h('table', {}, row('Staat', v.country), row('Typ', v.type),
            row(v.kind === 'ship' ? 'MMSI' : 'Kennzeichen', v.reg), row('Positionen', String(points.length)))));
      });
      return item;
    }) : [h('li', { className: 'muted', textContent: 'Nichts gefunden an diesem Tag.' })]));
  };
  $('#vsearch-go').addEventListener('click', search);
  $('#vsearch').addEventListener('keydown', (e) => { if (e.key === 'Enter') search(); });
  $('#track-clear').addEventListener('click', () => { clearTrack(); popup.remove(); });
}

// Welche Abfrage zu welcher Ebene gehört (Einschalten lädt sofort nach)
const LOADERS = {
  risk: { key: 'risk', load: () => loadRisk() },
  news: { key: 'events', load: () => loadEvents() },
  disaster: { key: 'events', load: () => loadEvents() },
  weather: { key: 'events', load: () => Promise.all([loadEvents(), loadWeather()]) },
  stations: { key: 'stations', load: () => loadStations() },
  radiation: { key: 'radiation', load: () => loadRadiation() },
  conflict: { key: 'conflicts', load: () => loadConflicts() },
  aircraft: { key: 'vehicles', load: () => loadVehicles() },
  ship: { key: 'vehicles', load: () => loadVehicles() },
  radar: { key: 'film', load: () => loadFilm() },
  clouds: { key: 'film', load: () => loadFilm() },
  wind: { key: 'wind', load: () => loadWind() },
};

/** Bilder des Wetterfilms (Verzeichnis) und das Windfeld - nur wenn die Ebene eingeschaltet ist. */
let filmLoaded = 0;
async function loadFilm() {
  if (!film || !(state.visible.radar || state.visible.clouds) || !state.status?.weathermaps) return;
  const manifest = await api('/weathermaps/frames.json');
  if (manifest.updated !== filmLoaded) film.setManifest(manifest);
  filmLoaded = manifest.updated;
}
let windLoaded = 0;
async function loadWind() {
  if (!film || !state.visible.wind || !state.status?.wind) return;
  const data = await api('/geo/wind.json');
  if (data.updated !== windLoaded) film.setWind(data);
  windLoaded = data.updated;
}

function reloadAll() {
  for (const task of [loadEvents, loadRisk, loadVehicles, loadWeather, loadStations, loadRadiation, loadConflicts]) {
    task().catch(failed);
  }
}

/** Regelmäßig neu laden; Fehler (Server kurz weg) dürfen die Schleife nicht beenden.
 *  Ist die Seite verdeckt (anderer Tab, Bildschirm aus), wird nicht abgefragt. */
function every(seconds, task) {
  const run = () => task().catch(failed);
  setInterval(() => { if (!document.hidden) run(); }, seconds * 1000);
  return run();
}
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && ready) { loadStatus(); reloadAll(); }
});
/** Fehlgeschlagener Abruf: beim nächsten Mal alles neu zeichnen, statt "unverändert" anzunehmen. */
function failed(err) {
  forgetSeen();
  console.warn(err);
}

// ---------------------------------------------------------------- Einstellungen
const MAP_SIZES = { 5: '15 MB', 6: '45 MB', 7: '190 MB', 8: '560 MB', 9: '1,6 GB', 10: '3,8 GB', 11: '8 GB',
  12: '18 GB', 13: 'über 18 GB', 14: 'über 18 GB', 15: '138 GB' };
const bytes = (n) => (n >= 1e9 ? `${(n / 1e9).toFixed(1).replace('.', ',')} GB` : `${Math.round(n / 1e6)} MB`);

function bindSettings() {
  const dialog = $('#settings');
  const reload = () => h('button', { textContent: 'Seite neu laden', onclick: () => location.reload(),
    style: { marginLeft: '8px' } });
  let timer = null;
  let wasRunning = false;

  const render = (m) => {
    if (m.error) { $('#map-message').textContent = m.error; return; }
    const running = m.job.state === 'running';
    $('#map-info').textContent = `${m.file
      ? `Weltkarte vorhanden: Detailstufe ${m.file.max_zoom} (${bytes(m.file.bytes)}). Zoomen geht immer beliebig `
        + 'tief; Details (Straßen, Gebäude) gibt es bis zur geladenen Stufe – oder unten einzelne Länder komplett laden.'
      : 'Keine Karte vorhanden – es werden nur Länderumrisse gezeigt.'} Freier Speicher: ${bytes(m.free_bytes)}.`;
    const select = $('#map-zoom');
    const chosen = Number(select.value) || m.file?.max_zoom || 7;
    select.replaceChildren(...Object.keys(MAP_SIZES).map(Number).filter((z) => z <= m.zoom_limit)
      .map((z) => h('option', { value: String(z), textContent: `Stufe ${z} – ${MAP_SIZES[z]}` })));
    select.value = String(Math.max(5, Math.min(chosen, m.zoom_limit)));
    renderRegions(m);
    renderTerrain(m);
    renderKeys(m.settings.api_keys);
    $('#history-days').value = String(m.settings.history_days);
    $('#track-days').value = String(m.settings.track_days);
    const s = state.status;
    $('#history-info').textContent = s ? `Gespeichert: ${bytes(s.db_bytes)} · ${s.events} Einträge`
      + `${s.history_since ? ` seit ${date(s.history_since)}` : ''}. Aufgeräumt wird stündlich.` : '';
    $('#map-progress').hidden = !running;
    if (running) {
      $('#map-progress progress').value = m.job.total ? m.job.done / m.job.total : 0;
      $('#map-progress span').textContent = m.job.total ? `${bytes(m.job.done)} / ${bytes(m.job.total)}` : '';
      wasRunning = true;
    }
    for (const id of ['#map-download', '#map-zoom', '#region-download', '#region-select', '#terrain-download',
      '#terrain-select']) $(id).disabled = running;
    if (running) {
      $('#map-progress span').textContent = `${m.job.target ?? 'Welt'}: ${m.job.total
        ? `${bytes(m.job.done)} / ${bytes(m.job.total)}` : m.job.message}`;
    }
    $('#map-delete').disabled = running || !m.file;
    $('#map-message').replaceChildren(m.job.message, wasRunning && m.job.state === 'done' ? reload() : '');
    clearTimeout(timer);
    if (running && dialog.open) timer = setTimeout(refresh, 1500);
  };
  const refresh = async () => render(await api('/api/map'));

  // Höhendaten: Welt grob und einzelne Länder in voller Auflösung
  let terrainShown = '';
  const renderTerrain = (m) => {
    const select = $('#terrain-select');
    if (!select.options.length) {
      select.replaceChildren(h('option', { value: 'WORLD', textContent: 'Ganze Welt, grob (rund 80 MB)' }),
        ...m.countries.map((c) => h('option', { value: c.iso, textContent: c.name })));
      select.value = m.terrain.some((t) => t.iso === 'WORLD') ? 'DEU' : 'WORLD';
    }
    const signature = JSON.stringify(m.terrain);
    if (signature === terrainShown) return;
    terrainShown = signature;
    $('#terrain').replaceChildren(...m.terrain.map((t) => h('li', {},
      h('span', { textContent: `${t.name} – Stufe ${t.max_zoom}, ${bytes(t.bytes)}` }),
      h('button', { className: 'danger', textContent: 'Löschen', onclick: async () => {
        if (!confirm(`Höhendaten von ${t.name} löschen?`)) return;
        const result = await send('DELETE', '/api/map/terrain', { iso: t.iso });
        render(result);
        if (!result.error) $('#map-message').replaceChildren(`Höhendaten ${t.name} gelöscht.`, reload());
      } }))));
  };

  // Länder: Auswahl und Liste der geladenen Länder (nur neu aufbauen, wenn sich etwas geändert hat)
  let regionsShown = '';
  const renderRegions = (m) => {
    const select = $('#region-select');
    if (!select.options.length) {
      select.replaceChildren(h('option', { value: '', textContent: 'Land wählen …' }),
        ...m.countries.map((c) => h('option', { value: c.iso, textContent: c.name })));
      select.value = 'DEU';
    }
    const signature = JSON.stringify(m.regions);
    if (signature === regionsShown) return;
    regionsShown = signature;
    $('#regions').replaceChildren(...m.regions.map((r) => h('li', {},
      h('span', { textContent: `${r.name} – Detailstufe ${r.max_zoom}, ${bytes(r.bytes)}` }),
      h('button', { className: 'danger', textContent: 'Löschen', onclick: async () => {
        if (!confirm(`Karte von ${r.name} löschen?`)) return;
        const result = await send('DELETE', '/api/map/region', { iso: r.iso });
        render(result);
        if (!result.error) $('#map-message').replaceChildren(`${r.name} gelöscht.`, reload());
      } }))));
  };


  // Eingabefelder nur neu aufbauen, wenn sich etwas geändert hat - sonst ginge Getipptes verloren
  let keysShown = '';
  const renderKeys = (keys) => {
    const source = (key) => state.status?.sources.find((s) => s.name === key.source);
    const signature = JSON.stringify(keys.map((key) => [key.name, key.set, key.tail, source(key)?.ok, source(key)?.message]));
    if (signature === keysShown) return;
    keysShown = signature;
    $('#api-keys').replaceChildren(...keys.map((key) => {
      const status = source(key);
      const input = h('input', { type: 'password', autocomplete: 'off', spellcheck: false,
        placeholder: key.set ? 'neuen Schlüssel eintragen' : 'Schlüssel eintragen' });
      const store = async (value) => {
        const result = await send('POST', '/api/settings', { api_keys: { [key.name]: value } });
        await loadStatus();
        render(result);
        setTimeout(async () => { await loadStatus(); refresh(); }, 6000); // Verbindung braucht einen Moment
      };
      const saveButton = h('button', { textContent: 'Speichern', onclick: () => input.value.trim() && store(input.value) });
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') saveButton.click(); });
      return h('div', { className: 'api-key' },
        h('div', { className: 'name' }, h('span', {}, `${key.label} – `, link(key.url, key.provider)),
          h('span', { className: `state ${key.set && status?.ok ? 'ok' : ''}`,
            textContent: key.set ? `gespeichert${key.tail ? ` (…${key.tail})` : ''}` : 'nicht eingetragen' })),
        h('div', { className: 'row' }, input, saveButton,
          key.set ? h('button', { className: 'danger', textContent: 'Entfernen', onclick: () => store('') }) : ''),
        h('p', { className: 'muted', textContent: key.set && status?.message
          ? `Status: ${status.message}` : key.hint }));
    }));
  };

  // Software-Update: Einspielen geht nur direkt am Gerät (localhost), weil dabei neuer Programmcode läuft
  const renderUpdate = (u) => {
    if (!u) return;
    if (!u.version) { $('#update-message').textContent = u.error || ''; return; } // abgelehnt (z. B. nicht lokal)
    const latest = u.latest;
    $('#update-version').textContent = `Installiert: Version ${u.version}.${latest
      ? (latest.newer ? ` Neu verfügbar: Version ${latest.version}${latest.date ? ` vom ${latest.date}` : ''}.`
        : ' Das ist die neueste Version.') : ''}`;
    if (document.activeElement !== $('#update-source')) $('#update-source').value = u.source;
    $('#update-notes').textContent = latest?.newer ? latest.notes || '' : '';
    $('#update-install').hidden = !latest?.newer;
    const busy = u.state === 'running' || u.state === 'restarting';
    for (const id of ['#update-source', '#update-source-save', '#update-check', '#update-install', '#update-upload']) {
      $(id).disabled = !u.local || busy;
    }
    $('#update-message').textContent = u.error || u.message
      || (u.local ? '' : 'Updates lassen sich nur direkt an diesem Gerät einspielen (http://localhost).');
    $('#open-settings').classList.toggle('has-update', Boolean(latest?.newer));
  };
  const waitForRestart = (old) => {
    const started = Date.now();
    const poll = async () => {
      try {
        if ((await api('/api/status')).version !== old) { location.reload(); return; }
      } catch { /* startet gerade neu */ }
      if (Date.now() - started < 180000) setTimeout(poll, 2000);
      else $('#update-message').textContent = 'OpenRisk meldet sich nicht zurück – bitte von Hand neu starten.';
    };
    setTimeout(poll, 3000);
  };
  const afterInstall = (u) => {
    renderUpdate(u);
    if (u.state === 'restarting') waitForRestart(u.version);
  };
  $('#update-source-save').addEventListener('click', async () =>
    renderUpdate(await send('POST', '/api/update/source', { source: $('#update-source').value })));
  $('#update-source').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('#update-source-save').click(); });
  $('#update-check').addEventListener('click', async () => {
    $('#update-message').textContent = 'Suche …';
    renderUpdate(await api('/api/update?check=2'));
  });
  $('#update-install').addEventListener('click', async () => {
    if (!confirm('Update jetzt installieren? OpenRisk startet danach neu.')) return;
    $('#update-message').textContent = 'Update wird eingespielt …';
    afterInstall(await send('POST', '/api/update/install'));
  });
  $('#update-upload').addEventListener('click', () => $('#update-file').click());
  $('#update-file').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    e.target.value = '';
    if (!file || !confirm(`${file.name} einspielen? OpenRisk startet danach neu.`)) return;
    $('#update-message').textContent = 'Update wird eingespielt …';
    const response = await fetch('/api/update/upload', { method: 'POST', body: file, headers: { 'X-OpenRisk': '1' } });
    afterInstall(await response.json());
  });
  setTimeout(() => api('/api/update?check=1').then(renderUpdate).catch(() => {}), 5000);

  $('#open-settings').addEventListener('click', () => {
    dialog.showModal();
    refresh();
    api('/api/update').then(renderUpdate).catch(() => {});
  });
  dialog.addEventListener('close', () => clearTimeout(timer));
  $('#map-download').addEventListener('click', async () => {
    const zoom = Number($('#map-zoom').value);
    if (zoom >= 10 && !confirm(`Stufe ${zoom} lädt ${MAP_SIZES[zoom]} herunter. Fortfahren?`)) return;
    render(await send('POST', '/api/map', { max_zoom: zoom }));
  });
  $('#map-cancel').addEventListener('click', async () => render(await send('POST', '/api/map/cancel')));
  $('#map-delete').addEventListener('click', async () => {
    if (!confirm('Offline-Karte wirklich löschen?')) return;
    const result = await send('DELETE', '/api/map');
    render(result);
    if (!result.error) $('#map-message').replaceChildren('Karte gelöscht.', reload());
  });
  $('#track-days').addEventListener('change', async (e) => {
    const days = Number(e.target.value);
    const label = e.target.selectedOptions[0].textContent;
    if (days && !confirm(`Flug- und Schiffsspuren, die älter als ${label} sind, werden gelöscht. Fortfahren?`)) {
      refresh();
      return;
    }
    render(await send('POST', '/api/settings', { track_days: days }));
  });
  $('#history-days').addEventListener('change', async (e) => {
    const days = Number(e.target.value);
    const label = e.target.selectedOptions[0].textContent;
    if (days && !confirm(`Einträge, die älter als ${label} sind, werden beim nächsten Aufräumen gelöscht. Fortfahren?`)) {
      refresh();
      return;
    }
    render(await send('POST', '/api/settings', { history_days: days }));
  });
  $('#map-style').value = state.mapStyle;
  $('#map-style').addEventListener('change', (e) => {
    state.mapStyle = e.target.value;
    persist();
    location.reload(); // Kartenstil gilt für alle Ebenen - neu aufbauen ist am einfachsten
  });
  $('#terrain-download').addEventListener('click', async () => {
    const select = $('#terrain-select');
    const name = select.selectedOptions[0]?.textContent;
    if (!select.value || !confirm(select.value === 'WORLD' ? 'Höhendaten der ganzen Welt (grob, rund 80 MB) laden?'
      : `Höhendaten für ${name} in voller Auflösung laden? Je nach Land einige 10 MB bis etwa 1 GB `
        + '(Deutschland rund 950 MB).')) return;
    render(await send('POST', '/api/map/terrain', { iso: select.value }));
  });
  $('#region-download').addEventListener('click', async () => {
    const select = $('#region-select');
    const name = select.selectedOptions[0]?.textContent;
    if (!select.value || !confirm(`${name} komplett in voller Detailstufe laden? Je nach Land einige 100 MB `
      + 'bis mehrere GB (Deutschland etwa 5–8 GB).')) return;
    render(await send('POST', '/api/map/region', { iso: select.value, max_zoom: 15 }));
  });
}

// ------------------------------------------------------------------------ Start
function bindControls() {
  $('#hours').value = String(state.hours);
  $('#min-severity').value = String(state.minSeverity);
  $('#hours').addEventListener('change', (e) => {
    state.hours = Number(e.target.value); persist(); forgetSeen(); loadEvents();
  });
  $('#min-severity').addEventListener('change', (e) => {
    state.minSeverity = Number(e.target.value); persist(); forgetSeen(); loadEvents();
  });
  let typing;
  $('#search').addEventListener('input', (e) => {
    state.search = e.target.value;
    clearTimeout(typing);
    typing = setTimeout(renderFeed, 200); // nicht bei jedem Tastendruck 300 Einträge neu aufbauen
  });
  const searchHistory = async () => {
    if (state.search.trim().length < 2) return;
    const { events } = await api(`/api/search?q=${encodeURIComponent(state.search.trim())}`);
    state.searchResults = events.map((ev) => ({ ...ev, since: ev.started ?? ev.ts }));
    renderFeed();
  };
  $('#search-history').addEventListener('click', searchHistory);
  $('#search').addEventListener('keydown', (e) => { if (e.key === 'Enter') searchHistory(); });

  // Rückblick: alle Ebenen zeigen den Stand zum gewählten Zeitpunkt
  const setMoment = (at) => {
    state.at = at;
    document.body.classList.toggle('history', Boolean(at));
    $('#live').hidden = !at;
    if (!at) $('#at').value = '';
    popup.remove();
    renderStatus();
    forgetSeen();
    reloadAll();
  };
  $('#at').addEventListener('change', (e) => {
    const at = e.target.value ? new Date(e.target.value).getTime() / 1000 : null;
    setMoment(at && at < Date.now() / 1000 - 60 ? at : null);
  });
  $('#live').addEventListener('click', () => setMoment(null));
  $('#refresh').addEventListener('click', async () => {
    await send('POST', '/api/refresh');
    setTimeout(() => { loadStatus(); reloadAll(); }, 6000);
  });
  for (const side of ['left', 'right']) {
    $(`#toggle-${side}`).addEventListener('click', () => {
      document.body.classList.toggle(`hide-${side}`);
      setTimeout(() => map.resize(), 0);
    });
  }
  if (innerWidth <= 1000) document.body.classList.add('hide-left', 'hide-right');
  const tick = () => {
    $('#clock').textContent = `${new Date().toLocaleTimeString('de-DE', { timeZone: 'UTC', hour: '2-digit', minute: '2-digit' })} UTC`;
  };
  tick();
  setInterval(tick, 10_000);
}

// ------------------------------------------------------------------- Ortssuche
function bindPlaceSearch() {
  const input = $('#place');
  const list = $('#place-results');
  let results = [];
  let active = -1;
  let typing;
  let pin;
  const describe = (p) => [p.kind, p.region, p.country].filter(Boolean).join(' · ');
  const close = () => { list.hidden = true; active = -1; };
  const show = (items) => {
    const box = input.getBoundingClientRect();
    list.style.left = `${Math.max(8, Math.min(box.left, innerWidth - list.offsetWidth - 8))}px`;
    list.style.top = `${box.bottom + 4}px`;
    list.replaceChildren(...items);
    list.hidden = false;
  };
  const go = (p) => {
    close();
    input.value = p.name;
    input.blur();
    if (!map) return;
    const zoom = p.kind === 'Land' ? 5 : p.population >= 1e6 ? 9 : p.population >= 1e5 ? 10 : 12;
    const at = [p.lon, p.lat];
    // Infos im gemeinsamen Karten-Popup; Klick auf die Nadel öffnet es wieder (nicht an die Karte weiterreichen,
    // sonst öffnet sich das Popup eines Punkts darunter)
    const open = () => {
      if (p.code === 'country' && p.iso) showCountry(p.iso, at).catch(failed);
      else if (p.id) showPlace(`id=${p.id}`, at).catch(failed);
      else showPopup(at, h('div', {}, h('h3', { textContent: p.name }), h('div', { className: 'meta', textContent: describe(p) })));
    };
    const element = h('div', { className: 'place-pin', title: p.name });
    for (const type of ['mousedown', 'mouseup']) element.addEventListener(type, (e) => e.stopPropagation());
    element.addEventListener('click', (e) => { e.stopPropagation(); open(); });
    pin?.remove();
    pin = new Marker({ element }).setLngLat(at).addTo(map);
    map.flyTo({ center: at, zoom: Math.min(zoom, map.getMaxZoom()), speed: 1.6 });
    open(); // wandert beim Flug mit
  };
  const highlight = (i) => {
    active = i;
    [...list.children].forEach((li, n) => li.classList.toggle('active', n === i));
    list.children[i]?.scrollIntoView({ block: 'nearest' });
  };
  const lookup = async () => {
    const q = input.value.trim();
    if (q.length < 2) { close(); return; }
    const data = await api(`/api/places?q=${encodeURIComponent(q)}`);
    if (input.value.trim() !== q) return; // inzwischen weitergetippt
    results = data.places;
    const items = results.map((p, i) => h('li', { role: 'option', onmousedown: (e) => { e.preventDefault(); go(p); },
      onmousemove: () => highlight(i) }, p.name, h('small', { textContent: describe(p)
        + (p.population ? ` · ${p.population.toLocaleString('de-DE')} Einw.` : '') })));
    if (!results.length) items.push(h('li', { className: 'note', textContent: 'Kein Ort gefunden.' }));
    if (!data.ready) items.push(h('li', { className: 'note', textContent: 'Ortsverzeichnis wird noch geladen – '
      + 'bis dahin nur Länder und große Städte.' }));
    show(items);
    highlight(results.length ? 0 : -1);
  };
  input.addEventListener('input', () => { clearTimeout(typing); typing = setTimeout(() => lookup().catch(failed), 150); });
  input.addEventListener('focus', () => { if (results.length && input.value.trim().length >= 2) lookup().catch(failed); });
  input.addEventListener('blur', () => setTimeout(close, 150));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (results.length) highlight((active + (e.key === 'ArrowDown' ? 1 : results.length - 1)) % results.length);
    } else if (e.key === 'Enter') {
      e.preventDefault(); // sonst „drückt“ dieselbe Taste den Schließen-Knopf des eben geöffneten Popups
      if (results[active]) go(results[active]);
    } else if (e.key === 'Escape') {
      close();
      if (!input.value) pin?.remove();
    }
  });
  input.addEventListener('search', () => { if (!input.value) { close(); pin?.remove(); pin = null; } });
}

async function main() {
  bindControls();
  bindPlaceSearch();
  await loadStatus();
  bindSettings();
  await createMap(state.status?.map, state.status?.map_regions ?? []);
  bindVehicleSearch();
  ready = true;
  // Seitenleiste auch dann aufbauen, wenn (noch) keine Ebene eingeschaltet ist und nichts geladen wird
  renderLayers();
  renderLegend();
  renderRisk();
  renderFeed();
  // Takt nach den Sammlern: Flugzeuge kommen alle 2 Minuten, Meldungen und Stationen alle 10-15
  await Promise.all([every(60, loadEvents), every(60, loadRisk), every(60, loadVehicles), every(600, loadWeather), every(300, loadStations),
    every(600, loadRadiation),
    every(900, loadConflicts), every(300, loadFilm), every(1800, loadWind)]);
  every(60, loadStatus);
  setInterval(renderFeed, 60_000 * 5); // relative Zeitangaben auffrischen
}

main();
window.openrisk = { state, get map() { return map; }, get film() { return film; } }; // für die Browser-Konsole
