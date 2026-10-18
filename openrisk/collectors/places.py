"""Ortsverzeichnis für die Ortssuche (GeoNames, CC BY 4.0).

  - weltweit alle Orte ab 500 Einwohnern (cities500, rund 235.000)
  - Deutschland vollständig bis zum Dorf und Ortsteil (DE, Merkmalsklasse P, rund 82.000)
Daraus entsteht data/geo/places.db (SQLite, ~30 MB). Gesucht wird nach dem Anfang des Namens;
große Städte sind zusätzlich unter ihren anderen Schreibweisen zu finden (München/Munich/Monaco).
Wird alle 90 Tage erneuert; ohne Internet bleibt das vorhandene Verzeichnis.
"""
from __future__ import annotations

import io
import sqlite3
import zipfile

from ..places import normalize

BASE = "https://download.geonames.org/export/dump/"
ALIAS_MIN_POP = 100_000  # andere Schreibweisen nur für große Städte - sonst wird das Verzeichnis riesig
# Bundesländer und Bundesländer Österreichs auf Deutsch (GeoNames liefert englische Namen)
REGIONS_DE = {
    "DE.01": "Baden-Württemberg", "DE.02": "Bayern", "DE.03": "Bremen", "DE.04": "Hamburg", "DE.05": "Hessen",
    "DE.06": "Niedersachsen", "DE.07": "Nordrhein-Westfalen", "DE.08": "Rheinland-Pfalz", "DE.09": "Saarland",
    "DE.10": "Schleswig-Holstein", "DE.11": "Brandenburg", "DE.12": "Mecklenburg-Vorpommern", "DE.13": "Sachsen",
    "DE.14": "Sachsen-Anhalt", "DE.15": "Thüringen", "DE.16": "Berlin",
    "AT.01": "Burgenland", "AT.02": "Kärnten", "AT.03": "Niederösterreich", "AT.04": "Oberösterreich",
    "AT.05": "Salzburg", "AT.06": "Steiermark", "AT.07": "Tirol", "AT.08": "Vorarlberg", "AT.09": "Wien",
}
VERSION = 2  # bei Änderungen am Aufbau erhöhen - dann baut OpenRisk das Verzeichnis gleich neu auf
SCHEMA = f"""
CREATE TABLE places (id INTEGER PRIMARY KEY, name TEXT, region TEXT, country TEXT, lat REAL, lon REAL,
                     population INTEGER, kind TEXT,
                     iso TEXT, ags TEXT, elevation INTEGER, timezone TEXT);  -- ags: Gemeindeschlüssel (DE)
CREATE TABLE names (key TEXT, place INTEGER, PRIMARY KEY (key, place)) WITHOUT ROWID;
PRAGMA user_version = {VERSION};
"""


def ready(data_dir) -> bool:
    """Ist das Verzeichnis vorhanden und auf dem aktuellen Stand?"""
    path = data_dir / "geo" / "places.db"
    if not path.exists():
        return False
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return con.execute("PRAGMA user_version").fetchone()[0] == VERSION
    finally:
        con.close()


def _download(http, name: str, dest) -> None:
    with http.stream("GET", BASE + name, timeout=300) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)


def _lines(http, name: str, tmp_dir):
    """Zeilen einer GeoNames-Datei; ZIPs werden auf die Platte geladen und gestreamt (wenig Speicher)."""
    path = tmp_dir / name
    _download(http, name, path)
    try:
        if name.endswith(".zip"):
            with zipfile.ZipFile(path) as z:
                member = name.replace(".zip", ".txt")
                with z.open(member) as raw:
                    yield from io.TextIOWrapper(raw, encoding="utf-8")
        else:
            with path.open(encoding="utf-8") as f:
                yield from f
    finally:
        path.unlink(missing_ok=True)


def _latin(name: str) -> bool:
    return len(name) >= 3 and not name.isupper() and all(ch < "ɐ" or not ch.isalpha() for ch in name)


def _german_names(geo) -> dict[tuple[str, str], list[tuple[str, float, float]]]:
    """Deutsche Namen großer Städte aus Natural Earth (z. B. Moskau statt Moscow), nach (Land, Name)."""
    out: dict[tuple[str, str], list[tuple[str, float, float]]] = {}
    for p in geo.cities:
        for name in p["names"]:
            out.setdefault((p["iso"], name), []).append((p["name"], p["lat"], p["lon"]))
    return out


def collect(ctx) -> int:
    geo_dir = ctx.data_dir / "geo"
    geo_dir.mkdir(parents=True, exist_ok=True)
    iso3, countries = {}, {}
    for line in _lines(ctx.http, "countryInfo.txt", geo_dir):
        if not line.startswith("#"):
            cols = line.rstrip("\n").split("\t")
            iso3[cols[0]] = cols[1]
            countries[cols[0]] = ctx.geo.country_names.get(cols[1]) or cols[4]
    regions = {}
    for line in _lines(ctx.http, "admin1CodesASCII.txt", geo_dir):
        code, name = line.split("\t")[:2]
        regions[code] = REGIONS_DE.get(code, name)
    german = _german_names(ctx.geo)

    tmp = geo_dir / "places.db.tmp"
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    seen: set[int] = set()
    count = 0

    def add(rows):
        nonlocal count
        places, names = [], []
        for cols in rows:
            gid = int(cols[0])
            if gid in seen:
                continue
            seen.add(gid)
            name, lat, lon, cc = cols[1], float(cols[4]), float(cols[5]), cols[8]
            population = int(cols[14] or 0)
            iso = iso3.get(cc, cc)
            # deutscher Name, falls Natural Earth die Stadt kennt (gleicher Name, gleiche Lage)
            for de_name, de_lat, de_lon in german.get((iso, name), ()):
                if abs(de_lat - lat) < 0.2 and abs(de_lon - lon) < 0.2:
                    name = de_name
                    break
            elevation = cols[15] or (cols[16] if cols[16] not in ("", "-9999") else "")
            places.append((gid, name, regions.get(f"{cc}.{cols[10]}", ""), countries.get(cc, cc), round(lat, 5),
                           round(lon, 5), population, cols[7], iso, cols[13] if cc == "DE" else "",
                           int(elevation) if elevation else None, cols[17] if len(cols) > 17 else ""))
            keys = {normalize(name), normalize(cols[1]), normalize(cols[2])}
            if population >= ALIAS_MIN_POP:
                keys |= {normalize(alt) for alt in cols[3].split(",") if _latin(alt)}
            names += [(key, gid) for key in keys if key]
        con.executemany("INSERT INTO places VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", places)
        con.executemany("INSERT OR IGNORE INTO names VALUES (?, ?)", names)
        count += len(places)

    def batches(lines, keep):
        batch = []
        for line in lines:
            cols = line.rstrip("\n").split("\t")
            if len(cols) >= 15 and keep(cols):
                batch.append(cols)
                if len(batch) >= 5000:
                    add(batch)
                    batch = []
        add(batch)

    try:
        batches(_lines(ctx.http, "cities500.zip", geo_dir), lambda c: True)
        batches(_lines(ctx.http, "DE.zip", geo_dir), lambda c: c[6] == "P")  # Deutschland bis zum Ortsteil
        # Länder aus Natural Earth (deutsche Namen)
        for iso, loc in ctx.geo.countries.items():
            gid = -len(seen) - 1
            seen.add(gid)
            con.execute("INSERT INTO places VALUES (?, ?, '', '', ?, ?, 0, 'country', ?, '', NULL, '')",
                        (gid, loc.name, loc.lat, loc.lon, iso))
            con.execute("INSERT OR IGNORE INTO names VALUES (?, ?)", (normalize(loc.name), gid))
        con.execute("CREATE INDEX places_pop ON places (population)")
        con.commit()
    finally:
        con.close()
    tmp.replace(geo_dir / "places.db")
    return count
