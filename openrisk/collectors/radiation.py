"""Gamma-Ortsdosisleistung (ODL) in Deutschland – Messnetz des Bundesamts für Strahlenschutz.

Rund 1.700 Sonden messen die Strahlung in der Umgebung; das BfS stellt Stundenwerte
frei zur Verfügung. Die natürliche Strahlung ist an jedem Ort über lange Zeit nahezu
gleich (abhängig von Höhe und Untergrund) und steigt vor allem bei Regen kurzzeitig an,
weil Niederschlag radioaktive Folgeprodukte des natürlichen Radons aus der Luft wäscht.

Bewertung (eigene Heuristik, keine amtliche Einstufung):
  grün  – im üblichen Bereich der Sonde
  gelb  – erhöht: mindestens doppelt so hoch wie üblich oder über 0,3 µSv/h.
          Meist natürlich (Regen), deshalb nur beobachten.
  rot   – deutlich erhöht: mindestens dreifach und über 0,3 µSv/h, oder über 1 µSv/h.
          Natürlich kaum zu erklären.
"Üblich" ist der Median der eigenen Messwerte der letzten 7 Tage. Solange zu wenig
Verlauf vorliegt, gelten nur die festen Grenzen.

Neben Deutschland kommen die europäischen Messstationen (EURDEP) dazu, die das BfS
ebenfalls bereitstellt; deren Verlauf wird nur alle 6 Stunden gespeichert.
"""
from __future__ import annotations

import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from .. import db

URL = "https://www.imis.bfs.de/ogc/opendata/ows"
PARAMS = {
    "service": "WFS", "version": "1.1.0", "request": "GetFeature", "typeName": "opendata:odlinfo_odl_1h_latest",
    "outputFormat": "application/json",
    "propertyName": "id,name,site_status,height_above_sea,end_measure,value,value_cosmic,value_terrestrial,geom",
}
NATURAL_MAX = 0.3  # µSv/h – darüber ist ein Wert in Deutschland ungewöhnlich
CLEARLY_HIGH = 1.0  # µSv/h – unabhängig vom Ort deutlich erhöht
BASELINE_DAYS = 7
MIN_BASELINE_VALUES = 12  # ein halber Tag Stundenwerte bzw. drei Tage bei EURDEP (alle 6 Stunden)
# Status: 0 kein Messwert (Sonde defekt/Testbetrieb), 1 grün, 2 gelb, 3 rot
LABELS = {0: "kein Messwert", 1: "im üblichen Bereich", 2: "erhöht – meist Regen, beobachten",
          3: "deutlich erhöht – natürlich kaum zu erklären"}


def assess(value: float | None, baseline: float | None) -> int:
    if value is None:
        return 0
    ratio = value / baseline if baseline else 1.0
    if value >= CLEARLY_HIGH or (ratio >= 3 and value >= NATURAL_MAX):
        return 3
    if value >= NATURAL_MAX or ratio >= 2:
        return 2
    return 1


def baselines(con, now: int) -> dict[str, float]:
    """Üblicher Wert je Sonde: Median der letzten 7 Tage (in nSv/h gespeichert)."""
    values: dict[str, list[int]] = {}
    for station, nsv in con.execute("SELECT id, value FROM radiation_history WHERE ts > ?",
                                    (now - BASELINE_DAYS * 86400,)):
        values.setdefault(station, []).append(nsv)
    return {station: statistics.median(v) / 1000 for station, v in values.items() if len(v) >= MIN_BASELINE_VALUES}


def _store(ctx, features: list[dict], network: str, history_every: int, url: str) -> int:
    """Messwerte bewerten und speichern. history_every: Mindestabstand der Verlaufseinträge je Sonde (s)."""
    now = int(time.time())
    usual = baselines(ctx.db, now)
    rows, events = [], []
    for f in features:
        p, coords = f["properties"], (f.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2 or not p.get("id"):
            continue
        value = p.get("value") if p.get("site_status") == 1 else None
        ts = int(datetime.fromisoformat(p["end_measure"].replace("Z", "+00:00")).timestamp())             if p.get("end_measure") else None
        baseline = usual.get(p["id"])
        status = assess(value, baseline)
        rows.append((p["id"], network, p.get("name") or p["id"], coords[1], coords[0], p.get("height_above_sea"), ts,
                     value, p.get("value_cosmic"), p.get("value_terrestrial"), baseline, status))
        # Meldung nur bei einem plötzlichen Anstieg gegenüber dem eigenen üblichen Wert. Dauerhaft belastete
        # Orte (z. B. die Sperrzone um Tschernobyl) bleiben rot auf der Karte, erzeugen aber nicht täglich Warnungen.
        if status == 3 and ts and baseline and value >= 2 * baseline:
            day = datetime.fromtimestamp(ts).date().isoformat()
            where = p.get("name") if network == "BfS" else f"{p.get('name')} ({p['id'][:2]})"
            events.append({
                "id": f"odl:{p['id']}:{day}", "source": network, "category": "disaster", "kind": "radiation",
                "title": f"Strahlung deutlich erhöht – {where}",
                "summary": f"Ortsdosisleistung {value:.2f} µSv/h"
                           + (f", üblich {baseline:.2f} µSv/h" if baseline else "") + ". " + LABELS[3] + ".",
                "url": url, "lat": coords[1], "lon": coords[0], "place": where,
                "country": "DEU" if network == "BfS" else ctx.geo.country_at(coords[1], coords[0]),
                "severity": 4, "ts": min(ts, now),
            })
    ctx.db.executemany(
        "INSERT OR REPLACE INTO radiation (id, network, name, lat, lon, elevation, ts, value, cosmic, terrestrial, "
        "baseline, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    # Verlauf (nSv/h als ganze Zahl spart Platz); bei EURDEP nur alle 6 Stunden, sonst würde er riesig
    ctx.db.execute(
        "INSERT OR IGNORE INTO radiation_history (id, ts, value, status) "
        "SELECT id, ts, CAST(ROUND(value * 1000) AS INTEGER), status FROM radiation "
        "WHERE network = ? AND value IS NOT NULL AND ts IS NOT NULL AND ts >= "
        "COALESCE((SELECT MAX(h.ts) FROM radiation_history h WHERE h.id = radiation.id), 0) + ?",
        (network, history_every - 600))
    db.upsert_events(ctx.db, events)
    return sum(1 for row in rows if row[7] is not None)


def _wfs(http, params: dict, timeout: int) -> dict:
    """Abfrage beim BfS-Server. Bei einer Störung antwortet er mit einer XML-Fehlermeldung statt JSON
    (teils mit Status 200) - dann eine verständliche Meldung statt "JSONDecodeError"."""
    r = http.get(URL, params=params, timeout=timeout)
    if "ExceptionReport" in r.text[:600]:
        detail = "Datenbank nicht erreichbar" if "connection" in r.text.lower() else f"HTTP {r.status_code}"
        raise RuntimeError(f"BfS-Server gestört ({detail}) – letzter Stand bleibt, neuer Versuch beim nächsten Abruf")
    r.raise_for_status()
    return r.json()


def collect(ctx) -> int:
    """Deutschland: ODL-Messnetz des BfS (Stundenwerte, Verlauf stündlich)."""
    return _store(ctx, _wfs(ctx.http, PARAMS, 90)["features"], "BfS", 3600, "https://odlinfo.bfs.de/")


EURDEP_PAGE = 1000  # mehr gibt der Server je Abfrage nicht heraus


def collect_eurdep(ctx) -> int:
    """Europa: EURDEP-Messstationen über das BfS. Deutsche Stationen kommen schon direkt vom BfS."""
    base = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "outputFormat": "application/json",
            "typeNames": "opendata:eurdep_latestValue", "sortBy": "id", "count": EURDEP_PAGE,
            "propertyName": "id,name,site_status,height_above_sea,end_measure,value,geom"}

    def page(start: int) -> dict:
        return _wfs(ctx.http, {**base, "startIndex": start}, 120)

    first = page(0)
    total = int(first.get("numberMatched") or len(first["features"]))
    with ThreadPoolExecutor(4) as pool:  # der Server braucht je Seite einige Sekunden
        pages = [first, *pool.map(page, range(EURDEP_PAGE, total, EURDEP_PAGE))]
    # Der Server liefert jede Station mehrfach; je Station zählt der jüngste Wert
    latest: dict[str, dict] = {}
    for f in (f for p in pages for f in p["features"]):
        station = str(f["properties"].get("id") or "")
        if station.startswith("DE"):
            continue
        known = latest.get(station)
        if not known or (f["properties"].get("end_measure") or "") > (known["properties"].get("end_measure") or ""):
            latest[station] = f
    features = list(latest.values())
    return _store(ctx, features, "EURDEP", 6 * 3600, "https://remon.jrc.ec.europa.eu/")
