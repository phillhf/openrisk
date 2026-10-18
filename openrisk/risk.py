"""Risikobewertung: Schlagwort-Einstufung von Meldungen und Länder-Risikoindex.

Stufen: 0 Info, 1 gering, 2 mittel, 3 hoch, 4 kritisch.
Beides sind einfache, nachvollziehbare Heuristiken - keine Prognose.
"""
from __future__ import annotations

import math
import re
import sqlite3
import time

# (Art, Stufe, Wortanfänge) - getrennt nach Sprache, weil z.B. "war" im Deutschen kein Krieg ist
RULES = {
    "de": [
        ("conflict", 4, ["atomangriff", "atomschlag", "kriegserklärung", "einmarsch", "invasion"]),
        ("conflict", 3, ["krieg", "luftangriff", "raketenangriff", "drohnenangriff", "beschuss", "bombardier",
                         "offensive", "gefecht", "kämpfe", "angriff", "getötet", "tote", "toten", "todesopfer", "massaker"]),
        ("terror", 3, ["terror", "anschlag", "attentat", "explosion", "geisel", "amoklauf", "schießerei"]),
        ("disaster", 3, ["erdbeben", "tsunami", "hurrikan", "taifun", "zyklon", "vulkanausbruch"]),
        ("disaster", 2, ["überschwemmung", "hochwasser", "waldbrand", "waldbrände", "erdrutsch", "dürre",
                         "unwetter", "sturm", "stürme", "tornado", "flut", "hitzewelle"]),
        ("military", 2, ["militär", "truppen", "manöver", "kriegsschiff", "kampfjet", "rakete", "drohne",
                         "atomwaffe", "mobilmachung", "aufrüstung", "waffenlieferung"]),
        ("unrest", 2, ["putsch", "unruhen", "ausschreitung", "ausnahmezustand", "kriegsrecht", "protest",
                       "sanktion", "blockade", "aufstand"]),
        ("health", 2, ["epidemie", "pandemie", "seuche", "cholera", "ebola", "mpox", "ausbruch"]),
        ("cyber", 2, ["cyberangriff", "hackerangriff", "ransomware", "hacker"]),
        ("infrastructure", 2, ["pipeline", "stromausfall", "blackout", "sabotage", "ölpest", "öltanker",
                               "havarie", "flugzeugabsturz", "zugunglück"]),
        ("economy", 1, ["rezession", "börsencrash", "inflation", "zölle", "staatspleite", "insolvenz",
                        "ölpreis", "handelskrieg"]),
    ],
    "en": [
        ("conflict", 4, ["nuclear strike", "nuclear attack", "declares war", "declaration of war", "invasion",
                         "invades"]),
        ("conflict", 3, ["war", "wars", "airstrike", "air strike", "missile strike", "missile attack",
                         "drone strike", "drone attack", "shelling", "bombard", "offensive", "clashes",
                         "fighting", "attack", "killed", "dead", "death toll", "massacre", "strikes on"]),
        ("terror", 3, ["terror", "bombing", "explosion", "hostage", "mass shooting", "gunman", "assassinat"]),
        ("disaster", 3, ["earthquake", "tsunami", "hurricane", "typhoon", "cyclone", "eruption"]),
        ("disaster", 2, ["flood", "wildfire", "landslide", "drought", "storm", "tornado", "heatwave"]),
        ("military", 2, ["military", "troops", "warship", "fighter jet", "missile", "drone", "nuclear",
                         "mobilisation", "mobilization", "navy", "airspace"]),
        ("unrest", 2, ["coup", "coups", "riot", "unrest", "state of emergency", "martial law", "protest", "sanction",
                       "blockade", "uprising"]),
        ("health", 2, ["epidemic", "pandemic", "outbreak", "cholera", "ebola", "mpox"]),
        ("cyber", 2, ["cyberattack", "cyber attack", "ransomware", "hacker", "data breach"]),
        ("infrastructure", 2, ["pipeline", "blackout", "power outage", "sabotage", "oil spill", "tanker",
                               "plane crash", "derail"]),
        ("economy", 1, ["recession", "market crash", "inflation", "tariff", "bankruptcy",
                        "oil price", "trade war"]),
    ],
}

# Wörter, die vollständig übereinstimmen müssen (sonst trifft "war" auch "warning")
EXACT = {"war", "wars", "dead", "tote", "toten", "flut", "sturm", "stürme", "coup", "coups", "navy"}


def _compile(rules):
    compiled = []
    for kind, severity, words in rules:
        parts = [re.escape(w) + (r"\b" if w in EXACT else "") for w in words]
        compiled.append((kind, severity, re.compile(r"\b(?:" + "|".join(parts) + ")", re.IGNORECASE)))
    return sorted(compiled, key=lambda rule: -rule[1])  # höchste Stufe zuerst


_COMPILED = {lang: _compile(rules) for lang, rules in RULES.items()}


def classify(text: str, lang: str) -> tuple[str, int]:
    """Art und Stufe einer Meldung; ohne Treffer ('general', 0)."""
    for kind, severity, pattern in _COMPILED.get(lang, _COMPILED["en"]):
        if pattern.search(text):
            return kind, severity
    return "general", 0


WEIGHT = {1: 1.0, 2: 3.0, 3: 8.0, 4: 20.0}
HALF_LIFE_DAYS = 2.0
CONFLICT_SHARE = 0.15
WINDOW_DAYS = 7


def country_scores(con: sqlite3.Connection, at: float | None = None) -> list[dict]:
    """Risikoindex 0..100 je Land aus den Ereignissen der letzten 7 Tage.

    Jedes Ereignis zählt nach Stufe gewichtet und verliert mit dem Alter an
    Gewicht (Halbwertszeit 2 Tage). Die Summe wird gesättigt, damit viele
    Meldungen zum selben Thema den Index nicht beliebig aufblasen.
    """
    now = at or time.time()  # at: Rückblick auf einen früheren Zeitpunkt
    raw: dict[str, float] = {}
    counts: dict[str, int] = {}
    top: dict[str, int] = {}
    # Regionale Warnungen (DWD: eine je Landkreis; NINA: eine je Gemeinde) zählen je Art nur einmal, mit der höchsten Stufe -
    # sonst stünde Deutschland bei jedem Herbststurm an der Spitze.
    rows = con.execute(
        "SELECT country, MAX(severity), MAX(ts), category FROM events "
        "WHERE country IS NOT NULL AND severity > 0 AND ts > ? AND COALESCE(started, ts) <= ? "
        "GROUP BY CASE WHEN id LIKE 'dwd:%' OR id LIKE 'nina:%' THEN substr(id, 1, 4) || kind ELSE id END",
        (now - WINDOW_DAYS * 86400, now))
    for country, severity, ts, category in rows:
        age_days = max(0.0, (now - ts) / 86400)
        # Konfliktdaten (GDELT) zählen jedes Ereignis einzeln; eine Nachricht fasst dagegen viele zusammen
        share = CONFLICT_SHARE if category == "conflict" else 1.0
        raw[country] = raw.get(country, 0.0) + share * WEIGHT[severity] * 0.5 ** (age_days / HALF_LIFE_DAYS)
        counts[country] = counts.get(country, 0) + 1
        top[country] = max(top.get(country, 0), severity)
    scores = [{"iso": iso, "score": round(100 * (1 - math.exp(-value / 40)), 1),
               "events": counts[iso], "max_severity": top[iso]} for iso, value in raw.items()]
    return sorted(scores, key=lambda s: -s["score"])
