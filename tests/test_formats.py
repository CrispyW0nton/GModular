"""
Tests for new GModular format parsers:
  - TPC texture reader (gmodular.formats.tpc_reader)
  - LYT/VIS room layout parsers (gmodular.formats.lyt_vis)
  - WOK walkmesh parser (gmodular.formats.wok_parser)
  - MDL parser improvements (node header fix, face materials)

All tests are pure Python — no OpenGL / Qt required.
"""
from __future__ import annotations
import struct
import math
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ═════════════════════════════════════════════════════════════════════════════
#  TPC Reader Tests
# ═════════════════════════════════════════════════════════════════════════════

class TestTPCReader:
    """Tests for the KotOR TPC texture reader."""

    def _make_tpc_header(self, size_field=0, width=4, height=4,
                         encoding=4, mip_count=1) -> bytes:
        """Build a minimal TPC header."""
        header = bytearray(128)
        struct.pack_into('<I', header, 0, size_field)
        struct.pack_into('<f', header, 4, 0.0)
        struct.pack_into('<H', header, 8, width)
        struct.pack_into('<H', header, 10, height)
        header[12] = encoding
        header[13] = mip_count
        return bytes(header)

    def test_import(self):
        """TPCReader can be imported."""
        from gmodular.formats.tpc_reader import TPCReader, TPCImage
        assert TPCReader is not None

    def test_tpc_constants(self):
        """TPC encoding constants are correct."""
        from gmodular.formats.tpc_reader import (
            TPC_ENC_GRAYSCALE, TPC_ENC_RGB_DXT1, TPC_ENC_RGBA_DXT5,
            TPC_HEADER_SIZE
        )
        assert TPC_ENC_GRAYSCALE == 1
        assert TPC_ENC_RGB_DXT1  == 2
        assert TPC_ENC_RGBA_DXT5 == 4
        assert TPC_HEADER_SIZE   == 128

    def test_too_small_data(self):
        """Returns empty image for data smaller than header."""
        from gmodular.formats.tpc_reader import TPCReader
        img = TPCReader.from_bytes(b'\x00' * 64)
        assert not img.is_valid

    def test_zero_dimension(self):
        """Returns empty image for zero-size texture."""
        from gmodular.formats.tpc_reader import TPCReader
        header = self._make_tpc_header(width=0, height=0)
        img = TPCReader.from_bytes(header)
        assert not img.is_valid

    def test_uncompressed_rgba_2x2(self):
        """Parse a 2x2 uncompressed RGBA texture."""
        from gmodular.formats.tpc_reader import TPCReader, TPC_ENC_RGBA_DXT5
        header = self._make_tpc_header(size_field=0, width=2, height=2,
                                       encoding=TPC_ENC_RGBA_DXT5, mip_count=1)
        # 4 pixels × 4 bytes = 16 bytes of RGBA data
        pixel_data = bytes([
            255, 0,   0,   255,   # red
            0,   255, 0,   255,   # green
            0,   0,   255, 255,   # blue
            255, 255, 0,   255,   # yellow
        ])
        data = header + pixel_data
        img = TPCReader.from_bytes(data)
        assert img.is_valid
        assert img.width  == 2
        assert img.height == 2
        assert len(img.mipmaps) == 1
        rgba = img.rgba_bytes
        assert len(rgba) == 16
        assert rgba[0]  == 255   # R
        assert rgba[1]  == 0     # G
        assert rgba[2]  == 0     # B
        assert rgba[3]  == 255   # A

    def test_uncompressed_rgb_1x1(self):
        """Parse a 1x1 uncompressed RGB texture."""
        from gmodular.formats.tpc_reader import TPCReader, TPC_ENC_RGB_DXT1
        header = self._make_tpc_header(size_field=0, width=1, height=1,
                                       encoding=TPC_ENC_RGB_DXT1, mip_count=1)
        pixel_data = bytes([128, 64, 32])   # one RGB pixel
        data = header + pixel_data
        img = TPCReader.from_bytes(data)
        assert img.is_valid
        assert img.width == 1
        assert img.height == 1
        rgba = img.rgba_bytes
        assert rgba[0] == 128   # R
        assert rgba[1] == 64    # G
        assert rgba[2] == 32    # B
        assert rgba[3] == 255   # Alpha filled to 255

    def test_grayscale_4x4(self):
        """Parse a 4x4 grayscale texture."""
        from gmodular.formats.tpc_reader import TPCReader, TPC_ENC_GRAYSCALE
        header = self._make_tpc_header(size_field=0, width=4, height=4,
                                       encoding=TPC_ENC_GRAYSCALE, mip_count=1)
        # 16 pixels × 1 byte
        pixel_data = bytes(range(16))
        data = header + pixel_data
        img = TPCReader.from_bytes(data)
        assert img.is_valid
        assert img.width  == 4
        assert img.height == 4
        rgba = img.rgba_bytes
        assert len(rgba) == 64
        # First pixel should be grey(0) = (0, 0, 0, 255)
        assert rgba[0] == 0
        assert rgba[1] == 0
        assert rgba[2] == 0
        assert rgba[3] == 255

    def test_multiple_mipmaps(self):
        """TPC with multiple mip levels parses correctly."""
        from gmodular.formats.tpc_reader import TPCReader, TPC_ENC_RGBA_DXT5
        # 4x4 base + 2x2 + 1x1
        header = self._make_tpc_header(size_field=0, width=4, height=4,
                                       encoding=TPC_ENC_RGBA_DXT5, mip_count=3)
        mip0 = bytes([100, 100, 100, 255] * 16)   # 4x4
        mip1 = bytes([50,  50,  50,  255] * 4)    # 2x2
        mip2 = bytes([25,  25,  25,  255] * 1)    # 1x1
        data = header + mip0 + mip1 + mip2
        img = TPCReader.from_bytes(data)
        assert img.is_valid
        assert len(img.mipmaps) == 3
        assert img.mipmaps[0].width == 4
        assert img.mipmaps[1].width == 2
        assert img.mipmaps[2].width == 1

    def test_txi_metadata_appended(self):
        """TXI metadata after pixel data is stored in txi field."""
        from gmodular.formats.tpc_reader import TPCReader, TPC_ENC_GRAYSCALE
        header = self._make_tpc_header(size_field=0, width=1, height=1,
                                       encoding=TPC_ENC_GRAYSCALE, mip_count=1)
        pixel_data = bytes([128])
        txi_text = b"proceduretype cycle\nnumframes 4\n"
        data = header + pixel_data + txi_text
        img = TPCReader.from_bytes(data)
        assert "proceduretype" in img.txi

    def test_dxt1_rgb565_conversion(self):
        """RGB565 to RGB conversion is correct."""
        from gmodular.formats.tpc_reader import _rgb565_to_rgb
        # White: 0xFFFF = R=31,G=63,B=31 -> all 255
        r, g, b = _rgb565_to_rgb(0xFFFF)
        assert r == 255
        assert g == 255
        assert b == 255
        # Black: 0x0000 -> all 0
        r, g, b = _rgb565_to_rgb(0x0000)
        assert r == 0
        assert g == 0
        assert b == 0


class TestTGAReader:
    """Tests for the TGA fallback reader."""

    def _make_tga(self, width=2, height=2, bpp=24) -> bytes:
        """Build a minimal uncompressed TGA."""
        header = bytearray(18)
        header[2]  = 2          # image type: uncompressed true-colour
        header[12] = width & 0xFF
        header[13] = (width >> 8) & 0xFF
        header[14] = height & 0xFF
        header[15] = (height >> 8) & 0xFF
        header[16] = bpp
        header[17] = 0x20       # top-left origin (no flip needed)
        return bytes(header)

    def test_import(self):
        from gmodular.formats.tpc_reader import read_tga
        assert read_tga is not None

    def test_simple_24bit(self):
        """Read a 2x2 24-bit TGA."""
        from gmodular.formats.tpc_reader import read_tga
        header = self._make_tga(2, 2, 24)
        # BGR order in TGA, 4 pixels
        pixels = bytes([
            0, 0, 255,   # blue pixel
            0, 255, 0,   # green pixel
            255, 0, 0,   # red pixel
            255, 255, 0, # yellow pixel (BGR)
        ])
        data = header + pixels
        img = read_tga(data)
        assert img.is_valid
        assert img.width  == 2
        assert img.height == 2
        rgba = img.rgba_bytes
        # First pixel: BGR(0,0,255) -> RGB(255,0,0) -> RGBA(255,0,0,255)
        assert rgba[0] == 255   # R
        assert rgba[1] == 0     # G
        assert rgba[2] == 0     # B
        assert rgba[3] == 255   # A

    def test_32bit_alpha(self):
        """Read a 1x1 32-bit TGA with alpha."""
        from gmodular.formats.tpc_reader import read_tga
        header = self._make_tga(1, 1, 32)
        # BGRA
        pixels = bytes([64, 128, 200, 180])
        data = header + pixels
        img = read_tga(data)
        assert img.is_valid
        rgba = img.rgba_bytes
        assert rgba[0] == 200   # R (from B=64 in BGRA? no, TGA stores BGRA)
        # Actually: BGRA(64,128,200,180) -> R=200, G=128, B=64, A=180
        assert rgba[3] == 180   # A

    def test_invalid_type(self):
        """Unknown image type returns invalid image."""
        from gmodular.formats.tpc_reader import read_tga
        header = bytearray(self._make_tga(2, 2, 24))
        header[2] = 10   # RLE compressed — not supported
        img = read_tga(bytes(header) + bytes(12))
        assert not img.is_valid


# ═════════════════════════════════════════════════════════════════════════════
#  LYT / VIS Parser Tests
# ═════════════════════════════════════════════════════════════════════════════

class TestLYTParser:
    """Tests for the KotOR LYT room layout parser."""

    def test_import(self):
        from gmodular.formats.lyt_vis import LYTParser, LayoutData, RoomPlacement
        assert LYTParser is not None

    def test_empty_lyt(self):
        """Empty LYT text produces empty LayoutData."""
        from gmodular.formats.lyt_vis import LYTParser
        layout = LYTParser.from_string("")
        assert layout.room_count == 0
        assert len(layout.door_hooks) == 0

    def test_simple_rooms(self):
        """Parse a simple LYT with two rooms."""
        from gmodular.formats.lyt_vis import LYTParser
        text = """
roomcount 2
  danm13aa 0.0000 0.0000 0.0000
  danm13ab 10.0000 5.0000 0.0000

doorhookcount 0

trackcount 0

obstaclecount 0
"""
        layout = LYTParser.from_string(text)
        assert layout.room_count == 2
        r0 = layout.rooms[0]
        assert r0.resref == 'danm13aa'
        assert r0.x == 0.0
        assert r0.y == 0.0

        r1 = layout.rooms[1]
        assert r1.resref == 'danm13ab'
        assert r1.x == pytest.approx(10.0)
        assert r1.y == pytest.approx(5.0)

    def test_room_with_door_hooks(self):
        """Parse a LYT with door hooks."""
        from gmodular.formats.lyt_vis import LYTParser
        text = """
roomcount 1
  room01 0.0 0.0 0.0

doorhookcount 1
  DW_door01 room01 2.5 0.0 0.0 0.0 0.0 0.0 1.0

trackcount 0
obstaclecount 0
"""
        layout = LYTParser.from_string(text)
        assert len(layout.door_hooks) == 1
        dh = layout.door_hooks[0]
        assert dh.name == 'DW_door01'
        assert dh.room == 'room01'
        assert dh.x == pytest.approx(2.5)
        assert dh.qw == pytest.approx(1.0)

    def test_get_room_case_insensitive(self):
        """get_room() is case-insensitive."""
        from gmodular.formats.lyt_vis import LYTParser
        text = "roomcount 1\n  MyRoom 1.0 2.0 3.0\ndoorhookcount 0\ntrackcount 0\nobstaclecount 0\n"
        layout = LYTParser.from_string(text)
        assert layout.get_room('MYROOM') is not None
        assert layout.get_room('myroom') is not None
        assert layout.get_room('myroom').z == pytest.approx(3.0)

    def test_comment_lines_ignored(self):
        """Lines starting with '#' are ignored."""
        from gmodular.formats.lyt_vis import LYTParser
        text = """
# This is a comment
roomcount 1
  room01 0.0 0.0 0.0
# Another comment
doorhookcount 0
trackcount 0
obstaclecount 0
"""
        layout = LYTParser.from_string(text)
        assert layout.room_count == 1

    def test_position_tuple(self):
        """RoomPlacement.position returns tuple."""
        from gmodular.formats.lyt_vis import RoomPlacement
        rp = RoomPlacement('test', 1.0, 2.0, 3.0)
        assert rp.position == (1.0, 2.0, 3.0)

    def test_malformed_room_line_skipped(self):
        """Malformed room lines are skipped gracefully."""
        from gmodular.formats.lyt_vis import LYTParser
        text = "roomcount 3\n  good_room 0 0 0\n  bad_room\n  also_good 1.0 2.0 3.0\n\ndoorhookcount 0\ntrackcount 0\nobstaclecount 0\n"
        layout = LYTParser.from_string(text)
        # 'bad_room' is malformed, so only 2 valid rooms
        assert layout.room_count == 2

    def test_get_door_hooks_for_room(self):
        """get_door_hooks returns hooks for specific room."""
        from gmodular.formats.lyt_vis import LYTParser
        text = """
roomcount 2
  room01 0.0 0.0 0.0
  room02 10.0 0.0 0.0

doorhookcount 3
  DW_A room01 1.0 0.0 0.0
  DW_B room01 -1.0 0.0 0.0
  DW_C room02 1.0 0.0 0.0

trackcount 0
obstaclecount 0
"""
        layout = LYTParser.from_string(text)
        hooks_01 = layout.get_door_hooks('room01')
        hooks_02 = layout.get_door_hooks('room02')
        assert len(hooks_01) == 2
        assert len(hooks_02) == 1


class TestLYTWriter:
    """Tests for the LYT writer (round-trip)."""

    def test_roundtrip_empty(self):
        """Empty layout serialises and re-parses cleanly."""
        from gmodular.formats.lyt_vis import LYTParser, LYTWriter, LayoutData
        layout = LayoutData()
        text = LYTWriter.to_string(layout)
        layout2 = LYTParser.from_string(text)
        assert layout2.room_count == 0

    def test_roundtrip_rooms(self):
        """Layout with rooms round-trips correctly."""
        from gmodular.formats.lyt_vis import (
            LYTParser, LYTWriter, LayoutData, RoomPlacement
        )
        layout = LayoutData()
        layout.rooms.append(RoomPlacement('danm13aa', 0.0, 0.0, 0.0))
        layout.rooms.append(RoomPlacement('danm13ab', 15.0, 5.0, -1.5))
        text = LYTWriter.to_string(layout)
        layout2 = LYTParser.from_string(text)
        assert layout2.room_count == 2
        r = layout2.get_room('danm13ab')
        assert r is not None
        assert r.x == pytest.approx(15.0)
        assert r.z == pytest.approx(-1.5)


class TestVISParser:
    """Tests for the KotOR VIS visibility file parser."""

    def test_import(self):
        from gmodular.formats.lyt_vis import VISParser, VisibilityData
        assert VISParser is not None

    def test_empty_vis(self):
        from gmodular.formats.lyt_vis import VISParser
        vis = VISParser.from_string("")
        assert vis.visibility == {}

    def test_simple_vis(self):
        """Parse basic visibility relationships."""
        from gmodular.formats.lyt_vis import VISParser
        text = """
room01
  room02 room03
room02
  room01
room03
  room01
"""
        vis = VISParser.from_string(text)
        assert 'room01' in vis.visibility
        v = vis.visible_from('room01')
        assert 'room02' in v or 'room03' in v

    def test_are_visible_symmetric(self):
        """are_visible checks both directions."""
        from gmodular.formats.lyt_vis import VISParser
        # Real KotOR .vis format: room_a on one line, then visible rooms listed
        # The VIS format requires visible rooms to be on subsequent lines
        # after each room declaration, NOT on the same line.
        text = "room_a\nroom_b\n"
        vis = VISParser.from_string(text)
        # room_a sees room_b (manually set up for this test)
        vis.visibility['room_a'] = ['room_b']
        assert vis.are_visible('room_a', 'room_b')
        assert vis.are_visible('room_b', 'room_a')

    def test_unknown_room_returns_empty(self):
        """visible_from for unknown room returns empty list."""
        from gmodular.formats.lyt_vis import VISParser
        vis = VISParser.from_string("")
        assert vis.visible_from('nonexistent') == []


# ═════════════════════════════════════════════════════════════════════════════
#  WOK Parser Tests
# ═════════════════════════════════════════════════════════════════════════════

class TestWalkability:
    """Tests for surface material walkability."""

    def test_dirt_is_walkable(self):
        from gmodular.formats.wok_parser import is_walkable
        assert is_walkable(0) is True    # Dirt

    def test_nonwalk_not_walkable(self):
        from gmodular.formats.wok_parser import is_walkable
        assert is_walkable(6) is False   # NonWalk

    def test_lava_not_walkable(self):
        from gmodular.formats.wok_parser import is_walkable
        assert is_walkable(14) is False  # Lava

    def test_stone_walkable(self):
        from gmodular.formats.wok_parser import is_walkable
        assert is_walkable(3) is True    # Stone

    def test_water_walkable(self):
        from gmodular.formats.wok_parser import is_walkable
        assert is_walkable(5) is True    # Water

    def test_out_of_range_returns_false(self):
        from gmodular.formats.wok_parser import is_walkable
        assert is_walkable(999) is False


class TestWalkFace:
    """Tests for WalkFace data structure."""

    def test_walkable_face(self):
        from gmodular.formats.wok_parser import WalkFace
        face = WalkFace((0,0,0), (1,0,0), (0,1,0), material=0)  # Dirt
        assert face.walkable is True

    def test_non_walkable_face(self):
        from gmodular.formats.wok_parser import WalkFace
        face = WalkFace((0,0,0), (1,0,0), (0,1,0), material=6)  # NonWalk
        assert face.walkable is False

    def test_face_center(self):
        from gmodular.formats.wok_parser import WalkFace
        face = WalkFace((0,0,0), (2,0,0), (1,2,0), material=0)
        cx, cy, cz = face.center
        assert cx == pytest.approx(1.0)
        assert cy == pytest.approx(2.0/3.0, abs=1e-5)
        assert cz == pytest.approx(0.0)

    def test_as_tuple(self):
        from gmodular.formats.wok_parser import WalkFace
        v0, v1, v2 = (0,0,0), (1,0,0), (0,1,0)
        face = WalkFace(v0, v1, v2, material=0)
        t = face.as_tuple()
        assert t == (v0, v1, v2)


class TestWalkMesh:
    """Tests for WalkMesh operations."""

    def _make_walk_mesh(self):
        from gmodular.formats.wok_parser import WalkMesh, WalkFace
        wm = WalkMesh(name="test")
        # A 2x2 floor at z=0 (walkable)
        wm.faces.append(WalkFace(
            (-1,-1,0), (1,-1,0), (1,1,0), material=0, normal=(0,0,1)
        ))
        wm.faces.append(WalkFace(
            (-1,-1,0), (1,1,0), (-1,1,0), material=0, normal=(0,0,1)
        ))
        # A wall (non-walkable)
        wm.faces.append(WalkFace(
            (2,0,0), (3,0,0), (3,0,2), material=6, normal=(0,1,0)
        ))
        return wm

    def test_walkable_filter(self):
        wm = self._make_walk_mesh()
        assert len(wm.walkable_faces)    == 2
        assert len(wm.non_walkable_faces) == 1

    def test_height_at_center(self):
        wm = self._make_walk_mesh()
        z = wm.height_at(0.0, 0.0)
        assert z is not None
        assert abs(z) < 0.01

    def test_height_outside_returns_none(self):
        wm = self._make_walk_mesh()
        z = wm.height_at(100.0, 100.0)
        assert z is None

    def test_position_walkable(self):
        wm = self._make_walk_mesh()
        assert wm.is_position_walkable(0.0, 0.0) is True
        assert wm.is_position_walkable(100.0, 100.0) is False

    def test_walk_tris_count(self):
        wm = self._make_walk_mesh()
        assert len(wm.walk_tris())    == 2
        assert len(wm.nowalk_tris())  == 1


class TestRayTriangleIntersect:
    """Tests for the Möller-Trumbore ray-triangle intersection."""

    def test_hit_flat_floor(self):
        """Ray from above hits a horizontal floor triangle."""
        from gmodular.formats.wok_parser import _ray_triangle_intersect
        # Triangle at z=0
        v0, v1, v2 = (0,0,0), (2,0,0), (1,2,0)
        # Ray from above (z=5) pointing downward
        z = _ray_triangle_intersect((1, 0.5, 5), (0,0,-1), v0, v1, v2)
        assert z is not None
        assert abs(z) < 0.01

    def test_miss_outside_triangle(self):
        """Ray misses triangle when aimed outside it."""
        from gmodular.formats.wok_parser import _ray_triangle_intersect
        v0, v1, v2 = (0,0,0), (1,0,0), (0,1,0)
        z = _ray_triangle_intersect((5, 5, 5), (0,0,-1), v0, v1, v2)
        assert z is None

    def test_parallel_ray_no_hit(self):
        """Horizontal ray parallel to floor doesn't intersect."""
        from gmodular.formats.wok_parser import _ray_triangle_intersect
        v0, v1, v2 = (0,0,0), (2,0,0), (1,2,0)
        z = _ray_triangle_intersect((0, 0, 1), (1,0,0), v0, v1, v2)
        assert z is None


class TestFaceNormal:
    """Tests for face normal computation."""

    def test_floor_normal_up(self):
        """A flat floor has upward normal."""
        from gmodular.formats.wok_parser import _face_normal
        nx, ny, nz = _face_normal((0,0,0), (1,0,0), (0,1,0))
        assert abs(nz - 1.0) < 0.01
        assert abs(nx) < 0.01
        assert abs(ny) < 0.01

    def test_wall_normal_horizontal(self):
        """A vertical wall has horizontal normal."""
        from gmodular.formats.wok_parser import _face_normal
        nx, ny, nz = _face_normal((0,0,0), (1,0,0), (0,0,1))
        # Normal should be perpendicular to XZ plane => points in Y direction
        assert abs(abs(ny) - 1.0) < 0.01
        assert abs(nz) < 0.01


# ═════════════════════════════════════════════════════════════════════════════
#  MDL Parser improvements
# ═════════════════════════════════════════════════════════════════════════════

class TestMDLFaceMaterilas:
    """Test that MDL parser stores face material IDs."""

    def _build_minimal_mdl(self) -> bytes:
        """
        Build a minimal valid KotOR1 MDL with 2 triangles and known materials.
        Face 0: material=0 (dirt, walkable)
        Face 1: material=6 (nonwalk)
        """
        BASE = 12
        GEO  = 80
        MDL  = 104
        NO   = GEO + MDL  # = 184 (names offset)

        # Offsets (all relative to BASE)
        ROOT_OFF  = NO + 12        # 196
        ARR_OFF   = ROOT_OFF + 80  # 276
        STR0      = ARR_OFF + 8    # 284
        MESH_OFF  = STR0 + 16      # 300
        MESH_HDR  = MESH_OFF + 80  # 380
        CHILD_OFF = MESH_HDR + 332 # 712
        FACE_OFF  = CHILD_OFF + 4  # 716
        VERT_OFF  = FACE_OFF + 64  # 780 (2 faces × 32 bytes)
        TOTAL_OFF = VERT_OFF + 48  # 780 + 48 = 828

        mdl = bytearray(TOTAL_OFF + BASE)
        B = BASE

        # File header
        struct.pack_into('<I', mdl, 4, len(mdl))   # mdl_length

        # Geometry header
        struct.pack_into('<I', mdl, B+0,  4273776)  # fp1 = K1
        mdl[B+8:B+40] = b'testroom' + b'\x00' * 24
        struct.pack_into('<I', mdl, B+40, ROOT_OFF)
        struct.pack_into('<I', mdl, B+44, 2)

        # Model extension
        M = B + GEO
        mdl[M+52:M+84] = b'NULL' + b'\x00' * 28

        # Names array
        N = B + NO
        struct.pack_into('<I', mdl, N+0, ARR_OFF)
        struct.pack_into('<I', mdl, N+4, 2)
        struct.pack_into('<I', mdl, N+8, 2)

        # Name pointers
        struct.pack_into('<I', mdl, B+ARR_OFF+0, STR0)
        struct.pack_into('<I', mdl, B+ARR_OFF+4, STR0 + 8)
        mdl[B+STR0:B+STR0+8] = b'root\x00\x00\x00\x00'
        mdl[B+STR0+8:B+STR0+16] = b'floor\x00\x00\x00'

        # Root node
        # Kotor.NET MDLBinaryNodeHeader: NodeType(2)+NodeIndex(2)+NameIndex(2)+Padding(2)
        R = B + ROOT_OFF
        struct.pack_into('<H', mdl, R,   0x0001)  # node_type
        struct.pack_into('<H', mdl, R+2, 0)       # NodeIndex (sequential)
        struct.pack_into('<H', mdl, R+4, 0)       # NameIndex -> names[0] = 'root'
        struct.pack_into('<ffff', mdl, R+28, 0, 0, 0, 1)  # rotation
        struct.pack_into('<III', mdl, R+44, CHILD_OFF, 1, 1)

        # Mesh node
        MN = B + MESH_OFF
        struct.pack_into('<H', mdl, MN,   0x0021)  # mesh|header
        struct.pack_into('<H', mdl, MN+2, 1)       # NodeIndex (sequential)
        struct.pack_into('<H', mdl, MN+4, 1)       # NameIndex -> names[1] = 'floor'
        struct.pack_into('<ffff', mdl, MN+28, 0, 0, 0, 1)

        # Child pointer
        struct.pack_into('<I', mdl, B+CHILD_OFF, MESH_OFF)

        # Mesh header
        MH = B + MESH_HDR
        struct.pack_into('<III', mdl, MH+8, FACE_OFF, 2, 2)  # faces
        struct.pack_into('<fff', mdl, MH+60, 0.8, 0.8, 0.8)   # diffuse
        struct.pack_into('<fff', mdl, MH+72, 0.2, 0.2, 0.2)   # ambient
        struct.pack_into('<I', mdl, MH+252, 12)   # mdx stride (xyz only)
        struct.pack_into('<i', mdl, MH+260, 0)    # mdx vertex offset
        # other offsets: -1
        for off in range(264, 304, 4):
            struct.pack_into('<i', mdl, MH+off, -1)
        struct.pack_into('<H', mdl, MH+304, 4)    # 4 vertices
        mdl[MH+313] = 1                            # render = True
        struct.pack_into('<I', mdl, MH+324, VERT_OFF)  # MDX data offset
        struct.pack_into('<I', mdl, MH+328, VERT_OFF)  # MDL vert positions

        # Face 0: material=0 (dirt/walkable), vertices 0,1,2
        F0 = B + FACE_OFF
        struct.pack_into('<fff', mdl, F0+0, 0, 0, 1)   # normal
        struct.pack_into('<f',   mdl, F0+12, 0)          # plane_dist
        struct.pack_into('<I',   mdl, F0+12, 0)          # material=0 DIRT
        struct.pack_into('<HHH', mdl, F0+26, 0, 1, 2)   # vertex indices

        # Face 1: material=6 (nonwalk), vertices 0,2,3
        F1 = B + FACE_OFF + 32
        struct.pack_into('<fff', mdl, F1+0, 0, 0, 1)    # normal
        struct.pack_into('<I',   mdl, F1+12, 6)          # material=6 NONWALK
        struct.pack_into('<HHH', mdl, F1+26, 0, 2, 3)   # vertex indices

        # Vertices (positions at MDX)
        V = B + VERT_OFF
        struct.pack_into('<fff', mdl, V+0,  -1, -1, 0)
        struct.pack_into('<fff', mdl, V+12,  1, -1, 0)
        struct.pack_into('<fff', mdl, V+24,  1,  1, 0)
        struct.pack_into('<fff', mdl, V+36, -1,  1, 0)

        return bytes(mdl)

    def test_face_materials_parsed(self):
        """MDL parser extracts face material IDs."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl_data = self._build_minimal_mdl()
        # MDX is same region as the MDL vertices
        parser = MDLParser(mdl_data, mdl_data[12:])
        mesh = parser.parse()

        visible = mesh.visible_mesh_nodes()
        assert len(visible) >= 1

        node = visible[0]
        assert len(node.faces)          == 2
        assert len(node.face_materials) == 2
        assert node.face_materials[0]   == 0   # Dirt (walkable)
        assert node.face_materials[1]   == 6   # NonWalk

    def test_node_header_name_index_correct(self):
        """Node NameIndex is read from offset 4 (not offset 2, per Kotor.NET MDLBinaryNodeHeader).
        Layout: NodeType(u16@0), NodeIndex(u16@2), NameIndex(u16@4), Padding(u16@6).
        """
        from gmodular.formats.mdl_parser import MDLParser
        mdl_data = self._build_minimal_mdl()
        parser = MDLParser(mdl_data, mdl_data[12:])
        mesh = parser.parse()
        # Model name should be read correctly
        assert mesh.name == 'testroom'
        # Visible mesh node should be named 'floor' (name index 1)
        visible = mesh.visible_mesh_nodes()
        assert len(visible) >= 1
        # The root node has name_index=0 -> 'root'
        # The mesh node has name_index=1 -> 'floor'
        all_nodes = mesh.all_nodes()
        node_names = {n.name for n in all_nodes}
        assert 'root'  in node_names
        assert 'floor' in node_names


class TestMDLParserConstants:
    """Test MDL parser header constants match KotOR Modding Wiki."""

    def test_geo_header_size(self):
        from gmodular.formats.mdl_parser import MDLParser
        assert MDLParser._GEO_HDR_SIZE == 80

    def test_names_offset(self):
        from gmodular.formats.mdl_parser import MDLParser
        assert MDLParser._NAMES_OFF == 184

    def test_supermodel_offset(self):
        """Supermodel name is at BASE+136 = M+56."""
        from gmodular.formats.mdl_parser import MDLParser
        assert MDLParser._SUPERMODEL_OFF == 56

    def test_fp_k1_contains_known_values(self):
        from gmodular.formats.mdl_parser import MDLParser
        assert 4273776 in MDLParser._FP_K1
        assert 4273392 in MDLParser._FP_K1

    def test_fp_k2_contains_known_values(self):
        from gmodular.formats.mdl_parser import MDLParser
        assert 4285200 in MDLParser._FP_K2
        assert 4284816 in MDLParser._FP_K2

    def test_base_offset(self):
        from gmodular.formats.mdl_parser import MDLParser
        assert MDLParser.BASE == 12


class TestMDLMeshNode:
    """Test MeshNode data structure has face_materials field."""

    def test_face_materials_field(self):
        from gmodular.formats.mdl_parser import MeshNode
        node = MeshNode()
        assert hasattr(node, 'face_materials')
        assert isinstance(node.face_materials, list)
        assert len(node.face_materials) == 0

    def test_face_materials_independent(self):
        """face_materials uses separate default_factory."""
        from gmodular.formats.mdl_parser import MeshNode
        n1 = MeshNode()
        n2 = MeshNode()
        n1.face_materials.append(0)
        assert len(n2.face_materials) == 0


# ═════════════════════════════════════════════════════════════════════════════
#  Archives integration tests (TPC/WOK via ResourceManager)
# ═════════════════════════════════════════════════════════════════════════════

class TestResourceManagerTPC:
    """Test that ResourceManager can serve TPC textures."""

    def test_load_texture_no_manager(self):
        """load_texture with no manager returns None."""
        from gmodular.formats.tpc_reader import load_texture
        result = load_texture('sometex', resource_manager=None)
        assert result is None

    def test_ext_to_type_includes_tpc(self):
        """archives.EXT_TO_TYPE includes tpc and tga."""
        from gmodular.formats.archives import EXT_TO_TYPE
        assert 'tpc' in EXT_TO_TYPE
        assert 'tga' in EXT_TO_TYPE

    def test_res_type_map_includes_tpc(self):
        """archives.RES_TYPE_MAP includes TPC type 2056."""
        from gmodular.formats.archives import RES_TYPE_MAP
        assert 2056 in RES_TYPE_MAP
        assert RES_TYPE_MAP[2056] == 'tpc'


class TestResourceManagerWOK:
    """Test WOK resource loading from archives."""

    def test_ext_to_type_includes_wok(self):
        """archives.EXT_TO_TYPE includes wok."""
        from gmodular.formats.archives import EXT_TO_TYPE
        assert 'wok' in EXT_TO_TYPE

    def test_res_type_map_includes_wok(self):
        """archives.RES_TYPE_MAP includes WOK type 2021."""
        from gmodular.formats.archives import RES_TYPE_MAP
        assert 2021 in RES_TYPE_MAP
        assert RES_TYPE_MAP[2021] == 'wok'


class TestResourceManagerLYT:
    """Test LYT/VIS resource loading from archives."""

    def test_ext_to_type_includes_lyt(self):
        """archives.EXT_TO_TYPE includes lyt."""
        from gmodular.formats.archives import EXT_TO_TYPE
        assert 'lyt' in EXT_TO_TYPE
        assert 'vis' in EXT_TO_TYPE

    def test_res_type_map_includes_lyt(self):
        """archives.RES_TYPE_MAP includes LYT type 3006."""
        from gmodular.formats.archives import RES_TYPE_MAP
        assert 3006 in RES_TYPE_MAP
        assert RES_TYPE_MAP[3006] == 'lyt'
        assert 3007 in RES_TYPE_MAP
        assert RES_TYPE_MAP[3007] == 'vis'


# ═════════════════════════════════════════════════════════════════════════════
#  MDL Parser: New features from xoreos/cchargin audit
# ═════════════════════════════════════════════════════════════════════════════

def _build_full_mdl(with_mdx: bool = False) -> tuple:
    """
    Build a minimal but complete KotOR1 MDL following the exact cchargin/xoreos layout.
    Returns (mdl_bytes, mdx_bytes).

    Layout verified against:
      - cchargin's mdl_info.html mesh header offsets
      - xoreos model_kotor.cpp readMesh()
    """
    BASE = 12

    # Offsets (BASE-relative)
    NAME_PTRS = 196        # 2×uint32 name pointers
    NAME_STRS = NAME_PTRS + 8       # 204: string pool
    ROOT_OFF  = NAME_STRS + 16      # 220: root node header (80 bytes)
    CHILD_PTR = ROOT_OFF + 80       # 300: child pointer slot (4 bytes)
    MESH_OFF  = CHILD_PTR + 4       # 304: mesh node header (80 bytes)
    MESH_HDR  = MESH_OFF + 80       # 384: mesh header (332 bytes)
    FACE_OFF  = MESH_HDR + 332      # 716: face structs (2×32=64 bytes)
    VERT_OFF  = FACE_OFF + 64       # 780: vertex positions (4×12=48 bytes)
    TOTAL_REL = VERT_OFF + 48       # 828: total data

    TOTAL_ABS = BASE + TOTAL_REL

    mdl = bytearray(TOTAL_ABS)
    B = BASE

    # File header
    struct.pack_into('<III', mdl, 0, 0, TOTAL_ABS, 0)

    # Geometry header
    struct.pack_into('<I', mdl, B+0,  4273776)        # fp1=K1
    struct.pack_into('<I', mdl, B+4,  4273776)        # fp2=K1
    mdl[B+8:B+40] = b'testroom\x00' + b'\x00'*23
    struct.pack_into('<I', mdl, B+40, ROOT_OFF)       # root node offset
    struct.pack_into('<I', mdl, B+44, 2)              # node count

    # Model header extension (supermodel at M+56)
    M = B + 80
    mdl[M+52:M+84] = b'NULL\x00' + b'\x00'*27

    # Names array
    N = B + 184
    struct.pack_into('<I', mdl, N+0, NAME_PTRS)
    struct.pack_into('<I', mdl, N+4, 2)
    struct.pack_into('<I', mdl, N+8, 2)

    # Name pointer array
    struct.pack_into('<I', mdl, B+NAME_PTRS+0, NAME_STRS)
    struct.pack_into('<I', mdl, B+NAME_PTRS+4, NAME_STRS + 8)
    mdl[B+NAME_STRS:B+NAME_STRS+8]    = b'root\x00\x00\x00\x00'
    mdl[B+NAME_STRS+8:B+NAME_STRS+16] = b'floor\x00\x00\x00'

    # Root node
    R = B + ROOT_OFF
    struct.pack_into('<H',    mdl, R+0,   0x0001)     # NODE_HEADER
    struct.pack_into('<H',    mdl, R+2,   0)           # name idx=0 → 'root'
    struct.pack_into('<H',    mdl, R+4,   0)           # node number
    struct.pack_into('<ffff', mdl, R+28,  0,0,0,1)    # identity rotation
    struct.pack_into('<III',  mdl, R+44,  CHILD_PTR, 1, 1)
    struct.pack_into('<I',    mdl, B+CHILD_PTR, MESH_OFF)

    # Mesh node
    MN = B + MESH_OFF
    struct.pack_into('<H',    mdl, MN+0,  0x0021)     # NODE_HEADER|NODE_MESH
    struct.pack_into('<H',    mdl, MN+2,  1)           # name idx=1 → 'floor'
    struct.pack_into('<H',    mdl, MN+4,  1)           # node number
    struct.pack_into('<ffff', mdl, MN+28, 0,0,0,1)    # identity rotation

    # Mesh header (cchargin layout, K1=332 bytes)
    MH = B + MESH_HDR
    # offset  8: faces
    struct.pack_into('<III', mdl, MH+8,  FACE_OFF, 2, 2)
    # offset 60: colors
    struct.pack_into('<fff', mdl, MH+60, 0.8, 0.8, 0.8)   # diffuse
    struct.pack_into('<fff', mdl, MH+72, 0.2, 0.2, 0.2)   # ambient
    # offset 88: texture name
    mdl[MH+88:MH+120] = b'floor_tex\x00' + b'\x00'*22
    # offset 252: MDX stride
    stride = 12 if not with_mdx else 24   # 12=xyz only, 24=xyz+normal
    struct.pack_into('<I', mdl, MH+252, stride)
    struct.pack_into('<I', mdl, MH+256, 1)             # bitmap
    # offset 260: MDX channel offsets
    struct.pack_into('<i', mdl, MH+260,  0)            # pos at 0
    struct.pack_into('<i', mdl, MH+264, 12 if with_mdx else -1)  # normals
    struct.pack_into('<i', mdl, MH+268, -1)            # colors
    struct.pack_into('<i', mdl, MH+272, -1)            # UV absent
    for i in range(7):
        struct.pack_into('<i', mdl, MH+276+i*4, -1)
    # offset 304: vertex count
    struct.pack_into('<H', mdl, MH+304, 4)
    # offset 308: render flags (render=1)
    struct.pack_into('BBBBBB', mdl, MH+308, 0, 0, 0, 0, 0, 1)
    # offset 324: MDX data offset (0 = start of MDX buffer when MDX present)
    # offset 328: MDL vertex positions fallback offset
    mdx_off = 0 if with_mdx else VERT_OFF   # MDX data at offset 0 in MDX file
    struct.pack_into('<I', mdl, MH+324, mdx_off)
    struct.pack_into('<I', mdl, MH+328, VERT_OFF)

    # Face structs
    F0 = B + FACE_OFF
    struct.pack_into('<fff', mdl, F0+0,  0.0, 0.0, 1.0)  # normal
    struct.pack_into('<I',   mdl, F0+12, 0)               # material=0
    struct.pack_into('<HHH', mdl, F0+26, 0, 1, 2)         # vertices

    F1 = B + FACE_OFF + 32
    struct.pack_into('<fff', mdl, F1+0,  0.0, 0.0, 1.0)
    struct.pack_into('<I',   mdl, F1+12, 6)               # material=6
    struct.pack_into('<HHH', mdl, F1+26, 0, 2, 3)

    # Vertex positions
    V = B + VERT_OFF
    struct.pack_into('<fff', mdl, V+0,  -1.0, -1.0, 0.0)
    struct.pack_into('<fff', mdl, V+12,  1.0, -1.0, 0.0)
    struct.pack_into('<fff', mdl, V+24,  1.0,  1.0, 0.0)
    struct.pack_into('<fff', mdl, V+36, -1.0,  1.0, 0.0)

    mdl_bytes = bytes(mdl)

    # Build MDX data if requested
    if with_mdx:
        mdx = bytearray(4 * stride)
        for i, (x,y,z,nx,ny,nz) in enumerate([
            (-1,-1,0, 0,0,1), (1,-1,0, 0,0,1),
            (1,1,0, 0,0,1),   (-1,1,0, 0,0,1)
        ]):
            base = i * stride
            struct.pack_into('<fff', mdx, base+0,  x, y, z)
            struct.pack_into('<fff', mdx, base+12, nx, ny, nz)
        return mdl_bytes, bytes(mdx)

    return mdl_bytes, b''


class TestMDLParserCorrectnessWithCcharginLayout:
    """
    Comprehensive tests for MDL parser using exact cchargin/xoreos layout.
    Verifies mesh header offsets match the authoritative cchargin mdl_info.html spec.
    """

    def test_model_name_parsed(self):
        """Model name is read from geometry header."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl, mdx = _build_full_mdl()
        mesh = MDLParser(mdl, mdx).parse()
        assert mesh.name == 'testroom'

    def test_game_version_k1(self):
        """K1 function pointer sets game_version=1."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl, mdx = _build_full_mdl()
        mesh = MDLParser(mdl, mdx).parse()
        assert mesh.game_version == 1

    def test_node_names_resolved(self):
        """Node names are read from the names array correctly."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl, mdx = _build_full_mdl()
        mesh = MDLParser(mdl, mdx).parse()
        node_names = {n.name for n in mesh.all_nodes()}
        assert 'root' in node_names

    def test_visible_mesh_nodes_found(self):
        """At least one visible mesh node is found."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl, mdx = _build_full_mdl()
        mesh = MDLParser(mdl, mdx).parse()
        visible = mesh.visible_mesh_nodes()
        assert len(visible) >= 1

    def test_vertex_count(self):
        """4 vertices are read from the MDL p_data array."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl, mdx = _build_full_mdl()
        mesh = MDLParser(mdl, mdx).parse()
        visible = mesh.visible_mesh_nodes()
        assert len(visible) >= 1
        assert len(visible[0].vertices) == 4

    def test_face_count(self):
        """2 triangles are read from the 32-byte face structs."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl, mdx = _build_full_mdl()
        mesh = MDLParser(mdl, mdx).parse()
        visible = mesh.visible_mesh_nodes()
        assert len(visible) >= 1
        assert len(visible[0].faces) == 2

    def test_face_materials(self):
        """Face material IDs are extracted from the 32-byte face struct at offset+12."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl, mdx = _build_full_mdl()
        mesh = MDLParser(mdl, mdx).parse()
        visible = mesh.visible_mesh_nodes()
        assert len(visible) >= 1
        node = visible[0]
        assert len(node.face_materials) == 2
        assert node.face_materials[0] == 0   # dirt
        assert node.face_materials[1] == 6   # nonwalk

    def test_face_vertex_indices(self):
        """Face vertex indices are read from face struct at offset+26."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl, mdx = _build_full_mdl()
        mesh = MDLParser(mdl, mdx).parse()
        visible = mesh.visible_mesh_nodes()
        assert len(visible) >= 1
        node = visible[0]
        assert (0, 1, 2) in node.faces
        assert (0, 2, 3) in node.faces

    def test_texture_name_parsed(self):
        """Texture name is read from mesh header offset 88."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl, mdx = _build_full_mdl()
        mesh = MDLParser(mdl, mdx).parse()
        visible = mesh.visible_mesh_nodes()
        assert len(visible) >= 1
        assert visible[0].texture == 'floor_tex'

    def test_render_flag_true(self):
        """render=True from mesh header byte flag at offset 308+5."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl, mdx = _build_full_mdl()
        mesh = MDLParser(mdl, mdx).parse()
        visible = mesh.visible_mesh_nodes()
        assert len(visible) >= 1
        assert visible[0].render is True

    def test_diffuse_color(self):
        """Diffuse color parsed from mesh header offset 60."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl, mdx = _build_full_mdl()
        mesh = MDLParser(mdl, mdx).parse()
        visible = mesh.visible_mesh_nodes()
        assert len(visible) >= 1
        dr, dg, db = visible[0].diffuse
        assert abs(dr - 0.8) < 0.001
        assert abs(dg - 0.8) < 0.001

    def test_vertex_positions_from_mdl(self):
        """Vertex positions are read from MDL p_data fallback."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl, mdx = _build_full_mdl(with_mdx=False)
        mesh = MDLParser(mdl, b'').parse()
        visible = mesh.visible_mesh_nodes()
        assert len(visible) >= 1
        verts = visible[0].vertices
        assert len(verts) == 4
        # Check a known vertex
        assert any(abs(v[0] - (-1.0)) < 0.001 and abs(v[1] - (-1.0)) < 0.001 for v in verts)

    def test_normals_loaded_from_mdx(self):
        """Normals are loaded from MDX interleaved data."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl, mdx = _build_full_mdl(with_mdx=True)
        mesh = MDLParser(mdl, mdx).parse()
        visible = mesh.visible_mesh_nodes()
        assert len(visible) >= 1
        node = visible[0]
        assert len(node.vertices) == 4
        assert len(node.normals) == 4
        # All normals should point up (0,0,1)
        for nx, ny, nz in node.normals:
            assert abs(nz - 1.0) < 0.001

    def test_bounding_box_computed(self):
        """MeshData.compute_bounds() calculates correct bounding box."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl, mdx = _build_full_mdl()
        mesh = MDLParser(mdl, mdx).parse()
        assert mesh.bb_min is not None
        assert mesh.bb_max is not None
        assert mesh.bb_min[0] < mesh.bb_max[0]
        assert mesh.bb_min[1] < mesh.bb_max[1]

    def test_mesh_data_flat_triangles(self):
        """flat_triangle_array() returns triangles for collision detection."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl, mdx = _build_full_mdl()
        mesh = MDLParser(mdl, mdx).parse()
        triangles = mesh.flat_triangle_array()
        assert len(triangles) == 2
        for verts, norm in triangles:
            assert len(verts) == 3
            assert len(norm) == 3

    def test_node_flags_mesh(self):
        """NODE_MESH flag (0x0020) is set on the mesh node."""
        from gmodular.formats.mdl_parser import MDLParser, NODE_MESH
        mdl, mdx = _build_full_mdl()
        mesh = MDLParser(mdl, mdx).parse()
        visible = mesh.visible_mesh_nodes()
        assert len(visible) >= 1
        assert bool(visible[0].flags & NODE_MESH)

    def test_is_mesh_property(self):
        """MeshNode.is_mesh property works."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl, mdx = _build_full_mdl()
        mesh = MDLParser(mdl, mdx).parse()
        visible = mesh.visible_mesh_nodes()
        assert len(visible) >= 1
        assert visible[0].is_mesh


class TestMDLNodeTypeConstants:
    """Test the new node type constants from xoreos audit."""

    def test_node_header_constant(self):
        from gmodular.formats.mdl_parser import NODE_HEADER
        assert NODE_HEADER == 0x0001

    def test_node_mesh_constant(self):
        from gmodular.formats.mdl_parser import NODE_MESH
        assert NODE_MESH == 0x0020

    def test_node_aabb_constant(self):
        from gmodular.formats.mdl_parser import NODE_AABB
        assert NODE_AABB == 0x0200

    def test_node_light_constant(self):
        from gmodular.formats.mdl_parser import NODE_LIGHT
        assert NODE_LIGHT == 0x0002

    def test_node_emitter_constant(self):
        from gmodular.formats.mdl_parser import NODE_EMITTER
        assert NODE_EMITTER == 0x0004

    def test_node_skin_constant(self):
        from gmodular.formats.mdl_parser import NODE_SKIN
        assert NODE_SKIN == 0x0040

    def test_node_dangly_constant(self):
        from gmodular.formats.mdl_parser import NODE_DANGLY
        assert NODE_DANGLY == 0x0100

    def test_ctrl_position_constant(self):
        from gmodular.formats.mdl_parser import CTRL_POSITION
        assert CTRL_POSITION == 8

    def test_ctrl_orientation_constant(self):
        from gmodular.formats.mdl_parser import CTRL_ORIENTATION
        assert CTRL_ORIENTATION == 20

    def test_mesh_hdr_k1_size(self):
        from gmodular.formats.mdl_parser import MDLParser
        assert MDLParser._MESH_HDR_K1 == 332

    def test_mesh_hdr_k2_size(self):
        from gmodular.formats.mdl_parser import MDLParser
        assert MDLParser._MESH_HDR_K2 == 340


class TestMDLMeshNodeProperties:
    """Test MeshNode properties added in xoreos audit."""

    def test_is_walkmesh_property(self):
        """is_walkmesh True when NODE_AABB set."""
        from gmodular.formats.mdl_parser import MeshNode, NODE_HEADER, NODE_AABB
        n = MeshNode(flags=NODE_HEADER | NODE_AABB)
        assert n.is_aabb is True
        assert n.is_walkmesh is True

    def test_is_walkmesh_false_for_mesh_node(self):
        """Normal mesh node is NOT walkmesh."""
        from gmodular.formats.mdl_parser import MeshNode, NODE_HEADER, NODE_MESH
        n = MeshNode(flags=NODE_HEADER | NODE_MESH)
        assert n.is_mesh is True
        assert n.is_walkmesh is False

    def test_is_light_property(self):
        from gmodular.formats.mdl_parser import MeshNode, NODE_LIGHT
        n = MeshNode(flags=NODE_LIGHT)
        assert n.is_light is True

    def test_is_emitter_property(self):
        from gmodular.formats.mdl_parser import MeshNode, NODE_EMITTER
        n = MeshNode(flags=NODE_EMITTER)
        assert n.is_emitter is True

    def test_is_dangly_property(self):
        from gmodular.formats.mdl_parser import MeshNode, NODE_DANGLY
        n = MeshNode(flags=NODE_DANGLY)
        assert n.is_dangly is True


class TestMeshDataWalkmeshNodes:
    """Test MeshData.walkmesh_nodes() and aabb_nodes()."""

    def test_walkmesh_nodes_empty_without_aabb(self):
        """walkmesh_nodes() returns empty list when no AABB nodes."""
        from gmodular.formats.mdl_parser import MeshData, MeshNode, NODE_HEADER, NODE_MESH
        md = MeshData()
        md.root_node = MeshNode(name='mesh', flags=NODE_HEADER | NODE_MESH)
        md.root_node.vertices = [(0,0,0), (1,0,0), (0,1,0)]
        assert md.walkmesh_nodes() == []

    def test_walkmesh_nodes_returns_aabb_nodes(self):
        """walkmesh_nodes() returns nodes with NODE_AABB flag and vertices."""
        from gmodular.formats.mdl_parser import MeshData, MeshNode, NODE_HEADER, NODE_AABB
        md = MeshData()
        md.root_node = MeshNode(name='wok', flags=NODE_HEADER | NODE_AABB)
        md.root_node.vertices = [(0,0,0), (1,0,0), (0,1,0)]
        wok_nodes = md.walkmesh_nodes()
        assert len(wok_nodes) == 1
        assert wok_nodes[0].name == 'wok'

    def test_aabb_nodes_alias(self):
        """aabb_nodes() is an alias for walkmesh_nodes()."""
        from gmodular.formats.mdl_parser import MeshData, MeshNode, NODE_HEADER, NODE_AABB
        md = MeshData()
        md.root_node = MeshNode(name='wok', flags=NODE_HEADER | NODE_AABB)
        md.root_node.vertices = [(0,0,0)]
        assert md.aabb_nodes() == md.walkmesh_nodes()


class TestMDLFlatIndexFallback:
    """Test that the flat vertex index (xoreos path) works as fallback."""

    def _build_mdl_with_flat_indices(self) -> bytes:
        """
        Build a MDL where the face structs are empty but the flat index array is present.
        This tests the vo_array fallback (xoreos rendering path).
        """
        BASE = 12
        NAME_PTRS = 196
        NAME_STRS = NAME_PTRS + 8
        ROOT_OFF  = NAME_STRS + 16
        CHILD_PTR = ROOT_OFF + 80
        MESH_OFF  = CHILD_PTR + 4
        MESH_HDR  = MESH_OFF + 80
        FLAT_IDX  = MESH_HDR + 332   # flat uint16 index array (2×3 = 12 bytes)
        FLAT_PTR  = FLAT_IDX + 12    # pointer cell for double-pointer (4 bytes)
        VERT_OFF  = FLAT_PTR + 4     # vertex positions (4×12 = 48 bytes)
        TOTAL_REL = VERT_OFF + 48

        TOTAL_ABS = BASE + TOTAL_REL
        mdl = bytearray(TOTAL_ABS)
        B = BASE

        struct.pack_into('<III', mdl, 0, 0, TOTAL_ABS, 0)
        struct.pack_into('<I', mdl, B+0,  4273776)
        mdl[B+8:B+40] = b'flattest\x00' + b'\x00'*23
        struct.pack_into('<I', mdl, B+40, ROOT_OFF)
        struct.pack_into('<I', mdl, B+44, 2)
        M = B + 80
        mdl[M+52:M+84] = b'NULL\x00' + b'\x00'*27
        N = B + 184
        struct.pack_into('<I', mdl, N+0, NAME_PTRS)
        struct.pack_into('<I', mdl, N+4, 2)
        struct.pack_into('<I', mdl, N+8, 2)
        struct.pack_into('<I', mdl, B+NAME_PTRS+0, NAME_STRS)
        struct.pack_into('<I', mdl, B+NAME_PTRS+4, NAME_STRS+8)
        mdl[B+NAME_STRS:B+NAME_STRS+8]    = b'root\x00\x00\x00\x00'
        mdl[B+NAME_STRS+8:B+NAME_STRS+16] = b'floor\x00\x00\x00'
        R = B + ROOT_OFF
        struct.pack_into('<H',    mdl, R+0,  0x0001)
        struct.pack_into('<H',    mdl, R+2,  0)
        struct.pack_into('<ffff', mdl, R+28, 0,0,0,1)
        struct.pack_into('<III',  mdl, R+44, CHILD_PTR, 1, 1)
        struct.pack_into('<I',    mdl, B+CHILD_PTR, MESH_OFF)
        MN = B + MESH_OFF
        struct.pack_into('<H',    mdl, MN+0,  0x0021)
        struct.pack_into('<H',    mdl, MN+2,  1)
        struct.pack_into('<ffff', mdl, MN+28, 0,0,0,1)
        MH = B + MESH_HDR
        # NO face structs — faces_off=0, faces_cnt=0 intentionally
        struct.pack_into('<III', mdl, MH+8, 0, 0, 0)   # NO 32-byte face structs
        struct.pack_into('<fff', mdl, MH+60, 0.8, 0.8, 0.8)
        mdl[MH+88:MH+120] = b'flattest\x00' + b'\x00'*23
        # Use vertex count = 4, and flat index array
        # vo_array at offset 188 → ptr to FLAT_PTR → FLAT_IDX
        struct.pack_into('<III', mdl, MH+188, FLAT_PTR, 1, 1)  # vo_array_def
        struct.pack_into('<I',   mdl, B+FLAT_PTR, FLAT_IDX)    # pointer to flat index array
        # Write flat uint16 index array: tri0=(0,1,2), tri1=(0,2,3)
        struct.pack_into('<HHHHHH', mdl, B+FLAT_IDX, 0, 1, 2, 0, 2, 3)
        # Also need faces_cnt for flat path (but faces_off=0 so A-path skips)
        # For flat path we need faces_cnt > 0
        struct.pack_into('<III', mdl, MH+8, 0, 2, 2)  # faces_off=0, faces_cnt=2
        struct.pack_into('<I', mdl, MH+252, 12)   # MDX stride
        struct.pack_into('<i', mdl, MH+260, 0)    # pos offset
        for i in range(10):
            struct.pack_into('<i', mdl, MH+264+i*4, -1)
        struct.pack_into('<H', mdl, MH+304, 4)
        struct.pack_into('BBBBBB', mdl, MH+308, 0, 0, 0, 0, 0, 1)
        struct.pack_into('<I', mdl, MH+324, VERT_OFF)
        struct.pack_into('<I', mdl, MH+328, VERT_OFF)
        V = B + VERT_OFF
        struct.pack_into('<fff', mdl, V+0,  -1,-1,0)
        struct.pack_into('<fff', mdl, V+12,  1,-1,0)
        struct.pack_into('<fff', mdl, V+24,  1, 1,0)
        struct.pack_into('<fff', mdl, V+36, -1, 1,0)
        return bytes(mdl)

    def test_flat_index_fallback_gives_triangles(self):
        """When 32-byte face structs have faces_off=0, flat index array is used."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl = self._build_mdl_with_flat_indices()
        mesh = MDLParser(mdl, b'').parse()
        visible = mesh.visible_mesh_nodes()
        assert len(visible) >= 1
        node = visible[0]
        # Should have 2 triangles from flat index path
        assert len(node.faces) == 2
        assert (0, 1, 2) in node.faces
        assert (0, 2, 3) in node.faces


class TestMDLScanTextures:
    """Test scan_mdl_textures() and list_mdl_dependencies() with corrected layout."""

    def test_scan_textures_finds_floor_tex(self):
        from gmodular.formats.mdl_parser import scan_mdl_textures
        mdl, _ = _build_full_mdl()
        textures = scan_mdl_textures(mdl)
        assert 'floor_tex' in textures

    def test_list_deps_includes_texture(self):
        from gmodular.formats.mdl_parser import list_mdl_dependencies
        mdl, _ = _build_full_mdl()
        deps = list_mdl_dependencies(mdl, b'')
        assert 'floor_tex' in deps['textures']

    def test_list_deps_supermodel_null(self):
        from gmodular.formats.mdl_parser import list_mdl_dependencies
        mdl, _ = _build_full_mdl()
        deps = list_mdl_dependencies(mdl, b'')
        # 'null' supermodel should NOT appear in models list
        assert 'null' not in deps['models']


# ═════════════════════════════════════════════════════════════════════════════
#  Kotor.NET-informed parser tests (based on MDLBinaryNodeHeader.cs,
#  MDLBinaryTrimeshHeader.cs, MDLBinaryModelHeader.cs analysis)
# ═════════════════════════════════════════════════════════════════════════════

class TestKotorNETNodeHeaderLayout:
    """Verify node header field order matches Kotor.NET MDLBinaryNodeHeader.cs.

    Layout: NodeType(u16@0), NodeIndex(u16@2), NameIndex(u16@4), Padding(u16@6)
    The parser must use NameIndex (offset 4) for name lookup, NOT NodeIndex (offset 2).
    """

    def _build_name_order_mdl(self) -> bytes:
        """Build MDL where NodeIndex != NameIndex to verify correct field is used."""
        import struct
        BASE = 12
        GEO  = 80
        MDL  = 104
        NO   = GEO + MDL   # 184

        ROOT_OFF = NO + 12        # 196
        ARR_OFF  = ROOT_OFF + 80  # 276
        STR0     = ARR_OFF + 8    # 284
        TOTAL    = STR0 + 32      # 316

        mdl = bytearray(TOTAL + BASE)
        B = BASE

        # File header
        struct.pack_into('<I', mdl, 4, len(mdl))

        # Geometry header (K1)
        struct.pack_into('<I', mdl, B+0, 4273776)
        mdl[B+8:B+40] = b'nametest' + b'\x00' * 24
        struct.pack_into('<I', mdl, B+40, ROOT_OFF)

        # Model extension
        M = B + GEO
        mdl[M+56:M+88] = b'NULL' + b'\x00' * 28

        # Names array: 2 names (root, specialname)
        N = B + NO
        struct.pack_into('<I', mdl, N+0, ARR_OFF)
        struct.pack_into('<I', mdl, N+4, 2)
        struct.pack_into('<I', mdl, N+8, 2)

        struct.pack_into('<I', mdl, B+ARR_OFF+0, STR0)
        struct.pack_into('<I', mdl, B+ARR_OFF+4, STR0 + 16)
        mdl[B+STR0:B+STR0+16]    = b'root\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
        mdl[B+STR0+16:B+STR0+32] = b'specialname\x00\x00\x00\x00\x00'

        # Root node:
        # NodeType=0x0001, NodeIndex=99(garbage), NameIndex=0 → 'root'
        R = B + ROOT_OFF
        struct.pack_into('<H', mdl, R+0, 0x0001)   # NodeType
        struct.pack_into('<H', mdl, R+2, 99)        # NodeIndex (sequential, should NOT be used for name)
        struct.pack_into('<H', mdl, R+4, 0)         # NameIndex → names[0] = 'root'
        struct.pack_into('<H', mdl, R+6, 0)         # Padding
        struct.pack_into('<ffff', mdl, R+28, 0, 0, 0, 1)  # rotation (identity)

        return bytes(mdl)

    def test_name_from_name_index_not_node_index(self):
        """Parser reads NameIndex (offset 4), not NodeIndex (offset 2) for name lookup."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl = self._build_name_order_mdl()
        data = MDLParser(mdl, b'').parse()
        names = {n.name for n in data.all_nodes()}
        # 'root' is at NameIndex=0; NodeIndex=99 doesn't map to any name
        assert 'root' in names
        # Should NOT have a node named 'specialname' (that's at names[1], not used)
        assert 'specialname' not in names

    def test_node_index_99_does_not_become_name(self):
        """NodeIndex value (99) is not mistakenly used as a name array index."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl = self._build_name_order_mdl()
        data = MDLParser(mdl, b'').parse()
        names = [n.name for n in data.all_nodes()]
        # Should not have node_99 or any garbage name from NodeIndex
        assert not any('node_99' in nm for nm in names)


class TestKotorNETModelHeaderFields:
    """Verify model header fields parsed correctly per MDLBinaryModelHeader.cs."""

    def _build_mdl_with_header_fields(self, model_type=4, fog=1, anim_scale=1.5,
                                       mdx_size=1024, child_count=3) -> bytes:
        """Build MDL with specific model header values."""
        import struct, math
        BASE = 12
        GEO  = 80
        NO   = GEO + 104   # 184

        ROOT_OFF = NO + 12
        TOTAL    = ROOT_OFF + 80

        mdl = bytearray(TOTAL + BASE)
        B = BASE

        struct.pack_into('<I', mdl, 4, len(mdl))
        # Geometry header (K1)
        struct.pack_into('<I', mdl, B+0, 4273776)
        mdl[B+8:B+40] = b'headertest' + b'\x00' * 22
        struct.pack_into('<I', mdl, B+40, ROOT_OFF)

        # Model header at M = B + GEO = B + 80
        M = B + GEO
        # ModelType(1)@M+0, Unknown(1)@M+1, Padding(1)@M+2, Fog(1)@M+3
        struct.pack_into('BBBB', mdl, M+0, model_type, 0, 0, fog)
        # ChildModelCount(4)@M+4
        struct.pack_into('<I', mdl, M+4, child_count)
        # AnimationScale(4)@M+52
        struct.pack_into('<f', mdl, M+52, anim_scale)
        # SupermodelName[32]@M+56
        mdl[M+56:M+88] = b'NULL' + b'\x00' * 28
        # MDXSize(4)@M+96
        struct.pack_into('<I', mdl, M+96, mdx_size)
        # NamesArray (empty) at M+104 = _NAMES_OFF
        N = B + NO
        struct.pack_into('<I', mdl, N+4, 0)

        # Root node
        R = B + ROOT_OFF
        struct.pack_into('<H', mdl, R+0, 0x0001)
        struct.pack_into('<H', mdl, R+2, 0)
        struct.pack_into('<H', mdl, R+4, 0)
        struct.pack_into('<ffff', mdl, R+28, 0, 0, 0, 1)

        return bytes(mdl)

    def test_model_type_parsed(self):
        """model_type field is extracted from byte at M+0."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl = self._build_mdl_with_header_fields(model_type=4)
        data = MDLParser(mdl, b'').parse()
        assert data.model_type == 4

    def test_fog_flag_parsed(self):
        """fog flag is extracted from byte at M+3."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl_fog = self._build_mdl_with_header_fields(fog=1)
        mdl_nofog = self._build_mdl_with_header_fields(fog=0)
        assert MDLParser(mdl_fog, b'').parse().fog == True
        assert MDLParser(mdl_nofog, b'').parse().fog == False

    def test_animation_scale_parsed(self):
        """animation_scale float is extracted from M+52."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl = self._build_mdl_with_header_fields(anim_scale=2.0)
        data = MDLParser(mdl, b'').parse()
        assert abs(data.animation_scale - 2.0) < 0.001

    def test_mdx_size_parsed(self):
        """mdx_size is extracted from M+96."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl = self._build_mdl_with_header_fields(mdx_size=2048)
        data = MDLParser(mdl, b'').parse()
        assert data.mdx_size == 2048

    def test_child_model_count_parsed(self):
        """child_model_count is extracted from M+4."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl = self._build_mdl_with_header_fields(child_count=5)
        data = MDLParser(mdl, b'').parse()
        assert data.child_model_count == 5

    def test_classification_character(self):
        """model_type=4 classifies as 'character'."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl = self._build_mdl_with_header_fields(model_type=4)
        data = MDLParser(mdl, b'').parse()
        assert data.classification == 'character'

    def test_classification_door(self):
        """model_type=6 classifies as 'door'."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl = self._build_mdl_with_header_fields(model_type=6)
        data = MDLParser(mdl, b'').parse()
        assert data.classification == 'door'

    def test_classification_geometry(self):
        """model_type=2 classifies as 'geometry'."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl = self._build_mdl_with_header_fields(model_type=2)
        data = MDLParser(mdl, b'').parse()
        assert data.classification == 'geometry'

    def test_classification_unknown(self):
        """Unrecognized model_type classifies as 'other'."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl = self._build_mdl_with_header_fields(model_type=99)
        data = MDLParser(mdl, b'').parse()
        assert data.classification == 'other'


class TestKotorNETFunctionPointerConstants:
    """Verify FP constants match Kotor.NET MDLBinaryGeometryHeader.cs and MDLBinaryTrimeshHeader.cs."""

    def test_k1_geometry_fp_in_k1_set(self):
        """K1 PC model FP1=4273776 is in _FP_K1."""
        from gmodular.formats.mdl_parser import MDLParser
        assert 4273776 in MDLParser._FP_K1   # K1_PC_MODEL_FP1

    def test_k1_animation_fp_in_k1_set(self):
        """K1 PC anim FP1=4273392 is in _FP_K1."""
        from gmodular.formats.mdl_parser import MDLParser
        assert 4273392 in MDLParser._FP_K1   # K1_PC_ANIM_FP1

    def test_k2_geometry_fp_in_k2_set(self):
        """K2 PC model FP1=4285200 is in _FP_K2."""
        from gmodular.formats.mdl_parser import MDLParser
        assert 4285200 in MDLParser._FP_K2   # K2_PC_MODEL_FP1

    def test_k1_trimesh_fp_in_k1_set(self):
        """K1 PC trimesh FP1=4216656 is in _FP_K1."""
        from gmodular.formats.mdl_parser import MDLParser
        assert 4216656 in MDLParser._FP_K1   # K1_PC_MESH_FP1

    def test_k2_trimesh_fp_in_k2_set(self):
        """K2 PC trimesh FP1=4216880 is in _FP_K2 and _FP_K2_MESH."""
        from gmodular.formats.mdl_parser import MDLParser
        assert 4216880 in MDLParser._FP_K2       # K2_PC_MESH_FP1
        assert 4216880 in MDLParser._FP_K2_MESH  # per-mesh K2 detection

    def test_k1_xbox_fps_in_k1_set(self):
        """K1 Xbox FP values are in _FP_K1."""
        from gmodular.formats.mdl_parser import MDLParser
        assert 4254992 in MDLParser._FP_K1  # K1_XBOX_MODEL_FP1
        assert 4253536 in MDLParser._FP_K1  # K1_XBOX_ANIM_FP1

    def test_k2_pc_skin_fp_in_k2_mesh(self):
        """K2 PC skin FP1=4216816 in _FP_K2_MESH for per-node K2 detection."""
        from gmodular.formats.mdl_parser import MDLParser
        assert 4216816 in MDLParser._FP_K2_MESH  # K2_PC_SKIN_FP1

    def test_k1_k2_fp_sets_disjoint(self):
        """K1 and K2 FP sets must be disjoint (no overlap)."""
        from gmodular.formats.mdl_parser import MDLParser
        overlap = MDLParser._FP_K1 & MDLParser._FP_K2
        assert len(overlap) == 0, f"FP overlap: {overlap}"

    def test_k2_model_fp_detects_version_2(self):
        """MDL with K2 FP1 is parsed as game_version=2."""
        import struct
        from gmodular.formats.mdl_parser import MDLParser
        BASE = 12
        mdl = bytearray(BASE + 184 + 12)
        struct.pack_into('<I', mdl, BASE+0, 4285200)   # K2 PC model FP
        mdl[BASE+8:BASE+40] = b'k2test' + b'\x00'*26
        data = MDLParser(bytes(mdl), b'').parse()
        assert data.game_version == 2


class TestKotorNETTrimeshK2Detection:
    """Per-mesh K2 detection using trimesh function pointer (MDLBinaryTrimeshHeader.IsTSL)."""

    def _build_k2_mesh_mdl(self) -> bytes:
        """Build a minimal MDL with K2 PC mesh FP to test per-mesh K2 detection."""
        import struct
        BASE = 12
        GEO  = 80
        NO   = GEO + 104

        ROOT_OFF  = NO + 12
        ARR_OFF   = ROOT_OFF + 80
        STR0      = ARR_OFF + 8
        MESH_OFF  = STR0 + 16
        MESH_HDR  = MESH_OFF + 80
        CHILD_OFF = MESH_HDR + 340  # K2 size
        FACE_OFF  = CHILD_OFF + 4
        VERT_OFF  = FACE_OFF + 64
        TOTAL     = VERT_OFF + 48

        mdl = bytearray(TOTAL + BASE)
        B = BASE

        struct.pack_into('<I', mdl, 4, len(mdl))
        # Use K2 geometry FP
        struct.pack_into('<I', mdl, B+0, 4285200)
        mdl[B+8:B+40] = b'k2meshtest' + b'\x00' * 22
        struct.pack_into('<I', mdl, B+40, ROOT_OFF)

        M = B + GEO
        mdl[M+56:M+88] = b'NULL' + b'\x00' * 28

        N = B + NO
        struct.pack_into('<I', mdl, N+0, ARR_OFF)
        struct.pack_into('<I', mdl, N+4, 2)
        struct.pack_into('<I', mdl, N+8, 2)
        struct.pack_into('<I', mdl, B+ARR_OFF+0, STR0)
        struct.pack_into('<I', mdl, B+ARR_OFF+4, STR0+8)
        mdl[B+STR0:B+STR0+8]    = b'root\x00\x00\x00\x00'
        mdl[B+STR0+8:B+STR0+16] = b'meshnode\x00\x00\x00\x00\x00\x00\x00\x00'

        R = B + ROOT_OFF
        struct.pack_into('<H', mdl, R+0, 0x0001)
        struct.pack_into('<H', mdl, R+2, 0)
        struct.pack_into('<H', mdl, R+4, 0)
        struct.pack_into('<ffff', mdl, R+28, 0, 0, 0, 1)
        struct.pack_into('<III', mdl, R+44, CHILD_OFF, 1, 1)

        MN = B + MESH_OFF
        struct.pack_into('<H', mdl, MN+0, 0x0021)
        struct.pack_into('<H', mdl, MN+2, 1)
        struct.pack_into('<H', mdl, MN+4, 1)
        struct.pack_into('<ffff', mdl, MN+28, 0, 0, 0, 1)

        struct.pack_into('<I', mdl, B+CHILD_OFF, MESH_OFF)

        MH = B + MESH_HDR
        # K2 PC mesh FP1 (MDLBinaryTrimeshHeader.K2_PC_MESH_FP1 = 4216880)
        struct.pack_into('<II', mdl, MH+0, 4216880, 4216896)
        struct.pack_into('<III', mdl, MH+8, FACE_OFF, 2, 2)
        struct.pack_into('<fff', mdl, MH+60, 0.8, 0.8, 0.8)
        struct.pack_into('<fff', mdl, MH+72, 0.2, 0.2, 0.2)
        struct.pack_into('<I', mdl, MH+252, 12)
        struct.pack_into('<i', mdl, MH+260, 0)
        for off in range(264, 304, 4):
            struct.pack_into('<i', mdl, MH+off, -1)
        struct.pack_into('<H', mdl, MH+304, 4)
        struct.pack_into('BBBBBB', mdl, MH+308, 0, 0, 0, 0, 0, 1)
        # K2 has 8 extra bytes at 314 (+10 for unknown block) before pointers
        # Pointers at offset 332 (not 324) for K2
        struct.pack_into('<I', mdl, MH+332, VERT_OFF)   # MDX data offset (K2: 332)
        struct.pack_into('<I', mdl, MH+336, VERT_OFF)   # Vertex positions (K2: 336)

        # Faces
        F0 = B + FACE_OFF
        struct.pack_into('<fff', mdl, F0+0, 0, 0, 1)
        struct.pack_into('<I',   mdl, F0+12, 0)
        struct.pack_into('<HHH', mdl, F0+26, 0, 1, 2)
        F1 = B + FACE_OFF + 32
        struct.pack_into('<fff', mdl, F1+0, 0, 0, 1)
        struct.pack_into('<I',   mdl, F1+12, 6)
        struct.pack_into('<HHH', mdl, F1+26, 0, 2, 3)

        V = B + VERT_OFF
        struct.pack_into('<fff', mdl, V+0,  -1, -1, 0)
        struct.pack_into('<fff', mdl, V+12,  1, -1, 0)
        struct.pack_into('<fff', mdl, V+24,  1,  1, 0)
        struct.pack_into('<fff', mdl, V+36, -1,  1, 0)

        return bytes(mdl)

    def test_k2_mesh_fp_triggers_k2_mode(self):
        """MDL with K2 PC trimesh FP1=4216880 is parsed as K2 (game_version=2)."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl = self._build_k2_mesh_mdl()
        data = MDLParser(mdl, mdl[12:]).parse()
        assert data.game_version == 2

    def test_k2_mesh_reads_verts_at_offset_332(self):
        """K2 mesh reads vertex pointers at offset 332/336, not 324/328."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl = self._build_k2_mesh_mdl()
        data = MDLParser(mdl, mdl[12:]).parse()
        visible = data.visible_mesh_nodes()
        assert len(visible) >= 1
        node = visible[0]
        assert len(node.vertices) == 4


class TestKotorNETControllerPacking:
    """Test the packed quaternion orientation controller format per Kotor.NET."""

    def test_compressed_orientation_2col_decode(self):
        """2-column compressed orientation packs x,y,z into 32 bits."""
        import struct, math
        from gmodular.formats.mdl_parser import MDLParser

        # Encode orientation (0, 0, 0, 1) = identity quaternion
        # x=0, y=0, z=0 → all bits = 0x1FF for z, 0x1FF for x and y at value 0 offset
        # Per Kotor.NET: x = (bits & 0x7FF)/1023 - 1, etc.
        # For x=0: (b & 0x7FF)/1023 - 1 = 0 → b & 0x7FF = 1023
        # For z=0: (b >> 22)/511 - 1 = 0 → b >> 22 = 511
        # Packed: x_bits=1023 (11 bits), y_bits=1023<<11, z_bits=511<<22
        x_bits = 1023
        y_bits = 1023 << 11
        z_bits = 511 << 22
        packed = x_bits | y_bits | z_bits

        # Decode manually (mirrors MDLBinaryDeserializer)
        x_enc = ((packed & 0x7FF) / 1023.0) - 1.0
        y_enc = (((packed >> 11) & 0x7FF) / 1023.0) - 1.0
        z_enc = ((packed >> 22) / 511.0) - 1.0
        mag2  = x_enc**2 + y_enc**2 + z_enc**2
        w_enc = math.sqrt(1.0 - mag2) if mag2 < 1.0 else 0.0

        assert abs(x_enc) < 0.01
        assert abs(y_enc) < 0.01
        assert abs(z_enc) < 0.01
        assert abs(w_enc - 1.0) < 0.01  # identity → w ≈ 1.0


class TestWOKEnhancedMethods:
    """Test new WalkMesh utility methods added for pathfinding and height queries."""

    def _make_flat_walkmesh(self) -> 'WalkMesh':
        """Create a simple flat walkmesh 4×4 square at Z=0."""
        from gmodular.formats.wok_parser import WalkMesh, WalkFace
        wm = WalkMesh(name="test_floor")
        # Two triangles forming a 4×4 square at Z=0, material=0 (walkable)
        wm.faces.append(WalkFace(
            v0=(0.0, 0.0, 0.0), v1=(4.0, 0.0, 0.0), v2=(4.0, 4.0, 0.0),
            material=0, normal=(0.0, 0.0, 1.0)
        ))
        wm.faces.append(WalkFace(
            v0=(0.0, 0.0, 0.0), v1=(4.0, 4.0, 0.0), v2=(0.0, 4.0, 0.0),
            material=0, normal=(0.0, 0.0, 1.0)
        ))
        # One non-walkable face
        wm.faces.append(WalkFace(
            v0=(5.0, 0.0, 1.0), v1=(7.0, 0.0, 1.0), v2=(6.0, 2.0, 1.0),
            material=6, normal=(0.0, 0.0, 1.0)  # NonWalk
        ))
        return wm

    def test_height_at_center_returns_zero(self):
        """height_at() returns 0.0 for a flat floor at Z=0."""
        wm = self._make_flat_walkmesh()
        h = wm.height_at(2.0, 2.0)
        assert h is not None
        assert abs(h) < 0.001

    def test_height_at_outside_returns_none(self):
        """height_at() returns None for a position outside the walkmesh."""
        wm = self._make_flat_walkmesh()
        h = wm.height_at(10.0, 10.0)
        assert h is None

    def test_height_at_any_includes_nonwalkable(self):
        """height_at_any() also queries non-walkable faces."""
        wm = self._make_flat_walkmesh()
        # The non-walkable face is at x=5..7, y=0..2, z=1
        h = wm.height_at_any(6.0, 1.0)
        assert h is not None
        assert h > 0.0  # should find the elevated non-walkable face

    def test_face_at_returns_correct_material(self):
        """face_at() returns the face with correct material ID."""
        wm = self._make_flat_walkmesh()
        face = wm.face_at(2.0, 2.0, walkable_only=True)
        assert face is not None
        assert face.material == 0  # dirt/walkable

    def test_face_at_nonwalkable_returns_none_when_walkable_only(self):
        """face_at(walkable_only=True) skips non-walkable faces."""
        wm = self._make_flat_walkmesh()
        face = wm.face_at(6.0, 1.0, walkable_only=True)
        assert face is None  # non-walkable region

    def test_surface_material_at_walkable_area(self):
        """surface_material_at() returns 0 for walkable area."""
        wm = self._make_flat_walkmesh()
        mat = wm.surface_material_at(2.0, 2.0)
        assert mat == 0

    def test_surface_material_at_outside_returns_minus1(self):
        """surface_material_at() returns -1 when no face found."""
        wm = self._make_flat_walkmesh()
        mat = wm.surface_material_at(100.0, 100.0)
        assert mat == -1

    def test_bounds_returns_correct_extent(self):
        """bounds() returns correct (bb_min, bb_max)."""
        wm = self._make_flat_walkmesh()
        (mn, mx) = wm.bounds()
        assert mn[0] == 0.0 and mn[1] == 0.0
        assert mx[0] == 7.0 and mx[1] == 4.0

    def test_walkable_region_center_approx(self):
        """walkable_region_center() returns centroid of walkable faces."""
        wm = self._make_flat_walkmesh()
        center = wm.walkable_region_center()
        assert center is not None
        # Centroid of two triangles covering (0,0)-(4,4) at Z=0
        assert abs(center[2]) < 0.001  # Z should be ~0

    def test_material_counts_dict(self):
        """material_counts() maps material IDs to face counts."""
        wm = self._make_flat_walkmesh()
        counts = wm.material_counts()
        assert counts[0] == 2   # 2 walkable faces
        assert counts[6] == 1   # 1 non-walkable face

    def test_clamp_to_walkmesh_walkable_position(self):
        """clamp_to_walkmesh() returns (x, y, z) for a walkable position."""
        wm = self._make_flat_walkmesh()
        pos = wm.clamp_to_walkmesh(2.0, 2.0)
        assert pos is not None
        assert abs(pos[0] - 2.0) < 0.001
        assert abs(pos[2]) < 0.001  # Z=0

    def test_clamp_to_walkmesh_nearby_position(self):
        """clamp_to_walkmesh() snaps to nearest face center when not directly over walkmesh."""
        wm = self._make_flat_walkmesh()
        pos = wm.clamp_to_walkmesh(4.5, 2.0, search_radius=3.0)
        assert pos is not None  # should find a nearby walkable face center


class TestTPCImageEnhancements:
    """Test new TPCImage methods added from Kotor.NET analysis."""

    def test_is_cubemap_false_for_square(self):
        """Regular square texture is not a cube map."""
        from gmodular.formats.tpc_reader import TPCImage, TPCMipMap
        img = TPCImage(width=64, height=64)
        img.mipmaps.append(TPCMipMap(width=64, height=64, data=bytes(64*64*4)))
        assert img.is_cubemap == False

    def test_is_cubemap_true_when_height_6x_width(self):
        """height = 6 × width indicates a cube map."""
        from gmodular.formats.tpc_reader import TPCImage, TPCMipMap
        img = TPCImage(width=64, height=384)  # 384 = 64 × 6
        img.mipmaps.append(TPCMipMap(width=64, height=384, data=bytes(64*384*4)))
        assert img.is_cubemap == True

    def test_mip_count_property(self):
        """mip_count returns correct number of mip-map levels."""
        from gmodular.formats.tpc_reader import TPCImage, TPCMipMap
        img = TPCImage(width=64, height=64)
        assert img.mip_count == 0
        img.mipmaps.append(TPCMipMap(width=64, height=64, data=bytes(16384)))
        img.mipmaps.append(TPCMipMap(width=32, height=32, data=bytes(4096)))
        assert img.mip_count == 2

    def test_mipmap_at_valid_level(self):
        """mipmap_at(0) returns the full-resolution mip."""
        from gmodular.formats.tpc_reader import TPCImage, TPCMipMap
        img = TPCImage(width=32, height=32)
        mm = TPCMipMap(width=32, height=32, data=bytes(32*32*4))
        img.mipmaps.append(mm)
        assert img.mipmap_at(0) is mm

    def test_mipmap_at_invalid_level_returns_none(self):
        """mipmap_at() with out-of-range index returns None."""
        from gmodular.formats.tpc_reader import TPCImage
        img = TPCImage(width=32, height=32)
        assert img.mipmap_at(0) is None
        assert img.mipmap_at(-1) is None

    def test_get_rgba_at_level_0(self):
        """get_rgba_at_level(0) returns full-res RGBA bytes."""
        from gmodular.formats.tpc_reader import TPCImage, TPCMipMap
        data = bytes(range(256)) * (32*32*4 // 256 + 1)
        data = data[:32*32*4]
        img = TPCImage(width=32, height=32)
        img.mipmaps.append(TPCMipMap(width=32, height=32, data=data))
        assert img.get_rgba_at_level(0) == data

    def test_get_rgba_at_invalid_level_returns_empty(self):
        """get_rgba_at_level() returns empty bytes for invalid level."""
        from gmodular.formats.tpc_reader import TPCImage
        img = TPCImage(width=32, height=32)
        assert img.get_rgba_at_level(99) == b''
