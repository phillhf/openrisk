"""OpenRisk starten:  python -m openrisk [serve|collect|update PAKET]"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser

from . import __version__, collectors, config, db, updater
from .geo import Geo
from .scheduler import run_once


def _quiet_disconnects(record: logging.LogRecord) -> bool:
    """Windows meldet jede vom Browser abgebrochene Verbindung (Tab zu, Karte verschoben) als Fehler mit
    Traceback - harmlos, nur Lärm im Protokoll (bekannte Eigenheit von asyncio unter Windows)."""
    error = record.exc_info[1] if record.exc_info else None
    return not (isinstance(error, (ConnectionResetError, ConnectionAbortedError))
                and "_call_connection_lost" in record.getMessage())


def main() -> None:
    ap = argparse.ArgumentParser(prog="openrisk", description="Offline-Risikomonitor")
    ap.add_argument("command", nargs="?", default="serve", choices=["serve", "collect", "update"],
                    help="serve: Oberfläche + Sammler (Standard) · collect: alle Quellen einmal abrufen · "
                         "update: Update-Paket einspielen (OpenRisk vorher beenden)")
    ap.add_argument("package", nargs="?", help="bei update: ZIP-Datei oder Update-Quelle (Ordner/Web-Adresse)")
    ap.add_argument("--version", action="version", version=f"OpenRisk {__version__}")
    ap.add_argument("--config", help="Pfad zur config.toml")
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    ap.add_argument("--no-collect", action="store_true", help="nur anzeigen, nichts aus dem Netz laden")
    ap.add_argument("--open", action="store_true", help="Oberfläche nach dem Start im Browser öffnen")
    args = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")  # Umlaute dürfen auf alten Konsolen nicht zum Absturz führen

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("asyncio").addFilter(_quiet_disconnects)
    cfg = config.load(args.config)

    if args.command == "update":
        sys.exit(update(args.package, cfg))

    if args.command == "collect":
        db_path = cfg["storage"]["data_dir"] / "openrisk.db"
        db.init(db_path)
        geo = Geo(cfg["storage"]["data_dir"] / "geo")
        for name in collectors.PERIODIC:
            if cfg["sources"][name].get("enabled", True):
                ok, message = run_once(name, cfg, geo, db_path)
                print(f"{name:10} {'ok    ' if ok else 'FEHLER'} {message}")
        return

    import uvicorn

    from .server import create_app

    app = create_app(cfg, collect=cfg["collect"]["enabled"] and not args.no_collect)
    host, port = args.host or cfg["server"]["host"], args.port or cfg["server"]["port"]
    print(f"OpenRisk läuft auf http://localhost:{port}  (Beenden mit Strg+C)", flush=True)
    if args.open:
        threading.Timer(1.5, webbrowser.open, [f"http://localhost:{port}"]).start()
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    app.state.stop = lambda: setattr(server, "should_exit", True)  # für den Neustart nach einem Update
    server.run()
    if getattr(app.state, "restart", False):
        print("Neustart nach Update …", flush=True)
        sys.exit(updater.RESTART_EXIT_CODE)  # der Starter (OpenRisk.bat, openrisk.sh, systemd) startet neu


def update(package: str | None, cfg: dict) -> int:
    from pathlib import Path

    import httpx

    if not package:
        print("Aufruf: python -m openrisk update PAKET.zip  oder  ... update ORDNER/WEB-ADRESSE")
        return 2
    try:
        path = Path(package)
        if not path.is_file():  # Update-Quelle
            with httpx.Client(follow_redirects=True) as http:
                latest = updater.check(package, http)
                if not latest["newer"]:
                    print(f"Installiert ist {__version__}, die Quelle hat {latest['version']} – nichts zu tun.")
                    return 0
                path = updater.fetch(package, latest, cfg["storage"]["data_dir"] / "update" / "paket.zip", http)
        result = updater.install(path)
    except updater.UpdateError as e:
        print(f"Fehler: {e}")
        return 1
    print(f"Version {result['from']} -> {result['to']} eingespielt. OpenRisk jetzt neu starten.")
    return 0


if __name__ == "__main__":
    main()
