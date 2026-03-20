"""
GModular — Roadmap Pass 4 Tests
================================
Offline tests (no Qt, no GPU, no game files) for all Phase-2 improvements
implemented in this session:

  1. DLGGraphData.to_gff_bytes()  — round-trip DLG serialisation
  2. DLGNodeData extensions       — camera_style, anim_list, listener, sound
  3. kotor_dlg_write MCP tool     — schema + handler round-trip
  4. kotor_read_pth MCP tool      — schema + handler
  5. Tool count = 85              — regression guard
"""
from __future__ import annotations

import asyncio
import base64
import json
import struct
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
#  1  DLGGraphData.to_gff_bytes()
# ═══════════════════════════════════════════════════════════════════════════════

class TestDLGToGffBytes:
    """DLGGraphData serialises back to valid GFF V3.2 binary."""

    def _minimal_graph(self):
        from gmodular.gui.dlg_editor import DLGGraphData
        g = DLGGraphData(starters=[0])
        e = g.add_entry("Hello, traveller!")
        r = g.add_reply("I need your help.")
        g.link(e.node_id, r.node_id)
        g.link(r.node_id, e.node_id)
        return g

    def test_returns_bytes(self):
        g = self._minimal_graph()
        data = g.to_gff_bytes()
        assert isinstance(data, bytes)
        assert len(data) > 56  # at least one full GFF header

    def test_magic_and_file_type(self):
        """Output must start with GFF V3.2 header: file_type='DLG ', version='V3.2'."""
        g = self._minimal_graph()
        data = g.to_gff_bytes()
        assert data[0:4] == b"DLG "
        assert data[4:8] == b"V3.2"

    def test_round_trip_text(self):
        """Parsing the written GFF recovers the original node text."""
        from gmodular.gui.dlg_editor import DLGGraphData
        g = DLGGraphData(starters=[0])
        e = g.add_entry("Unique NPC line XYZ")
        r = g.add_reply("Unique reply ABC")
        g.link(e.node_id, r.node_id)

        data  = g.to_gff_bytes()
        g2    = DLGGraphData.from_gff_bytes(data)
        texts = [n.text for n in g2.nodes.values()]
        assert "Unique NPC line XYZ" in texts
        assert "Unique reply ABC" in texts

    def test_round_trip_entry_reply_counts(self):
        """Entry and reply counts are preserved after round-trip."""
        from gmodular.gui.dlg_editor import DLGGraphData
        g = DLGGraphData(starters=[0])
        e0 = g.add_entry("Line 0")
        e1 = g.add_entry("Line 1")
        r0 = g.add_reply("Reply 0")
        g.link(e0.node_id, r0.node_id)
        g.link(r0.node_id, e1.node_id)

        data = g.to_gff_bytes()
        g2   = DLGGraphData.from_gff_bytes(data)
        entries = [n for n in g2.nodes.values() if n.is_entry]
        replies = [n for n in g2.nodes.values() if not n.is_entry]
        assert len(entries) == 2
        assert len(replies) == 1

    def test_round_trip_script_field(self):
        """Script ResRef is preserved in round-trip."""
        from gmodular.gui.dlg_editor import DLGGraphData
        g = DLGGraphData()
        e = g.add_entry("Triggered line")
        e.script = "k_trg_onuse"
        data = g.to_gff_bytes()
        g2   = DLGGraphData.from_gff_bytes(data)
        scripts = [n.script for n in g2.nodes.values()]
        assert "k_trg_onuse" in scripts

    def test_empty_graph(self):
        """Empty graph produces a minimal valid GFF binary."""
        from gmodular.gui.dlg_editor import DLGGraphData
        g    = DLGGraphData()
        data = g.to_gff_bytes()
        assert isinstance(data, bytes)
        assert data[0:4] == b"DLG "

    def test_metadata_round_trip(self):
        """on_abort, on_end, skippable, conversation_type are preserved."""
        from gmodular.gui.dlg_editor import DLGGraphData
        g = DLGGraphData(
            on_abort="abort_scr",
            on_end="end_scr",
            skippable=False,
            conversation_type=1,
        )
        data = g.to_gff_bytes()
        g2   = DLGGraphData.from_gff_bytes(data)
        assert g2.on_abort == "abort_scr"
        assert g2.on_end   == "end_scr"
        assert g2.skippable is False
        assert g2.conversation_type == 1


# ═══════════════════════════════════════════════════════════════════════════════
#  2  DLGNodeData extensions
# ═══════════════════════════════════════════════════════════════════════════════

class TestDLGNodeDataExtensions:
    """camera_style, anim_list, listener, sound are part of the data model."""

    def test_camera_style_default(self):
        from gmodular.gui.dlg_editor import DLGNodeData
        n = DLGNodeData()
        assert n.camera_style == 0

    def test_anim_list_default(self):
        from gmodular.gui.dlg_editor import DLGNodeData
        n = DLGNodeData()
        assert n.anim_list == []

    def test_listener_field(self):
        from gmodular.gui.dlg_editor import DLGNodeData
        n = DLGNodeData(listener="NPC_LISTENER")
        assert n.listener == "NPC_LISTENER"

    def test_sound_field(self):
        from gmodular.gui.dlg_editor import DLGNodeData
        n = DLGNodeData(sound="nfo_cantina_bnt")
        assert n.sound == "nfo_cantina_bnt"

    def test_to_dict_includes_camera_style_when_nonzero(self):
        from gmodular.gui.dlg_editor import DLGNodeData
        n = DLGNodeData(camera_style=3)
        d = n.to_dict()
        assert d["camera_style"] == 3

    def test_to_dict_omits_camera_style_when_zero(self):
        from gmodular.gui.dlg_editor import DLGNodeData
        n = DLGNodeData(camera_style=0)
        d = n.to_dict()
        assert "camera_style" not in d

    def test_to_dict_includes_anim_list_when_present(self):
        from gmodular.gui.dlg_editor import DLGNodeData
        n = DLGNodeData(anim_list=[{"id": 88, "participant": "npc1"}])
        d = n.to_dict()
        assert d["anim_list"] == [{"id": 88, "participant": "npc1"}]

    def test_camera_style_in_gff_round_trip(self):
        """camera_style written to GFF and read back correctly (K2 extension)."""
        from gmodular.gui.dlg_editor import DLGGraphData
        g = DLGGraphData()
        e = g.add_entry("Camera line")
        e.camera_style = 5
        data = g.to_gff_bytes()
        g2   = DLGGraphData.from_gff_bytes(data)
        entry = next(n for n in g2.nodes.values() if n.is_entry)
        assert entry.camera_style == 5

    def test_from_dict_round_trip_extended_fields(self):
        """from_dict ↔ to_dict preserves all new fields."""
        from gmodular.gui.dlg_editor import DLGGraphData, DLGNodeData
        g  = DLGGraphData()
        e  = g.add_entry("Extended node")
        e.listener     = "L1"
        e.sound        = "snd_01"
        e.quest        = "q_main"
        e.quest_entry  = 7
        e.camera_style = 2
        e.anim_list    = [{"id": 10, "participant": "pc"}]

        d  = g.to_dict()
        g2 = DLGGraphData.from_dict(d)
        n  = next(iter(g2.nodes.values()))
        assert n.listener     == "L1"
        assert n.sound        == "snd_01"
        assert n.quest        == "q_main"
        assert n.quest_entry  == 7
        assert n.camera_style == 2
        assert n.anim_list    == [{"id": 10, "participant": "pc"}]


# ═══════════════════════════════════════════════════════════════════════════════
#  3  kotor_dlg_write MCP tool
# ═══════════════════════════════════════════════════════════════════════════════

class TestDLGWriteTool:
    """kotor_dlg_write handler round-trips a DLG graph to GFF bytes."""

    def _run(self, coro):
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                raise RuntimeError("closed")
            return loop.run_until_complete(coro)
        except RuntimeError:
            return asyncio.run(coro)

    def _make_graph_json(self) -> str:
        from gmodular.gui.dlg_editor import DLGGraphData
        g = DLGGraphData(starters=[0])
        e = g.add_entry("Tool test line")
        r = g.add_reply("Tool test reply")
        g.link(e.node_id, r.node_id)
        return json.dumps(g.to_dict())

    def test_schema_exists(self):
        from gmodular.gui.dlg_editor import get_dlg_editor_tools
        names = [t["name"] for t in get_dlg_editor_tools()]
        assert "kotor_dlg_write" in names

    def test_schema_required_fields(self):
        from gmodular.gui.dlg_editor import get_dlg_editor_tools
        schema = next(t for t in get_dlg_editor_tools() if t["name"] == "kotor_dlg_write")
        assert "graph_json" in schema["inputSchema"]["required"]

    def test_handler_returns_ok(self):
        from gmodular.gui.dlg_editor import handle_dlg_write
        graph_json = self._make_graph_json()
        result = self._run(handle_dlg_write({"graph_json": graph_json}))
        content = json.loads(result["content"][0]["text"])
        assert content.get("ok") is True

    def test_handler_returns_b64(self):
        from gmodular.gui.dlg_editor import handle_dlg_write
        graph_json = self._make_graph_json()
        result = self._run(handle_dlg_write({"graph_json": graph_json}))
        content = json.loads(result["content"][0]["text"])
        assert "dlg_b64" in content
        data = base64.b64decode(content["dlg_b64"])
        assert data[0:4] == b"DLG "

    def test_handler_returns_counts(self):
        from gmodular.gui.dlg_editor import handle_dlg_write
        graph_json = self._make_graph_json()
        result = self._run(handle_dlg_write({"graph_json": graph_json}))
        content = json.loads(result["content"][0]["text"])
        assert content["entry_count"] == 1
        assert content["reply_count"] == 1

    def test_round_trip_via_handlers(self):
        """parse → write → parse produces identical graph."""
        from gmodular.gui.dlg_editor import handle_dlg_parse, handle_dlg_write
        graph_json = self._make_graph_json()

        # Write to GFF binary
        write_result = self._run(handle_dlg_write({"graph_json": graph_json}))
        dlg_b64 = json.loads(write_result["content"][0]["text"])["dlg_b64"]

        # Parse back
        parse_result = self._run(handle_dlg_parse({"dlg_b64": dlg_b64}))
        parsed = json.loads(parse_result["content"][0]["text"])
        assert parsed.get("ok") is True
        assert parsed["entry_count"] == 1
        assert parsed["reply_count"] == 1

    def test_error_on_bad_json(self):
        from gmodular.gui.dlg_editor import handle_dlg_write
        result = self._run(handle_dlg_write({"graph_json": "not-valid-json{"}))
        content = json.loads(result["content"][0]["text"])
        assert "error" in content


# ═══════════════════════════════════════════════════════════════════════════════
#  4  kotor_read_pth MCP tool
# ═══════════════════════════════════════════════════════════════════════════════

class TestReadPthTool:
    """kotor_read_pth parses a GFF-wrapped PTH path graph."""

    def _run(self, coro):
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                raise RuntimeError("closed")
            return loop.run_until_complete(coro)
        except RuntimeError:
            return asyncio.run(coro)

    def _make_minimal_pth_gff_bytes(self) -> bytes:
        """Build a minimal PTH GFF binary with 2 waypoints and 1 connection."""
        from gmodular.formats.gff_writer import GFFWriter
        from gmodular.formats.gff_types import GFFRoot, GFFStruct, GFFField, GFFFieldType

        pt0 = GFFStruct()
        pt0.fields["X"] = GFFField("X", GFFFieldType.FLOAT, 1.0)
        pt0.fields["Y"] = GFFField("Y", GFFFieldType.FLOAT, 2.0)
        conn0 = GFFStruct()
        conn0.fields["Destination"] = GFFField("Destination", GFFFieldType.INT, 1)
        pt0.fields["Conections"] = GFFField("Conections", GFFFieldType.LIST, [conn0])

        pt1 = GFFStruct()
        pt1.fields["X"] = GFFField("X", GFFFieldType.FLOAT, 3.0)
        pt1.fields["Y"] = GFFField("Y", GFFFieldType.FLOAT, 4.0)
        conn1 = GFFStruct()
        conn1.fields["Destination"] = GFFField("Destination", GFFFieldType.INT, 0)
        pt1.fields["Conections"] = GFFField("Conections", GFFFieldType.LIST, [conn1])

        root = GFFRoot(file_type="PTH ")
        root.fields["Path_Points"] = GFFField("Path_Points", GFFFieldType.LIST, [pt0, pt1])
        return GFFWriter(root).to_bytes()

    def test_schema_exists(self):
        from gmodular.mcp.tools.formats import get_tools
        names = [t["name"] for t in get_tools()]
        assert "kotor_read_pth" in names

    def test_schema_has_properties(self):
        from gmodular.mcp.tools.formats import get_tools
        schema = next(t for t in get_tools() if t["name"] == "kotor_read_pth")
        props = schema["inputSchema"]["properties"]
        assert "data_b64" in props
        assert "resref" in props

    def test_handler_parses_waypoints(self):
        from gmodular.mcp.tools.formats import handle_read_pth
        data   = self._make_minimal_pth_gff_bytes()
        b64    = base64.b64encode(data).decode()
        result = self._run(handle_read_pth({"data_b64": b64}))
        content = json.loads(result["content"][0]["text"])
        assert content.get("waypoint_count") == 2

    def test_handler_waypoint_coords(self):
        from gmodular.mcp.tools.formats import handle_read_pth
        data = self._make_minimal_pth_gff_bytes()
        b64  = base64.b64encode(data).decode()
        result  = self._run(handle_read_pth({"data_b64": b64}))
        content = json.loads(result["content"][0]["text"])
        wp0 = content["waypoints"][0]
        assert abs(wp0["x"] - 1.0) < 0.01
        assert abs(wp0["y"] - 2.0) < 0.01

    def test_handler_connections(self):
        from gmodular.mcp.tools.formats import handle_read_pth
        data   = self._make_minimal_pth_gff_bytes()
        b64    = base64.b64encode(data).decode()
        result = self._run(handle_read_pth({"data_b64": b64}))
        content = json.loads(result["content"][0]["text"])
        wp0 = content["waypoints"][0]
        assert 1 in wp0["connections"]

    def test_handler_error_on_no_data(self):
        from gmodular.mcp.tools.formats import handle_read_pth
        result  = self._run(handle_read_pth({}))
        content = json.loads(result["content"][0]["text"])
        assert "error" in content

    def test_dispatch_routes_to_handler(self):
        """MCP dispatcher routes kotor_read_pth to the correct handler."""
        import asyncio
        from gmodular.mcp.tools import handle_tool
        data   = self._make_minimal_pth_gff_bytes()
        b64    = base64.b64encode(data).decode()
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                raise RuntimeError("closed")
            result = loop.run_until_complete(
                handle_tool("kotor_read_pth", {"data_b64": b64})
            )
        except RuntimeError:
            result = asyncio.run(handle_tool("kotor_read_pth", {"data_b64": b64}))
        content = json.loads(result["content"][0]["text"])
        assert content.get("waypoint_count") == 2


# ═══════════════════════════════════════════════════════════════════════════════
#  5  Total tool count regression guard
# ═══════════════════════════════════════════════════════════════════════════════

def test_total_tool_count_85():
    """Total MCP tool count must be exactly 103 after Ghostworks IPC tool additions."""
    from gmodular.mcp.tools import get_all_tools
    total = len(get_all_tools())
    assert total == 103, f"Expected 103 tools, got {total}"


def test_kotor_dlg_write_in_all_tools():
    """kotor_dlg_write must appear in get_all_tools()."""
    from gmodular.mcp.tools import get_all_tools
    names = [t["name"] for t in get_all_tools()]
    assert "kotor_dlg_write" in names


def test_kotor_read_pth_in_all_tools():
    """kotor_read_pth must appear in get_all_tools()."""
    from gmodular.mcp.tools import get_all_tools
    names = [t["name"] for t in get_all_tools()]
    assert "kotor_read_pth" in names
