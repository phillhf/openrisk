"""Internet-Ausfälle weltweit (IODA, Georgia Tech - frei, ohne Konto).

IODA beobachtet je Land und Region, wie viele Netze erreichbar sind: über das Routing (BGP: angekündigte
Adressblöcke) und über aktives Anpingen (ping-slash24). Fällt der Wert unter das Übliche, gibt es eine
Warnung („critical“), ist er wieder normal, eine Entwarnung. Schon 1 % weniger gilt dort als kritisch - hier
zählt deshalb erst ein deutlicher Einbruch:
  - Land: mindestens 10 % weniger, Region: mindestens 25 % weniger
  - Stufe nach Stärke: ab 10 % gering, ab 25 % mittel, ab 50 % hoch
Stromausfälle zeigen sich hier oft mit (ohne Strom kein Netz) - eine weltweite offene Quelle nur für
Stromausfälle gibt es nicht.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from .. import db, places

URL = "https://api.ioda.inetintel.cc.gatech.edu/v2/outages/alerts"
SOURCES = {"bgp": "erreichbare Netze (Routing)", "ping-slash24": "antwortende Netze (Ping)"}
MIN_DROP = {"country": 0.10, "region": 0.25}
MIN_HISTORY = {"bgp": 20, "ping-slash24": 30}  # zu kleine Netze schwanken stark
HOURS = 48
MERGE_GAP = 3 * 3600  # Ausfälle, die kurz aussetzen und wiederkommen, gelten als einer


def episodes(alerts: list[dict]) -> list[dict]:
    """Warnungen je Gebiet und Messart zu Ausfällen zusammenfassen: kritisch ... bis zur Entwarnung."""
    by_key: dict[tuple, list[dict]] = {}
    for a in alerts:
        if a.get("datasource") in SOURCES:
            by_key.setdefault((a["entity"]["type"], a["entity"]["code"], a["datasource"]), []).append(a)
    found = []
    for (kind, code, source), items in by_key.items():
        items.sort(key=lambda a: a["time"])
        current = None
        for a in items:
            history = a.get("historyValue") or 0
            if a["level"] == "critical" and history >= MIN_HISTORY[source]:
                drop = max(0.0, 1 - (a.get("value") or 0) / history)
                if current is None:
                    current = {"kind": kind, "code": code, "source": source, "entity": a["entity"],
                               "start": a["time"], "end": None, "drop": drop}
                current["drop"] = max(current["drop"], drop)
            elif a["level"] == "normal" and current is not None:
                current["end"] = a["time"]
                found.append(current)
                current = None
        if current is not None:
            found.append(current)
    found = [e for e in found if e["drop"] >= MIN_DROP.get(e["kind"], 1)]
    # je Gebiet (beide Messarten zusammen) nahe beieinanderliegende Ausfälle zu einem zusammenfassen
    merged: list[dict] = []
    for e in sorted(found, key=lambda e: (e["kind"], e["code"], e["start"])):
        last = merged[-1] if merged else None
        if last and (last["kind"], last["code"]) == (e["kind"], e["code"]) and (
                last["end"] is None or e["start"] - last["end"] <= MERGE_GAP):
            if e["drop"] > last["drop"]:
                last.update(drop=e["drop"], source=e["source"])
            last["end"] = None if last["end"] is None or e["end"] is None else max(last["end"], e["end"])
        else:
            merged.append(dict(e))
    return merged


def _iso3(info: dict) -> dict[str, str]:
    return {v.get("iso2"): iso3 for iso3, v in info.items() if v.get("iso2")}


def collect(ctx) -> int:
    now = int(time.time())
    alerts = []
    for entity in ("country", "region"):
        r = ctx.http.get(URL, params={"from": now - HOURS * 3600, "until": now, "entityType": entity, "limit": 5000},
                         timeout=90)
        r.raise_for_status()
        alerts += r.json().get("data") or []
    info_path = ctx.data_dir / "geo" / "country_info.json"
    iso3 = _iso3(json.loads(info_path.read_text(encoding="utf-8"))) if info_path.exists() else {}
    events = []
    for e in episodes(alerts):
        attrs = e["entity"].get("attrs") or {}
        country2 = e["code"] if e["kind"] == "country" else attrs.get("country_code", "")
        country = iso3.get(country2, "")
        loc = ctx.geo.countries.get(country)
        lat, lon = (loc.lat, loc.lon) if loc else (None, None)
        name = e["entity"]["name"]
        if e["kind"] == "region":
            hit = next((p for p in places.search(ctx.data_dir / "geo" / "places.db", name, 5) or []
                        if p.get("iso") == country2), None)
            if hit:
                lat, lon = hit["lat"], hit["lon"]
            name = f"{name} ({ctx.geo.country_names.get(country, attrs.get('country_name', ''))})"
        else:
            name = ctx.geo.country_names.get(country, name)
        if lat is None:
            continue
        pct = round(e["drop"] * 100)
        since = datetime.fromtimestamp(e["start"], timezone.utc).strftime("%d.%m. %H:%M UTC")
        state = (f"behoben {datetime.fromtimestamp(e['end'], timezone.utc).strftime('%d.%m. %H:%M UTC')}"
                 if e["end"] else "dauert an")
        path = "country" if e["kind"] == "country" else "region"
        events.append({
            "id": f"ioda:{e['kind']}:{e['code']}:{e['start']}", "source": "IODA", "category": "disaster",
            "kind": "infrastructure", "title": f"Internet-Ausfall: {name}",
            "summary": f"{pct} % weniger {SOURCES[e['source']]} als üblich, seit {since} – {state}. "
                       "Ursachen können Stromausfall, Kabelschaden, Unwetter oder eine Abschaltung durch Behörden sein.",
            "url": f"https://ioda.inetintel.cc.gatech.edu/{path}/{e['code']}",
            "lat": lat, "lon": lon, "place": name, "country": country,
            "severity": 3 if e["drop"] >= 0.5 else 2 if e["drop"] >= 0.25 else 1,
            "ts": e["end"] or now, "started": e["start"], "ended": e["end"]})
    db.upsert_events(ctx.db, events)
    return len(events)
