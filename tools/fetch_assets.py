"""Alle statischen Dateien laden, die die Oberfläche offline braucht.

  - JavaScript-Bibliotheken (MapLibre GL, PMTiles, Protomaps-Kartenstil)  -> web/vendor/
  - Kartenschriften und Symbole                                           -> web/vendor/
  - Natural-Earth-Ländergrenzen und Städte (Ortsverzeichnis, Risikokarte) -> data/geo/

    python tools/fetch_assets.py

Muss nur einmal (mit Internet) laufen; die Ordner können danach auf den Pi kopiert werden.
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from openrisk import assets  # noqa: E402
from openrisk.assets import VENDOR, download  # noqa: E402
from openrisk.geo import simplify  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GEO = ROOT / "data" / "geo"

FONTS = ["Noto Sans Regular", "Noto Sans Medium", "Noto Sans Italic"]

NE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"


def fetch_vendor(client: httpx.Client) -> None:
    for rel, url in assets.files().items():
        download(client, url, VENDOR / rel)
        print(f"  {rel}")
    jobs = [(f"{assets.ASSETS}fonts/{quote(font)}/{start}-{start + 255}.pbf",
             VENDOR / "fonts" / font / f"{start}-{start + 255}.pbf")
            for font in FONTS for start in range(0, 65536, 256)]
    with ThreadPoolExecutor(12) as pool:
        found = sum(pool.map(lambda j: download(client, j[0], j[1], optional=True), jobs))
    print(f"  Schriften: {found} Dateien")


def slim_geometry(geometry: dict) -> dict:
    """Ländergrenzen vereinfachen (~1 km) - für Risiko-Einfärbung und Ländertest völlig ausreichend,
    spart aber über die Hälfte an Datei- und Arbeitsspeicher."""
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    slim = []
    for polygon in polygons:
        rings = [[[round(x, 3), round(y, 3)] for x, y in simplify([tuple(p[:2]) for p in ring], 0.01)]
                 for ring in polygon]
        rings = [ring for ring in rings if len(ring) >= 4]
        if rings:
            slim.append(rings)
    return {"type": "MultiPolygon", "coordinates": slim}


def uniq(names) -> list[str]:
    out = []
    for n in names:
        if n and n not in out:
            out.append(n)
    return out


def fetch_geo(client: httpx.Client) -> None:
    GEO.mkdir(parents=True, exist_ok=True)
    countries = client.get(NE + "ne_50m_admin_0_countries.geojson").raise_for_status().json()
    places = client.get(NE + "ne_50m_populated_places.geojson").raise_for_status().json()

    features, gaz_countries = [], []
    for f in countries["features"]:
        p = f["properties"]
        iso = p["ADM0_A3"]
        name = p.get("NAME_DE") or p["NAME"]
        features.append({
            "type": "Feature",
            "properties": {"iso": iso, "name": name},
            "geometry": slim_geometry(f["geometry"]),
        })
        gaz_countries.append({
            "iso": iso, "name": name, "lat": p["LABEL_Y"], "lon": p["LABEL_X"],
            "names": uniq([p.get("NAME_DE"), p.get("NAME_EN"), p["NAME"], p.get("NAME_LONG"), p.get("ADMIN")]),
        })
    (GEO / "countries.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")

    gaz_places = []
    for f in places["features"]:
        p = f["properties"]
        gaz_places.append({
            "name": p.get("NAME_DE") or p["NAME"], "iso": p["ADM0_A3"],
            "lat": round(p["LATITUDE"], 4), "lon": round(p["LONGITUDE"], 4),
            "pop": int(p.get("POP_MAX") or 0), "capital": bool(p.get("ADM0CAP")),
            "names": uniq([p.get("NAME_DE"), p.get("NAME_EN"), p["NAME"], p.get("NAMEASCII")]),
        })
    (GEO / "gazetteer.json").write_text(
        json.dumps({"countries": gaz_countries, "places": gaz_places}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")
    print(f"  {len(features)} Länder, {len(gaz_places)} Orte")


def main() -> None:
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "OpenRisk/0.1"}) as client:
        print("Bibliotheken, Schriften, Symbole ...")
        fetch_vendor(client)
        print("Natural Earth ...")
        fetch_geo(client)
    print("Fertig.")


if __name__ == "__main__":
    sys.exit(main())
