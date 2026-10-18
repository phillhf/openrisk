// Wetterfilm: Regenradar mit Vorhersage (DWD), Wolken vom Satelliten (EUMETSAT) und Windströmung
// (Open-Meteo) auf einer gemeinsamen Zeitleiste. Alle Daten legt der Server ab, die Karte läuft offline weiter.

const RADAR_TOLERANCE = 5 * 60; // Radarbild gilt höchstens 5 Minuten neben seiner Zeit
const SAT_TOLERANCE = 45 * 60; // Satellitenbilder kommen mit rund 30 Minuten Verzögerung - so lange weiter zeigen
const PLAY_MS = 650;

const clock = (ts) => new Date(ts * 1000).toLocaleString('de-DE', { weekday: 'short', hour: '2-digit', minute: '2-digit' });
function relative(ts, now) {
  const minutes = Math.round((ts - now) / 60);
  if (Math.abs(minutes) < 5) return 'jetzt';
  const text = Math.abs(minutes) < 60 ? `${Math.abs(minutes)} Min.`
    : `${Math.floor(Math.abs(minutes) / 60)}:${String(Math.abs(minutes) % 60).padStart(2, '0')} Std.`;
  return minutes < 0 ? `vor ${text}` : `Vorhersage +${text}`;
}

/** Radar- oder Satellitenbild aufbereiten: Hintergrund durchsichtig, nur Niederschlag bzw. Wolken bleiben. */
async function prepare(url, kind, light) {
  const bitmap = await createImageBitmap(await (await fetch(url)).blob());
  const canvas = document.createElement('canvas');
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(bitmap, 0, 0);
  const image = ctx.getImageData(0, 0, canvas.width, canvas.height);
  const d = image.data;
  if (kind === 'radar') {
    for (let i = 0; i < d.length; i += 4) {
      const [r, g, b] = [d[i], d[i + 1], d[i + 2]];
      // grau/weiß = kein Niederschlag oder außerhalb der Reichweite, magenta = Rand der Reichweite
      if ((Math.abs(r - g) < 14 && Math.abs(g - b) < 14) || (r > 200 && g < 90 && b > 200)) d[i + 3] = 0;
    }
  } else {
    // Infrarot: Wolken hell (kalt), Boden und Meer dunkel - Helligkeit wird zur Deckkraft
    const [cr, cg, cb, max] = light ? [71, 85, 105, 200] : [236, 242, 248, 225];
    for (let i = 0; i < d.length; i += 4) {
      const a = Math.min(1, Math.max(0, (d[i] - 105) / 105));
      d[i] = cr; d[i + 1] = cg; d[i + 2] = cb; d[i + 3] = Math.round(a * max);
    }
  }
  ctx.putImageData(image, 0, 0);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve));
  return URL.createObjectURL(blob);
}

/** Bilderfolge einer Ebene (Radar oder Satellit): lädt und bereitet die Bilder bei Bedarf auf. */
class ImageLayer {
  constructor(map, id, kind, light, before) {
    Object.assign(this, { map, id, kind, light, before, frames: [], urls: new Map(), shown: null, visible: false });
  }

  setManifest(layer) {
    if (!layer) return;
    const files = new Set(layer.frames.map((f) => f.file));
    for (const [file, url] of this.urls) {
      if (!files.has(file)) { URL.revokeObjectURL(url); this.urls.delete(file); }
    }
    this.frames = layer.frames;
    this.bounds = layer.bounds;
    this.attribution = layer.attribution;
  }

  frameAt(t) {
    let best = null;
    for (const f of this.frames) {
      const ok = this.kind === 'radar' ? Math.abs(f.t - t) <= RADAR_TOLERANCE : f.t <= t && t - f.t <= SAT_TOLERANCE;
      if (ok && (!best || Math.abs(f.t - t) < Math.abs(best.t - t))) best = f;
    }
    return best;
  }

  async url(frame) {
    if (!this.urls.has(frame.file)) {
      this.urls.set(frame.file, prepare(`/weathermaps/${frame.file}`, this.kind, this.light));
    }
    return this.urls.get(frame.file);
  }

  /** Bild zur Zeit t zeigen; ohne passendes Bild (z. B. Satellit in der Zukunft) wird die Ebene ausgeblendet. */
  async show(t) {
    const frame = this.visible ? this.frameAt(t) : null;
    if (!frame) {
      if (this.map.getLayer(this.id)) this.map.setLayoutProperty(this.id, 'visibility', 'none');
      this.shown = null;
      return;
    }
    const url = await this.url(frame);
    if (this.wanted !== t && this.wanted !== undefined) return; // inzwischen weitergeblättert
    const source = this.map.getSource(this.id);
    if (!source) {
      this.map.addSource(this.id, { type: 'image', url, coordinates: this.bounds }); // Bildquellen kennen keine Quellenangabe
      this.map.addLayer({ id: this.id, type: 'raster', source: this.id,
        paint: { 'raster-opacity': this.kind === 'radar' ? 0.85 : 1, 'raster-fade-duration': 0,
          'raster-resampling': this.kind === 'radar' ? 'nearest' : 'linear' } }, this.before());
    } else if (this.shown !== frame.file) {
      source.updateImage({ url, coordinates: this.bounds });
    }
    this.map.setLayoutProperty(this.id, 'visibility', 'visible');
    this.shown = frame.file;
  }

  /** Alle Bilder vorab aufbereiten, damit der Film flüssig läuft (nacheinander, um den Pi nicht zu blockieren). */
  async preload() {
    for (const frame of this.frames) {
      if (!this.visible) return;
      await this.url(frame).catch(() => null);
    }
  }
}

// ---------------------------------------------------------------- Windströmung
const WIND_COLORS = {
  dark: [[4, 'rgba(186,230,253,0.55)'], [8, 'rgba(255,255,255,0.8)'], [14, '#fde047'], [20, '#fb923c'], [Infinity, '#ef4444']],
  light: [[4, 'rgba(30,64,175,0.45)'], [8, 'rgba(30,64,175,0.8)'], [14, '#ca8a04'], [20, '#ea580c'], [Infinity, '#dc2626']],
};
const mercY = (lat) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));
const latOf = (y) => (360 / Math.PI) * Math.atan(Math.exp(y)) - 90;

class WindLayer {
  constructor(map, light) {
    this.map = map;
    this.colors = WIND_COLORS[light ? 'light' : 'dark'];
    this.canvas = document.createElement('canvas');
    this.canvas.className = 'wind-canvas';
    map.getContainer().appendChild(this.canvas);
    this.ctx = this.canvas.getContext('2d');
    this.particles = [];
    this.data = null;
    this.t = null;
    this.running = false;
    this.moving = false;
    const pause = () => { this.moving = true; this.clear(); };
    const resume = () => { this.moving = false; this.resize(); };
    map.on('movestart', pause);
    map.on('moveend', resume);
    map.on('resize', () => this.resize());
  }

  setData(data) { this.data = data; this.t = null; }

  setTime(t) {
    if (!this.data || t === this.t) return;
    this.t = t;
    const { times, u, v } = this.data;
    let i = times.findIndex((x) => x > t);
    if (i === -1) i = times.length - 1;
    const i0 = Math.max(0, i - 1);
    const f = times[i] === times[i0] ? 0 : Math.min(1, Math.max(0, (t - times[i0]) / (times[i] - times[i0])));
    const n = u[0].length;
    this.u = new Float32Array(n);
    this.v = new Float32Array(n);
    for (let k = 0; k < n; k += 1) {
      this.u[k] = (u[i0][k] * (1 - f) + u[i][k] * f) / 10;
      this.v[k] = (v[i0][k] * (1 - f) + v[i][k] * f) / 10;
    }
  }

  /** Wind (m/s) an einer Stelle, zwischen den Rasterpunkten gemittelt; außerhalb des Rasters null. */
  sample(lon, lat) {
    const { lat0, lon0, step, nlat, nlon } = this.data;
    const gx = (lon - lon0) / step;
    const gy = (lat - lat0) / step;
    if (gx < 0 || gy < 0 || gx > nlon - 1 || gy > nlat - 1) return null;
    const x0 = Math.min(Math.floor(gx), nlon - 2);
    const y0 = Math.min(Math.floor(gy), nlat - 2);
    const fx = gx - x0;
    const fy = gy - y0;
    const at = (arr, x, y) => arr[y * nlon + x];
    const mix = (arr) => (at(arr, x0, y0) * (1 - fx) + at(arr, x0 + 1, y0) * fx) * (1 - fy)
      + (at(arr, x0, y0 + 1) * (1 - fx) + at(arr, x0 + 1, y0 + 1) * fx) * fy;
    return [mix(this.u), mix(this.v)];
  }

  resize() {
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const { clientWidth: w, clientHeight: h } = this.map.getContainer();
    this.canvas.width = w * ratio;
    this.canvas.height = h * ratio;
    this.canvas.style.width = `${w}px`;
    this.canvas.style.height = `${h}px`;
    this.ratio = ratio;
    const slow = (navigator.hardwareConcurrency || 4) <= 4; // Raspberry Pi: weniger Teilchen
    const count = Math.min(slow ? 1200 : 2500, Math.round((w * h) / (slow ? 900 : 550)));
    this.particles = Array.from({ length: count }, () => this.spawn({}));
  }

  /** Bildschirm <-> Länge/Breite ohne map.unproject je Teilchen (Karte ist nie gedreht oder geneigt). */
  frame() {
    const b = this.map.getBounds();
    return { west: b.getWest(), east: b.getEast(), top: mercY(b.getNorth()), bottom: mercY(b.getSouth()),
      w: this.canvas.width, h: this.canvas.height };
  }

  spawn(p) {
    p.x = Math.random() * this.canvas.width;
    p.y = Math.random() * this.canvas.height;
    p.age = Math.floor(Math.random() * 80);
    return p;
  }

  clear() { this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height); }

  start() {
    this.canvas.hidden = false;
    if (this.running) return;
    this.running = true;
    if (!this.particles.length) this.resize();
    const loop = () => {
      if (!this.running) return;
      if (!document.hidden && !this.moving && this.u) this.step();
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }

  stop() { this.running = false; this.clear(); this.canvas.hidden = true; }

  step() {
    const { ctx } = this;
    const f = this.frame();
    // Spuren langsam verblassen lassen
    ctx.globalCompositeOperation = 'destination-in';
    ctx.fillStyle = 'rgba(0,0,0,0.95)';
    ctx.fillRect(0, 0, f.w, f.h);
    ctx.globalCompositeOperation = 'source-over';
    ctx.lineWidth = 1.5 * this.ratio;
    const speed = 0.3 * this.ratio; // Bildpunkte je Bild und m/s
    const paths = this.colors.map(() => new Path2D());
    for (const p of this.particles) {
      const lon = f.west + (p.x / f.w) * (f.east - f.west);
      const lat = latOf(f.top - (p.y / f.h) * (f.top - f.bottom));
      const wind = this.sample(lon, lat);
      p.age += 1;
      if (!wind || p.age > 100 || p.x < 0 || p.y < 0 || p.x > f.w || p.y > f.h) { this.spawn(p); continue; }
      const [u, v] = wind;
      const x = p.x + u * speed;
      const y = p.y - v * speed;
      const strength = Math.hypot(u, v);
      const bucket = this.colors.findIndex(([limit]) => strength < limit);
      paths[bucket].moveTo(p.x, p.y);
      paths[bucket].lineTo(x, y);
      p.x = x;
      p.y = y;
    }
    paths.forEach((path, i) => { ctx.strokeStyle = this.colors[i][1]; ctx.stroke(path); });
  }
}

// ------------------------------------------------------------------ Zeitleiste
export class WeatherFilm {
  /** before(): Kartenebene, unter der Radar und Wolken liegen (Beschriftungen bleiben lesbar). */
  constructor(map, { light, before, onChange }) {
    this.map = map;
    this.radar = new ImageLayer(map, 'radar', 'radar', light, before);
    this.clouds = new ImageLayer(map, 'clouds', 'satellite', light, before);
    this.wind = new WindLayer(map, light);
    this.onChange = onChange;
    this.show = { radar: false, clouds: false, wind: false };
    this.steps = [];
    this.index = -1;
    this.timer = null;
    this.bar = document.createElement('div');
    this.bar.className = 'film';
    this.bar.hidden = true;
    this.bar.innerHTML = '<button type="button" class="play" title="Abspielen / Anhalten" aria-label="Abspielen">▶</button>'
      + '<input type="range" min="0" max="0" value="0" aria-label="Zeitpunkt">'
      + '<span class="when"></span><button type="button" class="now" title="Zum aktuellen Stand">Jetzt</button>'
      + '<span class="note"></span><span class="credit"></span>';
    map.getContainer().appendChild(this.bar);
    this.slider = this.bar.querySelector('input');
    this.picked = false; // hat der Nutzer selbst einen Zeitpunkt gewählt? Dann beim Nachladen dort bleiben
    this.slider.addEventListener('input', () => { this.pause(); this.picked = true; this.go(Number(this.slider.value)); });
    this.bar.querySelector('.play').addEventListener('click', () => (this.timer ? this.pause() : this.play()));
    this.bar.querySelector('.now').addEventListener('click', () => { this.pause(); this.picked = false; this.go(this.nowIndex()); });
  }

  setManifest(manifest) {
    this.radar.setManifest(manifest?.radar);
    this.clouds.setManifest(manifest?.satellite);
    this.manifest = manifest;
    this.rebuild();
  }

  setWind(data) {
    this.wind.setData(data);
    this.windData = data;
    this.rebuild();
  }

  setVisible(name, on) {
    this.show[name] = on;
    if (name !== 'wind') {
      this[name].visible = on;
      if (on) this[name].preload();
    }
    if (name === 'wind') (on ? this.wind.start() : this.wind.stop());
    this.rebuild();
  }

  /** Zeitpunkte: Radar und Satellit im 10-Minuten-Takt, Wind stündlich darüber hinaus. */
  rebuild() {
    const t = this.steps[this.index];
    const times = new Set();
    if (this.show.radar) this.radar.frames.forEach((f) => times.add(f.t));
    if (this.show.clouds) this.clouds.frames.forEach((f) => times.add(f.t));
    if (this.show.wind && this.windData) {
      const last = Math.max(0, ...times);
      const now = Date.now() / 1000;
      this.windData.times.filter((x) => x >= now - 3 * 3600 && (!times.size || x > last)).forEach((x) => times.add(x));
    }
    this.steps = [...times].sort((a, b) => a - b);
    this.bar.hidden = !this.steps.length;
    if (!this.steps.length) { this.pause(); this.radar.show(0); this.clouds.show(0); return; }
    this.slider.max = String(this.steps.length - 1);
    const keep = t === undefined || !(this.picked || this.timer) ? -1 : this.steps.indexOf(t);
    this.go(keep >= 0 ? keep : this.nowIndex());
  }

  /** Letzter beobachteter Zeitpunkt (nicht in der Zukunft). */
  nowIndex() {
    const now = Date.now() / 1000;
    const observed = this.radar.frames.filter((f) => !f.forecast).map((f) => f.t);
    const limit = this.show.radar && observed.length ? Math.max(...observed) : now;
    let index = 0;
    this.steps.forEach((t, i) => { if (t <= limit + 60) index = i; });
    return index;
  }

  go(index) {
    this.index = Math.max(0, Math.min(index, this.steps.length - 1));
    const t = this.steps[this.index];
    this.slider.value = String(this.index);
    this.radar.wanted = t;
    this.clouds.wanted = t;
    this.radar.show(t).catch(() => null);
    this.clouds.show(t).catch(() => null);
    if (this.show.wind) this.wind.setTime(t);
    const now = Date.now() / 1000;
    this.bar.querySelector('.when').textContent = `${clock(t)} · ${relative(t, now)}`;
    const notes = [];
    if (this.show.radar && !this.radar.frameAt(t)) notes.push('Radar: keine Daten für diese Zeit');
    if (this.show.clouds && !this.clouds.frameAt(t)) notes.push('Satellit: nur Beobachtungen, keine Vorhersage');
    this.bar.querySelector('.note').textContent = notes.join(' · ');
    this.bar.querySelector('.credit').textContent = [this.show.radar && this.radar.attribution,
      this.show.clouds && this.clouds.attribution, this.show.wind && 'Wind: Open-Meteo'].filter(Boolean).join(' · ');
    this.onChange?.(t);
  }

  play() {
    this.bar.querySelector('.play').textContent = '❚❚';
    this.timer = setInterval(() => this.go(this.index + 1 >= this.steps.length ? 0 : this.index + 1), PLAY_MS);
  }

  pause() {
    clearInterval(this.timer);
    this.timer = null;
    this.bar.querySelector('.play').textContent = '▶';
  }
}
