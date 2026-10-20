"""Draw packaging/spiral.ico. The Windows build uses this file as the app icon.

Run from the repository root:

    python3 packaging/make_icon.py
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path


COPPER = (0x8D, 0x3F, 0x1C)
CREAM = (0xFF, 0xFD, 0xF8)
SIZES = (16, 24, 32, 48, 64, 256)


def _spiral_points(size: int) -> list[tuple[float, float]]:
    center = (size - 1) / 2
    cy = center + size * 0.03
    turns = 2.15
    steps = max(180, size * 6)
    max_r = size * 0.30
    points = []
    for index in range(steps):
        t = index / (steps - 1)
        angle = t * turns * 2 * math.pi
        radius = max_r * (0.10 + 0.90 * t)
        points.append((center + radius * math.sin(angle), cy - radius * math.cos(angle)))
    return points


def _inside_rounded_rect(x: float, y: float, size: int, radius: float) -> float:
    """1 inside the rounded square, 0 outside, and a fraction on the edge."""
    inset = radius
    nearest_x = min(max(x, inset), size - inset)
    nearest_y = min(max(y, inset), size - inset)
    distance = math.hypot(x - nearest_x, y - nearest_y)
    feather = 0.8
    if distance <= radius - feather:
        return 1.0
    if distance >= radius + feather:
        return 0.0
    return 1.0 - (distance - (radius - feather)) / (2 * feather)


def _paint(size: int) -> list[list[tuple[int, int, int, int]]]:
    points = _spiral_points(size)
    thickness = max(1.15, size * 0.058)
    feather = max(0.6, size * 0.012)
    corner = size * 0.22
    rows: list[list[tuple[int, int, int, int]]] = []
    for y in range(size):
        row = []
        for x in range(size):
            px = x + 0.5
            py = y + 0.5
            cover = _inside_rounded_rect(px, py, size, corner)
            if cover <= 0:
                row.append((0, 0, 0, 0))
                continue
            nearest = min(math.hypot(px - sx, py - sy) for sx, sy in points)
            if nearest <= thickness - feather:
                stroke = 1.0
            elif nearest >= thickness + feather:
                stroke = 0.0
            else:
                stroke = 1.0 - (nearest - (thickness - feather)) / (2 * feather)
            red = round(COPPER[0] * (1 - stroke) + CREAM[0] * stroke)
            green = round(COPPER[1] * (1 - stroke) + CREAM[1] * stroke)
            blue = round(COPPER[2] * (1 - stroke) + CREAM[2] * stroke)
            row.append((red, green, blue, round(255 * cover)))
        rows.append(row)
    return rows


def _png(size: int, rows: list[list[tuple[int, int, int, int]]]) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + bytes(channel for pixel in row for channel in pixel) for row in rows)
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def _dib(size: int, rows: list[list[tuple[int, int, int, int]]]) -> bytes:
    """Classic BMP icon image: bottom-up BGRA plus a 1-bit AND mask."""
    xor_rows = []
    for row in reversed(rows):
        xor_rows.append(b"".join(bytes((pixel[2], pixel[1], pixel[0], pixel[3])) for pixel in row))
    xor_bitmap = b"".join(xor_rows)
    mask_stride = ((size + 31) // 32) * 4
    mask_rows = []
    for row in reversed(rows):
        bits = 0
        count = 0
        packed = bytearray()
        for pixel in row:
            bits = (bits << 1) | (0 if pixel[3] >= 128 else 1)
            count += 1
            if count == 8:
                packed.append(bits)
                bits = 0
                count = 0
        if count:
            packed.append(bits << (8 - count))
        packed.extend(b"\x00" * (mask_stride - len(packed)))
        mask_rows.append(bytes(packed))
    mask = b"".join(mask_rows)
    header = struct.pack(
        "<IIIHHIIIIII",
        40,
        size,
        size * 2,
        1,
        32,
        0,
        len(xor_bitmap) + len(mask),
        0,
        0,
        0,
        0,
    )
    return header + xor_bitmap + mask


def _ico(images: list[tuple[int, bytes]]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(images))
    entries = b""
    payload = b""
    offset = 6 + 16 * len(images)
    for size, data in images:
        entries += struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,
            size if size < 256 else 0,
            0,
            0,
            1,
            32,
            len(data),
            offset,
        )
        payload += data
        offset += len(data)
    return header + entries + payload


def write_icon(path: Path) -> None:
    images = []
    for size in SIZES:
        rows = _paint(size)
        # PNG entries are accepted by Windows Vista and later, including the
        # 256 px image Explorer uses. Smaller sizes stay as BMP so older
        # shells and the PyInstaller bootloader still have a classic image.
        images.append((size, _png(size, rows) if size >= 256 else _dib(size, rows)))
    path.write_bytes(_ico(images))


def main() -> None:
    destination = Path(__file__).resolve().parent / "spiral.ico"
    write_icon(destination)
    print(destination)


if __name__ == "__main__":
    main()
