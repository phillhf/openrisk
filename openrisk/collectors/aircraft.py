"""Militärflugzeuge mit ADS-B/Mode-S-Sender (adsb.lol, Ausweichquelle adsb.fi).

Es sind nur Maschinen sichtbar, die ihren Transponder eingeschaltet haben und
von Freiwilligen-Empfängern erfasst werden - kein vollständiges Lagebild.
"""
from __future__ import annotations

import math
import time

MAX_POSITION_AGE = 600  # Sekunden
TRACK_MIN_KM = 2.0

# ICAO-24-Bit-Adressblöcke der Staaten (Auswahl); die Adresse verrät den Registrierstaat
ICAO_BLOCKS = [
    (0x008000, 0x00FFFF, "Südafrika"), (0x010000, 0x017FFF, "Ägypten"), (0x0D0000, 0x0D7FFF, "Mexiko"),
    (0x100000, 0x1FFFFF, "Russland"), (0x300000, 0x33FFFF, "Italien"), (0x340000, 0x37FFFF, "Spanien"),
    (0x380000, 0x3BFFFF, "Frankreich"), (0x3C0000, 0x3FFFFF, "Deutschland"),
    (0x400000, 0x43FFFF, "Großbritannien"), (0x440000, 0x447FFF, "Österreich"),
    (0x448000, 0x44FFFF, "Belgien"), (0x450000, 0x457FFF, "Bulgarien"), (0x458000, 0x45FFFF, "Dänemark"),
    (0x460000, 0x467FFF, "Finnland"), (0x468000, 0x46FFFF, "Griechenland"), (0x470000, 0x477FFF, "Ungarn"),
    (0x478000, 0x47FFFF, "Norwegen"), (0x480000, 0x487FFF, "Niederlande"), (0x488000, 0x48FFFF, "Polen"),
    (0x490000, 0x497FFF, "Portugal"), (0x498000, 0x49FFFF, "Tschechien"), (0x4A0000, 0x4A7FFF, "Rumänien"),
    (0x4A8000, 0x4AFFFF, "Schweden"), (0x4B0000, 0x4B7FFF, "Schweiz"), (0x4B8000, 0x4BFFFF, "Türkei"),
    (0x4C0000, 0x4C7FFF, "Serbien"), (0x4CA000, 0x4CAFFF, "Irland"), (0x508000, 0x50FFFF, "Ukraine"),
    (0x710000, 0x717FFF, "Saudi-Arabien"), (0x718000, 0x71FFFF, "Südkorea"), (0x730000, 0x737FFF, "Iran"),
    (0x738000, 0x73FFFF, "Israel"), (0x750000, 0x757FFF, "Malaysia"), (0x760000, 0x767FFF, "Pakistan"),
    (0x768000, 0x76FFFF, "Singapur"), (0x780000, 0x7BFFFF, "China"), (0x7C0000, 0x7FFFFF, "Australien"),
    (0x800000, 0x83FFFF, "Indien"), (0x840000, 0x87FFFF, "Japan"), (0x880000, 0x887FFF, "Thailand"),
    (0x896000, 0x896FFF, "VAE"), (0x8A0000, 0x8A7FFF, "Indonesien"), (0xA00000, 0xAFFFFF, "USA"),
    (0xC00000, 0xC3FFFF, "Kanada"), (0xC80000, 0xC87FFF, "Neuseeland"), (0xE00000, 0xE3FFFF, "Argentinien"),
    (0xE40000, 0xE7FFFF, "Brasilien"),
]


def country_of(hex_code: str) -> str | None:
    try:
        addr = int(hex_code, 16)
    except ValueError:
        return None
    return next((name for lo, hi, name in ICAO_BLOCKS if lo <= addr <= hi), None)


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dx = (lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, lat2 - lat1) * 111.2


def store_positions(con, kind: str, vehicles: list[dict]) -> None:
    """Fahrzeuge aktualisieren; Spurpunkt nur anlegen, wenn sich das Fahrzeug bewegt hat."""
    known = {row["id"]: row for row in con.execute(
        "SELECT id, seen, tp_ts, tp_lat, tp_lon FROM vehicles WHERE kind = ?", (kind,))}
    for v in vehicles:
        old = known.get(v["id"])
        if old and old["seen"] >= v["seen"]:
            continue
        tp = (old["tp_ts"], old["tp_lat"], old["tp_lon"]) if old else (None, None, None)
        if tp[0] is None or distance_km(tp[1], tp[2], v["lat"], v["lon"]) >= TRACK_MIN_KM:
            tp = (v["seen"], v["lat"], v["lon"])
            con.execute(
                "INSERT OR REPLACE INTO track_points (vehicle_id, ts, lat, lon, alt, speed, heading) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (v["id"], v["seen"], v["lat"], v["lon"], v.get("alt"), v.get("speed"), v.get("heading")))
        con.execute(  # Rufzeichen-Verlauf für die Suche
            "INSERT INTO vehicle_seen (vehicle_id, callsign, kind, reg, type, country, first_ts, last_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(vehicle_id, callsign) DO UPDATE SET "
            "last_ts = MAX(last_ts, excluded.last_ts), reg = COALESCE(excluded.reg, reg), "
            "type = COALESCE(excluded.type, type), country = COALESCE(excluded.country, country)",
            (v["id"], v.get("callsign") or "", kind, v.get("reg"), v.get("type"), v.get("country"), v["seen"], v["seen"]))
        con.execute(
            "INSERT OR REPLACE INTO vehicles (id, kind, callsign, reg, type, country, lat, lon, alt, speed, heading, "
            "squawk, info, seen, tp_ts, tp_lat, tp_lon) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (v["id"], kind, v.get("callsign"), v.get("reg"), v.get("type"), v.get("country"), v["lat"], v["lon"],
             v.get("alt"), v.get("speed"), v.get("heading"), v.get("squawk"), v.get("info"), v["seen"], *tp))


def collect(ctx) -> int:
    data, last_error = None, None
    for url in ctx.cfg["urls"]:
        try:
            r = ctx.http.get(url)
            r.raise_for_status()
            data = r.json()
            break
        except Exception as e:
            last_error = e
    if data is None:
        raise last_error or IOError("keine Quelle konfiguriert")

    now = time.time()
    vehicles = []
    for ac in data.get("ac") or []:
        age = ac.get("seen_pos")
        if ac.get("lat") is None or ac.get("lon") is None or age is None or age > MAX_POSITION_AGE:
            continue
        on_ground = ac.get("alt_baro") == "ground"
        alt = 0 if on_ground else ac.get("alt_baro") if ac.get("alt_baro") is not None else ac.get("alt_geom")
        vehicles.append({
            "id": f"air:{ac['hex']}", "callsign": (ac.get("flight") or "").strip() or None,
            "reg": ac.get("r"), "type": ac.get("t"), "country": country_of(ac["hex"]),
            "lat": ac["lat"], "lon": ac["lon"], "alt": alt, "speed": ac.get("gs"),
            "heading": ac.get("track", ac.get("true_heading")), "squawk": ac.get("squawk"),
            "info": "am Boden" if on_ground else None, "seen": int(now - age),
        })
    store_positions(ctx.db, "aircraft", vehicles)
    return len(vehicles)
