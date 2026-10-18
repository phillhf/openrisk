"""Stadt- und Gemeindeinfos für das Orts-Popup.

Deutschland, alle Gemeinden:
  - Statistisches Bundesamt, Gemeindeverzeichnis (amtlich, vierteljährlich): Einwohner gesamt/männlich/
    weiblich, Fläche, Einwohner je km², Postleitzahl, Grad der Verstädterung, Reisegebiet, Kreis
  - Wikidata: Kfz-Kennzeichen (je Kreis), Bürgermeister/in, Website, Höhe, Vorwahl, Partnerstädte
Weltweit, Städte ab 100.000 Einwohnern (über die GeoNames-Nummer aus dem Ortsverzeichnis):
  - Wikidata: Fläche, Höhe, Gründung, Website, Bürgermeister/in, Partnerstädte

Ergebnis: data/geo/cityinfo.db (SQLite, je Ort ein JSON-Eintrag). Läuft beim ersten Start sofort und
danach alle 14 Tage; ohne Internet bleibt der letzte Stand.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
import zipfile

DESTATIS = ("https://www.destatis.de/DE/Themen/Laender-Regionen/Regionales/Gemeindeverzeichnis/Administrativ/"
            "Archiv/GVAuszugQ/AuszugGV{}QAktuell.xlsx?__blob=publicationFile")
WIKIDATA = "https://query.wikidata.org/sparql"
WORLD_MIN_POP = 100_000
BATCH = 400  # GeoNames-Nummern je Wikidata-Abfrage
VERSION = 1
FAILED: list[str] = []  # Abfragen, die im aktuellen Lauf nicht geklappt haben
log = logging.getLogger("openrisk.cityinfo")
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
TYPES = {"61": "kreisfreie Stadt", "62": "Stadtkreis", "63": "Stadt", "64": "Gemeinde", "65": "Gemeinde",
         "66": "gemeindefreies Gebiet", "67": "gemeindefreies Gebiet"}
DISTRICTS = {"41": "kreisfreie Stadt", "42": "Stadtkreis", "43": "Kreis", "44": "Landkreis", "45": "Regionalverband"}
SCHEMA = f"""
CREATE TABLE gemeinden (ags TEXT PRIMARY KEY, data TEXT) WITHOUT ROWID;
CREATE TABLE kreise (key TEXT PRIMARY KEY, data TEXT) WITHOUT ROWID;
CREATE TABLE world (gid INTEGER PRIMARY KEY, data TEXT);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
PRAGMA user_version = {VERSION};
"""


def ready(data_dir) -> bool:
    path = data_dir / "geo" / "cityinfo.db"
    if not path.exists():
        return False
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        world = con.execute("SELECT value FROM meta WHERE key = 'world'").fetchone()
        return con.execute("PRAGMA user_version").fetchone()[0] == VERSION and bool(world and world[0] == "1")
    finally:
        con.close()


# ------------------------------------------------------------------ Destatis

def _rows(xlsx: zipfile.ZipFile):
    """Zeilen des Gemeindeverzeichnisses als {Spalte: Wert} - gestreamt, das Blatt ist über 10 MB groß."""
    strings = ["".join(si.itertext()) for si in ET.fromstring(xlsx.read("xl/sharedStrings.xml")).iter(f"{NS}si")]
    sheet = next(n for n in xlsx.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)
                 and xlsx.getinfo(n).file_size > 1_000_000)
    with xlsx.open(sheet) as f:
        for _, el in ET.iterparse(f):
            if el.tag == f"{NS}row":
                row = {}
                for c in el.iter(f"{NS}c"):
                    v = c.find(f"{NS}v")
                    if v is not None and v.text is not None:
                        row[re.sub(r"\d", "", c.get("r"))] = strings[int(v.text)] if c.get("t") == "s" else v.text
                yield row
                el.clear()


def _stand(title: str) -> tuple[int, int, int]:
    m = re.search(r"(\d\d)\.(\d\d)\.(\d{4})", title)
    return (int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else (0, 0, 0)


def _number(text: str | None) -> float | None:
    try:
        return float(str(text).replace(",", "."))
    except (TypeError, ValueError):
        return None


def destatis(http, tmp_dir) -> tuple[dict, dict, str]:
    """Neuestes Quartal des Gemeindeverzeichnisses (die vier Dateien „Aktuell“ sind je ein Quartal)."""
    best = None
    for quarter in (1, 2, 3, 4):
        path = tmp_dir / f"gv{quarter}.xlsx"
        r = http.get(DESTATIS.format(quarter), timeout=120)
        if r.status_code != 200:
            continue
        path.write_bytes(r.content)
        with zipfile.ZipFile(path) as z:
            rows = _rows(z)
            title = next(rows).get("A", "")
            rows.close()  # schließt das Blatt wieder (sonst lässt sich die Datei unter Windows nicht löschen)
        if best is None or _stand(title) > best[0]:
            if best:
                best[1].unlink(missing_ok=True)
            best = (_stand(title), path, title)
        else:
            path.unlink(missing_ok=True)
    if not best:
        raise RuntimeError("Gemeindeverzeichnis nicht erreichbar")
    gemeinden, kreise, stand = {}, {}, ""
    try:
        with zipfile.ZipFile(best[1]) as z:
            for row in _rows(z):
                kind, key = row.get("A"), row.get("C", "") + row.get("D", "") + row.get("E", "")
                if row.get("I") and "Jahr" in row.get("I", "") and not stand:
                    stand = row["I"].split(" ")[0]  # Stichtag der Einwohnerzahlen
                if kind == "40" and len(key) == 5:
                    kreise[key] = {"name": row.get("H", ""), "kind": DISTRICTS.get(row.get("B", ""), "")}
                elif kind == "60" and len(key) == 5 and row.get("G"):
                    population = _number(row.get("J"))
                    gemeinden[key + row["G"]] = {k: v for k, v in {
                        "name": row.get("H", ""), "type": TYPES.get(row.get("B", ""), ""),
                        "area_km2": _number(row.get("I")), "population": int(population) if population else None,
                        "male": int(_number(row.get("K")) or 0) or None, "female": int(_number(row.get("L")) or 0) or None,
                        "plz": row.get("N", ""), "urbanisation": row.get("T", ""), "region": row.get("R", ""),
                    }.items() if v not in (None, "")}
    finally:
        best[1].unlink(missing_ok=True)
    return gemeinden, kreise, stand


# ------------------------------------------------------------------ Wikidata

def sparql(http, query: str) -> list[dict]:
    """Wikidata-Abfrage; bricht die Antwort ab (Zeitlimit des Dienstes), wird einmal wiederholt.
    Klappt es auch dann nicht, fehlt nur dieses eine Merkmal - der Rest wird trotzdem gespeichert."""
    for attempt in range(2):
        try:
            r = http.post(WIKIDATA, data={"query": query}, timeout=90,
                          headers={"Accept": "application/sparql-results+json"})
            r.raise_for_status()
            return [{k: v["value"] for k, v in b.items()} for b in r.json()["results"]["bindings"]]
        except (ValueError, KeyError) as e:  # abgeschnittene Antwort
            error = e
        except Exception as e:  # noqa: BLE001 - Netzfehler
            if attempt or "ConnectError" in type(e).__name__:
                raise
            error = e
        time.sleep(5)
    log.warning("Wikidata-Abfrage abgebrochen (%s)", type(error).__name__)
    FAILED.append(query)
    return []


def _collect_into(target: dict, rows: list[dict], key: str, field: str, value: str, many: bool = False,
                  limit: int = 12) -> None:
    for row in rows:
        if value not in row:  # z. B. „unbekanntes Datum“
            continue
        entry = target.setdefault(row[key], {})
        if many:
            values = entry.setdefault(field, [])
            if row[value] not in values and len(values) < limit:
                values.append(row[value])
        else:
            entry.setdefault(field, row[value])


def wikidata_germany(http) -> tuple[dict, dict]:
    live = "FILTER NOT EXISTS { ?g wdt:P576 [] }"  # aufgelöste Gemeinden weglassen
    plates: dict = {}
    _collect_into(plates, sparql(http, "SELECT ?key ?kfz WHERE { ?g wdt:P440 ?key; wdt:P395 ?kfz. "
                                       f"{live} }}"), "key", "plates", "kfz", many=True)
    extra: dict = {}
    _collect_into(extra, sparql(http, f"SELECT ?ags ?web WHERE {{ ?g wdt:P439 ?ags; wdt:P856 ?web. {live} }}"),
                  "ags", "web", "web")
    _collect_into(extra, sparql(http, f"SELECT ?ags ?elev WHERE {{ ?g wdt:P439 ?ags; wdt:P2044 ?elev. {live} }}"),
                  "ags", "elevation", "elev")
    _collect_into(extra, sparql(http, f"SELECT ?ags ?tel WHERE {{ ?g wdt:P439 ?ags; wdt:P473 ?tel. {live} }}"),
                  "ags", "phone", "tel", many=True, limit=4)
    _collect_into(extra, sparql(http, "SELECT ?ags ?mayor WHERE { ?g wdt:P439 ?ags; p:P6 ?ms. "
                                      f"{live} ?ms a wikibase:BestRank; ps:P6 ?m. FILTER NOT EXISTS {{ ?ms pq:P582 [] }} "
                                      '?m wdt:P31 wd:Q5; rdfs:label ?mayor. FILTER(LANG(?mayor) = "de") }'),
                  "ags", "mayor", "mayor")
    _collect_into(extra, sparql(http, f"SELECT ?ags ?partner WHERE {{ ?g wdt:P439 ?ags; wdt:P190 ?p. {live} "
                                      '?p rdfs:label ?partner. FILTER(LANG(?partner) = "de") }'),
                  "ags", "partners", "partner", many=True)
    return plates, extra


def wikidata_world(http, gids: list[int]) -> dict:
    out: dict = {}
    for start in range(0, len(gids), BATCH):
        values = " ".join(f'"{gid}"' for gid in gids[start:start + BATCH])
        head = f"VALUES ?gid {{ {values} }} ?c wdt:P1566 ?gid."
        _collect_into(out, sparql(http, f"SELECT ?gid ?area WHERE {{ {head} ?c wdt:P2046 ?area. }}"),
                      "gid", "area_km2", "area")
        _collect_into(out, sparql(http, f"SELECT ?gid ?web WHERE {{ {head} ?c wdt:P856 ?web. }}"), "gid", "web", "web")
        _collect_into(out, sparql(http, f"SELECT ?gid (YEAR(?inc) AS ?founded) WHERE {{ {head} ?c wdt:P571 ?inc. }}"),
                      "gid", "founded", "founded")
        _collect_into(out, sparql(http, f"SELECT ?gid ?mayor WHERE {{ {head} ?c p:P6 ?ms. ?ms a wikibase:BestRank; "
                                        "ps:P6 ?m. FILTER NOT EXISTS { ?ms pq:P582 [] } ?m wdt:P31 wd:Q5; "
                                        'rdfs:label ?mayor. FILTER(LANG(?mayor) IN ("de", "en", "mul")) }'),
                      "gid", "mayor", "mayor")
        _collect_into(out, sparql(http, f"SELECT ?gid ?partner WHERE {{ {head} ?c wdt:P190 ?p. "
                                        '?p rdfs:label ?partner. FILTER(LANG(?partner) = "de") }'),
                      "gid", "partners", "partner", many=True)
    for entry in out.values():  # Zahlen aus Wikidata kommen als Text
        for key in ("area_km2", "founded"):
            if key in entry:
                number = _number(entry[key])
                entry[key] = int(number) if key == "founded" and number is not None else number
    return out


# ------------------------------------------------------------------ Sammler

def collect(ctx) -> int:
    geo_dir = ctx.data_dir / "geo"
    geo_dir.mkdir(parents=True, exist_ok=True)
    FAILED.clear()
    gemeinden, kreise, stand = destatis(ctx.http, geo_dir)
    plates, extra = wikidata_germany(ctx.http)
    for key, kreis in kreise.items():
        kreis["plates"] = plates.get(key, {}).get("plates", [])
    for ags, gemeinde in gemeinden.items():
        info = extra.get(ags, {})
        if "elevation" in info:
            info["elevation"] = _number(info["elevation"])
        gemeinde.update(info)

    world, world_done = {}, False
    places_db = geo_dir / "places.db"
    if places_db.exists():  # große Städte weltweit; das Ortsverzeichnis liefert die GeoNames-Nummern
        con = sqlite3.connect(f"file:{places_db}?mode=ro", uri=True)
        try:
            gids = [r[0] for r in con.execute("SELECT id FROM places WHERE population >= ? AND id > 0 "
                                              "ORDER BY population DESC", (WORLD_MIN_POP,))]
        finally:
            con.close()
        world, world_done = wikidata_world(ctx.http, gids), True

    tmp = geo_dir / "cityinfo.db.tmp"
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        con.executescript(SCHEMA)
        dump = lambda d: json.dumps(d, ensure_ascii=False, separators=(",", ":"))  # noqa: E731
        con.executemany("INSERT INTO gemeinden VALUES (?, ?)", [(k, dump(v)) for k, v in gemeinden.items()])
        con.executemany("INSERT INTO kreise VALUES (?, ?)", [(k, dump(v)) for k, v in kreise.items()])
        con.executemany("INSERT INTO world VALUES (?, ?)", [(int(k), dump(v)) for k, v in world.items()])
        con.executemany("INSERT INTO meta VALUES (?, ?)", [("stand", stand), ("world", "1" if world_done and not FAILED else "0")])
        con.commit()
    finally:
        con.close()
    tmp.replace(geo_dir / "cityinfo.db")
    return len(gemeinden) + len(world)
