"""
GModular — KotOR TPC Texture Reader
====================================
Reads KotOR's proprietary TPC (Texture ProCessed) format and converts
it to raw RGBA pixel data suitable for uploading to OpenGL.

Format reference: KotOR Modding Wiki — TPC Format
https://kotor-modding.fandom.com/wiki/TPC_Format

TPC Header (128 bytes total):
  Size        UInt32  0    If 0, texture is uncompressed; else = compressed data size
  Unknown     Float   4    Unknown float value
  Width       UInt16  8
  Height      UInt16  10
  Encoding    Byte    12   2=RGB/DXT1, 4=RGBA/DXT5
  MipMapCount Byte    13   Number of mip-map levels
  Padding     Byte[114] 14  Padding to 128 bytes

Followed by:
  Texture data (compressed or uncompressed)
  TXI info (optional text)

Encoding types:
  1 = Grayscale (8-bit, 1 byte/pixel)
  2 = RGB (24-bit, 3 bytes/pixel) or DXT1 (if Size != 0)
  4 = RGBA (32-bit, 4 bytes/pixel) or DXT5 (if Size != 0)

DXT1:
  Compressed 4x4 blocks, each 8 bytes:
    Color0 UInt16, Color1 UInt16, LookupTable UInt32
  4 bits per pixel, supports 1-bit alpha

DXT5:
  Compressed 4x4 blocks, each 16 bytes:
    Alpha0 Byte, Alpha1 Byte, AlphaIndices[6] Bytes (DXT5 alpha block)
    Color0 UInt16, Color1 UInt16, LookupTable UInt32  (DXT1 color block)
  8 bits per pixel, full alpha

The TPC reader provides:
  - TPCReader.read_bytes(data: bytes) -> TPCImage  (parse from in-memory bytes)
  - TPCReader.read_file(path: str) -> TPCImage
  - TPCImage.to_rgba() -> bytes (raw RGBA, row-major, top-to-bottom)
  - TPCImage.to_qimage() -> QImage (requires qtpy + Qt backend)
"""
from __future__ import annotations

import struct
import math
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

# ── Optional numpy ────────────────────────────────────────────────────────────
try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False
    np = None  # type: ignore

# ─────────────────────────────────────────────────────────────────────────────
#  TPC encoding constants
# ─────────────────────────────────────────────────────────────────────────────

TPC_ENC_GRAYSCALE = 1
TPC_ENC_RGB_DXT1  = 2   # RGB uncompressed OR DXT1 compressed (if Size != 0)
TPC_ENC_RGBA_DXT5 = 4   # RGBA uncompressed OR DXT5 compressed (if Size != 0)

TPC_HEADER_SIZE = 128


@dataclass
class TPCMipMap:
    """A single mip-map level."""
    width:  int
    height: int
    data:   bytes      # Raw pixel data (uncompressed RGBA after decode)


@dataclass
class TPCImage:
    """
    A parsed TPC texture.

    The ``mipmaps`` list is ordered from largest (index 0) to smallest.
    All mip-maps are stored as raw uncompressed RGBA bytes (top-to-bottom).

    Cube maps have Height = 6 × Width (per Kotor.NET TPCBinaryFileHeader.CubeMap).
    """
    width:    int = 0
    height:   int = 0
    encoding: int = 0
    compressed: bool = False
    mipmaps: List[TPCMipMap] = field(default_factory=list)
    txi:    str = ""       # Optional TXI metadata text

    @property
    def rgba_bytes(self) -> bytes:
        """Return the largest mip-map's raw RGBA data (4 bytes/pixel)."""
        if self.mipmaps:
            return self.mipmaps[0].data
        return b''

    @property
    def is_valid(self) -> bool:
        return self.width > 0 and self.height > 0 and bool(self.mipmaps)

    @property
    def is_cubemap(self) -> bool:
        """True if this is a cube-map texture (height == 6 × width per Kotor.NET)."""
        return self.width > 0 and self.height == self.width * 6

    @property
    def mip_count(self) -> int:
        """Number of mip-map levels stored."""
        return len(self.mipmaps)

    def mipmap_at(self, level: int) -> 'TPCMipMap | None':
        """Return mip-map at the given level (0=largest), or None if out of range."""
        if 0 <= level < len(self.mipmaps):
            return self.mipmaps[level]
        return None

    def get_rgba_at_level(self, level: int = 0) -> bytes:
        """Return RGBA bytes for the given mip level (0=full-res)."""
        mm = self.mipmap_at(level)
        return mm.data if mm else b''

    def to_qimage(self):
        """Convert to a QImage (ARGB32) via qtpy; requires a Qt backend to be installed."""
        if not self.is_valid:
            return None
        try:
            from qtpy.QtGui import QImage
            w, h = self.width, self.height
            rgba = self.rgba_bytes
            if len(rgba) < w * h * 4:
                return None
            img = QImage(w, h, QImage.Format_RGBA8888)
            img.loadFromData  # type: ignore  # just check it exists
            for y in range(h):
                for x in range(w):
                    o = (y * w + x) * 4
                    r, g, b, a = rgba[o], rgba[o+1], rgba[o+2], rgba[o+3]
                    img.setPixelColor(x, y,
                        __import__('qtpy.QtGui', fromlist=['QColor']).QColor(r, g, b, a))
            return img
        except Exception as e:
            log.debug(f"TPCImage.to_qimage: {e}")
            return None


# ─────────────────────────────────────────────────────────────────────────────
#  DXT decompression helpers
# ─────────────────────────────────────────────────────────────────────────────

def _rgb565_to_rgb(color16: int) -> Tuple[int, int, int]:
    """Unpack RGB565 to 8-bit RGB."""
    r = ((color16 >> 11) & 0x1F) * 255 // 31
    g = ((color16 >>  5) & 0x3F) * 255 // 63
    b = ((color16      ) & 0x1F) * 255 // 31
    return r, g, b


def _dxt1_decode_block(block: bytes) -> List[Tuple[int, int, int, int]]:
    """
    Decode a single 8-byte DXT1 block into 16 RGBA pixels.
    Returns a list of 16 (r, g, b, a) tuples.
    """
    c0_raw, c1_raw = struct.unpack_from('<HH', block, 0)
    lut            = struct.unpack_from('<I',  block, 4)[0]

    c0 = _rgb565_to_rgb(c0_raw)
    c1 = _rgb565_to_rgb(c1_raw)

    if c0_raw > c1_raw:
        c2 = ((2*c0[0]+c1[0]+1)//3, (2*c0[1]+c1[1]+1)//3, (2*c0[2]+c1[2]+1)//3)
        c3 = ((c0[0]+2*c1[0]+1)//3, (c0[1]+2*c1[1]+1)//3, (c0[2]+2*c1[2]+1)//3)
        palette = [(*c0, 255), (*c1, 255), (*c2, 255), (*c3, 255)]
    else:
        c2 = ((c0[0]+c1[0])//2, (c0[1]+c1[1])//2, (c0[2]+c1[2])//2)
        palette = [(*c0, 255), (*c1, 255), (*c2, 255), (0, 0, 0, 0)]

    pixels = []
    for i in range(16):
        idx = (lut >> (i * 2)) & 0x3
        pixels.append(palette[idx])
    return pixels


def _dxt5_decode_block(block: bytes) -> List[Tuple[int, int, int, int]]:
    """
    Decode a single 16-byte DXT5 block into 16 RGBA pixels.
    Returns a list of 16 (r, g, b, a) tuples.
    """
    # Alpha block (8 bytes)
    a0  = block[0]
    a1  = block[1]
    # 6 bytes = 48 bits of alpha indices, 3 bits each for 16 pixels
    ab  = block[2:8]
    bits = int.from_bytes(ab, 'little')

    if a0 > a1:
        apalette = [
            a0, a1,
            (6*a0 + 1*a1) // 7,
            (5*a0 + 2*a1) // 7,
            (4*a0 + 3*a1) // 7,
            (3*a0 + 4*a1) // 7,
            (2*a0 + 5*a1) // 7,
            (1*a0 + 6*a1) // 7,
        ]
    else:
        apalette = [
            a0, a1,
            (4*a0 + 1*a1) // 5,
            (3*a0 + 2*a1) // 5,
            (2*a0 + 3*a1) // 5,
            (1*a0 + 4*a1) // 5,
            0, 255,
        ]

    alpha_vals = [(bits >> (i * 3)) & 0x7 for i in range(16)]

    # Color block (8 bytes at offset 8)
    color_pixels = _dxt1_decode_block(block[8:])

    pixels = []
    for i in range(16):
        r, g, b, _ = color_pixels[i]
        a = apalette[alpha_vals[i]]
        pixels.append((r, g, b, a))
    return pixels


def _decompress_dxt(data: bytes, width: int, height: int, encoding: int) -> bytes:
    """
    Decompress a DXT1 or DXT5 encoded texture.

    Returns raw RGBA bytes (width * height * 4 bytes, top-to-bottom).
    """
    # Round up to multiple of 4
    bw = max(1, (width  + 3) // 4)
    bh = max(1, (height + 3) // 4)

    block_size = 8 if encoding == TPC_ENC_RGB_DXT1 else 16
    out = bytearray(width * height * 4)

    for by in range(bh):
        for bx in range(bw):
            block_off = (by * bw + bx) * block_size
            if block_off + block_size > len(data):
                break
            block = data[block_off: block_off + block_size]

            if encoding == TPC_ENC_RGB_DXT1:
                pixels = _dxt1_decode_block(block)
            else:
                pixels = _dxt5_decode_block(block)

            for py in range(4):
                for px in range(4):
                    dst_x = bx * 4 + px
                    dst_y = by * 4 + py
                    if dst_x >= width or dst_y >= height:
                        continue
                    r, g, b, a = pixels[py * 4 + px]
                    off = (dst_y * width + dst_x) * 4
                    out[off]   = r
                    out[off+1] = g
                    out[off+2] = b
                    out[off+3] = a

    return bytes(out)


def _uncompressed_to_rgba(data: bytes, width: int, height: int,
                          encoding: int) -> bytes:
    """Convert uncompressed TPC pixel data to RGBA bytes."""
    n_pixels = width * height
    out = bytearray(n_pixels * 4)

    if encoding == TPC_ENC_GRAYSCALE:
        for i in range(min(n_pixels, len(data))):
            v = data[i]
            out[i*4:i*4+4] = bytes([v, v, v, 255])
    elif encoding == TPC_ENC_RGB_DXT1:   # actually uncompressed RGB here
        for i in range(min(n_pixels, len(data) // 3)):
            r, g, b = data[i*3], data[i*3+1], data[i*3+2]
            out[i*4:i*4+4] = bytes([r, g, b, 255])
    elif encoding == TPC_ENC_RGBA_DXT5:  # actually uncompressed RGBA here
        for i in range(min(n_pixels, len(data) // 4)):
            r, g, b, a = data[i*4], data[i*4+1], data[i*4+2], data[i*4+3]
            out[i*4:i*4+4] = bytes([r, g, b, a])
    else:
        # Unknown encoding: fill with magenta as error indicator
        for i in range(n_pixels):
            out[i*4:i*4+4] = bytes([255, 0, 255, 255])

    return bytes(out)


# ─────────────────────────────────────────────────────────────────────────────
#  TPC Reader
# ─────────────────────────────────────────────────────────────────────────────

class TPCReader:
    """
    Reads KotOR TPC texture files.

    Usage::
        img = TPCReader.from_bytes(tpc_bytes)
        rgba = img.rgba_bytes   # raw RGBA, 4 bytes/pixel, top-to-bottom
        w, h = img.width, img.height

        # Or from a file
        img = TPCReader.from_file("texture.tpc")
    """

    @staticmethod
    def from_bytes(data: bytes) -> TPCImage:
        """Parse TPC from in-memory bytes. Returns a TPCImage."""
        if len(data) < TPC_HEADER_SIZE:
            log.warning(f"TPC: data too small ({len(data)} bytes)")
            return TPCImage()
        return TPCReader._parse(data)

    @staticmethod
    def from_file(path: str) -> TPCImage:
        """Load and parse a TPC file. Returns a TPCImage."""
        try:
            with open(path, 'rb') as f:
                data = f.read()
        except OSError as e:
            log.error(f"TPC: cannot read {path!r}: {e}")
            return TPCImage()
        return TPCReader._parse(data)

    @staticmethod
    def _parse(data: bytes) -> TPCImage:
        # Parse header
        size_field = struct.unpack_from('<I', data, 0)[0]
        width      = struct.unpack_from('<H', data, 8)[0]
        height     = struct.unpack_from('<H', data, 10)[0]
        encoding   = data[12]
        mip_count  = data[13]

        if width == 0 or height == 0:
            log.debug("TPC: zero-dimension texture")
            return TPCImage()

        compressed = (size_field != 0)
        img = TPCImage(
            width=width,
            height=height,
            encoding=encoding,
            compressed=compressed,
        )

        mip_count = max(1, mip_count)
        offset = TPC_HEADER_SIZE
        mip_w, mip_h = width, height

        for m in range(mip_count):
            if offset >= len(data):
                break

            if compressed:
                bw = max(1, (mip_w + 3) // 4)
                bh = max(1, (mip_h + 3) // 4)
                block_size = 8 if encoding == TPC_ENC_RGB_DXT1 else 16
                mip_bytes  = bw * bh * block_size
            elif encoding == TPC_ENC_GRAYSCALE:
                mip_bytes = mip_w * mip_h
            elif encoding == TPC_ENC_RGB_DXT1:
                mip_bytes = mip_w * mip_h * 3
            elif encoding == TPC_ENC_RGBA_DXT5:
                mip_bytes = mip_w * mip_h * 4
            else:
                mip_bytes = mip_w * mip_h * 4

            end = min(offset + mip_bytes, len(data))
            raw = data[offset:end]

            # Decode to RGBA
            try:
                if compressed:
                    rgba = _decompress_dxt(raw, mip_w, mip_h, encoding)
                else:
                    rgba = _uncompressed_to_rgba(raw, mip_w, mip_h, encoding)
            except Exception as e:
                log.debug(f"TPC mip {m} decode error: {e}")
                rgba = bytes(mip_w * mip_h * 4)

            img.mipmaps.append(TPCMipMap(width=mip_w, height=mip_h, data=rgba))
            offset += mip_bytes
            mip_w = max(1, mip_w // 2)
            mip_h = max(1, mip_h // 2)

        # Anything remaining is TXI text
        if offset < len(data):
            try:
                img.txi = data[offset:].decode('ascii', errors='replace').strip()
            except Exception:
                pass

        log.debug(f"TPC: {width}x{height} enc={encoding} "
                  f"compressed={compressed} mips={len(img.mipmaps)}")
        return img


# ─────────────────────────────────────────────────────────────────────────────
#  TGA reader (used as fallback when no TPC is available)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TGAImage:
    """Simple TGA image data."""
    width:  int = 0
    height: int = 0
    rgba:   bytes = b''

    @property
    def is_valid(self) -> bool:
        return self.width > 0 and self.height > 0

    @property
    def rgba_bytes(self) -> bytes:
        return self.rgba


def read_tga(data: bytes) -> TGAImage:
    """
    Read a TGA image from bytes.
    Supports uncompressed RGB (type 2) and RGBA (type 2 with 32-bit) TGAs.
    Returns a TGAImage with RGBA pixel data.
    """
    if len(data) < 18:
        return TGAImage()

    id_len    = data[0]
    color_map = data[1]
    img_type  = data[2]
    # Skip color map spec (5 bytes at 3)
    x_origin  = struct.unpack_from('<h', data, 8)[0]
    y_origin  = struct.unpack_from('<h', data, 10)[0]
    width     = struct.unpack_from('<H', data, 12)[0]
    height    = struct.unpack_from('<H', data, 14)[0]
    bpp       = data[16]
    img_desc  = data[17]

    # We only support uncompressed true-colour (type 2, 3)
    if img_type not in (2, 3) or width == 0 or height == 0:
        return TGAImage()

    offset = 18 + id_len + (color_map * 0)   # color_map entries not supported
    pixels = bytearray(width * height * 4)
    flip_v = not bool(img_desc & 0x20)  # bit 5 = top-left origin

    if bpp == 24:
        for i in range(width * height):
            off = offset + i * 3
            if off + 3 > len(data):
                break
            b, g, r = data[off], data[off+1], data[off+2]
            pixels[i*4:i*4+4] = bytes([r, g, b, 255])
    elif bpp == 32:
        for i in range(width * height):
            off = offset + i * 4
            if off + 4 > len(data):
                break
            b, g, r, a = data[off], data[off+1], data[off+2], data[off+3]
            pixels[i*4:i*4+4] = bytes([r, g, b, a])
    elif bpp == 8:
        for i in range(min(width * height, len(data) - offset)):
            v = data[offset + i]
            pixels[i*4:i*4+4] = bytes([v, v, v, 255])
    else:
        return TGAImage()

    rgba = bytes(pixels)
    # Flip vertically if origin is bottom-left (standard TGA)
    if flip_v:
        row_size = width * 4
        rows = [rgba[i*row_size:(i+1)*row_size] for i in range(height)]
        rgba = b''.join(reversed(rows))

    return TGAImage(width=width, height=height, rgba=rgba)


# ─────────────────────────────────────────────────────────────────────────────
#  Unified texture loader (TPC or TGA, from ResourceManager)
# ─────────────────────────────────────────────────────────────────────────────

def load_texture(resref: str, resource_manager=None) -> Optional[TPCImage]:
    """
    Load a texture by ResRef from the game's resource manager.

    Priority: TPC first, then TGA.
    Returns a TPCImage (which may have been loaded from TGA data).

    Args:
        resref:           Texture name without extension.
        resource_manager: gmodular.formats.archives.ResourceManager instance.
                          If None, returns None.
    """
    if resource_manager is None:
        return None

    # Try TPC first (native KotOR format)
    tpc_data = resource_manager.get_file(resref, 'tpc')
    if tpc_data:
        img = TPCReader.from_bytes(tpc_data)
        if img.is_valid:
            return img

    # Fallback: TGA
    tga_data = resource_manager.get_file(resref, 'tga')
    if tga_data:
        tga = read_tga(tga_data)
        if tga.is_valid:
            # Wrap in TPCImage for a uniform interface
            mip = TPCMipMap(width=tga.width, height=tga.height, data=tga.rgba)
            return TPCImage(
                width=tga.width, height=tga.height,
                encoding=TPC_ENC_RGBA_DXT5, compressed=False,
                mipmaps=[mip],
            )

    return None


# ═══════════════════════════════════════════════════════════════════════════
#  TPC Writer  —  TGA/raw-RGBA → .tpc
#  Ref: Kotor.NET/Formats/KotorTPC, PyKotor/resource/formats/tpc/io_tpc.py
# ═══════════════════════════════════════════════════════════════════════════

def write_tpc_from_rgba(
    rgba_data: bytes,
    width: int,
    height: int,
    txi_text: str = "",
    *,
    alpha: bool = True,
) -> bytes:
    """Encode raw RGBA (or RGB) pixel data as a KotOR .tpc file (uncompressed).

    TPC header layout (128 bytes):
      0x00  uint32  data_size   (0 = uncompressed; else = compressed byte count)
      0x04  float   alpha_test  (0.0 = opaque)
      0x08  uint16  width
      0x0A  uint16  height
      0x0C  uint8   encoding    (1=grey, 2=RGB, 4=RGBA, 2+compressed=DXT1, 4+compressed=DXT5)
      0x0D  uint8   mipmap_count
      0x0E  byte[114] padding to 128 bytes
    Pixel data starts at 0x80, optional TXI text appended after.

    Parameters
    ----------
    rgba_data:  Raw pixel bytes (4 bpp if alpha=True, else 3 bpp).
    width, height: Texture dimensions (powers of two recommended).
    txi_text:   Optional TXI metadata appended after pixel data.
    alpha:      True → RGBA encoding=4, False → RGB encoding=2.
    """
    import struct

    encoding = 4 if alpha else 2
    bpp      = 4 if alpha else 3
    expected = width * height * bpp
    if len(rgba_data) < expected:
        raise ValueError(
            f"write_tpc_from_rgba: need {expected} bytes for {width}x{height} "
            f"({'RGBA' if alpha else 'RGB'}), got {len(rgba_data)}"
        )

    header = struct.pack("<IfHHBB",
        0,          # data_size = 0 (uncompressed)
        0.0,        # alpha_test
        width,
        height,
        encoding,
        1,          # mipmap_count = 1
    )
    header += b"\x00" * (128 - len(header))   # pad to 128 bytes

    body      = bytes(rgba_data[:expected])
    txi_bytes = txi_text.encode("ascii", errors="replace") if txi_text else b""

    return header + body + txi_bytes


def write_tpc_from_tga(tga_data: bytes, txi_text: str = "") -> bytes:
    """Convert TGA bytes → KotOR .tpc binary (uncompressed RGBA).

    Supports TGA type 2 (truecolour RGB/RGBA) which covers the vast majority
    of KotOR texture sources.

    References
    ----------
    PyKotor/resource/formats/tpc/tga2tpc.py — same algorithm (TGA→TPC).
    Kotor.NET/Formats/KotorTPC — binary layout reference.
    """
    tga = read_tga(tga_data)
    if not tga.is_valid:
        raise ValueError("write_tpc_from_tga: cannot parse source TGA")
    # read_tga always returns RGBA bytes
    return write_tpc_from_rgba(tga.rgba, tga.width, tga.height, txi_text=txi_text, alpha=True)
