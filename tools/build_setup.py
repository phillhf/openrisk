"""Grafisches Windows-Setup bauen (Inno Setup 6): dist/OpenRisk-Setup-<Version>.exe

    python tools/build_setup.py

Enthält dieselben Programmdateien wie das Update-Paket (ohne Schriften - die lädt install.ps1). Das Setup
selbst ist nur die Oberfläche; Python-Umgebung, Abhängigkeiten, Bibliotheken und Karte richtet install.ps1
ein (siehe installer/OpenRisk.iss). Inno Setup: winget install JRSoftware.InnoSetup
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from openrisk import __version__, updater  # noqa: E402

DIST = ROOT / "dist"


def iscc() -> str | None:
    for candidate in (r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe", r"%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe",
                      r"%ProgramFiles%\Inno Setup 6\ISCC.exe"):
        path = os.path.expandvars(candidate)
        if os.path.exists(path):
            return path
    return shutil.which("iscc")


def build() -> Path:
    compiler = iscc()
    if not compiler:
        raise SystemExit("Inno Setup 6 nicht gefunden (winget install JRSoftware.InnoSetup).")
    stage = DIST / "_setup" / f"OpenRisk-{__version__}"
    shutil.rmtree(stage, ignore_errors=True)
    for rel in updater.program_files(ROOT):
        (stage / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, stage / rel)
    subprocess.run([compiler, "/Qp", f"/DAppVersion={__version__}", f"/DPkgDir={stage}", f"/O{DIST}",
                    str(ROOT / "installer" / "OpenRisk.iss")], check=True)
    shutil.rmtree(DIST / "_setup", ignore_errors=True)
    out = DIST / f"OpenRisk-Setup-{__version__}.exe"
    print(f"{out} ({out.stat().st_size // 1024} KB)")
    return out


if __name__ == "__main__":
    build()
