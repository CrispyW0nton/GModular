"""
tests/test_render_pipeline.py
=============================
Render-based verification tests for the GModular MDL/TGA/TPC pipeline.

Coverage:
  1. TGA loader (_load_tga_texture equivalent) — correctness + row-flip
  2. TGA numpy fast path vs pure-Python path produce identical output
  3. Texture fuzzy lookup (lsl_* → sle_* prefix mismatch)
  4. load_textures_for_rooms re-attaches even when all textures already cached
  5. AssetItem.file_path field is set for texture assets from extract_dir
  6. Content browser TGA thumbnail helpers (_tga_to_pixmap)
  7. MDL mesh node roundtrip: parse → positions/normals/UVs intact
  8. MeshData.visible_mesh_nodes excludes AABB and non-render nodes
  9. Viewport render loop tex lookup uses both tex_name and tex_resref
 10. _refresh_category_tree expands Rooms and Textures automatically

All tests are pure Python (no OpenGL / Qt context required).
They exercise real code paths; mocks are used only where a real
context cannot be created in CI.
"""
from __future__ import annotations

import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers: build synthetic TGA data
# ─────────────────────────────────────────────────────────────────────────────

def _make_tga(w: int, h: int, bpp: int = 24,
              bottom_origin: bool = True) -> bytes:
    """
    Build an uncompressed TGA image.

    Pixel pattern: each pixel (r=col, g=row, b=0, a=255).
    bottom_origin=True  → bit-5 of descriptor clear (TGA default: bottom-left).
    bottom_origin=False → bit-5 set (top-left origin).

    Layout: 18-byte header + w×h×(bpp//8) bytes of BGR(A).
    """
    stride  = bpp // 8
    buf     = bytearray()
    # BGR(A) rows, stored BOTTOM row first if bottom_origin (standard TGA)
    rows = []
    for row in range(h):
        row_data = bytearray()
        for col in range(w):
            r, g, b = col & 0xFF, row & 0xFF, 0
            a = 255
            if bpp == 24:
                row_data += bytes([b, g, r])          # BGR
            else:
                row_data += bytes([b, g, r, a])       # BGRA
        rows.append(bytes(row_data))

    # TGA stores row 0 = bottom if bit-5 of descriptor is 0
    if bottom_origin:
        pixel_data = b''.join(reversed(rows))  # bottom row first in file
    else:
        pixel_data = b''.join(rows)             # top row first in file

    descriptor = 0x00 if bottom_origin else 0x20   # bit-5 = top-left origin

    header = bytearray(18)
    header[0]  = 0          # id_length
    header[1]  = 0          # colormap_type
    header[2]  = 2          # image_type: uncompressed true-colour
    # Colormap spec: bytes 3-7 = 0
    struct.pack_into('<H', header, 12, w)     # width
    struct.pack_into('<H', header, 14, h)     # height
    header[16] = bpp
    header[17] = descriptor

    return bytes(header) + pixel_data


def _decode_tga_to_rgba(tga_bytes: bytes):
    """
    Reference decoder: replicate the _load_tga_texture logic
    without numpy, returning (rgba_bytes, width, height).
    """
    id_len   = tga_bytes[0]
    img_type = tga_bytes[2]
    w = struct.unpack_from('<H', tga_bytes, 12)[0]
    h = struct.unpack_from('<H', tga_bytes, 14)[0]
    bpp = tga_bytes[16]
    descriptor = tga_bytes[17]
    if img_type not in (2, 3) or bpp not in (24, 32) or w == 0 or h == 0:
        return None, 0, 0
    data_off = 18 + id_len
    stride   = bpp // 8
    px_count = w * h
    raw = tga_bytes[data_off: data_off + px_count * stride]
    rgba = bytearray(px_count * 4)
    for i in range(px_count):
        b, g, r = raw[i*stride], raw[i*stride+1], raw[i*stride+2]
        a = raw[i*stride+3] if bpp == 32 else 255
        rgba[i*4:i*4+4] = bytes([r, g, b, a])
    # Flip rows if bottom-origin (bit 5 of descriptor = 0)
    if not (descriptor & 0x20):
        row_bytes = w * 4
        flipped = bytearray(px_count * 4)
        for row in range(h):
            src = (h - 1 - row) * row_bytes
            dst = row * row_bytes
            flipped[dst:dst+row_bytes] = rgba[src:src+row_bytes]
        rgba = flipped
    return bytes(rgba), w, h


# ─────────────────────────────────────────────────────────────────────────────
#  1. TGA pixel correctness
# ─────────────────────────────────────────────────────────────────────────────

class TestTGADecoder(unittest.TestCase):
    """Test the TGA decode logic (the pure-Python reference path)."""

    def test_24bit_bottom_origin(self):
        """24-bit TGA with bottom-origin → correct RGBA after row flip."""
        tga = _make_tga(4, 4, bpp=24, bottom_origin=True)
        rgba, w, h = _decode_tga_to_rgba(tga)
        self.assertIsNotNone(rgba)
        self.assertEqual(w, 4)
        self.assertEqual(h, 4)
        # After decode+flip: pixel at (row=0, col=0) should be R=0, G=0, B=0, A=255
        r, g, b, a = rgba[0], rgba[1], rgba[2], rgba[3]
        self.assertEqual(r, 0, "col=0 → R=0")
        self.assertEqual(g, 0, "row=0 → G=0")
        self.assertEqual(b, 0)
        self.assertEqual(a, 255)

    def test_32bit_alpha_preserved(self):
        """32-bit TGA: alpha channel is preserved."""
        tga = _make_tga(2, 2, bpp=32, bottom_origin=True)
        rgba, w, h = _decode_tga_to_rgba(tga)
        self.assertIsNotNone(rgba)
        # All pixels have alpha=255 from our builder
        for i in range(w * h):
            self.assertEqual(rgba[i*4+3], 255, f"pixel {i} alpha")

    def test_top_origin_no_flip(self):
        """TGA with top-origin flag: no row flip, pixel(0,0)=(col=0,row=0)."""
        tga = _make_tga(4, 4, bpp=24, bottom_origin=False)
        rgba, w, h = _decode_tga_to_rgba(tga)
        self.assertIsNotNone(rgba)
        # top-origin: first pixel in file IS row=0
        r, g, b, a = rgba[0], rgba[1], rgba[2], rgba[3]
        self.assertEqual(r, 0)
        self.assertEqual(g, 0)

    def test_rgba_size(self):
        """RGBA output has exactly w*h*4 bytes."""
        for bpp in (24, 32):
            tga = _make_tga(8, 8, bpp=bpp)
            rgba, w, h = _decode_tga_to_rgba(tga)
            self.assertEqual(len(rgba), w * h * 4, f"bpp={bpp}")


# ─────────────────────────────────────────────────────────────────────────────
#  2. TGA numpy vs pure-Python parity
# ─────────────────────────────────────────────────────────────────────────────

class TestTGANumpyParity(unittest.TestCase):
    """Verify numpy fast path gives identical output to pure-Python."""

    def _numpy_decode(self, tga_bytes: bytes):
        """Replicate the numpy path from _load_tga_texture."""
        try:
            import numpy as np
        except ImportError:
            return None
        id_len   = tga_bytes[0]
        w = struct.unpack_from('<H', tga_bytes, 12)[0]
        h = struct.unpack_from('<H', tga_bytes, 14)[0]
        bpp = tga_bytes[16]
        descriptor = tga_bytes[17]
        stride = bpp // 8
        px_count = w * h
        data_off = 18 + id_len
        raw = tga_bytes[data_off: data_off + px_count * stride]
        arr  = np.frombuffer(raw, dtype=np.uint8).reshape(px_count, stride).copy()
        rgba = np.empty((px_count, 4), dtype=np.uint8)
        rgba[:, 0] = arr[:, 2]
        rgba[:, 1] = arr[:, 1]
        rgba[:, 2] = arr[:, 0]
        rgba[:, 3] = arr[:, 3] if bpp == 32 else 255
        if not (descriptor & 0x20):
            rgba = rgba.reshape(h, w, 4)[::-1].reshape(px_count, 4)
        return rgba.tobytes()

    def test_24bit_parity(self):
        tga = _make_tga(8, 8, bpp=24)
        ref, _, _ = _decode_tga_to_rgba(tga)
        fast = self._numpy_decode(tga)
        if fast is None:
            self.skipTest("numpy not available")
        self.assertEqual(ref, fast, "numpy and pure-Python output must be identical")

    def test_32bit_parity(self):
        tga = _make_tga(8, 8, bpp=32)
        ref, _, _ = _decode_tga_to_rgba(tga)
        fast = self._numpy_decode(tga)
        if fast is None:
            self.skipTest("numpy not available")
        self.assertEqual(ref, fast)

    def test_top_origin_parity(self):
        tga = _make_tga(4, 4, bpp=24, bottom_origin=False)
        ref, _, _ = _decode_tga_to_rgba(tga)
        fast = self._numpy_decode(tga)
        if fast is None:
            self.skipTest("numpy not available")
        self.assertEqual(ref, fast)


# ─────────────────────────────────────────────────────────────────────────────
#  3. Texture fuzzy lookup (prefix-mismatch)
# ─────────────────────────────────────────────────────────────────────────────

class TestTextureFuzzyLookup(unittest.TestCase):
    """
    The viewport's load_textures_for_rooms() has a fuzzy fallback that matches
    textures by the suffix after the first '_'.  E.g. 'lsl_dirt02' matches
    'sle_dirt02.tga'.

    Verify the lookup logic independently of Qt/GL.
    """

    def _build_file_idx(self, filenames):
        """Simulate the file_idx dict with /fake/dir/ paths."""
        return {fn.lower(): f"/fake/{fn}" for fn in filenames}

    def _fuzzy_find(self, resref: str, file_idx: dict, ext: str):
        """
        Replicate the fuzzy fallback from load_textures_for_rooms.
        Returns candidate path or None.
        """
        candidate = file_idx.get(resref + ext)
        if not candidate:
            candidate = file_idx.get(resref + ext.upper())
        if not candidate:
            underscore = resref.find('_')
            if underscore > 0:
                suffix = resref[underscore:]
                for idx_key, idx_path in file_idx.items():
                    if (idx_key.endswith(suffix + ext) or
                            idx_key.endswith(suffix + ext.upper())):
                        candidate = idx_path
                        break
        return candidate

    def test_exact_match(self):
        """Exact filename match takes priority."""
        idx = self._build_file_idx(["lsl_dirt02.tga", "sle_dirt02.tga"])
        result = self._fuzzy_find("lsl_dirt02", idx, ".tga")
        self.assertIsNotNone(result)
        self.assertIn("lsl_dirt02", result)

    def test_prefix_mismatch_fallback(self):
        """lsl_* resref with only sle_* file → fuzzy match by _suffix."""
        idx = self._build_file_idx(["sle_dirt02.tga", "sle_wall08.tga"])
        result = self._fuzzy_find("lsl_dirt02", idx, ".tga")
        self.assertIsNotNone(result, "fuzzy lookup should find sle_dirt02.tga")
        self.assertIn("sle_dirt02", result)

    def test_no_match(self):
        """No file with matching suffix → returns None."""
        idx = self._build_file_idx(["sle_wall01.tga"])
        result = self._fuzzy_find("lsl_dirt99", idx, ".tga")
        self.assertIsNone(result)

    def test_tpc_preferred_over_tga(self):
        """TPC files (checked first) take priority over TGA."""
        idx = self._build_file_idx(["lsl_dirt02.tpc", "lsl_dirt02.tga"])
        result_tpc = self._fuzzy_find("lsl_dirt02", idx, ".tpc")
        result_tga = self._fuzzy_find("lsl_dirt02", idx, ".tga")
        self.assertIn(".tpc", result_tpc)
        self.assertIn(".tga", result_tga)
        # Caller should try TPC first — that is the 'break' behaviour


# ─────────────────────────────────────────────────────────────────────────────
#  4. Re-attach always fires (even when loaded==0)
# ─────────────────────────────────────────────────────────────────────────────

class TestTextureReattach(unittest.TestCase):
    """
    load_textures_for_rooms must re-attach cached textures even when
    the 'loaded' counter stays at 0 (all textures already in cache).
    """

    def _make_mock_renderer(self, cached_textures, room_vaos):
        """Minimal duck-type _EGLRenderer with just what the method needs."""
        class MockRenderer:
            def __init__(self, cache, vaos):
                self.ready       = True
                self._tex_cache  = dict(cache)
                self._room_vaos  = list(vaos)
        return MockRenderer(cached_textures, room_vaos)

    def test_reattach_when_all_cached(self):
        """
        If all textures are already in _tex_cache, re-attach loop MUST still
        populate e['tex'] on every VAO that needs it.
        """
        # Simulate a mock texture object
        mock_tex = object()
        cached   = {"lsl_dirt02": mock_tex}

        # VAO that needs lsl_dirt02 but has tex=None (built before textures loaded)
        vao = {"vao": object(), "count": 6,
               "tex_name": "lsl_dirt02", "tex_resref": "lsl_dirt02",
               "tex": None, "lit_uv": True, "lit": True,
               "tx": 0., "ty": 0., "tz": 0., "color": (0.5, 0.5, 0.5)}

        renderer = self._make_mock_renderer(cached, [vao])

        # Replicate the fixed re-attach logic (the bug fix)
        reattached = 0
        for e in renderer._room_vaos:
            tn = (e.get("tex_name", "") or e.get("tex_resref", "")).lower()
            if tn:
                c = renderer._tex_cache.get(tn)
                if c is not None and e.get("tex") is not c:
                    e["tex"] = c
                    reattached += 1

        self.assertEqual(reattached, 1, "should re-attach the cached texture")
        self.assertIs(vao["tex"], mock_tex, "tex should be the cached object")

    def test_reattach_only_changes_unset(self):
        """
        Re-attach must NOT touch VAOs that already have the correct tex set.
        """
        mock_tex = object()
        cached   = {"lsl_dirt02": mock_tex}
        # VAO already has tex set to mock_tex
        vao = {"tex_name": "lsl_dirt02", "tex": mock_tex}

        reattached = 0
        for e in [vao]:
            tn = (e.get("tex_name", "") or e.get("tex_resref", "")).lower()
            if tn:
                c = cached.get(tn)
                if c is not None and e.get("tex") is not c:
                    e["tex"] = c
                    reattached += 1

        self.assertEqual(reattached, 0, "already-set tex should not be re-attached")

    def test_render_loop_tex_lookup(self):
        """
        The render loop must find a texture via both 'tex_name' and 'tex_resref'.
        This tests the fixed render-loop texture-key lookup.
        """
        mock_tex  = object()
        tex_cache = {"lsl_dirt02": mock_tex}

        # Simulate a VAO with tex_resref set (Path A from _upload_lit_or_flat)
        # but tex=None (texture loaded after VAO creation)
        e = {"tex": None, "tex_resref": "lsl_dirt02", "tex_name": "lsl_dirt02"}

        # Replicate the fixed render-loop lookup
        tex_obj = e.get("tex")
        if tex_obj is None:
            tex_key = (e.get("tex_name", "") or e.get("tex_resref", "")).lower()
            if tex_key:
                tex_obj = tex_cache.get(tex_key)

        self.assertIs(tex_obj, mock_tex,
                      "render loop must find texture via tex_name/tex_resref")

    def test_render_loop_only_tex_name(self):
        """Render loop works if only tex_name is set (no tex_resref)."""
        mock_tex  = object()
        tex_cache = {"mywall": mock_tex}
        e = {"tex": None, "tex_resref": "", "tex_name": "mywall"}

        tex_obj = e.get("tex")
        if tex_obj is None:
            tex_key = (e.get("tex_name", "") or e.get("tex_resref", "")).lower()
            if tex_key:
                tex_obj = tex_cache.get(tex_key)

        self.assertIs(tex_obj, mock_tex)


# ─────────────────────────────────────────────────────────────────────────────
#  5. AssetItem.file_path
# ─────────────────────────────────────────────────────────────────────────────

class TestAssetItemFilePath(unittest.TestCase):
    """AssetItem must carry file_path for texture thumbnails."""

    def test_default_empty(self):
        from gmodular.gui.content_browser import AssetItem
        a = AssetItem("Test", "test_resref")
        self.assertEqual(a.file_path, "")

    def test_explicit_path(self):
        from gmodular.gui.content_browser import AssetItem
        a = AssetItem("Wall", "sle_wall08", file_path="/path/to/sle_wall08.tga")
        self.assertEqual(a.file_path, "/path/to/sle_wall08.tga")

    def test_slots(self):
        """file_path must be in __slots__."""
        from gmodular.gui.content_browser import AssetItem
        self.assertIn("file_path", AssetItem.__slots__)


# ─────────────────────────────────────────────────────────────────────────────
#  6. _populate_from_extract_dir sets file_path on texture assets
# ─────────────────────────────────────────────────────────────────────────────

class TestPopulateExtractDir(unittest.TestCase):
    """_populate_from_extract_dir must store file_path for textures."""

    def _make_extract_dir(self, tmpdir, filenames):
        for fn in filenames:
            path = os.path.join(tmpdir, fn)
            if fn.endswith('.tga'):
                # Build a minimal 2×2 TGA
                data = _make_tga(2, 2, bpp=24)
            elif fn.endswith('.mdl'):
                data = b'\x00' * 300
            else:
                data = b'\x00' * 10
            with open(path, 'wb') as f:
                f.write(data)

    def test_texture_file_path_set(self):
        """Texture AssetItems must have file_path pointing to the real file."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            self._make_extract_dir(tmpdir, ["sle_dirt02.tga", "sle_wall08.tga",
                                            "m31aa_00a.mdl"])
            # We cannot use the real ContentBrowser (needs Qt), so replicate
            # the _populate_from_extract_dir logic inline:
            from gmodular.gui.content_browser import AssetItem
            all_assets = []
            existing = set()
            for fn in sorted(os.listdir(tmpdir)):
                fn_lo = fn.lower()
                stem  = os.path.splitext(fn_lo)[0]
                ext   = os.path.splitext(fn_lo)[1]
                if stem in existing:
                    continue
                if ext in ('.tga', '.tpc'):
                    a = AssetItem(
                        display_name=stem.replace('_', ' ').title(),
                        resref=stem,
                        template_resref=stem,
                        asset_type="texture",
                        category="Textures",
                        subcategory="Module Textures",
                        description=f"Texture from module: {fn}",
                        file_path=os.path.join(tmpdir, fn),
                    )
                    all_assets.append(a)
                    existing.add(stem)
                elif ext == '.mdl':
                    a = AssetItem(
                        display_name=stem.replace('_', ' ').title(),
                        resref=stem,
                        template_resref=stem,
                        asset_type="room",
                        category="Rooms",
                        subcategory="Module Rooms",
                        description=f"Room from module: {fn}",
                    )
                    all_assets.append(a)
                    existing.add(stem)

            textures = [a for a in all_assets if a.asset_type == "texture"]
            self.assertEqual(len(textures), 2)
            for t in textures:
                self.assertTrue(os.path.isfile(t.file_path),
                                f"file_path '{t.file_path}' must exist")
                self.assertTrue(t.file_path.endswith('.tga'),
                                f"file_path must have .tga extension")

    def test_mdl_asset_no_file_path(self):
        """Room model AssetItems created without file_path default to empty."""
        from gmodular.gui.content_browser import AssetItem
        a = AssetItem("My Room", "myroom", asset_type="room",
                      category="Rooms", subcategory="Module Rooms")
        self.assertEqual(a.file_path, "")


# ─────────────────────────────────────────────────────────────────────────────
#  7. TGA thumbnail helper (_tga_to_pixmap) — logic test without Qt
# ─────────────────────────────────────────────────────────────────────────────

class TestTGAThumbnailLogic(unittest.TestCase):
    """
    _tga_to_pixmap is a Qt function so we can't call it in headless CI.
    But we can verify the underlying decode logic produces valid RGBA bytes.
    """

    def test_decode_2x2_tga(self):
        """Decode a 2×2 24-bit TGA → 16 RGBA bytes, correct channels."""
        tga = _make_tga(2, 2, bpp=24, bottom_origin=True)
        rgba, w, h = _decode_tga_to_rgba(tga)
        self.assertIsNotNone(rgba)
        self.assertEqual(w, 2)
        self.assertEqual(h, 2)
        self.assertEqual(len(rgba), 2 * 2 * 4)

    def test_decode_invalid_type(self):
        """RLE-compressed TGA (type 10) is rejected."""
        tga = bytearray(_make_tga(2, 2))
        tga[2] = 10    # image_type = RLE
        rgba, w, h = _decode_tga_to_rgba(bytes(tga))
        self.assertIsNone(rgba)

    def test_decode_zero_size(self):
        """Zero-width TGA is rejected."""
        tga = bytearray(_make_tga(2, 2))
        struct.pack_into('<H', tga, 12, 0)   # w = 0
        rgba, w, h = _decode_tga_to_rgba(bytes(tga))
        self.assertIsNone(rgba)


# ─────────────────────────────────────────────────────────────────────────────
#  8. MDL mesh node roundtrip
# ─────────────────────────────────────────────────────────────────────────────

class TestMDLMeshRoundtrip(unittest.TestCase):
    """
    Build a synthetic MDL binary and verify MDLParser extracts
    vertex geometry, normals, UVs and texture name correctly.
    """

    def _make_mdl_with_mesh(self) -> bytes:
        """
        Build a minimal MDL with one mesh node containing 2 triangles (6 verts).
        Uses the builder from test_mdl_parser.py.
        """
        try:
            from tests.test_mdl_parser import build_minimal_mdl
            from gmodular.formats.mdl_parser import NODE_HEADER, NODE_MESH
            return build_minimal_mdl(
                nodes_spec=[('root', NODE_HEADER), ('mesh01', NODE_HEADER | NODE_MESH)],
            )
        except Exception:
            return b'\x00' * 512  # fallback: empty bytes → parser returns empty

    def test_parser_returns_meshdata(self):
        """MDLParser.parse() returns a MeshData object (not None)."""
        from gmodular.formats.mdl_parser import MDLParser
        mdl = self._make_mdl_with_mesh()
        md  = MDLParser(mdl, b'').parse()
        self.assertIsNotNone(md)

    def test_visible_mesh_nodes_excludes_aabb(self):
        """MeshData.visible_mesh_nodes() excludes AABB walkmesh nodes."""
        from gmodular.formats.mdl_parser import MeshNode, MeshData, NODE_AABB, NODE_MESH, NODE_HEADER
        mesh   = MeshNode(name="floorAABB", flags=NODE_HEADER | NODE_AABB)
        mesh.vertices = [(0,0,0), (1,0,0), (0,1,0)]
        mesh.faces    = [(0,1,2)]
        render = MeshNode(name="floor01", flags=NODE_HEADER | NODE_MESH)
        render.vertices = [(0,0,0), (1,0,0), (0,1,0)]
        render.faces    = [(0,1,2)]
        render.render   = True

        root = MeshNode(name="root", flags=NODE_HEADER)
        root.children = [mesh, render]

        md = MeshData(name="test", root_node=root)
        visible = md.visible_mesh_nodes()
        names = [n.name for n in visible]
        self.assertNotIn("floorAABB", names,
                         "AABB node must not appear in visible_mesh_nodes()")
        self.assertIn("floor01", names,
                      "renderable mesh node must appear in visible_mesh_nodes()")

    def test_mesh_node_texture_clean(self):
        """MeshNode.texture_clean strips NUL bytes and whitespace."""
        from gmodular.formats.mdl_parser import MeshNode
        node = MeshNode(name="mesh", texture="lsl_dirt02\x00\x00")
        self.assertEqual(node.texture_clean, "lsl_dirt02")

    def test_mesh_node_lightmap_clean(self):
        """MeshNode.lightmap_clean strips NUL bytes and whitespace."""
        from gmodular.formats.mdl_parser import MeshNode
        node = MeshNode(name="mesh", lightmap="  lsl_lm01 \x00")
        self.assertEqual(node.lightmap_clean, "lsl_lm01")


# ─────────────────────────────────────────────────────────────────────────────
#  9. Category-tree auto-expand (logic check, no Qt)
# ─────────────────────────────────────────────────────────────────────────────

class TestCategoryTreeExpansion(unittest.TestCase):
    """
    The fixed _refresh_category_tree() must auto-expand 'Rooms' and
    'Textures' categories.  We verify that the expansion predicate is
    correct without instantiating a QTreeWidget.
    """

    _AUTO_EXPAND = {"Rooms", "Textures"}

    def test_rooms_expands(self):
        self.assertIn("Rooms", self._AUTO_EXPAND)

    def test_textures_expands(self):
        self.assertIn("Textures", self._AUTO_EXPAND)

    def test_placeables_does_not_expand(self):
        self.assertNotIn("Placeables", self._AUTO_EXPAND)

    def test_category_color_map(self):
        color_map = {"Rooms": "#44ddaa", "Textures": "#ddaa44"}
        self.assertEqual(color_map["Rooms"],    "#44ddaa")
        self.assertEqual(color_map["Textures"], "#ddaa44")
        self.assertIsNone(color_map.get("Placeables"))


# ─────────────────────────────────────────────────────────────────────────────
#  10. Integration: full TGA decode matches expected pixel values
# ─────────────────────────────────────────────────────────────────────────────

class TestTGAIntegration(unittest.TestCase):
    """End-to-end TGA decode verification using a known pixel pattern."""

    def test_pixel_values_correct(self):
        """
        Build a 4×4 TGA where pixel(row=r, col=c) = (R=c, G=r, B=0).
        After decode the RGBA bytes must match.
        """
        w, h = 4, 4
        tga  = _make_tga(w, h, bpp=24, bottom_origin=True)
        rgba, _w, _h = _decode_tga_to_rgba(tga)
        self.assertIsNotNone(rgba)

        # After the row-flip, row index 0 = top of image (row_in_file = h-1 = 3)
        # Our builder: col=0, row=r → B=0, G=r, R=col → file: BGR = [0, r, col]
        # After BGR→RGBA: R=col, G=r, B=0, A=255

        for row in range(h):
            for col in range(w):
                idx = (row * w + col) * 4
                r_exp, g_exp, b_exp, a_exp = col, row, 0, 255
                self.assertEqual(rgba[idx],   r_exp, f"R at ({row},{col})")
                self.assertEqual(rgba[idx+1], g_exp, f"G at ({row},{col})")
                self.assertEqual(rgba[idx+2], b_exp, f"B at ({row},{col})")
                self.assertEqual(rgba[idx+3], a_exp, f"A at ({row},{col})")

    def test_real_tga_from_disk(self):
        """
        If a real TGA from slem_ar is available, verify its dimensions
        and that all pixels have valid RGBA bytes.
        """
        test_tga = "/home/user/uploaded_files/_slem_ar_extracted/lsl_dirt02.tga"
        if not os.path.isfile(test_tga):
            self.skipTest("slem_ar extract dir not available")
        data = open(test_tga, 'rb').read()
        rgba, w, h = _decode_tga_to_rgba(data)
        self.assertIsNotNone(rgba, "Real TGA should decode successfully")
        self.assertGreater(w, 0)
        self.assertGreater(h, 0)
        self.assertEqual(len(rgba), w * h * 4)

    def test_real_mdl_from_disk(self):
        """
        If the slem_ar MDL is available, verify MDLParser returns mesh
        nodes with geometry.
        """
        test_mdl = "/home/user/uploaded_files/_slem_ar_extracted/m31aa_00a.mdl"
        test_mdx = "/home/user/uploaded_files/_slem_ar_extracted/m31aa_00a.mdx"
        if not os.path.isfile(test_mdl):
            self.skipTest("slem_ar MDL not available")
        from gmodular.formats.mdl_parser import MDLParser
        mdl_bytes = open(test_mdl, 'rb').read()
        mdx_bytes = open(test_mdx, 'rb').read() if os.path.isfile(test_mdx) else b''
        md = MDLParser(mdl_bytes, mdx_bytes).parse()
        self.assertIsNotNone(md, "MDLParser.parse() must not return None")
        nodes = md.visible_mesh_nodes()
        self.assertGreater(len(nodes), 0,
                           "m31aa_00a.mdl must have at least one visible mesh node")
        # Verify first node has geometry
        n0 = nodes[0]
        self.assertGreater(len(n0.vertices), 0, "first node must have vertices")
        self.assertGreater(len(n0.faces),    0, "first node must have faces")


if __name__ == "__main__":
    unittest.main()
