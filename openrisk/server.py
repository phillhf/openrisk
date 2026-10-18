"""Webserver: JSON-API über der Datenbank plus die statische Oberfläche und Karte."""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path

import httpx

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import __version__, assets, collectors, db, mapfetch, places, risk, settings, updater
from .collectors.weather import assess
from .config import ROOT
from .geo import Geo
from .scheduler import Scheduler

# Fahrzeuge werden relativ zum jüngsten Datensatz gefiltert, damit der letzte
# bekannte Stand auch ohne Internet sichtbar bleibt.
VEHICLE_WINDOW = {"aircraft": 15 * 60, "ship": 24 * 3600}
MAX_EVENTS = 4000
EVENT_FIELDS = "id, source, category, kind, title, summary, url, lat, lon, place, country, severity, ts, started, ended"


def cached(request: Request, payload: dict) -> Response:
    """JSON mit ETag: Hat sich nichts geändert, antwortet der Server mit 304 ohne Inhalt.
    Die Oberfläche fragt alle ein bis zwei Minuten nach - meist ist alles beim Alten."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    tag = '"' + hashlib.blake2b(body, digest_size=10).hexdigest() + '"'
    headers = {"ETag": tag, "Cache-Control": "no-cache"}
    if request.headers.get("if-none-match") == tag:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type="application/json", headers=headers)


class Revalidated(StaticFiles):
    """Oberfläche und Geodaten: der Browser fragt jedes Mal kurz nach (meist 304), damit nach einem
    Update nie eine veraltete Fassung aus dem Zwischenspeicher läuft."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response


def moment(request: Request) -> tuple[float, bool]:
    """Betrachteter Zeitpunkt: ?at=<Unix-Zeit> für den Rückblick, sonst jetzt."""
    at = request.query_params.get("at")
    return (float(at), True) if at else (time.time(), False)


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))


def local_request(request: Request) -> bool:
    """Aufruf direkt am Gerät? Prüft Absender und Host-Kopfzeile (Schutz vor DNS-Rebinding)."""
    client = request.client.host if request.client else ""
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
    return client in ("127.0.0.1", "::1") and host in ("localhost", "127.0.0.1", "::1")


def create_app(cfg: dict, collect: bool = True) -> Starlette:
    data_dir: Path = cfg["storage"]["data_dir"]
    db_path = data_dir / "openrisk.db"
    for sub in ("map", "geo", "weathermaps"):
        (data_dir / sub).mkdir(parents=True, exist_ok=True)
    db.init(db_path)
    settings.forget_unknown(data_dir)  # z. B. Zugangstoken des entfernten ACLED-Zugangs
    geo = Geo(data_dir / "geo")
    scheduler = Scheduler(cfg, geo, db_path)
    world = data_dir / "map" / "world.pmtiles"
    region_dir = data_dir / "map" / "regions"  # einzelne Länder in voller Detailstufe
    terrain_dir = data_dir / "map" / "terrain"  # Höhendaten: WORLD.pmtiles (grob) und einzelne Länder
    map_job = mapfetch.MapJob(world)
    terrain_readers: dict[Path, tuple[float, object]] = {}
    terrain_lock = threading.Lock()
    local = threading.local()

    def guarded(handler):
        """Ändernde Aufrufe nur von der eigenen Oberfläche: fremde Webseiten können den Kopf nicht setzen."""
        async def wrapper(request: Request):
            if request.headers.get("x-openrisk") != "1":
                return JSONResponse({"error": "Kopfzeile X-OpenRisk fehlt"}, status_code=403)
            body = await request.json() if await request.body() else {}
            return await run_in_threadpool(handler, request, body if isinstance(body, dict) else {})
        return wrapper

    def con() -> sqlite3.Connection:
        if not hasattr(local, "con"):
            local.con = db.connect(db_path)
        return local.con

    def events(request: Request) -> JSONResponse:
        hours = min(float(request.query_params.get("hours", 24)), 24 * 365)
        min_severity = int(request.query_params.get("min_severity", 0))
        at, _ = moment(request)
        # alles, was im Zeitfenster vor `at` aktiv war: begonnen vor `at`, zuletzt gesehen nach Fensterbeginn
        rows = con().execute(
            f"SELECT {EVENT_FIELDS} FROM events WHERE ts >= ? AND COALESCE(started, ts) <= ? AND severity >= ? "
            "AND category != 'conflict' ORDER BY ts DESC LIMIT ?",
            (at - hours * 3600, at, min_severity, MAX_EVENTS)).fetchall()
        return cached(request, {"events": [dict(r) for r in rows]})

    def search(request: Request) -> JSONResponse:
        """Volltextsuche über den gesamten gespeicherten Verlauf."""
        words = [w for w in request.query_params.get("q", "").split() if len(w) >= 2][:6]
        if not words:
            return JSONResponse({"events": []})
        escape = lambda w: "%" + w.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        clause = " AND ".join(["(title LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\' OR place LIKE ? ESCAPE '\\')"]
                              * len(words))
        rows = con().execute(f"SELECT {EVENT_FIELDS} FROM events WHERE {clause} ORDER BY ts DESC LIMIT 300",
                             [escape(w) for w in words for _ in range(3)]).fetchall()
        return JSONResponse({"events": [dict(r) for r in rows]})

    def conflicts(request: Request) -> JSONResponse:
        """Konfliktereignisse (GDELT) in knapper Form - es sind Tausende; Einzelheiten liefert /api/event."""
        columns = ["id", "kind", "title", "place", "lat", "lon", "severity", "ts"]
        at, _ = moment(request)
        rows = con().execute(
            f"SELECT {', '.join(columns)} FROM events WHERE category = 'conflict' AND ts >= ? AND ts <= ? "
            "AND lat IS NOT NULL ORDER BY severity DESC, ts DESC LIMIT 12000",
            (at - cfg["sources"]["gdelt"]["days"] * 86400, at)).fetchall()
        return cached(request, {"columns": columns, "rows": [list(r) for r in rows]})

    def event(request: Request) -> JSONResponse:
        row = con().execute(f"SELECT {EVENT_FIELDS} FROM events WHERE id = ?",
                            (request.query_params.get("id", ""),)).fetchone()
        return JSONResponse(dict(row) if row else {"error": "nicht gefunden"}, status_code=200 if row else 404)

    def vehicles(request: Request) -> JSONResponse:
        at, past = moment(request)
        if past:
            return cached(request, {"vehicles": vehicles_at(at)})
        out = []
        for kind, window in VEHICLE_WINDOW.items():
            latest = con().execute("SELECT MAX(seen) FROM vehicles WHERE kind = ?", (kind,)).fetchone()[0]
            if latest is None:
                continue
            rows = con().execute(
                "SELECT id, kind, callsign, reg, type, country, lat, lon, alt, speed, heading, squawk, info, seen "
                "FROM vehicles WHERE kind = ? AND seen >= ?", (kind, latest - window))
            out += [dict(r) for r in rows]
        return cached(request, {"vehicles": out})

    def vehicles_at(at: float) -> list[dict]:
        """Rückblick: letzte gespeicherte Position je Fahrzeug vor `at` (Flugzeuge 15 min, Schiffe 24 h)."""
        out = []
        for kind, prefix, window in (("aircraft", "air:%", 15 * 60), ("ship", "ship:%", 24 * 3600)):
            rows = con().execute(
                "SELECT t.vehicle_id AS id, t.ts AS seen, t.lat, t.lon, t.alt, t.speed, t.heading, "
                "s.callsign, s.reg, s.type, s.country FROM track_points t "
                "JOIN (SELECT vehicle_id, MAX(ts) AS ts FROM track_points WHERE ts > ? AND ts <= ? "
                "      AND vehicle_id LIKE ? GROUP BY vehicle_id) m ON m.vehicle_id = t.vehicle_id AND m.ts = t.ts "
                "LEFT JOIN vehicle_seen s ON s.vehicle_id = t.vehicle_id AND s.first_ts <= t.ts AND s.last_ts >= t.ts "
                "GROUP BY t.vehicle_id", (at - window, at, prefix)).fetchall()
            out += [{**dict(r), "kind": kind, "squawk": None, "info": None} for r in rows]
        return out

    def track(request: Request) -> JSONResponse:
        """Spur eines Fahrzeugs im Zeitraum from..to (Standard: die 12 Stunden vor dem betrachteten Zeitpunkt)."""
        to = float(request.query_params.get("to") or moment(request)[0])
        start = float(request.query_params.get("from") or to - 12 * 3600)
        rows = con().execute(
            "SELECT ts, lat, lon, alt, speed, heading FROM track_points WHERE vehicle_id = ? AND ts >= ? AND ts <= ? "
            "ORDER BY ts LIMIT 20000", (request.query_params.get("id", ""), start, to)).fetchall()
        return JSONResponse({"points": [dict(r) for r in rows]})

    def vehicle_search(request: Request) -> JSONResponse:
        """Flüge und Schiffe finden: Rufzeichen, Kennzeichen, ICAO-Adresse, MMSI oder Typ - im Zeitraum from..to."""
        query = request.query_params.get("q", "").strip()
        if len(query) < 2:
            return JSONResponse({"vehicles": []})
        start, end = float(request.query_params.get("from", 0)), float(request.query_params.get("to", time.time()))
        like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = con().execute(
            "SELECT s.vehicle_id AS id, s.kind, s.callsign, s.reg, s.type, s.country, "
            "(SELECT COUNT(*) FROM track_points t WHERE t.vehicle_id = s.vehicle_id AND t.ts >= ? AND t.ts <= ?) AS points, "
            "(SELECT MIN(ts) FROM track_points t WHERE t.vehicle_id = s.vehicle_id AND t.ts >= ? AND t.ts <= ?) AS first, "
            "(SELECT MAX(ts) FROM track_points t WHERE t.vehicle_id = s.vehicle_id AND t.ts >= ? AND t.ts <= ?) AS last "
            "FROM vehicle_seen s WHERE s.first_ts <= ? AND s.last_ts >= ? AND "
            "(s.callsign LIKE ? ESCAPE '\\' OR s.reg LIKE ? ESCAPE '\\' OR s.vehicle_id LIKE ? ESCAPE '\\' "
            "OR s.type LIKE ? ESCAPE '\\') ORDER BY s.last_ts DESC LIMIT 60",
            (start, end, start, end, start, end, end, start, like, like, like, like)).fetchall()
        return JSONResponse({"vehicles": [dict(r) for r in rows if r["points"]]})

    def weather(request: Request) -> JSONResponse:
        at, past = moment(request)
        if not past:
            rows = con().execute("SELECT * FROM weather").fetchall()
            return cached(request, {"weather": [{**dict(r), "forecast": json.loads(r["forecast"] or "[]")} for r in rows]})
        rows = con().execute(  # letzter gespeicherter Wert je Stadt vor `at` (höchstens 3 Stunden alt)
            "SELECT w.place, w.country, w.lat, w.lon, h.ts, h.temp, h.wind, h.gust, h.precip, h.code "
            "FROM weather w JOIN weather_history h ON h.place = w.place AND h.ts = "
            "(SELECT MAX(ts) FROM weather_history WHERE place = w.place AND ts <= ? AND ts > ?)",
            (at, at - 3 * 3600)).fetchall()
        return cached(request, {"weather": [
            {**dict(r), "severity": assess(r["temp"], r["gust"], r["precip"], r["code"])[0], "forecast": []}
            for r in rows]})

    def stations(request: Request) -> JSONResponse:
        at, past = moment(request)
        if not past:
            rows = con().execute("SELECT * FROM stations WHERE ts >= ?", (at - 6 * 3600,)).fetchall()
            return cached(request, {"stations": [dict(r) for r in rows]})
        rows = con().execute(
            "SELECT s.id, s.name, s.lat, s.lon, s.elevation, h.ts, h.temp, h.gust, h.precip, "
            "NULL AS humidity, NULL AS wind, NULL AS wind_dir, NULL AS pressure "
            "FROM stations s JOIN station_history h ON h.id = s.id AND h.ts = "
            "(SELECT MAX(ts) FROM station_history WHERE id = s.id AND ts <= ? AND ts > ?)",
            (at, at - 3 * 3600)).fetchall()
        return cached(request, {"stations": [dict(r) for r in rows]})

    def radiation(request: Request) -> JSONResponse:
        at, past = moment(request)
        if not past:
            rows = con().execute("SELECT * FROM radiation").fetchall()
            return cached(request, {"radiation": [dict(r) for r in rows]})
        rows = con().execute(  # Rückblick: letzter Stundenwert vor `at` je Sonde
            "SELECT r.id, r.name, r.lat, r.lon, r.elevation, r.baseline, h.ts, h.value / 1000.0 AS value, h.status "
            "FROM radiation r JOIN radiation_history h ON h.id = r.id AND h.ts = "
            "(SELECT MAX(ts) FROM radiation_history WHERE id = r.id AND ts <= ? AND ts > ?)",
            (at, at - 3 * 3600)).fetchall()
        return cached(request, {"radiation": [dict(r) for r in rows]})

    def history(request: Request) -> JSONResponse:
        """Temperaturverlauf einer Stadt (kind=city) oder DWD-Station (kind=station)."""
        at, _ = moment(request)
        days = min(max(int(request.query_params.get("days", 7)), 1), 3650)
        table, key, column = {"station": ("station_history", "id", "temp"),
                              "radiation": ("radiation_history", "id", "value / 1000.0")}.get(
            request.query_params.get("kind"), ("weather_history", "place", "temp"))
        rows = con().execute(f"SELECT ts, {column} FROM {table} WHERE {key} = ? AND ts > ? AND ts <= ? ORDER BY ts",
                             (request.query_params.get("id", ""), at - days * 86400, at)).fetchall()
        step = max(1, len(rows) // 400)  # für die Kurve reichen ~400 Punkte
        return JSONResponse({"points": [[r[0], r[1]] for r in rows[::step] if r[1] is not None]})

    def risk_scores(request: Request) -> JSONResponse:
        scores = risk.country_scores(con(), moment(request)[0])
        for s in scores:
            s["name"] = geo.country_names.get(s["iso"], s["iso"])
            if s["iso"] in geo.countries:
                s["lat"], s["lon"] = geo.countries[s["iso"]].lat, geo.countries[s["iso"]].lon
        return cached(request, {"countries": scores})

    country_info: dict = {"mtime": None, "data": {}}

    def country(request: Request) -> JSONResponse:
        """Länder-Popup: Grunddaten, Kennzahlen, Risiko und die wichtigsten Ereignisse der letzten 7 Tage."""
        iso = request.query_params.get("iso", "")[:3]
        path = data_dir / "geo" / "country_info.json"
        mtime = path.stat().st_mtime if path.exists() else None
        if mtime != country_info["mtime"]:  # nur nach einem neuen Abruf neu einlesen
            country_info.update(mtime=mtime, data=json.loads(path.read_text(encoding="utf-8")) if mtime else {})
        at, _ = moment(request)
        rows = con().execute(
            f"SELECT {EVENT_FIELDS} FROM events WHERE country = ? AND ts >= ? AND COALESCE(started, ts) <= ? "
            "AND severity > 0 ORDER BY severity DESC, ts DESC LIMIT 8", (iso, at - 7 * 86400, at)).fetchall()
        score = next((s for s in risk.country_scores(con(), at) if s["iso"] == iso), None)
        return JSONResponse({"iso": iso, "name": geo.country_names.get(iso, iso),
                             "info": country_info["data"].get(iso), "risk": score, "events": [dict(r) for r in rows]})

    radon_by_ags: dict = {"mtime": None, "data": {}}

    def radon_value(ags: str) -> dict | None:
        path = data_dir / "geo" / "radon.geojson"
        mtime = path.stat().st_mtime if path.exists() else None
        if mtime != radon_by_ags["mtime"]:  # nur nach einem neuen Abruf neu einlesen
            features = json.loads(path.read_text(encoding="utf-8"))["features"] if mtime else []
            radon_by_ags.update(mtime=mtime, data={f["properties"]["ags"]: f["properties"] for f in features
                                                   if f["properties"].get("ags")})
        return radon_by_ags["data"].get(ags)

    def nearest(table: str, lat: float, lon: float, where: str = "", params: tuple = ()) -> dict | None:
        rows = con().execute(f"SELECT * FROM {table} WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? {where}",
                             (lat - 0.5, lat + 0.5, lon - 0.8, lon + 0.8, *params)).fetchall()
        best = min(rows, key=lambda r: distance_km(lat, lon, r["lat"], r["lon"]), default=None)
        return {**dict(best), "km": round(distance_km(lat, lon, best["lat"], best["lon"]), 1)} if best else None

    def place_info(request: Request) -> JSONResponse:
        """Orts-Popup: Ortsverzeichnis, Gemeinde- bzw. Stadtdaten und was in der Nähe gemessen oder gemeldet wurde.
        ?id=<GeoNames-Nummer> (Suche) oder ?name=&lat=&lon= (Klick auf eine Beschriftung der Karte)."""
        q = request.query_params
        path = data_dir / "geo" / "places.db"
        try:
            place = places.get(path, int(q["id"])) if q.get("id") else                 places.near(path, q.get("name", ""), float(q["lat"]), float(q["lon"]))
        except (KeyError, ValueError):
            return JSONResponse({"error": "id oder name/lat/lon fehlt"}, status_code=400)
        if not place:
            return JSONResponse({"error": "Ort nicht im Ortsverzeichnis"}, status_code=404)
        info_db = data_dir / "geo" / "cityinfo.db"
        out: dict = {"place": place, "cityinfo": info_db.exists()}
        if info_db.exists():
            info = sqlite3.connect(f"file:{info_db}?mode=ro", uri=True)
            try:
                load = lambda sql, key: (lambda row: json.loads(row[0]) if row else None)(  # noqa: E731
                    info.execute(sql, (key,)).fetchone())
                if place.get("ags"):
                    out["gemeinde"] = load("SELECT data FROM gemeinden WHERE ags = ?", place["ags"])
                    out["kreis"] = load("SELECT data FROM kreise WHERE key = ?", place["ags"][:5])
                    out["stand"] = (info.execute("SELECT value FROM meta WHERE key = 'stand'").fetchone() or [""])[0]
                out["city"] = load("SELECT data FROM world WHERE gid = ?", place["id"])
            finally:
                info.close()
        lat, lon = place["lat"], place["lon"]
        at, past = moment(request)
        if place.get("ags"):
            out["radon"] = radon_value(place["ags"])
        if not past:
            # viele DWD-Stationen messen nur Niederschlag - die nächste mit Temperatur nehmen
            out["station"] = nearest("stations", lat, lon, "AND ts >= ? AND temp IS NOT NULL", (at - 6 * 3600,))
            out["radiation"] = nearest("radiation", lat, lon, "AND value IS NOT NULL")
        events = con().execute(
            f"SELECT {EVENT_FIELDS} FROM events WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? "
            "AND ts >= ? AND COALESCE(started, ts) <= ?",
            (lat - 0.3, lat + 0.3, lon - 0.45, lon + 0.45, at - 7 * 86400, at)).fetchall()
        events = [dict(e) for e in events if distance_km(lat, lon, e["lat"], e["lon"]) <= 30]
        out["events"] = sorted(events, key=lambda e: (-e["severity"], -e["ts"]))[:6]
        return JSONResponse(out)

    def place_search(request: Request) -> JSONResponse:
        """Ortssuche. Solange das Ortsverzeichnis fehlt, nur Länder und große Städte (Natural Earth)."""
        query = request.query_params.get("q", "").strip()[:80]
        if len(query) < 2:
            return JSONResponse({"ready": True, "places": []})
        found = places.search(data_dir / "geo" / "places.db", query)
        if found is not None:
            return JSONResponse({"ready": True, "places": found})
        key = places.normalize(query)
        found = [{"name": c.name, "region": "", "country": "", "lat": c.lat, "lon": c.lon, "population": 0,
                  "kind": "Land"} for c in geo.countries.values() if places.normalize(c.name).startswith(key)]
        found += [{"name": p["name"], "region": "", "country": geo.country_names.get(p["iso"], ""), "lat": p["lat"],
                   "lon": p["lon"], "population": p["pop"], "kind": ""} for p in geo.cities
                  if any(places.normalize(n).startswith(key) for n in p["names"])]
        return JSONResponse({"ready": False, "places": found[:12]})

    def status(request: Request) -> JSONResponse:
        rows = {r["name"]: dict(r) for r in con().execute("SELECT * FROM sources")}
        sources = []
        for name in [*collectors.PERIODIC, "ships"]:
            source_cfg = cfg["sources"][name]
            sources.append({
                "name": name, "enabled": collect and source_cfg.get("enabled", True),
                "interval_min": source_cfg.get("interval_min"),
                **{k: rows.get(name, {}).get(k) for k in ("last_run", "last_ok", "ok", "message", "count")}})
        return JSONResponse({
            "now": int(time.time()), "version": __version__, "collecting": collect, "sources": sources,
            "db_bytes": db_path.stat().st_size if db_path.exists() else 0,
            "events": con().execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "track_points": con().execute("SELECT COUNT(*) FROM track_points").fetchone()[0],
            "history_since": con().execute("SELECT MIN(ts) FROM events WHERE category != 'conflict'").fetchone()[0],
            "history_days": settings.load(data_dir)["history_days"],
            "map": mapfetch.info(world),
            "map_regions": [{"iso": r["iso"], "max_zoom": r["max_zoom"]} for r in mapfetch.regions(region_dir, {})],
            "terrain": [{"iso": r["iso"], "max_zoom": r["max_zoom"]} for r in mapfetch.regions(terrain_dir, {})],
            "weathermaps": (data_dir / "weathermaps" / "frames.json").exists(),
            "wind": (data_dir / "geo" / "wind.json").exists(),
            **{name: (data_dir / "geo" / f"{name}.geojson").exists() for name in ("pipelines", "bases", "dwd_cells", "nina_areas", "radon",
                                                                                 "masts", "bunkers", "telecom", "water", "cables",
                                                                                 "landings", "ixp", "datacenters")},
        })

    def refresh(request: Request, body: dict) -> JSONResponse:
        return JSONResponse({"triggered": scheduler.trigger(body.get("source"))})

    def map_state() -> dict:
        return {"file": mapfetch.info(world), "job": map_job.state, "zoom_limit": settings.map_zoom_limit(data_dir),
                "regions": mapfetch.regions(region_dir, geo.country_names),
                "terrain": mapfetch.regions(terrain_dir, {**geo.country_names, "WORLD": "Welt (grob)"}),
                "countries": sorted(({"iso": iso, "name": name} for iso, name in geo.country_names.items()
                                     if any(p[0] == iso for p in geo.polygons)), key=lambda c: c["name"]),
                "free_bytes": shutil.disk_usage(data_dir).free,
                "settings": settings.public(data_dir, scheduler.fallback_keys())}

    def map_get(request: Request) -> JSONResponse:
        return JSONResponse(map_state())

    def map_download(request: Request, body: dict) -> JSONResponse:
        try:
            zoom = int(body.get("max_zoom"))
        except (TypeError, ValueError):
            return JSONResponse({"error": "max_zoom fehlt"}, status_code=400)
        limit = settings.map_zoom_limit(data_dir)
        if not 0 <= zoom <= limit:
            return JSONResponse({"error": f"Zoomstufe {zoom} ist gesperrt (Grenze: {limit})."}, status_code=400)
        try:
            map_job.start(zoom)
        except mapfetch.MapError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        return JSONResponse(map_state())

    def map_region(request: Request, body: dict) -> JSONResponse:
        """Ein Land in voller Detailstufe laden (Stufe 15 = alles, was OpenStreetMap hat)."""
        iso = str(body.get("iso") or "")
        polygons = [poly for code, _, poly in geo.polygons if code == iso]
        if not polygons or not iso.isalnum():
            return JSONResponse({"error": "Land unbekannt."}, status_code=400)
        try:
            zoom = max(0, min(int(body.get("max_zoom", 15)), settings.MAP_ZOOM_MAX))
            map_job.start(zoom, (iso, geo.country_names.get(iso, iso), polygons, region_dir / f"{iso}.pmtiles"))
        except (TypeError, ValueError):
            return JSONResponse({"error": "max_zoom ungültig"}, status_code=400)
        except mapfetch.MapError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        return JSONResponse(map_state())

    def map_region_delete(request: Request, body: dict) -> JSONResponse:
        iso = str(body.get("iso") or "")
        if not iso.isalnum():
            return JSONResponse({"error": "Land unbekannt."}, status_code=400)
        if map_job.running() and map_job.state.get("iso") == iso:
            return JSONResponse({"error": "Für dieses Land läuft gerade ein Download."}, status_code=409)
        try:
            mapfetch.delete(region_dir / f"{iso}.pmtiles")
        except mapfetch.MapError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        return JSONResponse(map_state())

    def terrain_download(request: Request, body: dict) -> JSONResponse:
        """Höhendaten laden: iso=WORLD für die ganze Welt grob, sonst ein Land in voller Auflösung."""
        iso = str(body.get("iso") or "")
        try:
            if iso == "WORLD":
                map_job.start(mapfetch.TERRAIN_WORLD_ZOOM, url=mapfetch.TERRAIN_URL, out=terrain_dir / "WORLD.pmtiles",
                              kind="terrain")
            else:
                polygons = [poly for code, _, poly in geo.polygons if code == iso]
                if not polygons or not iso.isalnum():
                    return JSONResponse({"error": "Land unbekannt."}, status_code=400)
                map_job.start(mapfetch.TERRAIN_ZOOM, (iso, geo.country_names.get(iso, iso), polygons,
                                                     terrain_dir / f"{iso}.pmtiles"), url=mapfetch.TERRAIN_URL,
                              kind="terrain")
        except mapfetch.MapError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        return JSONResponse(map_state())

    def terrain_delete(request: Request, body: dict) -> JSONResponse:
        iso = str(body.get("iso") or "")
        if not iso.isalnum():
            return JSONResponse({"error": "Land unbekannt."}, status_code=400)
        if map_job.running() and map_job.state.get("kind") == "terrain":
            return JSONResponse({"error": "Es werden gerade Höhendaten geladen."}, status_code=409)
        path = terrain_dir / f"{iso}.pmtiles"
        with terrain_lock:
            reader = terrain_readers.pop(path, None)
            if reader:
                reader[1].close()
        try:
            mapfetch.delete(path)
        except mapfetch.MapError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        return JSONResponse(map_state())

    def _file_source(handle):
        def get_bytes(offset: int, length: int) -> bytes:
            handle.seek(offset)
            return handle.read(length)
        return get_bytes

    def terrain_tile(request: Request) -> Response:
        """Eine Höhenkachel aus den geladenen Dateien: Länder (fein) vor der Welt (grob)."""
        from pmtiles.reader import Reader
        try:
            z, x, y = (int(request.path_params[k]) for k in ("z", "x", "y"))
        except ValueError:
            return Response(status_code=400)
        files = sorted(terrain_dir.glob("*.pmtiles"), key=lambda f: f.stem == "WORLD")
        for path in files:
            if map_job.running() and map_job.state.get("kind") == "terrain" and path.stem == (
                    map_job.state.get("iso") or "WORLD"):
                continue  # wird gerade geschrieben
            with terrain_lock:
                mtime = path.stat().st_mtime
                cached = terrain_readers.get(path)
                if not cached or cached[0] != mtime:
                    if cached:
                        cached[1].close()
                    handle = open(path, "rb")  # kein mmap: unter Windows ließe sich die Datei sonst nicht löschen
                    terrain_readers[path] = cached = (mtime, handle, Reader(_file_source(handle)))
                reader = cached[2]
                if z > reader.header()["max_zoom"]:
                    continue
                data = reader.get(z, x, y)
            if data:
                return Response(data, media_type="image/webp", headers={"Cache-Control": "max-age=86400"})
        return Response(status_code=204)  # keine Höhendaten: die Karte zeigt dort einfach nichts

    def map_cancel(request: Request, body: dict) -> JSONResponse:
        map_job.cancel()
        return JSONResponse(map_state())

    def map_delete(request: Request, body: dict) -> JSONResponse:
        if map_job.running():
            return JSONResponse({"error": "Es läuft gerade ein Download."}, status_code=409)
        try:
            mapfetch.delete(world)
        except mapfetch.MapError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        return JSONResponse(map_state())

    def settings_update(request: Request, body: dict) -> JSONResponse:
        settings.save(data_dir, body)
        if "api_keys" in body:
            if settings.api_key(data_dir, "aisstream"):
                db.set_status(con(), "ships", False, "Verbindung wird aufgebaut …", None)
            scheduler.keys_changed()
        return JSONResponse(map_state())

    # ------------------------------------------------------------ Software-Update
    # Ein Update führt neuen Programmcode aus - deshalb nur direkt am Gerät (localhost), nie aus dem Netz.
    update = {"state": "idle", "message": "", "latest": None, "checked": 0, "error": ""}
    update_lock = threading.Lock()

    def update_info(request: Request) -> dict:
        return {"version": __version__, "source": settings.load(data_dir)["update_source"],
                "local": local_request(request), "launcher": bool(os.environ.get("OPENRISK_LAUNCHER")), **update}

    def update_check(source: str) -> None:
        update.update(checked=int(time.time()), latest=None, error="")
        if not source:
            return
        try:
            with httpx.Client(follow_redirects=True) as http:
                update["latest"] = updater.check(source, http)
        except updater.UpdateError as e:
            update["error"] = str(e)

    def update_get(request: Request) -> JSONResponse:
        """?check=1 fragt die Update-Quelle (höchstens alle 6 Stunden), ?check=2 sofort."""
        mode = request.query_params.get("check")
        if mode == "2" or (mode == "1" and time.time() - update["checked"] > 6 * 3600):
            update_check(settings.load(data_dir)["update_source"])
        return JSONResponse(update_info(request))

    def local_only(handler):
        async def wrapper(request: Request):
            if not local_request(request):
                return JSONResponse({"error": "Updates nur direkt am Gerät (http://localhost:…)."}, status_code=403)
            return await handler(request)
        return wrapper

    def update_source(request: Request, body: dict) -> JSONResponse:
        source = str(body.get("source") or "").strip()
        settings.save_update_source(data_dir, source)
        update_check(source)
        return JSONResponse(update_info(request))

    def update_apply(request: Request, package: Path | None) -> JSONResponse:
        """Paket (hochgeladen oder aus der Quelle) einspielen und neu starten."""
        if not update_lock.acquire(blocking=False):
            return JSONResponse({"error": "Es läuft schon ein Update."}, status_code=409)
        try:
            update.update(state="running", message="Update wird eingespielt …", error="")
            if package is None:
                source = settings.load(data_dir)["update_source"]
                update_check(source)
                if not update["latest"] or not update["latest"]["newer"]:
                    raise updater.UpdateError(update["error"] or "Keine neuere Version in der Update-Quelle.")
                with httpx.Client(follow_redirects=True) as http:
                    package = updater.fetch(source, update["latest"], data_dir / "update" / "paket.zip", http)
            result = updater.install(package)
        except updater.UpdateError as e:
            update.update(state="error", message=str(e))
            return JSONResponse({**update_info(request), "error": str(e)}, status_code=400)
        except Exception as e:  # noqa: BLE001 - alter Stand ist wiederhergestellt, Grund anzeigen
            update.update(state="error", message=f"Update fehlgeschlagen ({type(e).__name__}: {e}).")
            return JSONResponse({**update_info(request), "error": update["message"]}, status_code=500)
        finally:
            update_lock.release()
        stop = getattr(request.app.state, "stop", None)
        if os.environ.get("OPENRISK_LAUNCHER") and stop:
            request.app.state.restart = True
            threading.Timer(1.0, stop).start()  # erst antworten, dann neu starten
            update.update(state="restarting", message=f"Version {result['to']} eingespielt – OpenRisk startet neu …")
        else:
            update.update(state="done", message=f"Version {result['to']} eingespielt. Bitte OpenRisk neu starten.")
        return JSONResponse(update_info(request))

    def update_install(request: Request, body: dict) -> JSONResponse:
        return update_apply(request, None)

    async def update_upload(request: Request) -> JSONResponse:
        if request.headers.get("x-openrisk") != "1":
            return JSONResponse({"error": "Kopfzeile X-OpenRisk fehlt"}, status_code=403)
        package = data_dir / "update" / "hochgeladen.zip"
        package.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        with package.open("wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > updater.MAX_PACKAGE:
                    f.close()
                    package.unlink(missing_ok=True)
                    return JSONResponse({"error": "Die Datei ist zu groß für ein Update-Paket."}, status_code=413)
                f.write(chunk)
        return await run_in_threadpool(update_apply, request, package)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        if collect:
            scheduler.start()
            threading.Thread(target=assets.fetch_missing, daemon=True).start()  # z. B. nach einem Update
        yield
        await scheduler.stop()

    return Starlette(lifespan=lifespan, routes=[
        Route("/api/update", update_get),
        Route("/api/update/source", local_only(guarded(update_source)), methods=["POST"]),
        Route("/api/update/install", local_only(guarded(update_install)), methods=["POST"]),
        Route("/api/update/upload", local_only(update_upload), methods=["POST"]),
        Route("/api/events", events),
        Route("/api/search", search),
        Route("/api/history", history),
        Route("/api/conflicts", conflicts),
        Route("/api/event", event),
        Route("/api/vehicles", vehicles),
        Route("/api/track", track),
        Route("/api/vehicle_search", vehicle_search),
        Route("/api/weather", weather),
        Route("/api/stations", stations),
        Route("/api/radiation", radiation),
        Route("/api/risk", risk_scores),
        Route("/api/status", status),
        Route("/api/places", place_search),
        Route("/api/place", place_info),
        Route("/api/country", country),
        Route("/api/refresh", guarded(refresh), methods=["POST"]),
        Route("/api/map", map_get),
        Route("/api/map", guarded(map_download), methods=["POST"]),
        Route("/api/map", guarded(map_delete), methods=["DELETE"]),
        Route("/api/map/cancel", guarded(map_cancel), methods=["POST"]),
        Route("/api/map/region", guarded(map_region), methods=["POST"]),
        Route("/api/map/region", guarded(map_region_delete), methods=["DELETE"]),
        Route("/api/map/terrain", guarded(terrain_download), methods=["POST"]),
        Route("/api/map/terrain", guarded(terrain_delete), methods=["DELETE"]),
        Route("/terrain/{z}/{x}/{y}.webp", terrain_tile),
        Route("/api/settings", guarded(settings_update), methods=["POST"]),
        Mount("/map", StaticFiles(directory=data_dir / "map")),
        Mount("/geo", Revalidated(directory=data_dir / "geo")),
        Mount("/weathermaps", Revalidated(directory=data_dir / "weathermaps")),
        Mount("/", Revalidated(directory=ROOT / "web", html=True)),
    ])
