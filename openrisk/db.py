"""SQLite-Datenbank: Schema und kleine Helfer.

Eine Verbindung pro Thread; WAL-Modus, damit die Sammler schreiben können,
während die Oberfläche liest (und die SD-Karte des Pi geschont wird).
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id       TEXT PRIMARY KEY,      -- quelle:eigene-id
    source   TEXT NOT NULL,
    category TEXT NOT NULL,         -- news | disaster | weather
    kind     TEXT,                  -- earthquake, conflict, storm, ...
    title    TEXT NOT NULL,
    summary  TEXT,
    url      TEXT,
    lat      REAL,
    lon      REAL,
    place    TEXT,
    country  TEXT,                  -- ISO-3
    severity INTEGER NOT NULL DEFAULT 0,   -- 0 Info .. 4 kritisch
    ts       INTEGER NOT NULL,      -- letzte Aktivität (Unix-Zeit); bei gültigen Warnungen der letzte Abruf
    updated  INTEGER NOT NULL,
    started  INTEGER,               -- Beginn, falls er von ts abweicht (Warnungen, die seit Tagen gelten)
    ended    INTEGER                -- Warnungen: Zeitpunkt der Aufhebung; NULL = gilt noch
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);

CREATE TABLE IF NOT EXISTS vehicles (
    id       TEXT PRIMARY KEY,      -- air:<icao-hex> | ship:<mmsi>
    kind     TEXT NOT NULL,         -- aircraft | ship
    callsign TEXT,
    reg      TEXT,
    type     TEXT,
    country  TEXT,
    lat      REAL NOT NULL,
    lon      REAL NOT NULL,
    alt      REAL,                  -- Fuß
    speed    REAL,                  -- Knoten
    heading  REAL,
    squawk   TEXT,
    info     TEXT,
    seen     INTEGER NOT NULL,
    tp_ts    INTEGER,               -- letzter gespeicherter Spurpunkt
    tp_lat   REAL,
    tp_lon   REAL
);
CREATE INDEX IF NOT EXISTS vehicles_kind_seen ON vehicles (kind, seen);

CREATE TABLE IF NOT EXISTS track_points (
    vehicle_id TEXT NOT NULL,
    ts         INTEGER NOT NULL,
    lat        REAL NOT NULL,
    lon        REAL NOT NULL,
    alt        REAL,
    speed      REAL,
    heading    REAL,
    PRIMARY KEY (vehicle_id, ts)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS track_points_ts ON track_points (ts);  -- Rückblick: wer war wann wo

-- Welches Fahrzeug trug wann welches Rufzeichen (für die Suche nach Flug oder Schiff)
CREATE TABLE IF NOT EXISTS vehicle_seen (
    vehicle_id TEXT NOT NULL,
    callsign   TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL,
    reg        TEXT,
    type       TEXT,
    country    TEXT,
    first_ts   INTEGER NOT NULL,
    last_ts    INTEGER NOT NULL,
    PRIMARY KEY (vehicle_id, callsign)
);
CREATE INDEX IF NOT EXISTS vehicle_seen_last ON vehicle_seen (last_ts);

CREATE TABLE IF NOT EXISTS weather (
    place    TEXT PRIMARY KEY,
    country  TEXT,
    lat      REAL NOT NULL,
    lon      REAL NOT NULL,
    ts       INTEGER NOT NULL,
    temp     REAL,
    wind     REAL,
    gust     REAL,
    precip   REAL,
    code     INTEGER,
    severity INTEGER NOT NULL DEFAULT 0,
    forecast TEXT                   -- JSON: Tageswerte
);

CREATE TABLE IF NOT EXISTS stations (      -- Wetterstationen (DWD), jeweils letzter Messwert
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    lat       REAL NOT NULL,
    lon       REAL NOT NULL,
    elevation REAL,
    ts        INTEGER NOT NULL,
    temp      REAL,                 -- °C
    humidity  REAL,                 -- %
    wind      REAL,                 -- km/h
    wind_dir  REAL,                 -- Grad
    gust      REAL,                 -- km/h
    precip    REAL,                 -- mm / 10 min
    pressure  REAL                  -- hPa
);

-- Verlauf: stündliche Werte, damit man auch später nachsehen kann, wie das Wetter war
CREATE TABLE IF NOT EXISTS weather_history (
    place  TEXT NOT NULL,
    ts     INTEGER NOT NULL,
    temp   REAL,
    wind   REAL,
    gust   REAL,
    precip REAL,
    code   INTEGER,
    PRIMARY KEY (place, ts)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS station_history (
    id     TEXT NOT NULL,
    ts     INTEGER NOT NULL,
    temp   REAL,
    gust   REAL,
    precip REAL,
    PRIMARY KEY (id, ts)
) WITHOUT ROWID;

-- Gamma-Ortsdosisleistung (BfS): letzter Stand je Sonde und stündlicher Verlauf
CREATE TABLE IF NOT EXISTS radiation (
    id          TEXT PRIMARY KEY,
    network     TEXT NOT NULL DEFAULT 'BfS',  -- BfS (Deutschland) oder EURDEP (Europa)
    name        TEXT NOT NULL,
    lat         REAL NOT NULL,
    lon         REAL NOT NULL,
    elevation   REAL,
    ts          INTEGER,           -- Ende des Messintervalls
    value       REAL,              -- µSv/h; NULL = Sonde ohne Messwert
    cosmic      REAL,              -- Anteil der Höhenstrahlung
    terrestrial REAL,              -- Anteil aus dem Boden/der Luft (steigt bei Freisetzungen)
    baseline    REAL,              -- üblicher Wert (Median 7 Tage)
    status      INTEGER NOT NULL   -- 0 kein Wert, 1 grün, 2 gelb, 3 rot
);

CREATE TABLE IF NOT EXISTS radiation_history (
    id     TEXT NOT NULL,
    ts     INTEGER NOT NULL,
    value  INTEGER NOT NULL,       -- nSv/h
    status INTEGER NOT NULL,
    PRIMARY KEY (id, ts)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS state (         -- Merker der Sammler (z. B. zuletzt verarbeitete Datei)
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    name     TEXT PRIMARY KEY,
    last_run INTEGER,
    last_ok  INTEGER,
    ok       INTEGER,
    message  TEXT,
    count    INTEGER
);
"""

EVENT_COLUMNS = ("id", "source", "category", "kind", "title", "summary", "url",
                 "lat", "lon", "place", "country", "severity", "ts", "updated", "started", "ended")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA cache_size=-8000")  # 8 MB
    return con


def init(path: Path) -> None:
    con = connect(path)
    con.executescript(SCHEMA)
    # Datenbanken aus älteren Versionen nachrüsten
    for table, columns in (("events", {"started": "INTEGER", "ended": "INTEGER"}),
                           ("track_points", {"speed": "REAL", "heading": "REAL"}),
                           ("radiation", {"network": "TEXT NOT NULL DEFAULT 'BfS'"})):
        existing = {row["name"] for row in con.execute(f"PRAGMA table_info({table})")}
        for column, kind in columns.items():
            if column not in existing:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    # Fahrzeuge aus der Zeit vor der Rufzeichen-Tabelle nachtragen, damit ihre Spuren auffindbar sind
    con.execute(
        "INSERT OR IGNORE INTO vehicle_seen (vehicle_id, callsign, kind, reg, type, country, first_ts, last_ts) "
        "SELECT id, COALESCE(callsign, ''), kind, reg, type, country, "
        "COALESCE((SELECT MIN(ts) FROM track_points WHERE vehicle_id = vehicles.id), seen), seen FROM vehicles")
    con.commit()
    con.close()


def upsert_events(con: sqlite3.Connection, events: list[dict]) -> int:
    now = int(time.time())
    rows = [tuple({**{c: None for c in EVENT_COLUMNS}, "severity": 0, **e, "updated": now}[c]
                  for c in EVENT_COLUMNS) for e in events]
    updates = ", ".join(f"{c}=excluded.{c}" for c in EVENT_COLUMNS if c != "id")
    con.executemany(
        f"INSERT INTO events ({', '.join(EVENT_COLUMNS)}) VALUES ({', '.join('?' * len(EVENT_COLUMNS))}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}", rows)
    return len(rows)


def set_status(con: sqlite3.Connection, name: str, ok: bool, message: str, count: int | None) -> None:
    now = int(time.time())
    con.execute(
        "INSERT INTO sources (name, last_run, last_ok, ok, message, count) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(name) DO UPDATE SET last_run=excluded.last_run, ok=excluded.ok, message=excluded.message, "
        "last_ok=COALESCE(excluded.last_ok, last_ok), count=COALESCE(excluded.count, count)",
        (name, now, now if ok else None, int(ok), message, count))
    con.commit()


def end_missing(con: sqlite3.Connection, prefix: str, active_ids: set[str], now: int) -> None:
    """Warnungen, die die Quelle nicht mehr meldet, als aufgehoben markieren (sie bleiben im Verlauf)."""
    rows = con.execute("SELECT id FROM events WHERE id LIKE ? AND ended IS NULL", (prefix + "%",)).fetchall()
    con.executemany("UPDATE events SET ended = ? WHERE id = ?",
                    [(now, row[0]) for row in rows if row[0] not in active_ids])


LIVE_VEHICLE_HOURS = 48  # Lagebild der Fahrzeuge; ihre Spuren bleiben nach track_days im Verlauf


def cleanup(con: sqlite3.Connection, history_days: int, track_days: int) -> None:
    """Verlauf älter als history_days, Flug-/Schiffsspuren älter als track_days löschen (0 = unbegrenzt).

    Gezählt wird ab dem letzten erfolgreichen Abruf, nicht ab heute: Steht das Gerät offline,
    kommt nichts Neues hinzu - dann soll auch nichts Altes verschwinden.
    """
    last_data = con.execute("SELECT MAX(last_ok) FROM sources").fetchone()[0]
    now = min(int(time.time()), last_data) if last_data else int(time.time())
    if history_days > 0:
        limit = now - history_days * 86400
        con.execute("DELETE FROM events WHERE ts < ?", (limit,))
        con.execute("DELETE FROM weather_history WHERE ts < ?", (limit,))
        con.execute("DELETE FROM station_history WHERE ts < ?", (limit,))
        con.execute("DELETE FROM radiation_history WHERE ts < ?", (limit,))
    if track_days > 0:
        con.execute("DELETE FROM track_points WHERE ts < ?", (now - track_days * 86400,))
        con.execute("DELETE FROM vehicle_seen WHERE last_ts < ?", (now - track_days * 86400,))
    con.execute("DELETE FROM vehicles WHERE seen < ?", (now - LIVE_VEHICLE_HOURS * 3600,))
    con.commit()
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
