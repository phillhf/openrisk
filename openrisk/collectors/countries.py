"""Länderdaten für das Länder-Popup: Grunddaten (GeoNames) und aktuelle Kennzahlen (Weltbank).

  - GeoNames countryInfo: Hauptstadt, Fläche, Kontinent, Währung, Sprachen, Vorwahl, Nachbarländer
  - Weltbank (frei, ohne Schlüssel): Einwohner, Wirtschaftsleistung, BIP je Einwohner, Lebenserwartung -
    jeweils der jüngste veröffentlichte Wert samt Jahr
Ergebnis: data/geo/country_info.json, nach ISO3 - alle Länder in einem Abruf (~2 s, unter 100 KB).
Läuft beim ersten Start sofort und danach alle 14 Tage; ohne Internet bleibt der letzte Stand.
"""
from __future__ import annotations

import json

BASE = "https://download.geonames.org/export/dump/countryInfo.txt"
WORLD_BANK = "https://api.worldbank.org/v2/country/all/indicator/{}?format=json&mrnev=1&per_page=500"
INDICATORS = {
    "population": "SP.POP.TOTL",
    "gdp": "NY.GDP.MKTP.CD",  # US-Dollar
    "gdp_capita": "NY.GDP.PCAP.CD",
    "life_expectancy": "SP.DYN.LE00.IN",
}
CONTINENTS = {"AF": "Afrika", "AS": "Asien", "EU": "Europa", "NA": "Nordamerika", "OC": "Ozeanien",
              "SA": "Südamerika", "AN": "Antarktis"}


def _capital(geo, iso3: str, name: str) -> str:
    """Hauptstadt mit deutschem Namen, falls Natural Earth sie kennt (Moskau statt Moscow)."""
    for p in geo.cities:
        if p["iso"] == iso3 and p.get("capital") and name in p["names"]:
            return p["name"]
    return name


def collect(ctx) -> int:
    r = ctx.http.get(BASE, timeout=60)
    r.raise_for_status()
    rows = [line.split("\t") for line in r.text.splitlines() if line and not line.startswith("#")]
    info: dict[str, dict] = {}
    for cols in rows:
        if len(cols) < 18:
            continue
        code = cols[1]
        info[code] = {
            "capital": _capital(ctx.geo, code, cols[5]) if cols[5] else "",
            "area_km2": float(cols[6]) if cols[6] else None,
            "continent": CONTINENTS.get(cols[8], cols[8]),
            "tld": cols[9], "currency": cols[10], "phone": f"+{cols[12]}" if cols[12] else "",
            "languages": [lang for lang in cols[15].split(",") if lang][:4],
            "iso2": cols[0], "neighbours": [n for n in cols[17].split(",") if n],  # Namen setzt die Oberfläche ein
            "population": int(cols[7]) if cols[7] else None, "population_year": None,
        }
    for key, indicator in INDICATORS.items():
        r = ctx.http.get(WORLD_BANK.format(indicator), timeout=90)
        r.raise_for_status()
        for item in (r.json()[1] or []):
            code, value = item.get("countryiso3code"), item.get("value")
            if code in info and value is not None:
                info[code][key] = value
                info[code][f"{key}_year"] = int(item["date"])
    if "XKX" in info:
        info["KOS"] = info["XKX"]  # Kosovo heißt in Natural Earth KOS
    path = ctx.data_dir / "geo" / "country_info.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(info, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    return len(info)
