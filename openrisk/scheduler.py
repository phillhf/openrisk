"""Ruft die Sammler regelmäßig im Hintergrund auf."""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import httpx

from . import collectors, db, settings
from .collectors import cityinfo, internet, places, ships
from .geo import Geo

log = logging.getLogger("openrisk.scheduler")
# Seltene, große Abrufe: Ist ihr Ergebnis vorhanden und aktuell? Dann beim Neustart nicht wiederholen.
READY = {
    "radon": lambda data_dir: (data_dir / "geo" / "radon.geojson").exists(),
    "places": places.ready,
    "countries": lambda data_dir: (data_dir / "geo" / "country_info.json").exists(),
    "cityinfo": cityinfo.ready,
    "osm_de": lambda data_dir: all((data_dir / "geo" / f"{n}.geojson").exists() for n in ("bunkers", "masts", "telecom", "water")),
    "internet": internet.ready,
}
AFTER = {"places": ["cityinfo"]}  # nach erfolgreichem Abruf gleich anstoßen (Stadtinfos brauchen das Ortsverzeichnis)
USER_AGENT = "OpenRisk/0.1 (privater Risikomonitor)"


def run_once(name: str, cfg: dict, geo: Geo, db_path: Path) -> tuple[bool, str]:
    """Einen Sammler synchron ausführen und das Ergebnis im Quellenstatus vermerken."""
    con = db.connect(db_path)
    try:
        with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as http:
            ctx = collectors.Context(http=http, db=con, cfg=cfg["sources"][name], geo=geo,
                                     data_dir=db_path.parent)
            count = collectors.PERIODIC[name](ctx)
        con.commit()
        db.set_status(con, name, True, "ok", count)
        return True, f"{count} Datensätze"
    except Exception as e:
        con.rollback()
        offline = isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout))
        message = ("offline – keine Verbindung" if offline else str(e) if isinstance(e, collectors.NotReady)
                   else f"{type(e).__name__}: {e}")[:300]
        db.set_status(con, name, False, message, None)
        return False, message
    finally:
        con.close()


class Scheduler:
    def __init__(self, cfg: dict, geo: Geo, db_path: Path):
        self.cfg, self.geo, self.db_path = cfg, geo, db_path
        self.tasks: list[asyncio.Task] = []
        self.wake: dict[str, asyncio.Event] = {}
        self.loop: asyncio.AbstractEventLoop | None = None
        self.ships_task: asyncio.Task | None = None

    def enabled(self) -> list[str]:
        return [n for n in collectors.PERIODIC if self.cfg["sources"][n].get("enabled", True)]

    async def _loop(self, name: str, start_delay: float) -> None:
        interval = self.cfg["sources"][name]["interval_min"] * 60
        # seltene, große Abrufe (Radon, Ortsverzeichnis) nicht bei jedem Neustart wiederholen - außer das Ergebnis fehlt
        if interval >= 86400 and (name not in READY or READY[name](self.db_path.parent)):
            start_delay = max(start_delay, self._last_ok(name) + interval - time.time())
        try:  # „Aktualisieren“ oder ein vorausgehender Abruf beendet das Warten vorzeitig
            await asyncio.wait_for(self.wake[name].wait(), timeout=max(0, start_delay))
        except asyncio.TimeoutError:
            pass
        while True:
            ok, message = await asyncio.to_thread(run_once, name, self.cfg, self.geo, self.db_path)
            log.log(logging.INFO if ok else logging.WARNING, "%s: %s", name, message)
            for follower in AFTER.get(name, []) if ok else []:
                self.trigger(follower)
            self.wake[name].clear()
            try:
                # fehlgeschlagen (z. B. offline): seltene Abrufe nicht erst nach Tagen, sondern stündlich wiederholen
                await asyncio.wait_for(self.wake[name].wait(), timeout=interval if ok else min(interval, 3600))
            except asyncio.TimeoutError:
                pass

    def _last_ok(self, name: str) -> float:
        con = db.connect(self.db_path)
        try:
            row = con.execute("SELECT last_ok FROM sources WHERE name = ?", (name,)).fetchone()
            return row[0] or 0 if row else 0
        finally:
            con.close()

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.to_thread(self._cleanup)
            await asyncio.sleep(3600)

    def _cleanup(self) -> None:
        con = db.connect(self.db_path)
        try:
            current = settings.load(self.db_path.parent)
            db.cleanup(con, current["history_days"], current["track_days"])
        finally:
            con.close()

    def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        for i, name in enumerate(self.enabled()):
            self.wake[name] = asyncio.Event()
            self.tasks.append(asyncio.create_task(self._loop(name, start_delay=i * 2)))
        self._start_ships()
        self.tasks.append(asyncio.create_task(self._cleanup_loop()))

    def fallback_keys(self) -> dict:
        """Schlüssel aus config.toml gelten, solange in den Einstellungen keiner steht."""
        return {"aisstream": self.cfg["sources"]["ships"].get("api_key", "")}

    def _start_ships(self) -> None:
        if self.ships_task:
            self.ships_task.cancel()
            self.tasks.remove(self.ships_task)
            self.ships_task = None
        cfg = self.cfg["sources"]["ships"]
        if cfg.get("enabled", True):
            key = settings.api_key(self.db_path.parent, "aisstream", self.fallback_keys()["aisstream"])
            self.ships_task = asyncio.create_task(ships.run(cfg, self.db_path, key))
            self.tasks.append(self.ships_task)

    def keys_changed(self) -> None:
        """Nach einer Änderung der API-Schlüssel (aus einem beliebigen Thread): Dauerverbindungen neu aufbauen."""
        if self.loop:
            self.loop.call_soon_threadsafe(self._start_ships)

    def trigger(self, name: str | None = None) -> list[str]:
        names = [n for n in ([name] if name else list(self.wake)) if n in self.wake]
        for n in names:  # wird aus Arbeits-Threads des Servers aufgerufen
            self.loop.call_soon_threadsafe(self.wake[n].set)
        return names

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
