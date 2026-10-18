"""Einstellungen, die in der Oberfläche geändert werden (data/settings.json).

Getrennt von config.toml: dort steht, was vor dem Start feststehen muss
(Port, Quellen); hier, was im Betrieb umgeschaltet wird - auch die API-Schlüssel.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULTS = {
    "history_days": 365,  # so lange bleibt der Verlauf gespeichert; 0 = unbegrenzt
    "track_days": 30,  # Flug- und Schiffsspuren (viel Platz: grob 10 MB je Tag); 0 = unbegrenzt
    "api_keys": {},  # Name -> Schlüssel, siehe API_KEYS
    "update_source": "",  # Ordner oder Web-Adresse mit latest.json (siehe updater.py); leer = keine
}
MAP_ZOOM_MAX = 15  # höchste Stufe der Quelle (ganze Welt 138 GB); einzelne Länder sind viel kleiner
HISTORY_CHOICES = (30, 90, 180, 365, 730, 0)
TRACK_CHOICES = (2, 30, 90, 180, 365, 0)

# Dienste, die einen Schlüssel brauchen. Ein neuer Eintrag hier erscheint automatisch
# als Feld unter Einstellungen; `source` ist die Datenquelle, deren Status dort angezeigt wird.
API_KEYS = {
    "aisstream": {
        "label": "Militärschiffe (AIS)", "provider": "aisstream.io", "url": "https://aisstream.io",
        "hint": "Kostenloser Schlüssel nach Anmeldung bei aisstream.io.", "source": "ships",
    },
}


def load(data_dir: Path) -> dict:
    try:
        stored = json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    return {key: type(default)(stored.get(key, default)) for key, default in DEFAULTS.items()}


def forget_unknown(data_dir: Path) -> None:
    """Einträge entfernen, die es nicht mehr gibt (z. B. gespeicherte Zugangstoken entfernter Dienste)."""
    try:
        stored = json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if set(stored) - set(DEFAULTS):
        _write(data_dir, load(data_dir))


def save(data_dir: Path, changes: dict) -> dict:
    current = load(data_dir)
    if changes.get("history_days") in HISTORY_CHOICES:
        current["history_days"] = changes["history_days"]
    if changes.get("track_days") in TRACK_CHOICES:
        current["track_days"] = changes["track_days"]
    for name, value in (changes.get("api_keys") or {}).items():
        if name not in API_KEYS:
            continue
        if str(value or "").strip():
            current["api_keys"][name] = str(value).strip()
        else:  # leer = Schlüssel entfernen
            current["api_keys"].pop(name, None)
    _write(data_dir, current)
    return current


def _write(data_dir: Path, current: dict) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "settings.json"
    path.write_text(json.dumps(current, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)  # enthält Schlüssel: nur für den eigenen Benutzer lesbar (wirkt unter Linux)
    except OSError:
        pass


def save_update_source(data_dir: Path, source: str) -> None:
    """Nur über die gesicherte Update-Schnittstelle änderbar: wer die Quelle bestimmt, bestimmt das Programm."""
    current = load(data_dir)
    current["update_source"] = str(source or "").strip()
    _write(data_dir, current)


def api_key(data_dir: Path, name: str, fallback: str = "") -> str:
    return load(data_dir)["api_keys"].get(name) or fallback


def public(data_dir: Path, fallbacks: dict | None = None) -> dict:
    """Einstellungen für die Oberfläche - Schlüssel und Token selbst verlassen den Server nicht."""
    current = load(data_dir)
    keys = []
    for name, meta in API_KEYS.items():
        value = current["api_keys"].get(name) or (fallbacks or {}).get(name) or ""
        keys.append({"name": name, **meta, "set": bool(value), "tail": value[-4:] if len(value) >= 12 else ""})
    return {"history_days": current["history_days"],
            "track_days": current["track_days"], "api_keys": keys}


def map_zoom_limit(data_dir: Path) -> int:
    """Höchste Zoomstufe, die heruntergeladen werden darf - frei wählbar, keine Sperre mehr."""
    return MAP_ZOOM_MAX
