"""Messwerte der deutschen Wetterstationen (DWD Open Data, SYNOP-Meldungen).

Der DWD legt alle paar Minuten eine Datei mit den neu eingegangenen Meldungen
ab (10-Minuten-Werte von rund 1000 Stationen). Verarbeitet werden nur Dateien,
die seit dem letzten Lauf hinzugekommen sind.

Die Dateien sind ausführlich formatiert (entpackt bis über 50 MB). Statt sie ganz
als JSON zu laden (Hunderte MB im Speicher, sekundenlang Rechenzeit auf dem Pi),
werden sie gestückelt entpackt und je Meldung nur die benötigten Werte per
regulärem Ausdruck herausgezogen.
"""
from __future__ import annotations

import json
import re
import zlib
from datetime import datetime, timezone
from urllib.parse import unquote

BASE = "https://opendata.dwd.de/weather/weather_reports/synoptic/germany/geojson/"
FILE = re.compile(r'href="(Z__C_EDZW_\d{14}_[^"]+\.geojson\.gz)"')
FIRST_RUN_FILES = 8  # deckt gut eine Stunde ab
MAX_FILES_PER_RUN = 12  # nach längerer Pause nur die jüngsten nachholen
FEATURE = b'"type": "Feature"'
NEEDED = {"wigosLocalIdentifierCharacter", "blockNumber", "stationNumber", "stationOrSiteName", "year", "month",
          "day", "hour", "minute", "airTemperature", "relativeHumidity", "windSpeed", "windDirection",
          "maximumWindGustSpeed", "totalPrecipitationOrTotalWaterEquivalent", "pressureReducedToMeanSeaLevel"}
# Ein Messwert im DWD-GeoJSON: "value": <Zahl|"Text"|null>, direkt gefolgt von "shortname": "<Name>"
MARKERS = {name: f'"shortname": "{name}"'.encode() for name in NEEDED}
VALUE = re.compile(rb'"value":\s*("(?:[^"\\]|\\.)*"|[^,\s}]+),\s*"shortname"')
COORDS = re.compile(rb'"coordinates":\s*\[\s*([-\d.eE]+),\s*([-\d.eE]+)(?:,\s*([-\d.eE]+|null))?')
STATE_KEY = "dwd_stations_last_file"
COLUMNS = ("temp", "humidity", "wind", "wind_dir", "gust", "precip", "pressure")


def parse_report(feature: dict) -> dict | None:
    """Eine Stationsmeldung (BUFR als GeoJSON, bereits als dict) abbilden."""
    first: dict = {}
    for entry in feature["properties"].values():  # Reihenfolge der Meldung; der erste Wert zählt
        if isinstance(entry, dict) and entry.get("value") is not None:
            first.setdefault(entry.get("shortname"), entry["value"])
    return _report(first, (feature.get("geometry") or {}).get("coordinates") or [])


def _value(raw: bytes):
    """Zahl, Text oder null aus dem Rohtext (schneller als json.loads je Wert)."""
    if raw == b"null":
        return None
    if raw[:1] == b'"':
        return json.loads(raw)  # Texte sind selten (Stationsname, Kennung)
    return float(raw) if b"." in raw or b"e" in raw or b"E" in raw else int(raw)


def parse_chunk(chunk: bytes) -> dict | None:
    """Eine Stationsmeldung direkt aus dem Rohtext lesen, ohne das ganze JSON aufzubauen."""
    first: dict = {}
    for name, marker in MARKERS.items():  # bytes.find läuft in C - deutlich schneller als eine Regex über alles
        end = chunk.find(marker)
        while end != -1:
            start = chunk.rfind(b'"value":', 0, end)
            match = VALUE.match(chunk, start) if start != -1 else None
            value = _value(match[1]) if match else None
            if value is not None:  # erster Wert, der nicht leer ist (wie bei parse_report)
                first[name] = value
                break
            end = chunk.find(marker, end + 1)
    coords = COORDS.search(chunk)
    if not coords:
        return None
    return _report(first, [_value(c) for c in coords.groups() if c is not None])


def parse_file(gz: bytes):
    """Meldungen einer .geojson.gz-Datei stückweise entpacken und auswerten (wenige MB Speicher)."""
    inflate = zlib.decompressobj(16 + zlib.MAX_WBITS)
    tail = b""
    for i in range(0, len(gz), 1 << 15):
        parts = (tail + inflate.decompress(gz[i:i + (1 << 15)])).split(FEATURE)
        tail = parts.pop()
        yield from filter(None, map(parse_chunk, parts))
    yield from filter(None, map(parse_chunk, (tail + inflate.flush()).split(FEATURE)))


def _report(first: dict, coords: list) -> dict | None:
    station_id = str(first.get("wigosLocalIdentifierCharacter") or "").strip()
    if not station_id and "blockNumber" in first and "stationNumber" in first:
        station_id = f"{first['blockNumber']:02d}{first['stationNumber']:03d}"
    if not station_id or len(coords) < 2 or "year" not in first:
        return None
    try:
        ts = datetime(first["year"], first["month"], first["day"], first.get("hour", 0), first.get("minute", 0),
                      tzinfo=timezone.utc).timestamp()
    except (KeyError, ValueError):
        return None
    kelvin, speed = first.get("airTemperature"), first.get("windSpeed")
    gust, pressure = first.get("maximumWindGustSpeed"), first.get("pressureReducedToMeanSeaLevel")
    return {
        "id": station_id, "name": str(first.get("stationOrSiteName") or station_id).strip(),
        "lat": coords[1], "lon": coords[0], "elevation": coords[2] if len(coords) > 2 else None, "ts": int(ts),
        "temp": round(kelvin - 273.15, 1) if kelvin is not None else None,
        "humidity": first.get("relativeHumidity"),
        "wind": round(speed * 3.6, 1) if speed is not None else None,  # m/s -> km/h
        "wind_dir": first.get("windDirection"),
        "gust": round(gust * 3.6, 1) if gust is not None else None,
        "precip": first.get("totalPrecipitationOrTotalWaterEquivalent"),  # mm in den letzten 10 Minuten
        "pressure": round(pressure / 100, 1) if pressure is not None else None,  # Pa -> hPa
    }


def store(con, reports: list[dict]) -> None:
    """Neuere Meldung gewinnt; fehlende Messgrößen bleiben vom vorherigen Stand erhalten."""
    keep = ", ".join(f"{c} = COALESCE(excluded.{c}, {c})" for c in COLUMNS)
    con.executemany(
        "INSERT INTO stations (id, name, lat, lon, elevation, ts, temp, humidity, wind, wind_dir, gust, precip, "
        "pressure) VALUES (:id, :name, :lat, :lon, :elevation, :ts, :temp, :humidity, :wind, :wind_dir, :gust, "
        f":precip, :pressure) ON CONFLICT(id) DO UPDATE SET name = excluded.name, ts = excluded.ts, {keep} "
        "WHERE excluded.ts >= ts", reports)


def collect(ctx) -> int:
    listing = ctx.http.get(BASE)
    listing.raise_for_status()
    files = sorted(set(FILE.findall(listing.text)), key=unquote)
    row = ctx.db.execute("SELECT value FROM state WHERE key = ?", (STATE_KEY,)).fetchone()
    todo = [f for f in files if unquote(f) > row[0]] if row else files[-FIRST_RUN_FILES:]
    for name in todo[-MAX_FILES_PER_RUN:]:
        r = ctx.http.get(BASE + name)
        r.raise_for_status()
        reports = list(parse_file(r.content))
        if not reports and len(r.content) > 4096:  # Format geändert? Dann der langsame, sichere Weg
            features = json.loads(zlib.decompress(r.content, 16 + zlib.MAX_WBITS))["features"]
            reports = [rep for rep in map(parse_report, features) if rep]
        store(ctx.db, sorted(reports, key=lambda rep: rep["ts"]))
    # Verlauf: höchstens ein Wert je Station und Stunde, sonst wüchse die Datenbank um Gigabytes im Jahr
    ctx.db.execute(
        "INSERT OR IGNORE INTO station_history (id, ts, temp, gust, precip) "
        "SELECT id, ts, temp, gust, precip FROM stations WHERE temp IS NOT NULL AND ts >= "
        "COALESCE((SELECT MAX(h.ts) FROM station_history h WHERE h.id = stations.id), 0) + 3000")
    if todo:
        ctx.db.execute("INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)", (STATE_KEY, unquote(todo[-1])))
    return ctx.db.execute("SELECT COUNT(*) FROM stations").fetchone()[0]
