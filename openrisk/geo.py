"""Offline-Geodaten: Ortsverzeichnis für Schlagzeilen und Punkt-in-Land-Test."""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

TOKEN = re.compile(r"[^\W\d_]+")


@dataclass(frozen=True)
class Location:
    name: str
    lat: float
    lon: float
    iso: str | None
    specific: bool  # Stadt/Region (True) oder nur Land (False)


# Regionen und Meerengen, die in Risikomeldungen häufig vorkommen
REGIONS = [
    (["Gaza", "Gazastreifen", "Gaza Strip", "Hamas"], "Gaza", 31.42, 34.38, "PSX"),
    (["Westjordanland", "West Bank"], "Westjordanland", 31.95, 35.25, "PSX"),
    (["Krim", "Crimea"], "Krim", 45.3, 34.4, "UKR"),
    (["Donbass", "Donbas", "Donezk", "Donetsk"], "Donbass", 48.0, 37.8, "UKR"),
    (["Golanhöhen", "Golan Heights", "Golan"], "Golanhöhen", 33.0, 35.75, "SYR"),
    (["Kaschmir", "Kashmir"], "Kaschmir", 34.1, 74.8, "IND"),
    (["Darfur"], "Darfur", 13.5, 24.5, "SDN"),
    (["Tigray"], "Tigray", 13.5, 39.5, "ETH"),
    (["Rotes Meer", "Roten Meer", "Red Sea"], "Rotes Meer", 19.0, 39.5, None),
    (["Straße von Hormus", "Strait of Hormuz", "Hormus", "Hormuz"], "Straße von Hormus", 26.57, 56.25, None),
    (["Taiwanstraße", "Taiwan Strait"], "Taiwanstraße", 24.5, 119.5, None),
    (["Südchinesisches Meer", "Südchinesischen Meer", "South China Sea"], "Südchinesisches Meer", 13.0, 114.0, None),
    (["Schwarzes Meer", "Schwarzen Meer", "Black Sea"], "Schwarzes Meer", 43.4, 34.3, None),
    (["Ostsee", "Baltic Sea"], "Ostsee", 58.0, 20.0, None),
    (["Suezkanal", "Suez Canal"], "Suezkanal", 30.6, 32.33, "EGY"),
    (["Panamakanal", "Panama Canal"], "Panamakanal", 9.1, -79.7, "PAN"),
    (["Persischer Golf", "Persischen Golf", "Persian Gulf"], "Persischer Golf", 26.9, 51.5, None),
    (["Sahel", "Sahelzone"], "Sahel", 15.0, 2.0, None),
]

# Weitere Namen für Länder (ISO-3 -> exakte Begriffe)
ALIASES = {
    "USA": ["USA", "US", "U S", "United States", "Vereinigte Staaten", "Vereinigten Staaten", "American",
            "Americans", "Pentagon", "White House", "Weißes Haus", "Weißen Haus"],
    "GBR": ["UK", "Britain", "British", "Großbritannien", "Grossbritannien", "Briten"],
    "RUS": ["Russian", "Russians", "Kreml", "Kremlin", "Russen"],
    "UKR": ["Ukrainian", "Ukrainians", "Ukrainer"],
    "ISR": ["Israeli", "Israelis"],
    "PSX": ["Palestinian", "Palestinians", "Palästinenser", "Palästina", "Palestine"],
    "IRN": ["Iranian", "Iranians", "Iraner"],
    "IRQ": ["Iraqi", "Iraqis"],
    "CHN": ["Chinese", "Chinesen"],
    "TWN": ["Taiwanese", "Taiwaner"],
    "PRK": ["North Korea", "North Korean", "Nordkorea"],
    "KOR": ["South Korea", "South Korean", "Südkorea"],
    "SYR": ["Syrian", "Syrians", "Syrer"],
    "LBN": ["Lebanese", "Hisbollah", "Hezbollah", "Libanesen"],
    "YEM": ["Yemeni", "Houthi", "Houthis", "Huthi", "Huthis"],
    "AFG": ["Afghan", "Afghans", "Taliban", "Afghanen"],
    "TUR": ["Turkish", "Turkey", "Türkei", "Türkiye", "Türken"],
    "DEU": ["German", "Germans", "Bundeswehr", "Bundesregierung"],
    "FRA": ["French", "Franzosen"],
    "IND": ["Indian", "Inder"],
    "PAK": ["Pakistani", "Pakistanis"],
    "SAU": ["Saudi", "Saudis"],
    "EGY": ["Egyptian", "Egyptians", "Ägypter"],
    "SDN": ["Sudanese", "Sudanesen"],
    "VEN": ["Venezuelan", "Venezuelans"],
    "JPN": ["Japanese", "Japaner"],
    "POL": ["Polish", "Poles", "Polen"],
    "COD": ["DRC", "DR Congo", "DR Kongo", "Demokratische Republik Kongo"],
    "MMR": ["Burma", "Birma"],
    "ARE": ["UAE", "VAE", "Emirates", "Emirate"],
}

# Deutsche Adjektive werden gebeugt (russische, russischen, ...) -> Wortstamm
ADJECTIVE_STEMS = {
    "russisch": "RUS", "ukrainisch": "UKR", "israelisch": "ISR", "iranisch": "IRN", "irakisch": "IRQ",
    "chinesisch": "CHN", "amerikanisch": "USA", "britisch": "GBR", "französisch": "FRA", "türkisch": "TUR",
    "syrisch": "SYR", "libanesisch": "LBN", "indisch": "IND", "pakistanisch": "PAK", "afghanisch": "AFG",
    "nordkoreanisch": "PRK", "südkoreanisch": "KOR", "saudisch": "SAU", "ägyptisch": "EGY", "polnisch": "POL",
    "japanisch": "JPN", "taiwanisch": "TWN", "sudanesisch": "SDN", "jemenitisch": "YEM",
    "palästinensisch": "PSX", "venezolanisch": "VEN", "belarussisch": "BLR", "georgisch": "GEO",
    "serbisch": "SRB", "armenisch": "ARM", "äthiopisch": "ETH", "nigerianisch": "NGA", "kubanisch": "CUB",
}

# Ortsnamen, die zugleich gewöhnliche Wörter oder Personennamen sind
AMBIGUOUS = {
    "Nice", "Bar", "Male", "Mobile", "Reading", "Split", "Essen", "Worms", "Hof", "Bath", "Hull", "Halle",
    "Victoria", "George", "Orange", "Buffalo", "Phoenix", "Jackson", "Columbia", "Columbus", "Lincoln",
    "Troy", "Sale", "Salt", "Moss", "Mary", "Same", "Deal", "Hamilton", "Santiago", "Santa", "Georgetown",
    "Kingston", "Richmond", "Regina", "Nelson", "Charlotte", "Austin", "Madison",
    "Trinidad", "Laredo", "Natal", "Apia", "Bari", "Orel", "Spa", "Wels", "Most", "Sur",
    "Salem", "Monroe", "Savannah", "Augusta", "Aurora", "Concord", "David", "Florence", "Carolina",
    "Santos", "Man", "Goes", "Best", "Zug", "Ulm", "Eden", "Victor", "Douglas", "Alexander",
    "Windsor", "Roma", "Palmer", "Springfield", "Newcastle", "Valencia", "Cordoba", "Córdoba",
}

PLACE_MIN_POP = 100_000
SPECIFIC_MIN_POP = 300_000


class Geo:
    def __init__(self, geo_dir: Path):
        self.index: dict[str, Location] = {}
        self.max_words = 1
        self.country_names: dict[str, str] = {}
        self.countries: dict[str, Location] = {}
        self.cities: list[dict] = []
        self.stems: dict[str, Location] = {}
        self.polygons: list[tuple[str, tuple[float, float, float, float], list]] = []

        gaz_file = geo_dir / "gazetteer.json"
        if gaz_file.exists():
            self._load_gazetteer(json.loads(gaz_file.read_text(encoding="utf-8")))
        countries_file = geo_dir / "countries.geojson"
        if countries_file.exists():
            self._load_polygons(json.loads(countries_file.read_text(encoding="utf-8")))

    # -- Ortsverzeichnis ------------------------------------------------------
    def _add(self, name: str, loc: Location, overwrite: bool = True) -> None:
        key = " ".join(TOKEN.findall(name))
        if not key or (not overwrite and key in self.index):
            return
        self.index[key] = loc
        self.max_words = max(self.max_words, key.count(" ") + 1)

    def _load_gazetteer(self, gaz: dict) -> None:
        by_iso: dict[str, Location] = {}
        places = sorted(gaz["places"], key=lambda p: -p["pop"])
        self.cities = places
        for p in reversed(places):  # größte Stadt gewinnt bei Namensgleichheit
            if p["pop"] < PLACE_MIN_POP and not p["capital"]:
                continue
            loc = Location(p["name"], p["lat"], p["lon"], p["iso"], p["capital"] or p["pop"] >= SPECIFIC_MIN_POP)
            for name in p["names"]:
                if name not in AMBIGUOUS and len(name) >= 4:
                    self._add(name, loc)
        for c in gaz["countries"]:
            loc = Location(c["name"], c["lat"], c["lon"], c["iso"], False)
            by_iso[c["iso"]] = self.countries[c["iso"]] = loc
            self.country_names[c["iso"]] = c["name"]
            for name in c["names"]:
                if len(name) >= 4:
                    self._add(name, loc)
        for iso, names in ALIASES.items():
            if iso in by_iso:
                for name in names:
                    self._add(name, by_iso[iso])
        for names, label, lat, lon, iso in REGIONS:
            for name in names:
                self._add(name, Location(label, lat, lon, iso, True))
        self.stems = {stem: by_iso[iso] for stem, iso in ADJECTIVE_STEMS.items() if iso in by_iso}

    def _matches(self, text: str) -> list[Location]:
        tokens = TOKEN.findall(text)
        found: list[Location] = []
        i = 0
        while i < len(tokens):
            hit = None
            for n in range(min(self.max_words, len(tokens) - i), 0, -1):
                hit = self.index.get(" ".join(tokens[i:i + n]))
                if hit:
                    i += n
                    break
            if not hit:
                tok = tokens[i]
                if len(tok) > 4 and tok.endswith("s"):  # deutscher Genitiv: "Russlands"
                    hit = self.index.get(tok[:-1])
                if not hit:
                    low = tok.lower()
                    hit = next((loc for stem, loc in self.stems.items() if low.startswith(stem)), None)
                i += 1
            if hit:
                found.append(hit)
        return found

    def locate(self, title: str, summary: str = "") -> Location | None:
        """Wahrscheinlichsten Ort einer Meldung bestimmen (Titel vor Zusammenfassung)."""
        if not self.index:
            return None
        for text in (title, summary):
            matches = self._matches(text or "")
            if matches:
                return next((m for m in matches if m.specific), matches[0])
        return None

    # -- Ländergrenzen --------------------------------------------------------
    def _load_polygons(self, fc: dict) -> None:
        for f in fc["features"]:
            geom = f["geometry"]
            polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
            for poly in polys:
                xs = [p[0] for p in poly[0]]
                ys = [p[1] for p in poly[0]]
                self.polygons.append((f["properties"]["iso"], (min(xs), min(ys), max(xs), max(ys)), poly))
            self.country_names.setdefault(f["properties"]["iso"], f["properties"]["name"])

    def country_at(self, lat: float, lon: float) -> str | None:
        for iso, (x0, y0, x1, y1), poly in self.polygons:
            if x0 <= lon <= x1 and y0 <= lat <= y1 and _in_polygon(lon, lat, poly):
                return iso
        return None


def simplify(pts: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Linienzug vereinfachen (Douglas-Peucker, iterativ); `tol` in Grad."""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = pts[a], pts[b]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy)
        worst, idx = 0.0, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            d = abs(dy * (px - ax) - dx * (py - ay)) / norm if norm else math.hypot(px - ax, py - ay)
            if d > worst:
                worst, idx = d, i
        if worst > tol:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(pts, keep) if k]


def _in_ring(x: float, y: float, ring: list) -> bool:
    inside = False
    x1, y1 = ring[-1]
    for x2, y2 in ring:
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
        x1, y1 = x2, y2
    return inside


def _in_polygon(x: float, y: float, poly: list) -> bool:
    return _in_ring(x, y, poly[0]) and not any(_in_ring(x, y, hole) for hole in poly[1:])
