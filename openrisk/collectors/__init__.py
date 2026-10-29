"""Sammler: holen Daten aus dem Netz und schreiben sie in die Datenbank.

Jeder Sammler ist eine Funktion collect(ctx) -> Anzahl Datensätze. Schlägt der
Abruf fehl (kein Internet), bleibt einfach der letzte Stand in der Datenbank.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..geo import Geo


@dataclass
class Context:
    http: httpx.Client
    db: sqlite3.Connection
    cfg: dict  # Abschnitt [sources.<name>] der Konfiguration
    geo: Geo
    data_dir: Path


class NotReady(Exception):
    """Quelle kann noch nicht abgerufen werden (z. B. keine Anmeldung); der Text steht im Quellenstatus."""


from . import (aircraft, cityinfo, countries, dwd_stations, dwd_warnings, eonet, fires, frontline, gdacs, gdelt, internet, news, nina, nuclear, osm_de, outages, places,  # noqa: E402
               radiation, radon, usgs, weather, weathermaps, wind)

PERIODIC = {
    "countries": countries.collect,  # zuerst: klein und schnell, danach sind alle Länderinfos offline da
    "cityinfo": cityinfo.collect,  # Gemeinde- und Stadtinfos
    "news": news.collect,
    "usgs": usgs.collect,
    "gdacs": gdacs.collect,
    "eonet": eonet.collect,
    "weather": weather.collect,
    "weathermaps": weathermaps.collect,  # Regenradar mit Vorhersage, Wolken vom Satelliten
    "wind": wind.collect,  # Windfeld für die Windströmung
    "dwd_warnings": dwd_warnings.collect,
    "nina": nina.collect,
    "dwd_stations": dwd_stations.collect,
    "radiation": radiation.collect,
    "eurdep": radiation.collect_eurdep,
    "radon": radon.collect,
    "osm_de": osm_de.collect,
    "internet": internet.collect,
    "nuclear": nuclear.collect,  # Kernkraftwerke weltweit
    "fires": fires.collect,  # Brandherde vom Satelliten (NASA FIRMS)
    "outages": outages.collect,  # Internet-Ausfälle (IODA)
    "frontline": frontline.collect,  # Frontverlauf Ukraine (DeepStateMap)  # Seekabel, Internetknoten, Rechenzentren
    "places": places.collect,
    "aircraft": aircraft.collect,
    "gdelt": gdelt.collect,
}
