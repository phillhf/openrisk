"""Frontverlauf in der Ukraine (DeepStateMap - ukrainisches OSINT-Projekt, täglich aktualisiert).

Übernommen werden die Flächen: von Russland besetzt, zuletzt befreit, Status unklar (umkämpft) sowie
weitere besetzte Gebiete aus der Karte (Krim, Transnistrien, Südossetien …). Angriffsrichtungen und
Einheiten (Punkte) bleiben weg. Die Darstellung ist eine Einschätzung des Projekts, keine amtliche Lage.
Ergebnis: data/geo/frontline.geojson. Alle 6 Stunden.
"""
from __future__ import annotations

import json

URL = "https://deepstatemap.live/api/history/last"
STATUS = (("status.occupied", "occupied"), ("territories", "occupied"), ("status.dismissed", "liberated"),
          ("status.unknown", "contested"))


def _flat(coords):
    """Höhenangabe (dritte Koordinate) weglassen und auf 5 Stellen (1 m) kürzen."""
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], 5), round(coords[1], 5)]
    return [_flat(c) for c in coords]


def areas(data: dict) -> list[dict]:
    features = []
    for f in data.get("map", {}).get("features", []):
        if f.get("geometry", {}).get("type") not in ("Polygon", "MultiPolygon"):
            continue
        name = f.get("properties", {}).get("name") or ""
        status = next((s for key, s in STATUS if f"geoJSON.{key}" in name), None)
        if not status:
            continue
        # Name dreisprachig: „Окуповано /// Occupied /// geoJSON.status.occupied“ - der englische Teil
        parts = [p.strip() for p in name.split("///")]
        label = parts[1] if len(parts) > 2 else ""
        features.append({"type": "Feature", "properties": {"status": status, "label": label},
                         "geometry": {"type": f["geometry"]["type"], "coordinates": _flat(f["geometry"]["coordinates"])}})
    return features


def collect(ctx) -> int:
    data = ctx.http.get(URL, timeout=90).raise_for_status().json()
    features = areas(data)
    if not features:
        raise RuntimeError("DeepStateMap hat keine Gebiete geliefert (Format geändert?)")
    stand = int(data.get("id") or 0)
    for f in features:
        f["properties"]["stand"] = stand
    path = ctx.data_dir / "geo" / "frontline.geojson"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False,
                              separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    return len(features)
