"""Aktuelles Wetter und 4-Tage-Vorhersage für große Städte (Open-Meteo)."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from .. import db

URL = "https://api.open-meteo.com/v1/forecast"
CURRENT = "temperature_2m,wind_speed_10m,wind_gusts_10m,precipitation,weather_code"
DAILY = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,wind_gusts_10m_max"
BATCH = 50


def pick_cities(places: list[dict], count: int) -> list[dict]:
    """Größte Stadt je Land zuerst (weltweite Abdeckung), dann nach Einwohnerzahl auffüllen."""
    first, rest, seen = [], [], set()
    for p in places:  # bereits nach Einwohnerzahl sortiert
        (rest if p["iso"] in seen else first).append(p)
        seen.add(p["iso"])
    return (first + rest)[:count]


def assess(temp, gust, precip, code) -> tuple[int, str, str]:
    """Stufe, Begründung und Art (für das Kartensymbol) aus den aktuellen Messwerten."""
    found = [(0, "", "weather")]
    if gust is not None and gust >= 75:
        found.append((4 if gust >= 120 else 3 if gust >= 100 else 2, f"Sturmböen {gust:.0f} km/h", "wind"))
    if temp is not None and temp >= 40:
        found.append((3 if temp >= 45 else 2, f"Extreme Hitze {temp:.0f} °C", "heat"))
    if temp is not None and temp <= -35:
        found.append((2, f"Extreme Kälte {temp:.0f} °C", "ice"))
    if code in (95, 96, 99):
        found.append((2, "Gewitter mit Hagel" if code != 95 else "Gewitter", "thunderstorm"))
    if precip is not None and precip >= 20:
        found.append((2, f"Starkregen {precip:.0f} mm/h", "rain"))
    return max(found)


def collect(ctx) -> int:
    cities = pick_cities(ctx.geo.cities, ctx.cfg["cities"])
    now = int(time.time())
    rows, events = [], []
    for i in range(0, len(cities), BATCH):
        batch = cities[i:i + BATCH]
        r = ctx.http.get(URL, params={
            "latitude": ",".join(str(c["lat"]) for c in batch),
            "longitude": ",".join(str(c["lon"]) for c in batch),
            "current": CURRENT, "daily": DAILY, "forecast_days": 4, "timezone": "UTC"})
        r.raise_for_status()
        results = r.json()
        if isinstance(results, dict):
            results = [results]
        for city, res in zip(batch, results):
            cur = res["current"]
            temp, gust = cur.get("temperature_2m"), cur.get("wind_gusts_10m")
            precip, code = cur.get("precipitation"), cur.get("weather_code")
            ts = int(datetime.fromisoformat(cur["time"]).replace(tzinfo=timezone.utc).timestamp())
            severity, reason, kind = assess(temp, gust, precip, code)
            daily = res.get("daily") or {}
            forecast = [{"date": d, "code": daily["weather_code"][j],
                         "tmax": daily["temperature_2m_max"][j], "tmin": daily["temperature_2m_min"][j],
                         "precip": daily["precipitation_sum"][j], "gust": daily["wind_gusts_10m_max"][j]}
                        for j, d in enumerate(daily.get("time", []))]
            rows.append((city["name"], city["iso"], city["lat"], city["lon"], ts, temp,
                         cur.get("wind_speed_10m"), gust, precip, code, severity, json.dumps(forecast)))
            if severity >= 2:
                day = datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
                events.append({
                    "id": f"weather:{city['name']}:{day}", "source": "Open-Meteo", "category": "weather",
                    "kind": kind, "title": f"{city['name']}: {reason}",
                    "summary": f"{temp:.0f} °C, Böen {gust:.0f} km/h, Niederschlag {precip:.1f} mm/h"
                    if None not in (temp, gust, precip) else "",
                    "lat": city["lat"], "lon": city["lon"], "place": city["name"], "country": city["iso"],
                    "severity": severity, "ts": min(ts, now),
                })
    ctx.db.executemany(
        "INSERT OR REPLACE INTO weather (place, country, lat, lon, ts, temp, wind, gust, precip, code, severity, "
        "forecast) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    ctx.db.executemany(  # Verlauf: ein Eintrag je Stadt und Messzeitpunkt
        "INSERT OR IGNORE INTO weather_history (place, ts, temp, wind, gust, precip, code) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(r[0], r[4], r[5], r[6], r[7], r[8], r[9]) for r in rows])
    db.upsert_events(ctx.db, events)
    return len(rows)
