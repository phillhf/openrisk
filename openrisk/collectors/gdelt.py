"""Konfliktereignisse weltweit aus GDELT (Global Database of Events, Language, and Tone).

GDELT liest rund um die Uhr Nachrichten aus aller Welt und legt alle 15 Minuten eine Datei mit
erkannten Ereignissen ab - frei, ohne Konto. Die Erkennung ist automatisch und deshalb verrauscht
(ein "Kampf" kann auch ein Gerichtsstreit sein). Gefiltert wird deshalb streng:
  - nur Gewalt: Angriffe (CAMEO 18), Kämpfe (19), Massengewalt (20), gewaltsame Proteste (145)
  - Angriffe, Kämpfe und Massengewalt nur mit bewaffneten Beteiligten (Militär, Rebellen, Aufständische …)
    oder bei ausdrücklichen Angriffsarten (Luft-, Artillerieangriff, Bombenanschlag, Massenmord)
  - nur das Hauptereignis eines Artikels und nur auf eine Stadt genau verortet
  - gleiche Art am selben Ort und Tag wird zusammengefasst (gdelt_agg) und erst ab MIN_SOURCES
    unabhängigen Quellen als Ereignis gezeigt; je mehr Quellen, desto höher die Stufe.
Fehlen nach einer Pause Dateien, werden bis zu CATCH_UP_HOURS nachgeholt.
"""
from __future__ import annotations

import csv
import io
import json
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .. import db

BASE = "https://data.gdeltproject.org/gdeltv2/"
STEP = 15 * 60
CATCH_UP_HOURS = 48  # beim ersten Start bzw. nach einer Pause höchstens so viel nachholen
MIN_SOURCES = 3
KEEP_DAYS = 8  # Zwischenspeicher; gezeigt werden die letzten DAYS Tage (config: sources.gdelt.days)
SCHEMA = """
CREATE TABLE IF NOT EXISTS gdelt_agg (
    id       TEXT PRIMARY KEY,   -- gdelt:<Ort>:<Ereigniscode>:<Tag>
    code     TEXT, place TEXT, country TEXT, lat REAL, lon REAL,
    actors   TEXT,               -- JSON-Liste der häufigsten Beteiligten
    sources  INTEGER, articles INTEGER,
    first_ts INTEGER, last_ts INTEGER,
    urls     TEXT                -- JSON-Liste, höchstens 5 Artikel
);
"""
# CAMEO-Ereigniscode (Präfix) -> (Art, deutsche Bezeichnung, Grundstufe); der längste passende Präfix gewinnt
CODES = {
    "145": ("unrest", "Gewaltsame Proteste", 1),
    "18": ("violence", "Angriff", 2), "180": ("violence", "Angriff", 2), "181": ("violence", "Entführung", 2),
    "182": ("violence", "Körperliche Gewalt", 2), "183": ("terror", "Bombenanschlag", 3),
    "184": ("violence", "Menschen als Schutzschild", 2), "185": ("violence", "Attentatsversuch", 3),
    "186": ("violence", "Attentat", 3),
    "19": ("conflict", "Kampfhandlungen", 2), "190": ("conflict", "Militärische Gewalt", 2),
    "191": ("conflict", "Blockade", 1), "192": ("conflict", "Besetzung von Gebiet", 2),
    "193": ("conflict", "Gefecht mit Handwaffen", 2), "194": ("strike", "Artillerie/Panzerangriff", 3),
    "195": ("strike", "Luftangriff", 3), "196": ("conflict", "Waffenstillstandsbruch", 2),
    "20": ("violence", "Massengewalt", 3), "201": ("violence", "Massenvertreibung", 3),
    "202": ("violence", "Massenmord", 4), "203": ("violence", "Ethnische Säuberung", 4),
    "204": ("strike", "Massenvernichtungswaffen", 4),
}
# Spalten der GDELT-2.0-Ereignisdatei
C_ID, C_DAY, C_ACTOR1_CODE, C_ACTOR1, C_ACTOR2_CODE, C_ACTOR2, C_ROOT_EVENT, C_CODE = 0, 1, 5, 6, 15, 16, 25, 26
C_SOURCES, C_ARTICLES, C_GEO_TYPE, C_PLACE, C_COUNTRY, C_LAT, C_LON, C_FEATURE, C_ADDED, C_URL = \
    32, 33, 51, 52, 53, 56, 57, 58, 59, 60


def _kind(code: str):
    for length in (3, 2):
        if code[:length] in CODES:
            return CODES[code[:length]]
    return None


ARMED = ("MIL", "REB", "INS", "SEP", "UAF")  # Militär, Rebellen, Aufständische, Separatisten, bewaffnete Gruppen
EXPLICIT = ("183", "194", "195", "202", "203", "204")  # Anschlag, Artillerie, Luftangriff, Massenmord …


def _armed(code: str) -> bool:
    return any(code[i:i + 3] in ARMED for i in range(0, len(code), 3))


def relevant(cols: list[str]) -> bool:
    if not (len(cols) > C_URL and cols[C_ROOT_EVENT] == "1" and cols[C_GEO_TYPE] in ("3", "4")
            and cols[C_LAT] and cols[C_LON] and _kind(cols[C_CODE]) is not None):
        return False
    # „Kämpfe“ und „Angriffe“ erkennt GDELT auch in Politik, Sport und Alltagskriminalität - nur mit bewaffneten
    # Beteiligten oder bei ausdrücklichen Angriffsarten (Luftangriff, Anschlag …) zählen
    if cols[C_CODE][:2] in ("18", "19", "20") and cols[C_CODE][:3] not in EXPLICIT:
        return _armed(cols[C_ACTOR1_CODE]) or _armed(cols[C_ACTOR2_CODE])
    return True


def _stamps(last: int | None, newest: int) -> list[int]:
    start = max(last + STEP if last else 0, newest - CATCH_UP_HOURS * 3600)
    return list(range(start - start % STEP, newest + 1, STEP))


def _fetch(http, stamp: int) -> list[list[str]]:
    name = datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y%m%d%H%M%S")
    r = http.get(f"{BASE}{name}.export.CSV.zip", timeout=60)
    if r.status_code == 404:  # GDELT lässt gelegentlich eine Datei aus
        return []
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        text = io.TextIOWrapper(z.open(z.namelist()[0]), encoding="utf-8", errors="replace")
        return [cols for cols in csv.reader(text, delimiter="\t") if relevant(cols)]


def aggregate(con, rows: list[list[str]]) -> set[str]:
    """Rohzeilen in gdelt_agg einrechnen; liefert die betroffenen IDs."""
    touched = {}
    for cols in rows:
        code = cols[C_CODE][:3]
        key = f"gdelt:{cols[C_FEATURE] or cols[C_PLACE]}:{code}:{cols[C_DAY]}"
        added = int(datetime.strptime(cols[C_ADDED], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp())
        entry = touched.setdefault(key, {"code": code, "place": cols[C_PLACE], "country": cols[C_COUNTRY],
                                         "lat": float(cols[C_LAT]), "lon": float(cols[C_LON]), "actors": [],
                                         "sources": 0, "articles": 0, "first": added, "last": added, "urls": []})
        entry["sources"] += int(cols[C_SOURCES] or 0)
        entry["articles"] += int(cols[C_ARTICLES] or 0)
        entry["first"], entry["last"] = min(entry["first"], added), max(entry["last"], added)
        pair = " gegen ".join(a.title() for a in (cols[C_ACTOR1], cols[C_ACTOR2]) if a)
        if pair and pair not in entry["actors"]:
            entry["actors"].append(pair)
        if cols[C_URL] and cols[C_URL] not in entry["urls"]:
            entry["urls"].append(cols[C_URL])
    for key, e in touched.items():
        old = con.execute("SELECT actors, sources, articles, first_ts, last_ts, urls FROM gdelt_agg WHERE id = ?",
                          (key,)).fetchone()
        if old:
            actors = list(dict.fromkeys(json.loads(old[0]) + e["actors"]))[:4]
            urls = list(dict.fromkeys(json.loads(old[5]) + e["urls"]))[:5]
            con.execute("UPDATE gdelt_agg SET actors = ?, sources = sources + ?, articles = articles + ?, "
                        "first_ts = MIN(first_ts, ?), last_ts = MAX(last_ts, ?), urls = ? WHERE id = ?",
                        (json.dumps(actors), e["sources"], e["articles"], e["first"], e["last"], json.dumps(urls), key))
        else:
            con.execute("INSERT INTO gdelt_agg VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (key, e["code"], e["place"], e["country"], e["lat"], e["lon"], json.dumps(e["actors"][:4]),
                         e["sources"], e["articles"], e["first"], e["last"], json.dumps(e["urls"][:5])))
    return set(touched)


def to_event(row, geo) -> dict | None:
    key, code, place, country, lat, lon, actors, sources, articles, first, last, urls = row
    found = _kind(code)
    if not found or sources < MIN_SOURCES:
        return None
    kind, label, severity = found
    if code[:2] in ("19", "20"):  # Kampfhandlungen, über die viel berichtet wird, sind vermutlich bedeutend
        severity = min(4, severity + (sources >= 15) + (sources >= 40))
    actors, urls = json.loads(actors), json.loads(urls)
    town = place.split(",")[0]
    return {
        "id": key, "source": "GDELT", "category": "conflict", "kind": kind,
        "title": f"{label}: {town}",
        "summary": " ".join(x for x in (
            f"{'; '.join(actors)}." if actors else "",
            f"{sources} Quellen, {articles} Artikel.",
            "Automatisch aus Nachrichten erkannt (GDELT), nicht geprüft – Quelle lesen.") if x),
        "url": urls[0] if urls else "https://www.gdeltproject.org/", "lat": lat, "lon": lon, "place": place,
        "country": geo.country_at(lat, lon), "severity": severity, "ts": last, "started": first,
    }


def collect(ctx) -> int:
    ctx.db.executescript(SCHEMA)
    newest_name = ctx.http.get(f"{BASE}lastupdate.txt", timeout=30).raise_for_status().text.split()[2]
    newest = int(datetime.strptime(newest_name.rsplit("/", 1)[1][:14], "%Y%m%d%H%M%S")
                 .replace(tzinfo=timezone.utc).timestamp())
    row = ctx.db.execute("SELECT value FROM state WHERE key = 'gdelt_last'").fetchone()
    stamps = _stamps(int(row[0]) if row else None, newest)
    touched: set[str] = set()
    with ThreadPoolExecutor(4) as pool:  # beim Nachholen viele kleine Dateien (je ~60 KB)
        for rows in pool.map(lambda s: _fetch(ctx.http, s), stamps):
            touched |= aggregate(ctx.db, rows)
    ctx.db.execute("INSERT OR REPLACE INTO state VALUES ('gdelt_last', ?)", (str(newest),))
    ctx.db.execute("DELETE FROM gdelt_agg WHERE last_ts < ?", (time.time() - KEEP_DAYS * 86400,))
    events = []
    for key in touched:
        found = ctx.db.execute("SELECT * FROM gdelt_agg WHERE id = ?", (key,)).fetchone()
        event = to_event(tuple(found), ctx.geo) if found else None
        if event:
            events.append(event)
    db.upsert_events(ctx.db, events)
    return ctx.db.execute("SELECT COUNT(*) FROM events WHERE category = 'conflict' AND source = 'GDELT' "
                          "AND ts >= ?", (time.time() - ctx.cfg.get("days", 7) * 86400,)).fetchone()[0]
