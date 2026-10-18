"""Warnungen des Bevölkerungsschutzes für Deutschland (Warn-App NINA des BBK).

NINA bündelt mehrere Warnsysteme; KATWARN und BIWAPP sind darin enthalten,
ebenso das Modulare Warnsystem des Bundes (MoWaS), die Hochwasserportale der
Länder und Polizeimeldungen. Die DWD-Wetterwarnungen holt OpenRisk direkt beim
DWD (siehe dwd_warnings) und lässt sie hier aus.

Die Warngebiete landen in data/geo/nina_areas.geojson und werden in der
Oberfläche als Flächen gezeichnet.
"""
from __future__ import annotations

import json
import time
from datetime import datetime

from .. import db
from .news import _clean

BASE = "https://warnung.bund.de/api31/"
PROVIDERS = {"mowas": "NINA (MoWaS)", "katwarn": "KATWARN", "biwapp": "BIWAPP", "lhp": "Hochwasserportal",
             "police": "Polizei"}
SEVERITY = {"Minor": 1, "Moderate": 2, "Severe": 3, "Extreme": 4}
# Stichwort im Titel -> Art (bestimmt das Symbol); erste Übereinstimmung zählt
KINDS = [("hochwasser", "flood"), ("überschwemmung", "flood"), ("sturmflut", "flood"), ("brand", "fire"),
         ("rauch", "fire"), ("feuer", "fire"), ("trinkwasser", "health"), ("leitungswasser", "health"),
         ("gesundheit", "health"), ("stromausfall", "infrastructure"), ("versorgung", "infrastructure"),
         ("ausfall", "infrastructure"), ("notruf", "infrastructure"), ("cyber", "cyber"),
         ("gewitter", "thunderstorm"), ("unwetter", "weather"), ("sturm", "wind")]
MAX_NEW_PER_RUN = 60  # Einzelabrufe je Lauf begrenzen; der Rest folgt beim nächsten
STATE_KEY = "nina_cache_v2"
URL = "https://warnung.bund.de/meldungen"


def _rings(geometry: dict) -> list:
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"][0]]
    if geometry["type"] == "MultiPolygon":
        return [polygon[0] for polygon in geometry["coordinates"]]
    return []


def _timestamp(text: str | None) -> int | None:
    try:
        return int(datetime.fromisoformat(text).timestamp())
    except (TypeError, ValueError):
        return None


def build(entry: dict, provider: str, detail: dict, shapes: dict) -> dict:
    """Aus Übersichtseintrag, Volltext und Geometrie ein Ereignis samt Warnflächen machen."""
    info = next((i for i in detail.get("info", []) if i.get("language", "").lower().startswith("de")),
                (detail.get("info") or [{}])[0])
    title = _clean((entry.get("i18nTitle") or {}).get("de") or info.get("headline") or "Warnung", 200)
    rings = [ring for f in shapes.get("features", []) if f.get("geometry") for ring in _rings(f["geometry"])]
    largest = max(rings, key=len, default=None)
    lower = title.lower()
    severity = SEVERITY.get(entry.get("severity") or info.get("severity"), 1)
    event = {
        "id": f"nina:{entry['id']}", "source": PROVIDERS[provider], "category": "disaster",
        "kind": next((kind for word, kind in KINDS if word in lower), "disaster"),
        "title": title, "summary": _clean(info.get("description"), 600), "url": URL,
        "lat": sum(p[1] for p in largest) / len(largest) if largest else None,
        "lon": sum(p[0] for p in largest) / len(largest) if largest else None,
        "place": _clean(", ".join(a.get("areaDesc", "") for a in info.get("area", [])), 120) or None,
        "country": "DEU", "severity": severity, "started": _timestamp(entry.get("startDate") or detail.get("sent")),
    }
    areas = [{"type": "Feature", "properties": {"id": event["id"], "severity": severity}, "geometry": f["geometry"]}
             for f in shapes.get("features", []) if f.get("geometry") and _rings(f["geometry"])]
    return {"version": entry.get("version"), "event": event, "areas": areas}


def collect(ctx) -> int:
    row = ctx.db.execute("SELECT value FROM state WHERE key = ?", (STATE_KEY,)).fetchone()
    known: dict = json.loads(row[0]) if row else {}
    active: dict = {}
    fetched = 0
    for provider in PROVIDERS:
        r = ctx.http.get(f"{BASE}{provider}/mapData.json")
        r.raise_for_status()
        for entry in r.json():
            if entry.get("type") == "Cancel":  # Entwarnung
                continue
            cached = known.get(entry["id"])
            if cached and cached["version"] == entry.get("version"):
                active[entry["id"]] = cached
            elif fetched < MAX_NEW_PER_RUN:
                fetched += 1
                detail = ctx.http.get(f"{BASE}warnings/{entry['id']}.json")
                shapes = ctx.http.get(f"{BASE}warnings/{entry['id']}.geojson")
                if detail.status_code == 200:
                    active[entry["id"]] = build(entry, provider, detail.json(),
                                                shapes.json() if shapes.status_code == 200 else {})

    now = int(time.time())
    db.upsert_events(ctx.db, [{**item["event"], "ts": now} for item in active.values()])
    db.end_missing(ctx.db, "nina:", {item["event"]["id"] for item in active.values()}, now)  # bleiben im Verlauf
    ctx.db.execute("INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)",
                   (STATE_KEY, json.dumps(active, ensure_ascii=False)))
    areas = {"type": "FeatureCollection", "features": [f for item in active.values() for f in item["areas"]]}
    path = ctx.data_dir / "geo" / "nina_areas.geojson"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(areas, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return len(active)
