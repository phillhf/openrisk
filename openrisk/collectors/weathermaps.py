"""Wetterfilm: Niederschlagsradar mit Vorhersage (DWD) und Wolken vom Satelliten (EUMETSAT).

  - Radar: DWD-Radarkomposit RV, Deutschland und Nachbarländer, 1 km. Analyse der letzten zwei Stunden
    und Vorhersage (Nowcasting) für die nächsten zwei Stunden - wie in der DWD-App. Bilder im 10-Minuten-
    Takt, ein Bild etwa 50 KB.
  - Satellit: Meteosat Third Generation (MTG-FCI), Infrarot 10,5 µm - Wolken hell, Boden dunkel, Tag und
    Nacht gleich gut. Mitteleuropa, letzte drei Stunden alle 10 Minuten, ein Bild etwa 200 KB. Die
    Oberfläche macht den dunklen Boden durchsichtig, damit nur die Wolken über der Karte liegen.
Beide Dienste liefern fertige Bilder (WMS) in Web-Mercator; gespeichert wird in data/weathermaps/ mit
einem Verzeichnis frames.json. Ältere Bilder werden gelöscht. Datenmenge: rund 120 MB am Tag.
"""
from __future__ import annotations

import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

STEP = 600  # 10 Minuten
RADAR = {
    "url": "https://maps.dwd.de/geoserver/dwd/wms", "layer": "Radar_rv_product_1x1km_ger",
    "caps": "https://maps.dwd.de/geoserver/dwd/Radar_rv_product_1x1km_ger/ows",
    "bbox": (1.46, 45.68, 18.73, 56.22), "width": 1100, "format": "image/png", "ext": "png",
    "past": 2 * 3600, "attribution": "Radar: Deutscher Wetterdienst",
}
SATELLITE = {
    "url": "https://view.eumetsat.int/geoserver/ows", "layer": "mtg_fd:ir105_hrfi",
    "caps": "https://view.eumetsat.int/geoserver/mtg_fd/ir105_hrfi/ows",
    "bbox": (-15.0, 35.0, 35.0, 66.0), "width": 1400, "format": "image/jpeg", "ext": "jpg",
    "past": 3 * 3600, "attribution": "Satellit: © EUMETSAT",
}


def mercator(lon: float, lat: float) -> tuple[float, float]:
    return lon * 20037508.34 / 180, math.log(math.tan((90 + lat) * math.pi / 360)) * 6378137


def corners(bbox) -> list[list[float]]:
    """Ecken für eine MapLibre-Bildquelle: oben links, oben rechts, unten rechts, unten links."""
    w, s, e, n = bbox
    return [[w, n], [e, n], [e, s], [w, s]]


def size(bbox, width: int) -> tuple[int, int]:
    (x0, y0), (x1, y1) = mercator(bbox[0], bbox[1]), mercator(bbox[2], bbox[3])
    return width, round(width * (y1 - y0) / (x1 - x0))


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:00.000Z")


def _parse(text: str) -> int:
    return int(datetime.strptime(text[:16], "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc).timestamp())


def latest_time(http, caps: str) -> int:
    """Letzte verfügbare Zeit aus der Zeitdimension (Form start/ende/PT..)."""
    text = http.get(caps, params={"service": "WMS", "version": "1.3.0", "request": "GetCapabilities"},
                    timeout=60).raise_for_status().text
    found = re.search(r'<Dimension name="time"[^>]*>[^<]*?/([0-9T:\-.]+Z)/PT', text)
    if not found:
        raise RuntimeError("Zeitangaben fehlen in der Dienstbeschreibung")
    return _parse(found.group(1))


def _image(http, spec: dict, ts: int) -> bytes:
    (x0, y0), (x1, y1) = mercator(spec["bbox"][0], spec["bbox"][1]), mercator(spec["bbox"][2], spec["bbox"][3])
    width, height = size(spec["bbox"], spec["width"])
    r = http.get(spec["url"], params={
        "service": "WMS", "version": "1.3.0", "request": "GetMap", "layers": spec["layer"], "styles": "",
        "crs": "EPSG:3857", "bbox": f"{x0},{y0},{x1},{y1}", "width": width, "height": height,
        "format": spec["format"], "transparent": "true", "time": iso(ts)}, timeout=60)
    r.raise_for_status()
    if not r.headers.get("content-type", "").startswith("image/"):
        raise RuntimeError(f"kein Bild erhalten ({r.text[:80]})")
    return r.content


def plan(newest: int, past: int, ahead: int = 0) -> list[int]:
    """Bildzeiten im 10-Minuten-Takt von newest-past bis newest+ahead."""
    end = newest - newest % STEP
    return list(range(end - past, end + ahead + 1, STEP))


def _layer(http, folder, name: str, spec: dict, times: list[int], forecast_from: int | None, run: int) -> dict:
    """Bilder laden, die noch fehlen. Vorhersagebilder gehören zu einem Lauf und werden je Lauf neu geholt."""
    frames, todo = [], []
    for ts in times:
        forecast = forecast_from is not None and ts > forecast_from
        file = f"{name}-{'fc' if forecast else 'obs'}-{ts}{f'-{run}' if forecast else ''}.{spec['ext']}"
        frames.append({"t": ts, "file": file, "forecast": forecast})
        if not (folder / file).exists():
            todo.append((ts, file))

    def fetch(item):
        ts, file = item
        try:
            data = _image(http, spec, ts)
        except Exception:  # noqa: BLE001 - einzelnes Bild fehlt: Film läuft ohne es
            return None
        (folder / file).write_bytes(data)
        return file

    with ThreadPoolExecutor(4) as pool:
        got = set(filter(None, pool.map(fetch, todo)))
    frames = [f for f in frames if (folder / f["file"]).exists() or f["file"] in got]
    return {"bounds": corners(spec["bbox"]), "frames": frames, "run": run, "attribution": spec["attribution"]}


def collect(ctx) -> int:
    folder = ctx.data_dir / "weathermaps"
    folder.mkdir(parents=True, exist_ok=True)
    manifest_path = folder / "frames.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    errors = []
    try:
        end = latest_time(ctx.http, RADAR["caps"])  # letzte Vorhersagezeit = Analyse + 2 Stunden
        analysis = end - 2 * 3600
        manifest["radar"] = _layer(ctx.http, folder, "radar", RADAR, plan(analysis, RADAR["past"], 2 * 3600),
                                   analysis, analysis)
    except Exception as e:  # noqa: BLE001
        errors.append(f"Radar: {type(e).__name__}")
    try:
        newest = latest_time(ctx.http, SATELLITE["caps"])
        manifest["satellite"] = _layer(ctx.http, folder, "sat", SATELLITE, plan(newest, SATELLITE["past"]), None,
                                       newest)
    except Exception as e:  # noqa: BLE001
        errors.append(f"Satellit: {type(e).__name__}")
    manifest["updated"] = int(time.time())
    tmp = manifest_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest), encoding="utf-8")
    tmp.replace(manifest_path)
    keep = {f["file"] for layer in ("radar", "satellite") for f in manifest.get(layer, {}).get("frames", [])}
    for path in folder.iterdir():
        if path.suffix in (".png", ".jpg") and path.name not in keep:
            path.unlink(missing_ok=True)
    if errors:
        raise RuntimeError("; ".join(errors) + " – letzter Stand bleibt")
    return sum(len(manifest.get(layer, {}).get("frames", [])) for layer in ("radar", "satellite"))
