"""Militärstützpunkte weltweit aus OpenStreetMap laden.

Ergebnis: data/geo/bases.geojson (Punkte mit Name, Art und Betreiber).

    python tools/fetch_bases.py

Erfasst werden Militärbasen, Militärflugplätze, Marinestützpunkte und Kasernen,
so wie sie in OpenStreetMap eingetragen sind – also nur öffentlich Bekanntes und
je nach Land unterschiedlich vollständig. Einmal laden genügt.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import overpass  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "geo" / "bases.geojson"
QUERY = """nwr["military"~"^(base|airfield|naval_base|barracks)$"];
out tags center;"""


def main() -> None:
    features = []
    for el in overpass.world(QUERY, ROOT / "data" / "geo" / "bases_cache"):
        tags = el.get("tags", {})
        center = el.get("center") or el
        if "lat" not in center:
            continue
        name = tags.get("name:de") or tags.get("name:en") or tags.get("name") or tags.get("official_name")
        props = {"type": tags["military"]}
        if name:
            props["name"] = name
        if tags.get("operator"):
            props["operator"] = tags["operator"]
        features.append({
            "type": "Feature", "properties": props,
            "geometry": {"type": "Point", "coordinates": [round(center["lon"], 4), round(center["lat"], 4)]},
        })
    OUT.write_text(json.dumps({"type": "FeatureCollection", "features": features},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Fertig: {OUT} ({len(features)} Standorte, {OUT.stat().st_size / 1e6:.1f} MB)")
    print("Datenquelle: © OpenStreetMap-Mitwirkende (ODbL)")


if __name__ == "__main__":
    sys.exit(main())
