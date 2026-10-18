"""Militärschiffe über AIS (aisstream.io, kostenloser API-Schlüssel nötig; Eingabe unter Einstellungen).

Dauerhafte WebSocket-Verbindung statt periodischem Abruf. Abonniert werden nur
die Stammdaten-Meldungen (enthalten Schiffstyp und letzte Position); behalten
werden Schiffe mit AIS-Typ 35 "Military ops". Kriegsschiffe schalten AIS im
Einsatz oft ab oder senden als anderer Typ - die Abdeckung ist lückenhaft.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from .. import db
from .aircraft import store_positions

log = logging.getLogger("openrisk.ships")
URL = "wss://stream.aisstream.io/v0/stream"
FLUSH_SECONDS = 30

# Maritime Identification Digits (erste drei Ziffern der MMSI) -> Flaggenstaat (Auswahl)
MID = {
    "201": "Albanien", "205": "Belgien", "207": "Bulgarien", "209": "Zypern", "210": "Zypern",
    "211": "Deutschland", "218": "Deutschland", "219": "Dänemark", "220": "Dänemark", "224": "Spanien",
    "225": "Spanien", "226": "Frankreich", "227": "Frankreich", "228": "Frankreich", "230": "Finnland",
    "232": "Großbritannien", "233": "Großbritannien", "234": "Großbritannien", "235": "Großbritannien",
    "237": "Griechenland", "239": "Griechenland", "240": "Griechenland", "241": "Griechenland",
    "244": "Niederlande", "245": "Niederlande", "246": "Niederlande", "247": "Italien", "250": "Irland",
    "257": "Norwegen", "258": "Norwegen", "259": "Norwegen", "261": "Polen", "263": "Portugal",
    "264": "Rumänien", "265": "Schweden", "266": "Schweden", "271": "Türkei", "272": "Ukraine",
    "273": "Russland", "275": "Lettland", "276": "Estland", "277": "Litauen", "316": "Kanada",
    "338": "USA", "366": "USA", "367": "USA", "368": "USA", "369": "USA", "403": "Saudi-Arabien",
    "412": "China", "413": "China", "414": "China", "416": "Taiwan", "419": "Indien", "422": "Iran",
    "428": "Israel", "431": "Japan", "432": "Japan", "440": "Südkorea", "441": "Südkorea",
    "463": "Pakistan", "470": "VAE", "503": "Australien", "512": "Neuseeland", "525": "Indonesien",
    "533": "Malaysia", "548": "Philippinen", "563": "Singapur", "564": "Singapur", "565": "Singapur",
    "566": "Singapur", "567": "Thailand", "574": "Vietnam", "601": "Südafrika", "622": "Ägypten",
    "701": "Argentinien", "710": "Brasilien", "725": "Chile",
}


def parse(message: dict, ship_types: set[int]) -> dict | None:
    static = (message.get("Message") or {}).get("ShipStaticData") or {}
    meta = message.get("MetaData") or {}
    lat, lon = meta.get("latitude"), meta.get("longitude")
    if static.get("Type") not in ship_types or lat is None or lon is None:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None
    mmsi = str(meta.get("MMSI") or static.get("UserID"))
    dim = static.get("Dimension") or {}
    length = (dim.get("A") or 0) + (dim.get("B") or 0)
    info = " · ".join(x for x in (
        f"{length} m" if length else "",
        f"Ziel: {static['Destination'].strip()}" if (static.get("Destination") or "").strip(" @") else "") if x)
    return {
        "id": f"ship:{mmsi}", "callsign": (meta.get("ShipName") or static.get("Name") or "").strip() or None,
        "reg": mmsi, "type": f"AIS-Typ {static['Type']}", "country": MID.get(mmsi[:3]),
        "lat": lat, "lon": lon, "info": info or None, "seen": int(time.time()),
    }


def _flush(db_path: Path, vehicles: list[dict]) -> None:
    con = db.connect(db_path)
    try:
        store_positions(con, "ship", vehicles)
        count = con.execute("SELECT COUNT(*) FROM vehicles WHERE kind = 'ship'").fetchone()[0]
        db.set_status(con, "ships", True, "verbunden", count)
    finally:
        con.close()


def _status(db_path: Path, ok: bool, message: str) -> None:
    con = db.connect(db_path)
    try:
        db.set_status(con, "ships", ok, message, None)
    finally:
        con.close()


async def run(cfg: dict, db_path: Path, api_key: str) -> None:
    if not api_key:
        _status(db_path, False, "kein API-Schlüssel – unter Einstellungen eintragen")
        return
    import websockets

    ship_types = set(cfg["ship_types"])
    subscription = json.dumps({
        "APIKey": api_key, "BoundingBoxes": [[[-90, -180], [90, 180]]],
        "FilterMessageTypes": ["ShipStaticData"]})
    delay = 5
    while True:
        received = 0
        try:
            async with websockets.connect(URL, open_timeout=30) as ws:
                await ws.send(subscription)
                pending: dict[str, dict] = {}
                last_flush = time.monotonic()
                async for raw in ws:
                    message = json.loads(raw)
                    if "error" in message:
                        raise IOError(str(message["error"]))
                    received += 1
                    if received == 1:  # erste Meldung: Schlüssel wurde angenommen
                        await asyncio.to_thread(_status, db_path, True, "verbunden")
                    vessel = parse(message, ship_types)
                    if vessel:
                        pending[vessel["id"]] = vessel
                    if time.monotonic() - last_flush >= FLUSH_SECONDS:
                        await asyncio.to_thread(_flush, db_path, list(pending.values()))
                        pending.clear()
                        last_flush = time.monotonic()
                        delay = 5
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("AIS-Verbindung unterbrochen: %s", e)
            # aisstream trennt bei unbekanntem Schlüssel kommentarlos, bevor Daten fließen
            reason = "Verbindung abgelehnt – ist der API-Schlüssel gültig?" if not received else f"{type(e).__name__}: {e}"
            await asyncio.to_thread(_status, db_path, False, reason[:200])
        await asyncio.sleep(delay)
        delay = min(delay * 2, 300)
