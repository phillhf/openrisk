"""Öl-Pipelines weltweit aus OpenStreetMap laden.

Ergebnis: data/geo/pipelines.geojson (vereinfachte Linien, wenige MB).

    python tools/fetch_pipelines.py

Einmal laden genügt; Pipelines ändern sich selten.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]

import overpass  # noqa: E402
from openrisk.geo import simplify  # noqa: E402

OUT = ROOT / "data" / "geo" / "pipelines.geojson"
QUERY = """way["man_made"="pipeline"]["substance"~"oil|petroleum",i];
out tags geom;"""
TOLERANCE = 0.004  # Vereinfachung in Grad (~400 m)
MIN_KM = 1.0  # unbenannte Kurzstücke (Werksgelände, Tanklager) weglassen


def length_km(pts: list[tuple[float, float]]) -> float:
    total = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        total += math.hypot((x2 - x1) * math.cos(math.radians((y1 + y2) / 2)), y2 - y1) * 111.2
    return total


def to_feature(el: dict) -> dict | None:
    tags = el.get("tags", {})
    pts = [(g["lon"], g["lat"]) for g in el.get("geometry", []) if g]
    named = tags.get("name") or tags.get("operator") or tags.get("ref")
    if len(pts) < 2 or (not named and length_km(pts) < MIN_KM):
        return None
    return {
        "type": "Feature", "properties": {k: tags[k] for k in ("name", "operator", "substance") if tags.get(k)},
        "geometry": {"type": "LineString",
                     "coordinates": [[round(x, 4), round(y, 4)] for x, y in simplify(pts, TOLERANCE)]},
    }


def main() -> None:
    features = [f for el in overpass.world(QUERY, ROOT / "data" / "geo" / "pipelines_cache")
                if el.get("type") == "way" and (f := to_feature(el))]
    OUT.write_text(json.dumps({"type": "FeatureCollection", "features": features},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Fertig: {OUT} ({len(features)} Linien, {OUT.stat().st_size / 1e6:.1f} MB)")
    print("Datenquelle: © OpenStreetMap-Mitwirkende (ODbL)")


if __name__ == "__main__":
    sys.exit(main())
