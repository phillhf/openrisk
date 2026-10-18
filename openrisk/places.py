"""Ortssuche im Ortsverzeichnis data/geo/places.db (aufgebaut von collectors/places.py)."""
from __future__ import annotations

import sqlite3
import unicodedata
from pathlib import Path

KINDS = {"country": "Land", "PPLC": "Hauptstadt", "PPLA": "Verwaltungssitz", "PPLA2": "Kreisstadt",
         "PPLX": "Ortsteil", "PPLL": "Ortschaft", "PPLF": "Hof", "PPLQ": "Wüstung", "PPLH": "historischer Ort",
         "PPLW": "zerstörter Ort", "LCTY": "Flurname"}


def normalize(text: str) -> str:
    """Suchschlüssel: klein, ohne Akzente, ß -> ss (München, Munchen und muenchen finden dasselbe)."""
    text = unicodedata.normalize("NFKD", text.casefold().replace("ß", "ss"))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.replace("-", " ").split())


def _variants(query: str) -> set[str]:
    key = normalize(query)
    # "Muenchen" soll auch "München" finden
    return {key, key.replace("ae", "a").replace("oe", "o").replace("ue", "u")} - {""}


def search(path: Path, query: str, limit: int = 12) -> list[dict] | None:
    """Treffer nach Anfang des Namens: genaue Treffer zuerst, dann die größten Orte. None = kein Verzeichnis."""
    if not path.exists():
        return None
    con = _open(path)
    try:
        found: dict[int, tuple] = {}
        variants = _variants(query)
        for key in variants:
            rows = con.execute(
                "SELECT p.*, MAX(n.key = ?) AS exact FROM names n JOIN places p ON p.id = n.place "
                "WHERE n.key >= ? AND n.key < ? GROUP BY p.id "
                "ORDER BY exact DESC, p.kind = 'country' DESC, p.population DESC LIMIT ?",
                (key, key, key + "￿", limit * 3)).fetchall()
            for r in rows:
                # Genaue Treffer zuerst, dabei zählt auch die Kurzform eines längeren Namens ("Halle" für
                # "Halle (Saale)"); genaue Treffer auf fremdsprachige Namen ("Neustadt" = Baia Mare) danach
                name = normalize(r["name"])
                level = 0 if r["exact"] and any(name.startswith(v) for v in variants) else 1 if r["exact"] else 2
                rank = (level, r["kind"] != "country", -r["population"])
                if r["id"] not in found or rank < found[r["id"]][0]:
                    found[r["id"]] = (rank, r)
    finally:
        con.close()
    return [_public(r) for _, r in sorted(found.values(), key=lambda x: x[0])[:limit]]


def _public(r: sqlite3.Row) -> dict:
    keys = r.keys()
    return {"id": r["id"], "name": r["name"], "region": r["region"], "country": r["country"], "lat": r["lat"],
            "lon": r["lon"], "population": r["population"], "kind": KINDS.get(r["kind"], ""), "code": r["kind"],
            **{k: r[k] for k in ("iso", "ags", "elevation", "timezone") if k in keys}}


def _open(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def get(path: Path, place_id: int) -> dict | None:
    if not path.exists():
        return None
    con = _open(path)
    try:
        row = con.execute("SELECT * FROM places WHERE id = ?", (place_id,)).fetchone()
        return _public(row) if row else None
    finally:
        con.close()


def near(path: Path, name: str, lat: float, lon: float) -> dict | None:
    """Ort zu einer Kartenbeschriftung: gleicher Name in der Nähe, der größte gewinnt."""
    if not path.exists():
        return None
    con = _open(path)
    try:
        rows = con.execute(
            "SELECT p.* FROM names n JOIN places p ON p.id = n.place WHERE n.key IN (%s) "
            "AND p.lat BETWEEN ? AND ? AND p.lon BETWEEN ? AND ? AND p.id > 0" % ",".join("?" * len(_variants(name))),
            (*_variants(name), lat - 0.3, lat + 0.3, lon - 0.5, lon + 0.5)).fetchall()
    finally:
        con.close()
    if not rows:
        return None
    return _public(max(rows, key=lambda r: (r["population"], -abs(r["lat"] - lat) - abs(r["lon"] - lon))))
