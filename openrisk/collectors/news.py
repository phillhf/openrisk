"""Nachrichten aus RSS-/Atom-Feeds, verortet über das Offline-Ortsverzeichnis."""
from __future__ import annotations

import hashlib
import html
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from .. import db, risk

MAX_BYTES = 5_000_000
MAX_AGE = 7 * 86400
TAG = re.compile(r"<[^>]+>")
SPACE = re.compile(r"\s+")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _clean(text: str | None, limit: int) -> str:
    text = SPACE.sub(" ", html.unescape(TAG.sub(" ", html.unescape(text or "")))).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _timestamp(text: str | None) -> int | None:
    if not text:
        return None
    text = text.strip()
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def parse_feed(xml: bytes) -> list[dict]:
    """RSS 2.0, RSS 1.0 (RDF) und Atom auf title/link/summary/ts abbilden."""
    items = []
    for el in ET.fromstring(xml).iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        item: dict = {}
        for child in el:
            name = _local(child.tag)
            if name == "title":
                item["title"] = child.text
            elif name == "link":
                href = child.get("href") or child.text
                if href and child.get("rel", "alternate") == "alternate":
                    item.setdefault("link", href.strip())
            elif name in ("description", "summary"):
                item.setdefault("summary", child.text)
            elif name in ("pubdate", "published", "updated", "date"):
                item.setdefault("ts", _timestamp(child.text))
        if item.get("title"):
            items.append(item)
    return items


def collect(ctx) -> int:
    now = int(time.time())
    events, failed, last_error = [], 0, None
    for feed in ctx.cfg["feeds"]:
        try:
            r = ctx.http.get(feed["url"])
            r.raise_for_status()
            items = parse_feed(r.content[:MAX_BYTES])
        except Exception as e:  # ein kaputter Feed soll die anderen nicht aufhalten
            failed, last_error = failed + 1, e
            continue
        lang = feed.get("lang", "en")
        for item in items:
            ts = min(item.get("ts") or now, now)
            if ts < now - MAX_AGE:
                continue
            title = _clean(item["title"], 300)
            summary = _clean(item.get("summary"), 400)
            link = item.get("link") or ""
            if not link.startswith(("http://", "https://")):
                link = ""
            kind, severity = risk.classify(title, lang)
            if not severity:  # Treffer nur im Anreißer sind unsicherer -> eine Stufe niedriger
                kind, severity = risk.classify(summary, lang)
                severity = max(1, severity - 1) if severity else 0
            loc = ctx.geo.locate(title, summary)
            events.append({
                "id": "news:" + hashlib.sha1((link or title).encode()).hexdigest()[:16],
                "source": feed["name"], "category": "news", "kind": kind,
                "title": title, "summary": summary, "url": link,
                "lat": loc.lat if loc else None, "lon": loc.lon if loc else None,
                "place": loc.name if loc else None, "country": loc.iso if loc else None,
                "severity": severity, "ts": ts,
            })
    if last_error and failed == len(ctx.cfg["feeds"]):
        raise last_error
    db.upsert_events(ctx.db, events)
    return len(events)
