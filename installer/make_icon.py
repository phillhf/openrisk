"""Programmsymbol (app.ico) erzeugen: Radarschirm in den Farben der Oberfläche - ohne Zusatzpakete.

    python installer/make_icon.py
"""
from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent / "app.ico"
BACKGROUND = (13, 17, 23)
RING = (255, 139, 62)
SWEEP = (255, 77, 79)


def render(size: int) -> bytes:
    """RGBA-Pixel eines Radarschirms; vierfach überabgetastet für glatte Kanten."""
    sub, rows = 4, []
    c = size / 2
    for y in range(size):
        row = bytearray()
        for x in range(size):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sy in range(sub):
                for sx in range(sub):
                    px, py = x + (sx + 0.5) / sub - c, y + (sy + 0.5) / sub - c
                    r = math.hypot(px, py) / c
                    color, alpha = BACKGROUND, 0.0
                    # abgerundetes Quadrat als Hintergrund
                    edge = 0.92
                    qx, qy = max(abs(px) / c - (edge - 0.22), 0), max(abs(py) / c - (edge - 0.22), 0)
                    if math.hypot(qx, qy) <= 0.22:
                        alpha = 1.0
                        angle = (math.degrees(math.atan2(py, px)) + 360) % 360
                        # Radarstrahl: Keil, der zur Kante hin verblasst
                        if r < 0.72 and 290 <= angle <= 345:
                            strength = (angle - 290) / 55
                            color = tuple(round(b + (s - b) * 0.55 * strength) for b, s in zip(BACKGROUND, SWEEP))
                        for ring in (0.24, 0.48, 0.72):
                            if abs(r - ring) < 0.035:
                                color = RING
                        if r < 0.72 and (abs(px) < c * 0.025 or abs(py) < c * 0.025):
                            color = tuple(round(b + (g - b) * 0.45) for b, g in zip(BACKGROUND, RING))
                        # Ziel: roter Punkt
                        if math.hypot(px / c - 0.30, py / c + 0.36) < 0.085:
                            color = SWEEP
                    for i in range(3):
                        acc[i] += color[i] * alpha
                    acc[3] += alpha
            n = sub * sub
            a = acc[3] / n
            row += bytes((round(acc[0] / acc[3]) if acc[3] else 0, round(acc[1] / acc[3]) if acc[3] else 0,
                          round(acc[2] / acc[3]) if acc[3] else 0, round(a * 255)))
        rows.append(bytes(row))
    return b"".join(b"\x00" + r for r in rows)


def png(size: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(render(size), 9)) + chunk(b"IEND", b"")


def main() -> None:
    sizes = (16, 24, 32, 48, 64, 128, 256)
    images = [png(s) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    for size, data in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    OUT.write_bytes(head + entries + b"".join(images))
    print(f"{OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
