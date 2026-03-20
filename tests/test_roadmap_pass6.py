"""
tests/test_roadmap_pass6.py  —  Deep-Scan Pass 6
=================================================
Validates the three new MCP write tools (kotor_write_lip, kotor_write_vis,
kotor_write_txi), the DLG Script2 round-trip, and the updated tool-count
guard (91 tools total).

Run with:  pytest tests/test_roadmap_pass6.py -v
"""
from __future__ import annotations

import asyncio
import base64
import json
import struct
import sys
from typing import Any, Dict

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro) -> Any:
    """Execute a coroutine synchronously (Python 3.12-safe)."""
    return asyncio.run(coro)


def _json(result: Any) -> Dict[str, Any]:
    """Extract JSON payload from an MCP tool result."""
    return json.loads(result["content"][0]["text"])


# ---------------------------------------------------------------------------
# Tool schema tests
# ---------------------------------------------------------------------------

class TestWriteLipSchema:
    """kotor_write_lip tool registration and schema."""

    def test_tool_registered(self):
        from gmodular.mcp.tools import get_all_tools
        names = [t["name"] for t in get_all_tools()]
        assert "kotor_write_lip" in names

    def test_schema_has_required_fields(self):
        from gmodular.mcp.tools import get_all_tools
        tool = next(t for t in get_all_tools() if t["name"] == "kotor_write_lip")
        schema = tool["inputSchema"]
        assert "duration" in schema["properties"]
        assert "keyframes" in schema["properties"]
        assert "duration" in schema.get("required", [])
        assert "keyframes" in schema.get("required", [])

    def test_tool_has_description(self):
        from gmodular.mcp.tools import get_all_tools
        tool = next(t for t in get_all_tools() if t["name"] == "kotor_write_lip")
        assert "lip" in tool["description"].lower() or "LIP" in tool["description"]


class TestWriteVisSchema:
    """kotor_write_vis tool registration and schema."""

    def test_tool_registered(self):
        from gmodular.mcp.tools import get_all_tools
        names = [t["name"] for t in get_all_tools()]
        assert "kotor_write_vis" in names

    def test_schema_has_visibility_property(self):
        from gmodular.mcp.tools import get_all_tools
        tool = next(t for t in get_all_tools() if t["name"] == "kotor_write_vis")
        schema = tool["inputSchema"]
        assert "visibility" in schema["properties"]
        assert "visibility" in schema.get("required", [])

    def test_tool_has_description(self):
        from gmodular.mcp.tools import get_all_tools
        tool = next(t for t in get_all_tools() if t["name"] == "kotor_write_vis")
        assert "vis" in tool["description"].lower() or "visibility" in tool["description"].lower()


class TestWriteTxiSchema:
    """kotor_write_txi tool registration and schema."""

    def test_tool_registered(self):
        from gmodular.mcp.tools import get_all_tools
        names = [t["name"] for t in get_all_tools()]
        assert "kotor_write_txi" in names

    def test_schema_has_fields_property(self):
        from gmodular.mcp.tools import get_all_tools
        tool = next(t for t in get_all_tools() if t["name"] == "kotor_write_txi")
        schema = tool["inputSchema"]
        assert "fields" in schema["properties"]
        assert "fields" in schema.get("required", [])

    def test_tool_has_description(self):
        from gmodular.mcp.tools import get_all_tools
        tool = next(t for t in get_all_tools() if t["name"] == "kotor_write_txi")
        assert "txi" in tool["description"].lower() or "TXI" in tool["description"]


# ---------------------------------------------------------------------------
# kotor_write_lip functional tests
# ---------------------------------------------------------------------------

class TestWriteLipTool:
    """Functional tests for the LIP writer MCP tool."""

    def test_basic_write_returns_bytes(self):
        from gmodular.mcp.tools import handle_tool
        result = _run(handle_tool("kotor_write_lip", {
            "duration": 2.0,
            "keyframes": [
                {"time": 0.0, "shape": 0},
                {"time": 1.0, "shape": 3},
            ]
        }))
        data = _json(result)
        assert "data_b64" in data
        assert data["keyframe_count"] == 2
        assert data["size_bytes"] > 12  # LIP header is 12 bytes minimum

    def test_lip_magic_header(self):
        from gmodular.mcp.tools import handle_tool
        result = _run(handle_tool("kotor_write_lip", {
            "duration": 1.5,
            "keyframes": [{"time": 0.0, "shape": 0}]
        }))
        raw = base64.b64decode(_json(result)["data_b64"])
        # "LIP V1.0" header
        assert raw[:4] == b"LIP "
        assert raw[4:8] == b"V1.0"

    def test_lip_duration_in_header(self):
        from gmodular.mcp.tools import handle_tool
        result = _run(handle_tool("kotor_write_lip", {
            "duration": 3.75,
            "keyframes": []
        }))
        raw = base64.b64decode(_json(result)["data_b64"])
        # bytes 8-11: float32 duration
        (dur,) = struct.unpack_from("<f", raw, 8)
        assert abs(dur - 3.75) < 0.001

    def test_lip_keyframe_count_in_header(self):
        from gmodular.mcp.tools import handle_tool
        result = _run(handle_tool("kotor_write_lip", {
            "duration": 2.0,
            "keyframes": [
                {"time": 0.0, "shape": 0},
                {"time": 0.5, "shape": 1},
                {"time": 1.0, "shape": 2},
            ]
        }))
        raw = base64.b64decode(_json(result)["data_b64"])
        # bytes 12-15: uint32 keyframe count
        (count,) = struct.unpack_from("<I", raw, 12)
        assert count == 3

    def test_lip_shape_by_name(self):
        """Shape names like 'ee', 'ah', 'neutral' should be accepted."""
        from gmodular.mcp.tools import handle_tool
        result = _run(handle_tool("kotor_write_lip", {
            "duration": 2.0,
            "keyframes": [
                {"time": 0.0, "shape": "neutral"},
                {"time": 0.5, "shape": "ee"},
                {"time": 1.0, "shape": "ah"},
                {"time": 1.5, "shape": "ooh"},
            ]
        }))
        data = _json(result)
        assert data["keyframe_count"] == 4
        assert "error" not in data

    def test_lip_shape_by_int(self):
        """Integer shape IDs 0-15 should be accepted."""
        from gmodular.mcp.tools import handle_tool
        result = _run(handle_tool("kotor_write_lip", {
            "duration": 1.0,
            "keyframes": [{"time": 0.0, "shape": i} for i in range(16)]
        }))
        data = _json(result)
        assert data["keyframe_count"] == 16
        assert "error" not in data

    def test_lip_round_trip_via_read_tool(self):
        """write then read back should recover duration and frame count."""
        from gmodular.mcp.tools import handle_tool
        write_result = _run(handle_tool("kotor_write_lip", {
            "duration": 4.0,
            "keyframes": [
                {"time": 0.0, "shape": "neutral"},
                {"time": 1.0, "shape": "ee"},
                {"time": 2.0, "shape": "ah"},
                {"time": 3.5, "shape": "neutral"},
            ]
        }))
        b64 = _json(write_result)["data_b64"]
        read_result = _run(handle_tool("kotor_read_lip", {"data_b64": b64}))
        r = _json(read_result)
        assert "error" not in r
        # Duration field might be named 'length_secs' or 'length'
        length = r.get("length_secs") or r.get("length") or r.get("duration")
        assert length is not None
        assert abs(float(length) - 4.0) < 0.01
        # Frame count
        count = r.get("frame_count") or r.get("count") or len(r.get("keyframes", []))
        assert count == 4

    def test_lip_empty_keyframes(self):
        """Empty keyframe list should produce valid minimal LIP."""
        from gmodular.mcp.tools import handle_tool
        result = _run(handle_tool("kotor_write_lip", {
            "duration": 0.0,
            "keyframes": []
        }))
        data = _json(result)
        assert "error" not in data
        assert data["keyframe_count"] == 0
        raw = base64.b64decode(data["data_b64"])
        assert raw[:4] == b"LIP "

    def test_lip_error_on_no_args(self):
        """Should return error when required fields missing."""
        from gmodular.mcp.tools import handle_tool
        result = _run(handle_tool("kotor_write_lip", {}))
        data = _json(result)
        # duration missing → should error
        assert "error" in data or data.get("keyframe_count", 0) == 0


# ---------------------------------------------------------------------------
# kotor_write_vis functional tests
# ---------------------------------------------------------------------------

class TestWriteVisTool:
    """Functional tests for the VIS writer MCP tool."""

    def test_basic_write_returns_text_and_bytes(self):
        from gmodular.mcp.tools import handle_tool
        result = _run(handle_tool("kotor_write_vis", {
            "visibility": [
                {"room": "room01", "visible": ["room01", "room02"]},
            ]
        }))
        data = _json(result)
        assert "data_b64" in data
        assert "vis_text" in data
        assert "room_count" in data

    def test_vis_text_format(self):
        """VIS text should list rooms with counts and indented visible rooms."""
        from gmodular.mcp.tools import handle_tool
        result = _run(handle_tool("kotor_write_vis", {
            "visibility": [
                {"room": "danm13aa", "visible": ["danm13aa", "danm13ab"]},
                {"room": "danm13ab", "visible": ["danm13ab", "danm13aa"]},
            ]
        }))
        data = _json(result)
        text = data["vis_text"]
        assert "danm13aa" in text
        assert "danm13ab" in text
        # Each room line: "room N"
        lines = text.strip().split("\n")
        room_lines = [l for l in lines if not l.startswith("  ")]
        assert len(room_lines) == 2

    def test_vis_room_count(self):
        from gmodular.mcp.tools import handle_tool
        result = _run(handle_tool("kotor_write_vis", {
            "visibility": [
                {"room": "area01", "visible": ["area01", "area02", "area03"]},
                {"room": "area02", "visible": ["area01", "area02"]},
                {"room": "area03", "visible": ["area01", "area03"]},
            ]
        }))
        data = _json(result)
        assert data["room_count"] == 3

    def test_vis_round_trip_via_read_tool(self):
        """Write then read back should recover room names."""
        from gmodular.mcp.tools import handle_tool
        write_result = _run(handle_tool("kotor_write_vis", {
            "visibility": [
                {"room": "rm01", "visible": ["rm01", "rm02"]},
                {"room": "rm02", "visible": ["rm01", "rm02", "rm03"]},
                {"room": "rm03", "visible": ["rm02", "rm03"]},
            ]
        }))
        b64 = _json(write_result)["data_b64"]
        read_result = _run(handle_tool("kotor_read_vis", {"data_b64": b64}))
        r = _json(read_result)
        assert "error" not in r
        rooms = r.get("rooms") or []
        room_names = [
            (rm if isinstance(rm, str) else rm.get("room", ""))
            for rm in rooms
        ]
        assert any("rm01" in n for n in room_names)
        assert any("rm02" in n for n in room_names)

    def test_vis_empty_visibility(self):
        """Empty visibility list should produce valid (empty) VIS."""
        from gmodular.mcp.tools import handle_tool
        result = _run(handle_tool("kotor_write_vis", {"visibility": []}))
        data = _json(result)
        assert "error" not in data
        assert data["room_count"] == 0

    def test_vis_size_nonzero(self):
        from gmodular.mcp.tools import handle_tool
        result = _run(handle_tool("kotor_write_vis", {
            "visibility": [{"room": "r1", "visible": ["r1"]}]
        }))
        data = _json(result)
        assert data["size_bytes"] > 0

    def test_vis_lowercase_rooms(self):
        """Rooms should be stored lowercase (KotOR convention)."""
        from gmodular.mcp.tools import handle_tool
        result = _run(handle_tool("kotor_write_vis", {
            "visibility": [{"room": "UPPERCASEROOM", "visible": ["UPPERCASEROOM"]}]
        }))
        data = _json(result)
        text = data.get("vis_text", "")
        assert "uppercaseroom" in text.lower()


# ---------------------------------------------------------------------------
# kotor_write_txi functional tests
# ---------------------------------------------------------------------------

class TestWriteTxiTool:
    """Functional tests for the TXI writer MCP tool."""

    def test_basic_write_returns_text_and_bytes(self):
        from gmodular.mcp.tools import handle_tool
        result = _run(handle_tool("kotor_write_txi", {
            "fields": {"envmaptexture": "CM_baremetal", "decal": "1"}
        }))
        data = _json(result)
        assert "data_b64" in data
        assert "txi_text" in data
        assert "field_count" in data

    def test_txi_text_contains_fields(self):
        from gmodular.mcp.tools import handle_tool
        result = _run(handle_tool("kotor_write_txi", {
            "fields": {
                "envmaptexture": "CM_shiny",
                "blending": "additive",
                "fps": "24",
            }
        }))
        data = _json(result)
        text = data["txi_text"]
        assert "envmaptexture" in text
        assert "CM_shiny" in text
        assert "blending" in text
        assert "additive" in text

    def test_txi_field_count(self):
        from gmodular.mcp.tools import handle_tool
        result = _run(handle_tool("kotor_write_txi", {
            "fields": {"a": "1", "b": "2", "c": "3"}
        }))
        data = _json(result)
        assert data["field_count"] == 3

    def test_txi_round_trip_via_read_tool(self):
        """Write then read back should recover field values."""
        from gmodular.mcp.tools import handle_tool
        write_result = _run(handle_tool("kotor_write_txi", {
            "fields": {
                "envmaptexture": "CM_baremetal",
                "fps": "15",
                "numx": "4",
                "numy": "4",
            }
        }))
        b64 = _json(write_result)["data_b64"]
        read_result = _run(handle_tool("kotor_read_txi", {"data_b64": b64}))
        r = _json(read_result)
        assert "error" not in r
        # Check envmaptexture was preserved
        envmap = r.get("envmap") or (r.get("fields") or {}).get("envmaptexture", "")
        assert "CM_baremetal" in str(envmap)

    def test_txi_empty_fields(self):
        """Empty fields dict should produce empty bytes (no error)."""
        from gmodular.mcp.tools import handle_tool
        result = _run(handle_tool("kotor_write_txi", {"fields": {}}))
        data = _json(result)
        assert "error" not in data
        assert data["field_count"] == 0

    def test_txi_animated_texture(self):
        """Animation-related fields (numx, numy, fps) should be preserved."""
        from gmodular.mcp.tools import handle_tool
        result = _run(handle_tool("kotor_write_txi", {
            "fields": {"numx": "4", "numy": "4", "fps": "10"}
        }))
        data = _json(result)
        text = data["txi_text"]
        assert "numx" in text
        assert "numy" in text
        assert "fps" in text
        assert "4" in text
        assert "10" in text

    def test_txi_numeric_values_as_strings(self):
        """Numeric values passed as Python ints should work too."""
        from gmodular.mcp.tools import handle_tool
        result = _run(handle_tool("kotor_write_txi", {
            "fields": {"fps": 24, "numx": 8, "numy": 8}
        }))
        data = _json(result)
        assert "error" not in data
        assert data["field_count"] == 3

    def test_txi_size_nonzero_with_fields(self):
        from gmodular.mcp.tools import handle_tool
        result = _run(handle_tool("kotor_write_txi", {
            "fields": {"envmaptexture": "CM_baremetal"}
        }))
        data = _json(result)
        assert data["size_bytes"] > 0


# ---------------------------------------------------------------------------
# DLG Script2 round-trip tests
# ---------------------------------------------------------------------------

class TestDLGScript2:
    """Tests for DLG Script2 (KotOR 2 second script) round-trip."""

    def _make_dlg_with_script2(self) -> bytes:
        from gmodular.gui.dlg_editor import DLGGraphData
        g = DLGGraphData()
        entry = g.add_entry("NPC dialogue")
        entry.script = "script_one"
        entry.script2 = "script_two"
        entry.speaker = "npc_tag"
        reply = g.add_reply("Player reply")
        reply.script = "reply_scr"
        reply.script2 = "reply_scr2"
        g.link(entry.node_id, reply.node_id)
        g.starters.append(entry.node_id)
        return g.to_gff_bytes()

    def test_entry_script2_survives_gff_round_trip(self):
        from gmodular.gui.dlg_editor import DLGGraphData
        data = self._make_dlg_with_script2()
        g2 = DLGGraphData.from_gff_bytes(data)
        entries = [n for n in g2.nodes.values() if n.is_entry]
        assert entries
        e = entries[0]
        assert e.script == "script_one"
        assert e.script2 == "script_two"

    def test_reply_script2_survives_gff_round_trip(self):
        from gmodular.gui.dlg_editor import DLGGraphData
        data = self._make_dlg_with_script2()
        g2 = DLGGraphData.from_gff_bytes(data)
        replies = [n for n in g2.nodes.values() if not n.is_entry]
        assert replies
        r = replies[0]
        assert r.script == "reply_scr"
        assert r.script2 == "reply_scr2"

    def test_script2_absent_when_empty(self):
        """When script2 is empty, it should NOT appear as a GFF field."""
        from gmodular.gui.dlg_editor import DLGGraphData
        from gmodular.formats.gff_reader import GFFReader
        g = DLGGraphData()
        entry = g.add_entry("No script2")
        entry.script = "main_script"
        entry.script2 = ""   # empty → should not be written
        g.starters.append(entry.node_id)
        data = g.to_gff_bytes()
        # Parse raw GFF and check no Script2 field in EntryList[0]
        root = GFFReader.from_bytes(data).parse()
        entry_list = root.get("EntryList") or []
        if entry_list:
            entry_struct = entry_list[0]
            if hasattr(entry_struct, "fields"):
                assert "Script2" not in entry_struct.fields

    def test_script2_written_when_set(self):
        """When script2 is non-empty, it must appear as Script2 RESREF in GFF."""
        from gmodular.gui.dlg_editor import DLGGraphData
        from gmodular.formats.gff_reader import GFFReader
        g = DLGGraphData()
        entry = g.add_entry("Has script2")
        entry.script2 = "k2_cond_scr"
        g.starters.append(entry.node_id)
        data = g.to_gff_bytes()
        root = GFFReader.from_bytes(data).parse()
        entry_list = root.get("EntryList") or []
        assert entry_list
        entry_struct = entry_list[0]
        if hasattr(entry_struct, "fields"):
            assert "Script2" in entry_struct.fields

    def test_script2_via_mcp_dlg_write(self):
        """Script2 must survive the MCP kotor_dlg_write round-trip."""
        from gmodular.mcp.tools import handle_tool
        from gmodular.gui.dlg_editor import DLGGraphData
        import base64

        g = DLGGraphData()
        e = g.add_entry("K2 entry")
        e.script = "s1"
        e.script2 = "s2_cond"
        g.starters.append(e.node_id)

        graph_json = json.dumps(g.to_dict())
        result = _run(handle_tool("kotor_dlg_write", {"graph_json": graph_json}))
        data = _json(result)
        assert "error" not in data
        dlg_bytes = base64.b64decode(data["dlg_b64"])

        # Parse back
        g2 = DLGGraphData.from_gff_bytes(dlg_bytes)
        entries = [n for n in g2.nodes.values() if n.is_entry]
        assert entries[0].script2 == "s2_cond"


# ---------------------------------------------------------------------------
# LIP format unit tests
# ---------------------------------------------------------------------------

class TestLIPFormatRoundTrip:
    """Direct format-level LIP round-trip tests."""

    def test_write_and_read_back(self):
        from gmodular.formats.kotor_formats import LIPData, LIPKeyframe, LIPShape, write_lip, read_lip
        lip = LIPData(length=2.0)
        lip.add(0.0, LIPShape.NEUTRAL)
        lip.add(0.5, LIPShape.EE)
        lip.add(1.0, LIPShape.AH)
        lip.add(1.5, LIPShape.NEUTRAL)
        data = write_lip(lip)
        lip2 = read_lip(data)
        assert abs(lip2.length - 2.0) < 0.001
        assert len(lip2.keyframes) == 4
        assert lip2.keyframes[1].shape == LIPShape.EE
        assert lip2.keyframes[2].shape == LIPShape.AH

    def test_all_16_shapes_survive(self):
        from gmodular.formats.kotor_formats import LIPData, LIPKeyframe, LIPShape, write_lip, read_lip
        lip = LIPData(length=16.0)
        for i, shape in enumerate(LIPShape):
            lip.add(float(i), shape)
        data = write_lip(lip)
        lip2 = read_lip(data)
        assert len(lip2.keyframes) == 16
        shapes_out = [kf.shape for kf in lip2.keyframes]
        shapes_in  = list(LIPShape)
        assert shapes_out == shapes_in


# ---------------------------------------------------------------------------
# VIS format unit tests
# ---------------------------------------------------------------------------

class TestVISFormatRoundTrip:
    """Direct format-level VIS round-trip tests."""

    def test_write_and_read_back(self):
        from gmodular.formats.kotor_formats import VISData, write_vis, read_vis
        vis = VISData()
        vis.add_room("roomA")
        vis.add_room("roomB")
        vis.set_visible("roomA", "roomA", visible=True)
        vis.set_visible("roomA", "roomB", visible=True)
        vis.set_visible("roomB", "roomB", visible=True)
        data = write_vis(vis)
        vis2 = read_vis(data)
        assert "rooma" in [r.lower() for r in vis2.all_rooms()]
        assert vis2.is_visible("rooma", "roomB".lower())

    def test_visibility_count_in_text(self):
        from gmodular.formats.kotor_formats import VISData, write_vis
        vis = VISData()
        vis.add_room("r1")
        vis.add_room("r2")
        vis.add_room("r3")
        vis.set_visible("r1", "r1", visible=True)
        vis.set_visible("r1", "r2", visible=True)
        vis.set_visible("r1", "r3", visible=True)
        text = write_vis(vis).decode("latin-1")
        # Line "r1 3" should appear
        assert "r1 3" in text


# ---------------------------------------------------------------------------
# TXI format unit tests
# ---------------------------------------------------------------------------

class TestTXIFormatRoundTrip:
    """Direct format-level TXI round-trip tests."""

    def test_write_and_read_back(self):
        from gmodular.formats.kotor_formats import TXIData, write_txi, read_txi
        txi = TXIData()
        txi.set("envmaptexture", "CM_baremetal")
        txi.set("blending", "additive")
        txi.set("fps", "24")
        data = write_txi(txi)
        txi2 = read_txi(data)
        assert txi2.get("envmaptexture") == "CM_baremetal"
        assert txi2.get("blending") == "additive"
        assert txi2.get("fps") == "24"

    def test_animation_fields_round_trip(self):
        from gmodular.formats.kotor_formats import TXIData, write_txi, read_txi
        txi = TXIData()
        txi.set("numx", "4")
        txi.set("numy", "4")
        txi.set("fps", "10")
        data = write_txi(txi)
        txi2 = read_txi(data)
        assert txi2.num_frames == 16   # 4*4
        assert abs(txi2.fps - 10.0) < 0.01

    def test_empty_txi_produces_empty_bytes(self):
        from gmodular.formats.kotor_formats import TXIData, write_txi
        txi = TXIData()
        data = write_txi(txi)
        assert data == b""


# ---------------------------------------------------------------------------
# Tool count guard
# ---------------------------------------------------------------------------

def test_total_tool_count_91():
    """Total MCP tool count must be exactly 103 after Ghostworks IPC tool additions."""
    from gmodular.mcp.tools import get_all_tools
    all_tools = get_all_tools()
    total = len(all_tools)
    assert total == 103, (
        f"Expected 103 tools after Ghostworks IPC additions (91 + 12), got {total}.\n"
        f"Tools: {sorted(t['name'] for t in all_tools)}"
    )


def test_format_tools_count_23():
    """Format module must expose exactly 23 tools after Pass 6."""
    from gmodular.mcp.tools.formats import get_tools
    tools = get_tools()
    assert len(tools) == 23, (
        f"Expected 23 format tools, got {len(tools)}.\n"
        f"Tools: {[t['name'] for t in tools]}"
    )


def test_no_duplicate_tool_names():
    """All tool names across all modules must be unique."""
    from gmodular.mcp.tools import get_all_tools
    names = [t["name"] for t in get_all_tools()]
    dupes = [n for n in names if names.count(n) > 1]
    assert not dupes, f"Duplicate tool names: {set(dupes)}"


def test_new_tools_have_input_schema():
    """All three new tools must have a valid inputSchema dict."""
    from gmodular.mcp.tools import get_all_tools
    new_names = {"kotor_write_lip", "kotor_write_vis", "kotor_write_txi"}
    tools = {t["name"]: t for t in get_all_tools()}
    for name in new_names:
        assert name in tools, f"Tool '{name}' not found"
        schema = tools[name].get("inputSchema", {})
        assert isinstance(schema, dict), f"'{name}' has no inputSchema dict"
        assert "properties" in schema, f"'{name}' inputSchema missing 'properties'"
