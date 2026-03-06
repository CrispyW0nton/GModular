#!/usr/bin/env python3
"""
generate_icon.py — Generate GModular application icon.

Creates assets/icons/gmodular.ico (multi-size ICO file).
Run this before building the EXE if you don't have a real .ico file.

Usage:
    python tools/generate_icon.py
"""
from __future__ import annotations
import struct
import os
from pathlib import Path

HERE = Path(__file__).parent.parent   # GModular root
OUT  = HERE / "assets" / "icons" / "gmodular.ico"
OUT.parent.mkdir(parents=True, exist_ok=True)


def _make_bmp_24(size: int, bg_rgb: tuple, fg_rgb: tuple) -> bytes:
    """Create a minimal 24-bit BMP for an ICO entry."""
    w = h = size
    row_bytes = ((w * 3 + 3) & ~3)
    pixel_data_size = row_bytes * h

    # BITMAPINFOHEADER (40 bytes) — inside ICO uses combined h*2
    bih = struct.pack(
        "<IiiHHIIiiII",
        40,                 # biSize
        w,                  # biWidth
        h * 2,              # biHeight (XOR + AND mask combined)
        1,                  # biPlanes
        24,                 # biBitCount
        0,                  # biCompression (BI_RGB)
        pixel_data_size,    # biSizeImage
        0, 0, 0, 0,         # XPelsPerMeter, YPelsPerMeter, ClrUsed, ClrImportant
    )

    rows = []
    cx = w // 2
    cy = h // 2
    r = w // 2 - 1
    r2 = r * r

    for y in range(h - 1, -1, -1):   # BMP rows are bottom-up
        row = bytearray()
        for x in range(w):
            dx = x - cx
            dy = y - cy
            if dx * dx + dy * dy <= r2:
                row += bytes([fg_rgb[2], fg_rgb[1], fg_rgb[0]])   # BGR
            else:
                row += bytes([bg_rgb[2], bg_rgb[1], bg_rgb[0]])
        # Padding to 4-byte boundary
        while len(row) % 4:
            row += b"\x00"
        rows.append(bytes(row))

    xor_mask = b"".join(rows)

    # AND mask (1-bit per pixel, all 0 = opaque)
    and_row_bytes = ((w + 31) // 32) * 4
    and_mask = b"\x00" * and_row_bytes * h

    return bih + xor_mask + and_mask


def generate_ico(path: Path):
    sizes = [16, 32, 48, 64, 128, 256]

    # GModular colour: dark teal background (#1e6e6e), bright cyan foreground (#4ec9b0)
    bg = (0x1e, 0x6e, 0x6e)   # dark teal
    fg = (0x4e, 0xc9, 0xb0)   # KotOR green-teal

    images = [_make_bmp_24(s, bg, fg) for s in sizes]

    n = len(images)
    ico_header = struct.pack("<HHH", 0, 1, n)   # reserved=0, type=1 (ICO), count

    # ICONDIRENTRY: bWidth, bHeight, bColorCount, bReserved, wPlanes, wBitCount,
    #               dwBytesInRes, dwImageOffset
    entry_size = 16
    offset = 6 + n * entry_size

    entries = b""
    for i, img in enumerate(images):
        s = sizes[i]
        bw = 0 if s == 256 else s   # 0 means 256
        bh = bw
        entries += struct.pack(
            "<BBBBHHII",
            bw, bh, 0, 0,   # bWidth, bHeight, bColorCount, bReserved
            1,               # wPlanes
            24,              # wBitCount
            len(img),        # dwBytesInRes
            offset,          # dwImageOffset
        )
        offset += len(img)

    with open(path, "wb") as f:
        f.write(ico_header + entries + b"".join(images))

    print(f"Generated icon: {path} ({n} sizes: {sizes})")


if __name__ == "__main__":
    generate_ico(OUT)
