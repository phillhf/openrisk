"""Katastrophenwarnungen des Global Disaster Alert and Coordination System (UN/EU)."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from .. import db

URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
KINDS = {"EQ": ("earthquake", "Erdbeben"), "TC": ("cyclone", "Tropensturm"), "FL": ("flood", "Überschwemmung"),
         "VO": ("volcano", "Vulkan"), "DR": ("drought", "Dürre"), "WF": ("wildfire", "Waldbrand")}
LEVELS = {"Green": 1, "Orange": 3, "Red": 4}
# Grüne Erdbeben und Waldbrände liefern USGS bzw. EONET bereits in großer Zahl
QUERIES = [("EQ;TC;FL;VO;DR;WF", "Orange;Red", 14), ("TC;FL;VO;DR", "Green", 7)]


def _timestamp(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


def collect(ctx) -> int:
    today = datetime.now(timezone.utc).date()
    events = {}
    for types, levels, days in QUERIES:
        r = ctx.http.get(URL, params={
            "eventlist": types, "alertlevel": levels,
            "fromDate": (today - timedelta(days=days)).isoformat(), "toDate": today.isoformat()})
        r.raise_for_status()
        if not r.content.strip():  # leere Antwort = keine Treffer
            continue
        for f in r.json().get("features", []):
            p = f["properties"]
            if p["eventtype"] not in KINDS or f["geometry"]["type"] != "Point":
                continue
            kind, label = KINDS[p["eventtype"]]
            lon, lat = f["geometry"]["coordinates"][:2]
            detail = (p.get("severitydata") or {}).get("severitytext") or ""
            events[f"gdacs:{p['eventtype']}{p['eventid']}"] = {
                "source": "GDACS", "category": "disaster", "kind": kind,
                "title": f"{label}: {p.get('eventname') or p.get('country') or p.get('name')}",
                "summary": " · ".join(x for x in (p.get("country"), detail) if x),
                "url": (p.get("url") or {}).get("report"), "lat": lat, "lon": lon,
                "place": p.get("country"), "country": p.get("iso3") or ctx.geo.country_at(lat, lon),
                "severity": LEVELS.get(p.get("alertlevel"), 1),
                "ts": min(_timestamp(p["todate"]), int(time.time())),
            }
    db.upsert_events(ctx.db, [{"id": key, **e} for key, e in events.items()])
    return len(events)
