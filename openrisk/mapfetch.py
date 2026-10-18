"""Offline-Weltkarte laden, prüfen und löschen.

Schneidet aus dem tagesaktuellen Protomaps-Planet-Build (OpenStreetMap, ~138 GB)
per HTTP-Range-Requests nur die Zoomstufen 0..N heraus und schreibt sie als eine
PMTiles-Datei. Verzeichnisse werden in kompakten Arrays gehalten und die Kacheln
direkt in die Zieldatei gestreamt, damit auch Stufe 11 (~8 GB, Millionen Kacheln)
auf einem Raspberry Pi mit 4 GB RAM durchläuft.
"""
from __future__ import annotations

import gzip
import math
import shutil
import threading
import time
from array import array
from bisect import bisect_left
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from typing import Callable

import httpx
from pmtiles.tile import Compression, deserialize_directory, deserialize_header, serialize_header, zxy_to_tileid

BUILDS_URL = "https://build-metadata.protomaps.dev/builds.json"
BUILD_BASE = "https://build.protomaps.com/"
# Höhendaten weltweit (Terrarium-Kodierung, WebP, 512 px, bis Stufe 12 ≈ 20 m); für Schummerung und Höhenlinien
TERRAIN_URL = "https://download.mapterhorn.com/planet.pmtiles"
TERRAIN_WORLD_ZOOM = 5  # ganze Welt grob: rund 80 MB
TERRAIN_ZOOM = 12
CHUNK = 8 * 1024 * 1024  # max. Größe eines Range-Requests
GAP = 64 * 1024  # Lücken bis zu dieser Größe werden mitgeladen statt neu angefragt
ROOT_BUDGET = 16384 - 127  # Kopf + Wurzelverzeichnis müssen in die ersten 16 KiB passen
RESERVE = 50 * 1024 * 1024  # freier Speicher, der nach dem Download übrig bleiben muss


class Cancelled(Exception):
    pass


class MapError(Exception):
    """Fehler mit einer Meldung, die der Nutzer direkt lesen kann."""


def info(path: Path) -> dict | None:
    """Größe und Zoomstufen einer vorhandenen Kartendatei."""
    try:
        with open(path, "rb") as f:
            header = deserialize_header(f.read(127))
        return {"bytes": path.stat().st_size, "min_zoom": header["min_zoom"], "max_zoom": header["max_zoom"]}
    except Exception:
        return None


def regions(folder: Path, names: dict) -> list[dict]:
    """Heruntergeladene Länder (data/map/regions/<ISO3>.pmtiles)."""
    out = []
    for path in sorted(folder.glob("*.pmtiles")):
        found = info(path)
        if found:
            out.append({"iso": path.stem, "name": names.get(path.stem, path.stem), **found})
    return sorted(out, key=lambda r: r["name"])


def delete(path: Path) -> None:
    for attempt in range(5):  # unter Windows kann gerade noch eine Kachel ausgeliefert werden
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(0.5)
    raise MapError("Kartendatei ist in Benutzung – bitte erneut versuchen.")


def _get_range(client: httpx.Client, url: str, start: int, length: int, halted=None) -> bytes:
    last = None
    for attempt in range(5):
        if halted and halted():
            raise Cancelled()
        try:
            r = client.get(url, headers={"Range": f"bytes={start}-{start + length - 1}"})
            r.raise_for_status()
            if len(r.content) != length:
                raise IOError(f"erwartet {length} Bytes, erhalten {len(r.content)}")
            return r.content
        except (httpx.HTTPError, IOError) as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise MapError(f"Download fehlgeschlagen: {type(last).__name__}: {last}")


def _varint(buf: bytearray, value: int) -> None:
    while value >= 0x80:
        buf.append((value & 0x7F) | 0x80)
        value >>= 7
    buf.append(value)


class _Directory:
    """Kachelverzeichnis als vier parallele Arrays (tile_id aufsteigend)."""

    def __init__(self):
        self.ids, self.offsets, self.lengths, self.runs = array("Q"), array("Q"), array("Q"), array("Q")

    def __len__(self) -> int:
        return len(self.ids)

    def add(self, tile_id: int, offset: int, length: int, run: int) -> None:
        self.ids.append(tile_id)
        self.offsets.append(offset)
        self.lengths.append(length)
        self.runs.append(run)

    def serialize(self, lo: int, hi: int) -> bytes:
        buf = bytearray()
        _varint(buf, hi - lo)
        last = 0
        for i in range(lo, hi):
            _varint(buf, self.ids[i] - last)
            last = self.ids[i]
        for i in range(lo, hi):
            _varint(buf, self.runs[i])
        for i in range(lo, hi):
            _varint(buf, self.lengths[i])
        for i in range(lo, hi):
            contiguous = i > lo and self.offsets[i] == self.offsets[i - 1] + self.lengths[i - 1]
            _varint(buf, 0 if contiguous else self.offsets[i] + 1)
        return gzip.compress(bytes(buf), mtime=0)

    def build(self) -> tuple[bytes, bytes]:
        """Wurzel- und Blattverzeichnisse; die Wurzel muss in ROOT_BUDGET passen."""
        root = self.serialize(0, len(self))
        if len(root) <= ROOT_BUDGET:
            return root, b""
        leaf_size = 4096
        while True:
            pointers, leaves = _Directory(), bytearray()
            for lo in range(0, len(self), leaf_size):
                leaf = self.serialize(lo, min(lo + leaf_size, len(self)))
                pointers.add(self.ids[lo], len(leaves), len(leaf), 0)
                leaves += leaf
            root = pointers.serialize(0, len(pointers))
            if len(root) <= ROOT_BUDGET:
                return root, bytes(leaves)
            leaf_size *= 2


def _read_directory(client, url: str, header: dict, root: bytes, max_id: int, cancel) -> _Directory:
    """Alle Einträge mit tile_id < max_id aus Wurzel- und Blattverzeichnissen."""
    gz = header["internal_compression"] == Compression.GZIP
    parse = lambda buf: deserialize_directory(buf if gz else gzip.compress(buf))
    directory = _Directory()

    def take(entries) -> None:
        for e in entries:
            if e.tile_id >= max_id:
                return
            if e.run_length == 0:
                if cancel and cancel.is_set():
                    raise Cancelled()
                take(parse(_get_range(client, url, header["leaf_directory_offset"] + e.offset, e.length)))
            else:  # letzter Lauf darf nicht in die nächste Zoomstufe reichen
                directory.add(e.tile_id, e.offset, e.length, min(e.run_length, max_id - e.tile_id))

    take(parse(root))
    return directory


def latest_build(client: httpx.Client) -> str:
    builds = client.get(BUILDS_URL).raise_for_status().json()
    return BUILD_BASE + max(b["key"] for b in builds)


def _open_source(client: httpx.Client, url: str | None) -> tuple[str, dict, bytes]:
    try:
        url = url or latest_build(client)
    except httpx.HTTPError as e:
        raise MapError(f"Kartenserver nicht erreichbar: {type(e).__name__}") from e
    head = _get_range(client, url, 0, 16384)
    header = deserialize_header(head[:127])
    if not header["clustered"]:
        raise MapError("Quelle ist nicht nach Kachel-ID sortiert (nicht unterstützt).")
    return url, header, head[header["root_offset"]: header["root_offset"] + header["root_length"]]


def _client() -> httpx.Client:
    # hängende Verbindungen schnell erkennen und neu anfragen
    return httpx.Client(timeout=httpx.Timeout(30, connect=15), follow_redirects=True,
                        headers={"User-Agent": "OpenRisk/0.1"})


def extract(out: Path, maxzoom: int, url: str | None = None, threads: int = 6, dry_run: bool = False,
            progress: Callable[[int, int], None] | None = None, cancel: threading.Event | None = None) -> dict:
    """Ganze Welt, Zoomstufen 0..maxzoom nach `out` schreiben. Liefert Größe je Zoomstufe und Gesamtgröße."""
    with _client() as client:
        url, header, root = _open_source(client, url)
        maxzoom = min(maxzoom, header["max_zoom"])
        src = _read_directory(client, url, header, root, zxy_to_tileid(maxzoom + 1, 0, 0), cancel)

        # Einzigartige Kacheln: in einer sortierten Quelle liegt jede neue Kachel hinter allen
        # bisherigen; kleinere Offsets sind Verweise auf bereits gesehene (z. B. leeres Meer).
        blob_offsets, blob_lengths, new_offsets = array("Q"), array("Q"), array("Q")
        zoom_sizes, zoom, high, total = [], header["min_zoom"], -1, 0
        limit = zxy_to_tileid(zoom + 1, 0, 0)
        for i in range(len(src)):
            while src.ids[i] >= limit:
                zoom_sizes.append((zoom, total))
                zoom += 1
                limit = zxy_to_tileid(zoom + 1, 0, 0)
            if src.offsets[i] > high:
                high = src.offsets[i]
                blob_offsets.append(high)
                blob_lengths.append(src.lengths[i])
                new_offsets.append(total)
                total += src.lengths[i]
        zoom_sizes.append((zoom, total))
        result = {"url": url, "max_zoom": maxzoom, "bytes": total, "tiles": len(blob_offsets), "zoom_sizes": zoom_sizes}
        if dry_run:
            return result
        _write(out, client, url, header, src, blob_offsets, blob_lengths, new_offsets, total,
               {"min_zoom": header["min_zoom"], "max_zoom": maxzoom}, threads, progress, cancel)
        return result


# ---------------------------------------------------------------- einzelne Länder

PROBE_ZOOM = 10  # Abdeckung eines Landes wird auf dieser Stufe bestimmt (Kachel ~40 km), darüber nur Unterkacheln


def _tile_of(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 1 << z
    lat = max(-85.05, min(85.05, lat))
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def _tile_bounds(x: int, y: int, z: int) -> tuple[float, float, float, float]:
    n = 1 << z
    lat = lambda ty: math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * ty / n))))  # noqa: E731
    return x / n * 360 - 180, lat(y + 1), (x + 1) / n * 360 - 180, lat(y)


def _tile_hits(x: int, y: int, poly: list) -> bool:
    from .geo import _in_polygon
    w, s, e, n = _tile_bounds(x, y, PROBE_ZOOM)
    if any(_in_polygon(px, py, poly) for px, py in ((w, s), (w, n), (e, s), (e, n), ((w + e) / 2, (s + n) / 2))):
        return True
    return any(w <= px <= e and s <= py <= n for px, py in poly[0])


def region_tiles(polygons: list, maxzoom: int) -> array:
    """Kachel-IDs (aufsteigend) aller Kacheln 0..maxzoom, die die Polygone berühren."""
    covered: set[tuple[int, int]] = set()
    for poly in polygons:
        xs, ys = [p[0] for p in poly[0]], [p[1] for p in poly[0]]
        x0, y0 = _tile_of(min(xs), max(ys), PROBE_ZOOM)
        x1, y1 = _tile_of(max(xs), min(ys), PROBE_ZOOM)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                if (x, y) not in covered and _tile_hits(x, y, poly):
                    covered.add((x, y))
    ids = array("Q")
    for z in range(maxzoom + 1):
        if z <= PROBE_ZOOM:
            shift = PROBE_ZOOM - z
            tiles = {(x >> shift, y >> shift) for x, y in covered}
        else:
            shift, k = z - PROBE_ZOOM, 1 << (z - PROBE_ZOOM)
            tiles = [((x << shift) + dx, (y << shift) + dy) for x, y in covered for dx in range(k) for dy in range(k)]
        ids.extend(sorted(zxy_to_tileid(z, x, y) for x, y in tiles))  # IDs steigen mit der Zoomstufe
    return ids


def _lookup(client, url: str, header: dict, root: bytes, wanted: array, threads: int, cancel) -> list:
    """(tile_id, Offset, Länge) der gewünschten Kacheln; lädt nur die Blattverzeichnisse, die sie enthalten."""
    gz = header["internal_compression"] == Compression.GZIP
    parse = lambda buf: deserialize_directory(buf if gz else gzip.compress(buf))  # noqa: E731
    has = lambda lo, hi: bisect_left(wanted, lo) < bisect_left(wanted, hi)  # noqa: E731
    found: list = []
    level = [(parse(root), 1 << 64)]
    with ThreadPoolExecutor(threads) as pool:
        while level:
            leaves = []
            for entries, bound in level:
                for i, e in enumerate(entries):
                    end = entries[i + 1].tile_id if i + 1 < len(entries) else bound
                    if e.run_length == 0:
                        if has(e.tile_id, end):
                            leaves.append((e, end))
                    else:
                        lo = bisect_left(wanted, e.tile_id)
                        hi = bisect_left(wanted, e.tile_id + e.run_length)
                        found += [(wanted[j], e.offset, e.length) for j in range(lo, hi)]
            if cancel and cancel.is_set():
                raise Cancelled()
            fetch = lambda item: parse(_get_range(  # noqa: E731
                client, url, header["leaf_directory_offset"] + item[0].offset, item[0].length))
            level = [(entries, end) for entries, (_, end) in zip(pool.map(fetch, leaves), leaves)]
    found.sort()
    return found


def extract_region(out: Path, polygons: list, maxzoom: int = 15, url: str | None = None, threads: int = 8,
                   progress: Callable[[int, int], None] | None = None, cancel: threading.Event | None = None,
                   status: Callable[[str], None] | None = None) -> dict:
    """Ein Land (Polygone) in allen Zoomstufen 0..maxzoom nach `out` schreiben."""
    say = status or (lambda text: None)
    with _client() as client:
        url, header, root = _open_source(client, url)
        maxzoom = min(maxzoom, header["max_zoom"])
        say("Kacheln des Landes werden bestimmt …")
        wanted = region_tiles(polygons, maxzoom)
        say(f"Verzeichnis wird gelesen ({len(wanted):,} Kacheln) …".replace(",", "."))
        found = _lookup(client, url, header, root, wanted, threads, cancel)

        src = _Directory()  # gleiche Nachbarn mit gleichem Inhalt (Meer, Wald) zu Läufen zusammenfassen
        for tile_id, offset, length in found:
            if len(src) and src.ids[-1] + src.runs[-1] == tile_id and src.offsets[-1] == offset:
                src.runs[-1] += 1
            else:
                src.add(tile_id, offset, length, 1)
        blobs = sorted({(offset, length) for _, offset, length in found})
        del found
        blob_offsets, blob_lengths, new_offsets, total = array("Q"), array("Q"), array("Q"), 0
        for offset, length in blobs:
            blob_offsets.append(offset)
            blob_lengths.append(length)
            new_offsets.append(total)
            total += length
        del blobs
        xs = [p[0] for poly in polygons for p in poly[0]]
        ys = [p[1] for poly in polygons for p in poly[0]]
        bounds = {"min_zoom": 0, "max_zoom": maxzoom,
                  "min_lon_e7": int(min(xs) * 1e7), "max_lon_e7": int(max(xs) * 1e7),
                  "min_lat_e7": int(min(ys) * 1e7), "max_lat_e7": int(max(ys) * 1e7),
                  "center_lon_e7": int((min(xs) + max(xs)) / 2 * 1e7),
                  "center_lat_e7": int((min(ys) + max(ys)) / 2 * 1e7)}
        say("")
        _write(out, client, url, header, src, blob_offsets, blob_lengths, new_offsets, total, bounds, threads,
               progress, cancel)
        return {"url": url, "max_zoom": maxzoom, "bytes": total, "tiles": len(blob_offsets)}


def _write(out: Path, client, url: str, header: dict, src: _Directory, blob_offsets: array, blob_lengths: array,
           new_offsets: array, total: int, overrides: dict, threads: int, progress, cancel) -> None:
    """Kacheln der Quelle (blob_offsets aufsteigend) in eine neue PMTiles-Datei streamen."""
    abort = threading.Event()
    halted = lambda: abort.is_set() or bool(cancel and cancel.is_set())  # noqa: E731
    out.parent.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(out.parent).free
    if free < total + RESERVE:
        raise MapError(f"Zu wenig Speicherplatz: {total / 1e9:.1f} GB nötig, {free / 1e9:.1f} GB frei.")

    dst = _Directory()
    for i in range(len(src)):
        dst.add(src.ids[i], new_offsets[bisect_left(blob_offsets, src.offsets[i])], src.lengths[i], src.runs[i])
    root_bytes, leaf_bytes = dst.build()
    metadata = _get_range(client, url, header["metadata_offset"], header["metadata_length"])
    if header["internal_compression"] != Compression.GZIP:
        metadata = gzip.compress(metadata, mtime=0)
    new_header = {k: header[k] for k in (
        "tile_compression", "tile_type", "min_zoom", "min_lon_e7", "min_lat_e7", "max_lon_e7", "max_lat_e7",
        "center_lon_e7", "center_lat_e7")}
    new_header.update(overrides)
    new_header.update(
        center_zoom=min(header["center_zoom"], new_header["max_zoom"]), clustered=True,
        internal_compression=Compression.GZIP, root_offset=127, root_length=len(root_bytes),
        metadata_offset=127 + len(root_bytes), metadata_length=len(metadata),
        leaf_directory_offset=127 + len(root_bytes) + len(metadata), leaf_directory_length=len(leaf_bytes),
        tile_data_offset=127 + len(root_bytes) + len(metadata) + len(leaf_bytes), tile_data_length=total,
        addressed_tiles_count=sum(src.runs), tile_entries_count=len(dst), tile_contents_count=len(blob_offsets))

    # Benachbarte Kacheln zu großen Range-Requests bündeln: (Start, Länge, erste, letzte+1 Kachel)
    chunks, first = [], 0
    for j in range(1, len(blob_offsets) + 1):
        end = blob_offsets[j - 1] + blob_lengths[j - 1]
        if (j == len(blob_offsets) or blob_offsets[j] - end > GAP
                or blob_offsets[j] + blob_lengths[j] - blob_offsets[first] > CHUNK):
            chunks.append((blob_offsets[first], end - blob_offsets[first], first, j))
            first = j

    part = out.with_name(out.name + ".part")
    base = header["tile_data_offset"]
    done = 0
    pool = ThreadPoolExecutor(threads)
    try:
        with open(part, "wb") as f:
            for block in (serialize_header(new_header), root_bytes, metadata, leaf_bytes):
                f.write(block)
            pending: deque = deque()  # begrenztes Fenster, damit nicht alles im RAM landet
            todo = iter(chunks)
            while True:
                while len(pending) < threads * 2 and (chunk := next(todo, None)):
                    pending.append((chunk, pool.submit(_get_range, client, url, base + chunk[0], chunk[1], halted)))
                if not pending:
                    break
                (start, length, a, b), future = pending.popleft()
                while not future.done():
                    if halted():
                        raise Cancelled()
                    wait([future], timeout=0.5)
                data = future.result()
                if length == sum(blob_lengths[a:b]):
                    f.write(data)
                else:
                    for j in range(a, b):
                        f.write(data[blob_offsets[j] - start: blob_offsets[j] - start + blob_lengths[j]])
                done += sum(blob_lengths[a:b])
                if progress:
                    progress(done, total)
        for attempt in range(10):  # alte Datei kann unter Windows kurz gesperrt sein
            try:
                part.replace(out)
                break
            except PermissionError:
                time.sleep(0.5)
        else:
            raise MapError("Alte Kartendatei ist in Benutzung – bitte erneut versuchen.")
    finally:
        abort.set()  # laufende Anfragen nicht abwarten, sie brechen beim nächsten Versuch ab
        pool.shutdown(wait=False, cancel_futures=True)
        part.unlink(missing_ok=True)


class MapJob:
    """Ein Karten-Download im Hintergrund (höchstens einer gleichzeitig)."""

    def __init__(self, path: Path):
        self.path = path
        self.state = {"state": "idle", "done": 0, "total": 0, "max_zoom": None, "message": ""}
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, maxzoom: int, region: tuple[str, str, list, Path] | None = None, url: str | None = None,
              out: Path | None = None, kind: str = "map") -> None:
        """Weltkarte laden oder - mit region=(ISO, Name, Polygone, Zieldatei) - ein einzelnes Land.
        url/out/kind: andere Quelle (z. B. Höhendaten) und Zieldatei; kind steht im Zustand für die Oberfläche."""
        if self.running():
            raise MapError("Es läuft bereits ein Download.")
        self._cancel.clear()
        label = "Höhendaten " if kind == "terrain" else ""
        self.state = {"state": "running", "done": 0, "total": 0, "max_zoom": maxzoom, "kind": kind,
                      "target": label + (region[1] if region else "Welt"), "iso": region[0] if region else None,
                      "message": "Verzeichnis wird gelesen …"}
        self._thread = threading.Thread(target=self._run, args=(maxzoom, region, url, out), daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    def _progress(self, done: int, total: int) -> None:
        self.state.update(done=done, total=total, message="")

    def _run(self, maxzoom: int, region, url: str | None, out: Path | None) -> None:
        try:
            if region:
                extract_region(region[3], region[2], maxzoom, url=url, progress=self._progress, cancel=self._cancel,
                               status=lambda text: self.state.update(message=text))
                self.state.update(state="done", message=f"{self.state['target']} geladen.")
            else:
                extract(out or self.path, maxzoom, url=url, progress=self._progress, cancel=self._cancel)
                self.state.update(state="done", message=f"{self.state['target']} geladen." if out else "Karte geladen.")
        except Cancelled:
            self.state.update(state="cancelled", message="Abgebrochen.")
        except MapError as e:
            self.state.update(state="error", message=str(e))
        except Exception as e:
            self.state.update(state="error", message=f"{type(e).__name__}: {e}"[:200])
