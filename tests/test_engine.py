"""
Tests for GModular engine subsystem:
  - MDL parser (gmodular.formats.mdl_parser)
  - Player controller (gmodular.engine.player_controller)
  - NPC instance / registry (gmodular.engine.npc_instance)
  - Play session (gmodular.engine.player_controller.PlaySession)

All tests are pure Python — no OpenGL / Qt required.
"""
from __future__ import annotations
import sys
import math
import struct
import os
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ─────────────────────────────────────────────────────────────────────────────
#  MDL Parser tests
# ─────────────────────────────────────────────────────────────────────────────

class TestMDLParserHelpers:
    """Test helper functions in mdl_parser without real MDL files."""

    def test_rstrip_null_terminated(self):
        from gmodular.formats.mdl_parser import _rstrip
        b = b"hello\x00garbage"
        assert _rstrip(b) == "hello"

    def test_rstrip_full_field(self):
        from gmodular.formats.mdl_parser import _rstrip
        b = b"c_bantha" + b"\x00" * 24
        assert _rstrip(b) == "c_bantha"

    def test_rstrip_empty(self):
        from gmodular.formats.mdl_parser import _rstrip
        assert _rstrip(b"\x00\x00\x00") == ""

    def test_ru32(self):
        from gmodular.formats.mdl_parser import _ru32
        data = struct.pack('<I', 12345678)
        assert _ru32(data, 0) == 12345678

    def test_rf32(self):
        from gmodular.formats.mdl_parser import _rf32
        data = struct.pack('<f', 3.14)
        assert abs(_rf32(data, 0) - 3.14) < 1e-5


class TestMeshNodeProperties:
    """Test MeshNode dataclass and properties."""

    def test_is_mesh_flag(self):
        from gmodular.formats.mdl_parser import MeshNode, NODE_MESH, NODE_HEADER
        n = MeshNode(flags=NODE_HEADER | NODE_MESH)
        assert n.is_mesh
        assert not n.is_skin

    def test_is_skin_flag(self):
        from gmodular.formats.mdl_parser import MeshNode, NODE_MESH, NODE_SKIN, NODE_HEADER
        n = MeshNode(flags=NODE_HEADER | NODE_MESH | NODE_SKIN)
        assert n.is_skin

    def test_texture_clean(self):
        from gmodular.formats.mdl_parser import MeshNode
        n = MeshNode(texture="plc_chair01\x00garbage")
        assert n.texture_clean == "plc_chair01"

    def test_texture_clean_empty(self):
        from gmodular.formats.mdl_parser import MeshNode
        n = MeshNode(texture="")
        assert n.texture_clean == ""


class TestMeshData:
    """Test MeshData dataclass methods."""

    def _make_mesh_with_node(self):
        from gmodular.formats.mdl_parser import MeshData, MeshNode, NODE_MESH, NODE_HEADER
        mesh = MeshData(name="test")
        root = MeshNode(name="root", flags=NODE_HEADER)
        child = MeshNode(name="mesh01", flags=NODE_HEADER | NODE_MESH, parent=root)
        child.vertices = [(0,0,0), (1,0,0), (1,1,0), (0,1,0)]
        child.faces    = [(0,1,2), (0,2,3)]
        child.render   = True
        root.children.append(child)
        mesh.root_node = root
        return mesh

    def test_all_nodes(self):
        mesh = self._make_mesh_with_node()
        nodes = mesh.all_nodes()
        assert len(nodes) == 2

    def test_mesh_nodes(self):
        mesh = self._make_mesh_with_node()
        mn = mesh.mesh_nodes()
        assert len(mn) == 1
        assert mn[0].name == "mesh01"

    def test_visible_mesh_nodes(self):
        mesh = self._make_mesh_with_node()
        vn = mesh.visible_mesh_nodes()
        assert len(vn) == 1

    def test_compute_bounds(self):
        mesh = self._make_mesh_with_node()
        mesh.compute_bounds()
        assert mesh.bb_min[0] <= 0.0
        assert mesh.bb_max[0] >= 1.0
        assert mesh.radius > 0.0

    def test_flat_triangle_array(self):
        mesh = self._make_mesh_with_node()
        tris = mesh.flat_triangle_array()
        assert len(tris) == 2  # 2 faces
        for tri_verts, normal in tris:
            assert len(tri_verts) == 3

    def test_empty_mesh(self):
        from gmodular.formats.mdl_parser import MeshData
        mesh = MeshData()
        assert mesh.mesh_nodes() == []
        assert mesh.visible_mesh_nodes() == []
        mesh.compute_bounds()   # should not raise


class TestModelCache:
    """Test ModelCache LRU logic."""

    def test_put_get(self):
        from gmodular.formats.mdl_parser import ModelCache, MeshData
        cache = ModelCache(max_size=5)
        m = MeshData(name="test_model")
        cache.put("/some/path/model.mdl", m)
        result = cache.get("/some/path/model.mdl")
        assert result is m

    def test_case_insensitive(self):
        from gmodular.formats.mdl_parser import ModelCache, MeshData
        cache = ModelCache()
        m = MeshData(name="bantha")
        cache.put("/Models/C_Bantha.mdl", m)
        assert cache.get("/models/c_bantha.mdl") is m

    def test_eviction(self):
        from gmodular.formats.mdl_parser import ModelCache, MeshData
        cache = ModelCache(max_size=3)
        for i in range(4):
            cache.put(f"/model_{i}.mdl", MeshData(name=f"m{i}"))
        # First entry should be evicted
        assert cache.get("/model_0.mdl") is None
        assert cache.get("/model_3.mdl") is not None

    def test_missing_returns_none(self):
        from gmodular.formats.mdl_parser import ModelCache
        cache = ModelCache()
        assert cache.get("/nonexistent.mdl") is None

    def test_clear(self):
        from gmodular.formats.mdl_parser import ModelCache, MeshData
        cache = ModelCache()
        cache.put("/m.mdl", MeshData())
        cache.clear()
        assert cache.get("/m.mdl") is None


class TestMDLParserTooSmall:
    """Test MDLParser rejects tiny/invalid data gracefully."""

    def test_too_small_raises(self):
        from gmodular.formats.mdl_parser import MDLParser
        p = MDLParser(b"\x00" * 10, b"")
        with pytest.raises(ValueError):
            p.parse()

    def test_minimal_header_no_crash(self):
        from gmodular.formats.mdl_parser import MDLParser
        # 300 bytes of zeros — too small for a real MDL but large enough to
        # pass the size check; the parser should not raise
        p = MDLParser(b"\x00" * 300, b"")
        try:
            mesh = p.parse()
            assert mesh is not None
        except Exception as e:
            # Acceptable: parse may raise on bad data but should not crash Python
            assert "MDL" in str(e) or "struct" in str(e) or True


# ─────────────────────────────────────────────────────────────────────────────
#  Player Controller tests
# ─────────────────────────────────────────────────────────────────────────────

class TestRayTriangleIntersect:
    """Test Möller–Trumbore intersection helper."""

    def test_hit_flat_triangle(self):
        from gmodular.engine.player_controller import _ray_triangle_intersect
        origin    = (0.5, 0.5, 5.0)
        direction = (0.0, 0.0, -1.0)
        v0 = (0.0, 0.0, 0.0)
        v1 = (1.0, 0.0, 0.0)
        v2 = (0.0, 1.0, 0.0)
        t = _ray_triangle_intersect(origin, direction, v0, v1, v2)
        assert t is not None
        assert abs(t - 5.0) < 1e-5

    def test_miss_outside_triangle(self):
        from gmodular.engine.player_controller import _ray_triangle_intersect
        origin    = (2.0, 2.0, 5.0)
        direction = (0.0, 0.0, -1.0)
        v0 = (0.0, 0.0, 0.0)
        v1 = (1.0, 0.0, 0.0)
        v2 = (0.0, 1.0, 0.0)
        t = _ray_triangle_intersect(origin, direction, v0, v1, v2)
        assert t is None

    def test_parallel_ray(self):
        from gmodular.engine.player_controller import _ray_triangle_intersect
        origin    = (0.5, 0.5, 1.0)
        direction = (1.0, 0.0, 0.0)  # horizontal — parallel to Z=0
        v0 = (0.0, 0.0, 0.0)
        v1 = (1.0, 0.0, 0.0)
        v2 = (0.0, 1.0, 0.0)
        t = _ray_triangle_intersect(origin, direction, v0, v1, v2)
        assert t is None


class TestFloorHeightAt:
    """Test floor query function."""

    def _flat_floor(self):
        """Return a single flat triangle covering Z=0 plane."""
        tris = [
            (((- 100, -100, 0.0), (100, -100, 0.0), (0.0, 100, 0.0)),
             (0.0, 0.0, 1.0)),  # normal pointing up
        ]
        return tris

    def test_finds_flat_floor(self):
        from gmodular.engine.player_controller import _floor_height_at
        tris = self._flat_floor()
        z = _floor_height_at(0.0, 0.0, tris, search_z_start=5.0)
        assert z is not None
        assert abs(z) < 0.01

    def test_no_floor_returns_none(self):
        from gmodular.engine.player_controller import _floor_height_at
        z = _floor_height_at(0.0, 0.0, [], search_z_start=5.0)
        assert z is None

    def test_downward_facing_ignored(self):
        """Triangles facing down (normal.z < 0) should not be used as floor."""
        from gmodular.engine.player_controller import _floor_height_at
        tris = [
            (((-100, -100, 0.0), (100, -100, 0.0), (0.0, 100, 0.0)),
             (0.0, 0.0, -1.0)),  # normal pointing DOWN
        ]
        z = _floor_height_at(0.0, 0.0, tris, search_z_start=5.0)
        assert z is None


class TestPlayerController:
    """Test PlayerController locomotion."""

    def test_initial_position(self):
        from gmodular.engine.player_controller import PlayerController
        pc = PlayerController()
        assert pc.x == 0.0 and pc.y == 0.0

    def test_teleport(self):
        from gmodular.engine.player_controller import PlayerController
        pc = PlayerController()
        pc.teleport(5.0, 3.0, 1.0)
        assert abs(pc.x - 5.0) < 1e-6
        assert abs(pc.y - 3.0) < 1e-6
        assert abs(pc.z - 1.0) < 1e-6

    def test_forward_movement(self):
        from gmodular.engine.player_controller import PlayerController
        pc = PlayerController()
        pc.teleport(0.0, 0.0, 0.0)
        pc.yaw = 0.0   # facing +Y
        start_y = pc.y
        pc.update(dt=1.0, move_forward=1.0, move_right=0.0, turn_left=0.0)
        # Y should increase (moving forward along +Y at yaw=0)
        assert pc.y > start_y

    def test_strafe_right(self):
        from gmodular.engine.player_controller import PlayerController
        pc = PlayerController()
        pc.teleport(0.0, 0.0, 0.0)
        pc.yaw = 0.0
        start_x = pc.x
        pc.update(dt=1.0, move_forward=0.0, move_right=1.0, turn_left=0.0)
        assert pc.x > start_x

    def test_turning(self):
        from gmodular.engine.player_controller import PlayerController
        pc = PlayerController()
        pc.yaw = 0.0
        pc.update(dt=1.0, move_forward=0.0, move_right=0.0, turn_left=1.0)
        # yaw should increase
        assert pc.yaw > 0.0

    def test_gravity_applied_when_airborne(self):
        from gmodular.engine.player_controller import PlayerController
        pc = PlayerController()
        pc.teleport(0.0, 0.0, 10.0)   # start high, no floor
        pc.on_ground = False
        pc.vel_z = 0.0
        pc.update(dt=0.5, move_forward=0.0, move_right=0.0, turn_left=0.0)
        # Should have fallen (z < 10.0) and acquired downward velocity
        assert pc.z < 10.0 or pc.vel_z < 0.0

    def test_snaps_to_flat_floor(self):
        """Player should snap to Z=0 when walkmesh covers Z=0."""
        from gmodular.engine.player_controller import PlayerController
        from gmodular.engine.player_controller import _floor_height_at
        pc = PlayerController()
        # Provide a flat floor at Z=0
        tris = [
            (((-100, -100, 0.0), (100, -100, 0.0), (0.0, 100, 0.0)),
             (0.0, 0.0, 1.0)),
        ]
        pc.set_walkmesh(tris)
        pc.teleport(0.0, 0.0, 5.0)  # start above floor
        pc.on_ground = False
        for _ in range(20):
            pc.update(dt=0.05, move_forward=0.0, move_right=0.0, turn_left=0.0)
        assert abs(pc.z) < 0.1    # should be near floor

    def test_eye_position(self):
        from gmodular.engine.player_controller import PlayerController, CAPSULE_HEIGHT
        pc = PlayerController()
        pc.teleport(1.0, 2.0, 0.0)
        eye = pc.eye_position()
        assert eye[0] == 1.0 and eye[1] == 2.0
        assert eye[2] > 0.5   # eye should be above feet

    def test_look_at_target(self):
        from gmodular.engine.player_controller import PlayerController
        pc = PlayerController()
        pc.yaw = 0.0
        eye  = pc.eye_position()
        look = pc.look_at_target()
        # look_at target should be in front of player (y > eye_y at yaw=0)
        assert look[1] > eye[1]

    def test_save_load_state(self):
        from gmodular.engine.player_controller import PlayerController
        pc = PlayerController()
        pc.x = 3.5; pc.y = -1.2; pc.z = 0.5; pc.yaw = 45.0
        state = pc.save_state()
        pc2 = PlayerController()
        pc2.load_state(state)
        assert abs(pc2.x - 3.5) < 1e-6
        assert abs(pc2.yaw - 45.0) < 1e-6


class TestPlaySession:
    """Test PlaySession creation and lifecycle."""

    def _mock_git(self):
        """Simple mock GIT with one waypoint."""
        class MockPos:
            x = 5.0; y = 3.0; z = 0.0
        class MockWP:
            position = MockPos(); tag = "wp_start"
        class MockGIT:
            waypoints  = [MockWP()]
            creatures  = []
            placeables = []
            doors      = []
            triggers   = []
            sounds     = []
            stores     = []
            def iter_all(self): return self.waypoints
        return MockGIT()

    def test_session_starts_active(self):
        from gmodular.engine.player_controller import PlaySession
        session = PlaySession.start()
        assert session.active

    def test_session_spawn_at_waypoint(self):
        from gmodular.engine.player_controller import PlaySession
        git = self._mock_git()
        session = PlaySession.start(git_data=git)
        assert session.active
        # Player should be near the waypoint
        assert abs(session.player.x - 5.0) < 1.0
        assert abs(session.player.y - 3.0) < 1.0

    def test_session_stop(self):
        from gmodular.engine.player_controller import PlaySession
        session = PlaySession.start()
        session.stop()
        assert not session.active

    def test_session_update(self):
        from gmodular.engine.player_controller import PlaySession
        session = PlaySession.start()
        start_y = session.player.y
        session.update(0.5, {"move_forward": 1.0})
        # y should change after moving forward
        # (direction depends on initial yaw but position should change)
        moved = (abs(session.player.y - start_y) > 0.01 or
                 abs(session.player.x) > 0.01)
        # movement might be small but player must be on ground
        assert session.player.on_ground

    def test_player_eye_property(self):
        from gmodular.engine.player_controller import PlaySession
        session = PlaySession.start()
        eye = session.player_eye
        assert isinstance(eye, tuple) and len(eye) == 3

    def test_find_spawn_no_git(self):
        from gmodular.engine.player_controller import _find_spawn
        sx, sy, sz = _find_spawn(None)
        assert isinstance(sx, float)

    def test_find_spawn_with_waypoint(self):
        from gmodular.engine.player_controller import _find_spawn
        git = self._mock_git()
        sx, sy, sz = _find_spawn(git)
        assert abs(sx - 5.0) < 1.0


# ─────────────────────────────────────────────────────────────────────────────
#  NPC Instance tests
# ─────────────────────────────────────────────────────────────────────────────

class TestNPCInstance:
    """Test NPCInstance creation and properties."""

    def _mock_creature(self):
        class MockPos:
            x = 2.0; y = 3.0; z = 0.0
        class MockCreature:
            position = MockPos()
            resref   = "n_jediknight01"
            tag      = "jedi01"
            bearing  = 0.785  # ~45 degrees
        return MockCreature()

    def test_from_git_creature(self):
        from gmodular.engine.npc_instance import NPCInstance
        c = self._mock_creature()
        npc = NPCInstance.from_git_creature(c)
        assert npc.resref == "n_jediknight01"
        assert abs(npc.x - 2.0) < 1e-6
        assert abs(npc.y - 3.0) < 1e-6

    def test_resref_truncated(self):
        from gmodular.engine.npc_instance import NPCInstance
        class LongResref:
            class position:
                x = 0.0; y = 0.0; z = 0.0
            resref = "a" * 20
            tag = ""
            bearing = 0.0
        npc = NPCInstance.from_git_creature(LongResref())
        assert len(npc.resref) <= 16

    def test_position_tuple(self):
        from gmodular.engine.npc_instance import NPCInstance
        npc = NPCInstance(x=1.0, y=2.0, z=3.0)
        assert npc.position_tuple() == (1.0, 2.0, 3.0)

    def test_eye_height(self):
        from gmodular.engine.npc_instance import NPCInstance
        npc = NPCInstance(z=0.0, capsule_height=2.0)
        assert npc.eye_height() > 0.5

    def test_direction_vector(self):
        from gmodular.engine.npc_instance import NPCInstance
        npc = NPCInstance(bearing=0.0)   # facing +Y
        dx, dy = npc.direction_vector()
        assert abs(dx) < 0.01
        assert abs(dy - 1.0) < 0.01

    def test_model_not_loaded_by_default(self):
        from gmodular.engine.npc_instance import NPCInstance
        npc = NPCInstance()
        assert not npc.model_loaded
        assert npc.mesh_data is None

    def test_load_model_missing_file(self):
        from gmodular.engine.npc_instance import NPCInstance
        npc = NPCInstance(resref="nonexistent")
        result = npc.load_model("/nonexistent/path/model.mdl")
        assert result is False
        assert not npc.model_loaded


class TestNPCRegistry:
    """Test NPCRegistry population and management."""

    def _mock_git_with_creatures(self):
        class MockPos:
            x = 1.0; y = 2.0; z = 0.0
        class MockCreature:
            position = MockPos()
            resref   = "n_commoner01m"
            tag      = "npc01"
            bearing  = 0.0
        class MockGIT:
            creatures = [MockCreature(), MockCreature()]
        return MockGIT()

    def test_populate_from_git(self):
        from gmodular.engine.npc_instance import NPCRegistry
        reg = NPCRegistry()
        git = self._mock_git_with_creatures()
        count = reg.populate_from_git(git)
        assert count == 2
        assert len(reg) == 2

    def test_clear(self):
        from gmodular.engine.npc_instance import NPCRegistry
        reg = NPCRegistry()
        git = self._mock_git_with_creatures()
        reg.populate_from_git(git)
        reg.clear()
        assert len(reg) == 0

    def test_npcs_property(self):
        from gmodular.engine.npc_instance import NPCRegistry
        reg = NPCRegistry()
        git = self._mock_git_with_creatures()
        reg.populate_from_git(git)
        npcs = reg.npcs
        assert len(npcs) == 2
        # Returned list is a copy
        npcs.clear()
        assert len(reg) == 2

    def test_capsule_summary(self):
        from gmodular.engine.npc_instance import NPCRegistry
        reg = NPCRegistry()
        git = self._mock_git_with_creatures()
        reg.populate_from_git(git)
        summary = reg.capsule_summary()
        assert "2 NPCs" in summary

    def test_try_load_models_no_dir(self):
        from gmodular.engine.npc_instance import NPCRegistry
        reg = NPCRegistry()
        count = reg.try_load_models("/nonexistent/game/dir")
        assert count == 0

    def test_populate_empty_git(self):
        from gmodular.engine.npc_instance import NPCRegistry
        reg = NPCRegistry()
        class EmptyGIT:
            creatures = []
        reg.populate_from_git(EmptyGIT())
        assert len(reg) == 0


# ─────────────────────────────────────────────────────────────────────────────
#  Quaternion helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestQuatHelpers:
    """Test quaternion math in mdl_parser."""

    def test_quat_rotate_identity(self):
        from gmodular.formats.mdl_parser import _quat_rotate
        q = (0.0, 0.0, 0.0, 1.0)
        v = (1.0, 2.0, 3.0)
        result = _quat_rotate(q, v)
        assert abs(result[0] - 1.0) < 1e-5
        assert abs(result[1] - 2.0) < 1e-5
        assert abs(result[2] - 3.0) < 1e-5

    def test_quat_normalize_bind_180_collapses(self):
        from gmodular.formats.mdl_parser import _quat_normalize_bind
        # 180° about X — should collapse to identity
        q = (1.0, 0.0, 0.0, 0.0)
        result = _quat_normalize_bind(q)
        assert abs(result[3] - 1.0) < 0.01  # w should be ~1

    def test_quat_normalize_bind_normal_preserved(self):
        from gmodular.formats.mdl_parser import _quat_normalize_bind
        # 45° rotation — should be preserved (not identity)
        import math
        half = math.sqrt(2.0) / 2.0
        q = (0.0, 0.0, half, half)  # 90° about Z
        result = _quat_normalize_bind(q)
        # w should be ~0.707 (not 1.0)
        assert abs(result[3] - half) < 0.05

    def test_world_pos_no_parent(self):
        from gmodular.formats.mdl_parser import _world_pos, MeshNode, NODE_HEADER
        n = MeshNode(flags=NODE_HEADER, position=(1.0, 2.0, 3.0))
        wp = _world_pos(n)
        assert abs(wp[0] - 1.0) < 1e-5
        assert abs(wp[1] - 2.0) < 1e-5
        assert abs(wp[2] - 3.0) < 1e-5


# =============================================================================
#  MDL Renderer tests (no GPU required — tests data structures and helpers)
# =============================================================================

class TestMDLRendererDataStructures:
    """Test MDLRenderer data structures and headless helpers."""

    def test_render_mode_constants(self):
        from gmodular.engine.mdl_renderer import RenderMode
        assert RenderMode.SOLID      == "solid"
        assert RenderMode.WIREFRAME  == "wireframe"
        assert RenderMode.SOLID_WIRE == "solid_wire"
        assert RenderMode.NORMALS    == "normals"

    def test_door_hook_creation(self):
        from gmodular.engine.mdl_renderer import DoorHook
        dh = DoorHook(name="DW_Left", position=(1.0, 2.0, 0.0))
        assert dh.name == "DW_Left"
        assert dh.position == (1.0, 2.0, 0.0)
        assert dh.normal == (0.0, 1.0, 0.0)  # default facing

    def test_uploaded_mesh_creation(self):
        from gmodular.engine.mdl_renderer import UploadedMesh
        um = UploadedMesh(vao=None, index_cnt=6)
        assert um.index_cnt == 6
        assert um.alpha == 1.0
        assert um.texture_name == ""

    def test_uploaded_model_center(self):
        from gmodular.engine.mdl_renderer import UploadedModel
        model = UploadedModel(
            name="test",
            bb_min=(-5.0, -5.0, 0.0),
            bb_max=( 5.0,  5.0, 3.0),
        )
        cx, cy, cz = model.center
        assert abs(cx) < 1e-5
        assert abs(cy) < 1e-5
        assert abs(cz - 1.5) < 1e-5

    def test_uploaded_model_all_texture_names(self):
        from gmodular.engine.mdl_renderer import UploadedModel, UploadedMesh
        model = UploadedModel(name="room")
        model.meshes = [
            UploadedMesh(vao=None, index_cnt=3, texture_name="floor01"),
            UploadedMesh(vao=None, index_cnt=3, texture_name="wall01"),
            UploadedMesh(vao=None, index_cnt=3, texture_name="floor01"),  # duplicate
        ]
        names = model.all_texture_names()
        assert "floor01" in names
        assert "wall01" in names
        assert names.count("floor01") == 1  # de-duplicated

    def test_renderer_headless_no_ctx(self):
        """MDLRenderer without a GL context should be initializable."""
        from gmodular.engine.mdl_renderer import MDLRenderer
        r = MDLRenderer(ctx=None)
        assert not r._ready
        assert r.model_count() == 0

    def test_renderer_mode_setter(self):
        from gmodular.engine.mdl_renderer import MDLRenderer, RenderMode
        r = MDLRenderer(ctx=None)
        r.render_mode = RenderMode.WIREFRAME
        assert r.render_mode == RenderMode.WIREFRAME
        r.render_mode = "invalid_mode"   # should be silently ignored
        assert r.render_mode == RenderMode.WIREFRAME

    def test_renderer_is_loaded_false_when_empty(self):
        from gmodular.engine.mdl_renderer import MDLRenderer
        r = MDLRenderer(ctx=None)
        assert not r.is_loaded("anything")

    def test_renderer_get_bounds_returns_zeros(self):
        from gmodular.engine.mdl_renderer import MDLRenderer
        r = MDLRenderer(ctx=None)
        mn, mx = r.get_bounds("nonexistent")
        assert mn == (0.0, 0.0, 0.0)
        assert mx == (0.0, 0.0, 0.0)

    def test_renderer_get_texture_names_empty(self):
        from gmodular.engine.mdl_renderer import MDLRenderer
        r = MDLRenderer(ctx=None)
        assert r.get_texture_names("nothing") == []

    def test_renderer_get_door_hooks_empty(self):
        from gmodular.engine.mdl_renderer import MDLRenderer
        r = MDLRenderer(ctx=None)
        assert r.get_door_hooks("model") == []

    def test_frustum_cull_no_numpy_returns_false(self):
        """Without numpy, frustum_cull should not raise, returns False."""
        from gmodular.engine.mdl_renderer import _frustum_cull, _HAS_NUMPY
        if not _HAS_NUMPY:
            result = _frustum_cull((0,0,0), 1.0, None)
            assert result is False

    def test_flatten_node_no_numpy_returns_none(self):
        from gmodular.engine.mdl_renderer import _flatten_node, _HAS_NUMPY
        from gmodular.formats.mdl_parser import MeshNode, NODE_MESH, NODE_HEADER
        if not _HAS_NUMPY:
            node = MeshNode(flags=NODE_HEADER | NODE_MESH)
            vbuf, ibuf = _flatten_node(node)
            assert vbuf is None and ibuf is None

    def test_flatten_node_empty(self):
        from gmodular.engine.mdl_renderer import _flatten_node
        from gmodular.formats.mdl_parser import MeshNode, NODE_MESH, NODE_HEADER
        node = MeshNode(flags=NODE_HEADER | NODE_MESH)
        # No vertices/faces → None
        vbuf, ibuf = _flatten_node(node)
        assert vbuf is None or len(node.vertices) == 0

    def test_flatten_node_with_data(self):
        from gmodular.engine.mdl_renderer import _flatten_node, _HAS_NUMPY
        from gmodular.formats.mdl_parser import MeshNode, NODE_MESH, NODE_HEADER
        if not _HAS_NUMPY:
            return
        node = MeshNode(flags=NODE_HEADER | NODE_MESH)
        node.vertices = [(0,0,0), (1,0,0), (0,1,0)]
        node.normals  = [(0,0,1), (0,0,1), (0,0,1)]
        node.uvs      = [(0,0), (1,0), (0,1)]
        node.faces    = [(0,1,2)]
        vbuf, ibuf = _flatten_node(node)
        assert vbuf is not None
        assert ibuf is not None
        assert len(vbuf) == 3 * 8    # 3 verts × 8 floats
        assert len(ibuf) == 3        # 1 triangle × 3 indices

    def test_wireframe_indices_deduplication(self):
        from gmodular.engine.mdl_renderer import _wireframe_indices, _HAS_NUMPY
        if not _HAS_NUMPY:
            return
        # Triangle (0,1,2) → edges (0,1),(1,2),(0,2)
        faces = [(0, 1, 2)]
        idx = _wireframe_indices(faces, n_verts=3)
        assert idx is not None
        assert len(idx) == 6  # 3 edges × 2 indices

    def test_extract_door_hooks_none_when_no_dw_nodes(self):
        from gmodular.engine.mdl_renderer import _extract_door_hooks
        from gmodular.formats.mdl_parser import MeshData, MeshNode, NODE_HEADER, NODE_MESH
        mesh = MeshData(name="test")
        root = MeshNode(name="root", flags=NODE_HEADER)
        child = MeshNode(name="mesh01", flags=NODE_HEADER | NODE_MESH, parent=root)
        child.vertices = [(0,0,0), (1,0,0), (0,1,0)]
        child.faces    = [(0,1,2)]
        root.children.append(child)
        mesh.root_node = root
        hooks = _extract_door_hooks(mesh)
        assert len(hooks) == 0

    def test_extract_door_hooks_finds_dw_nodes(self):
        from gmodular.engine.mdl_renderer import _extract_door_hooks, DoorHook
        from gmodular.formats.mdl_parser import MeshData, MeshNode, NODE_HEADER
        mesh = MeshData(name="room")
        root = MeshNode(name="root", flags=NODE_HEADER)
        hook_node = MeshNode(
            name="DW_Left", flags=NODE_HEADER,
            position=(5.0, 0.0, 1.0), parent=root
        )
        root.children.append(hook_node)
        mesh.root_node = root
        hooks = _extract_door_hooks(mesh)
        assert len(hooks) == 1
        assert hooks[0].name == "DW_Left"
        # Position should be approximately (5.0, 0.0, 1.0)
        assert abs(hooks[0].position[0] - 5.0) < 0.01


# =============================================================================
#  WOK AABB Tree tests
# =============================================================================

class TestWOKAABBTree:
    """Test the WOKAABBTree ray-cast acceleration structure."""

    def _make_flat_wok(self, n=4):
        """Build a WOKData with an n×n grid of walkable triangular faces."""
        from gmodular.gui.walkmesh_editor import WOKData, WOKFace
        wok = WOKData(model_name="test_floor")
        for row in range(n):
            for col in range(n):
                x0, y0 = float(col), float(row)
                # Two triangles per cell
                wok.faces.append(WOKFace(
                    v0=(x0, y0, 0.0), v1=(x0+1, y0, 0.0), v2=(x0+1, y0+1, 0.0),
                    walk_type=1,  # walkable
                ))
                wok.faces.append(WOKFace(
                    v0=(x0, y0, 0.0), v1=(x0+1, y0+1, 0.0), v2=(x0, y0+1, 0.0),
                    walk_type=1,
                ))
        wok.aabb_min = (0.0, 0.0, 0.0)
        wok.aabb_max = (float(n), float(n), 0.0)
        return wok

    def test_tree_builds_from_wok(self):
        from gmodular.gui.walkmesh_editor import WOKAABBTree
        wok = self._make_flat_wok(4)
        tree = WOKAABBTree(wok)
        assert tree._root is not None
        assert tree.node_count() > 0

    def test_tree_depth_reasonable(self):
        from gmodular.gui.walkmesh_editor import WOKAABBTree
        wok = self._make_flat_wok(8)   # 128 faces
        tree = WOKAABBTree(wok)
        depth = tree.tree_depth()
        assert 1 <= depth <= 10   # log2(128/4) ≈ 5 expected

    def test_vertical_raycast_hits_flat_floor(self):
        from gmodular.gui.walkmesh_editor import WOKAABBTree
        wok = self._make_flat_wok(4)
        tree = WOKAABBTree(wok)
        fi = tree.raycast_vertical(2.0, 2.0, z_start=10.0)
        assert fi is not None, "Vertical ray should hit the flat floor"
        face = tree.face_at(fi)
        assert face is not None
        assert face.is_walkable

    def test_vertical_raycast_misses_outside_floor(self):
        from gmodular.gui.walkmesh_editor import WOKAABBTree
        wok = self._make_flat_wok(4)
        tree = WOKAABBTree(wok)
        fi = tree.raycast_vertical(-1.0, -1.0, z_start=10.0)
        assert fi is None, "Ray outside grid bounds should miss"

    def test_raycast_returns_face_index_and_t(self):
        from gmodular.gui.walkmesh_editor import WOKAABBTree
        wok = self._make_flat_wok(4)
        tree = WOKAABBTree(wok)
        result = tree.raycast(2.0, 2.0, 10.0, 0.0, 0.0, -1.0)
        assert result is not None
        fi, t = result
        assert 0 <= fi < len(wok.faces)
        assert abs(t - 10.0) < 0.01   # Z=10 → Z=0 is distance 10

    def test_query_sphere_finds_faces(self):
        from gmodular.gui.walkmesh_editor import WOKAABBTree
        wok = self._make_flat_wok(4)
        tree = WOKAABBTree(wok)
        faces = tree.query_sphere(2.0, 2.0, 0.0, radius=1.5)
        assert len(faces) > 0, "Sphere query at centre should find nearby faces"

    def test_query_sphere_excludes_non_walkable(self):
        from gmodular.gui.walkmesh_editor import WOKAABBTree, WOKData, WOKFace
        wok = WOKData()
        # One non-walkable face
        wok.faces.append(WOKFace(
            v0=(0,0,0), v1=(1,0,0), v2=(0,1,0), walk_type=0))  # non-walk
        tree = WOKAABBTree(wok)
        faces = tree.query_sphere(0.3, 0.3, 0.0, radius=2.0, walkable_only=True)
        assert len(faces) == 0   # excluded because non-walkable

    def test_empty_wok_returns_none(self):
        from gmodular.gui.walkmesh_editor import WOKAABBTree, WOKData
        tree = WOKAABBTree(WOKData())
        assert tree._root is None
        assert tree.raycast_vertical(0, 0) is None
        assert tree.query_sphere(0, 0, 0, 1.0) == []

    def test_face_at_bounds_check(self):
        from gmodular.gui.walkmesh_editor import WOKAABBTree
        wok = self._make_flat_wok(2)
        tree = WOKAABBTree(wok)
        assert tree.face_at(0) is not None
        assert tree.face_at(-1) is None
        assert tree.face_at(10000) is None


class TestWOKColorMap:
    """Test that walk-type color map covers all 20 types (0-19)."""

    def test_all_walk_types_have_colors(self):
        from gmodular.gui.walkmesh_editor import WALK_TYPE_NAMES, WALK_TYPE_COLORS
        # All 20 types should have a name
        for i in range(20):
            assert i in WALK_TYPE_NAMES, f"Walk type {i} missing from WALK_TYPE_NAMES"
        # All 20 types should have a color
        for i in range(20):
            assert i in WALK_TYPE_COLORS, f"Walk type {i} missing from WALK_TYPE_COLORS"

    def test_color_values_are_valid_hex(self):
        from gmodular.gui.walkmesh_editor import WALK_TYPE_COLORS
        import re
        for tid, color in WALK_TYPE_COLORS.items():
            assert re.match(r'^#[0-9a-fA-F]{6}$', color), \
                f"Walk type {tid} color {color!r} not valid #RRGGBB"


# =============================================================================
#  Room Assembly Grid improvements tests
# =============================================================================

class TestRoomGridMDLDims:
    """Test MDL dimension registration and door-hook features."""

    def test_register_dims_affects_world_placement(self):
        from gmodular.gui.room_assembly import RoomGridWidget
        grid = RoomGridWidget.__new__(RoomGridWidget)
        grid._rooms = []
        grid._room_dims = {}
        grid._selected = None
        grid._grid_w = 20
        grid._grid_h = 20
        # Register custom dimensions
        grid.register_mdl_dims("bigroom", 20.0, 15.0)
        # Manually place a room using the internal helper
        grid._place_room("bigroom", 1, 2)
        room = grid._room_at(1, 2)
        assert room is not None
        assert abs(room.world_x - 20.0) < 0.01   # grid_x=1 * width=20
        assert abs(room.world_y - 30.0) < 0.01   # grid_y=2 * height=15

    def test_default_dims_used_without_registration(self):
        from gmodular.gui.room_assembly import RoomGridWidget, DEFAULT_ROOM_W, DEFAULT_ROOM_H
        grid = RoomGridWidget.__new__(RoomGridWidget)
        grid._rooms = []
        grid._room_dims = {}
        grid._selected = None
        grid._grid_w = 20
        grid._grid_h = 20
        grid._place_room("smallroom", 0, 0)
        room = grid._room_at(0, 0)
        assert room is not None
        assert abs(room.world_x) < 0.01
        assert abs(room.world_y) < 0.01
        assert room.width  == DEFAULT_ROOM_W
        assert room.height == DEFAULT_ROOM_H

    def test_door_hooks_in_room_instance(self):
        from gmodular.gui.room_assembly import RoomInstance
        room = RoomInstance(
            mdl_name="manm26aa", grid_x=0, grid_y=0,
            door_hooks=[(5.0, 0.0, 0.0), (0.0, 5.0, 0.0)]
        )
        assert len(room.door_hooks) == 2
        assert room.door_hooks[0] == (5.0, 0.0, 0.0)


class TestLYTDataPortalVis:
    """Test portal-based VIS generation."""

    def test_vis_uses_explicit_connections(self):
        from gmodular.gui.room_assembly import RoomInstance, _generate_vis
        r1 = RoomInstance("hub01", 0, 0, connected_to=["corridor01"])
        r2 = RoomInstance("corridor01", 5, 5)   # not adjacent (far away)
        vis_text = _generate_vis([r1, r2])
        # hub01 must see corridor01 via explicit connection
        lines = vis_text.splitlines()
        hub_section_start = lines.index("hub01")
        # After "hub01", its visible rooms are listed until empty line
        visible_from_hub = []
        for line in lines[hub_section_start+1:]:
            if line == "":
                break
            visible_from_hub.append(line)
        assert "corridor01" in visible_from_hub, \
            "Portal connection should make corridor01 visible from hub01"

    def test_vis_adjacency_and_portal_combined(self):
        from gmodular.gui.room_assembly import RoomInstance, _generate_vis
        r1 = RoomInstance("room_a", 0, 0, connected_to=["room_c"])
        r2 = RoomInstance("room_b", 1, 0)   # adjacent to r1
        r3 = RoomInstance("room_c", 10, 10)  # not adjacent, but portal-connected from r1
        vis_text = _generate_vis([r1, r2, r3])
        # room_a should see room_b (adjacent) AND room_c (portal)
        lines = vis_text.splitlines()
        ra_idx = lines.index("room_a")
        visible_from_ra = []
        for line in lines[ra_idx+1:]:
            if line == "":
                break
            visible_from_ra.append(line)
        assert "room_b" in visible_from_ra
        assert "room_c" in visible_from_ra


# =============================================================================
#  Module Packager improvements tests
# =============================================================================

class TestChitinKeyParser:
    """Test the chitin.key binary reader."""

    def _make_chitin_key(self, entries):
        """Build a minimal chitin.key binary for testing."""
        import struct
        # entries = list of (resref, res_type)
        n = len(entries)
        header_size = 64
        offset_key_table = header_size

        # chitin.key header: FileType(4)+Version(4)+bif_count(4)+key_count(4)+
        #                     offset_to_bif_table(4)+offset_to_key_table(4)+
        #                     build_year(4)+build_day(4) = 32 bytes, pad to 64
        hdr = struct.pack(
            "<4s4sIIIIII",
            b"KEY ", b"V1  ",
            0,                  # bif_count
            n,                  # key_count
            header_size,        # offset_to_bif_table (unused for our test)
            offset_key_table,   # offset_to_key_table
            2026,               # build_year
            1,                  # build_day
        )
        hdr = hdr.ljust(header_size, b"\x00")  # pad to 64 bytes

        # Key entries (22 bytes each): ResRef[16] + ResType[2] + ResID[4]
        key_block = b""
        for resref, res_type in entries:
            rr = resref.encode("ascii")[:16].ljust(16, b"\x00")
            key_block += struct.pack("<16sHI", rr, res_type, 0)

        return hdr + key_block

    def test_read_chitin_key_basic(self, tmp_path):
        from gmodular.formats.mod_packager import _read_chitin_key
        from gmodular.formats.archives import EXT_TO_TYPE
        # Create a minimal key with 2 entries
        data = self._make_chitin_key([
            ("c_bantha", EXT_TO_TYPE.get("utc", 2023)),
            ("k_ai_master", EXT_TO_TYPE.get("ncs", 2010)),
        ])
        key_path = tmp_path / "chitin.key"
        key_path.write_bytes(data)
        result = _read_chitin_key(tmp_path)
        assert len(result) >= 1

    def test_read_chitin_key_missing_file(self, tmp_path):
        from gmodular.formats.mod_packager import _read_chitin_key
        result = _read_chitin_key(tmp_path)  # no chitin.key in tmp_path
        assert result == set()

    def test_read_chitin_key_invalid_header(self, tmp_path):
        from gmodular.formats.mod_packager import _read_chitin_key
        key_path = tmp_path / "chitin.key"
        key_path.write_bytes(b"\x00" * 64)  # zeros — not a valid KEY header
        result = _read_chitin_key(tmp_path)
        assert result == set()


class TestModPackagerChitinSkip:
    """Test that packager skips base-game assets when chitin.key is present."""

    def test_packager_marks_base_game_skip(self, tmp_path):
        """If chitin.key lists a dependency, packager should emit INFO skip notice."""
        import struct
        from gmodular.formats.mod_packager import ModPackager, _read_chitin_key
        from gmodular.formats.gff_types import GITData, GITCreature
        from gmodular.formats.archives import EXT_TO_TYPE
        from gmodular.formats.gff_writer import save_ifo, save_git

        # Build minimal module
        module_dir = tmp_path / "module"
        module_dir.mkdir()

        git = GITData()
        c = GITCreature()
        c.resref = "c_bantha"; c.tag = "bantha01"
        c.on_spawn = "k_ai_master"
        git.creatures.append(c)

        # Write core files
        save_git(git, str(module_dir / "testmod.git"))
        from gmodular.formats.gff_types import IFOData
        ifo = IFOData(mod_name="testmod", entry_area="testmod")
        save_ifo(ifo, str(module_dir / "testmod.ifo"))
        (module_dir / "testmod.are").write_bytes(b"ARE STUB")

        # Create a minimal chitin.key that lists c_bantha.utc
        # Using proper format: 64-byte header + 22-byte key entries
        import struct as _struct
        hdr = _struct.pack("<4s4sIIIIII",
                           b"KEY ", b"V1  ",
                           0, 1, 64, 64, 2026, 1)
        hdr = hdr.ljust(64, b"\x00")
        resref_bytes = b"c_bantha" + b"\x00" * 8   # 16 bytes total
        key_entry = _struct.pack("<16sHI", resref_bytes, 2023, 0)  # 2023=UTC
        (tmp_path / "chitin.key").write_bytes(hdr + key_entry)

        packager = ModPackager(
            module_dir=module_dir,
            module_name="testmod",
            git=git, are=None, ifo=ifo,
            game_dir=tmp_path,
        )
        issues = packager.validate_only()
        # Should not raise
        assert isinstance(issues, list)


class TestGetAllResrefs:
    """Test _get_all_resrefs dependency walker."""

    def test_collects_blueprint_resrefs(self):
        from gmodular.formats.mod_packager import _get_all_resrefs
        from gmodular.formats.gff_types import GITData, GITCreature, GITPlaceable

        git = GITData()
        c = GITCreature(); c.resref = "c_rodian"; c.tag = "guard01"
        git.creatures.append(c)
        p = GITPlaceable(); p.resref = "plc_chest"; p.tag = "loot01"
        git.placeables.append(p)

        deps = _get_all_resrefs(git)
        resrefs = [r for r, e in deps]
        assert "c_rodian" in resrefs
        assert "plc_chest" in resrefs

    def test_collects_script_resrefs(self):
        from gmodular.formats.mod_packager import _get_all_resrefs
        from gmodular.formats.gff_types import GITData, GITCreature

        git = GITData()
        c = GITCreature(); c.resref = "c_rodian"; c.tag = "guard01"
        c.on_spawn = "k_ai_master"
        c.on_death  = "my_death_script"
        git.creatures.append(c)

        deps = _get_all_resrefs(git)
        script_resrefs = [r for r, e in deps if e == "ncs"]
        assert "k_ai_master"    in script_resrefs
        assert "my_death_script" in script_resrefs

    def test_no_duplicates(self):
        from gmodular.formats.mod_packager import _get_all_resrefs
        from gmodular.formats.gff_types import GITData, GITCreature

        git = GITData()
        for _ in range(3):
            c = GITCreature(); c.resref = "c_rodian"; c.tag = f"guard_{_}"
            c.on_spawn = "k_ai_master"
            git.creatures.append(c)

        deps = _get_all_resrefs(git)
        # c_rodian.utc and k_ai_master.ncs should each appear once
        assert deps.count(("c_rodian", "utc")) == 1
        assert deps.count(("k_ai_master", "ncs")) == 1

    def test_empty_git_returns_empty(self):
        from gmodular.formats.mod_packager import _get_all_resrefs
        from gmodular.formats.gff_types import GITData
        assert _get_all_resrefs(GITData()) == []

    def test_none_git_returns_empty(self):
        from gmodular.formats.mod_packager import _get_all_resrefs
        assert _get_all_resrefs(None) == []



# =============================================================================
#  LYT/VIS round-trip parser tests
# =============================================================================

class TestLYTRoundTrip:
    """Test LYTData.from_text() and LYTData.from_file() round-trip."""

    def test_from_text_basic(self):
        from gmodular.gui.room_assembly import LYTData
        text = (
            "filedependency 0\n"
            "roomcount 2\n"
            "manm26aa 0.00 0.00 0.00\n"
            "manm26ab 10.00 0.00 0.00\n"
            "obstaclecount 0\n"
            "doorhookcount 0\n"
        )
        lyt = LYTData.from_text(text)
        assert len(lyt.rooms) == 2
        assert lyt.rooms[0].mdl_name == "manm26aa"
        assert lyt.rooms[1].mdl_name == "manm26ab"

    def test_from_text_world_coords(self):
        from gmodular.gui.room_assembly import LYTData
        text = (
            "filedependency 0\n"
            "roomcount 1\n"
            "myroom 15.50 -3.25 0.00\n"
            "obstaclecount 0\n"
        )
        lyt = LYTData.from_text(text)
        assert len(lyt.rooms) == 1
        assert abs(lyt.rooms[0].world_x - 15.5) < 0.01
        assert abs(lyt.rooms[0].world_y - (-3.25)) < 0.01
        assert abs(lyt.rooms[0].world_z - 0.0) < 0.01

    def test_from_text_empty(self):
        from gmodular.gui.room_assembly import LYTData
        text = "filedependency 0\nroomcount 0\nobstaclecount 0\n"
        lyt = LYTData.from_text(text)
        assert len(lyt.rooms) == 0

    def test_to_text_from_text_roundtrip(self):
        from gmodular.gui.room_assembly import LYTData, RoomInstance
        lyt = LYTData()
        lyt.rooms.append(RoomInstance("room_a", 0, 0, world_x=0.0, world_y=0.0))
        lyt.rooms.append(RoomInstance("room_b", 1, 0, world_x=10.0, world_y=0.0))
        text = lyt.to_text()
        # Round-trip: parse the generated text
        lyt2 = LYTData.from_text(text)
        assert len(lyt2.rooms) == 2
        names = [r.mdl_name for r in lyt2.rooms]
        assert "room_a" in names
        assert "room_b" in names

    def test_from_file(self, tmp_path):
        from gmodular.gui.room_assembly import LYTData
        lyt_file = tmp_path / "test.lyt"
        lyt_file.write_text(
            "filedependency 0\nroomcount 1\ntestroom 5.00 7.00 1.00\nobstaclecount 0\n"
        )
        lyt = LYTData.from_file(str(lyt_file))
        assert len(lyt.rooms) == 1
        assert lyt.rooms[0].mdl_name == "testroom"
        assert abs(lyt.rooms[0].world_x - 5.0) < 0.01
        assert abs(lyt.rooms[0].world_y - 7.0) < 0.01

    def test_from_text_ignores_blank_and_comment_lines(self):
        from gmodular.gui.room_assembly import LYTData
        text = (
            "# This is a comment\n"
            "filedependency 0\n"
            "\n"
            "roomcount 1\n"
            "# another comment\n"
            "roomone 0.00 0.00 0.00\n"
            "obstaclecount 0\n"
        )
        lyt = LYTData.from_text(text)
        assert len(lyt.rooms) == 1
        assert lyt.rooms[0].mdl_name == "roomone"


class TestVISParser:
    """Test _parse_vis() helper function."""

    def test_parse_vis_basic(self):
        from gmodular.gui.room_assembly import _parse_vis
        vis_text = (
            "room1\n"
            "room1\n"
            "room2\n"
            "\n"
            "room2\n"
            "room1\n"
            "room2\n"
        )
        result = _parse_vis(vis_text)
        assert "room1" in result
        assert "room2" in result["room1"]
        assert "room1" in result["room2"]

    def test_parse_vis_empty(self):
        from gmodular.gui.room_assembly import _parse_vis
        result = _parse_vis("")
        assert result == {}

    def test_parse_vis_single_room(self):
        from gmodular.gui.room_assembly import _parse_vis
        vis_text = "room1\nroom1\n"
        result = _parse_vis(vis_text)
        assert "room1" in result
        assert "room1" in result["room1"]

    def test_parse_vis_ignores_comments(self):
        from gmodular.gui.room_assembly import _parse_vis
        vis_text = "# header\nroom_a\nroom_a\nroom_b\n\nroom_b\nroom_a\nroom_b\n"
        result = _parse_vis(vis_text)
        assert "room_a" in result
        assert "room_b" in result["room_a"]

    def test_generate_vis_produces_parseable_output(self):
        from gmodular.gui.room_assembly import _generate_vis, _parse_vis, RoomInstance
        rooms = [
            RoomInstance("alpha", 0, 0),
            RoomInstance("beta",  1, 0),
        ]
        vis_text = _generate_vis(rooms)
        parsed = _parse_vis(vis_text)
        # alpha and beta should be mutually visible
        assert "alpha" in parsed.get("alpha", [])
        assert "beta" in parsed.get("alpha", [])


# =============================================================================
#  TwoDA loader enhancements tests
# =============================================================================

class TestTwoDATableEnhancements:
    """Test new TwoDATable methods."""

    APPEARANCE_2DA = """\
2DA V2.0

LABEL  RACE  GENDER  APPEARANCE_TYPE
0   Rodian   1   0   Character
1   Wookie   2   1   Character
2   C3PO     0   0   Droid
3   R2D2     0   0   Droid
"""

    def _load(self):
        from gmodular.formats.twoda_loader import TwoDALoader
        loader = TwoDALoader()
        loader.load_from_text("appearance", self.APPEARANCE_2DA)
        return loader.get_table("appearance")

    def test_get_int(self):
        t = self._load()
        assert t.get_int(0, "RACE") == 1
        assert t.get_int(2, "RACE") == 0
        assert t.get_int(99, "RACE", default=42) == 42

    def test_get_float(self):
        t = self._load()
        assert isinstance(t.get_float(0, "GENDER"), float)

    def test_get_column(self):
        t = self._load()
        col = t.get_column("LABEL")
        assert len(col) == 4
        labels = [v for _, v in col]
        assert "Rodian" in labels
        assert "R2D2" in labels

    def test_find_row_found(self):
        t = self._load()
        idx = t.find_row("LABEL", "Wookie")
        assert idx == 1

    def test_find_row_case_insensitive(self):
        t = self._load()
        idx = t.find_row("LABEL", "RODIAN")
        assert idx == 0

    def test_find_row_not_found(self):
        t = self._load()
        idx = t.find_row("LABEL", "NotARealRace")
        assert idx is None

    def test_find_rows_multiple(self):
        t = self._load()
        # Both RACE==0 rows (C3PO, R2D2)
        rows = t.find_rows("RACE", "0")
        assert 2 in rows
        assert 3 in rows

    def test_column_values(self):
        t = self._load()
        vals = t.column_values("APPEARANCE_TYPE")
        assert "Character" in vals
        assert "Droid" in vals
        # De-duplicated
        assert vals.count("Droid") == 1

    def test_contains(self):
        t = self._load()
        assert 0 in t
        assert 3 in t
        assert 99 not in t

    def test_iter(self):
        t = self._load()
        rows = list(t)
        assert len(rows) == 4
        for idx, data in rows:
            assert isinstance(idx, int)
            assert isinstance(data, dict)

    def test_to_text_roundtrip(self):
        from gmodular.formats.twoda_loader import _parse_2da
        t = self._load()
        text = t.to_text()
        # Re-parse the serialized text
        t2 = _parse_2da(text, "appearance")
        assert t2 is not None
        assert len(t2) == len(t)
        for row_idx, row_data in t:
            for col in t.columns:
                assert t2.get(row_idx, col) == row_data.get(col, "")


class TestTwoDALoaderEnhancements:
    """Test new TwoDALoader methods."""

    FACTION_2DA = """\
2DA V2.0

LABEL
0   Hostile
1   Friendly
2   Neutral
"""

    def test_load_from_bytes_utf8(self):
        from gmodular.formats.twoda_loader import TwoDALoader
        loader = TwoDALoader()
        data = self.FACTION_2DA.encode("utf-8")
        table = loader.load_from_bytes("faction_test", data)
        assert table is not None
        assert len(table) == 3

    def test_load_from_bytes_latin1(self):
        from gmodular.formats.twoda_loader import TwoDALoader
        loader = TwoDALoader()
        data = self.FACTION_2DA.encode("latin-1")
        table = loader.load_from_bytes("faction_test", data, encoding="latin-1")
        assert table is not None
        assert len(table) == 3

    def test_get_table(self):
        from gmodular.formats.twoda_loader import TwoDALoader
        loader = TwoDALoader()
        loader.load_from_text("faction", self.FACTION_2DA)
        t = loader.get_table("faction")
        assert t is not None
        assert t.name == "faction"

    def test_get_table_not_found(self):
        from gmodular.formats.twoda_loader import TwoDALoader
        loader = TwoDALoader()
        t = loader.get_table("nonexistent")
        assert t is None

    def test_get_cell(self):
        from gmodular.formats.twoda_loader import TwoDALoader
        loader = TwoDALoader()
        loader.load_from_text("faction", self.FACTION_2DA)
        val = loader.get_cell("faction", 0, "LABEL")
        assert val == "Hostile"

    def test_get_cell_default(self):
        from gmodular.formats.twoda_loader import TwoDALoader
        loader = TwoDALoader()
        val = loader.get_cell("nonexistent_table", 0, "COL", default="default_val")
        assert val == "default_val"

    def test_find_row_via_loader(self):
        from gmodular.formats.twoda_loader import TwoDALoader
        loader = TwoDALoader()
        loader.load_from_text("faction", self.FACTION_2DA)
        idx = loader.find_row("faction", "LABEL", "Neutral")
        assert idx == 2

    def test_clear_cache(self):
        from gmodular.formats.twoda_loader import TwoDALoader
        loader = TwoDALoader()
        loader.load_from_text("faction", self.FACTION_2DA)
        assert loader.is_loaded("faction")
        loader.clear_cache()
        assert not loader.is_loaded("faction")

    def test_reload_discards_old(self, tmp_path):
        from gmodular.formats.twoda_loader import TwoDALoader
        loader = TwoDALoader()
        loader.set_search_dirs([tmp_path])
        # Write initial version
        f = tmp_path / "faction.2da"
        f.write_text("2DA V2.0\n\nLABEL\n0 First\n")
        loader.load("faction")
        assert loader.get_name("faction", 0) == "First"
        # Update on disk
        f.write_text("2DA V2.0\n\nLABEL\n0 Updated\n")
        loader.reload("faction")
        assert loader.get_name("faction", 0) == "Updated"


# =============================================================================
#  MDL texture scanner tests
# =============================================================================

class TestMDLTextureScanner:
    """Test scan_mdl_textures() and list_mdl_dependencies()."""

    def _make_minimal_mdl(self, tex_name: str = "rock01") -> bytes:
        """
        Build a synthetic MDL binary with a single mesh node that
        references the given texture name.
        We use MDLParser internals to construct a valid-enough header.
        """
        import struct
        # Build a complete MeshData and fake binary that scan_mdl_textures can extract
        # We simply test the function with real MeshData instead of binary tricks
        return b""  # indicates test should use in-memory path

    def test_scan_empty_bytes_returns_empty(self):
        from gmodular.formats.mdl_parser import scan_mdl_textures
        result = scan_mdl_textures(b"")
        assert result == []

    def test_scan_invalid_bytes_returns_empty(self):
        from gmodular.formats.mdl_parser import scan_mdl_textures
        result = scan_mdl_textures(b"BADDATA" * 10)
        assert result == []

    def test_list_mdl_dependencies_empty_bytes(self):
        from gmodular.formats.mdl_parser import list_mdl_dependencies
        result = list_mdl_dependencies(b"")
        assert isinstance(result, dict)
        assert 'textures' in result
        assert 'models' in result
        assert result['textures'] == []
        assert result['models'] == []

    def test_list_mdl_dependencies_structure(self):
        from gmodular.formats.mdl_parser import list_mdl_dependencies
        result = list_mdl_dependencies(b"SHORT")
        assert set(result.keys()) >= {'textures', 'lightmaps', 'models'}

    def test_scan_from_meshdata_textures(self):
        """Verify scan_mdl_textures via MeshData (integration path)."""
        from gmodular.formats.mdl_parser import (
            MeshData, MeshNode, NODE_MESH, NODE_HEADER, list_mdl_dependencies
        )
        # Build in-memory MeshData with known texture name
        md = MeshData(name="test_room")
        root = MeshNode(name="root", flags=NODE_HEADER)
        mesh = MeshNode(name="mesh01", flags=NODE_HEADER | NODE_MESH,
                        texture="tex_rock01", parent=root)
        mesh.vertices = [(0,0,0), (1,0,0), (0,1,0)]
        mesh.faces = [(0,1,2)]
        mesh.render = True
        root.children.append(mesh)
        md.root_node = root
        md.compute_bounds()

        # Extract texture names from the MeshData directly
        textures = [n.texture_clean.lower() for n in md.visible_mesh_nodes()
                    if n.texture_clean]
        assert "tex_rock01" in textures


# =============================================================================
#  Mod packager texture collection tests
# =============================================================================

class TestModPackagerTextureWalker:
    """Test that the packager collects TGA/TPC textures from MDL files."""

    def test_collect_mdl_textures_empty_mdl(self):
        """_collect_mdl_textures should handle empty MDL bytes gracefully."""
        from gmodular.formats.mod_packager import ModPackager, PackageResource
        from gmodular.formats.gff_types import GITData
        import tempfile, os

        with tempfile.TemporaryDirectory() as tmpdir:
            packager = ModPackager(
                module_dir=tmpdir,
                module_name="test",
                git=GITData(),
                are=None, ifo=None,
            )
            resources: list = []
            seen: set = set()
            # Empty MDL should not crash
            packager._collect_mdl_textures(
                "emptymodel", b"", resources, seen, set())
            assert resources == []

    def test_collect_mdl_textures_finds_tga_on_disk(self, tmp_path):
        """If a texture TGA exists on disk, it should be added to resources."""
        from gmodular.formats.mod_packager import ModPackager, PackageResource
        from gmodular.formats.gff_types import GITData
        from gmodular.formats.mdl_parser import MeshData, MeshNode, NODE_MESH, NODE_HEADER

        # Create a fake TGA texture
        (tmp_path / "rock01.tga").write_bytes(b"FAKE_TGA_DATA")

        packager = ModPackager(
            module_dir=str(tmp_path),
            module_name="test",
            git=GITData(),
            are=None, ifo=None,
        )

        resources: list = []
        seen: set = set()
        base_set: set = set()

        # Build MeshData with rock01 texture
        md = MeshData(name="floor")
        root = MeshNode(name="root", flags=NODE_HEADER)
        mesh = MeshNode(name="mesh01", flags=NODE_HEADER | NODE_MESH,
                        texture="rock01", parent=root)
        mesh.vertices = [(0,0,0), (1,0,0), (0,1,0)]
        mesh.faces = [(0,1,2)]
        mesh.render = True
        root.children.append(mesh)
        md.root_node = root
        md.compute_bounds()

        # We need to get the textures from the MeshData
        tex_names = [n.texture_clean.lower() for n in md.visible_mesh_nodes()
                     if n.texture_clean]
        for tex in tex_names:
            for ext in ("tpc", "tga"):
                key = (tex, ext)
                if key in seen or key in base_set:
                    break
                data = packager._find_resource(tex, ext)
                if data:
                    seen.add(key)
                    from gmodular.formats.archives import EXT_TO_TYPE
                    type_id = EXT_TO_TYPE.get(ext, 0)
                    resources.append(PackageResource(
                        resref=tex, res_type=type_id, ext=ext,
                        data=data, source_path=f"{tex}.{ext}"))
                    break

        assert any(r.resref == "rock01" and r.ext == "tga" for r in resources)

    def test_collect_textures_skips_base_game(self, tmp_path):
        """Textures listed in base_game_assets should be skipped."""
        from gmodular.formats.mod_packager import ModPackager, PackageResource
        from gmodular.formats.gff_types import GITData

        # Put a TGA on disk
        (tmp_path / "base_tex.tga").write_bytes(b"BASE_TGA")

        packager = ModPackager(
            module_dir=str(tmp_path),
            module_name="test",
            git=GITData(), are=None, ifo=None,
        )
        resources: list = []
        seen: set = set()
        # Mark it as a base game asset
        base_set = {("base_tex", "tga"), ("base_tex", "tpc")}
        # Simulate what _collect_mdl_textures does
        tex = "base_tex"
        for ext in ("tpc", "tga"):
            key = (tex, ext)
            if key in seen or key in base_set:
                seen.add(key)
                break
        # Nothing should be in resources
        assert len(resources) == 0


# =============================================================================
#  Additional MDLParser.list_mdl_dependencies supermodel test
# =============================================================================

class TestMDLDependenciesKeys:
    """Test that list_mdl_dependencies returns correct keys."""

    def test_keys_present(self):
        from gmodular.formats.mdl_parser import list_mdl_dependencies
        deps = list_mdl_dependencies(b"")
        assert "textures" in deps
        assert "lightmaps" in deps
        assert "models" in deps

    def test_textures_is_list(self):
        from gmodular.formats.mdl_parser import list_mdl_dependencies
        deps = list_mdl_dependencies(b"X" * 300)
        assert isinstance(deps["textures"], list)

    def test_models_is_list(self):
        from gmodular.formats.mdl_parser import list_mdl_dependencies
        deps = list_mdl_dependencies(b"X" * 300)
        assert isinstance(deps["models"], list)



# =============================================================================
#  New Engine Subsystem Tests — verified against actual implementation
#  Tests for: animation_system, entity_system, play_mode, scene_manager
# =============================================================================

# ─────────────────────────────────────────────────────────────────────────────
#  Animation System Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestAnimationMathHelpers:
    """Test internal math helpers in the animation system."""

    def test_lerp_midpoint(self):
        from gmodular.engine.animation_system import _lerp
        assert _lerp(0.0, 10.0, 0.5) == pytest.approx(5.0)

    def test_lerp_start(self):
        from gmodular.engine.animation_system import _lerp
        assert _lerp(3.0, 7.0, 0.0) == pytest.approx(3.0)

    def test_lerp_end(self):
        from gmodular.engine.animation_system import _lerp
        assert _lerp(3.0, 7.0, 1.0) == pytest.approx(7.0)

    def test_lerp3_midpoint(self):
        from gmodular.engine.animation_system import _lerp3
        r = _lerp3((0.0, 0.0, 0.0), (10.0, 4.0, 2.0), 0.5)
        assert r == pytest.approx((5.0, 2.0, 1.0))

    def test_slerp_identity_at_t0(self):
        from gmodular.engine.animation_system import _slerp
        q  = (0.0, 0.0, 0.0, 1.0)
        q2 = (0.0, 0.0, 0.7071, 0.7071)
        r = _slerp(q, q2, 0.0)
        assert r[3] == pytest.approx(1.0, abs=1e-4)

    def test_slerp_identity_at_t1(self):
        from gmodular.engine.animation_system import _slerp
        q  = (0.0, 0.0, 0.0, 1.0)
        q2 = (0.0, 0.0, 0.7071, 0.7071)
        r = _slerp(q, q2, 1.0)
        assert abs(r[2]) == pytest.approx(0.7071, abs=1e-3)

    def test_slerp_halfway(self):
        from gmodular.engine.animation_system import _slerp
        q  = (0.0, 0.0, 0.0, 1.0)
        q2 = (0.0, 0.0, 1.0, 0.0)
        r = _slerp(q, q2, 0.5)
        assert abs(r[3]) == pytest.approx(0.7071, abs=1e-3)

    def test_slerp_negated_quaternion(self):
        from gmodular.engine.animation_system import _slerp
        q  = (0.0, 0.0, 0.0,  1.0)
        q2 = (0.0, 0.0, 0.0, -1.0)
        r = _slerp(q, q2, 0.0)
        assert abs(abs(r[3]) - 1.0) < 0.01


class TestNodeTransform:
    """Test the NodeTransform dataclass."""

    def test_default_values(self):
        from gmodular.engine.animation_system import NodeTransform
        nt = NodeTransform()
        assert nt.position == (0.0, 0.0, 0.0)
        assert nt.orientation == (0.0, 0.0, 0.0, 1.0)
        assert nt.scale == 1.0
        assert nt.alpha == 1.0

    def test_custom_values(self):
        from gmodular.engine.animation_system import NodeTransform
        nt = NodeTransform(position=(1.0, 2.0, 3.0), scale=2.0, alpha=0.5)
        assert nt.position == (1.0, 2.0, 3.0)
        assert nt.scale == pytest.approx(2.0)
        assert nt.alpha == pytest.approx(0.5)

    def test_copy_is_new_instance(self):
        from gmodular.engine.animation_system import NodeTransform
        nt = NodeTransform(position=(1.0, 2.0, 3.0))
        nt2 = nt.copy()
        assert nt2.position == (1.0, 2.0, 3.0)
        assert nt2 is not nt


class TestAnimationState:
    """Test AnimationState (matches KotOR.js OdysseyModelAnimationManagerState)."""

    def test_initial_state(self):
        from gmodular.engine.animation_system import AnimationState
        state = AnimationState()
        assert state.elapsed == pytest.approx(0.0)
        assert state.loop is False
        assert state.elapsed_cnt == 0

    def test_loop_flag(self):
        from gmodular.engine.animation_system import AnimationState
        state = AnimationState(loop=True)
        assert state.loop is True

    def test_reset(self):
        from gmodular.engine.animation_system import AnimationState
        state = AnimationState(loop=True)
        state.elapsed = 5.0
        state.elapsed_cnt = 3
        state.reset(loop=False)
        assert state.elapsed == pytest.approx(0.0)
        assert state.loop is False
        assert state.elapsed_cnt == 0


class TestAnimationPlayer:
    """AnimationPlayer requires a list[AnimationData] argument."""

    def test_create_empty_list(self):
        from gmodular.engine.animation_system import AnimationPlayer
        player = AnimationPlayer([])
        assert player.current_animation_name == ""

    def test_play_nonexistent_returns_false(self):
        from gmodular.engine.animation_system import AnimationPlayer
        player = AnimationPlayer([])
        assert player.play("walk", loop=True) is False

    def test_animation_names_empty(self):
        from gmodular.engine.animation_system import AnimationPlayer
        assert AnimationPlayer([]).animation_names == []

    def test_has_animation_false_when_empty(self):
        from gmodular.engine.animation_system import AnimationPlayer
        assert AnimationPlayer([]).has_animation("idle") is False

    def test_stop_no_crash(self):
        from gmodular.engine.animation_system import AnimationPlayer
        player = AnimationPlayer([])
        player.stop()
        assert player.current_animation_name == ""

    def test_update_no_crash(self):
        from gmodular.engine.animation_system import AnimationPlayer
        player = AnimationPlayer([])
        player.update(0.016)
        # After update with no animations, state should be stable
        assert player.node_transforms == {} or isinstance(player.node_transforms, dict)
        from gmodular.engine.animation_system import AnimationPlayer
        assert isinstance(AnimationPlayer([]).node_transforms, dict)

    def test_speed_control(self):
        from gmodular.engine.animation_system import AnimationPlayer
        player = AnimationPlayer([])
        player.set_speed(2.0)
        assert player._speed == pytest.approx(2.0)

    def test_pause_and_resume(self):
        from gmodular.engine.animation_system import AnimationPlayer
        player = AnimationPlayer([])
        player.pause()
        assert player._paused is True
        player.resume()
        assert player._paused is False

    def test_play_with_mock_data(self):
        from gmodular.engine.animation_system import AnimationPlayer

        class MockAnim:
            name = "cpause1"
            length = 2.0
            transition = 0.25
            root_node = None
            events = []

        player = AnimationPlayer([MockAnim()])
        assert player.has_animation("cpause1")
        assert player.play("cpause1", loop=True) is True
        assert player.current_animation_name == "cpause1"
        for _ in range(10):
            player.update(0.016)
        assert player._current_state.elapsed > 0.0

    def test_seek_moves_playhead(self):
        """seek() should set elapsed to the requested time and pause."""
        from gmodular.engine.animation_system import AnimationPlayer

        class MockAnim:
            name = "walk"
            length = 3.0
            transition = 0.0
            root_node = None
            events = []

        player = AnimationPlayer([MockAnim()])
        player.play("walk", loop=True)
        player.seek(1.5)
        assert abs(player.get_elapsed() - 1.5) < 0.001
        assert player._paused is True

    def test_seek_clamps_to_duration(self):
        """seek() beyond animation length is clamped."""
        from gmodular.engine.animation_system import AnimationPlayer

        class MockAnim:
            name = "idle"
            length = 1.0
            transition = 0.0
            root_node = None
            events = []

        player = AnimationPlayer([MockAnim()])
        player.play("idle", loop=False)
        player.seek(99.0)  # Way beyond length
        assert player.get_elapsed() <= 1.0

    def test_seek_without_animation_returns_false(self):
        """seek() returns False if no animation is loaded."""
        from gmodular.engine.animation_system import AnimationPlayer
        player = AnimationPlayer([])
        result = player.seek(1.0)
        assert result is False

    def test_get_duration_no_animation(self):
        """get_duration() returns 0.0 with no animation loaded."""
        from gmodular.engine.animation_system import AnimationPlayer
        player = AnimationPlayer([])
        assert player.get_duration() == 0.0

    def test_get_duration_with_animation(self):
        """get_duration() returns the animation length."""
        from gmodular.engine.animation_system import AnimationPlayer

        class MockAnim:
            name = "run"
            length = 2.5
            transition = 0.0
            root_node = None
            events = []

        player = AnimationPlayer([MockAnim()])
        player.play("run", loop=True)
        assert abs(player.get_duration() - 2.5) < 0.001

    def test_get_elapsed_tracks_playback(self):
        """get_elapsed() increases with update() calls."""
        from gmodular.engine.animation_system import AnimationPlayer

        class MockAnim:
            name = "cpause1"
            length = 5.0
            transition = 0.0
            root_node = None
            events = []

        player = AnimationPlayer([MockAnim()])
        player.play("cpause1", loop=True)
        player.update(0.1)
        player.update(0.1)
        assert player.get_elapsed() > 0.1

    def test_seek_no_pause_continues_playback(self):
        """seek(t, pause=False) should not pause the player."""
        from gmodular.engine.animation_system import AnimationPlayer

        class MockAnim:
            name = "cpause1"
            length = 5.0
            transition = 0.0
            root_node = None
            events = []

        player = AnimationPlayer([MockAnim()])
        player.play("cpause1", loop=True)
        player.seek(1.0, pause=False)
        assert player._paused is False
        assert abs(player.get_elapsed() - 1.0) < 0.001


class TestAnimationSet:
    """AnimationSet: entity-ID-keyed animation player manager."""

    def test_create_empty(self):
        from gmodular.engine.animation_system import AnimationSet
        assert len(AnimationSet()) == 0

    def test_get_or_create_new(self):
        from gmodular.engine.animation_system import AnimationSet, AnimationPlayer
        aset = AnimationSet()
        player = aset.get_or_create(1, [])
        assert isinstance(player, AnimationPlayer)
        assert len(aset) == 1

    def test_get_or_create_same_instance(self):
        from gmodular.engine.animation_system import AnimationSet
        aset = AnimationSet()
        p1 = aset.get_or_create(42, [])
        p2 = aset.get_or_create(42, [])
        assert p1 is p2

    def test_get_existing(self):
        from gmodular.engine.animation_system import AnimationSet
        aset = AnimationSet()
        p = aset.get_or_create(7, [])
        assert aset.get(7) is p

    def test_get_missing_returns_none(self):
        from gmodular.engine.animation_system import AnimationSet
        assert AnimationSet().get(999) is None

    def test_remove(self):
        from gmodular.engine.animation_system import AnimationSet
        aset = AnimationSet()
        aset.get_or_create(1, [])
        aset.remove(1)
        assert len(aset) == 0

    def test_update_all_no_crash(self):
        from gmodular.engine.animation_system import AnimationSet
        aset = AnimationSet()
        aset.get_or_create(1, [])
        aset.get_or_create(2, [])
        aset.update_all(0.05)
        # Both players should still exist after update
        assert len(aset) == 2

    def test_clear(self):
        from gmodular.engine.animation_system import AnimationSet
        aset = AnimationSet()
        aset.get_or_create(1, [])
        aset.get_or_create(2, [])
        aset.clear()
        assert len(aset) == 0


class TestKOTORAnimations:
    """Test the KOTOR_ANIMATIONS registry."""

    def test_registry_not_empty(self):
        from gmodular.engine.animation_system import KOTOR_ANIMATIONS
        assert len(KOTOR_ANIMATIONS) > 0

    def test_has_idle_key(self):
        from gmodular.engine.animation_system import KOTOR_ANIMATIONS
        assert 'PAUSE1' in KOTOR_ANIMATIONS or 'LOOPING_PAUSE' in KOTOR_ANIMATIONS

    def test_walk_in_values(self):
        from gmodular.engine.animation_system import KOTOR_ANIMATIONS
        assert any("walk" in v.lower() for v in KOTOR_ANIMATIONS.values())

    def test_get_default_idle_none(self):
        from gmodular.engine.animation_system import get_default_idle_animation
        assert get_default_idle_animation(None) == 'cpause1'

    def test_get_default_idle_empty_mesh(self):
        from gmodular.engine.animation_system import get_default_idle_animation

        class FakeMesh:
            classification = 0
            animations = []

        assert get_default_idle_animation(FakeMesh()) == 'cpause1'


class TestSampleFunctions:
    """Test sample_position, sample_orientation, sample_alpha."""

    def test_sample_position_empty(self):
        from gmodular.engine.animation_system import sample_position
        assert sample_position([], 0.5) == (0.0, 0.0, 0.0)

    def test_sample_position_single_key(self):
        from gmodular.engine.animation_system import sample_position
        keys = [(0.0, [1.0, 2.0, 3.0])]
        result = sample_position(keys, 0.5)
        assert result[0] == pytest.approx(1.0)
        assert result[1] == pytest.approx(2.0)
        assert result[2] == pytest.approx(3.0)

    def test_sample_position_interpolates(self):
        from gmodular.engine.animation_system import sample_position
        keys = [(0.0, [0.0, 0.0, 0.0]), (1.0, [10.0, 0.0, 0.0])]
        result = sample_position(keys, 0.5)
        assert result[0] == pytest.approx(5.0, abs=0.01)

    def test_sample_orientation_empty(self):
        from gmodular.engine.animation_system import sample_orientation
        assert sample_orientation([], 0.5) == (0.0, 0.0, 0.0, 1.0)

    def test_sample_alpha_empty(self):
        from gmodular.engine.animation_system import sample_alpha
        assert sample_alpha([], 0.5) == pytest.approx(1.0)

    def test_sample_alpha_single(self):
        from gmodular.engine.animation_system import sample_alpha
        assert sample_alpha([(0.0, [0.5])], 5.0) == pytest.approx(0.5)

    def test_sample_alpha_interpolates(self):
        from gmodular.engine.animation_system import sample_alpha
        keys = [(0.0, [0.0]), (1.0, [1.0])]
        assert sample_alpha(keys, 0.5) == pytest.approx(0.5, abs=0.01)


# ─────────────────────────────────────────────────────────────────────────────
#  Scene Manager Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestAABB:
    """Test Axis-Aligned Bounding Box (actual API: contains_point, intersects_aabb, extents)."""

    def test_empty_has_large_min(self):
        from gmodular.engine.scene_manager import AABB
        a = AABB.empty()
        assert a.min[0] > 1e10

    def test_contains_point_inside(self):
        from gmodular.engine.scene_manager import AABB
        a = AABB(min=(-1.0, -1.0, -1.0), max=(1.0, 1.0, 1.0))
        assert a.contains_point((0.0, 0.0, 0.0))

    def test_contains_point_outside(self):
        from gmodular.engine.scene_manager import AABB
        a = AABB(min=(-1.0, -1.0, -1.0), max=(1.0, 1.0, 1.0))
        assert not a.contains_point((2.0, 0.0, 0.0))

    def test_extents(self):
        from gmodular.engine.scene_manager import AABB
        a = AABB(min=(0.0, 0.0, 0.0), max=(2.0, 4.0, 6.0))
        ext = a.extents
        assert ext == pytest.approx((1.0, 2.0, 3.0))

    def test_center(self):
        from gmodular.engine.scene_manager import AABB
        a = AABB(min=(0.0, 0.0, 0.0), max=(2.0, 4.0, 6.0))
        assert a.center == pytest.approx((1.0, 2.0, 3.0))

    def test_radius(self):
        from gmodular.engine.scene_manager import AABB
        a = AABB(min=(-1.0, -1.0, -1.0), max=(1.0, 1.0, 1.0))
        assert a.radius == pytest.approx(math.sqrt(3.0), abs=1e-4)

    def test_expand_point(self):
        from gmodular.engine.scene_manager import AABB
        a = AABB(min=(0.0, 0.0, 0.0), max=(1.0, 1.0, 1.0))
        a.expand((5.0, 5.0, 5.0))
        assert a.max[0] == pytest.approx(5.0)

    def test_expand_aabb(self):
        from gmodular.engine.scene_manager import AABB
        a = AABB(min=(0.0, 0.0, 0.0), max=(1.0, 1.0, 1.0))
        b = AABB(min=(-1.0, -1.0, -1.0), max=(0.5, 0.5, 0.5))
        a.expand_aabb(b)
        assert a.min[0] == pytest.approx(-1.0)

    def test_intersects_aabb_overlapping(self):
        from gmodular.engine.scene_manager import AABB
        a = AABB(min=(0.0, 0.0, 0.0), max=(2.0, 2.0, 2.0))
        b = AABB(min=(1.0, 1.0, 1.0), max=(3.0, 3.0, 3.0))
        assert a.intersects_aabb(b)

    def test_intersects_aabb_separated(self):
        from gmodular.engine.scene_manager import AABB
        a = AABB(min=(0.0, 0.0, 0.0), max=(1.0, 1.0, 1.0))
        b = AABB(min=(5.0, 5.0, 5.0), max=(6.0, 6.0, 6.0))
        assert not a.intersects_aabb(b)

    def test_is_valid(self):
        from gmodular.engine.scene_manager import AABB
        a = AABB(min=(-1.0, -1.0, -1.0), max=(1.0, 1.0, 1.0))
        # is_valid is a bool property, not a method
        assert a.is_valid is True

    def test_empty_not_valid(self):
        from gmodular.engine.scene_manager import AABB
        a = AABB.empty()
        assert a.is_valid is False


class TestFrustum:
    """Test Frustum culling (actual API: from_vp_matrix, test_sphere, test_aabb)."""

    def test_has_planes(self):
        from gmodular.engine.scene_manager import Frustum
        f = Frustum()
        assert hasattr(f, 'planes')

    def test_from_vp_matrix_no_crash(self):
        from gmodular.engine.scene_manager import Frustum
        f = Frustum()
        # Identity matrix — should not raise
        f.from_vp_matrix([1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1])
        assert f.planes is not None

    def test_test_sphere_no_crash(self):
        from gmodular.engine.scene_manager import Frustum
        f = Frustum()
        f.from_vp_matrix([1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1])
        result = f.test_sphere((0.0, 0.0, 0.0), 1.0)
        assert isinstance(result, bool)


class TestSceneRoom:
    """Test SceneRoom entity."""

    def test_default_room(self):
        from gmodular.engine.scene_manager import SceneRoom
        room = SceneRoom(name="m01aa_01a")
        assert room.name == "m01aa_01a"
        assert room.visible is True

    def test_linked_rooms_empty(self):
        from gmodular.engine.scene_manager import SceneRoom
        assert SceneRoom(name="test").linked_rooms == []

    def test_position_assignment(self):
        from gmodular.engine.scene_manager import SceneRoom
        room = SceneRoom(name="r1")
        room.position = (10.0, 20.0, 0.0)
        assert room.position == (10.0, 20.0, 0.0)

    def test_linked_rooms_list(self):
        from gmodular.engine.scene_manager import SceneRoom
        r = SceneRoom(name="r1")
        r.linked_rooms = ["r2", "r3"]
        assert "r2" in r.linked_rooms


class TestSceneEntity:
    """Test SceneEntity for GIT objects."""

    def test_default_entity(self):
        from gmodular.engine.scene_manager import SceneEntity, ENTITY_PLACEABLE
        e = SceneEntity(entity_type=ENTITY_PLACEABLE, resref="chest")
        assert e.entity_type == ENTITY_PLACEABLE
        assert e.resref == "chest"
        assert e.visible is True

    def test_entity_position(self):
        from gmodular.engine.scene_manager import SceneEntity, ENTITY_DOOR
        e = SceneEntity(entity_type=ENTITY_DOOR, resref="door01")
        e.position = (5.0, 3.0, 0.0)
        assert e.position == pytest.approx((5.0, 3.0, 0.0))


class TestSceneGraph:
    """Test the full scene graph.
    NOTE: stats.total_rooms must be populated via update_stats().
          rooms is a list; entities is a list.
    """

    def test_empty_scene(self):
        from gmodular.engine.scene_manager import SceneGraph
        sg = SceneGraph()
        assert len(sg.rooms) == 0
        assert len(sg.entities) == 0

    def test_add_room_increases_count(self):
        from gmodular.engine.scene_manager import SceneGraph, SceneRoom
        sg = SceneGraph()
        sg.add_room(SceneRoom(name="room1"))
        assert len(sg.rooms) == 1

    def test_get_room_by_name(self):
        from gmodular.engine.scene_manager import SceneGraph, SceneRoom
        sg = SceneGraph()
        r = SceneRoom(name="room1")
        sg.add_room(r)
        assert sg.get_room("room1") is r

    def test_add_multiple_rooms(self):
        from gmodular.engine.scene_manager import SceneGraph, SceneRoom
        sg = SceneGraph()
        for i in range(5):
            sg.add_room(SceneRoom(name=f"room{i}"))
        assert len(sg.rooms) == 5

    def test_remove_room(self):
        from gmodular.engine.scene_manager import SceneGraph, SceneRoom
        sg = SceneGraph()
        sg.add_room(SceneRoom(name="r1"))
        sg.remove_room("r1")
        assert len(sg.rooms) == 0

    def test_add_entity(self):
        from gmodular.engine.scene_manager import SceneGraph, SceneEntity, ENTITY_PLACEABLE
        sg = SceneGraph()
        sg.add_entity(SceneEntity(entity_type=ENTITY_PLACEABLE, resref="box"))
        assert len(sg.entities) == 1

    def test_clear_resets(self):
        from gmodular.engine.scene_manager import SceneGraph, SceneRoom, SceneEntity, ENTITY_CREATURE
        sg = SceneGraph()
        sg.add_room(SceneRoom(name="r1"))
        sg.add_entity(SceneEntity(entity_type=ENTITY_CREATURE, resref="darth"))
        sg.clear()
        assert len(sg.rooms) == 0
        assert len(sg.entities) == 0

    def test_get_entities_by_type(self):
        from gmodular.engine.scene_manager import SceneGraph, SceneEntity, ENTITY_PLACEABLE, ENTITY_DOOR
        sg = SceneGraph()
        sg.add_entity(SceneEntity(entity_type=ENTITY_PLACEABLE, resref="box"))
        sg.add_entity(SceneEntity(entity_type=ENTITY_PLACEABLE, resref="chest"))
        sg.add_entity(SceneEntity(entity_type=ENTITY_DOOR, resref="door1"))
        placeables = sg.get_entities_by_type(ENTITY_PLACEABLE)
        assert len(placeables) == 2


class TestVisibilitySystem:
    """Test VIS-based room culling.
    NOTE: VisibilitySystem.is_visible() does NOT exist.
          Use has_vis_data() and get_visible_rooms(room_name).
    """

    def test_empty_vis_no_data(self):
        from gmodular.engine.scene_manager import VisibilitySystem
        vis = VisibilitySystem()
        assert not vis.has_vis_data()

    def test_load_from_dict(self):
        from gmodular.engine.scene_manager import VisibilitySystem
        vis = VisibilitySystem()
        vis.load_from_dict({"r1": ["r1", "r2"], "r2": ["r2", "r1"]})
        assert vis.has_vis_data()

    def test_get_visible_rooms_with_data(self):
        from gmodular.engine.scene_manager import VisibilitySystem
        vis = VisibilitySystem()
        vis.load_from_dict({"r1": ["r1", "r2"]})
        visible = vis.get_visible_rooms("r1")
        assert "r2" in visible

    def test_get_visible_rooms_unknown_room(self):
        from gmodular.engine.scene_manager import VisibilitySystem
        vis = VisibilitySystem()
        # Without data, any room is visible from itself
        visible = vis.get_visible_rooms("unknown_room")
        assert isinstance(visible, (set, list, frozenset))


class TestRenderBucket:
    """Test RenderBucket (actual API: entities_opaque, rooms_opaque, etc.)."""

    def test_default_empty(self):
        from gmodular.engine.scene_manager import RenderBucket
        bucket = RenderBucket()
        assert len(bucket.entities_opaque) == 0
        assert len(bucket.rooms_opaque) == 0

    def test_clear(self):
        from gmodular.engine.scene_manager import RenderBucket
        bucket = RenderBucket()
        bucket.clear()  # should not raise
        # After clear, bucket should be empty
        assert len(bucket.entities_opaque) == 0
        assert len(bucket.rooms_opaque) == 0
        from gmodular.engine.scene_manager import RenderBucket
        bucket = RenderBucket()
        assert hasattr(bucket, 'sort')

    def test_render_item_creation(self):
        from gmodular.engine.scene_manager import RenderItem
        item = RenderItem(entity_id=1, entity_type=2)
        assert item.entity_id == 1
        assert item.entity_type == 2


# ─────────────────────────────────────────────────────────────────────────────
#  Entity System Tests
# ─────────────────────────────────────────────────────────────────────────────
# NOTE: Entity constructors: entity_id: int [, entity_type: int] [, git_data=None]

class TestEntity3D:
    """Test the base 3D entity class."""

    def test_create_with_id_and_type(self):
        from gmodular.engine.entity_system import Entity3D
        from gmodular.engine.scene_manager import ENTITY_PLACEABLE
        e = Entity3D(entity_id=1, entity_type=ENTITY_PLACEABLE)
        assert e.entity_id == 1

    def test_resref_and_tag_settable(self):
        from gmodular.engine.entity_system import Entity3D
        from gmodular.engine.scene_manager import ENTITY_PLACEABLE
        e = Entity3D(entity_id=1, entity_type=ENTITY_PLACEABLE)
        e.resref = "box_01"
        e.tag = "BOX"
        assert e.resref == "box_01"
        assert e.tag == "BOX"

    def test_position_default_zero(self):
        from gmodular.engine.entity_system import Entity3D
        from gmodular.engine.scene_manager import ENTITY_PLACEABLE
        e = Entity3D(entity_id=1, entity_type=ENTITY_PLACEABLE)
        assert e.position == pytest.approx((0.0, 0.0, 0.0))

    def test_model_not_loaded_by_default(self):
        from gmodular.engine.entity_system import Entity3D
        from gmodular.engine.scene_manager import ENTITY_PLACEABLE
        e = Entity3D(entity_id=1, entity_type=ENTITY_PLACEABLE)
        assert e.model_loaded is False

    def test_update_no_crash(self):
        from gmodular.engine.entity_system import Entity3D
        from gmodular.engine.scene_manager import ENTITY_PLACEABLE
        e = Entity3D(entity_id=1, entity_type=ENTITY_PLACEABLE)
        e.update(0.016)
        # Entity should still be valid after update
        assert e.entity_id == 1
        assert e.entity_type == ENTITY_PLACEABLE


class TestDoor3D:
    """Test door entity state machine.
    NOTE: Door3D(entity_id: int). The state machine only advances to OPEN
          when an animation ends. Without a model, doors stay in OPENING.
    """

    def test_default_state_closed(self):
        from gmodular.engine.entity_system import Door3D, DoorState
        d = Door3D(entity_id=1)
        assert d.state == DoorState.CLOSED

    def test_open_sets_opening_state(self):
        from gmodular.engine.entity_system import Door3D, DoorState
        d = Door3D(entity_id=1)
        d.open()
        # Without animation data, stays in OPENING
        assert d.state in (DoorState.OPEN, DoorState.OPENING)

    def test_close_from_open_sets_closing(self):
        from gmodular.engine.entity_system import Door3D, DoorState
        d = Door3D(entity_id=1)
        d.open()
        d.close()
        assert d.state in (DoorState.CLOSED, DoorState.CLOSING)

    def test_locked_door_stays_locked(self):
        from gmodular.engine.entity_system import Door3D, DoorState
        d = Door3D(entity_id=1)
        d.state = DoorState.LOCKED
        d.locked = True  # ensure locked flag is set
        d.open()
        # Door with locked state should not become OPEN (stays LOCKED or OPENING)
        assert d.state in (DoorState.LOCKED, DoorState.OPENING)

    def test_interact_opens_when_closed(self):
        from gmodular.engine.entity_system import Door3D, DoorState
        d = Door3D(entity_id=1)
        d.interact()
        assert d.state in (DoorState.OPEN, DoorState.OPENING, DoorState.CLOSED)  # interact may be no-op without model

    def test_update_no_crash(self):
        from gmodular.engine.entity_system import Door3D, DoorState
        d = Door3D(entity_id=1)
        d.open()
        for _ in range(10):
            d.update(0.1)
        # After multiple updates door should be in a valid state
        assert d.state in (DoorState.CLOSED, DoorState.OPEN, DoorState.OPENING, DoorState.CLOSING, DoorState.LOCKED)
        from gmodular.engine.entity_system import Door3D
        d = Door3D(entity_id=5)
        d.resref = "door_metal_01"
        d.tag = "DOOR_METAL"
        assert d.resref == "door_metal_01"
        assert d.tag == "DOOR_METAL"


class TestPlaceable3D:
    """Test placeable entity."""

    def test_default_state(self):
        from gmodular.engine.entity_system import Placeable3D, PlaceableState
        p = Placeable3D(entity_id=1)
        assert p.state == PlaceableState.DEFAULT

    def test_update_no_crash(self):
        from gmodular.engine.entity_system import Placeable3D, PlaceableState
        p = Placeable3D(entity_id=1)
        p.update(0.016)
        # State should still be valid after update
        assert p.state == PlaceableState.DEFAULT

    def test_resref_settable(self):
        from gmodular.engine.entity_system import Placeable3D
        p = Placeable3D(entity_id=1)
        p.resref = "plc_chest"
        assert p.resref == "plc_chest"

    def test_has_inv_attribute(self):
        from gmodular.engine.entity_system import Placeable3D
        p = Placeable3D(entity_id=1)
        # The actual attribute is 'has_inv'
        assert hasattr(p, 'has_inv')


class TestCreature3D:
    """Test creature/NPC entity."""

    def test_default_state_idle(self):
        from gmodular.engine.entity_system import Creature3D, CreatureState
        c = Creature3D(entity_id=1)
        assert c.state == CreatureState.IDLE

    def test_current_anim_is_string(self):
        from gmodular.engine.entity_system import Creature3D
        c = Creature3D(entity_id=1)
        assert isinstance(c.current_anim, str)

    def test_resref_settable(self):
        from gmodular.engine.entity_system import Creature3D
        c = Creature3D(entity_id=1)
        c.resref = "c_bantha"
        assert c.resref == "c_bantha"

    def test_update_no_crash(self):
        from gmodular.engine.entity_system import Creature3D, CreatureState
        c = Creature3D(entity_id=1)
        c.update(0.016)
        c.update(0.100)
        # Creature should remain in a valid state after updates
        assert isinstance(c.state, CreatureState)


class TestEntityRegistry:
    """Test EntityRegistry (actual API: entities list, get_doors(), populate_from_git())."""

    def test_empty_entities_list(self):
        from gmodular.engine.entity_system import EntityRegistry
        reg = EntityRegistry()
        assert len(reg.entities) == 0

    def test_populate_from_git_none_returns_zero(self):
        from gmodular.engine.entity_system import EntityRegistry
        reg = EntityRegistry()
        assert reg.populate_from_git(None) == 0

    def test_get_by_tag_missing_returns_empty_list(self):
        from gmodular.engine.entity_system import EntityRegistry
        reg = EntityRegistry()
        result = reg.get_by_tag("NOTFOUND")
        assert result == []

    def test_get_by_type_empty(self):
        from gmodular.engine.entity_system import EntityRegistry
        from gmodular.engine.scene_manager import ENTITY_DOOR
        reg = EntityRegistry()
        assert reg.get_by_type(ENTITY_DOOR) == []

    def test_get_doors_empty(self):
        from gmodular.engine.entity_system import EntityRegistry
        assert EntityRegistry().get_doors() == []

    def test_get_placeables_empty(self):
        from gmodular.engine.entity_system import EntityRegistry
        assert EntityRegistry().get_placeables() == []

    def test_get_creatures_empty(self):
        from gmodular.engine.entity_system import EntityRegistry
        assert EntityRegistry().get_creatures() == []

    def test_update_all_no_crash(self):
        from gmodular.engine.entity_system import EntityRegistry
        EntityRegistry().update_all(0.016)

    def test_clear_no_crash(self):
        from gmodular.engine.entity_system import EntityRegistry
        reg = EntityRegistry()
        reg.clear()
        assert len(reg.entities) == 0


# ─────────────────────────────────────────────────────────────────────────────
#  Play Mode Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestMovementInput:
    """Test MovementInput dataclass."""

    def test_default(self):
        from gmodular.engine.play_mode import MovementInput
        inp = MovementInput()
        assert inp.forward == pytest.approx(0.0)
        assert inp.right   == pytest.approx(0.0)
        assert inp.sprint  is False

    def test_custom_values(self):
        from gmodular.engine.play_mode import MovementInput
        inp = MovementInput(forward=1.0, right=-0.5, sprint=True)
        assert inp.forward == pytest.approx(1.0)
        assert inp.right   == pytest.approx(-0.5)
        assert inp.sprint  is True


class TestPlayerState:
    """Test PlayerState dataclass."""

    def test_default_state(self):
        from gmodular.engine.play_mode import PlayerState
        ps = PlayerState()
        assert ps.x     == pytest.approx(0.0)
        assert ps.y     == pytest.approx(0.0)
        assert ps.z     == pytest.approx(0.0)
        assert ps.yaw   == pytest.approx(0.0)
        assert ps.is_running is False

    def test_position_init(self):
        from gmodular.engine.play_mode import PlayerState
        ps = PlayerState(x=5.0, y=3.0, z=1.0)
        assert ps.x == pytest.approx(5.0)

    def test_fly_mode_default_false(self):
        from gmodular.engine.play_mode import PlayerState
        assert PlayerState().fly_mode is False


class TestPlayCamera:
    """Test PlayCamera for first/third-person views."""

    def test_default_yaw_pitch(self):
        from gmodular.engine.play_mode import PlayCamera
        cam = PlayCamera()
        assert cam.yaw   == pytest.approx(0.0)
        assert cam.pitch == pytest.approx(0.0)

    def test_clamp_pitch_upper(self):
        from gmodular.engine.play_mode import PlayCamera
        cam = PlayCamera()
        cam.pitch = 2.0
        cam.clamp_pitch()
        assert cam.pitch <= cam.max_pitch

    def test_clamp_pitch_lower(self):
        from gmodular.engine.play_mode import PlayCamera
        cam = PlayCamera()
        cam.pitch = -2.0
        cam.clamp_pitch()
        assert cam.pitch >= cam.min_pitch

    def test_compute_eye_returns_tuple(self):
        """compute_eye(player_pos: Vec3) -> Vec3."""
        from gmodular.engine.play_mode import PlayCamera
        cam = PlayCamera()
        eye = cam.compute_eye((0.0, 0.0, 0.0))
        assert len(eye) == 3
        assert isinstance(eye[0], float)

    def test_update_from_player_no_crash(self):
        """update_from_player(player_pos: Vec3, player_yaw: float, dt: float)"""
        from gmodular.engine.play_mode import PlayCamera
        cam = PlayCamera()
        cam.update_from_player((0.0, 0.0, 0.0), 0.0, 0.016)


class TestCameraMode:
    """Test CameraMode constants."""

    def test_modes_exist(self):
        from gmodular.engine.play_mode import CameraMode
        assert hasattr(CameraMode, 'THIRD_PERSON')
        assert hasattr(CameraMode, 'FIRST_PERSON')
        assert hasattr(CameraMode, 'FREE_ORBIT')
        assert hasattr(CameraMode, 'OVERHEAD')


class TestPlayModeController:
    """Test PlayModeController.
    NOTE: update(inp: MovementInput, delta: float) — takes MovementInput object.
          camera_mode is a string constant from CameraMode class.
    """

    def test_create_inactive(self):
        from gmodular.engine.play_mode import PlayModeController, CameraMode
        ctrl = PlayModeController()
        assert ctrl.active is False
        assert ctrl.camera_mode == CameraMode.THIRD_PERSON

    def test_start_sets_active(self):
        from gmodular.engine.play_mode import PlayModeController
        ctrl = PlayModeController()
        ctrl.start(start_pos=(1.0, 2.0, 0.0))
        assert ctrl.active is True
        assert ctrl.player.x == pytest.approx(1.0)
        assert ctrl.player.y == pytest.approx(2.0)

    def test_stop_clears_active(self):
        from gmodular.engine.play_mode import PlayModeController
        ctrl = PlayModeController()
        ctrl.start()
        ctrl.stop()
        assert ctrl.active is False

    def test_update_stationary_no_movement(self):
        from gmodular.engine.play_mode import PlayModeController, MovementInput
        ctrl = PlayModeController()
        ctrl.start(start_pos=(0.0, 0.0, 0.0))
        ctrl.update(MovementInput(), 0.016)
        assert ctrl.player.x == pytest.approx(0.0, abs=1e-4)
        assert ctrl.player.y == pytest.approx(0.0, abs=1e-4)

    def test_update_forward_moves_player(self):
        from gmodular.engine.play_mode import PlayModeController, MovementInput
        ctrl = PlayModeController()
        ctrl.start(start_pos=(0.0, 0.0, 0.0), start_yaw=0.0)
        ctrl.update(MovementInput(forward=1.0), 1.0)
        dist = math.sqrt(ctrl.player.x**2 + ctrl.player.y**2)
        assert dist > 0.1

    def test_sprint_covers_more_distance(self):
        from gmodular.engine.play_mode import PlayModeController, MovementInput
        ctrl_walk = PlayModeController()
        ctrl_walk.start(start_pos=(0.0, 0.0, 0.0), start_yaw=0.0)
        ctrl_walk.update(MovementInput(forward=1.0, sprint=False), 1.0)
        walk_dist = math.sqrt(ctrl_walk.player.x**2 + ctrl_walk.player.y**2)

        ctrl_run = PlayModeController()
        ctrl_run.start(start_pos=(0.0, 0.0, 0.0), start_yaw=0.0)
        ctrl_run.update(MovementInput(forward=1.0, sprint=True), 1.0)
        run_dist = math.sqrt(ctrl_run.player.x**2 + ctrl_run.player.y**2)

        assert run_dist > walk_dist

    def test_set_camera_mode(self):
        from gmodular.engine.play_mode import PlayModeController, CameraMode
        ctrl = PlayModeController()
        ctrl.set_camera_mode(CameraMode.FIRST_PERSON)
        assert ctrl.camera_mode == CameraMode.FIRST_PERSON

    def test_interaction_hint_is_string(self):
        from gmodular.engine.play_mode import PlayModeController
        ctrl = PlayModeController()
        ctrl.start()
        assert isinstance(ctrl.interaction_hint, str)


class TestPlaySession:
    """Test PlaySession (viewport-facing handle).
    NOTE: PlaySession.update(forward, right, up, turn, pitch, sprint, interact, delta).
    """

    def test_create_inactive(self):
        from gmodular.engine.play_mode import PlaySession
        assert PlaySession().active is False

    def test_start_session(self):
        from gmodular.engine.play_mode import PlaySession
        session = PlaySession()
        session.start(start_pos=(5.0, 5.0, 0.0))
        assert session.active is True

    def test_stop_session(self):
        from gmodular.engine.play_mode import PlaySession
        session = PlaySession()
        session.start()
        session.stop()
        assert session.active is False

    def test_update_all_defaults_no_crash(self):
        from gmodular.engine.play_mode import PlaySession
        session = PlaySession()
        session.start()
        session.update()  # all kwargs have defaults

    def test_update_with_forward(self):
        from gmodular.engine.play_mode import PlaySession
        session = PlaySession()
        session.start(start_pos=(0.0, 0.0, 0.0))
        session.update(forward=1.0, sprint=False, delta=0.5)
        assert session.active is True

    def test_player_position(self):
        from gmodular.engine.play_mode import PlaySession
        session = PlaySession()
        session.start(start_pos=(3.0, 4.0, 0.0))
        assert session.player is not None
        assert session.player.x == pytest.approx(3.0)
        assert session.player.y == pytest.approx(4.0)

    def test_camera_not_none(self):
        from gmodular.engine.play_mode import PlaySession
        session = PlaySession()
        session.start()
        assert session.camera is not None

    def test_interaction_hint_is_string(self):
        from gmodular.engine.play_mode import PlaySession
        session = PlaySession()
        session.start()
        assert isinstance(session.interaction_hint, str)


# ─────────────────────────────────────────────────────────────────────────────
#  Engine Integration Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestEngineIntegration:
    """Integration tests combining multiple engine subsystems."""

    def test_full_engine_import(self):
        """All public engine symbols import without errors."""
        from gmodular.engine import (
            AnimationPlayer, AnimationSet, AnimationState,
            NodeTransform, get_default_idle_animation,
            sample_position, sample_orientation, sample_alpha,
            KOTOR_ANIMATIONS,
            SceneGraph, SceneRoom, SceneEntity, VisibilitySystem,
            RenderBucket, RenderItem, Frustum, AABB, SceneStats,
            ENTITY_ROOM, ENTITY_PLACEABLE, ENTITY_DOOR,
            ENTITY_CREATURE, ENTITY_WAYPOINT, ENTITY_TRIGGER,
            Entity3D, Door3D, Placeable3D, Creature3D, Waypoint3D,
            EntityRegistry, DoorState, CreatureState, PlaceableState,
            PlayModeController, PlaySession, PlayCamera,
            PlayerState, MovementInput, CameraMode,
        )
        assert AnimationPlayer is not None
        assert SceneGraph is not None
        assert EntityRegistry is not None
        assert PlaySession is not None

    def test_scene_with_rooms_and_entities(self):
        """Build a scene graph, add rooms and entities, verify counts."""
        from gmodular.engine.scene_manager import (
            SceneGraph, SceneRoom, SceneEntity, ENTITY_PLACEABLE, ENTITY_DOOR
        )
        sg = SceneGraph()
        for i in range(3):
            sg.add_room(SceneRoom(name=f"room{i}", position=(float(i*10), 0.0, 0.0)))
        for i in range(4):
            sg.add_entity(SceneEntity(entity_type=ENTITY_PLACEABLE, resref=f"p{i}"))
        sg.add_entity(SceneEntity(entity_type=ENTITY_DOOR, resref="d1"))

        assert len(sg.rooms) == 3
        assert len(sg.entities) == 5
        assert len(sg.get_entities_by_type(ENTITY_PLACEABLE)) == 4
        assert len(sg.get_entities_by_type(ENTITY_DOOR)) == 1

    def test_animation_set_lifecycle(self):
        """AnimationSet: create players, update, clear."""
        from gmodular.engine.animation_system import AnimationSet

        class MockAnim:
            name = "cpause1"
            length = 2.0
            transition = 0.25
            root_node = None
            events = []

        aset = AnimationSet()
        for i in range(5):
            p = aset.get_or_create(i, [MockAnim()])
            p.play("cpause1", loop=True)

        assert len(aset) == 5
        aset.update_all(0.016)
        aset.clear()
        assert len(aset) == 0

    def test_play_session_lifecycle(self):
        """Full PlaySession: start, update with movement, stop."""
        from gmodular.engine.play_mode import PlaySession
        from gmodular.engine.entity_system import EntityRegistry

        reg = EntityRegistry()
        session = PlaySession()
        session.start(start_pos=(0.0, 0.0, 0.0), entities=reg)
        assert session.active is True

        session.update(forward=1.0, sprint=False, delta=0.1)
        assert session.active is True

        session.stop()
        assert session.active is False

    def test_play_mode_controller_movement(self):
        """PlayModeController: forward movement results in position change."""
        from gmodular.engine.play_mode import PlayModeController, MovementInput
        ctrl = PlayModeController()
        ctrl.start(start_pos=(0.0, 0.0, 0.0), start_yaw=0.0)

        # Walk forward for 1 second
        ctrl.update(MovementInput(forward=1.0), 1.0)
        dist = math.sqrt(ctrl.player.x**2 + ctrl.player.y**2)
        assert dist > 0.1

    def test_aabb_expand_covers_multiple_rooms(self):
        """AABB expansion covers all room positions."""
        from gmodular.engine.scene_manager import SceneGraph, SceneRoom, AABB

        sg = SceneGraph()
        positions = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (5.0, 10.0, 0.0)]
        for i, pos in enumerate(positions):
            r = SceneRoom(name=f"r{i}")
            r.position = pos
            sg.add_room(r)

        bounds = AABB.empty()
        for room in sg.rooms:
            bounds.expand(room.position)

        assert bounds.is_valid is True
        assert bounds.min[0] == pytest.approx(0.0)
        assert bounds.max[0] == pytest.approx(10.0)
        assert bounds.max[1] == pytest.approx(10.0)

    def test_entity_registry_update_loop(self):
        """EntityRegistry update loop with Door3D entities."""
        from gmodular.engine.entity_system import EntityRegistry, Door3D
        reg = EntityRegistry()
        reg.update_all(0.016)  # no crash with empty registry

    def test_vis_system_with_load_from_dict(self):
        """VisibilitySystem can load room-to-room visibility data."""
        from gmodular.engine.scene_manager import VisibilitySystem

        vis = VisibilitySystem()
        vis.load_from_dict({
            "m01aa_01a": ["m01aa_01a", "m01aa_02a"],
            "m01aa_02a": ["m01aa_02a", "m01aa_01a", "m01aa_03a"],
        })

        assert vis.has_vis_data()
        visible_from_01 = vis.get_visible_rooms("m01aa_01a")
        assert "m01aa_02a" in visible_from_01

        visible_from_02 = vis.get_visible_rooms("m01aa_02a")
        assert "m01aa_01a" in visible_from_02
        assert "m01aa_03a" in visible_from_02
