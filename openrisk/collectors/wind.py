"""Windfeld für die Windströmung auf der Karte (Open-Meteo, Modelle von DWD und anderen Wetterdiensten).

Raster mit 0,5° Abstand über Deutschland und den Nachbarländern (460 Punkte), Wind in 10 m Höhe,
stündlich von 2 Stunden zurück bis 48 Stunden voraus. Gespeichert als data/geo/wind.json mit
Ost- (u) und Nordkomponente (v) in 0,1 m/s - die Oberfläche lässt daraus Teilchen strömen.
Alle 3 Stunden neu: rund 3.700 Abrufe am Tag, gut im freien Rahmen von Open-Meteo.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

URL = "https://api.open-meteo.com/v1/forecast"
LAT0, LAT1, LON0, LON1, STEP = 46.0, 55.5, 5.0, 16.0, 0.5
BATCH = 100  # Orte je Abfrage (lange Adressen vermeiden)


def grid() -> tuple[list[float], list[float]]:
    lats = [round(LAT0 + i * STEP, 2) for i in range(int((LAT1 - LAT0) / STEP) + 1)]
    lons = [round(LON0 + i * STEP, 2) for i in range(int((LON1 - LON0) / STEP) + 1)]
    return lats, lons


def components(speed: float | None, direction: float | None) -> tuple[int, int]:
    """Windrichtung ist die Herkunft (270° = Westwind, weht nach Osten) -> u/v in 0,1 m/s."""
    if speed is None or direction is None:
        return 0, 0
    rad = math.radians(direction)
    return round(-speed * math.sin(rad) * 10), round(-speed * math.cos(rad) * 10)


def collect(ctx) -> int:
    lats, lons = grid()
    points = [(lat, lon) for lat in lats for lon in lons]  # zeilenweise von Süd nach Nord
    times: list[int] = []
    u = []
    v = []
    for i in range(0, len(points), BATCH):
        batch = points[i:i + BATCH]
        r = ctx.http.get(URL, params={
            "latitude": ",".join(str(p[0]) for p in batch), "longitude": ",".join(str(p[1]) for p in batch),
            "hourly": "wind_speed_10m,wind_direction_10m", "wind_speed_unit": "ms", "timezone": "UTC",
            "past_hours": 2, "forecast_hours": 48}, timeout=60)
        r.raise_for_status()
        results = r.json()
        for res in results if isinstance(results, list) else [results]:
            hourly = res["hourly"]
            if not times:
                times = [int(datetime.fromisoformat(t).replace(tzinfo=timezone.utc).timestamp())
                         for t in hourly["time"]]
            pairs = [components(s, d) for s, d in zip(hourly["wind_speed_10m"], hourly["wind_direction_10m"])]
            u.append([p[0] for p in pairs])
            v.append([p[1] for p in pairs])
    # umsortieren: je Zeitpunkt ein flaches Feld (Zeile = Breite, Spalte = Länge)
    data = {"lat0": LAT0, "lon0": LON0, "step": STEP, "nlat": len(lats), "nlon": len(lons), "times": times,
            "u": [[row[t] for row in u] for t in range(len(times))],
            "v": [[row[t] for row in v] for t in range(len(times))], "updated": int(datetime.now(timezone.utc).timestamp())}
    path = ctx.data_dir / "geo" / "wind.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    return len(points)
