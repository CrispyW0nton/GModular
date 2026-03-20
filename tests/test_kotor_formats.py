"""
tests/test_kotor_formats.py
===========================
Comprehensive test suite for gmodular.formats.kotor_formats and the
gmodular.mcp.tools.formats MCP tool layer.

Covers (in order):
  1.  SSF  — read/write round-trip, edge cases
  2.  LIP  — read/write round-trip, shape enum
  3.  TXI  — read/write, property helpers
  4.  VIS  — read/write, graph operations
  5.  PTH  — data model
  6.  2DA  — binary write, ASCII write
  7.  TLK  — read/write round-trip, partial patch
  8.  NCS  — header validation, disassembly smoke test
  9.  Format auto-detect
 10.  MCP tool layer — schema, dispatch, handler smoke tests
"""
from __future__ import annotations

import asyncio
import base64
import json
import struct
from io import BytesIO

import pytest

# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _run(coro):
    """Run a coroutine synchronously in a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_ssf_bytes(strefs=None):
    """Build a minimal valid SSF binary blob."""
    if strefs is None:
        strefs = list(range(28))
    buf = bytearray()
    buf += b"SSF "
    buf += b"V1.1"
    buf += struct.pack("<I", 12)   # offset to sound table
    for v in strefs:
        raw = 0xFFFFFFFF if v == -1 else v
        buf += struct.pack("<I", raw)
    return bytes(buf)


def _make_lip_bytes(length=1.5, keyframes=None):
    """Build a minimal valid LIP binary blob."""
    if keyframes is None:
        keyframes = [(0.0, 0), (0.5, 4), (1.0, 11)]
    buf = bytearray()
    buf += b"LIP "
    buf += b"V1.0"
    buf += struct.pack("<f", length)
    buf += struct.pack("<I", len(keyframes))
    for t, s in keyframes:
        buf += struct.pack("<f", t)
        buf += struct.pack("<B", s)
    return bytes(buf)


def _make_ncs_bytes():
    """Build a minimal NCS blob: header + RETN instruction."""
    buf = bytearray()
    buf += b"NCS V1.0"
    buf += b"\x42"                     # type byte
    buf += struct.pack(">I", 14)       # code size (big-endian)
    buf += bytes([0x20, 0x00])         # RETN opcode, subtype 0
    return bytes(buf)


def _make_tlk_bytes(entries=None):
    """Build a valid TLK binary blob."""
    if entries is None:
        entries = [("Hello", ""), ("World", "snd_01")]
    HEADER = 20
    ENTRY  = 40
    n      = len(entries)
    # Build string data
    strings_buf = bytearray()
    packed_entries = []
    for text, sound in entries:
        enc = text.encode("latin-1")
        packed_entries.append({
            "flags": 0x01,
            "sound_resref": sound,
            "vol_var": 0, "pitch_var": 0,
            "offset": len(strings_buf),
            "length": len(enc),
            "sound_length": 0.0,
        })
        strings_buf.extend(enc)

    str_off = HEADER + n * ENTRY
    buf = bytearray()
    buf += b"TLK "
    buf += b"V3.2"
    buf += struct.pack("<I", 0)        # language_id = 0
    buf += struct.pack("<I", n)
    buf += struct.pack("<I", str_off)
    for e in packed_entries:
        sr = e["sound_resref"][:16].encode("latin-1").ljust(16, b"\x00")
        buf += struct.pack("<I", e["flags"])
        buf += sr
        buf += struct.pack("<I", e["vol_var"])
        buf += struct.pack("<I", e["pitch_var"])
        buf += struct.pack("<I", e["offset"])
        buf += struct.pack("<I", e["length"])
        buf += struct.pack("<f", e["sound_length"])
    buf += strings_buf
    return bytes(buf)


# ═══════════════════════════════════════════════════════════════════════════
#  1.  SSF
# ═══════════════════════════════════════════════════════════════════════════

class TestSSF:
    def test_import(self):
        from gmodular.formats.kotor_formats import SSFData, SSFSound, read_ssf, write_ssf
        assert SSFSound.BATTLE_CRY_1 == 0
        assert SSFSound.POISONED == 27

    def test_read_basic(self):
        from gmodular.formats.kotor_formats import read_ssf, SSFSound
        data = _make_ssf_bytes()
        ssf = read_ssf(data)
        assert ssf.get(SSFSound.BATTLE_CRY_1) == 0
        assert ssf.get(SSFSound.POISONED) == 27

    def test_read_minus1_becomes_minus1(self):
        from gmodular.formats.kotor_formats import read_ssf, SSFSound
        strefs = [-1] * 28
        data = _make_ssf_bytes(strefs)
        ssf = read_ssf(data)
        for s in range(28):
            assert ssf._strefs[s] == -1

    def test_round_trip(self):
        from gmodular.formats.kotor_formats import read_ssf, write_ssf, SSFSound
        original = _make_ssf_bytes([i * 100 for i in range(28)])
        ssf = read_ssf(original)
        out = write_ssf(ssf)
        ssf2 = read_ssf(out)
        assert ssf2.get(SSFSound.BATTLE_CRY_1) == 0
        assert ssf2.get(SSFSound.POISONED) == 2700

    def test_write_sets_correct_offset(self):
        from gmodular.formats.kotor_formats import SSFData, write_ssf
        ssf = SSFData()
        out = write_ssf(ssf)
        # offset field at bytes 8-12 should be 12
        (offset,) = struct.unpack_from("<I", out, 8)
        assert offset == 12

    def test_magic_wrong_raises(self):
        from gmodular.formats.kotor_formats import read_ssf
        with pytest.raises(ValueError, match="Not an SSF"):
            read_ssf(b"GFF V3.2" + b"\x00" * 20)

    def test_too_short_raises(self):
        from gmodular.formats.kotor_formats import read_ssf
        with pytest.raises(ValueError):
            read_ssf(b"SSF ")

    def test_as_dict_returns_28_entries(self):
        from gmodular.formats.kotor_formats import SSFData
        ssf = SSFData()
        d = ssf.as_dict()
        assert len(d) == 28

    def test_reset_clears_values(self):
        from gmodular.formats.kotor_formats import SSFData, SSFSound
        ssf = SSFData()
        ssf.set(SSFSound.BATTLE_CRY_1, 999)
        ssf.reset()
        assert ssf.get(SSFSound.BATTLE_CRY_1) == -1


# ═══════════════════════════════════════════════════════════════════════════
#  2.  LIP
# ═══════════════════════════════════════════════════════════════════════════

class TestLIP:
    def test_import(self):
        from gmodular.formats.kotor_formats import LIPData, LIPShape, LIPKeyframe
        assert LIPShape.NEUTRAL == 0
        assert LIPShape.W_Q_OO == 15

    def test_read_basic(self):
        from gmodular.formats.kotor_formats import read_lip, LIPShape
        data = _make_lip_bytes(length=2.0, keyframes=[(0.0, 0), (1.0, 4)])
        lip = read_lip(data)
        assert abs(lip.length - 2.0) < 0.001
        assert len(lip) == 2
        assert lip.keyframes[0].shape == LIPShape.NEUTRAL
        assert lip.keyframes[1].shape == LIPShape.AH

    def test_round_trip(self):
        from gmodular.formats.kotor_formats import read_lip, write_lip
        original = _make_lip_bytes()
        lip = read_lip(original)
        out = write_lip(lip)
        lip2 = read_lip(out)
        assert len(lip2) == len(lip)
        assert abs(lip2.length - lip.length) < 0.001

    def test_bad_magic_raises(self):
        from gmodular.formats.kotor_formats import read_lip
        with pytest.raises((ValueError, TypeError)):
            read_lip(b"SSF V1.1" + b"\x00" * 20)

    def test_add_and_sort(self):
        from gmodular.formats.kotor_formats import LIPData, LIPShape
        lip = LIPData(length=2.0)
        lip.add(1.5, LIPShape.OH)
        lip.add(0.3, LIPShape.EE)
        lip.add(0.8, LIPShape.AH)
        sorted_kf = lip.sorted_keyframes()
        assert sorted_kf[0].time < sorted_kf[1].time < sorted_kf[2].time

    def test_empty_lip(self):
        from gmodular.formats.kotor_formats import LIPData, write_lip, read_lip
        lip = LIPData(length=0.5)
        out = write_lip(lip)
        lip2 = read_lip(out)
        assert len(lip2) == 0


# ═══════════════════════════════════════════════════════════════════════════
#  3.  TXI
# ═══════════════════════════════════════════════════════════════════════════

class TestTXI:
    def test_import(self):
        from gmodular.formats.kotor_formats import TXIData, read_txi, write_txi
        assert TXIData is not None

    def test_read_simple(self):
        from gmodular.formats.kotor_formats import read_txi
        raw = b"envmaptexture CM_BAREMETAL\ndecal 1\nfps 15.0\n"
        txi = read_txi(raw)
        assert txi.get("envmaptexture") == "CM_BAREMETAL"
        assert txi.get_int("decal") == 1
        assert abs(txi.get_float("fps") - 15.0) < 0.001

    def test_is_decal(self):
        from gmodular.formats.kotor_formats import read_txi
        txi = read_txi(b"decal 1\n")
        assert txi.is_decal is True

    def test_is_animated(self):
        from gmodular.formats.kotor_formats import read_txi
        txi = read_txi(b"numx 4\nnumy 2\nfps 8\n")
        assert txi.is_animated is True
        assert txi.num_frames == 8

    def test_round_trip(self):
        from gmodular.formats.kotor_formats import read_txi, write_txi
        raw = b"blending additive\ndecal 0\nenvmaptexture CM_SHINY\n"
        txi  = read_txi(raw)
        out  = write_txi(txi)
        txi2 = read_txi(out)
        assert txi2.get("envmaptexture") == "CM_SHINY"

    def test_empty_txi(self):
        from gmodular.formats.kotor_formats import read_txi
        txi = read_txi(b"")
        assert txi.all_fields == {}

    def test_comment_lines_ignored(self):
        from gmodular.formats.kotor_formats import read_txi
        txi = read_txi(b"# this is a comment\ndecal 1\n")
        assert "decal" in txi.all_fields
        assert len(txi.all_fields) == 1


# ═══════════════════════════════════════════════════════════════════════════
#  4.  VIS
# ═══════════════════════════════════════════════════════════════════════════

class TestVIS:
    def test_import(self):
        from gmodular.formats.kotor_formats import VISData, read_vis, write_vis
        assert VISData is not None

    def test_read_basic(self):
        from gmodular.formats.kotor_formats import read_vis
        raw = b"m05aa_01a 2\n  m05aa_01b\n  m05aa_01c\nm05aa_01b 1\n  m05aa_01a\n"
        vis = read_vis(raw)
        assert "m05aa_01a" in vis.all_rooms()
        assert vis.is_visible("m05aa_01a", "m05aa_01b")
        assert vis.is_visible("m05aa_01a", "m05aa_01c")
        assert vis.is_visible("m05aa_01b", "m05aa_01a")
        assert not vis.is_visible("m05aa_01b", "m05aa_01c")

    def test_round_trip(self):
        from gmodular.formats.kotor_formats import read_vis, write_vis
        raw = b"room_a 2\n  room_b\n  room_c\nroom_b 1\n  room_a\nroom_c 0\n"
        vis = read_vis(raw)
        out = write_vis(vis)
        vis2 = read_vis(out)
        assert vis2.is_visible("room_a", "room_b")
        assert vis2.is_visible("room_a", "room_c")
        assert not vis2.is_visible("room_c", "room_a")

    def test_set_visible_adds_room(self):
        from gmodular.formats.kotor_formats import VISData
        vis = VISData()
        vis.set_visible("room_x", "room_y")
        assert "room_x" in vis.all_rooms()
        assert "room_y" in vis.all_rooms()

    def test_unset_visible(self):
        from gmodular.formats.kotor_formats import VISData
        vis = VISData()
        vis.set_visible("a", "b")
        vis.set_visible("a", "b", visible=False)
        assert not vis.is_visible("a", "b")

    def test_empty_vis_writes_empty(self):
        from gmodular.formats.kotor_formats import VISData, write_vis
        vis = VISData()
        out = write_vis(vis)
        assert out.strip() == b""


# ═══════════════════════════════════════════════════════════════════════════
#  5.  PTH
# ═══════════════════════════════════════════════════════════════════════════

class TestPTH:
    def test_import(self):
        from gmodular.formats.kotor_formats import PTHData, PTHPoint
        assert PTHData is not None

    def test_add_point(self):
        from gmodular.formats.kotor_formats import PTHData
        pth = PTHData()
        idx = pth.add_point(1.0, 2.0)
        assert idx == 0
        assert pth.points[0].x == 1.0
        assert pth.points[0].y == 2.0

    def test_connect_bidirectional(self):
        from gmodular.formats.kotor_formats import PTHData
        pth = PTHData()
        a = pth.add_point(0, 0)
        b = pth.add_point(5, 0)
        c = pth.add_point(5, 5)
        pth.connect(a, b)
        pth.connect(b, c)
        assert b in pth.points[a].connections
        assert a in pth.points[b].connections
        assert c in pth.points[b].connections

    def test_no_duplicate_connections(self):
        from gmodular.formats.kotor_formats import PTHData
        pth = PTHData()
        a = pth.add_point(0, 0)
        b = pth.add_point(1, 0)
        pth.connect(a, b)
        pth.connect(a, b)  # duplicate
        assert pth.points[a].connections.count(b) == 1

    def test_write_to_dict(self):
        from gmodular.formats.kotor_formats import PTHData, write_pth_to_gff_dict
        pth = PTHData()
        a = pth.add_point(0, 0)
        b = pth.add_point(10, 0)
        pth.connect(a, b)
        d = write_pth_to_gff_dict(pth)
        assert "Path_Points" in d
        assert len(d["Path_Points"]) == 2


# ═══════════════════════════════════════════════════════════════════════════
#  6.  2DA
# ═══════════════════════════════════════════════════════════════════════════

class TestTwoDA:
    def test_import(self):
        from gmodular.formats.kotor_formats import TwoDAData, write_2da_binary, write_2da_ascii
        assert TwoDAData is not None

    def test_basic_table(self):
        from gmodular.formats.kotor_formats import TwoDAData
        t = TwoDAData(columns=["label", "value"])
        t.set(0, "label", "foo")
        t.set(0, "value", "42")
        assert t.get(0, "label") == "foo"
        assert t.get(0, "value") == "42"

    def test_ascii_write_header(self):
        from gmodular.formats.kotor_formats import TwoDAData, write_2da_ascii
        t = TwoDAData(columns=["A", "B"])
        t.set(0, "A", "hello")
        t.set(0, "B", "world")
        out = write_2da_ascii(t).decode("latin-1")
        assert out.startswith("2DA V2.0")
        assert "A" in out
        assert "hello" in out

    def test_binary_write_has_header(self):
        from gmodular.formats.kotor_formats import TwoDAData, write_2da_binary
        t = TwoDAData(columns=["X"])
        t.set(0, "X", "test")
        out = write_2da_binary(t)
        assert out[:4] == b"2DA "

    def test_add_column(self):
        from gmodular.formats.kotor_formats import TwoDAData
        t = TwoDAData()
        t.add_column("new_col")
        assert "new_col" in t.columns

    def test_add_row(self):
        from gmodular.formats.kotor_formats import TwoDAData
        t = TwoDAData(columns=["col"])
        idx = t.add_row({"col": "v"})
        assert idx == 0
        assert t.get(0, "col") == "v"

    def test_missing_value_default(self):
        from gmodular.formats.kotor_formats import TwoDAData
        t = TwoDAData(columns=["a"])
        assert t.get(99, "a") == ""

    def test_ascii_empty_cells_become_stars(self):
        from gmodular.formats.kotor_formats import TwoDAData, write_2da_ascii
        t = TwoDAData(columns=["A", "B"])
        t.set(0, "A", "hello")
        # B left empty
        out = write_2da_ascii(t).decode("latin-1")
        assert "****" in out


# ═══════════════════════════════════════════════════════════════════════════
#  7.  TLK
# ═══════════════════════════════════════════════════════════════════════════

class TestTLK:
    def test_import(self):
        from gmodular.formats.kotor_formats import TLKData, TLKEntry, read_tlk, write_tlk
        assert TLKData is not None

    def test_read_entries(self):
        from gmodular.formats.kotor_formats import read_tlk
        data = _make_tlk_bytes([("Hello", ""), ("World", "snd_01")])
        tlk = read_tlk(data)
        assert tlk.entry_count() == 2
        assert tlk.get_text(0) == "Hello"
        assert tlk.get_text(1) == "World"

    def test_get_text_out_of_range_returns_default(self):
        from gmodular.formats.kotor_formats import TLKData
        tlk = TLKData()
        assert tlk.get_text(999, "fallback") == "fallback"

    def test_round_trip(self):
        from gmodular.formats.kotor_formats import read_tlk, write_tlk
        original = _make_tlk_bytes([("Alpha", ""), ("Beta", "snd_x")])
        tlk  = read_tlk(original)
        out  = write_tlk(tlk)
        tlk2 = read_tlk(out)
        assert tlk2.get_text(0) == "Alpha"
        assert tlk2.get_text(1) == "Beta"

    def test_bad_magic_raises(self):
        from gmodular.formats.kotor_formats import read_tlk
        with pytest.raises(ValueError):
            read_tlk(b"SSF V1.1" + b"\x00" * 20)

    def test_append_returns_correct_index(self):
        from gmodular.formats.kotor_formats import TLKData, TLKEntry
        tlk = TLKData()
        idx0 = tlk.append(TLKEntry(text="first"))
        idx1 = tlk.append(TLKEntry(text="second"))
        assert idx0 == 0
        assert idx1 == 1
        assert tlk.get_text(0) == "first"

    def test_write_preserves_language_id(self):
        from gmodular.formats.kotor_formats import TLKData, TLKEntry, write_tlk, read_tlk
        tlk = TLKData(language_id=6)  # French
        tlk.append(TLKEntry(text="Bonjour"))
        out  = write_tlk(tlk)
        tlk2 = read_tlk(out)
        assert tlk2.language_id == 6


# ═══════════════════════════════════════════════════════════════════════════
#  8.  NCS
# ═══════════════════════════════════════════════════════════════════════════

class TestNCS:
    def test_import(self):
        from gmodular.formats.kotor_formats import NCSData, NCSOpcode, read_ncs
        assert NCSOpcode.RETN == 0x20

    def test_read_header(self):
        from gmodular.formats.kotor_formats import read_ncs
        data = _make_ncs_bytes()
        ncs = read_ncs(data)
        assert ncs.code_size == 14

    def test_bad_magic_raises(self):
        from gmodular.formats.kotor_formats import read_ncs
        with pytest.raises(ValueError):
            read_ncs(b"TLK V3.2" + b"\x00" * 20)

    def test_disassembly_text_is_string(self):
        from gmodular.formats.kotor_formats import read_ncs
        data = _make_ncs_bytes()
        ncs = read_ncs(data)
        text = ncs.disassembly_text()
        assert isinstance(text, str)

    def test_too_short_gives_empty(self):
        from gmodular.formats.kotor_formats import read_ncs
        ncs = read_ncs(b"NCS V1.0\x42")  # header but no code size
        assert len(ncs) == 0

    def test_retn_decoded(self):
        from gmodular.formats.kotor_formats import read_ncs, NCSOpcode
        data = _make_ncs_bytes()
        ncs = read_ncs(data)
        # Should decode exactly one RETN instruction
        assert any(i.opcode == NCSOpcode.RETN for i in ncs.instructions)


# ═══════════════════════════════════════════════════════════════════════════
#  9.  Auto-detect
# ═══════════════════════════════════════════════════════════════════════════

class TestAutoDetect:
    def test_detect_ssf(self):
        from gmodular.formats.kotor_formats import detect_and_read, SSFData
        data = _make_ssf_bytes()
        result = detect_and_read(data)
        assert isinstance(result, SSFData)

    def test_detect_lip(self):
        from gmodular.formats.kotor_formats import detect_and_read, LIPData
        data = _make_lip_bytes()
        result = detect_and_read(data)
        assert isinstance(result, LIPData)

    def test_detect_ncs(self):
        from gmodular.formats.kotor_formats import detect_and_read, NCSData
        data = _make_ncs_bytes()
        result = detect_and_read(data)
        assert isinstance(result, NCSData)

    def test_detect_tlk(self):
        from gmodular.formats.kotor_formats import detect_and_read, TLKData
        data = _make_tlk_bytes()
        result = detect_and_read(data)
        assert isinstance(result, TLKData)

    def test_detect_vis_by_ext(self):
        from gmodular.formats.kotor_formats import detect_and_read, VISData
        raw = b"room_a 1\n  room_b\n"
        result = detect_and_read(raw, ext_hint="vis")
        assert isinstance(result, VISData)

    def test_unknown_raises(self):
        from gmodular.formats.kotor_formats import detect_and_read
        with pytest.raises(ValueError):
            detect_and_read(b"\x00\x01\x02\x03", ext_hint="xyz")


# ═══════════════════════════════════════════════════════════════════════════
# 10.  MCP Tool Layer
# ═══════════════════════════════════════════════════════════════════════════

class TestMCPFormatTools:
    def test_get_tools_count(self):
        from gmodular.mcp.tools.formats import get_tools
        tools = get_tools()
        assert len(tools) == 23  # 20 (prev) + 3 new (write_lip, write_vis, write_txi)

    def test_all_tools_have_name_and_schema(self):
        from gmodular.mcp.tools.formats import get_tools
        for tool in get_tools():
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool

    def test_tool_names(self):
        from gmodular.mcp.tools.formats import get_tools
        names = {t["name"] for t in get_tools()}
        assert "kotor_read_ssf"       in names
        assert "kotor_read_lip"       in names
        assert "kotor_read_txi"       in names
        assert "kotor_read_vis"       in names
        assert "kotor_read_ncs"       in names
        assert "kotor_write_ssf"      in names
        assert "kotor_write_2da_csv"  in names
        assert "kotor_write_tlk_patch" in names
        assert "kotor_describe_ssf"   in names

    def test_handle_read_ssf_with_b64(self):
        from gmodular.mcp.tools.formats import handle_read_ssf
        data = _make_ssf_bytes()
        b64  = base64.b64encode(data).decode()
        result = _run(handle_read_ssf({"data_b64": b64}))
        assert "content" in result
        payload = json.loads(result["content"][0]["text"])
        assert "sounds" in payload
        assert payload["assigned"] >= 0

    def test_handle_read_ssf_no_data_returns_error(self):
        from gmodular.mcp.tools.formats import handle_read_ssf
        result = _run(handle_read_ssf({}))
        payload = json.loads(result["content"][0]["text"])
        assert "error" in payload

    def test_handle_read_lip_with_b64(self):
        from gmodular.mcp.tools.formats import handle_read_lip
        data = _make_lip_bytes()
        b64  = base64.b64encode(data).decode()
        result = _run(handle_read_lip({"data_b64": b64}))
        payload = json.loads(result["content"][0]["text"])
        assert "keyframes" in payload

    def test_handle_read_txi_with_b64(self):
        from gmodular.mcp.tools.formats import handle_read_txi
        raw = b"decal 1\nenvmaptexture CM_TEST\n"
        b64 = base64.b64encode(raw).decode()
        result = _run(handle_read_txi({"data_b64": b64}))
        payload = json.loads(result["content"][0]["text"])
        assert payload["is_decal"] is True
        assert "CM_TEST" in payload["envmap"]

    def test_handle_read_vis_with_b64(self):
        from gmodular.mcp.tools.formats import handle_read_vis
        raw = b"room_a 1\n  room_b\nroom_b 0\n"
        b64 = base64.b64encode(raw).decode()
        result = _run(handle_read_vis({"data_b64": b64}))
        payload = json.loads(result["content"][0]["text"])
        assert "room_a" in payload["rooms"]

    def test_handle_read_ncs_with_b64(self):
        from gmodular.mcp.tools.formats import handle_read_ncs
        data = _make_ncs_bytes()
        b64  = base64.b64encode(data).decode()
        result = _run(handle_read_ncs({"data_b64": b64}))
        payload = json.loads(result["content"][0]["text"])
        assert "disassembly" in payload

    def test_handle_write_ssf_round_trip(self):
        from gmodular.mcp.tools.formats import handle_write_ssf
        sounds = {"BATTLE_CRY_1": 12345, "POISONED": 99}
        result = _run(handle_write_ssf({"sounds": sounds}))
        payload = json.loads(result["content"][0]["text"])
        assert "ssf_b64" in payload
        # Decode and re-read
        from gmodular.formats.kotor_formats import read_ssf, SSFSound
        data = base64.b64decode(payload["ssf_b64"])
        ssf  = read_ssf(data)
        assert ssf.get(SSFSound.BATTLE_CRY_1) == 12345
        assert ssf.get(SSFSound.POISONED) == 99

    def test_handle_write_2da_csv(self):
        from gmodular.mcp.tools.formats import handle_write_2da_csv
        result = _run(handle_write_2da_csv({
            "columns": ["label", "value"],
            "rows": [{"label": "foo", "value": "42"}],
        }))
        payload = json.loads(result["content"][0]["text"])
        assert "2DA" in payload["twoda_ascii"]
        assert "foo" in payload["twoda_ascii"]

    def test_handle_write_tlk_patch(self):
        from gmodular.mcp.tools.formats import handle_write_tlk_patch
        result = _run(handle_write_tlk_patch({
            "entries": [
                {"strref": 0, "text": "Hello"},
                {"strref": 1, "text": "World"},
            ],
        }))
        payload = json.loads(result["content"][0]["text"])
        assert "tlk_b64" in payload
        from gmodular.formats.kotor_formats import read_tlk
        data = base64.b64decode(payload["tlk_b64"])
        tlk  = read_tlk(data)
        assert tlk.get_text(0) == "Hello"
        assert tlk.get_text(1) == "World"

    def test_handle_describe_ssf_no_resref_error(self):
        from gmodular.mcp.tools.formats import handle_describe_ssf
        result = _run(handle_describe_ssf({}))
        payload = json.loads(result["content"][0]["text"])
        assert "error" in payload

    def test_mcp_registry_includes_format_tools(self):
        from gmodular.mcp.tools import get_all_tools
        names = {t["name"] for t in get_all_tools()}
        assert "kotor_read_ssf"  in names
        assert "kotor_read_lip"  in names
        assert "kotor_read_ncs"  in names
        assert "kotor_write_ssf" in names

    def test_handle_tool_dispatch_format_tools(self):
        from gmodular.mcp.tools import handle_tool
        data = _make_ssf_bytes()
        b64  = base64.b64encode(data).decode()
        # Should not raise ValueError
        result = _run(handle_tool("kotor_read_ssf", {"data_b64": b64}))
        assert result is not None

    def test_total_tool_count(self):
        from gmodular.mcp.tools import get_all_tools
        total = len(get_all_tools())
        # 91 (previous) + 12 Ghostworks IPC tools = 103
        assert total == 103, f"Expected 103, got {total}"


# ═══════════════════════════════════════════════════════════════════════════
# 11.  Integration: all format tools no-duplicate-name check
# ═══════════════════════════════════════════════════════════════════════════

class TestToolRegistry:
    def test_no_duplicate_names(self):
        from gmodular.mcp.tools import get_all_tools
        names = [t["name"] for t in get_all_tools()]
        dupes = [n for n in set(names) if names.count(n) > 1]
        assert not dupes, f"Duplicate tool names: {dupes}"

    def test_all_tools_have_input_schema(self):
        from gmodular.mcp.tools import get_all_tools
        for tool in get_all_tools():
            assert "inputSchema" in tool, f"Missing inputSchema: {tool['name']}"

    def test_format_tools_all_dispatchable(self):
        """Every format tool name must be routable by handle_tool."""
        from gmodular.mcp.tools import handle_tool, get_all_tools
        format_names = [
            "kotor_read_ssf", "kotor_read_lip", "kotor_read_txi",
            "kotor_read_vis", "kotor_read_ncs",
        ]
        for name in format_names:
            try:
                _run(handle_tool(name, {}))
            except ValueError as e:
                pytest.fail(f"handle_tool raised ValueError for '{name}': {e}")
            except Exception:
                pass  # DB/network/data errors acceptable in isolation


# ═══════════════════════════════════════════════════════════════════════════
# 12.  LTR — read/write round-trip
# ═══════════════════════════════════════════════════════════════════════════

class TestLTRFormat:
    """Tests for the KotOR LTR (name-generator Markov chain) format."""

    def _make_ltr(self, n: int = 28) -> "LTRData":
        from gmodular.formats.kotor_formats import LTRData
        ltr = LTRData(letter_count=n)
        # Set some non-zero values for round-trip verification
        for i in range(n * 3):
            ltr.single[i] = float(i) / (n * 3)
        return ltr

    def test_ltr_default_letter_count(self):
        from gmodular.formats.kotor_formats import LTRData
        ltr = LTRData()
        assert ltr.letter_count == 28

    def test_ltr_single_table_size(self):
        from gmodular.formats.kotor_formats import LTRData
        ltr = LTRData(letter_count=28)
        assert len(ltr.single) == 28 * 3   # 84 floats
        assert len(ltr.double) == 28 * 28 * 3
        assert len(ltr.triple) == 28 * 28 * 28 * 3

    def test_write_ltr_magic(self):
        from gmodular.formats.kotor_formats import LTRData, write_ltr
        ltr = LTRData()
        data = write_ltr(ltr)
        assert data[:8] == b"LTR V1.0"

    def test_write_ltr_size(self):
        """Verify the written size matches the documented byte count."""
        from gmodular.formats.kotor_formats import LTRData, write_ltr
        n = 28
        ltr = LTRData(letter_count=n)
        data = write_ltr(ltr)
        expected = 9 + (n*3 + n*n*3 + n*n*n*3) * 4
        assert len(data) == expected, f"Expected {expected} bytes, got {len(data)}"

    def test_ltr_roundtrip(self):
        from gmodular.formats.kotor_formats import LTRData, write_ltr, read_ltr
        ltr = self._make_ltr()
        data = write_ltr(ltr)
        ltr2 = read_ltr(data)
        assert ltr2.letter_count == ltr.letter_count
        assert abs(ltr2.single[0] - ltr.single[0]) < 1e-6
        assert abs(ltr2.single[-1] - ltr.single[-1]) < 1e-6

    def test_ltr_roundtrip_26_letters(self):
        """26-letter variant (NWN) should also round-trip."""
        from gmodular.formats.kotor_formats import LTRData, write_ltr, read_ltr
        ltr = LTRData(letter_count=26)
        ltr.single[0] = 0.9
        data = write_ltr(ltr)
        ltr2 = read_ltr(data)
        assert ltr2.letter_count == 26
        assert abs(ltr2.single[0] - 0.9) < 1e-5

    def test_read_ltr_bad_magic(self):
        from gmodular.formats.kotor_formats import read_ltr
        with pytest.raises(ValueError, match="Not an LTR file"):
            read_ltr(b"BAD DATA" + b"\x00" * 100)

    def test_ltr_detect_and_read(self):
        """detect_and_read() should recognise LTR files."""
        from gmodular.formats.kotor_formats import LTRData, write_ltr, detect_and_read
        ltr = LTRData()
        data = write_ltr(ltr)
        result = detect_and_read(data, "ltr")
        from gmodular.formats.kotor_formats import LTRData as LTRType
        assert isinstance(result, LTRType)


# ═══════════════════════════════════════════════════════════════════════════
# 13.  NCS write — re-assembler round-trip
# ═══════════════════════════════════════════════════════════════════════════

class TestNCSWrite:
    """Tests for the KotOR NCS binary write (re-assembler)."""

    def test_write_ncs_magic(self):
        from gmodular.formats.kotor_formats import NCSData, write_ncs
        ncs = NCSData()
        data = write_ncs(ncs)
        assert data[:8] == b"NCS V1.0"
        assert data[8] == 0x42  # magic byte

    def test_write_ncs_empty(self):
        from gmodular.formats.kotor_formats import NCSData, write_ncs
        ncs = NCSData()
        data = write_ncs(ncs)
        # Header is 13 bytes: "NCS V1.0" (8) + 0x42 (1) + total_size (4)
        assert len(data) == 13

    def test_write_ncs_total_size_field(self):
        """The size field in the header should equal the total file length."""
        from gmodular.formats.kotor_formats import NCSData, NCSInstruction, write_ncs
        ncs = NCSData()
        ncs.instructions.append(NCSInstruction(0, 0x20, 0x00, b""))  # RETN
        data = write_ncs(ncs)
        size_in_header = struct.unpack(">I", data[9:13])[0]
        assert size_in_header == len(data)

    def test_ncs_roundtrip_nop_retn(self):
        from gmodular.formats.kotor_formats import NCSData, NCSInstruction, write_ncs, read_ncs
        ncs = NCSData()
        ncs.instructions.append(NCSInstruction(0, 0x2D, 0x00, b""))   # NOP
        ncs.instructions.append(NCSInstruction(2, 0x20, 0x00, b""))   # RETN
        data = write_ncs(ncs)
        ncs2 = read_ncs(data)
        assert len(ncs2.instructions) == 2
        assert ncs2.instructions[0].opcode == 0x2D
        assert ncs2.instructions[1].opcode == 0x20

    def test_ncs_roundtrip_with_operands(self):
        """Instructions with operand bytes survive the round-trip."""
        from gmodular.formats.kotor_formats import NCSData, NCSInstruction, write_ncs, read_ncs
        ncs = NCSData()
        # CONSTI (0x04, qualifier 0x03) + 4-byte int operand
        ncs.instructions.append(NCSInstruction(0, 0x04, 0x03, struct.pack(">i", 42)))
        ncs.instructions.append(NCSInstruction(6, 0x20, 0x00, b""))   # RETN
        data = write_ncs(ncs)
        ncs2 = read_ncs(data)
        assert len(ncs2.instructions) >= 1
        # First instruction opcode should be 0x04 (CONST)
        assert ncs2.instructions[0].opcode == 0x04


# ═══════════════════════════════════════════════════════════════════════════
# 14.  MCP tool layer — LTR + NCS-write tools
# ═══════════════════════════════════════════════════════════════════════════

class TestMCPLTRNCSTools:
    """MCP handlers for kotor_read_ltr, kotor_write_ltr, kotor_write_ncs."""

    def test_read_ltr_no_data_error(self):
        from gmodular.mcp.tools.formats import handle_read_ltr
        result = _run(handle_read_ltr({}))
        payload = json.loads(result["content"][0]["text"])
        assert "error" in payload

    def test_read_ltr_from_b64(self):
        from gmodular.formats.kotor_formats import LTRData, write_ltr
        from gmodular.mcp.tools.formats import handle_read_ltr
        ltr = LTRData()
        data = write_ltr(ltr)
        b64 = base64.b64encode(data).decode()
        result = _run(handle_read_ltr({"data_b64": b64}))
        payload = json.loads(result["content"][0]["text"])
        assert "letter_count" in payload
        assert payload["letter_count"] == 28
        assert "single" in payload

    def test_write_ltr_default(self):
        from gmodular.mcp.tools.formats import handle_write_ltr
        result = _run(handle_write_ltr({}))
        payload = json.loads(result["content"][0]["text"])
        assert "ltr_b64" in payload
        data = base64.b64decode(payload["ltr_b64"])
        assert data[:8] == b"LTR V1.0"

    def test_write_ltr_custom_letter_count(self):
        from gmodular.mcp.tools.formats import handle_write_ltr
        result = _run(handle_write_ltr({"letter_count": 26}))
        payload = json.loads(result["content"][0]["text"])
        assert payload["letter_count"] == 26

    def test_write_ncs_empty_instructions(self):
        from gmodular.mcp.tools.formats import handle_write_ncs
        result = _run(handle_write_ncs({"instructions": []}))
        payload = json.loads(result["content"][0]["text"])
        assert "ncs_b64" in payload
        data = base64.b64decode(payload["ncs_b64"])
        assert data[:8] == b"NCS V1.0"

    def test_write_ncs_with_instructions(self):
        from gmodular.mcp.tools.formats import handle_write_ncs
        result = _run(handle_write_ncs({
            "instructions": [
                {"opcode": 0x2D, "subtype": 0x00},  # NOP
                {"opcode": 0x20, "subtype": 0x00},  # RETN
            ]
        }))
        payload = json.loads(result["content"][0]["text"])
        assert payload["instruction_count"] == 2
        data = base64.b64decode(payload["ncs_b64"])
        # Verify re-read
        from gmodular.formats.kotor_formats import read_ncs
        ncs = read_ncs(data)
        assert len(ncs.instructions) == 2

    def test_new_tools_in_registry(self):
        from gmodular.mcp.tools import get_all_tools
        names = {t["name"] for t in get_all_tools()}
        assert "kotor_read_ltr" in names
        assert "kotor_write_ltr" in names
        assert "kotor_write_ncs" in names

    def test_tool_dispatch_ltr(self):
        from gmodular.mcp.tools import handle_tool
        result = _run(handle_tool("kotor_write_ltr", {}))
        assert result is not None

    def test_tool_dispatch_ncs_write(self):
        from gmodular.mcp.tools import handle_tool
        result = _run(handle_tool("kotor_write_ncs", {"instructions": []}))
        assert result is not None
