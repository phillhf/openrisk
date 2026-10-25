"""Konfiguration: eingebaute Standardwerte, überschrieben durch config.toml."""
from __future__ import annotations

import copy
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict = {
    "server": {"host": "127.0.0.1", "port": 8642},
    "storage": {
        "data_dir": "data",
    },
    "collect": {"enabled": True},
    "sources": {
        "news": {
            "enabled": True,
            "interval_min": 15,
            "feeds": [
                {"name": "Tagesschau", "lang": "de", "url": "https://www.tagesschau.de/ausland/index~rss2.xml"},
                {"name": "Spiegel Ausland", "lang": "de", "url": "https://www.spiegel.de/ausland/index.rss"},
                {"name": "DW", "lang": "de", "url": "https://rss.dw.com/rdf/rss-de-all"},
                {"name": "BBC World", "lang": "en", "url": "https://feeds.bbci.co.uk/news/world/rss.xml"},
                {"name": "Al Jazeera", "lang": "en", "url": "https://www.aljazeera.com/xml/rss/all.xml"},
                {"name": "The Guardian", "lang": "en", "url": "https://www.theguardian.com/world/rss"},
                {"name": "France 24", "lang": "en", "url": "https://www.france24.com/en/rss"},
                {"name": "UN News", "lang": "en", "url": "https://news.un.org/feed/subscribe/en/news/all/rss.xml"},
            ],
        },
        "usgs": {
            "enabled": True,
            "interval_min": 10,
            # ab Magnitude 4.5; kleinere Beben meldet USGS fast nur für die USA
            "url": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson",
        },
        "gdacs": {"enabled": True, "interval_min": 30},
        "eonet": {"enabled": True, "interval_min": 60},
        "weather": {"enabled": True, "interval_min": 60, "cities": 150},
        "weathermaps": {"enabled": True, "interval_min": 10},  # Radar (DWD) und Satellit (EUMETSAT), ~120 MB/Tag
        "wind": {"enabled": True, "interval_min": 180},  # Windfeld Mitteleuropa (Open-Meteo)
        "dwd_warnings": {"enabled": True, "interval_min": 10},
        "nina": {"enabled": True, "interval_min": 10},
        "dwd_stations": {"enabled": True, "interval_min": 10},
        "radiation": {"enabled": True, "interval_min": 30},  # BfS-Messnetz, Stundenwerte
        "eurdep": {"enabled": True, "interval_min": 60},  # europäische Strahlungsmessstationen
        "radon": {"enabled": True, "interval_min": 7 * 24 * 60},  # Radonkarten des BfS, ändern sich selten
        "places": {"enabled": True, "interval_min": 90 * 24 * 60},  # Ortsverzeichnis (GeoNames) für die Ortssuche
        "countries": {"enabled": True, "interval_min": 14 * 24 * 60},  # Länderdaten (GeoNames, Weltbank)
        "cityinfo": {"enabled": True, "interval_min": 14 * 24 * 60},  # Gemeinden (Destatis), Städte (Wikidata)
        "osm_de": {"enabled": True, "interval_min": 30 * 24 * 60},  # Masten, Bunker, Leitungen, Wasser (OpenStreetMap)
        "internet": {"enabled": True, "interval_min": 30 * 24 * 60},
        "nuclear": {"enabled": True, "interval_min": 30 * 24 * 60},  # Kernkraftwerke (GeoNuclearData)  # Seekabel, Internetknoten, Rechenzentren
        # Konflikte weltweit aus GDELT (frei, ohne Konto), alle 15 Minuten; gezeigt werden die letzten `days` Tage
        "gdelt": {"enabled": True, "interval_min": 15, "days": 7},
        "aircraft": {
            "enabled": True,
            "interval_min": 2,
            "urls": ["https://api.adsb.lol/v2/mil", "https://opendata.adsb.fi/api/v2/mil"],
        },
        # Schiffe: kostenloser API-Schlüssel von https://aisstream.io nötig - Eingabe in der Oberfläche
        # unter Einstellungen; "api_key" hier gilt nur, solange dort keiner eingetragen ist
        "ships": {"enabled": True, "api_key": "", "ship_types": [35]},
    },
}


def _merge(base: dict, override: dict) -> dict:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load(path: str | Path | None = None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    path = Path(path) if path else ROOT / "config.toml"
    if path.exists():
        with open(path, "rb") as f:
            _merge(cfg, tomllib.load(f))
    data_dir = Path(cfg["storage"]["data_dir"])
    cfg["storage"]["data_dir"] = data_dir if data_dir.is_absolute() else ROOT / data_dir
    return cfg
