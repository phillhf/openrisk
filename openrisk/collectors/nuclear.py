"""Kernkraftwerke weltweit (GeoNuclearData: rund 800 Reaktoren aus IAEA PRIS und WNA, Lizenz ODbL).

Je Reaktor: Status, Typ, Leistung, Bau-, Betriebs- und Abschaltdatum. Reaktoren am selben Standort werden
zu einem Kraftwerk zusammengefasst; dessen Status ist der „lebendigste“ seiner Reaktoren (ein Block in
Betrieb = Kraftwerk in Betrieb). Reaktoren ohne Koordinaten (meist nur geplante) fehlen.
Die Quelle wird selten gepflegt (Stand der Daten steht in der Oberfläche) - monatlich nachsehen reicht.
Ergebnis: data/geo/nuclear.geojson
"""
from __future__ import annotations

import json
import os
import re

URL = "https://raw.githubusercontent.com/cristianst85/GeoNuclearData/master/data/json/denormalized/nuclear_power_plants.json"
COMMITS = "https://api.github.com/repos/cristianst85/GeoNuclearData/commits"
# Status der Quelle -> eigener Schlüssel; Reihenfolge = Vorrang für den Status des Kraftwerks
STATUS = {"Operational": "operational", "Suspended Operation": "suspended", "Under Construction": "construction",
          "Planned": "planned", "Suspended Construction": "planned", "Shutdown": "shutdown",
          "Decommissioning Completed": "shutdown", "Never Commissioned": "cancelled",
          "Cancelled Construction": "cancelled", "Unknown": "unknown"}
ORDER = ("operational", "suspended", "construction", "planned", "shutdown", "cancelled", "unknown")


def plant_name(names: list[str]) -> str:
    """Gemeinsamer Name der Blöcke: „Biblis-A“, „Biblis-B“ -> „Biblis“."""
    if len(names) == 1:
        return re.sub(r"[\s-]+(\d+|[A-Z])$", "", names[0]) or names[0]
    prefix = re.sub(r"[\s\-(]*[\dA-Z]?$", "", os.path.commonprefix(names)).strip(" -(")
    return prefix or names[0]


def plants(reactors: list[dict]) -> list[dict]:
    sites: dict[tuple, list[dict]] = {}
    for r in reactors:
        if r.get("Latitude") is None or r.get("Longitude") is None:
            continue
        sites.setdefault((round(r["Latitude"], 2), round(r["Longitude"], 2)), []).append(r)
    features = []
    for (lat, lon), group in sites.items():
        group.sort(key=lambda r: r["Name"])
        blocks = [{"name": r["Name"], "status": STATUS.get(r.get("Status"), "unknown"), "type": r.get("ReactorType"),
                   "mw": r.get("Capacity"), "from": (r.get("OperationalFrom") or "")[:4],
                   "to": (r.get("OperationalTo") or "")[:4], "built": (r.get("ConstructionStartAt") or "")[:4]}
                  for r in group]
        statuses = {b["status"] for b in blocks}
        status = next(s for s in ORDER if s in statuses or s == "unknown")
        props = {"name": plant_name([r["Name"] for r in group]), "country": group[0].get("CountryCode") or "",
                 "status": status, "reactors": len(blocks),
                 "active": sum(b["status"] == "operational" for b in blocks),
                 "mw": sum(b["mw"] or 0 for b in blocks if b["status"] in ("operational", "suspended")),
                 "blocks": json.dumps(blocks, ensure_ascii=False)}
        features.append({"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [lon, lat]}})
    # große, aktive Kraftwerke zuletzt zeichnen (liegen dann oben)
    features.sort(key=lambda f: (ORDER.index(f["properties"]["status"]) * -1, f["properties"]["mw"]))
    return features


def collect(ctx) -> int:
    reactors = ctx.http.get(URL, timeout=60).raise_for_status().json()
    stand = ""
    try:  # Stand der Daten = letzte Änderung der Datei
        commits = ctx.http.get(COMMITS, params={"path": "data/json/denormalized/nuclear_power_plants.json",
                                                "per_page": 1}, timeout=30).json()
        stand = commits[0]["commit"]["committer"]["date"][:10]
    except Exception:  # noqa: BLE001 - nur eine Zusatzangabe
        pass
    features = plants(reactors)
    for f in features:
        f["properties"]["stand"] = stand
    path = ctx.data_dir / "geo" / "nuclear.geojson"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"type": "FeatureCollection", "stand": stand, "features": features},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    return len(features)
