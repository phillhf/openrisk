"""Mobilfunkmasten, Bunker und Fernmeldeleitungen in Deutschland aus OpenStreetMap (Overpass-API).

  - Funk- und Mobilfunkmasten: tower:type=communication oder communication:mobile_phone=yes.
    OpenStreetMap kennt rund 28.000 davon - etwa ein Drittel der tatsächlichen Mobilfunkstandorte;
    die vollständige Liste der Bundesnetzagentur ist nicht als offene Daten verfügbar.
  - Bunker und Schutzräume: military=bunker und building=bunker (Hoch-/Tiefbunker, Luftschutz,
    Militär), rund 15.000.
  - Fernmelde- und Glasfaserleitungen: telecom=line und communication=line. Nur ein kleiner Teil des
    tatsächlichen Netzes ist erfasst - die Betreiber veröffentlichen ihre Trassen nicht.
  - Quellen und Wasserstellen: natural=spring, amenity=drinking_water/water_point, man_made=water_well/
    water_tap. Ob das Wasser trinkbar ist, steht nur dabei, wenn es eingetragen ist.
Ergebnis: data/geo/masts.geojson, bunkers.geojson, telecom.geojson und water.geojson. Monatlich erneuert; ohne Internet
bleibt der letzte Stand.
"""
from __future__ import annotations

import json
import time

import httpx

SERVERS = ("https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter",
           "https://maps.mail.ru/osm/tools/overpass/api/interpreter")
AREA = 'area["ISO3166-1"="DE"][admin_level=2]->.de;'
MOBILE_OPERATORS = ("telekom", "vodafone", "telefónica", "telefonica", "o2", "1&1", "vantage", "funkturm", "dfmg",
                    "e-plus", "american tower")
BUNKER_TYPES = {
    "personnel_shelter": "Schutzraum", "air_raid_shelter": "Luftschutzbunker", "pillbox": "Kampfstand",
    "gun_emplacement": "Geschützstellung", "hardened_aircraft_shelter": "Flugzeugschutzbau",
    "munitions": "Munitionsbunker", "technical": "Technikbunker", "mg_nest": "MG-Stand", "blockhouse": "Blockhaus",
    "command": "Befehlsbunker", "observation": "Beobachtungsbunker",
}


def _overpass(http, query: str, out: str = "tags center") -> list[dict]:
    """Abfrage mit Ausweichservern - die öffentlichen Server sind oft ausgelastet (429/504)."""
    error = ""
    for attempt, server in enumerate(SERVERS * 2):
        try:
            r = http.post(server, data={"data": f"[out:json][timeout:300];{AREA}{query}out {out};"},
                          timeout=330)
            if r.status_code == 200:
                data = r.json()
                if "error" not in data.get("remark", "").lower():
                    return data["elements"]
                error = data["remark"][:120]
            else:
                error = f"HTTP {r.status_code}"
        except (httpx.TransportError, ValueError) as e:
            error = type(e).__name__
        time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"Overpass nicht erreichbar ({error})")


def _point(el: dict) -> list[float] | None:
    center = el.get("center") or el
    return [round(center["lon"], 5), round(center["lat"], 5)] if "lat" in center else None


def _write(path, features: list[dict]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False,
                              separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def masts(elements: list[dict]) -> list[dict]:
    features = []
    for el in elements:
        tags, point = el.get("tags", {}), _point(el)
        if not point:
            continue
        operator = tags.get("operator") or ""
        mobile = tags.get("communication:mobile_phone") == "yes" or \
            any(name in operator.lower() for name in MOBILE_OPERATORS)
        bands = [label for key, label in (("communication:gsm", "2G"), ("communication:umts", "3G"),
                                          ("communication:lte", "4G"), ("communication:5g", "5G"))
                 if tags.get(key) == "yes"]
        props = {"mobile": mobile, "kind": "Turm" if tags.get("man_made") == "tower" else "Mast"}
        for key, value in (("name", tags.get("name")), ("operator", operator), ("height", tags.get("height")),
                           ("bands", " ".join(bands)), ("ref", tags.get("ref"))):
            if value:
                props[key] = value
        features.append({"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": point}})
    return features


def bunkers(elements: list[dict]) -> list[dict]:
    features = []
    for el in elements:
        tags, point = el.get("tags", {}), _point(el)
        if not point:
            continue
        kind = tags.get("bunker_type", "")
        props = {"type": BUNKER_TYPES.get(kind, "Bunker"),
                 "military": tags.get("military") == "bunker",
                 "lost": any(k.startswith(("abandoned", "disused", "ruins", "demolished")) for k in tags)
                 or tags.get("ruins") == "yes" or tags.get("historic") in ("ruins", "bunker")}
        for key, value in (("name", tags.get("name")), ("operator", tags.get("operator")),
                           ("access", tags.get("access")), ("built", tags.get("start_date")),
                           ("note", tags.get("description") or tags.get("description:de")),
                           ("level", "Tiefbunker" if tags.get("location") == "underground"
                            or tags.get("layer", "").startswith("-") else "")):
            if value:
                props[key] = value
        features.append({"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": point}})
    return features


MEDIUM = {"fibre": "Glasfaser", "copper": "Kupfer", "coaxial": "Koaxialkabel"}
LOCATION = {"underground": "unterirdisch", "overhead": "oberirdisch", "underwater": "unter Wasser",
            "overground": "oberirdisch"}


def telecom(elements: list[dict]) -> list[dict]:
    features = []
    for el in elements:
        tags, line = el.get("tags", {}), [[round(n["lon"], 5), round(n["lat"], 5)] for n in el.get("geometry", [])]
        if len(line) < 2:
            continue
        props = {"fibre": tags.get("telecom:medium") == "fibre"}
        for key, value in (("name", tags.get("name")), ("operator", tags.get("operator")), ("ref", tags.get("ref")),
                           ("medium", MEDIUM.get(tags.get("telecom:medium", ""), tags.get("telecom:medium"))),
                           ("location", LOCATION.get(tags.get("location", ""), tags.get("location")))):
            if value:
                props[key] = value
        features.append({"type": "Feature", "properties": props, "geometry": {"type": "LineString", "coordinates": line}})
    return features


WATER_TYPES = {"spring": "Quelle", "drinking_water": "Trinkwasserstelle", "water_point": "Wasserentnahmestelle",
               "water_well": "Brunnen", "water_tap": "Wasserhahn"}


def water(elements: list[dict]) -> list[dict]:
    features = []
    for el in elements:
        tags, point = el.get("tags", {}), _point(el)
        if not point:
            continue
        kind = next((k for k in (tags.get("natural"), tags.get("amenity"), tags.get("man_made")) if k in WATER_TYPES),
                    None)
        if not kind:
            continue
        drinkable = tags.get("drinking_water") or ("yes" if kind == "drinking_water" else "")
        props = {"kind": kind, "type": WATER_TYPES[kind],
                 "drinking": {"yes": True, "no": False}.get(drinkable.split(";")[0]) if drinkable else None}
        if props["drinking"] is None:
            del props["drinking"]
        seasonal = tags.get("seasonal") == "yes" or tags.get("intermittent") == "yes"
        for key, value in (("name", tags.get("name")), ("seasonal", seasonal or None),
                           ("pump", {"manual": "Handpumpe", "powered": "elektrische Pumpe", "no": "keine"}
                            .get(tags.get("pump", ""), None)),
                           ("access", tags.get("access")), ("operator", tags.get("operator")),
                           ("note", tags.get("description") or tags.get("description:de") or tags.get("note"))):
            if value:
                props[key] = value
        features.append({"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": point}})
    return features


def collect(ctx) -> int:
    """Bunker zuerst (kleiner), jede Datei sofort speichern - klappt nur eine Abfrage, ist die andere schon da.
    Dateien, die noch frisch sind (z. B. nach dem Neustart wegen einer neuen Ebene), werden nicht neu geholt."""
    geo = ctx.data_dir / "geo"
    geo.mkdir(parents=True, exist_ok=True)
    max_age = ctx.cfg.get("interval_min", 30 * 24 * 60) * 60 * 0.8
    count, errors = 0, []
    for name, label, parse, query, out in (
            ("bunkers", "Bunker", bunkers, '(nwr["military"="bunker"](area.de);nwr["building"="bunker"](area.de););',
             "tags center"),
            ("masts", "Masten", masts, '(nwr["tower:type"="communication"](area.de);'
                                       'nwr["communication:mobile_phone"="yes"](area.de););', "tags center"),
            ("telecom", "Leitungen", telecom, '(way["telecom"="line"](area.de);way["communication"="line"](area.de););',
             "tags geom"),
            ("water", "Wasserstellen", water, '(nwr["natural"="spring"](area.de);nwr["amenity"="drinking_water"](area.de);'
                                              'nwr["amenity"="water_point"](area.de);nwr["man_made"="water_well"](area.de);'
                                              'nwr["man_made"="water_tap"](area.de););', "tags center")):
        path = geo / f"{name}.geojson"
        if path.exists() and time.time() - path.stat().st_mtime < max_age:
            continue
        try:
            features = parse(_overpass(ctx.http, query, out))
        except RuntimeError as e:
            errors.append(f"{label}: {e}")
            continue
        _write(path, features)
        count += len(features)
    if errors:  # Status zeigt den Grund; der Planer versucht es stündlich erneut
        raise RuntimeError("; ".join(errors) + " – OpenStreetMap-Server ausgelastet, neuer Versuch stündlich")
    return count
