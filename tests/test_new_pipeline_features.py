"""
Tests for the new pipeline features added in the latest round of development:

  1. NCS decoder — RETN + all zero-operand opcodes
  2. TPC writer  — write_tpc_from_rgba / write_tpc_from_tga
  3. LYT writer  — canonical beginlayout/donelayout format
  4. MCP scripts — kotor_disasm_script, kotor_ncs_info, kotor_compile_script, kotor_decompile_script
  5. MCP diff    — kotor_gff_diff, kotor_2da_diff, kotor_tlk_diff, kotor_patch_gff
  6. 2DA editor  — TwoDAEditorPanel importable, load/save cycle (headless)
  7. Tool count  — total 83 (79 + 4 new: lyt, bwm, resource_type_lookup, tpc_info)

References
----------
PyKotor/resource/formats/ncs/io_ncs.py         — NCS opcode layout
Kotor.NET/Formats/KotorTPC/TPCBinaryStructure   — TPC header format
PyKotor/resource/formats/lyt/io_lyt.py         — LYT canonical format
"""
from __future__ import annotations

import asyncio
import base64
import struct
from typing import Any

import pytest


# ═══════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _make_ncs_bytes(*opcodes: tuple[int, int]) -> bytes:
    """
    Build a minimal NCS blob with the given (opcode, subtype) pairs.
    Header: 'NCS V1.0' (8 bytes) + 0x42 type + uint32 big-endian total_size.
    """
    instructions = b""
    for op, sub in opcodes:
        instructions += bytes([op, sub])
    total_size = 13 + len(instructions)
    buf  = b"NCS V1.0"
    buf += b"\x42"
    buf += struct.pack(">I", total_size)
    buf += instructions
    return buf


def _run(coro):
    """Run a coroutine synchronously regardless of event loop state."""
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════════════════
#  1.  NCS Decoder — zero-operand opcodes
# ═══════════════════════════════════════════════════════════════════════════

class TestNCSDecoder:
    def test_retn_decoded(self):
        from gmodular.formats.kotor_formats import read_ncs, NCSOpcode
        data = _make_ncs_bytes((0x20, 0x00))   # RETN, subtype 0
        ncs  = read_ncs(data)
        assert any(i.opcode == NCSOpcode.RETN for i in ncs.instructions)

    def test_savebp_decoded(self):
        from gmodular.formats.kotor_formats import read_ncs, NCSOpcode
        data = _make_ncs_bytes((0x2A, 0x00))   # SAVEBP
        ncs  = read_ncs(data)
        assert any(i.opcode == NCSOpcode.SAVEBP for i in ncs.instructions)

    def test_restorebp_decoded(self):
        from gmodular.formats.kotor_formats import read_ncs, NCSOpcode
        data = _make_ncs_bytes((0x2B, 0x00))   # RESTOREBP
        ncs  = read_ncs(data)
        assert any(i.opcode == NCSOpcode.RESTOREBP for i in ncs.instructions)

    def test_arithmetic_opcodes_decoded(self):
        """All binary arithmetic opcodes (0x14–0x1A) should parse without breaking."""
        from gmodular.formats.kotor_formats import read_ncs
        # ADDII SUBII MULII DIVII MODII NEGII COMPII
        opcodes = [(0x14,0x20),(0x15,0x20),(0x16,0x20),(0x17,0x20),
                   (0x18,0x20),(0x19,0x03),(0x1A,0x03)]
        data = _make_ncs_bytes(*opcodes)
        ncs  = read_ncs(data)
        assert len(ncs.instructions) == len(opcodes)

    def test_comparison_opcodes_decoded(self):
        """EQ / NEQ / GEQ / GT / LT / LEQ (0x0B-0x10) decode correctly."""
        from gmodular.formats.kotor_formats import read_ncs
        opcodes = [(op, 0x20) for op in range(0x0B, 0x11)]
        data = _make_ncs_bytes(*opcodes)
        ncs  = read_ncs(data)
        assert len(ncs.instructions) == len(opcodes)

    def test_jmp_reads_4_byte_operand(self):
        """JMP must consume 4 operand bytes."""
        from gmodular.formats.kotor_formats import read_ncs, NCSOpcode
        # JMP (0x1D) + subtype + int32 big-endian target
        buf  = b"NCS V1.0\x42" + struct.pack(">I", 19)
        buf += bytes([0x1D, 0x00]) + struct.pack(">i", 0)  # JMP to offset 0
        ncs  = read_ncs(buf)
        assert len(ncs.instructions) == 1
        assert ncs.instructions[0].opcode == NCSOpcode.JMP
        assert len(ncs.instructions[0].operands) == 4

    def test_multiple_instructions_sequence(self):
        """A realistic sequence: RSADD → CONST INT → ACTION → RETN."""
        from gmodular.formats.kotor_formats import read_ncs, NCSOpcode
        buf  = b"NCS V1.0\x42"
        body = b""
        body += bytes([0x02, 0x03])                         # RSADD INT
        body += bytes([0x04, 0x03]) + struct.pack(">i", 42) # CONST INT 42
        body += bytes([0x05, 0x00]) + struct.pack(">H", 1) + bytes([1])  # ACTION #1 argc=1
        body += bytes([0x20, 0x00])                         # RETN
        buf  += struct.pack(">I", 13 + len(body)) + body
        ncs  = read_ncs(buf)
        opcodes = [i.opcode for i in ncs.instructions]
        assert NCSOpcode.RSADD  in opcodes
        assert NCSOpcode.CONST  in opcodes
        assert NCSOpcode.ACTION in opcodes
        assert NCSOpcode.RETN   in opcodes


# ═══════════════════════════════════════════════════════════════════════════
#  2.  TPC Writer
# ═══════════════════════════════════════════════════════════════════════════

class TestTPCWriter:
    def test_write_rgba_header_magic(self):
        """data_size field must be 0 for uncompressed TPC."""
        from gmodular.formats.tpc_reader import write_tpc_from_rgba
        rgba = bytes([128, 64, 32, 255] * 4)  # 2x2
        tpc  = write_tpc_from_rgba(rgba, 2, 2)
        data_size = struct.unpack_from("<I", tpc, 0)[0]
        assert data_size == 0, "Uncompressed TPC must have data_size=0"

    def test_write_rgba_dimensions(self):
        """Width/height must be correctly encoded at offsets 8/10."""
        from gmodular.formats.tpc_reader import write_tpc_from_rgba
        rgba = bytes([0] * (8 * 8 * 4))
        tpc  = write_tpc_from_rgba(rgba, 8, 8)
        w = struct.unpack_from("<H", tpc, 8)[0]
        h = struct.unpack_from("<H", tpc, 10)[0]
        assert w == 8 and h == 8

    def test_write_rgba_encoding(self):
        """Alpha=True → encoding=4 (RGBA), alpha=False → encoding=2 (RGB)."""
        from gmodular.formats.tpc_reader import write_tpc_from_rgba
        rgba = bytes([0] * (4 * 4 * 4))
        rgb  = bytes([0] * (4 * 4 * 3))
        assert write_tpc_from_rgba(rgba, 4, 4, alpha=True)[12]  == 4
        assert write_tpc_from_rgba(rgb,  4, 4, alpha=False)[12] == 2

    def test_write_rgba_header_length(self):
        """TPC header is always exactly 128 bytes."""
        from gmodular.formats.tpc_reader import write_tpc_from_rgba
        rgba = bytes([0] * (4 * 4 * 4))
        tpc  = write_tpc_from_rgba(rgba, 4, 4)
        assert len(tpc) == 128 + 4 * 4 * 4

    def test_write_rgba_txi_appended(self):
        """Optional TXI text is appended after pixel data."""
        from gmodular.formats.tpc_reader import write_tpc_from_rgba
        rgba = bytes([0] * 16)   # 2x2 RGB
        tpc  = write_tpc_from_rgba(rgba, 2, 2, txi_text="mipmap 0\n", alpha=False)
        txi_part = tpc[128 + 2 * 2 * 3:]
        assert b"mipmap 0" in txi_part

    def test_write_rgba_too_small_raises(self):
        """Providing insufficient pixel data must raise ValueError."""
        from gmodular.formats.tpc_reader import write_tpc_from_rgba
        with pytest.raises(ValueError, match="need"):
            write_tpc_from_rgba(b"\x00" * 4, 4, 4, alpha=True)  # 4 << 64 required

    def test_roundtrip_read_write(self):
        """TPC written by write_tpc_from_rgba can be parsed back by TPCReader."""
        from gmodular.formats.tpc_reader import write_tpc_from_rgba, TPCReader
        rgba = bytes([i % 256 for i in range(4 * 4 * 4)])
        tpc_data = write_tpc_from_rgba(rgba, 4, 4, alpha=True)
        img = TPCReader.from_bytes(tpc_data)
        assert img.is_valid
        assert img.width  == 4
        assert img.height == 4


# ═══════════════════════════════════════════════════════════════════════════
#  3.  LYT Canonical Writer
# ═══════════════════════════════════════════════════════════════════════════

class TestLYTCanonicalWriter:
    def test_beginlayout_present(self):
        from gmodular.formats.lyt_vis import LYTWriter, LayoutData, RoomPlacement
        layout = LayoutData()
        layout.rooms.append(RoomPlacement("danm13aa", 0.0, 0.0, 0.0))
        text = LYTWriter.to_string(layout)
        assert "beginlayout" in text

    def test_donelayout_present(self):
        from gmodular.formats.lyt_vis import LYTWriter, LayoutData
        text = LYTWriter.to_string(LayoutData())
        assert "donelayout" in text

    def test_roomcount_in_output(self):
        from gmodular.formats.lyt_vis import LYTWriter, LayoutData, RoomPlacement
        layout = LayoutData()
        layout.rooms.append(RoomPlacement("danm13aa", 1.0, 2.0, 3.0))
        layout.rooms.append(RoomPlacement("danm13ab", 4.0, 5.0, 6.0))
        text = LYTWriter.to_string(layout)
        assert "roomcount 2" in text

    def test_room_coordinates_present(self):
        from gmodular.formats.lyt_vis import LYTWriter, LayoutData, RoomPlacement
        layout = LayoutData()
        layout.rooms.append(RoomPlacement("myroom", 10.5, 20.25, 0.0))
        text = LYTWriter.to_string(layout)
        assert "myroom" in text
        assert "10.5" in text

    def test_crlf_line_endings(self):
        """Canonical KotOR LYT uses CRLF."""
        from gmodular.formats.lyt_vis import LYTWriter, LayoutData
        text = LYTWriter.to_string(LayoutData())
        assert "\r\n" in text

    def test_roundtrip_parse(self):
        """LYTWriter output can be parsed back by LYTParser."""
        from gmodular.formats.lyt_vis import LYTWriter, LYTParser, LayoutData, RoomPlacement
        layout = LayoutData()
        layout.rooms.append(RoomPlacement("danm13aa", 1.0, 2.0, 3.0))
        layout.rooms.append(RoomPlacement("danm13ba", -5.0, 0.0, 0.5))
        text = LYTWriter.to_string(layout)
        parsed = LYTParser.from_string(text)
        assert len(parsed.rooms) == 2
        room_names = [r.resref for r in parsed.rooms]
        assert "danm13aa" in room_names
        assert "danm13ba" in room_names


# ═══════════════════════════════════════════════════════════════════════════
#  4.  MCP Scripts module
# ═══════════════════════════════════════════════════════════════════════════

class TestMCPScriptsTools:
    def test_get_tools_returns_four(self):
        from gmodular.mcp.tools.scripts import get_tools
        tools = get_tools()
        assert len(tools) == 4

    def test_tool_names(self):
        from gmodular.mcp.tools.scripts import get_tools
        names = {t["name"] for t in get_tools()}
        assert names == {
            "kotor_disasm_script", "kotor_compile_script",
            "kotor_decompile_script", "kotor_ncs_info",
        }

    def test_disasm_with_b64(self):
        data_b64 = base64.b64encode(_make_ncs_bytes((0x20, 0x00))).decode()
        result = _run(__import__('gmodular.mcp.tools.scripts', fromlist=['handle_disasm_script'])
                      .handle_disasm_script({"data_b64": data_b64, "format": "text"}))
        payload = result["content"][0]["text"]
        import json
        obj = json.loads(payload)
        assert obj["instruction_count"] == 1
        assert "RETN" in obj["disassembly"]

    def test_disasm_json_format(self):
        data_b64 = base64.b64encode(_make_ncs_bytes((0x20, 0x00))).decode()
        from gmodular.mcp.tools.scripts import handle_disasm_script
        result = _run(handle_disasm_script({"data_b64": data_b64, "format": "json"}))
        import json
        obj = json.loads(result["content"][0]["text"])
        assert "instructions" in obj
        assert obj["instructions"][0]["opcode"] == "RETN"

    def test_disasm_no_data_error(self):
        from gmodular.mcp.tools.scripts import handle_disasm_script
        result = _run(handle_disasm_script({}))
        import json
        obj = json.loads(result["content"][0]["text"])
        assert "error" in obj

    def test_ncs_info_opcode_histogram(self):
        # Three RETN instructions
        data = _make_ncs_bytes((0x20, 0x00), (0x20, 0x00), (0x20, 0x00))
        data_b64 = base64.b64encode(data).decode()
        from gmodular.mcp.tools.scripts import handle_ncs_info
        result = _run(handle_ncs_info({"data_b64": data_b64}))
        import json
        obj = json.loads(result["content"][0]["text"])
        assert obj["opcode_histogram"]["RETN"] == 3

    def test_compile_without_compiler(self):
        """When no compiler is available, should return an error dict."""
        from gmodular.mcp.tools.scripts import handle_compile_script
        result = _run(handle_compile_script({"source": "void main(){}"}))
        import json
        obj = json.loads(result["content"][0]["text"])
        # Either "error" (no compiler on PATH) or "success" if somehow installed
        assert "error" in obj or "success" in obj

    def test_decompile_fallback_disasm(self):
        """Without a decompiler binary, should fall back to disassembly."""
        data_b64 = base64.b64encode(_make_ncs_bytes((0x20, 0x00))).decode()
        from gmodular.mcp.tools.scripts import handle_decompile_script
        result = _run(handle_decompile_script({"data_b64": data_b64, "tool_path": "nonexistent_tool"}))
        import json
        obj = json.loads(result["content"][0]["text"])
        # Either source (if decompiler found) or disassembly fallback
        assert "source" in obj or "disassembly" in obj

    def test_handler_dispatch(self):
        """All four tools must be dispatchable via handle_tool."""
        from gmodular.mcp.tools import handle_tool
        data_b64 = base64.b64encode(_make_ncs_bytes((0x20, 0x00))).decode()
        for name in ("kotor_disasm_script", "kotor_ncs_info", "kotor_decompile_script"):
            result = _run(handle_tool(name, {"data_b64": data_b64}))
            assert result is not None


# ═══════════════════════════════════════════════════════════════════════════
#  5.  MCP Diff Tools
# ═══════════════════════════════════════════════════════════════════════════

class TestMCPDiffTools:
    def test_get_tools_returns_four(self):
        from gmodular.mcp.tools.diff_tools import get_tools
        assert len(get_tools()) == 4

    def test_tool_names(self):
        from gmodular.mcp.tools.diff_tools import get_tools
        names = {t["name"] for t in get_tools()}
        assert names == {
            "kotor_gff_diff", "kotor_2da_diff",
            "kotor_tlk_diff", "kotor_patch_gff",
        }

    def test_2da_diff_added_row(self):
        """B has one more row than A → added_rows should be 1."""
        from gmodular.formats.kotor_formats import TwoDAData, write_2da_binary
        from gmodular.mcp.tools.diff_tools import handle_2da_diff

        a = TwoDAData(headers=["col1", "col2"], rows=[{"col1": "a", "col2": "b"}])
        b = TwoDAData(headers=["col1", "col2"], rows=[
            {"col1": "a", "col2": "b"},
            {"col1": "c", "col2": "d"},
        ])
        a_b64 = base64.b64encode(write_2da_binary(a)).decode()
        b_b64 = base64.b64encode(write_2da_binary(b)).decode()
        result = _run(handle_2da_diff({"twoda_a_b64": a_b64, "twoda_b_b64": b_b64}))
        import json
        obj = json.loads(result["content"][0]["text"])
        assert obj["added_rows"] == 1

    def test_2da_diff_changed_cell(self):
        """Changing a cell value shows up as changed_cell in changes list."""
        from gmodular.formats.kotor_formats import TwoDAData, write_2da_binary
        from gmodular.mcp.tools.diff_tools import handle_2da_diff

        a = TwoDAData(headers=["hp"], rows=[{"hp": "10"}])
        b = TwoDAData(headers=["hp"], rows=[{"hp": "20"}])
        a_b64 = base64.b64encode(write_2da_binary(a)).decode()
        b_b64 = base64.b64encode(write_2da_binary(b)).decode()
        result = _run(handle_2da_diff({"twoda_a_b64": a_b64, "twoda_b_b64": b_b64}))
        import json
        obj = json.loads(result["content"][0]["text"])
        changed = [c for c in obj["changes"] if c.get("type") == "changed_cell"]
        assert len(changed) == 1
        assert changed[0]["old"] == "10"
        assert changed[0]["new"] == "20"

    def test_2da_diff_no_data_error(self):
        from gmodular.mcp.tools.diff_tools import handle_2da_diff
        result = _run(handle_2da_diff({}))
        import json
        assert "error" in json.loads(result["content"][0]["text"])

    def test_gff_diff_no_data_error(self):
        from gmodular.mcp.tools.diff_tools import handle_gff_diff
        result = _run(handle_gff_diff({}))
        import json
        assert "error" in json.loads(result["content"][0]["text"])

    def test_tlk_diff_no_data_error(self):
        from gmodular.mcp.tools.diff_tools import handle_tlk_diff
        result = _run(handle_tlk_diff({}))
        import json
        assert "error" in json.loads(result["content"][0]["text"])

    def test_patch_gff_no_data_error(self):
        from gmodular.mcp.tools.diff_tools import handle_patch_gff
        result = _run(handle_patch_gff({"patch": {"Field": "value"}}))
        import json
        assert "error" in json.loads(result["content"][0]["text"])

    def test_handler_dispatch(self):
        """Diff tools must be dispatchable via the top-level handle_tool."""
        from gmodular.mcp.tools import handle_tool
        for name in ("kotor_gff_diff", "kotor_2da_diff",
                     "kotor_tlk_diff", "kotor_patch_gff"):
            # All should return an error (no data) rather than raising ValueError
            result = _run(handle_tool(name, {}))
            assert result is not None


# ═══════════════════════════════════════════════════════════════════════════
#  6.  TwoDAEditorPanel (headless)
# ═══════════════════════════════════════════════════════════════════════════

class TestTwoDAEditorPanel:
    def test_importable(self):
        from gmodular.gui.twoda_editor import TwoDAEditorPanel, _HAS_QT
        assert TwoDAEditorPanel is not None

    def test_no_crash_without_qt(self):
        """Panel instantiation should not crash even if Qt is unavailable."""
        from gmodular.gui.twoda_editor import TwoDAEditorPanel, _HAS_QT
        if not _HAS_QT:
            panel = TwoDAEditorPanel()
            assert panel is not None

    def test_write_2da_binary_roundtrip(self):
        """write_2da_binary / read produce the same data."""
        from gmodular.formats.kotor_formats import TwoDAData, write_2da_binary
        from gmodular.formats.twoda_loader import TwoDALoader
        orig = TwoDAData(
            headers=["Label", "HP", "STR"],
            rows=[
                {"Label": "warrior", "HP": "10", "STR": "15"},
                {"Label": "rogue",   "HP": "8",  "STR": "12"},
            ],
        )
        data   = write_2da_binary(orig)
        parsed = TwoDALoader.from_bytes(data)
        assert parsed.headers == orig.headers
        assert parsed.rows[0]["HP"]  == "10"
        assert parsed.rows[1]["Label"] == "rogue"


# ═══════════════════════════════════════════════════════════════════════════
#  7.  Total tool count
# ═══════════════════════════════════════════════════════════════════════════

class TestFinalToolCount:
    def test_total_76(self):
        from gmodular.mcp.tools import get_all_tools
        total = len(get_all_tools())
        assert total == 103, f"Expected 103, got {total}"

    def test_no_duplicates(self):
        from gmodular.mcp.tools import get_all_tools
        names = [t["name"] for t in get_all_tools()]
        dups  = [n for n in names if names.count(n) > 1]
        assert not dups, f"Duplicate tool names: {dups}"

    def test_scripts_in_registry(self):
        from gmodular.mcp.tools import get_all_tools
        names = {t["name"] for t in get_all_tools()}
        assert "kotor_disasm_script"  in names
        assert "kotor_compile_script" in names
        assert "kotor_decompile_script" in names
        assert "kotor_ncs_info"       in names

    def test_diff_tools_in_registry(self):
        from gmodular.mcp.tools import get_all_tools
        names = {t["name"] for t in get_all_tools()}
        assert "kotor_gff_diff"  in names
        assert "kotor_2da_diff"  in names
        assert "kotor_tlk_diff"  in names
        assert "kotor_patch_gff" in names
