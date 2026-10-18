"""Radon in Deutschland (Bundesamt für Strahlenschutz).

Für Radon gibt es keine laufend messenden Stationen; das BfS veröffentlicht Karten:
  - Radon in Wohnräumen: geschätzte Radonkonzentration je Gemeinde in Bq/m³
  - Radon-Vorsorgegebiete: Gemeinden, die nach Strahlenschutzgesetz ausgewiesen sind
Beides ändert sich selten; der Sammler holt es einmal pro Woche und legt die
vereinfachten Flächen als data/geo/radon.geojson und data/geo/radon_areas.geojson ab.

Ampel für Wohnräume: grün unter 100 Bq/m³ (Referenzwert der WHO), gelb bis 300 Bq/m³
(Referenzwert in Deutschland, § 124 StrlSchG), rot darüber.
"""
from __future__ import annotations

import json

from ..geo import _in_polygon, simplify

URL = "https://www.imis.bfs.de/ogc/opendata/ows"
PAGE = 1000  # mehr gibt der Server je Abfrage nicht heraus
TOLERANCE = 0.003  # Vereinfachung in Grad (~250 m) - reicht für eine Übersichtskarte der Gemeinden


def _thin(ring: list) -> list[tuple[float, float]]:
    """Punkte, die dichter als die halbe Toleranz am vorigen liegen, vorab weglassen - das macht die
    anschließende genaue Vereinfachung um ein Vielfaches schneller (die Gemeindegrenzen sind sehr fein)."""
    step = TOLERANCE / 2
    kept = [tuple(ring[0][:2])]
    for x, y, *_ in ring[1:-1]:
        lx, ly = kept[-1]
        if abs(x - lx) > step or abs(y - ly) > step:
            kept.append((x, y))
    kept.append(tuple(ring[-1][:2]))
    return kept


def _rings(geometry: dict) -> list:
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    out = []
    for polygon in polygons:
        rings = []
        for ring in polygon:
            thinned = _thin(ring)
            slim = simplify(thinned, TOLERANCE)
            if len(slim) < 4:  # sehr kleine Gemeinde: nicht wegvereinfachen, sondern fein lassen
                slim = thinned if len(thinned) >= 4 else [tuple(p[:2]) for p in ring]
            if len(slim) >= 4:
                rings.append([[round(x, 4), round(y, 4)] for x, y in slim])
        if rings:
            out.append(rings)
    return out


def _layer(http, name: str, fields: str, props) -> list[dict]:
    """Alle Flächen einer BfS-Ebene seitenweise holen und gleich vereinfachen (wenig Speicher)."""
    features, start, total = [], 0, None
    while total is None or start < total:
        r = http.get(URL, timeout=180, params={
            "service": "WFS", "version": "2.0.0", "request": "GetFeature", "outputFormat": "application/json",
            "typeNames": f"opendata:{name}", "propertyName": f"{fields},geom", "srsName": "EPSG:4326",
            "sortBy": fields.split(",")[0], "count": PAGE, "startIndex": start})
        r.raise_for_status()
        data = r.json()
        total = int(data.get("numberMatched") or 0)
        for f in data["features"]:
            polygons = _rings(f["geometry"]) if f.get("geometry") else []
            if polygons:
                features.append({"type": "Feature", "properties": props(f["properties"]),
                                 "geometry": {"type": "MultiPolygon", "coordinates": polygons}})
        start += PAGE
        if not data["features"]:
            break
    return features


def _write(path, features: list[dict]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False,
                              separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _centroid(feature: dict) -> tuple[float, float]:
    ring = max((polygon[0] for polygon in feature["geometry"]["coordinates"]), key=len)
    return sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)


def _mark_precaution_areas(homes: list[dict], areas: list[dict]) -> None:
    """Gemeinde im Vorsorgegebiet? Gleicher Name UND Mittelpunkt in der Fläche - die vereinfachten Grenzen
    überlappen sich am Rand, ein Klick allein würde manchmal die Nachbargemeinde treffen."""
    by_name: dict[str, list[dict]] = {}
    for area in areas:
        by_name.setdefault(area["properties"].pop("short", ""), []).append(area)
    for home in homes:
        x, y = _centroid(home)
        for area in by_name.get(home["properties"].get("name") or "", []):
            if any(_in_polygon(x, y, polygon) for polygon in area["geometry"]["coordinates"]):
                home["properties"]["area"] = area["properties"]["name"]
                break


def collect(ctx) -> int:
    homes = _layer(ctx.http, "radon_wohnraeume", "AGS,GEN,AM",
                   lambda p: {"name": p.get("GEN"), "ags": p.get("AGS"),
                             **({"bq": p["AM"]} if p.get("AM") is not None else {})})
    areas = _layer(ctx.http, "radonvorsorgegebiete", "gid,NAME,name_description",
                   lambda p: {"name": p.get("name_description"), "short": (p.get("NAME") or "").strip()})
    _mark_precaution_areas(homes, areas)
    geo = ctx.data_dir / "geo"
    geo.mkdir(parents=True, exist_ok=True)
    _write(geo / "radon.geojson", homes)
    _write(geo / "radon_areas.geojson", areas)
    return len(homes)  # davon im Vorsorgegebiet: sum("area" in h["properties"] for h in homes)
