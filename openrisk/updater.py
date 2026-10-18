"""Software-Updates einspielen.

Ein Update-Paket (ZIP, gebaut mit tools/make_release.py) enthält nur Programmdateien: openrisk/, web/
(ohne die Bibliotheken in web/vendor), tools/, deploy/, Installer und README. Datenbank, Karte,
Einstellungen und config.toml liegen in data/ bzw. außerhalb und bleiben unberührt.

Woher das Paket kommt:
  - hochgeladen in den Einstellungen (geht auch ganz ohne Internet, z. B. vom USB-Stick)
  - eine Update-Quelle (Ordner oder Web-Adresse) mit latest.json neben dem Paket
  - auf der Kommandozeile:  python -m openrisk update PAKET.zip|QUELLE

Vor dem Einspielen wird der bisherige Stand nach data/update/vorher gesichert; schlägt etwas fehl
(z. B. eine neue Abhängigkeit lässt sich ohne Internet nicht installieren), wird er zurückgespielt.
Danach startet OpenRisk neu: Der Starter (OpenRisk.bat, openrisk.sh, systemd) startet das Programm
erneut, wenn es mit RESTART_EXIT_CODE endet.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath

from . import __version__

ROOT = Path(__file__).resolve().parent.parent
PROGRAM_DIRS = ("openrisk", "web", "tools", "deploy")
PROGRAM_FILES = ("requirements.txt", "README.md", "config.example.toml", "install.ps1", "install.bat", "install.sh")
SKIP_DIRS = ("__pycache__", "fonts")  # Schriften (12 MB) lädt tools/fetch_assets.py; Bibliotheken kommen mit
MAX_PACKAGE = 50 * 1024 * 1024  # Programmdateien sind wenige MB; alles darüber ist kein Update-Paket
RESTART_EXIT_CODE = 75


class UpdateError(Exception):
    pass


def parse_version(text: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in str(text).strip().split("."))
    except ValueError:
        return ()


def is_program_file(rel: str) -> bool:
    """Gehört der Pfad (relativ zum Programmordner, mit /) zu den Programmdateien?"""
    path = PurePosixPath(rel)
    if path.is_absolute() or ".." in path.parts or "\\" in rel or ":" in rel or not path.parts:
        return False
    if len(path.parts) == 1:
        return rel in PROGRAM_FILES
    return path.parts[0] in PROGRAM_DIRS and not any(part in SKIP_DIRS for part in path.parts[1:-1]) \
        and not rel.endswith((".pyc", ".tmp"))


def program_files(root: Path) -> list[str]:
    files = [name for name in PROGRAM_FILES if (root / name).is_file()]
    for top in PROGRAM_DIRS:
        files += [p.relative_to(root).as_posix() for p in (root / top).rglob("*") if p.is_file()]
    return sorted(f for f in files if is_program_file(f))


def inspect(package: Path) -> dict:
    """Paket prüfen: Version und enthaltene Programmdateien (Pfad im Programmordner -> Name im ZIP)."""
    if package.stat().st_size > MAX_PACKAGE:
        raise UpdateError("Die Datei ist zu groß für ein Update-Paket.")
    try:
        with zipfile.ZipFile(package) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            # Pakete von GitHub & Co. stecken in einem Unterordner
            marker = next((n for n in names if n.endswith("openrisk/__init__.py")
                           and n.count("/") <= 2), None)
            if not marker:
                raise UpdateError("Das ist kein OpenRisk-Update-Paket (openrisk/__init__.py fehlt).")
            prefix = marker[: -len("openrisk/__init__.py")]
            match = re.search(r'__version__\s*=\s*"([\d.]+)"', z.read(marker).decode("utf-8", "replace"))
            members = {n[len(prefix):]: n for n in names if n.startswith(prefix) and is_program_file(n[len(prefix):])}
    except zipfile.BadZipFile:
        raise UpdateError("Die Datei ist kein gültiges ZIP-Archiv.") from None
    if not match:
        raise UpdateError("Im Paket fehlt die Versionsnummer.")
    if "openrisk/__main__.py" not in members:
        raise UpdateError("Das Paket ist unvollständig (openrisk/__main__.py fehlt).")
    return {"version": match.group(1), "members": members}


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _restore(root: Path, backup: Path, before: list[str]) -> None:
    for rel in set(program_files(root)) - set(before):
        (root / rel).unlink(missing_ok=True)
    for rel in before:
        _copy(backup / rel, root / rel)


def _pip(root: Path) -> None:
    result = subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--quiet",
                             "-r", str(root / "requirements.txt")], capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["unbekannter Fehler"]
        raise UpdateError(f"Abhängigkeiten ließen sich nicht installieren (Internet nötig): {detail[0]}")


def install(package: Path, root: Path = ROOT, pip=_pip) -> dict:
    """Paket einspielen. Gibt {"from", "to"} zurück; bei einem Fehler bleibt der alte Stand erhalten."""
    if (root / "tests").is_dir():
        raise UpdateError("Das ist der Entwicklungsordner – hier werden keine Updates eingespielt.")
    info = inspect(package)
    if parse_version(info["version"]) <= parse_version(__version__):
        raise UpdateError(f"Version {info['version']} ist nicht neuer als die installierte {__version__}.")

    work = root / "data" / "update"
    backup = work / "vorher"
    shutil.rmtree(backup, ignore_errors=True)
    before = program_files(root)
    for rel in before:
        _copy(root / rel, backup / rel)
    old_requirements = (root / "requirements.txt").read_bytes() if (root / "requirements.txt").exists() else b""

    try:
        with zipfile.ZipFile(package) as z:
            for rel, name in info["members"].items():
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_name(target.name + ".tmp")
                tmp.write_bytes(z.read(name))
                tmp.replace(target)
        for rel in set(before) - set(info["members"]):  # im neuen Stand entfallene Dateien
            (root / rel).unlink(missing_ok=True)
        if (root / "requirements.txt").read_bytes() != old_requirements:
            pip(root)
    except Exception:
        _restore(root, backup, before)
        raise

    result = {"from": __version__, "to": info["version"], "ts": int(time.time())}
    (work / "letztes_update.json").write_text(json.dumps(result), encoding="utf-8")
    return result


# ------------------------------------------------------------------ Update-Quelle

def _base(source: str) -> str:
    source = source.strip()
    return source[: -len("latest.json")].rstrip("/\\") if source.endswith("latest.json") else source.rstrip("/\\")


def _remote(source: str) -> bool:
    return source.startswith(("http://", "https://"))


def check(source: str, http) -> dict:
    """latest.json der Quelle lesen: {"version", "file", "sha256", "notes", "date"} plus "newer"."""
    base = _base(source)
    try:
        if _remote(base):
            r = http.get(f"{base}/latest.json", timeout=30)
            r.raise_for_status()
            manifest = r.json()
        else:
            manifest = json.loads((Path(base) / "latest.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise UpdateError("In der Update-Quelle liegt keine latest.json.") from None
    except (OSError, ValueError) as e:
        raise UpdateError(f"Update-Quelle nicht lesbar ({type(e).__name__}).") from None
    except Exception as e:  # httpx-Fehler
        raise UpdateError(f"Update-Quelle nicht erreichbar ({type(e).__name__}).") from None
    if not isinstance(manifest, dict) or not parse_version(manifest.get("version", "")) or not manifest.get("file"):
        raise UpdateError("Die latest.json der Update-Quelle ist unvollständig.")
    return {**manifest, "newer": parse_version(manifest["version"]) > parse_version(__version__)}


def fetch(source: str, manifest: dict, dest: Path, http) -> Path:
    """Paket aus der Quelle holen und die Prüfsumme kontrollieren."""
    base, name = _base(source), PurePosixPath(str(manifest["file"])).name
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if _remote(base):
            with http.stream("GET", f"{base}/{name}", timeout=120) as r:
                r.raise_for_status()
                with dest.open("wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
        else:
            shutil.copyfile(Path(base) / name, dest)
    except OSError as e:
        raise UpdateError(f"Paket nicht lesbar ({type(e).__name__}).") from None
    except Exception as e:
        raise UpdateError(f"Paket nicht ladbar ({type(e).__name__}).") from None
    if manifest.get("sha256") and hashlib.sha256(dest.read_bytes()).hexdigest() != manifest["sha256"]:
        dest.unlink(missing_ok=True)
        raise UpdateError("Das Paket ist beschädigt (Prüfsumme stimmt nicht).")
    return dest
