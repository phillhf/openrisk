"""Offline-Weltkarte herunterladen (OpenStreetMap über Protomaps).

    python tools/fetch_map.py --maxzoom 7             # Standard (~190 MB)
    python tools/fetch_map.py --maxzoom 9 --dry-run   # nur Größen anzeigen

Stufen 0..15 frei wählbar (15 = alles, ganze Welt ~138 GB). Einzelne Länder in voller
Detailstufe lädt die Oberfläche (Einstellungen → Offline-Karte → Länder). Die Datei ist
plattformunabhängig und kann 1:1 auf den Raspberry Pi kopiert werden.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openrisk import config, mapfetch  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--maxzoom", type=int, default=7, help="höchste Zoomstufe (Standard 7)")
    ap.add_argument("--url", help="Quell-PMTiles (Standard: neuester Protomaps-Build)")
    ap.add_argument("--config", help="Pfad zur config.toml")
    ap.add_argument("--dry-run", action="store_true", help="nur Größe je Zoomstufe anzeigen")
    ap.add_argument("--threads", type=int, default=6)
    args = ap.parse_args()

    data_dir = config.load(args.config)["storage"]["data_dir"]

    out = data_dir / "map" / "world.pmtiles"
    show = lambda done, total: print(f"\r  {done / 1e6:9.1f} / {total / 1e6:.1f} MB", end="", flush=True)
    try:
        result = mapfetch.extract(out, args.maxzoom, url=args.url, threads=args.threads, dry_run=args.dry_run,
                                  progress=show)
    except mapfetch.MapError as e:
        print(f"\nFehler: {e}")
        return 1
    print(f"\nQuelle: {result['url']}")
    print("Zoom   Größe kumuliert")
    for zoom, size in result["zoom_sizes"]:
        print(f"  z{zoom:<2} {size / 1e6:>10.1f} MB")
    if not args.dry_run:
        print(f"Fertig: {out}  ({out.stat().st_size / 1e6:.1f} MB, Zoom 0-{result['max_zoom']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
