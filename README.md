# OpenRisk – Offline-Risikomonitor

Weltkarte mit Nachrichten, Wetter, Katastrophen, Militärflugzeugen, Militärschiffen,
Militärbasen und Öl-Pipelines. Alles wird in einer lokalen SQLite-Datenbank gespeichert; Karte,
Schriften und Programmbibliotheken liegen lokal. Ohne Internet zeigt OpenRisk den
zuletzt gespeicherten Stand, mit Internet aktualisieren die Sammler im Hintergrund.

Ausgelegt für einen Raspberry Pi 5 (4 GB): ein einzelner Python-Prozess, keine
Build-Werkzeuge, keine Cloud-Dienste.

## Installation

**Windows, am einfachsten:** `OpenRisk-Setup-<Version>.exe` aus den
[Releases](https://github.com/phillhf/openrisk/releases/latest) herunterladen und starten. Das Setup
(ohne Administratorrechte) fragt Ordner, Port, Kartendetail und Verknüpfungen ab, erkennt eine
vorhandene Installation und aktualisiert sie dann – Datenbank, Karten, Einstellungen und Schlüssel
bleiben. Fehlt Python, installiert es das auf Wunsch über winget. Entfernen über *Einstellungen →
Apps* oder „OpenRisk deinstallieren“ im Startmenü; dabei fragt es, ob auch die Daten gelöscht werden
sollen. Still: `OpenRisk-Setup-<Version>.exe /VERYSILENT /DIR="D:\OpenRisk" /PORT=8642 /MAPZOOM=7`
(weitere Schalter: `/PYTHON=1`, `/NODESKTOP=1`, `/AUTOSTART=1`, `/NOPIPELINES=1`, `/NOBASES=1`).
Das Setup baut `python tools/build_setup.py` (Inno Setup 6); `tools/publish.py` hängt es an jedes Release.

Der Installer fragt Installationsort, Port und Kartendetail ab, richtet Python-Umgebung
und Abhängigkeiten ein und lädt alles, was offline gebraucht wird. Voraussetzung ist
Python 3.11 oder neuer; fehlt es, bietet der Installer die Installation an (Windows:
winget, Linux: apt).

| System | Aufruf | Ohne Rückfragen |
|---|---|---|
| Windows | Doppelklick auf `install.bat` | `install.bat -InstallDir D:\OpenRisk -Port 8642 -MapZoom 7 -Unattended` |
| Linux / Raspberry Pi | `bash install.sh` | `bash install.sh --dir ~/openrisk --port 8642 --map-zoom 7 --service --yes` |

Windows: legt `OpenRisk.bat` samt Desktop-Verknüpfung an (`-Autostart` startet beim
Anmelden), Entfernen mit `install.bat -Uninstall`. Linux: legt `openrisk.sh` an;
`--service` richtet einen systemd-Dienst ein, `--lan` gibt die Oberfläche im Netz frei.
Ein erneuter Lauf über eine bestehende Installation aktualisiert das Programm und den
Port, Datenbank und Karte bleiben erhalten.

### Von Hand

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # Linux/Pi: .venv/bin/pip

# einmalig mit Internet – lädt alles, was offline gebraucht wird
.venv/Scripts/python tools/fetch_assets.py         # Bibliotheken, Schriften, Ländergrenzen (~16 MB)
.venv/Scripts/python tools/fetch_map.py            # Weltkarte bis Zoom 7 (~190 MB)
.venv/Scripts/python tools/fetch_pipelines.py      # Öl-Pipelines aus OpenStreetMap
.venv/Scripts/python tools/fetch_bases.py          # Militärbasen aus OpenStreetMap

.venv/Scripts/python -m openrisk                   # http://localhost:8642
```

Weitere Aufrufe:

| Befehl | Zweck |
|---|---|
| `python -m openrisk --open` | starten und Oberfläche im Browser öffnen |
| `python -m openrisk --no-collect` | nur anzeigen, kein Netzzugriff |
| `python -m openrisk collect` | alle Quellen einmal abrufen und beenden |
| `python -m openrisk --host 0.0.0.0` | im lokalen Netz erreichbar machen (kein Passwortschutz) |
| `python -m openrisk update PAKET.zip` | Update-Paket einspielen (OpenRisk vorher beenden; statt der Datei geht auch eine Update-Quelle) |

Einstellungen: `config.example.toml` nach `config.toml` kopieren.

## Software-Updates

Neue Versionen kommen als Update-Paket (ZIP, rund 120 KB), gebaut im Entwicklungsordner mit
`python tools/make_release.py --notes "Was ist neu"` – die Versionsnummer steht in
`openrisk/__init__.py`. Das Paket landet mit einer `latest.json` im Ordner `dist`.

Eingespielt wird unter **Einstellungen → Software-Update**:

- **Update-Quelle:** ein Ordner (auch Netzlaufwerk oder USB-Stick) oder eine Web-Adresse, in der
  `latest.json` und das Paket liegen. OpenRisk schaut alle 6 Stunden nach; gibt es eine neue
  Version, erscheint ein Punkt am Zahnrad und „Jetzt installieren“.
- **Update-Datei einspielen:** das ZIP direkt auswählen – geht ganz ohne Internet.

Ersetzt werden nur die Programmdateien. Datenbank, Karte, Einstellungen, Schlüssel und
`config.toml` bleiben erhalten. Der bisherige Stand wird nach `data/update/vorher` gesichert und
bei einem Fehler zurückgespielt, z. B. wenn eine neue Abhängigkeit ohne Internet nicht ladbar ist.
Danach startet OpenRisk von selbst neu (über `OpenRisk.bat`, `openrisk.sh` oder den systemd-Dienst)
und die Seite lädt sich neu. Weil dabei neuer Programmcode läuft, geht das Einspielen nur direkt am
Gerät (`http://localhost`), nicht aus dem Netz.

### Veröffentlichen auf GitHub

`python tools/publish.py` lädt den aktuellen Stand auf GitHub hoch und legt ein Release mit dem
Update-Paket und der `latest.json` an (baut das Paket, falls es fehlt). Einmalig vorher: GitHub-
Kommandozeile installieren und anmelden (`winget install --id GitHub.cli`, dann `gh auth login`),
danach `python tools/publish.py --setup openrisk` – legt das öffentliche Repository an.

Als **Update-Quelle** trägt man dann auf jedem Gerät
`https://github.com/<name>/openrisk/releases/latest/download` ein: Neue Versionen kommen so von
überall, ohne Zugriff auf den Entwicklungsrechner. Ins Repository gelangt nur Programmcode – Daten,
Karten, `config.toml`, Einstellungen und Schlüssel bleiben auf dem Gerät (siehe `.gitignore`).

## Ortssuche

Das Feld „Ort suchen …“ oben springt zu einem Ort und setzt dort eine Nadel mit Einwohnerzahl,
Bundesland bzw. Region und Koordinaten. Gesucht wird nach dem Anfang des Namens, Umlaute und
Schreibweisen sind egal (München, Muenchen, Munich). Pfeiltasten und Enter wählen aus.

Das Ortsverzeichnis stammt von GeoNames: weltweit alle Orte ab 500 Einwohnern, in Deutschland
alle Orte bis zum Dorf und Ortsteil, zusammen rund 300.000. OpenRisk lädt es beim ersten Start
(~20 MB Download, ergibt `data/geo/places.db` mit ~36 MB) und erneuert es alle 90 Tage. Bis dahin
findet die Suche nur Länder und große Städte.

## Mobilfunkmasten, Bunker, Leitungen und Wasserstellen

Vier Ebenen für Deutschland aus OpenStreetMap, monatlich aktualisiert (Abruf über die Overpass-API,
etwa 3–4 Minuten, mit Ausweichservern; ohne Internet bleibt der letzte Stand):

- **Mobilfunkmasten (DE):** rund 32.000 Funkmasten und -türme; rosa = Mobilfunk (laut Eintrag oder
  Betreiber Telekom, Vodafone, Telefónica/O2, 1&1, Vantage Towers, Deutsche Funkturm), lila = sonstiger
  Funk. Popup mit Betreiber, Höhe und Netzen (2G–5G), soweit eingetragen. OpenStreetMap kennt etwa ein
  Drittel der tatsächlichen Mobilfunkstandorte; die vollständige Liste der Bundesnetzagentur ist nicht
  als offene Daten verfügbar.
- **Bunker (DE):** rund 12.500 Hoch- und Tiefbunker, Luftschutzbunker, Munitions- und Militärbunker;
  hell = erhalten, dunkel = aufgegeben/Ruine. Ob ein Bunker als Schutzraum nutzbar ist, geht daraus
  nicht hervor.
- **Fernmelde-/Glasfaserleitungen (DE):** rund 3.500 Abschnitte mit zusammen etwa 8.300 km, die meisten
  Glasfaser. Popup mit Betreiber, Leitungsart und Lage (unterirdisch/oberirdisch). Das ist nur ein kleiner
  Ausschnitt: überwiegend örtliche Glasfasernetze (z. B. Deutsche Glasfaser), dazu einzelne Fernstrecken
  (z. B. GasLINE entlang von Gasleitungen). Die großen Backbone-Trassen der Netzbetreiber sind nicht
  öffentlich – weder in OpenStreetMap noch als offene Daten.
- **Quellen & Wasserstellen (DE):** rund 55.000 Einträge – 22.500 Quellen, 15.500 Brunnen,
  8.600 Trinkwasserstellen, dazu Wasserhähne und Wasserentnahmestellen. Grün = laut Eintrag
  Trinkwasser, blau = Quelle, lila = Brunnen, grau = sonstige. Popup mit Name, Trinkbarkeit, ob die
  Quelle versiegen kann, Pumpe und Zugang. Natürliche Quellen sind nicht überwacht – im Zweifel
  abkochen oder filtern.

## Internet-Infrastruktur weltweit

Drei Ebenen, monatlich aktualisiert (Abruf etwa 20 Sekunden, ohne Konto; ohne Internet bleibt der letzte Stand):

- **Seekabel:** über 700 Unterseekabel aus der Submarine Cable Map von TeleGeography, jedes in eigener
  Farbe; gestrichelt = geplant/im Bau. Dazu rund 1.900 Anlandepunkte (Kreise an der Küste). Ein Klick
  nennt alle Kabel an der Stelle mit Inbetriebnahme, Länge, Eigentümern und Anlandepunkten. Über
  Seekabel läuft fast der gesamte Datenverkehr zwischen den Kontinenten; der Verlauf auf See ist
  schematisch.
- **Internetknoten (IXP):** rund 900 Internet Exchange Points aus PeeringDB – hier tauschen Netzbetreiber
  Datenverkehr aus (z. B. DE-CIX Frankfurt mit über 1.000 Netzen). Größe = Zahl der angeschlossenen
  Netze. Eingezeichnet am größten Standort im eigenen Land; liegen mehrere Knoten im selben
  Rechenzentrum, nennt das Popup alle.
- **Rechenzentren:** rund 5.300 Kolokations-Rechenzentren aus PeeringDB (gut 300 in Deutschland), in
  denen sich Netze zusammenschalten; Größe = Zahl der Netze im Haus. Reine Firmen- oder
  Cloud-Rechenzentren ohne öffentliche Zusammenschaltung fehlen.

## Orts- und Stadtinfos

Ein Klick auf einen Ortsnamen in der Karte oder ein Treffer der Ortssuche öffnet ein Info-Fenster:

- **Deutschland, alle Gemeinden:** amtliche Einwohnerzahl (Männer/Frauen), Fläche, Einwohner je km²,
  Postleitzahl und Besiedlung vom Statistischen Bundesamt; Kfz-Kennzeichen, Vorwahl, Höhe,
  Bürgermeister/in, Partnerstädte und Website aus Wikidata. Ortsteile zeigen die Daten ihrer Gemeinde.
- **Weltweit:** Einwohner, Höhe und Ortszeit (GeoNames); für Städte ab 100.000 Einwohnern zusätzlich
  Fläche, Gründungsjahr, Bürgermeister/in, Partnerstädte und Website (Wikidata).
- **In der Nähe:** Temperatur der nächsten DWD-Station, Strahlung der nächsten Messsonde, Radon der
  Gemeinde und Meldungen im Umkreis von 30 km aus den letzten 7 Tagen.

Die Daten liegen offline in `data/geo/cityinfo.db` (~4 MB). Sie werden beim ersten Start sofort geladen
(etwa 2–3 Minuten, vor allem Wikidata) und danach alle 14 Tage aktualisiert; ohne Internet bleibt der
letzte Stand.

## Länderinfos

Ein Klick auf ein Land (in der Übersicht bis Zoomstufe 7) zeigt Hauptstadt, Kontinent, Einwohner,
Fläche, Bevölkerungsdichte, Wirtschaftsleistung gesamt und je Einwohner, Lebenserwartung, Sprachen,
Währung, Vorwahl, Internet-Endung und Nachbarländer – dazu die Lage: Risiko-Index und die
wichtigsten Ereignisse der letzten 7 Tage (im Rückblick zum gewählten Zeitpunkt). Grunddaten von
GeoNames, Kennzahlen von der Weltbank (jeweils jüngster Wert mit Jahr). Alle Länder werden beim
ersten Start sofort geladen, danach alle 14 Tage aktualisiert, und liegen offline in
`data/geo/country_info.json`; ohne Internet bleibt der letzte Stand.

## Kartenmaterial

Die Karte ist eine einzige Datei (`data/map/world.pmtiles`), herausgeschnitten aus dem
tagesaktuellen Protomaps-Build (OpenStreetMap). Laden, Ersetzen und Löschen geht in der
Oberfläche über das Zahnrad (**Einstellungen → Offline-Karte**) oder mit
`tools/fetch_map.py --maxzoom N`. Größe der ganzen Welt je höchster Zoomstufe:

| Stufe | Größe | Detail |
|---|---|---|
| 5 | 15 MB | Länder, Großstädte |
| 6 | 45 MB | + Regionen, Fernstraßen |
| 7 (Standard) | 190 MB | + mittlere Städte |
| 8 | 560 MB | + Kleinstädte, Landstraßen |
| 9 | 1,6 GB | + Ortsteile |
| 10 | 3,8 GB | + Stadtstraßen grob |
| 11 | 8 GB | + Straßennetz |
| 12 | 18 GB | + Wege, Gebäude grob |
| 15 | 138 GB | alles (Gebäude, Hausnummern, Geschäfte) |

Alle Stufen sind frei wählbar, und hineinzoomen lässt sich immer beliebig tief – über der
höchsten geladenen Stufe wird die Karte vergrößert, zeigt dann aber keine weiteren Details.
Ohne Karte zeigt OpenRisk nur Länderumrisse. `fetch_map.py --dry-run` zeigt die Größen,
ohne zu laden.

### Einzelne Länder in voller Detailstufe

Unter **Einstellungen → Offline-Karte → Länder in voller Detailstufe** lässt sich ein Land
komplett bis Stufe 15 laden – mit Straßen, Gebäuden, Hausnummern und Geschäften, aber nur für
dieses Land statt für die ganze Welt. Beispiele: Liechtenstein 23 MB, Luxemburg 116 MB,
Deutschland grob 5–8 GB. Jedes Land liegt als eigene Datei in `data/map/regions/<ISO3>.pmtiles`
über der Weltkarte; mehrere Länder sind möglich, jedes lässt sich einzeln löschen. Nach dem
Laden oder Löschen die Seite neu laden.

### Kartenstil und Gelände

Unter **Einstellungen → Kartenstil und Gelände** gibt es zwei Stile: **Dunkel** und **Hell** – hell
nach Art der Wanderkarten mit grünem Wald, blauen Gewässern und Flüssen. Dazu kommen Höhendaten
(Mapterhorn, weltweit, Copernicus und andere amtliche Höhenmodelle):

- **Ganze Welt, grob** (Stufe 5, rund 80 MB) – Gebirge und Täler im Überblick.
- **Einzelne Länder in voller Auflösung** (Stufe 12, etwa 20 m) – Deutschland rund 950 MB,
  Liechtenstein 8 MB.

Daraus zeichnet die Ebene **Gelände** eine Schummerung (Berge als Schatten) und ab Zoomstufe 9
Höhenlinien mit Metern (je nach Zoom alle 50, 20, 10 oder 5 m). Die Höhenlinien berechnet der Browser
aus den Höhendaten, es gibt dafür keine eigene Datei. Die Dateien liegen in
`data/map/terrain/` (`WORLD.pmtiles` und `<ISO3>.pmtiles`); in Ländern mit feinen Daten werden diese
genommen, sonst die grobe Welt.

## Wetterfilm: Regenradar, Wolken und Wind

Drei Ebenen mit einer gemeinsamen Zeitleiste unten auf der Karte (▶ spielt den Film ab,
„Jetzt“ springt zum aktuellen Stand):

- **Regenradar mit Vorhersage (DWD):** Radarkomposit RV, Deutschland und Randgebiete, 1 km. Die
  letzten zwei Stunden und die Vorhersage (Nowcasting) für die nächsten zwei Stunden im
  10-Minuten-Takt – dasselbe Produkt wie in der DWD-App. Hellblau = leichter Regen bis rot =
  Starkregen.
- **Wolken (Satellit):** Meteosat (MTG, Infrarot) über Mitteleuropa, letzte drei Stunden alle
  10 Minuten, Tag und Nacht gleich gut. Nur Beobachtungen – eine Wolken-Vorhersage gibt es nicht.
  Die Bilder kommen mit rund 30 Minuten Verzögerung.
- **Windströmung:** bewegte Linien zeigen, wohin der Wind weht; Farbe nach Stärke (weiß/blau bis
  8 m/s, gelb bis 14 m/s, orange bis 20 m/s = Sturm, rot darüber). Raster über Deutschland und den
  Nachbarländern (Open-Meteo), stündlich bis 48 Stunden voraus.

Alles wird vom Server regelmäßig geladen und in `data/weathermaps/` bzw. `data/geo/wind.json`
gespeichert – ohne Internet läuft der letzte Film weiter. Datenmenge rund 120 MB am Tag; wer das
nicht möchte, schaltet in `config.toml` `[sources.weathermaps] enabled = false`.

## Datenquellen

| Ebene | Quelle | Abruf | Hinweis |
|---|---|---|---|
| Nachrichten | RSS (Tagesschau, Spiegel, DW, BBC, Al Jazeera, Guardian, France 24, UN) | 15 min | Ort und Stufe per Schlagwort-Heuristik |
| Konflikte | GDELT | 15 min | frei, ohne Konto; Kampfhandlungen, Luft- und Artillerieangriffe, Anschläge, Massengewalt, gewaltsame Proteste der letzten 7 Tage (siehe unten) |
| Erdbeben | USGS (ab M 4,5) | 10 min | |
| Katastrophen | GDACS, NASA EONET | 30/60 min | Stürme, Fluten, Vulkane, Waldbrände, Dürren |
| Wetter | Open-Meteo | 60 min | 150 Städte, aktuell + 4 Tage; Extremwerte werden zu Warnungen |
| Wetterwarnungen Deutschland | DWD | 10 min | amtliche Warnungen je Landkreis, als eingefärbte Warngebiete |
| Regenradar mit Vorhersage | DWD (Radarkomposit RV) | 10 min | 2 Std. zurück, 2 Std. voraus |
| Wolken | EUMETSAT (Meteosat, Infrarot) | 10 min | letzte 3 Stunden |
| Wind | Open-Meteo | 3 Std. | Windfeld bis 48 Std. voraus |
| Höhendaten | Mapterhorn | auf Wunsch | Schummerung und Höhenlinien |
| Warnungen Bevölkerungsschutz | NINA (BBK) – enthält KATWARN, BIWAPP, MoWaS, Hochwasserportale, Polizei | 10 min | nur Deutschland; Warngebiete als Flächen |
| Strahlung Deutschland | BfS, ODL-Messnetz | 30 min | rund 1.700 Sonden, Stundenwerte; Ampel siehe unten |
| Strahlung Europa | EURDEP (über das BfS) | 60 min | rund 3.600 Stationen; Verlauf nur alle 6 Stunden |
| Radon | BfS | wöchentlich | Schätzwert je Gemeinde für Wohnräume (Bq/m³) und Radon-Vorsorgegebiete |
| Wetterstationen Deutschland | DWD Open Data | 10 min | rund 1000 Stationen, etwa die Hälfte mit Temperatur |
| Militärflugzeuge | adsb.lol, Ausweichquelle adsb.fi | 2 min | nur Maschinen mit aktivem Transponder |
| Militärschiffe | aisstream.io | Dauerverbindung | **kostenloser API-Schlüssel nötig** (siehe unten); nur Schiffe, die AIS als „Militär“ senden |
| Militärbasen | OpenStreetMap | einmalig | Basen, Flugplätze, Marinestützpunkte, Kasernen – soweit eingetragen |
| Öl-Pipelines | OpenStreetMap | einmalig | |
| Seekabel | TeleGeography Submarine Cable Map | monatlich | Kabel und Anlandepunkte weltweit |
| Internetknoten, Rechenzentren | PeeringDB | monatlich | Internet Exchange Points und Kolokations-Rechenzentren weltweit |
| Masten, Bunker, Leitungen (DE) | OpenStreetMap (Overpass) | monatlich | nur was eingetragen ist |
| Länder, Städte | Natural Earth | einmalig | |

**API-Schlüssel und Zugänge** trägt man in der Oberfläche ein: Zahnrad → Einstellungen.
Schlüssel (aisstream) werden in `data/settings.json` auf dem Gerät gespeichert, die
Verbindung startet sofort, der Status steht direkt unter dem Feld. Eine weitere Quelle
braucht nur einen Eintrag in `API_KEYS` (`openrisk/settings.py`).

**Konflikte (GDELT):** GDELT liest rund um die Uhr Nachrichten aus aller Welt und erkennt
darin Ereignisse automatisch – frei und ohne Konto, aber ungeprüft und verrauscht. OpenRisk
nimmt deshalb nur Hauptereignisse eines Artikels, die auf eine Stadt genau verortet sind;
Angriffe und Kämpfe nur mit bewaffneten Beteiligten (Militär, Rebellen, Aufständische) oder
als ausdrückliche Angriffsart (Luft-, Artillerieangriff, Bombenanschlag). Gleiches am selben
Ort und Tag wird zusammengefasst und erst ab 3 unabhängigen Quellen gezeigt; viel berichtete
Kampfhandlungen bekommen eine höhere Stufe. Jedes Ereignis verlinkt einen Quellartikel –
vor Entscheidungen bitte dort nachlesen. Die Ebene zeigt die letzten 7 Tage unabhängig vom
gewählten Zeitraum. Nach einer Pause holt OpenRisk bis zu 48 Stunden nach.
(ACLED wurde entfernt: Konten mit privater E-Mail erhalten dort keinen API-Zugang.)

Militärische Schiffe und Flugzeuge lassen sich aus offenen Quellen nur lückenhaft
verfolgen: Wer Transponder oder AIS abschaltet, erscheint nicht.

Beim ersten Start sind alle Ebenen ausgeschaltet; links schaltet man ein, was man sehen will.
Nur eingeschaltete Ebenen werden geladen und gezeichnet – das hält Browser und Pi entlastet.
Die Auswahl merkt sich der Browser. Gesammelt wird im Hintergrund trotzdem alles, damit der
Verlauf vollständig bleibt.

Jedes Ereignis erscheint als Symbol seiner Art (Krieg/Konflikt, Erdbeben, Sturm, …) in
der Farbe seiner Stufe; die Legende „Symbole“ links zeigt, was gerade auf der Karte liegt.
Liegen mehrere Meldungen am selben Ort, zeigt das Bündel das schwerste Ereignis und die
Anzahl. Amtliche Unwetterwarnungen gibt es nur für Deutschland; weltweit kommen
Unwetter aus GDACS/NASA (große Stürme, Fluten) und aus Extremwerten der 150 Städte.

## Verlauf

Alles, was die Sammler abrufen, bleibt in der Datenbank (`data/openrisk.db`) und ist
offline durchsuchbar:

- **Meldungen und Ereignisse** (Nachrichten, Katastrophen, Konflikte) vollständig.
- **Warnungen** (DWD, NINA) bleiben nach ihrer Aufhebung erhalten und sind als
  „aufgehoben“ gekennzeichnet.
- **Wetter**: stündlich je Stadt (Open-Meteo) und je DWD-Station; die Popups zeigen die
  Temperaturkurve über 7 Tage, 30 Tage oder 1 Jahr.
- **Flugzeuge und Schiffe**: jede Position (sobald sich das Fahrzeug mindestens 2 km bewegt
  hat) mit Höhe, Geschwindigkeit und Kurs, dazu welches Rufzeichen es wann trug. Links unter
  „Flug- oder Schiffssuche“ Rufzeichen, Kennzeichen, MMSI oder Typ und einen Tag eingeben –
  die Spur erscheint mit Uhrzeiten auf der Karte. Im Rückblick stehen Flugzeuge und Schiffe an
  ihrer damaligen Position. Aufbewahrung getrennt einstellbar (Standard 30 Tage), weil Spuren
  viel Platz brauchen: grob geschätzt 10 MB pro Tag.

Bedienung: **Rückblick** oben wählt einen früheren Zeitpunkt – Karte, Lageliste,
Risiko-Index und Wetter zeigen dann den damaligen Stand. **Verlauf** neben dem Suchfeld
durchsucht alle gespeicherten Meldungen. Die Aufbewahrungsdauer (30 Tage bis unbegrenzt,
Standard 1 Jahr) steht unter Einstellungen → Verlauf. Aufgeräumt wird stündlich; die Frist
zählt ab dem letzten erfolgreichen Datenabruf – ist das Gerät offline, bleibt alles erhalten. Der Verlauf beginnt mit dem ersten
Start und hat Lücken, solange das Gerät aus ist – RSS-Feeds liefern keine alten Meldungen
nach. Grobe Schätzung des Speicherbedarfs: einige hundert MB pro Jahr, vor allem durch die
stündlichen Werte der rund 500 DWD-Stationen mit Temperatur.

Die **Strahlungs-Ampel** ist eine eigene Einordnung, keine amtliche Warnung. Grundlage: Die
natürliche Strahlung ist an jedem Messort weitgehend gleich und steigt vor allem bei Regen
kurzzeitig an. Grün = üblicher Bereich der Sonde; Gelb = mindestens doppelt so hoch wie dort
üblich (Median der letzten 7 Tage) oder über 0,3 µSv/h – meist Regen, beobachten; Rot =
mindestens dreifach und über 0,3 µSv/h oder über 1 µSv/h – natürlich kaum zu erklären, erscheint
auch als Meldung in der Lage. Am ersten Tag gibt es noch keinen „üblichen“ Wert, dann gelten nur
die festen Grenzen. Grenzen in `openrisk/collectors/radiation.py`.

Dauerhaft belastete Orte (etwa die Sperrzone um Tschernobyl) bleiben rot, erzeugen aber keine
täglichen Meldungen: Gemeldet wird nur ein plötzlicher Anstieg auf mindestens das Doppelte des
üblichen Werts.

**Radon** wird nicht laufend gemessen; das BfS veröffentlicht Karten. Die Ebene färbt jede Gemeinde
nach dem geschätzten Durchschnitt in Wohnräumen: grün unter 100 Bq/m³ (Empfehlung der WHO),
gelb bis 300 Bq/m³ (Referenzwert in Deutschland), rot darüber. Rot gestrichelt sind die
Radon-Vorsorgegebiete nach Strahlenschutzgesetz.

Der **Risiko-Index** je Land (0–100) summiert die Ereignisse der letzten 7 Tage,
gewichtet nach Stufe und Alter (`openrisk/risk.py`). Er ist eine grobe Orientierung
und bevorzugt Länder, über die viel berichtet wird.

## Raspberry Pi 5

Den OpenRisk-Ordner auf den Pi kopieren (mit `data/map` und `data/geo` spart das die
Downloads) und dort installieren:

```bash
bash install.sh --service
chromium-browser --kiosk http://localhost:8642      # Vollbild am Pi-Display
```

Der Serverprozess belegt unter 100 MB RAM, Chromium mit der Karte einige hundert MB.
Die Datenbank wächst mit den Standardwerten auf wenige zehn MB und wird stündlich
bereinigt (`[storage]` in `config.toml`).

## Aufbau

```
openrisk/            Server, Datenbank, Sammler (openrisk/collectors/)
tools/               einmalige Downloads: Karte, Bibliotheken, Pipelines
web/                 Oberfläche (MapLibre GL, ohne Build-Schritt)
data/                Datenbank, Karte, Geodaten (wird erzeugt)
deploy/              Vorlage für den systemd-Dienst
install.bat/.ps1     Installer für Windows
install.sh           Installer für Linux / Raspberry Pi
```

Eine neue Datenquelle ist eine Funktion `collect(ctx) -> int` in
`openrisk/collectors/`, eingetragen in `PERIODIC` und in `config.py`.

## Lizenzen der Daten

Karte und Pipelines © OpenStreetMap-Mitwirkende (ODbL), Kartenstil Protomaps,
Natural Earth (gemeinfrei), Wetter Open-Meteo (CC BY 4.0, nicht-kommerziell),
Flugdaten adsb.lol (ODbL), Ortsverzeichnis und Länderdaten GeoNames (CC BY 4.0), Kennzahlen Weltbank (CC BY 4.0),
Gemeindeverzeichnis Statistisches Bundesamt (Datenlizenz Deutschland 2.0), Wikidata (CC0),
Seekabel © TeleGeography Submarine Cable Map (CC BY-NC-SA 3.0, nur nicht-kommerziell), Internetknoten und
Rechenzentren PeeringDB, Radar Deutscher Wetterdienst (Geonutzungsverordnung, Quellenvermerk),
Satellitenbilder © EUMETSAT, Höhendaten © Mapterhorn (Quellen: mapterhorn.com/attribution).
Nachrichten-Feeds gehören den jeweiligen Anbietern.
