"""Laufende Naturereignisse (NASA EONET): Waldbrände, Stürme, Vulkane, ..."""
from __future__ import annotations

from datetime import datetime

from .. import db

URL = "https://eonet.gsfc.nasa.gov/api/v3/events"
KINDS = {
    "wildfires": ("wildfire", "Waldbrand", 1), "severeStorms": ("storm", "Sturm", 2),
    "volcanoes": ("volcano", "Vulkan", 2), "floods": ("flood", "Überschwemmung", 2),
    "landslides": ("landslide", "Erdrutsch", 2), "drought": ("drought", "Dürre", 1),
    "earthquakes": ("earthquake", "Erdbeben", 2), "tempExtremes": ("heat", "Extremtemperatur", 1),
    "dustHaze": ("dust", "Staub/Dunst", 1), "seaLakeIce": ("ice", "Eis", 0), "snow": ("snow", "Schnee", 1),
}


def _point(geometry: dict) -> tuple[float, float] | None:
    coords = geometry.get("coordinates")
    if geometry.get("type") == "Point":
        return coords[0], coords[1]
    if geometry.get("type") == "Polygon" and coords and coords[0]:
        ring = coords[0]
        return sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)
    return None


def collect(ctx) -> int:
    r = ctx.http.get(URL, params={"status": "open", "days": 30})
    r.raise_for_status()
    events = []
    for ev in r.json()["events"]:
        if not ev.get("geometry") or not ev.get("categories"):
            continue
        last = ev["geometry"][-1]
        point = _point(last)
        if not point or ev["categories"][0]["id"] not in KINDS:
            continue
        lon, lat = point
        kind, label, severity = KINDS[ev["categories"][0]["id"]]
        magnitude, unit = last.get("magnitudeValue"), last.get("magnitudeUnit")
        if kind == "storm" and unit == "kts" and magnitude:
            severity = 3 if magnitude >= 64 else 2 if magnitude >= 34 else 1
        if kind == "wildfire" and unit == "acres" and magnitude and magnitude >= 50_000:
            severity = 2
        sources = ev.get("sources") or [{}]
        events.append({
            "id": f"eonet:{ev['id']}", "source": "NASA EONET", "category": "disaster", "kind": kind,
            "title": f"{label}: {ev['title']}",
            "summary": f"{magnitude:g} {unit}" if magnitude and unit else "",
            "url": sources[0].get("url") or ev.get("link"), "lat": lat, "lon": lon,
            "country": ctx.geo.country_at(lat, lon), "severity": severity,
            "ts": int(datetime.fromisoformat(last["date"].replace("Z", "+00:00")).timestamp()),
        })
    db.upsert_events(ctx.db, events)
    return len(events)
