"""KI-Lagebericht (optional): fasst die gespeicherte Lage in einem kurzen Text zusammen.

Ganz ohne Einrichtung bleibt die Funktion aus. Unter Einstellungen wählbar:
  - Ollama: ein lokal (oder im Netz) laufendes Sprachmodell - keine Daten verlassen das eigene Netz
  - OpenAI-kompatibel: OpenAI, Groq, OpenRouter, Mistral, LM Studio … (Adresse, Schlüssel, Modell)
  - Anthropic (Claude) oder Google Gemini mit eigenem API-Schlüssel
An das Modell geht nur eine knappe Liste der aktuellen Meldungen (Titel, Ort, Stufe, Zeit) - keine
Einstellungen, Schlüssel oder Standortdaten des Geräts.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from . import risk

PROVIDERS = {
    "ollama": {"label": "Ollama (lokales Modell)", "url": "http://localhost:11434", "model": "", "key": False},
    "openai": {"label": "OpenAI-kompatibel (OpenAI, Groq, OpenRouter, Mistral, LM Studio …)",
               "url": "https://api.openai.com/v1", "model": "gpt-4o-mini", "key": True},
    "anthropic": {"label": "Anthropic (Claude)", "url": "https://api.anthropic.com", "model": "claude-haiku-5-5",
                  "key": True},
    "gemini": {"label": "Google Gemini", "url": "https://generativelanguage.googleapis.com",
               "model": "gemini-2.5-flash", "key": True},
}
SEVERITY = ["Info", "gering", "mittel", "hoch", "kritisch"]
INSTRUCTION = (
    "Du bist ein nüchterner Lageanalyst. Schreibe aus den folgenden Meldungen einen kurzen Lagebericht auf Deutsch "
    "(höchstens 250 Wörter) mit den Abschnitten „Weltlage“, „Deutschland“, „Wetter und Umwelt“ und „Worauf achten“. "
    "Nutze nur die Angaben unten, erfinde nichts, nenne keine Quellen-Links. Fasse Gleichartiges zusammen, das "
    "Wichtigste zuerst. Gibt es zu einem Abschnitt nichts Nennenswertes, schreibe das kurz. Keine Einleitung, "
    "kein Fazit, schlichter Text mit den Abschnittsüberschriften.")


def _when(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%d.%m. %H:%M UTC")


def facts(con, now: int, geo=None) -> str:
    """Die Lage der letzten 24 Stunden als knappe Liste (Grundlage für das Modell)."""
    names = geo.country_names if geo else {}
    lines = [f"Stand: {_when(now)}", "", "Meldungen der letzten 24 Stunden (Stufe, Art, Titel, Ort):"]
    rows = con.execute(
        "SELECT severity, category, title, place, country, ts FROM events WHERE category != 'conflict' "
        "AND ts >= ? AND (ended IS NULL OR ended >= ?) ORDER BY severity DESC, ts DESC LIMIT 45",
        (now - 86400, now - 86400)).fetchall()
    for severity, category, title, place, country, ts in rows:
        where = ", ".join(x for x in (place, country) if x)
        lines.append(f"- [{SEVERITY[severity or 0]}] {category}: {title}" + (f" ({where})" if where else ""))
    conflicts = con.execute(
        "SELECT severity, title, country, COUNT(*) FROM events WHERE category = 'conflict' AND ts >= ? "
        "GROUP BY country, kind ORDER BY MAX(severity) DESC, COUNT(*) DESC LIMIT 15", (now - 2 * 86400,)).fetchall()
    if conflicts:
        lines += ["", "Konfliktereignisse der letzten 48 Stunden (automatisch erkannt, je Land und Art):"]
        lines += [f"- {count}× {title.split(':')[0]} in {(names or {}).get(country, country or '?')} "
                  f"(höchste Stufe {SEVERITY[sev or 0]})"
                  for sev, title, country, count in conflicts]
    scores = risk.country_scores(con, now)[:8]
    if scores:
        lines += ["", "Länder mit dem höchsten Risiko-Index (0-100, letzte 7 Tage):"]
        lines += [f"- {(names or {}).get(s['iso'], s['iso'])}: {round(s['score'])}" for s in scores]
    red = con.execute("SELECT name, value, baseline, lat, lon FROM radiation WHERE status = 3 "
                      "ORDER BY value DESC").fetchall()
    rising, steady = {}, {}
    for name, value, baseline, lat, lon in red:
        # mit Land - sonst rät das Modell; dauerhaft hohe Orte (Sperrzone Tschernobyl) getrennt von echten Anstiegen
        where = names.get(geo.country_at(lat, lon), "") if geo else ""
        target = rising if baseline and value >= 2 * baseline else steady
        target.setdefault(f"{name}{f' ({where})' if where else ''}", value)
    if rising:
        lines += ["", "Strahlung plötzlich deutlich angestiegen (mindestens doppelt so hoch wie üblich): "
                  + ", ".join(f"{n}: {v:.2f} µSv/h" for n, v in list(rising.items())[:10])]
    if steady:
        lines += ["", "Strahlung dauerhaft erhöht, aber unverändert (z. B. Sperrzone Tschernobyl – kein neues Ereignis, "
                  "nicht erwähnen): " + ", ".join(list(steady)[:6])]
    return "\n".join(lines)


def _post(http, url: str, **kwargs) -> dict:
    r = http.post(url, timeout=300, **kwargs)
    if r.status_code in (401, 403):
        raise RuntimeError("Zugang abgelehnt – API-Schlüssel prüfen")
    if r.status_code == 404:
        raise RuntimeError("Modell oder Adresse nicht gefunden")
    r.raise_for_status()
    return r.json()


def generate(ai: dict, prompt: str, http: httpx.Client) -> str:
    provider, url, model, key = ai.get("provider"), ai.get("url", "").rstrip("/"), ai.get("model", ""), ai.get("key", "")
    if provider == "ollama":
        data = _post(http, f"{url}/api/chat", json={"model": model, "stream": False, "think": False, "messages": [
            {"role": "system", "content": INSTRUCTION}, {"role": "user", "content": prompt}]})
        return data["message"]["content"]
    if provider == "openai":
        data = _post(http, f"{url}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json={
            "model": model, "messages": [{"role": "system", "content": INSTRUCTION}, {"role": "user", "content": prompt}]})
        return data["choices"][0]["message"]["content"]
    if provider == "anthropic":
        data = _post(http, f"{url}/v1/messages", headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, json={
            "model": model, "max_tokens": 1200, "system": INSTRUCTION, "messages": [{"role": "user", "content": prompt}]})
        return "".join(part.get("text", "") for part in data.get("content", []))
    if provider == "gemini":
        data = _post(http, f"{url}/v1beta/models/{model}:generateContent", headers={"x-goog-api-key": key}, json={
            "systemInstruction": {"parts": [{"text": INSTRUCTION}]}, "contents": [{"parts": [{"text": prompt}]}]})
        return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    raise RuntimeError("Kein KI-Dienst eingerichtet")


def models(ai: dict, http: httpx.Client) -> list[str]:
    """Installierte Modelle eines Ollama-Servers (für die Auswahl in den Einstellungen)."""
    r = http.get(f"{ai.get('url', '').rstrip('/')}/api/tags", timeout=10)
    r.raise_for_status()
    return sorted(m["name"] for m in r.json().get("models", []))


def _strip_thinking(text: str) -> str:
    """Überlegungen (<think>…</think>) und Markdown (**fett**, # Überschrift) entfernen - schlichter Text."""
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?m)^#+\s*", "", text)
    return text.strip()


class BriefJob:
    """Ein Bericht wird im Hintergrund erstellt (ein kleines Modell auf dem Pi braucht Minuten)."""

    def __init__(self, data_dir: Path):
        self.path = data_dir / "brief.json"
        try:
            self.last = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.last = None
        self.state = {"state": "idle", "error": ""}
        self._thread: threading.Thread | None = None

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, ai: dict, connect, geo=None) -> None:
        if self.running():
            return
        self.state = {"state": "running", "error": "", "started": int(time.time())}
        self._thread = threading.Thread(target=self._run, args=(ai, connect, geo), daemon=True)
        self._thread.start()

    def _run(self, ai: dict, connect, geo) -> None:
        try:
            con = connect()
            try:
                prompt = facts(con, int(time.time()), geo)
            finally:
                con.close()
            with httpx.Client(follow_redirects=True) as http:
                text = _strip_thinking(generate(ai, prompt, http))
            if not text:
                raise RuntimeError("Das Modell hat keinen Text geliefert")
            self.last = {"text": text, "created": int(time.time()), "provider": ai.get("provider"),
                         "model": ai.get("model")}
            self.path.write_text(json.dumps(self.last, ensure_ascii=False), encoding="utf-8")
            self.state = {"state": "done", "error": ""}
        except httpx.ConnectError:
            self.state = {"state": "error", "error": "KI-Dienst nicht erreichbar (Adresse prüfen, läuft Ollama?)"}
        except httpx.TimeoutException:
            self.state = {"state": "error", "error": "Zeitüberschreitung – das Modell braucht zu lange"}
        except Exception as e:  # noqa: BLE001 - Grund in die Oberfläche
            self.state = {"state": "error", "error": str(e)[:200] or type(e).__name__}
