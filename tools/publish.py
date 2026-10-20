"""Aktuellen Stand auf GitHub veröffentlichen: hochladen (git push) und Release mit Update-Paket anlegen.

    python tools/publish.py

Voraussetzungen (einmalig): GitHub-Kommandozeile installiert und angemeldet (`gh auth login`),
Repository angelegt (`python tools/publish.py --setup openrisk`). Das Update-Paket der aktuellen Version
baut das Skript selbst, falls es noch fehlt (tools/make_release.py).

Danach findet jede OpenRisk-Installation neue Versionen über die Update-Quelle
    https://github.com/<name>/openrisk/releases/latest/download
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from openrisk import __version__  # noqa: E402

DIST = ROOT / "dist"
TRAILER = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"


def run(*args: str, check: bool = True, capture: bool = True) -> str:
    result = subprocess.run(args, cwd=ROOT, text=True, encoding="utf-8", capture_output=capture)
    if check and result.returncode:
        raise SystemExit(f"Fehler bei {' '.join(args[:3])}: {(result.stderr or result.stdout).strip()}")
    return (result.stdout or "").strip()


def setup(name: str) -> None:
    """Git-Repository anlegen, mit dem GitHub-Konto verbinden und das öffentliche Repository erstellen."""
    login = run("gh", "api", "user", "--jq", ".login")
    user_id = run("gh", "api", "user", "--jq", ".id")
    if not (ROOT / ".git").exists():
        run("git", "init", "-b", "main")
    # Absender der Commits: die anonyme GitHub-Adresse, damit keine private E-Mail öffentlich wird
    run("git", "config", "user.name", login)
    run("git", "config", "user.email", f"{user_id}+{login}@users.noreply.github.com")
    # git push meldet sich mit der Anmeldung von gh an - nur für dieses Projekt, nicht global
    gh = (shutil.which("gh") or "gh").replace("\\", "/")
    run("git", "config", "credential.https://github.com.helper", "")
    run("git", "config", "--add", "credential.https://github.com.helper", f'!"{gh}" auth git-credential')
    if not run("git", "remote", check=False):
        run("gh", "repo", "create", name, "--public", "--description",
            "OpenRisk – Offline-Risikomonitor für Raspberry Pi: Lage, Wetter, Strahlung, Infrastruktur", capture=True)
        run("git", "remote", "add", "origin", f"https://github.com/{login}/{name}.git")
    print(f"Repository: https://github.com/{login}/{name}")
    print(f"Update-Quelle für OpenRisk: https://github.com/{login}/{name}/releases/latest/download")


def manifest() -> dict:
    """latest.json der aktuellen Version - fehlt das Paket, wird es gebaut."""
    path = DIST / "latest.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if data.get("version") != __version__ or not (DIST / data.get("file", "-")).exists():
        run(sys.executable, "tools/make_release.py")
        data = json.loads(path.read_text(encoding="utf-8"))
    return data


def setup_exe() -> Path | None:
    """Windows-Setup der aktuellen Version - wird gebaut, wenn Inno Setup da ist."""
    exe = DIST / f"OpenRisk-Setup-{__version__}.exe"
    if not exe.exists():
        sys.path.insert(0, str(ROOT / "tools"))
        import build_setup
        if not build_setup.iscc():
            print("Hinweis: Inno Setup fehlt - Release ohne Windows-Setup.")
            return None
        build_setup.build()
    return exe


def publish() -> None:
    release = manifest()
    version, notes = release["version"], release.get("notes") or ""
    tag = f"v{version}"
    run("git", "add", "-A")
    if run("git", "status", "--porcelain"):
        subject = f"OpenRisk {version}" + (f": {notes[:60].rstrip()}…" if len(notes) > 60 else f": {notes}" if notes else "")
        run("git", "commit", "-q", "-m", subject, "-m", notes or "Aktualisierung", "-m", TRAILER)
    if not run("git", "tag", "--list", tag):
        run("git", "tag", tag)
    run("git", "push", "-q", "origin", "HEAD:main", "--tags")
    if run("gh", "release", "view", tag, check=False):
        print(f"Release {tag} gibt es schon - nur der Code wurde hochgeladen.")
        return
    exe = setup_exe()
    files = [str(DIST / release["file"]), str(DIST / "latest.json")] + ([str(exe)] if exe else [])
    text = notes or "Aktualisierung"
    if exe:
        text += (f"\n\n**Neu installieren unter Windows:** `{exe.name}` herunterladen und starten. "
                 "Bestehende Installationen aktualisieren sich über Einstellungen → Software-Update.")
    run("gh", "release", "create", tag, *files, "--title", f"OpenRisk {version}", "--notes", text, "--latest")
    print(f"Veröffentlicht: {tag} - {run('gh', 'release', 'view', tag, '--json', 'url', '--jq', '.url')}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--setup", metavar="NAME", help="einmalig: Repository anlegen (z. B. openrisk)")
    args = ap.parse_args()
    if args.setup:
        setup(args.setup)
    publish()


if __name__ == "__main__":
    main()
