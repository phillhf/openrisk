"""Brandherde weltweit vom Satelliten (NASA FIRMS, VIIRS auf NOAA-20, letzte 24 Stunden, ohne Konto).

Jeder Punkt ist ein Bildpunkt (375 m), in dem der Satellit ungewöhnlich viel Wärme gemessen hat: Wald- und
Flächenbrände, aber auch Industrie (Fackeln an Ölfeldern, Stahlwerke) und Vulkane. Unsichere Erkennungen
(confidence = low) fallen weg. FRP = Feuerstrahlungsleistung in MW - je größer, desto heftiger der Brand.
Starke Brandherde in Deutschland werden zusätzlich als Meldung gezeigt.
Ergebnis: data/geo/fires.geojson. Alle 6 Stunden, rund 5 MB je Abruf.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

from .. import db

URL = "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_Global_24h.csv"
ALERT_FRP = 20  # MW - ab hier wird ein Brandherd in Deutschland gemeldet


def parse(text: str) -> list[dict]:
    features = []
    for row in csv.DictReader(io.StringIO(text)):
        if row.get("confidence", "").lower().startswith("l"):
            continue
        try:
            lat, lon, frp = float(row["latitude"]), float(row["longitude"]), float(row["frp"] or 0)
            ts = int(datetime.strptime(row["acq_date"] + row["acq_time"].zfill(4), "%Y-%m-%d%H%M")
                     .replace(tzinfo=timezone.utc).timestamp())
        except (KeyError, ValueError):
            continue
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 3), round(lat, 3)]},
                         "properties": {"frp": round(frp), "ts": ts}})  # knapp: über 50.000 Punkte
    features.sort(key=lambda f: f["properties"]["frp"])  # stärkste zuletzt = oben
    return features


def collect(ctx) -> int:
    features = parse(ctx.http.get(URL, timeout=120).raise_for_status().text)
    path = ctx.data_dir / "geo" / "fires.geojson"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")),
                   encoding="utf-8")
    tmp.replace(path)
    events = []
    for f in features:
        lon, lat = f["geometry"]["coordinates"]
        p = f["properties"]
        if p["frp"] < ALERT_FRP or ctx.geo.country_at(lat, lon) != "DEU":
            continue
        day = datetime.fromtimestamp(p["ts"], timezone.utc).date().isoformat()
        events.append({
            "id": f"firms:{round(lat, 2)}:{round(lon, 2)}:{day}", "source": "NASA FIRMS", "category": "disaster",
            "kind": "wildfire", "title": f"Brandherd vom Satelliten erkannt ({lat:.3f}, {lon:.3f})",
            "summary": f"Feuerstrahlungsleistung {p['frp']:.0f} MW. Kann auch ein Industriefeuer sein - "
                       "Warnungen der Behörden (NINA) beachten.",
            "url": f"https://firms.modaps.eosdis.nasa.gov/map/#d:24hrs;@{lon:.3f},{lat:.3f},12z",
            "lat": lat, "lon": lon, "place": "", "country": "DEU", "severity": 2 if p["frp"] < 100 else 3, "ts": p["ts"]})
    db.upsert_events(ctx.db, events)
    return len(features)
