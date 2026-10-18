"""Erdbeben weltweit (USGS)."""
from __future__ import annotations

from .. import db

PAGER = {"yellow": 2, "orange": 3, "red": 4}


def severity(mag: float, alert: str | None, tsunami: int) -> int:
    level = 4 if mag >= 7 else 3 if mag >= 6 else 2 if mag >= 4.5 else 1
    if tsunami and mag >= 6:
        level += 1
    return min(4, max(level, PAGER.get(alert or "", 0)))


def collect(ctx) -> int:
    r = ctx.http.get(ctx.cfg["url"])
    r.raise_for_status()
    events = []
    for f in r.json()["features"]:
        p = f["properties"]
        if p.get("type") != "earthquake" or p.get("mag") is None or not f.get("geometry"):
            continue
        lon, lat, depth = f["geometry"]["coordinates"][:3]
        events.append({
            "id": f"usgs:{f['id']}", "source": "USGS", "category": "disaster", "kind": "earthquake",
            "title": f"Erdbeben M {p['mag']:.1f} – {p.get('place') or 'unbekannter Ort'}",
            "summary": f"Tiefe {depth:.0f} km" + (" · Tsunami-Hinweis" if p.get("tsunami") else ""),
            "url": p.get("url"), "lat": lat, "lon": lon, "place": p.get("place"),
            "country": ctx.geo.country_at(lat, lon),
            "severity": severity(p["mag"], p.get("alert"), p.get("tsunami") or 0),
            "ts": int(p["time"] / 1000),
        })
    db.upsert_events(ctx.db, events)
    return len(events)
