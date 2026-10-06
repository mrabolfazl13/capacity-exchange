"""Render the Android and iOS app icons from the shared brand mark.

This machine has no Pillow and no ImageMagick, and an app icon is a few hundred
bytes of pixels — so the PNG is decoded and re-encoded here with zlib alone. The
source is the same 1024px mark the desktop build ships, which keeps one artwork in
the repo instead of three that drift.

iOS wants opaque squares, so the mark is composited over its own tile colour
before scaling; the transparent rounded corners would otherwise come back as a
rejected upload.

    python scripts/gen-launcher-icons.py
"""

import json
import struct
import zlib
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / "desktop" / "src-tauri" / "app-icon.png"
IOS_SET = ROOT / "ios" / "Runner" / "Assets.xcassets" / "AppIcon.appiconset"
# mipmap density buckets, per Android's launcher icon sizes.
TARGETS = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}


def decode_png(path: Path):
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    pos, idat, size, color_type, bit_depth = 8, [], None, None, None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + length]
        if kind == b"IHDR":
            size, height, bit_depth, color_type = struct.unpack(">IIBB", chunk[:10])
            if height != size:
                raise ValueError("expected a square source icon")
            if (bit_depth, color_type) != (8, 6):
                raise ValueError("expected an 8-bit RGBA source")
        elif kind == b"IDAT":
            idat.append(chunk)
        elif kind == b"IEND":
            break
        pos += 12 + length
    raw = zlib.decompress(b"".join(idat))
    stride = size * 4
    rows, prev = [], bytearray(stride)
    at = 0
    for _ in range(size):
        filter_type = raw[at]
        at += 1
        line = bytearray(raw[at : at + stride])
        at += stride
        # Undo each scanline filter in place; the left byte is the already-
        # decoded pixel to the left of the one being repaired.
        for i in range(stride):
            left = line[i - 4] if i >= 4 else 0
            up = prev[i]
            ul = prev[i - 4] if i >= 4 else 0
            if filter_type == 1:
                line[i] = (line[i] + left) & 0xFF
            elif filter_type == 2:
                line[i] = (line[i] + up) & 0xFF
            elif filter_type == 3:
                line[i] = (line[i] + ((left + up) >> 1)) & 0xFF
            elif filter_type == 4:
                p = left + up - ul
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - ul)
                pred = left if (pa <= pb and pa <= pc) else (up if pb <= pc else ul)
                line[i] = (line[i] + pred) & 0xFF
        rows.append(bytes(line))
        prev = line
    return size, rows


def scale(rows, src_size, dst):
    """Averaging downscale, nearest for the one upscale iOS asks for (its
    1024pt store image, which the 512px mark is smaller than)."""
    out = []
    for y in range(dst):
        y0 = y * src_size // dst
        y1 = max(y0 + 1, (y + 1) * src_size // dst)
        row = bytearray(dst * 4)
        for x in range(dst):
            x0 = x * src_size // dst
            x1 = max(x0 + 1, (x + 1) * src_size // dst)
            acc = [0, 0, 0, 0]
            n = 0
            for sy in range(y0, min(y1, src_size)):
                src = rows[sy]
                for sx in range(x0, min(x1, src_size)):
                    o = sx * 4
                    for c in range(4):
                        acc[c] += src[o + c]
                    n += 1
            o = x * 4
            for c in range(4):
                row[o + c] = acc[c] // n
        out.append(bytes(row))
    return out


def encode_png(size, rows, path: Path):
    raw = b"".join(b"\x00" + row for row in rows)
    body = zlib.compress(raw, 9)

    def chunk(kind, payload):
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", body)
        + chunk(b"IEND", b"")
    )


def tile_color(size, rows):
    """The most common opaque colour: the icon's own background tile, which
    iOS needs behind the mark's transparent rounded corners."""
    counts = Counter()
    for row in rows:
        for o in range(0, size * 4, 4):
            if row[o + 3] == 255:
                counts[(row[o], row[o + 1], row[o + 2])] += 1
    return counts.most_common(1)[0][0]


def flatten(size, rows, bg):
    out = []
    for row in rows:
        line = bytearray(len(row))
        for o in range(0, size * 4, 4):
            a = row[o + 3]
            for c in range(3):
                line[o + c] = (row[o + c] * a + bg[c] * (255 - a)) // 255
            line[o + 3] = 255
        out.append(bytes(line))
    return out


def write_ios_icons(size, rows):
    manifest = json.loads((IOS_SET / "Contents.json").read_text(encoding="utf-8"))
    opaque = flatten(size, rows, tile_color(size, rows))
    written = set()
    for image in manifest["images"]:
        filename = image.get("filename")
        if not filename or filename in written:
            continue
        written.add(filename)
        side = image["size"].split("x")[0]
        px = round(float(side) * int(image.get("scale", "1").rstrip("x")))
        encode_png(px, scale(opaque, size, px), IOS_SET / filename)
    return len(written)


def main():
    src_size, rows = decode_png(SOURCE)
    for density, px in TARGETS.items():
        out_dir = ROOT / "android" / "app" / "src" / "main" / "res" / f"mipmap-{density}"
        out_dir.mkdir(parents=True, exist_ok=True)
        encode_png(px, scale(rows, src_size, px), out_dir / "ic_launcher.png")
        print(f"wrote mipmap-{density}/ic_launcher.png ({px}px)")
    print(f"wrote {write_ios_icons(src_size, rows)} iOS app icons")


if __name__ == "__main__":
    main()
