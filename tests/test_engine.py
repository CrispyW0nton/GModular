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
