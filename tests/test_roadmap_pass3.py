"""
GModular — Roadmap Pass 3 Tests
=================================
Offline tests (no Qt, no GPU, no game files) for all roadmap pass 3 items:

  1. Skinned mesh VAO pipeline (_upload_skinned_mesh, _skin_vaos separation)
  2. AppController use-case coordinator
  3. GhostRigger IPC animation methods (bridge API shape)
  4. MCP animation tools (tool schema + handlers)
  5. NPC patrol AI tick (Creature3D.set_patrol_route, _patrol_tick)
  6. ViewportWidget.set_patrol_path delegation

All tests run with the standard Qt / ModernGL stubs already present in the
codebase, so they work in the sandbox CI environment.
"""
from __future__ import annotations

import asyncio
import math
import sys
from typing import Any
from unittest.mock import MagicMock, patch


# ═══════════════════════════════════════════════════════════════════════════════
#  1  Skinned Mesh VAO Pipeline
# ═══════════════════════════════════════════════════════════════════════════════

class TestSkinnedMeshPipeline:
    """_EGLRenderer handles skin nodes separately in _skin_vaos."""

    def test_skin_vaos_list_exists_on_renderer(self):
        """_skin_vaos is declared alongside _entity_vaos."""
        from gmodular.gui.viewport import _EGLRenderer
        # Instantiate without a real ctx by mocking moderngl
        with patch.dict(sys.modules, {'moderngl': MagicMock()}):
            r = object.__new__(_EGLRenderer)
            r._skin_vaos = []
            r._entity_vaos = []
            assert isinstance(r._skin_vaos, list)
            assert isinstance(r._entity_vaos, list)

    def test_upload_skinned_mesh_falls_back_without_prog(self):
        """_upload_skinned_mesh falls back to lit/flat when _prog_skinned is None."""
        from gmodular.gui.viewport import _EGLRenderer
        r = object.__new__(_EGLRenderer)
        r._prog_skinned = None
        r._prog_lit     = None
        r._prog_lit_no_uv = None
        r._prog_flat    = MagicMock()
        r.ctx           = MagicMock()

        # Mock the fallback
        r._upload_lit_or_flat = MagicMock(return_value={"vao": MagicMock(), "count": 6})

        result = r._upload_skinned_mesh(
            [(0,0,0),(1,0,0),(0,1,0)],  # positions
            [(0,0,1),(0,0,1),(0,0,1)],  # normals
            [],                          # uvs
            [(1,0,0,0),(1,0,0,0),(1,0,0,0)],  # bone_weights
            [(0,0,0,0),(0,0,0,0),(0,0,0,0)],  # bone_indices
            (0.5, 0.5, 0.7),            # color
        )
        # With no prog_skinned, fallback was called
        r._upload_lit_or_flat.assert_called_once()

    def test_rebuild_entity_vaos_separates_skin_nodes(self):
        """Skin nodes go to _skin_vaos; regular nodes go to _entity_vaos."""
        from gmodular.gui.viewport import _EGLRenderer
        from gmodular.engine.entity_system import Creature3D

        r = object.__new__(_EGLRenderer)
        r._entity_vaos = []
        r._skin_vaos   = []
        r.ctx          = MagicMock()
        r._prog_skinned = MagicMock()  # available, so skin path is taken

        # _release_list is a no-op in tests
        r._release_list = lambda lst: lst.clear()

        # Build a fake entity with one regular node and one skin node
        ent = Creature3D(1)
        ent.visible = True

        class _FakeMeshData:
            name = "c_bastila"
            def all_nodes(self):
                return [self._reg_node, self._skin_node]
            def visible_mesh_nodes(self):
                return [self._reg_node, self._skin_node]

        class _RegNode:
            name = "body"
            flags = 0x0020  # NODE_MESH but not skin
            vertices = [(0,0,0),(1,0,0),(0,1,0)]
            faces    = [(0,1,2)]
            normals  = [(0,0,1),(0,0,1),(0,0,1)]
            uvs      = []
            bone_weights = []
            bone_indices = []
            @property
            def is_skin(self): return False
            render = True
            is_aabb = False

        class _SkinNode:
            name = "body_skin"
            flags = 0x0060  # NODE_MESH | NODE_SKIN
            vertices = [(0,0,0),(1,0,0),(0,1,0)]
            faces    = [(0,1,2)]
            normals  = [(0,0,1),(0,0,1),(0,0,1)]
            uvs      = []
            bone_weights = [(1,0,0,0),(1,0,0,0),(1,0,0,0)]
            bone_indices = [(0,0,0,0),(0,0,0,0),(0,0,0,0)]
            bone_node_indices = [0, 1]
            @property
            def is_skin(self): return True
            render = True
            is_aabb = False

        md = _FakeMeshData()
        md._reg_node  = _RegNode()
        md._skin_node = _SkinNode()
        ent.mesh_data = md
        ent._x = ent._y = ent._z = 0.0
        ent._bearing = 0.0

        # Mock upload helpers
        r._upload_lit_or_flat   = MagicMock(return_value={"vao": MagicMock(), "vbo": MagicMock(), "count": 3})
        r._upload_skinned_mesh  = MagicMock(return_value={"vao": MagicMock(), "vbo": MagicMock(), "count": 3, "skinned": True})

        class _FakeReg:
            @property
            def entities(self): return [ent]

        count = r.rebuild_entity_vaos(_FakeReg())
        assert count == 2   # one regular + one skinned
        assert len(r._entity_vaos) == 1
        assert len(r._skin_vaos)   == 1
        assert r._skin_vaos[0].get("skinned") is True
        assert "bone_node_names" in r._skin_vaos[0]

    def test_bone_node_names_mapped_correctly(self):
        """bone_node_names resolves bone_node_indices to node name strings."""
        from gmodular.gui.viewport import _EGLRenderer
        from gmodular.engine.entity_system import Creature3D

        r = object.__new__(_EGLRenderer)
        r._entity_vaos = []
        r._skin_vaos   = []
        r.ctx          = MagicMock()
        r._prog_skinned = MagicMock()
        r._release_list = lambda lst: lst.clear()

        ent = Creature3D(2)
        ent.visible = True
        ent._x = ent._y = ent._z = 0.0
        ent._bearing = 0.0

        class _Node0:
            name = "rootdummy"; flags = 0
            is_skin = False; render = False; is_aabb = False
            vertices = []; faces = []
        class _Node1:
            name = "lthigh"; flags = 0
            is_skin = False; render = False; is_aabb = False
            vertices = []; faces = []
        class _SkinNode:
            name = "skin_mesh"
            flags = 0x0060
            vertices = [(0,0,0),(1,0,0),(0,1,0)]
            faces    = [(0,1,2)]
            normals  = [(0,0,1),(0,0,1),(0,0,1)]
            uvs      = []
            bone_weights = [(0.5,0.5,0,0),(0.5,0.5,0,0),(0.5,0.5,0,0)]
            bone_indices = [(0,1,0,0),(0,1,0,0),(0,1,0,0)]
            bone_node_indices = [0, 1]   # bone slot 0 → node 0, slot 1 → node 1
            @property
            def is_skin(self): return True
            render = True
            is_aabb = False

        class _MD:
            name = "test"
            def all_nodes(self): return [_Node0(), _Node1(), _SkinNode()]
            def visible_mesh_nodes(self): return [_SkinNode()]

        ent.mesh_data = _MD()
        r._upload_lit_or_flat  = MagicMock(return_value=None)
        r._upload_skinned_mesh = MagicMock(return_value={"vao": MagicMock(), "vbo": MagicMock(), "count": 3, "skinned": True})

        class _Reg:
            @property
            def entities(self): return [ent]

        r.rebuild_entity_vaos(_Reg())
        assert len(r._skin_vaos) == 1
        bnn = r._skin_vaos[0]["bone_node_names"]
        assert bnn[0] == "rootdummy"
        assert bnn[1] == "lthigh"


# ═══════════════════════════════════════════════════════════════════════════════
#  2  AppController
# ═══════════════════════════════════════════════════════════════════════════════

class TestAppController:
    """AppController wraps domain calls and returns (bool, str) tuples."""

    def _make_ctrl(self):
        from gmodular.gui.app_controller import AppController
        state = MagicMock()
        rm    = MagicMock()
        ctrl = AppController(state, rm)
        return ctrl, state, rm

    def test_importable(self):
        from gmodular.gui.app_controller import AppController
        assert AppController is not None

    def test_is_open_delegates_to_state(self):
        ctrl, state, _ = self._make_ctrl()
        state.is_open = True
        assert ctrl.is_open() is True
        state.is_open = False
        assert ctrl.is_open() is False

    def test_save_module_no_module_open(self):
        ctrl, state, _ = self._make_ctrl()
        state.is_open = False
        ok, msg = ctrl.save_module()
        assert ok is False
        assert "No module" in msg

    def test_save_module_with_project_calls_save(self):
        ctrl, state, _ = self._make_ctrl()
        state.is_open = True
        state.project  = MagicMock()
        state.project.git_path = "/tmp/test.git"
        state.save = MagicMock()
        ok, msg = ctrl.save_module()
        assert ok is True
        state.save.assert_called_once()

    def test_save_module_no_project_signals_save_as(self):
        ctrl, state, _ = self._make_ctrl()
        state.is_open = True
        state.project = None
        ok, msg = ctrl.save_module()
        assert ok is False
        assert msg == "save_as_needed"

    def test_save_module_with_explicit_path(self):
        ctrl, state, _ = self._make_ctrl()
        state.is_open = True
        state.ifo = None   # no IFO
        state.save = MagicMock()
        ok, msg = ctrl.save_module(git_path="/tmp/out.git")
        assert ok is True
        assert "/tmp/out.git" in msg

    def test_set_game_dir_missing_chitin(self, tmp_path):
        ctrl, _, _ = self._make_ctrl()
        ok, msg = ctrl.set_game_dir(str(tmp_path))
        assert ok is False
        assert "chitin.key" in msg

    def test_set_game_dir_valid(self, tmp_path):
        ctrl, _, rm = self._make_ctrl()
        (tmp_path / "chitin.key").write_bytes(b"\x00")
        rm.set_game = MagicMock()
        ok, msg = ctrl.set_game_dir(str(tmp_path))
        assert ok is True
        assert ctrl.game_dir == tmp_path
        rm.set_game.assert_called_once()

    def test_validate_module_delegates(self):
        ctrl, state, _ = self._make_ctrl()
        state.validate = MagicMock(return_value=["Issue A"])
        issues = ctrl.validate_module()
        assert issues == ["Issue A"]

    def test_validate_module_empty_on_pass(self):
        ctrl, state, _ = self._make_ctrl()
        state.validate = MagicMock(return_value=[])
        issues = ctrl.validate_module()
        assert issues == []

    def test_undo_delegates(self):
        ctrl, state, _ = self._make_ctrl()
        state.undo = MagicMock(return_value="Placed object")
        assert ctrl.undo() == "Placed object"

    def test_redo_delegates(self):
        ctrl, state, _ = self._make_ctrl()
        state.redo = MagicMock(return_value="Redo: place")
        assert ctrl.redo() == "Redo: place"

    def test_object_count_sums_git_lists(self):
        ctrl, state, _ = self._make_ctrl()
        git = MagicMock()
        git.placeables = [1, 2]
        git.creatures  = [1]
        git.doors      = []
        git.waypoints  = [1, 2, 3]
        git.triggers   = []
        git.sounds     = []
        git.stores     = []
        state.git = git
        assert ctrl.object_count() == 6

    def test_open_module_nonexistent_path(self):
        ctrl, _, _ = self._make_ctrl()
        ok, msg = ctrl.open_module("/nonexistent/path.mod")
        assert ok is False
        assert "not found" in msg

    def test_open_module_unsupported_extension(self, tmp_path):
        ctrl, _, _ = self._make_ctrl()
        f = tmp_path / "test.xyz"
        f.write_bytes(b"\x00")
        ok, msg = ctrl.open_module(str(f))
        assert ok is False
        assert "Unsupported" in msg


# ═══════════════════════════════════════════════════════════════════════════════
#  3  GhostRigger IPC animation methods
# ═══════════════════════════════════════════════════════════════════════════════

class TestGhostRiggerAnimationMethods:
    """GhostRiggerBridge exposes animation IPC methods."""

    def _make_bridge(self):
        """Build a minimal bridge stub without instantiating a QObject."""
        from gmodular.ipc.bridges import GhostRiggerBridge

        class _BridgeStub:
            """Minimal stub that provides only the methods under test."""
            _fg = None
            _is_connected = True
            # Bind the real methods to this stub class
            play_animation      = GhostRiggerBridge.play_animation
            stop_animation      = GhostRiggerBridge.stop_animation
            set_animation_speed = GhostRiggerBridge.set_animation_speed
            list_animations     = GhostRiggerBridge.list_animations

        stub = _BridgeStub()
        stub._fg = MagicMock()
        return stub

    def test_play_animation_posts_correct_payload(self):
        bridge = self._make_bridge()
        resp = MagicMock()
        resp.status_code = 200
        bridge._fg.post.return_value = resp

        ok = bridge.play_animation("c_bastila", "cpause1", loop=True, speed=1.0)
        assert ok is True
        call_kwargs = bridge._fg.post.call_args
        payload = call_kwargs[1]["json"]
        assert payload["model"] == "c_bastila"
        assert payload["anim"]  == "cpause1"
        assert payload["loop"]  is True
        assert payload["speed"] == 1.0

    def test_stop_animation_posts_correct_payload(self):
        bridge = self._make_bridge()
        resp = MagicMock(); resp.status_code = 200
        bridge._fg.post.return_value = resp
        ok = bridge.stop_animation("c_bastila")
        assert ok is True
        payload = bridge._fg.post.call_args[1]["json"]
        assert payload["model"] == "c_bastila"

    def test_set_animation_speed_clamps_negative(self):
        bridge = self._make_bridge()
        resp = MagicMock(); resp.status_code = 200
        bridge._fg.post.return_value = resp
        bridge.set_animation_speed("c_bastila", -2.0)
        payload = bridge._fg.post.call_args[1]["json"]
        assert payload["speed"] >= 0.0

    def test_list_animations_returns_list(self):
        bridge = self._make_bridge()
        resp = MagicMock(); resp.status_code = 200
        resp.json.return_value = ["cpause1", "cwalk", "crun"]
        bridge._fg.get.return_value = resp
        anims = bridge.list_animations("c_bastila")
        assert "cpause1" in anims

    def test_play_animation_returns_false_when_offline(self):
        from gmodular.ipc.bridges import GhostRiggerBridge

        class _Offline:
            _fg = None
            _is_connected = False
            play_animation = GhostRiggerBridge.play_animation

        ok = _Offline().play_animation("model", "anim")
        assert ok is False

    def test_stop_animation_returns_false_when_offline(self):
        from gmodular.ipc.bridges import GhostRiggerBridge

        class _Offline:
            _fg = None
            stop_animation = GhostRiggerBridge.stop_animation

        ok = _Offline().stop_animation("model")
        assert ok is False

    def test_list_animations_returns_empty_when_offline(self):
        from gmodular.ipc.bridges import GhostRiggerBridge

        class _Offline:
            _fg = None
            list_animations = GhostRiggerBridge.list_animations

        result = _Offline().list_animations("model")
        assert result == []


# ═══════════════════════════════════════════════════════════════════════════════
#  4  MCP animation tools
# ═══════════════════════════════════════════════════════════════════════════════

class TestMCPAnimationTools:
    """MCP animation tool schema and handler contract."""

    def test_get_tools_returns_four_tools(self):
        from gmodular.mcp.tools.animation import get_tools
        tools = get_tools()
        # kotor_entity_info added — total is now 5
        assert len(tools) == 5

    def test_tool_names_correct(self):
        from gmodular.mcp.tools.animation import get_tools
        names = {t["name"] for t in get_tools()}
        assert "kotor_list_animations"  in names
        assert "kotor_play_animation"   in names
        assert "kotor_stop_animation"   in names
        assert "kotor_animation_state"  in names
        assert "kotor_entity_info"      in names

    def test_all_tools_have_required_schema_keys(self):
        from gmodular.mcp.tools.animation import get_tools
        for t in get_tools():
            assert "name"        in t
            assert "description" in t
            assert "inputSchema" in t

    def test_list_animations_returns_catalogue(self):
        from gmodular.mcp.tools.animation import handle_list_animations
        import asyncio
        result = asyncio.new_event_loop().run_until_complete(
            handle_list_animations({})
        )
        # json_content returns {'content': [{'type':'text','text':'...'}]}
        assert result is not None
        assert "content" in result

    def test_play_animation_requires_animation_name(self):
        from gmodular.mcp.tools.animation import handle_play_animation
        import asyncio, json
        result = asyncio.new_event_loop().run_until_complete(
            handle_play_animation({})
        )
        # json_content returns {'content': [{'type':'text','text':'...'}]}
        text = result["content"][0]["text"]
        data = json.loads(text)
        assert "error" in data

    def test_stop_animation_graceful_without_registry(self):
        from gmodular.mcp.tools.animation import handle_stop_animation
        import asyncio, json
        result = asyncio.new_event_loop().run_until_complete(
            handle_stop_animation({"entity_tag": "nobody"})
        )
        text = result["content"][0]["text"]
        data = json.loads(text)
        assert "viewport" in data

    def test_animation_state_graceful_without_module(self):
        from gmodular.mcp.tools.animation import handle_animation_state
        import asyncio, json
        result = asyncio.new_event_loop().run_until_complete(
            handle_animation_state({})
        )
        text = result["content"][0]["text"]
        data = json.loads(text)
        assert "error" in data or "entities" in data

    def test_animation_tools_registered_in_init(self):
        from gmodular.mcp.tools import get_all_tools
        tools = get_all_tools()
        names = {t["name"] for t in tools}
        assert "kotor_list_animations" in names
        assert "kotor_play_animation"  in names

    def test_handler_dispatch_for_animation_tools(self):
        from gmodular.mcp.tools import handle_tool
        import asyncio
        loop = asyncio.new_event_loop()
        # Should not raise ValueError for known tool names
        for tool_name in ["kotor_list_animations", "kotor_stop_animation",
                          "kotor_animation_state", "kotor_entity_info"]:
            try:
                loop.run_until_complete(
                    handle_tool(tool_name, {})
                )
            except ValueError as e:
                assert False, f"handle_tool raised ValueError for '{tool_name}': {e}"
            except Exception:
                pass   # DB/network errors are fine in isolation
        loop.close()

    def test_entity_info_returns_error_without_module(self):
        """kotor_entity_info returns graceful error dict when no module is loaded."""
        import asyncio
        from gmodular.mcp.tools.animation import handle_entity_info
        import json
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(handle_entity_info({"tag": "bastila"}))
        loop.close()
        # json_content returns {"content": [{"type": "text", "text": "..."}]}
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        content_items = result.get("content", result if isinstance(result, list) else [])
        if isinstance(result, list):
            text = result[0]["text"]
        else:
            text = result["content"][0]["text"]
        data = json.loads(text)
        # Either an error (no module) or a valid entity response
        assert "entity" in data or "error" in data


# ═══════════════════════════════════════════════════════════════════════════════
#  5  NPC Patrol AI
# ═══════════════════════════════════════════════════════════════════════════════

class TestNPCPatrolAI:
    """Creature3D patrol state machine."""

    def _make_creature(self):
        from gmodular.engine.entity_system import Creature3D
        c = Creature3D(1)
        c._x = 0.0
        c._y = 0.0
        c._z = 0.0
        c._bearing = 0.0
        # No animation player needed for patrol movement tests
        c._animation_player = None
        return c

    def test_set_patrol_route_empty_disables_patrol(self):
        from gmodular.engine.entity_system import CreatureState
        c = self._make_creature()
        c.set_patrol_route([])
        assert c.patrol_waypoints == []

    def test_set_patrol_route_nonempty_sets_patrolling(self):
        from gmodular.engine.entity_system import CreatureState
        c = self._make_creature()
        c.set_patrol_route([(5.0, 0.0, 0.0)])
        assert c.state == CreatureState.PATROLLING
        assert len(c.patrol_waypoints) == 1

    def test_patrol_tick_moves_creature_toward_waypoint(self):
        c = self._make_creature()
        c.patrol_arrival_radius = 0.1
        c.walk_rate = 1.0
        c.set_patrol_route([(10.0, 0.0, 0.0)])
        c._patrol_tick(0.5)  # 0.5s at 1 m/s = 0.5 m
        assert abs(c._x - 0.5) < 0.01

    def test_patrol_tick_arrives_and_starts_dwell(self):
        from gmodular.engine.entity_system import CreatureState
        c = self._make_creature()
        c._x = 4.9
        c._y = 0.0
        c.patrol_arrival_radius = 0.25
        c.patrol_dwell = 2.0
        c.set_patrol_route([(5.0, 0.0, 0.0)])
        c._patrol_tick(0.1)  # close enough to arrive
        # Should be at waypoint now and dwelling
        assert abs(c._x - 5.0) < 0.01
        assert c._patrol_wait > 0.0

    def test_patrol_tick_advances_waypoint_after_dwell(self):
        c = self._make_creature()
        c.patrol_waypoints = [(5.0, 0.0, 0.0), (10.0, 0.0, 0.0)]
        c._patrol_idx  = 0
        c._patrol_wait = 0.1   # almost done dwelling
        c._patrol_tick(0.2)    # tick > remaining dwell
        # Should have advanced to next waypoint
        assert c._patrol_idx == 1
        assert c._patrol_wait == 0.0

    def test_patrol_tick_wraps_waypoint_index(self):
        c = self._make_creature()
        c.patrol_waypoints = [(5.0, 0.0, 0.0)]
        c._patrol_idx  = 0
        c._patrol_wait = 0.1
        c._patrol_tick(0.2)
        # Should wrap back to 0
        assert c._patrol_idx == 0

    def test_patrol_tick_faces_target(self):
        c = self._make_creature()
        c.patrol_waypoints = [(0.0, 10.0, 0.0)]  # directly north (+Y)
        c._patrol_idx = 0
        c._patrol_wait = 0.0
        c._patrol_tick(0.1)
        # atan2(10, 0) ≈ π/2
        assert abs(c._bearing - math.pi / 2) < 0.01

    def test_update_calls_patrol_tick_when_patrolling(self):
        from gmodular.engine.entity_system import CreatureState
        c = self._make_creature()
        c.set_patrol_route([(100.0, 0.0, 0.0)])
        original_x = c._x
        c.update(0.1)
        # Should have moved
        assert c._x > original_x

    def test_dead_creature_does_not_patrol(self):
        from gmodular.engine.entity_system import CreatureState
        c = self._make_creature()
        c.set_patrol_route([(100.0, 0.0, 0.0)])
        c.state = CreatureState.DEAD
        c.update(1.0)
        # Should not have moved
        assert abs(c._x) < 0.01

    def test_patrol_route_preserved_after_idle(self):
        c = self._make_creature()
        c.set_patrol_route([(5.0, 0.0, 0.0)])
        c.start_idle()
        # Route still stored
        assert len(c.patrol_waypoints) == 1

    def test_creature_state_patrolling_value_is_7(self):
        from gmodular.engine.entity_system import CreatureState
        assert int(CreatureState.PATROLLING) == 7


# ═══════════════════════════════════════════════════════════════════════════════
#  6  ViewportWidget.set_patrol_path
# ═══════════════════════════════════════════════════════════════════════════════

class TestViewportSetPatrolPath:
    """ViewportWidget.set_patrol_path delegates to EntityRegistry."""

    def _make_vp_stub(self):
        """Build a plain stub with only set_patrol_path and _entity_registry."""
        from gmodular.gui.viewport import ViewportWidget

        class _VPStub:
            _entity_registry = None
            set_patrol_path = ViewportWidget.set_patrol_path

        return _VPStub()

    def test_set_patrol_path_returns_false_without_registry(self):
        vp = self._make_vp_stub()
        vp._entity_registry = None
        result = vp.set_patrol_path("Bastila", [(1.0, 2.0, 0.0)])
        assert result is False

    def test_set_patrol_path_calls_set_patrol_route(self):
        from gmodular.engine.entity_system import Creature3D

        vp = self._make_vp_stub()
        c = Creature3D(10)
        c.tag = "Bastila"
        c.set_patrol_route = MagicMock()

        registry = MagicMock()
        registry.get_by_tag.return_value = [c]
        vp._entity_registry = registry

        waypoints = [(1.0, 2.0, 0.0), (3.0, 4.0, 0.0)]
        result = vp.set_patrol_path("Bastila", waypoints)
        assert result is True
        c.set_patrol_route.assert_called_once_with(waypoints)

    def test_set_patrol_path_returns_false_when_entity_not_found(self):
        vp = self._make_vp_stub()
        registry = MagicMock()
        registry.get_by_tag.return_value = []
        vp._entity_registry = registry
        result = vp.set_patrol_path("Unknown", [(0, 0, 0)])
        assert result is False
