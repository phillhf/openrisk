"""Gemeinsamer Abruf weltweiter OpenStreetMap-Daten über die Overpass-API.

Die Welt wird in Längengrad-Streifen abgefragt, damit der öffentliche Server die
Anfragen verkraftet. Jeder Streifen wird zwischengespeichert: bricht ein Lauf ab,
macht der nächste Aufruf dort weiter.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Iterator

import httpx

SERVER = "https://overpass-api.de/api/interpreter"
STRIP = 20  # Breite eines Streifens in Grad
MIN_STRIP = 2.5  # schlägt ein Streifen fehl, wird er bis zu dieser Breite halbiert


def _query(client: httpx.Client, query: str, w: float, e: float) -> list[dict] | None:
    """Elemente eines Streifens; None, wenn der Server ihn nicht liefert."""
    header = f"[out:json][timeout:170][bbox:-90,{w},90,{e}];\n"
    for attempt in range(3):
        try:
            r = client.post(SERVER, data={"data": header + query})
            if r.status_code == 200:
                data = r.json()
                if "error" not in data.get("remark", "").lower():
                    return data["elements"]
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(20 * (attempt + 1))  # 429/504: Server ist ausgelastet
    return None


def _strip(client, query: str, cache: Path, w: float, e: float, missing: list[str]) -> list[dict]:
    cached = cache / f"{w:g}_{e:g}.json"
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))
    elements = _query(client, query, w, e)
    if elements is None:
        if e - w <= MIN_STRIP:
            print(f"  {w:g}..{e:g}: nicht abrufbar – später erneut starten", flush=True)
            missing.append(f"{w:g}..{e:g}")
            return []
        mid = (w + e) / 2
        return _strip(client, query, cache, w, mid, missing) + _strip(client, query, cache, mid, e, missing)
    cached.write_text(json.dumps(elements), encoding="utf-8")
    print(f"  {w:>6g}..{e:<6g} {len(elements):>6} Elemente", flush=True)
    time.sleep(2)
    return elements


def world(query: str, cache: Path) -> Iterator[dict]:
    """Alle Elemente der Abfrage weltweit, jedes nur einmal. `query` ohne Kopfzeile ([out:json]…)."""
    cache.mkdir(parents=True, exist_ok=True)
    seen: set[tuple[str, int]] = set()
    missing: list[str] = []
    with httpx.Client(timeout=200, headers={"User-Agent": "OpenRisk/0.1"}) as client:
        for w in range(-180, 180, STRIP):
            for el in _strip(client, query, cache, w, w + STRIP, missing):
                key = (el.get("type"), el.get("id"))
                if key not in seen:
                    seen.add(key)
                    yield el
    if not missing:
        shutil.rmtree(cache)
