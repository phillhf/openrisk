"""Bibliotheken und Kartensymbole der Oberfläche (web/vendor).

Fehlen Dateien - etwa nach einem Update von einer älteren Version, die web/vendor noch nicht
mitgeliefert hat -, lädt fetch_missing() sie beim Start nach. Schriften lädt tools/fetch_assets.py.
"""
from __future__ import annotations

import logging
from pathlib import Path

import httpx

log = logging.getLogger("openrisk.assets")
VENDOR = Path(__file__).resolve().parent.parent / "web" / "vendor"
NPM = "https://unpkg.com/"
JS_FILES = {
    "maplibre-gl/maplibre-gl.mjs": "maplibre-gl@6.11.2/dist/maplibre-gl.mjs",
    "maplibre-gl/maplibre-gl-shared.mjs": "maplibre-gl@6.11.2/dist/maplibre-gl-shared.mjs",
    "maplibre-gl/maplibre-gl-worker.mjs": "maplibre-gl@6.11.2/dist/maplibre-gl-worker.mjs",
    "maplibre-gl/maplibre-gl.css": "maplibre-gl@6.11.2/dist/maplibre-gl.css",
    "pmtiles/index.js": "pmtiles@4.5.0/dist/esm/index.js",
    "fflate/browser.js": "fflate@0.8.2/esm/browser.js",
    "basemaps/index.js": "@protomaps/basemaps@5.7.2/dist/esm/index.js",
    "maplibre-contour/index.mjs": "maplibre-contour@0.1.1/dist/index.mjs",  # Höhenlinien aus Höhendaten
}
ASSETS = "https://protomaps.github.io/basemaps-assets/"
SPRITES = [f"{flavor}{size}.{ext}" for flavor in ("dark", "light") for size in ("", "@2x") for ext in ("json", "png")]


def files() -> dict[str, str]:
    """Pfad in web/vendor -> Download-Adresse."""
    return {**{dest: NPM + src for dest, src in JS_FILES.items()},
            **{f"sprites/{name}": f"{ASSETS}sprites/v4/{name}" for name in SPRITES}}


def download(client: httpx.Client, url: str, dest: Path, optional: bool = False) -> bool:
    if dest.exists() and dest.stat().st_size > 0:
        return True
    r = client.get(url)
    if r.status_code == 404 and optional:
        return False
    r.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(r.content)
    tmp.replace(dest)
    return True


def fetch_missing(vendor: Path = VENDOR) -> list[str]:
    """Fehlende Bibliotheken laden; ohne Internet bleibt es beim nächsten Start beim Versuch."""
    missing = {rel: url for rel, url in files().items() if not (vendor / rel).exists()}
    if not missing:
        return []
    try:
        with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "OpenRisk"}) as client:
            for rel, url in missing.items():
                download(client, url, vendor / rel)
        log.info("Bibliotheken nachgeladen: %s", ", ".join(missing))
    except httpx.HTTPError as e:
        log.warning("Bibliotheken konnten nicht nachgeladen werden (%s)", type(e).__name__)
    return list(missing)
