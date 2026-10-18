"""Kritische Internet-Infrastruktur weltweit.

  - Seekabel und ihre Anlandepunkte aus der Submarine Cable Map von TeleGeography (frei, ohne Konto;
    Lizenz CC BY-NC-SA 3.0 - private, nicht kommerzielle Nutzung). Über 700 Kabel mit Länge, Eigentümern,
    Inbetriebnahme und Anlandepunkten; geplante Kabel sind gekennzeichnet.
  - Internetknoten (Internet Exchange Points, IXP) und Rechenzentren aus PeeringDB (frei, ohne Konto).
    PeeringDB kennt die Knoten nur über ihre Standorte: ein Knoten wird an seinem Rechenzentrum mit den
    meisten angeschlossenen Netzen im eigenen Land eingezeichnet; Knoten ohne angeschlossene Netze fehlen.
Ergebnis: data/geo/cables.geojson, landings.geojson, ixp.geojson, datacenters.geojson. Monatlich erneuert;
ohne Internet bleibt der letzte Stand.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

CABLES = "https://www.submarinecablemap.com/api/v3/"
PEERINGDB = "https://www.peeringdb.com/api/"
FILES = ("cables", "landings", "ixp", "datacenters")


def ready(data_dir) -> bool:
    return all((data_dir / "geo" / f"{name}.geojson").exists() for name in FILES)


def _write(path, features: list[dict]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False,
                              separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _round(coords):
    """Koordinaten auf 4 Stellen (rund 10 m) kürzen - halbiert die Dateigröße."""
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], 4), round(coords[1], 4)]
    return [_round(c) for c in coords]


def _details(http, cable_id: str) -> dict:
    try:
        r = http.get(f"{CABLES}cable/{cable_id}.json", timeout=30)
        return r.json() if r.status_code == 200 else {}
    except Exception:  # einzelne Details dürfen fehlen, das Kabel wird trotzdem gezeigt
        return {}


def cables(geo: dict, details: dict[str, dict]) -> list[dict]:
    features = []
    for f in geo["features"]:
        cid = f["properties"]["id"]
        d = details.get(cid, {})
        props = {"id": cid, "name": f["properties"]["name"], "color": f["properties"].get("color") or "#22d3ee",
                 "planned": bool(d.get("is_planned"))}
        for key, value in (("length", d.get("length")), ("owners", d.get("owners")), ("rfs", d.get("rfs")),
                           ("url", d.get("url")), ("landings", " · ".join(lp["name"] for lp in d.get("landing_points", [])))):
            if value:
                props[key] = value
        features.append({"type": "Feature", "properties": props,
                         "geometry": {"type": f["geometry"]["type"], "coordinates": _round(f["geometry"]["coordinates"])}})
    return features


def landings(geo: dict, details: dict[str, dict]) -> list[dict]:
    at: dict[str, list[str]] = {}
    for d in details.values():
        for lp in d.get("landing_points", []):
            at.setdefault(lp["id"], []).append(d["name"])
    features = []
    for f in geo["features"]:
        p = f["properties"]
        props = {"id": p["id"], "name": p["name"], "planned": bool(p.get("is_tbd"))}
        if at.get(p["id"]):
            props["cables"] = " · ".join(sorted(at[p["id"]]))
            props["count"] = len(at[p["id"]])
        features.append({"type": "Feature", "properties": props,
                         "geometry": {"type": "Point", "coordinates": _round(f["geometry"]["coordinates"])}})
    return features


def _point(lon, lat) -> dict:
    return {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]}


def peering(ixs: list[dict], facs: list[dict], links: list[dict]) -> tuple[list[dict], list[dict]]:
    """Internetknoten (am größten ihrer Standorte) und Rechenzentren als GeoJSON-Punkte."""
    located = {f["id"]: f for f in facs if f.get("latitude") is not None and f.get("longitude") is not None}
    sites: dict[int, list[dict]] = {}
    for link in links:
        if link["fac_id"] in located:
            sites.setdefault(link["ix_id"], []).append(located[link["fac_id"]])
    ix_features = []
    for ix in ixs:
        if ix.get("status", "ok") != "ok" or not sites.get(ix["id"]) or not ix.get("net_count"):
            continue  # ohne angeschlossene Netze: verwaist oder nie in Betrieb
        # Standort im eigenen Land bevorzugen (ein Züricher Knoten mit Ablegern in Frankfurt gehört nach Zürich)
        home = [f for f in sites[ix["id"]] if f.get("country") == ix.get("country")] or sites[ix["id"]]
        main = max(home, key=lambda f: f.get("net_count") or 0)
        props = {"name": ix["name"], "members": ix.get("net_count") or 0, "sites": len(sites[ix["id"]])}
        for key, value in (("long", ix.get("name_long") if ix.get("name_long") != ix["name"] else None),
                           ("city", ix.get("city")), ("country", ix.get("country")), ("url", ix.get("website")),
                           ("site", main["name"])):
            if value:
                props[key] = value
        ix_features.append({"type": "Feature", "properties": props,
                            "geometry": _point(main["longitude"], main["latitude"])})
    dc_features = []
    for f in located.values():
        if f.get("status", "ok") != "ok":
            continue
        props = {"name": f["name"], "networks": f.get("net_count") or 0, "ixps": f.get("ix_count") or 0}
        for key, value in (("operator", f.get("org_name")), ("city", f.get("city")), ("country", f.get("country")),
                           ("url", f.get("website"))):
            if value:
                props[key] = value
        dc_features.append({"type": "Feature", "properties": props, "geometry": _point(f["longitude"], f["latitude"])})
    # größte zuerst: die Karte zeichnet sie dann oben und blendet kleinere bei Überlappung aus
    ix_features.sort(key=lambda f: -f["properties"]["members"])
    dc_features.sort(key=lambda f: -f["properties"]["networks"])
    return ix_features, dc_features


def _peeringdb(http, kind: str, fields: str) -> list[dict]:
    r = http.get(f"{PEERINGDB}{kind}", params={"fields": fields}, timeout=120)
    r.raise_for_status()
    return r.json()["data"]


def collect(ctx) -> int:
    """Seekabel und PeeringDB getrennt: klappt nur eine Quelle, ist die andere trotzdem gespeichert."""
    folder = ctx.data_dir / "geo"
    folder.mkdir(parents=True, exist_ok=True)
    count, errors = 0, []
    try:
        cable_geo = ctx.http.get(f"{CABLES}cable/cable-geo.json", timeout=120).raise_for_status().json()
        landing_geo = ctx.http.get(f"{CABLES}landing-point/landing-point-geo.json", timeout=120).raise_for_status().json()
        ids = sorted({f["properties"]["id"] for f in cable_geo["features"]})
        with ThreadPoolExecutor(6) as pool:
            details = {cid: d for cid, d in zip(ids, pool.map(lambda c: _details(ctx.http, c), ids)) if d}
        for name, features in (("cables", cables(cable_geo, details)), ("landings", landings(landing_geo, details))):
            _write(folder / f"{name}.geojson", features)
            count += len(features)
    except Exception as e:  # noqa: BLE001 - Grund in den Status, PeeringDB trotzdem versuchen
        errors.append(f"Seekabel: {type(e).__name__}")
    try:
        ixs = _peeringdb(ctx.http, "ix", "id,name,name_long,city,country,website,net_count,status")
        facs = _peeringdb(ctx.http, "fac", "id,name,org_name,city,country,latitude,longitude,net_count,ix_count,"
                                           "website,status")
        links = _peeringdb(ctx.http, "ixfac", "ix_id,fac_id")
        ix_features, dc_features = peering(ixs, facs, links)
        _write(folder / "ixp.geojson", ix_features)
        _write(folder / "datacenters.geojson", dc_features)
        count += len(ix_features) + len(dc_features)
    except Exception as e:  # noqa: BLE001
        errors.append(f"PeeringDB: {type(e).__name__}")
    if errors:
        raise RuntimeError("; ".join(errors) + " – neuer Versuch stündlich")
    return count
