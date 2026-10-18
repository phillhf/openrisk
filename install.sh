#!/usr/bin/env bash
# OpenRisk unter Linux / Raspberry Pi OS installieren.
#
#   ./install.sh                                   # fragt Ort, Port und Kartendetail ab
#   ./install.sh --dir ~/openrisk --port 8642 --map-zoom 7 --service --yes
#
# Optionen:
#   --dir DIR        Installationsort (Standard: ~/openrisk)
#   --port N         Port der Weboberfläche (Standard: 8642)
#   --map-zoom N     Kartendetail 5..15, 0 = keine Karte laden
#   --no-pipelines   Öl-Pipelines nicht laden (dauert 10-20 Minuten)
#   --no-bases       Militärbasen nicht laden (dauert 10-20 Minuten)
#   --service        als systemd-Dienst einrichten (startet beim Hochfahren, braucht sudo)
#   --lan            im lokalen Netz erreichbar machen (kein Passwortschutz; nur bei neuer config.toml)
#   --yes            keine Rückfragen, Standardwerte verwenden
set -euo pipefail

SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIR="" PORT="" MAP_ZOOM="" PIPELINES=ask BASES=ask SERVICE=ask LAN=no YES=no
MAP_ZOOM_LIMIT=15

while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DIR="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --map-zoom) MAP_ZOOM="$2"; shift 2 ;;
    --no-pipelines) PIPELINES=no; shift ;;
    --no-bases) BASES=no; shift ;;
    --service) SERVICE=yes; shift ;;
    --lan) LAN=yes; shift ;;
    --yes) YES=yes; shift ;;
    -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unbekannte Option: $1" >&2; exit 2 ;;
  esac
done

step() { printf '\n== %s\n' "$1"; }
die() { echo "Fehler: $1" >&2; exit 1; }
ask() {  # ask FRAGE STANDARD
  local answer=""
  [ "$YES" = yes ] || read -r -p "$1 [$2]: " answer
  echo "${answer:-$2}"
}
confirm() {  # confirm FRAGE STANDARD(j|n)
  local answer
  answer="$(ask "$1 (j/n)" "$2")"
  case "$answer" in [jJyY]*) return 0 ;; *) return 1 ;; esac
}

[ -f "$SOURCE/openrisk/__main__.py" ] || die "install.sh muss im OpenRisk-Ordner liegen."
echo "OpenRisk - Installation"

# ------------------------------------------------------------------- Python
PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    PY="$candidate"; break
  fi
done
if [ -z "$PY" ] || ! "$PY" -c 'import venv, ensurepip' 2>/dev/null; then
  if command -v apt-get >/dev/null 2>&1 && confirm "Python 3 mit venv fehlt - jetzt per apt installieren (sudo)?" j; then
    sudo apt-get update && sudo apt-get install -y python3 python3-venv
    PY=python3
  fi
fi
[ -n "$PY" ] && "$PY" -c 'import sys, venv, ensurepip; sys.exit(sys.version_info < (3, 11))' 2>/dev/null ||
  die "Python 3.11 oder neuer mit venv wird benötigt (Debian/Raspberry Pi OS: sudo apt install python3-venv)."

# ------------------------------------------------------------------ Abfragen
[ -n "$DIR" ] || DIR="$(ask "Installationsort" "$HOME/openrisk")"
DIR="${DIR/#\~/$HOME}"
mkdir -p "$DIR"
DIR="$(cd "$DIR" && pwd)"

port_free() {
  "$PY" - "$1" <<'EOF'
import socket, sys
s = socket.socket()
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)
EOF
}
while true; do
  [ -n "$PORT" ] || PORT="$(ask "Port der Weboberfläche" 8642)"
  if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [ "$PORT" -lt 1024 ] || [ "$PORT" -gt 65535 ]; then
    echo "Port $PORT liegt außerhalb von 1024-65535."
  elif ! port_free "$PORT"; then
    echo "Port $PORT ist belegt."
  else
    break
  fi
  [ "$YES" = no ] || die "Port $PORT ist nicht verwendbar."
  PORT=""
done

if [ -z "$MAP_ZOOM" ]; then
  if [ -f "$SOURCE/data/map/world.pmtiles" ] || [ -f "$DIR/data/map/world.pmtiles" ]; then
    echo "Vorhandene Offline-Karte wird übernommen (Detailstufe später unter Einstellungen änderbar)."
    MAP_ZOOM=0
  else
    echo
    echo "Offline-Karte, ganze Welt - Größe je Detailstufe:"
    echo "   5 = 15 MB    6 = 45 MB    7 = 190 MB   8 = 560 MB"
    echo "   9 = 1,6 GB  10 = 3,8 GB  11 = 8 GB    12 = 18 GB   15 = 138 GB (alles)"
    echo "   0 = keine Karte  (einzelne Länder in voller Detailstufe später unter Einstellungen)"
    MAP_ZOOM="$(ask "Detailstufe" 7)"
  fi
fi
[[ "$MAP_ZOOM" =~ ^[0-9]+$ ]] && [ "$MAP_ZOOM" -le "$MAP_ZOOM_LIMIT" ] ||
  die "Detailstufe $MAP_ZOOM gibt es nicht (höchstens $MAP_ZOOM_LIMIT)."

if [ "$PIPELINES" = ask ]; then
  if [ -f "$SOURCE/data/geo/pipelines.geojson" ] || [ -f "$DIR/data/geo/pipelines.geojson" ]; then PIPELINES=no
  elif confirm "Öl-Pipelines jetzt laden (dauert 10-20 Minuten)?" j; then PIPELINES=yes; else PIPELINES=no; fi
fi
if [ "$BASES" = ask ]; then
  if [ -f "$SOURCE/data/geo/bases.geojson" ] || [ -f "$DIR/data/geo/bases.geojson" ]; then BASES=no
  elif confirm "Militärbasen jetzt laden (dauert 10-20 Minuten)?" j; then BASES=yes; else BASES=no; fi
fi
if [ "$SERVICE" = ask ]; then
  if [ "$YES" = no ] && command -v systemctl >/dev/null 2>&1 &&
     confirm "Als Dienst einrichten (startet beim Hochfahren, braucht sudo)?" n; then SERVICE=yes; else SERVICE=no; fi
fi

# ------------------------------------------------------------------- Dateien
step "Programmdateien nach $DIR"
if [ "$DIR" = "$SOURCE" ]; then
  echo "Installation im Quellordner - nichts zu kopieren."
else
  for sub in openrisk tools web deploy data/map data/geo; do
    [ -d "$SOURCE/$sub" ] || continue
    mkdir -p "$DIR/$sub"
    # -u: neuere Dateien am Ziel (z. B. eine größere Karte) bleiben erhalten
    cp -Ru "$SOURCE/$sub/." "$DIR/$sub/"
  done
  find "$DIR" -name __pycache__ -type d -prune -exec rm -rf {} +
  rm -rf "$DIR/data/geo/pipelines_cache" "$DIR/data/geo/bases_cache" "$DIR"/data/map/*.part
  cp "$SOURCE/requirements.txt" "$SOURCE/README.md" "$SOURCE/config.example.toml" "$SOURCE/install.sh" "$DIR/"
fi

# ------------------------------------------------------------ Abhängigkeiten
step "Python-Umgebung und Abhängigkeiten"
[ -d "$DIR/.venv" ] || "$PY" -m venv "$DIR/.venv"
VENV_PY="$DIR/.venv/bin/python"
[ -x "$VENV_PY" ] || VENV_PY="$DIR/.venv/Scripts/python.exe"   # Git Bash unter Windows
"$VENV_PY" -m pip install --disable-pip-version-check --quiet -r "$DIR/requirements.txt"

# ------------------------------------------------------------- Konfiguration
step "Konfiguration"
CONFIG="$DIR/config.toml"
HOST=127.0.0.1
[ "$LAN" = no ] || HOST=0.0.0.0
if [ ! -f "$CONFIG" ]; then
  printf '# OpenRisk - weitere Optionen siehe config.example.toml\n[server]\nhost = "%s"\nport = %s\n' "$HOST" "$PORT" > "$CONFIG"
  echo "config.toml angelegt (Port $PORT, Adresse $HOST)."
elif grep -Eq '^[[:space:]]*port[[:space:]]*=' "$CONFIG"; then
  sed -i -E "0,/^([[:space:]]*port[[:space:]]*=[[:space:]]*)[0-9]+/s//\1$PORT/" "$CONFIG"
  echo "Vorhandene config.toml behalten, Port auf $PORT gesetzt."
else
  echo "Vorhandene config.toml enthält keinen Port - bitte dort unter [server] 'port = $PORT' eintragen."
fi

# ------------------------------------------------------------- Offline-Daten
cd "$DIR"
export PYTHONIOENCODING=utf-8
step "Bibliotheken, Schriften und Geodaten"
"$VENV_PY" tools/fetch_assets.py
if [ "$MAP_ZOOM" -gt 0 ]; then
  step "Offline-Karte bis Zoomstufe $MAP_ZOOM"
  "$VENV_PY" tools/fetch_map.py --maxzoom "$MAP_ZOOM" || echo "Karten-Download fehlgeschlagen - kann später nachgeholt werden."
fi
if [ "$PIPELINES" = yes ] && [ ! -f data/geo/pipelines.geojson ]; then
  step "Öl-Pipelines (OpenStreetMap)"
  "$VENV_PY" tools/fetch_pipelines.py || echo "Pipeline-Download fehlgeschlagen - kann später nachgeholt werden."
fi
if [ "$BASES" = yes ] && [ ! -f data/geo/bases.geojson ]; then
  step "Militärbasen (OpenStreetMap)"
  "$VENV_PY" tools/fetch_bases.py || echo "Basen-Download fehlgeschlagen - kann später nachgeholt werden."
fi

# -------------------------------------------------------------- Startdateien
step "Startdateien"
cat > "$DIR/openrisk.sh" <<EOF
#!/usr/bin/env bash
# Exit-Code 75 = nach einem Software-Update neu starten
cd "\$(dirname "\$0")" || exit 1
export OPENRISK_LAUNCHER=1
while true; do
  "$VENV_PY" -m openrisk "\$@"
  code=\$?
  [ "\$code" -eq 75 ] || exit "\$code"
done
EOF
chmod +x "$DIR/openrisk.sh"

if [ "$SERVICE" = yes ]; then
  sed -e "s|^User=.*|User=$(id -un)|" \
      -e "s|^WorkingDirectory=.*|WorkingDirectory=$DIR|" \
      -e "s|^ExecStart=.*|ExecStart=$VENV_PY -m openrisk|" \
      "$DIR/deploy/openrisk.service" | sudo tee /etc/systemd/system/openrisk.service >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl enable --now openrisk
  echo "Dienst 'openrisk' läuft (Status: systemctl status openrisk)."
fi

printf '\nFertig.\n'
echo "  Starten:       $DIR/openrisk.sh"
echo "  Oberfläche:    http://localhost:$PORT"
echo "  Vollbild (Pi): chromium-browser --kiosk http://localhost:$PORT"
echo "  Einstellungen: $CONFIG (Port, Quellen) und Zahnrad in der Oberfläche (Karte)"
echo "  Entfernen:     Dienst stoppen (sudo systemctl disable --now openrisk) und Ordner $DIR löschen"
