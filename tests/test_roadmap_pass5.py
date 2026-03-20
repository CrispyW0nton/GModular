"""
Phase-2 roadmap — pass 5 validation tests
==========================================

Covers the Phase-2 improvements derived from reference-repo deep-dives:

1. Resource-type map correctness — pth=3003, lip=3004, rim=3002, nss/ncs IDs
2. kotor_write_pth  — PTH path-graph GFF serialiser + round-trip
3. kotor_write_bwm  — BWM V1.0 walkmesh binary exporter + structural checks
4. kotor_write_lyt  — LYT text writer + round-trip
5. DLG AnimList / CameraStyle in DLGNodeData (GUI properties panel)
6. Tool count regression guard (88 tools total)
"""

from __future__ import annotations

import asyncio
import base64
import json
import struct

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run a coroutine in any test context (Python 3.12-safe)."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            raise RuntimeError("already running")
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════════════════════
#  1  Resource-type map correctness
# ═══════════════════════════════════════════════════════════════════════════════

class TestResourceTypeMap:
    """Canonical resource-type IDs verified against KotOR modding wiki."""

    def _map(self):
        from gmodular.formats.archives import RES_TYPE_MAP, EXT_TO_TYPE
        return RES_TYPE_MAP, EXT_TO_TYPE

    def test_nss_is_2009(self):
        _, ext = self._map()
        assert ext.get("nss") == 2009, "nss (NWScript source) must be 2009"

    def test_ncs_is_2010(self):
        _, ext = self._map()
        assert ext.get("ncs") == 2010, "ncs (compiled script) must be 2010"

    def test_pth_is_3003(self):
        _, ext = self._map()
        assert ext.get("pth") == 3003, "pth (area path/waypoints) must be 3003"

    def test_lip_is_3004(self):
        _, ext = self._map()
        assert ext.get("lip") == 3004, "lip (lip-sync) must be 3004"

    def test_rim_is_3002(self):
        _, ext = self._map()
        assert ext.get("rim") == 3002, "rim (module resource image) must be 3002"

    def test_lyt_is_3000(self):
        _, ext = self._map()
        assert ext.get("lyt") == 3000

    def test_vis_is_3001(self):
        _, ext = self._map()
        assert ext.get("vis") == 3001

    def test_tpc_is_3007(self):
        _, ext = self._map()
        assert ext.get("tpc") == 3007

    def test_mdx_is_3008(self):
        _, ext = self._map()
        assert ext.get("mdx") == 3008

    def test_dlg_is_2029(self):
        _, ext = self._map()
        assert ext.get("dlg") == 2029

    def test_wok_is_2016(self):
        _, ext = self._map()
        assert ext.get("wok") == 2016

    def test_erf_is_9997(self):
        _, ext = self._map()
        assert ext.get("erf") == 9997

    def test_no_duplicate_extensions(self):
        rtm, _ = self._map()
        exts = list(rtm.values())
        # Allow bwm to map via wok, but no true duplicates
        dups = [e for e in exts if exts.count(e) > 1]
        assert dups == [], f"Duplicate extensions: {set(dups)}"

    def test_reverse_map_pth(self):
        rtm, _ = self._map()
        assert rtm.get(3003) == "pth"

    def test_reverse_map_lip(self):
        rtm, _ = self._map()
        assert rtm.get(3004) == "lip"

    def test_reverse_map_rim(self):
        rtm, _ = self._map()
        assert rtm.get(3002) == "rim"


# ═══════════════════════════════════════════════════════════════════════════════
#  2  kotor_write_pth — PTH path-graph serialiser
# ═══════════════════════════════════════════════════════════════════════════════

class TestWritePthTool:
    """kotor_write_pth serialises a waypoint graph to a .pth GFF binary."""

    def _tool(self):
        from gmodular.mcp.tools.formats import handle_write_pth
        return handle_write_pth

    def _read_tool(self):
        from gmodular.mcp.tools.formats import handle_read_pth
        return handle_read_pth

    def _schema(self):
        from gmodular.mcp.tools.formats import get_tools
        return next(t for t in get_tools() if t["name"] == "kotor_write_pth")

    # --- schema ---

    def test_schema_exists(self):
        schema = self._schema()
        assert schema["name"] == "kotor_write_pth"

    def test_schema_requires_waypoints(self):
        schema = self._schema()
        assert "waypoints" in schema["inputSchema"].get("required", [])

    def test_schema_has_waypoints_property(self):
        schema = self._schema()
        assert "waypoints" in schema["inputSchema"]["properties"]

    # --- handler ---

    def test_handler_returns_data_b64(self):
        fn = self._tool()
        wps = [{"x": 0.0, "y": 0.0, "connections": [1]},
               {"x": 5.0, "y": 5.0, "connections": [0]}]
        result = _run(fn({"waypoints": wps}))
        content = json.loads(result["content"][0]["text"])
        assert "data_b64" in content

    def test_handler_reports_waypoint_count(self):
        fn = self._tool()
        wps = [{"x": 0.0, "y": 0.0, "connections": []},
               {"x": 1.0, "y": 1.0, "connections": []},
               {"x": 2.0, "y": 2.0, "connections": []}]
        result = _run(fn({"waypoints": wps}))
        content = json.loads(result["content"][0]["text"])
        assert content["waypoint_count"] == 3

    def test_handler_produces_gff_magic(self):
        fn = self._tool()
        wps = [{"x": 0.0, "y": 0.0, "connections": []}]
        result = _run(fn({"waypoints": wps}))
        content = json.loads(result["content"][0]["text"])
        data = base64.b64decode(content["data_b64"])
        assert data[:8] == b"PTH V3.2", f"Wrong magic: {data[:8]!r}"

    def test_round_trip_waypoint_count(self):
        w_fn = self._tool()
        r_fn = self._read_tool()
        wps = [
            {"x": 0.0, "y": 0.0, "connections": [1, 2]},
            {"x": 10.0, "y": 0.0, "connections": [0, 2]},
            {"x": 5.0, "y": 10.0, "connections": [0, 1]},
        ]
        write_result = _run(w_fn({"waypoints": wps}))
        b64 = json.loads(write_result["content"][0]["text"])["data_b64"]
        read_result = _run(r_fn({"data_b64": b64}))
        content = json.loads(read_result["content"][0]["text"])
        assert content["waypoint_count"] == 3

    def test_round_trip_coordinates(self):
        w_fn = self._tool()
        r_fn = self._read_tool()
        wps = [{"x": 3.14, "y": 2.71, "connections": []}]
        write_result = _run(w_fn({"waypoints": wps}))
        b64 = json.loads(write_result["content"][0]["text"])["data_b64"]
        read_result = _run(r_fn({"data_b64": b64}))
        content = json.loads(read_result["content"][0]["text"])
        wp = content["waypoints"][0]
        assert abs(wp["x"] - 3.14) < 0.001
        assert abs(wp["y"] - 2.71) < 0.001

    def test_round_trip_connections(self):
        w_fn = self._tool()
        r_fn = self._read_tool()
        wps = [
            {"x": 0.0, "y": 0.0, "connections": [1]},
            {"x": 1.0, "y": 0.0, "connections": [0]},
        ]
        write_result = _run(w_fn({"waypoints": wps}))
        b64 = json.loads(write_result["content"][0]["text"])["data_b64"]
        read_result = _run(r_fn({"data_b64": b64}))
        content = json.loads(read_result["content"][0]["text"])
        wp0 = content["waypoints"][0]
        assert 1 in wp0["connections"]

    def test_error_on_missing_waypoints(self):
        fn = self._tool()
        result = _run(fn({}))
        content = json.loads(result["content"][0]["text"])
        assert "error" in content

    def test_dispatch_via_handle_tool(self):
        from gmodular.mcp.tools import handle_tool
        wps = [{"x": 0.0, "y": 0.0, "connections": []}]
        result = _run(handle_tool("kotor_write_pth", {"waypoints": wps}))
        content = json.loads(result["content"][0]["text"])
        assert "data_b64" in content

    def test_write_pth_to_bytes_directly(self):
        """Test the low-level write_pth_to_bytes function."""
        from gmodular.formats.kotor_formats import PTHData, write_pth_to_bytes
        pth = PTHData()
        pth.add_point(1.0, 2.0)
        pth.add_point(3.0, 4.0)
        pth.connect(0, 1)
        data = write_pth_to_bytes(pth)
        assert data[:8] == b"PTH V3.2"
        assert len(data) > 50


# ═══════════════════════════════════════════════════════════════════════════════
#  3  kotor_write_bwm — BWM V1.0 walkmesh binary exporter
# ═══════════════════════════════════════════════════════════════════════════════

class TestWriteBwmTool:
    """kotor_write_bwm serialises a JSON mesh to a binary BWM V1.0 file."""

    _VERTS = [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [10.0, 10.0, 0.0], [0.0, 10.0, 0.0]]
    _FACES = [
        {"v0": 0, "v1": 1, "v2": 2, "material": 0},
        {"v0": 0, "v1": 2, "v2": 3, "material": 0},
    ]

    def _tool(self):
        from gmodular.mcp.tools.formats import handle_write_bwm
        return handle_write_bwm

    def _schema(self):
        from gmodular.mcp.tools.formats import get_tools
        return next(t for t in get_tools() if t["name"] == "kotor_write_bwm")

    # --- schema ---

    def test_schema_exists(self):
        assert self._schema()["name"] == "kotor_write_bwm"

    def test_schema_requires_vertices_and_faces(self):
        schema = self._schema()
        required = schema["inputSchema"].get("required", [])
        assert "vertices" in required
        assert "faces" in required

    def test_schema_has_bwm_type_enum(self):
        schema = self._schema()
        bwm_type = schema["inputSchema"]["properties"]["bwm_type"]
        assert "wok" in bwm_type["enum"]
        assert "dwk" in bwm_type["enum"]
        assert "pwk" in bwm_type["enum"]

    # --- handler ---

    def test_handler_returns_data_b64(self):
        fn = self._tool()
        result = _run(fn({"vertices": self._VERTS, "faces": self._FACES}))
        content = json.loads(result["content"][0]["text"])
        assert "data_b64" in content

    def test_handler_reports_face_count(self):
        fn = self._tool()
        result = _run(fn({"vertices": self._VERTS, "faces": self._FACES}))
        content = json.loads(result["content"][0]["text"])
        assert content["face_count"] == 2

    def test_handler_reports_walkable_faces(self):
        fn = self._tool()
        result = _run(fn({"vertices": self._VERTS, "faces": self._FACES}))
        content = json.loads(result["content"][0]["text"])
        assert content["walkable_faces"] == 2

    def test_handler_non_walkable_material(self):
        fn = self._tool()
        faces = [
            {"v0": 0, "v1": 1, "v2": 2, "material": 6},   # NonWalk
        ]
        result = _run(fn({"vertices": [[0,0,0],[1,0,0],[0,1,0]], "faces": faces}))
        content = json.loads(result["content"][0]["text"])
        assert content["walkable_faces"] == 0

    def test_handler_walkable_flag_false(self):
        """walkable=false with no explicit material should map to material 6."""
        fn = self._tool()
        faces = [{"v0": 0, "v1": 1, "v2": 2, "walkable": False}]
        result = _run(fn({"vertices": [[0,0,0],[1,0,0],[0,1,0]], "faces": faces}))
        content = json.loads(result["content"][0]["text"])
        assert content["walkable_faces"] == 0

    def test_output_begins_with_bwm_magic(self):
        fn = self._tool()
        result = _run(fn({"vertices": self._VERTS, "faces": self._FACES}))
        content = json.loads(result["content"][0]["text"])
        data = base64.b64decode(content["data_b64"])
        assert data[:4] == b"BWM ", f"Bad magic: {data[:4]!r}"
        assert data[4:8] == b"V1.0", f"Bad version: {data[4:8]!r}"

    def test_output_size_reasonable(self):
        fn = self._tool()
        result = _run(fn({"vertices": self._VERTS, "faces": self._FACES}))
        content = json.loads(result["content"][0]["text"])
        assert content["size_bytes"] > 100

    def test_dwk_type(self):
        fn = self._tool()
        result = _run(fn({"vertices": self._VERTS, "faces": self._FACES, "bwm_type": "dwk"}))
        content = json.loads(result["content"][0]["text"])
        assert content.get("bwm_type") == "dwk"
        assert content.get("ext") == "dwk"

    def test_pwk_type(self):
        fn = self._tool()
        result = _run(fn({"vertices": self._VERTS, "faces": self._FACES, "bwm_type": "pwk"}))
        content = json.loads(result["content"][0]["text"])
        assert content.get("bwm_type") == "pwk"

    def test_error_on_out_of_range_vertex(self):
        fn = self._tool()
        faces = [{"v0": 0, "v1": 1, "v2": 99}]  # vertex 99 doesn't exist
        result = _run(fn({"vertices": [[0,0,0],[1,0,0]], "faces": faces}))
        content = json.loads(result["content"][0]["text"])
        assert "error" in content

    def test_error_on_missing_inputs(self):
        fn = self._tool()
        result = _run(fn({}))
        content = json.loads(result["content"][0]["text"])
        assert "error" in content

    def test_dispatch_via_handle_tool(self):
        from gmodular.mcp.tools import handle_tool
        result = _run(handle_tool("kotor_write_bwm",
                                  {"vertices": self._VERTS, "faces": self._FACES}))
        content = json.loads(result["content"][0]["text"])
        assert "data_b64" in content

    def test_2d_vertices_default_z_zero(self):
        """Vertices with only x,y should default z=0."""
        fn = self._tool()
        verts = [[0.0, 0.0], [10.0, 0.0], [5.0, 10.0]]
        faces = [{"v0": 0, "v1": 1, "v2": 2}]
        result = _run(fn({"vertices": verts, "faces": faces}))
        content = json.loads(result["content"][0]["text"])
        assert "data_b64" in content


# ═══════════════════════════════════════════════════════════════════════════════
#  4  kotor_write_lyt — LYT text writer
# ═══════════════════════════════════════════════════════════════════════════════

class TestWriteLytTool:
    """kotor_write_lyt serialises a room layout to a .lyt text file."""

    _ROOMS = [
        {"resref": "danm13aa", "x": 0.0, "y": 0.0, "z": 0.0},
        {"resref": "danm13ab", "x": 24.0, "y": 0.0, "z": 0.0},
    ]

    def _tool(self):
        from gmodular.mcp.tools.formats import handle_write_lyt
        return handle_write_lyt

    def _read_tool(self):
        from gmodular.mcp.tools.formats import handle_read_lyt
        return handle_read_lyt

    def _schema(self):
        from gmodular.mcp.tools.formats import get_tools
        return next(t for t in get_tools() if t["name"] == "kotor_write_lyt")

    # --- schema ---

    def test_schema_exists(self):
        assert self._schema()["name"] == "kotor_write_lyt"

    def test_schema_requires_rooms(self):
        schema = self._schema()
        assert "rooms" in schema["inputSchema"].get("required", [])

    # --- handler ---

    def test_handler_returns_lyt_text(self):
        fn = self._tool()
        result = _run(fn({"rooms": self._ROOMS}))
        content = json.loads(result["content"][0]["text"])
        assert "lyt_text" in content

    def test_lyt_text_has_beginlayout(self):
        fn = self._tool()
        result = _run(fn({"rooms": self._ROOMS}))
        content = json.loads(result["content"][0]["text"])
        assert "beginlayout" in content["lyt_text"]
        assert "donelayout" in content["lyt_text"]

    def test_lyt_text_contains_rooms(self):
        fn = self._tool()
        result = _run(fn({"rooms": self._ROOMS}))
        content = json.loads(result["content"][0]["text"])
        text = content["lyt_text"]
        assert "danm13aa" in text
        assert "danm13ab" in text

    def test_lyt_text_crlf_endings(self):
        fn = self._tool()
        result = _run(fn({"rooms": self._ROOMS}))
        content = json.loads(result["content"][0]["text"])
        # KotOR canonical LYT uses CRLF
        assert "\r\n" in content["lyt_text"]

    def test_reports_room_count(self):
        fn = self._tool()
        result = _run(fn({"rooms": self._ROOMS}))
        content = json.loads(result["content"][0]["text"])
        assert content["room_count"] == 2

    def test_handler_returns_data_b64(self):
        fn = self._tool()
        result = _run(fn({"rooms": self._ROOMS}))
        content = json.loads(result["content"][0]["text"])
        assert "data_b64" in content

    def test_round_trip_room_count(self):
        w_fn = self._tool()
        r_fn = self._read_tool()
        write_result = _run(w_fn({"rooms": self._ROOMS}))
        b64 = json.loads(write_result["content"][0]["text"])["data_b64"]
        read_result = _run(r_fn({"data_b64": b64}))
        content = json.loads(read_result["content"][0]["text"])
        assert content["room_count"] == 2

    def test_round_trip_room_resref(self):
        w_fn = self._tool()
        r_fn = self._read_tool()
        write_result = _run(w_fn({"rooms": self._ROOMS}))
        b64 = json.loads(write_result["content"][0]["text"])["data_b64"]
        read_result = _run(r_fn({"data_b64": b64}))
        content = json.loads(read_result["content"][0]["text"])
        assert content["rooms"][0]["resref"] == "danm13aa"

    def test_round_trip_room_position(self):
        w_fn = self._tool()
        r_fn = self._read_tool()
        write_result = _run(w_fn({"rooms": self._ROOMS}))
        b64 = json.loads(write_result["content"][0]["text"])["data_b64"]
        read_result = _run(r_fn({"data_b64": b64}))
        content = json.loads(read_result["content"][0]["text"])
        assert abs(content["rooms"][1]["x"] - 24.0) < 0.001

    def test_door_hooks_written(self):
        fn = self._tool()
        hooks = [{"name": "door01", "room": "danm13aa",
                  "x": 5.0, "y": 0.0, "z": 0.0,
                  "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0}]
        result = _run(fn({"rooms": self._ROOMS, "door_hooks": hooks}))
        content = json.loads(result["content"][0]["text"])
        assert content["door_hook_count"] == 1
        assert "door01" in content["lyt_text"]

    def test_error_on_missing_rooms(self):
        fn = self._tool()
        result = _run(fn({}))
        content = json.loads(result["content"][0]["text"])
        assert "error" in content

    def test_dispatch_via_handle_tool(self):
        from gmodular.mcp.tools import handle_tool
        result = _run(handle_tool("kotor_write_lyt", {"rooms": self._ROOMS}))
        content = json.loads(result["content"][0]["text"])
        assert "lyt_text" in content


# ═══════════════════════════════════════════════════════════════════════════════
#  5  DLG AnimList / CameraStyle in DLGNodeData
# ═══════════════════════════════════════════════════════════════════════════════

class TestDLGAnimListCameraStyle:
    """DLGNodeData exposes camera_style and anim_list fields."""

    def test_dlg_node_has_camera_style(self):
        from gmodular.gui.dlg_editor import DLGNodeData
        node = DLGNodeData()
        assert hasattr(node, "camera_style")
        assert node.camera_style == 0

    def test_dlg_node_has_anim_list(self):
        from gmodular.gui.dlg_editor import DLGNodeData
        node = DLGNodeData()
        assert hasattr(node, "anim_list")
        assert isinstance(node.anim_list, list)

    def test_camera_style_serialised_in_to_dict(self):
        from gmodular.gui.dlg_editor import DLGNodeData
        node = DLGNodeData(camera_style=3)
        d = node.to_dict()
        assert d.get("camera_style") == 3

    def test_anim_list_serialised_in_to_dict(self):
        from gmodular.gui.dlg_editor import DLGNodeData
        node = DLGNodeData(anim_list=[{"id": 7, "participant": "npc"}])
        d = node.to_dict()
        assert d["anim_list"][0]["id"] == 7

    def test_camera_style_round_trip_via_gff(self):
        from gmodular.gui.dlg_editor import DLGGraphData
        g = DLGGraphData()
        e = g.add_entry(text="test")
        e.speaker = "npc"
        e.camera_style = 5
        data = g.to_gff_bytes()
        g2 = DLGGraphData.from_gff_bytes(data)
        entry_back = [n for n in g2.nodes.values() if n.is_entry][0]
        assert entry_back.camera_style == 5

    def test_anim_list_round_trip_via_gff(self):
        from gmodular.gui.dlg_editor import DLGGraphData
        g = DLGGraphData()
        e = g.add_entry(text="animated")
        e.speaker = "npc"
        e.anim_list = [{"id": 11, "participant": "npc_tag"}]
        data = g.to_gff_bytes()
        g2 = DLGGraphData.from_gff_bytes(data)
        entry_back = [n for n in g2.nodes.values() if n.is_entry][0]
        assert len(entry_back.anim_list) == 1
        assert entry_back.anim_list[0].get("id") == 11

    def test_dlg_graph_serialises_conversation_type(self):
        from gmodular.gui.dlg_editor import DLGGraphData
        g = DLGGraphData(conversation_type=2)
        data = g.to_gff_bytes()
        g2 = DLGGraphData.from_gff_bytes(data)
        assert g2.conversation_type == 2

    def test_dlg_graph_serialises_camera_model(self):
        from gmodular.gui.dlg_editor import DLGGraphData
        g = DLGGraphData(camera_model="cam_type_1")
        data = g.to_gff_bytes()
        g2 = DLGGraphData.from_gff_bytes(data)
        assert g2.camera_model == "cam_type_1"


# ═══════════════════════════════════════════════════════════════════════════════
#  6  Tool count regression guard
# ═══════════════════════════════════════════════════════════════════════════════

def test_total_tool_count_88():
    """Total MCP tool count must be exactly 103 after Ghostworks IPC tool additions."""
    from gmodular.mcp.tools import get_all_tools
    total = len(get_all_tools())
    assert total == 103, f"Expected 103 tools, got {total}"


def test_new_tools_in_all_tools():
    """The three new tools from pass-5 must appear in get_all_tools()."""
    from gmodular.mcp.tools import get_all_tools
    names = {t["name"] for t in get_all_tools()}
    assert "kotor_write_pth" in names
    assert "kotor_write_bwm" in names
    assert "kotor_write_lyt" in names


def test_no_duplicate_tool_names():
    """No two tools may have the same name."""
    from gmodular.mcp.tools import get_all_tools
    names = [t["name"] for t in get_all_tools()]
    dups = [n for n in names if names.count(n) > 1]
    assert dups == [], f"Duplicate tool names: {set(dups)}"
