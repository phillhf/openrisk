"""Update-Paket bauen:  python tools/make_release.py [--notes "Was ist neu"] [--out dist]

Schreibt dist/OpenRisk-<Version>.zip und dist/latest.json. Die Version steht in openrisk/__init__.py
und muss vorher erhöht werden. Den Ordner dist kann eine Installation als Update-Quelle verwenden
(Einstellungen → Software-Update); das ZIP lässt sich dort auch direkt einspielen.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from openrisk import __version__  # noqa: E402
from openrisk.updater import ROOT, program_files  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--notes", default="", help="Kurzbeschreibung der Änderungen (erscheint in den Einstellungen)")
    ap.add_argument("--out", default=str(ROOT / "dist"))
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    package = out / f"OpenRisk-{__version__}.zip"
    files = program_files(ROOT)
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in files:
            z.write(ROOT / rel, rel)
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    manifest = {"version": __version__, "file": package.name, "sha256": digest, "size": package.stat().st_size,
                "date": time.strftime("%Y-%m-%d"), "notes": args.notes}
    (out / "latest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{package} ({len(files)} Dateien, {package.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
