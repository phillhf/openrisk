"""Amtliche Wetter- und Unwetterwarnungen für Deutschland (DWD).

Die Warnungen gelten je Warngebiet (Landkreis, Küstenabschnitt, Binnensee). Die
Umrisse der Warngebiete werden einmalig geladen und als
data/geo/dwd_cells.geojson abgelegt; die Oberfläche färbt sie nach Warnstufe ein.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .. import db
from ..geo import simplify

WARNINGS_URL = "https://www.dwd.de/DWD/warnungen/warnapp/json/warnings.json"
WFS_URL = "https://maps.dwd.de/geoserver/dwd/ows"
CELL_LAYERS = ("Warngebiete_Kreise", "Warngebiete_Kueste", "Warngebiete_Binnenseen")
SOURCE = "DWD"

# DWD-Warnstufe (2 gelb, 3 orange, 4 rot, 5 violett; 1 = Vorabinformation) -> Stufe 1..4
LEVELS = {1: 2, 2: 1, 3: 2, 4: 3, 5: 4}
# Stichwort im Ereignisnamen -> Art (bestimmt das Symbol); erste Übereinstimmung zählt
KINDS = [("GEWITTER", "thunderstorm"), ("ORKAN", "wind"), ("STURM", "wind"), ("BÖEN", "wind"), ("WIND", "wind"),
         ("REGEN", "rain"), ("TAUWETTER", "rain"), ("SCHNEE", "snow"), ("GLÄTTE", "ice"), ("GLATTEIS", "ice"),
         ("FROST", "ice"), ("NEBEL", "fog"), ("HITZE", "heat"), ("UV", "heat")]


def _ring_points(geometry: dict) -> list:
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    return [polygon[0] for polygon in polygons]


def load_cells(http, path: Path) -> dict[str, tuple[str, float, float]]:
    """Warngebiete: id -> (Name, Breite, Länge). Lädt und vereinfacht die Umrisse beim ersten Aufruf."""
    if not path.exists():
        features = []
        for layer in CELL_LAYERS:
            r = http.get(WFS_URL, params={
                "service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeName": f"dwd:{layer}",
                "outputFormat": "application/json", "srsName": "EPSG:4326"}, timeout=90)
            r.raise_for_status()
            for f in r.json()["features"]:
                rings = [[[round(x, 3), round(y, 3)] for x, y in simplify([tuple(p[:2]) for p in ring], 0.004)]
                         for ring in _ring_points(f["geometry"])]
                largest = max(rings, key=len)
                features.append({
                    "type": "Feature",
                    "properties": {"id": str(f["properties"]["WARNCELLID"]), "name": f["properties"]["NAME"],
                                   "lon": round(sum(p[0] for p in largest) / len(largest), 3),
                                   "lat": round(sum(p[1] for p in largest) / len(largest), 3)},
                    "geometry": {"type": "MultiPolygon", "coordinates": [[ring] for ring in rings if len(ring) >= 4]},
                })
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False,
                                   separators=(",", ":")), encoding="utf-8")
    cells = json.loads(path.read_text(encoding="utf-8"))["features"]
    return {f["properties"]["id"]: (f["properties"]["name"], f["properties"]["lat"], f["properties"]["lon"])
            for f in cells}


def parse(payload: str, cells: dict, now: int) -> list[dict]:
    """warnings.json (JSONP) in Ereignisse umwandeln; die Warngebiets-ID steckt in der Ereignis-ID."""
    data = json.loads(payload[payload.index("(") + 1: payload.rindex(")")])
    events = {}
    for group, advance in ((data.get("warnings") or {}, False), (data.get("vorabInformation") or {}, True)):
        for cell_id, warnings in group.items():
            name, lat, lon = cells.get(cell_id, (None, None, None))
            for w in warnings:
                end = w.get("end")
                if end and end / 1000 < now:
                    continue
                label = (w.get("event") or "Wetterwarnung").strip()
                kind = next((k for word, k in KINDS if word in label.upper()), "weather")
                until = time.strftime("%d.%m. %H:%M UTC", time.gmtime(end / 1000)) if end else "auf Weiteres"
                place = name or w.get("regionName")
                start = w.get("start")
                event = {  # Beginn gehört zur ID: eine spätere, neue Warnung bekommt einen eigenen Eintrag
                    "id": f"dwd:{cell_id}:{label}:{int(start / 1000) if start else 0}", "source": SOURCE, "category": "weather", "kind": kind,
                    "title": ("Vorabinformation " if advance else "") + f"{label.title()} – {place}",
                    "summary": f"{(w.get('description') or '').strip()} Gültig bis {until}.".strip(),
                    "url": "https://www.dwd.de/DE/wetter/warnungen/warnWetter_node.html",
                    "lat": lat, "lon": lon, "place": place, "country": "DEU",
                    "severity": LEVELS.get(w.get("level"), 1),
                    "ts": now,  # solange die Warnung gilt, ist sie aktuell
                    "started": int(start / 1000) if start else None,
                }
                known = events.get(event["id"])
                if not known or known["severity"] < event["severity"]:
                    events[event["id"]] = event
    return list(events.values())


def collect(ctx) -> int:
    cells = load_cells(ctx.http, ctx.data_dir / "geo" / "dwd_cells.geojson")
    r = ctx.http.get(WARNINGS_URL)
    r.raise_for_status()
    now = int(time.time())
    events = parse(r.text, cells, now)
    db.upsert_events(ctx.db, events)
    db.end_missing(ctx.db, "dwd:", {e["id"] for e in events}, now)  # aufgehobene bleiben im Verlauf
    return len(events)
