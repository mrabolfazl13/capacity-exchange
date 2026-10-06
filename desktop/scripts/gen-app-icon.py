#!/usr/bin/env python3
"""Draw the desktop app icon: a mark on a rounded navy field, written as a 1024px PNG.

No image libraries are installed on this machine (and the network index is unreliable), so
the raster is emitted directly — zlib for the IDAT stream, struct for the chunks. The
render is supersampled 4x4 per pixel because a hard-edged mark looks wrong at 16px in a
taskbar, and every edge here is analytic rather than sampled from a bitmap font.

Usage: python scripts/gen-app-icon.py [output.png]
"""
from __future__ import annotations

import struct
import sys
import zlib

SIZE = 1024
SS = 4  # samples per axis, per pixel

NAVY = (13, 38, 54)
AMBER = (232, 178, 58)
CHALK = (243, 246, 248)


def rounded_field(x: float, y: float) -> bool:
    """True inside the rounded square, in unit coordinates with a small margin."""
    lo, hi, r = 0.03, 0.97, 0.20
    if lo + r <= x <= hi - r and lo <= y <= hi:
        return True
    if lo + r <= y <= hi - r and lo <= x <= hi:
        return True
    for cx in (lo + r, hi - r):
        for cy in (lo + r, hi - r):
            if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                return True
    return False


def arrow(x: float, y: float, *, right: bool) -> bool:
    """One exchange arrow: shaft plus a tapering head, mirrored for direction."""
    if not right:
        x = 1.0 - x
    cy = 0.38 if right else 0.62  # two arrows share the field, so each sits off-centre
    if 0.20 <= x <= 0.58 and abs(y - cy) <= 0.045:
        return True
    head_lo, head_hi, head_half = 0.52, 0.80, 0.15
    if head_lo <= x <= head_hi:
        taper = head_half * (1.0 - (x - head_lo) / (head_hi - head_lo))
        return abs(y - cy) <= taper
    return False


def pixel(x: float, y: float) -> tuple[int, int, int, int]:
    coverage = 0
    for sy in range(SS):
        for sx in range(SS):
            px = (x + (sx + 0.5) / SS) / SIZE
            py = (y + (sy + 0.5) / SS) / SIZE
            if rounded_field(px, py):
                coverage += 1
    if coverage == 0:
        return (0, 0, 0, 0)
    # Interior colours are tested at the pixel centre; only the field edge is supersampled.
    cx, cy = (x + 0.5) / SIZE, (y + 0.5) / SIZE
    colour = NAVY
    if arrow(cx, cy, right=True):
        colour = AMBER
    elif arrow(cx, cy, right=False):
        colour = CHALK
    return (*colour, round(255 * coverage / (SS * SS)))


def chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def write_png(path: str) -> None:
    rows = bytearray()
    for y in range(SIZE):
        rows += b"\x00"  # filter: none
        for x in range(SIZE):
            rows += bytes(pixel(x, y))
    ihdr = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + chunk(b"IEND", b"")
    )
    with open(path, "wb") as handle:
        handle.write(png)
    print(f"wrote {path} ({len(png):,} bytes, {SIZE}x{SIZE} RGBA)")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "src-tauri/app-icon.png"
    write_png(out)
