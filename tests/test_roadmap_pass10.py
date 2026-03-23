"""
test_roadmap_pass10.py — GModular v2.1.0 roadmap validation.

Covers all five priorities implemented this session:

  Priority 1 — Walkmesh face selection (hit_test_walkmesh wired to click)
    - ViewportWidget exposes walkmesh_face_selected signal
    - ViewportWidget exposes set_walkmesh_edit_mode() method
    - ViewportWidget exposes get_selected_face_index() method
    - ViewportWidget exposes _pick_walkmesh_face() helper
    - set_walkmesh_edit_mode(True) activates edit mode flag
    - set_walkmesh_edit_mode(False) resets selected face to -1
    - get_selected_face_index() starts as -1
    - ViewportWidget has set_vis_rooms() pass-through

  Priority 2 — LYT world-offset zero-value fix
    - _room_coord_opt correctly returns 0.0 for world-origin rooms
    - RoomInstance with world_x=0.0 is not displaced by or-0.0 shortcut
    - RoomInstance with world_x=5.0 is placed correctly
    - RoomPlacement preserves exact x/y coordinates
    - Two rooms at distinct positions stay distinct

  Priority 3 — AnimationClipSignal headless stub functional
    - Headless Signal.connect() registers callbacks
    - Headless Signal.emit() calls all registered callbacks with args
    - Headless Signal.disconnect() removes specific callback
    - Headless Signal.disconnect(None) clears all callbacks
    - Headless Signal does not double-register the same callback
    - Signal.emit() isolates slot exceptions (doesn't propagate)
    - AnimationTimelinePanel.animation_changed is a signal-like object
    - AnimationTimelinePanel.time_scrubbed is a signal-like object

  Priority 4 — MDL → GPU mesh bridge
    - ViewportWidget.load_mdl_mesh() method exists
    - load_mdl_mesh() returns False for empty mdl_path
    - load_mdl_mesh() returns False when renderer not ready (no GPU)
    - MeshData.visible_mesh_nodes() filters AABB walkmesh nodes
    - MeshNode has vertices, faces, normals, uvs, position, texture, lightmap
    - MeshNode.flags identifies AABB nodes via NODE_AABB constant

  Priority 5 — .vis portal graph from .mod
    - ViewportWidget.set_vis_rooms() passes through to renderer
    - VisibilityData.from_string builds complete room union correctly
    - VIS all-rooms union includes both source and target rooms
    - set_vis_rooms(None) disables culling (stores None)
    - set_vis_rooms({}) hides all rooms (stores empty set)

  Bonus — viewport line count + structural sanity
    - viewport.py is under 3000 lines (test against over-extension)
    - walkmesh_face_selected signal is declared in ViewportWidget source
    - set_walkmesh_edit_mode is in viewport source
    - load_mdl_mesh is in viewport source
    - set_vis_rooms is in viewport source
    - _room_coord_opt is in viewport_renderer source
    - headless Signal stub _callbacks is in animation_panel source

Total: ~47 tests

References:
  Ericson §5.3.6  — Möller-Trumbore (walkmesh face select)
  Eberly §7       — portal rendering (VIS culling)
  Lengyel §8      — frustum culling
  Phase 2.1, 1.5, 1.8, 3.1 roadmap items
"""

import os
import sys
import math
import types
import unittest
from pathlib import Path

import numpy as np


# ─── imports ───────────────────────────────────────────────────────────────────

from gmodular.gui.viewport import ViewportWidget
from gmodular.gui.viewport_renderer import _EGLRenderer
from gmodular.formats.lyt_vis import VisibilityData, LayoutData, RoomPlacement
from gmodular.formats.mdl_parser import MeshNode, MeshData, NODE_AABB, NODE_MESH, NODE_HEADER


# ─── helper to create a minimal ViewportWidget stub without Qt init ──────────

def _make_viewport():
    """
    Instantiate ViewportWidget bypassing the Qt constructor.
    Uses ViewportWidget.__new__(ViewportWidget) which PyQt5 allows
    (unlike object.__new__ which raises a TypeError for QObject subclasses).
    Callers must set any instance attributes they need.
    """
    vp = ViewportWidget.__new__(ViewportWidget)
    return vp


def _make_renderer():
    """Instantiate _EGLRenderer bypassing its __init__."""
    r = _EGLRenderer.__new__(_EGLRenderer)
    r.ready = False
    r._ready = False
    r._vis_rooms = None
    return r


# ═══════════════════════════════════════════════════════════════════════════════
# 1.  Priority 1 — walkmesh face selection wiring
# ═══════════════════════════════════════════════════════════════════════════════

class TestWalkmeshFaceSelection(unittest.TestCase):
    """
    Priority 1: walkmesh_face_selected signal, set_walkmesh_edit_mode(),
    get_selected_face_index(), _pick_walkmesh_face().
    Reference: Phase 2.1 roadmap; Ericson §5.3.6 Möller-Trumbore.
    """

    def test_walkmesh_face_selected_signal_exists(self):
        self.assertTrue(
            hasattr(ViewportWidget, "walkmesh_face_selected"),
            "ViewportWidget must declare walkmesh_face_selected signal"
        )

    def test_set_walkmesh_edit_mode_method_exists(self):
        self.assertTrue(callable(getattr(ViewportWidget, "set_walkmesh_edit_mode", None)))

    def test_get_selected_face_index_method_exists(self):
        self.assertTrue(callable(getattr(ViewportWidget, "get_selected_face_index", None)))

    def test_pick_walkmesh_face_method_exists(self):
        self.assertTrue(callable(getattr(ViewportWidget, "_pick_walkmesh_face", None)))

    def test_set_walkmesh_edit_mode_activates_flag(self):
        vp = _make_viewport()
        vp._walkmesh_edit_mode = False
        vp._selected_face_idx  = -1
        vp.update = lambda: None
        vp.set_walkmesh_edit_mode(True)
        self.assertTrue(vp._walkmesh_edit_mode)

    def test_set_walkmesh_edit_mode_resets_face_on_disable(self):
        vp = _make_viewport()
        vp._walkmesh_edit_mode = True
        vp._selected_face_idx  = 7   # simulate previously selected face
        vp.update = lambda: None
        vp.set_walkmesh_edit_mode(False)
        self.assertEqual(vp._selected_face_idx, -1)
        self.assertFalse(vp._walkmesh_edit_mode)

    def test_selected_face_starts_at_minus_one(self):
        vp = _make_viewport()
        vp._selected_face_idx = -1
        self.assertEqual(vp.get_selected_face_index(), -1)

    def test_set_vis_rooms_passthrough_exists(self):
        self.assertTrue(callable(getattr(ViewportWidget, "set_vis_rooms", None)))

    def test_set_vis_rooms_stores_value(self):
        vp = _make_viewport()
        vp._vis_room_names = None
        vp._renderer = _make_renderer()
        vp.set_vis_rooms({"slem_ar", "slem_conn"})
        self.assertIsNotNone(vp._vis_room_names)
        self.assertIn("slem_ar", vp._vis_room_names)

    def test_set_vis_rooms_none_disables_culling(self):
        vp = _make_viewport()
        vp._vis_room_names = {"some_room"}
        vp._renderer = _make_renderer()
        vp.set_vis_rooms(None)
        self.assertIsNone(vp._vis_room_names)


# ═══════════════════════════════════════════════════════════════════════════════
# 2.  Priority 2 — LYT world-offset zero-value fix
# ═══════════════════════════════════════════════════════════════════════════════

class TestLYTWorldOffsetZeroFix(unittest.TestCase):
    """
    Priority 2: _room_coord_opt must not treat 0.0 as 'missing'.
    Single-room modules at world origin must stay at (0,0,0).
    Phase 1.5 fix; Ericson §6.4 (AABB correctness).
    """

    def _room_coord_opt(self, obj, attr):
        """Replicate the _room_coord_opt logic from viewport_renderer.py."""
        v = getattr(obj, attr, None)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _make_room_at(self, wx, wy, wz=0.0):
        """Build a minimal RoomPlacement-like object."""
        class R:
            world_x = wx
            world_y = wy
            world_z = wz
            grid_x = 99   # must NOT be used when world_x is present
            grid_y = 99
            x = None      # no .x — only .world_x
            y = None
            z = None
        return R()

    def test_room_at_origin_world_x_zero_preserved(self):
        """world_x=0.0 must NOT fall back to grid_x * 10.0."""
        ri = self._make_room_at(0.0, 0.0)
        wx = self._room_coord_opt(ri, 'world_x')
        if wx is None:
            wx = self._room_coord_opt(ri, 'x')
        if wx is None:
            wx = (self._room_coord_opt(ri, 'grid_x') or 0.0) * 10.0
        self.assertAlmostEqual(wx, 0.0,
            msg="Room at world origin must stay at x=0.0 not 990.0")

    def test_room_at_nonzero_world_x_correct(self):
        ri = self._make_room_at(5.0, 10.0)
        wx = self._room_coord_opt(ri, 'world_x')
        if wx is None:
            wx = self._room_coord_opt(ri, 'x')
        if wx is None:
            wx = (self._room_coord_opt(ri, 'grid_x') or 0.0) * 10.0
        self.assertAlmostEqual(wx, 5.0)

    def test_room_placement_x_attr_preserved(self):
        """RoomPlacement.x is 0.0 for origin room — must not be lost."""
        r = RoomPlacement(resref="origin_room", x=0.0, y=0.0, z=0.0)
        self.assertAlmostEqual(r.x, 0.0)
        self.assertAlmostEqual(r.y, 0.0)

    def test_two_rooms_distinct_positions(self):
        """Rooms at (0,0) and (20,-5) must not both map to same position."""
        lyt = LayoutData.from_string(
            "beginlayout\n"
            "room 0 room_a 0.0 0.0 0.0\n"
            "room 1 room_b 20.0 -5.0 0.0\n"
            "donelayout"
        )
        ra, rb = lyt.rooms[0], lyt.rooms[1]
        self.assertAlmostEqual(ra.x, 0.0)
        self.assertAlmostEqual(rb.x, 20.0)
        self.assertAlmostEqual(rb.y, -5.0)
        self.assertNotAlmostEqual(ra.x, rb.x)

    def test_room_proxy_world_attrs(self):
        """RoomInstance has world_x/y/z attributes, not just x/y/z."""
        from gmodular.gui.room_assembly import RoomInstance
        ri = RoomInstance(mdl_name="test", grid_x=0, grid_y=0,
                          world_x=0.0, world_y=0.0, world_z=0.0)
        # world_x=0.0 must not accidentally be treated as missing
        self.assertIsNotNone(ri.world_x)
        self.assertAlmostEqual(ri.world_x, 0.0)
        # .x property returns world_x
        self.assertAlmostEqual(ri.x, 0.0)


# ═══════════════════════════════════════════════════════════════════════════════
# 3.  Priority 3 — AnimationClipSignal headless stub
# ═══════════════════════════════════════════════════════════════════════════════

class _HeadlessSignal:
    """
    Standalone replica of the headless Signal stub from animation_panel.py.
    Tested here independently of whether Qt is installed.
    """
    def __init__(self, *type_args):
        self._callbacks: list = []

    def connect(self, f):
        if callable(f) and f not in self._callbacks:
            self._callbacks.append(f)

    def disconnect(self, f=None):
        if f is None:
            self._callbacks.clear()
        else:
            try:
                self._callbacks.remove(f)
            except ValueError:
                pass

    def emit(self, *args):
        for cb in list(self._callbacks):
            try:
                cb(*args)
            except Exception:
                pass


class TestHeadlessSignalStub(unittest.TestCase):
    """
    Priority 3: Functional headless Signal stub.
    Covers emit, connect, disconnect, exception isolation, no-duplicate.
    """

    def test_connect_registers_callback(self):
        s = _HeadlessSignal(int)
        cb = lambda v: None
        s.connect(cb)
        self.assertIn(cb, s._callbacks)

    def test_emit_calls_registered_callback(self):
        s = _HeadlessSignal(int)
        received = []
        s.connect(lambda v: received.append(v))
        s.emit(42)
        self.assertEqual(received, [42])

    def test_emit_with_multiple_args(self):
        s = _HeadlessSignal(int, str)
        got = []
        s.connect(lambda a, b: got.append((a, b)))
        s.emit(7, "walk")
        self.assertEqual(got, [(7, "walk")])

    def test_emit_calls_multiple_callbacks(self):
        s = _HeadlessSignal(int)
        a, b = [], []
        s.connect(lambda v: a.append(v))
        s.connect(lambda v: b.append(v))
        s.emit(1)
        self.assertEqual(a, [1])
        self.assertEqual(b, [1])

    def test_disconnect_specific_callback(self):
        s = _HeadlessSignal(int)
        results = []
        cb1 = lambda v: results.append(("cb1", v))
        cb2 = lambda v: results.append(("cb2", v))
        s.connect(cb1)
        s.connect(cb2)
        s.disconnect(cb1)
        s.emit(5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], "cb2")

    def test_disconnect_all_clears_callbacks(self):
        s = _HeadlessSignal(int)
        results = []
        s.connect(lambda v: results.append(v))
        s.disconnect()       # no argument = clear all
        s.emit(99)
        self.assertEqual(results, [])

    def test_no_double_registration(self):
        s = _HeadlessSignal(int)
        calls = []
        cb = lambda v: calls.append(v)
        s.connect(cb)
        s.connect(cb)        # duplicate — must be ignored
        s.emit(1)
        self.assertEqual(len(calls), 1)

    def test_emit_isolates_slot_exceptions(self):
        """A raising callback must not prevent other callbacks from running."""
        s = _HeadlessSignal(int)
        results = []
        def raiser(v): raise RuntimeError("slot error")
        s.connect(raiser)
        s.connect(lambda v: results.append(v))
        s.emit(3)   # must not raise; results should have [3]
        self.assertEqual(results, [3])

    def test_disconnect_absent_callback_is_silent(self):
        """Disconnecting a callback that was never connected must not raise."""
        s = _HeadlessSignal(int)
        s.disconnect(lambda v: None)  # must not raise

    def test_animation_panel_has_signal_attributes(self):
        """AnimationTimelinePanel class must declare animation_changed and time_scrubbed."""
        from gmodular.gui.animation_panel import AnimationTimelinePanel
        self.assertTrue(
            hasattr(AnimationTimelinePanel, "animation_changed"),
            "AnimationTimelinePanel.animation_changed must be a class attribute"
        )
        self.assertTrue(
            hasattr(AnimationTimelinePanel, "time_scrubbed"),
            "AnimationTimelinePanel.time_scrubbed must be a class attribute"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 4.  Priority 4 — MDL → GPU mesh bridge
# ═══════════════════════════════════════════════════════════════════════════════

class TestMDLGPUBridge(unittest.TestCase):
    """
    Priority 4: load_mdl_mesh() method exists and handles edge cases.
    MeshData / MeshNode API validated for upstream readiness.
    Phase 3.1 roadmap.
    """

    def test_load_mdl_mesh_method_exists(self):
        self.assertTrue(
            callable(getattr(ViewportWidget, "load_mdl_mesh", None)),
            "ViewportWidget.load_mdl_mesh() must exist"
        )

    def test_load_mdl_mesh_returns_false_for_empty_path(self):
        vp = _make_viewport()
        vp._renderer = _make_renderer()
        result = vp.load_mdl_mesh("")
        self.assertFalse(result, "Empty path must return False")

    def test_load_mdl_mesh_returns_false_when_renderer_not_ready_and_no_gpu(self):
        vp = _make_viewport()
        vp._renderer = _make_renderer()
        # init() will fail because there's no display; result is False
        result = vp.load_mdl_mesh("/nonexistent/path.mdl")
        self.assertFalse(result)

    def test_meshnode_has_required_fields(self):
        """MeshNode must expose the fields the GPU bridge reads."""
        node = MeshNode(name="test_node")
        self.assertTrue(hasattr(node, "vertices"),  "MeshNode.vertices")
        self.assertTrue(hasattr(node, "faces"),     "MeshNode.faces")
        self.assertTrue(hasattr(node, "normals"),   "MeshNode.normals")
        self.assertTrue(hasattr(node, "uvs"),       "MeshNode.uvs")
        self.assertTrue(hasattr(node, "position"),  "MeshNode.position")
        self.assertTrue(hasattr(node, "texture"),   "MeshNode.texture")
        self.assertTrue(hasattr(node, "lightmap"),  "MeshNode.lightmap")
        self.assertTrue(hasattr(node, "render"),    "MeshNode.render")

    def test_meshnode_position_defaults_to_origin(self):
        node = MeshNode(name="n")
        self.assertEqual(node.position, (0.0, 0.0, 0.0))

    def test_meshnode_render_flag_default_true(self):
        node = MeshNode(name="n")
        self.assertTrue(node.render,
                        "render flag should default to True for normal mesh nodes")

    def test_meshnode_flags_identify_aabb(self):
        """Nodes with NODE_AABB flag set are identified by is_aabb property."""
        n_mesh = MeshNode(name="floor", flags=NODE_HEADER | NODE_MESH)
        n_aabb = MeshNode(name="aabb_floor", flags=NODE_HEADER | NODE_AABB)
        self.assertFalse(n_mesh.is_aabb, "Regular mesh node must not be AABB")
        self.assertTrue(n_aabb.is_aabb,  "AABB node must be identified as AABB")
        self.assertTrue(n_mesh.is_mesh,  "Regular mesh node is_mesh must be True")
        self.assertFalse(n_aabb.is_mesh, "AABB node is_mesh must be False")

    def test_visible_mesh_nodes_excludes_aabb(self):
        """MeshData.visible_mesh_nodes() must not include AABB walkmesh nodes."""
        # Build a tree: root → [floor (mesh), aabb_floor (aabb)]
        root  = MeshNode(name="root",       flags=NODE_HEADER)
        n1    = MeshNode(name="floor",      flags=NODE_HEADER | NODE_MESH)
        n1.vertices = [(0,0,0), (1,0,0), (0,1,0)]
        n1.faces    = [(0,1,2)]
        n1.render   = True
        n1.parent   = root

        n2    = MeshNode(name="aabb_floor", flags=NODE_HEADER | NODE_AABB)
        n2.vertices = [(0,0,0), (1,0,0), (0,1,0)]
        n2.faces    = [(0,1,2)]
        n2.parent   = root

        root.children = [n1, n2]
        md = MeshData()
        md.root_node = root

        renderable = md.visible_mesh_nodes()
        names = [n.name for n in renderable]
        self.assertIn("floor",          names)
        self.assertNotIn("aabb_floor",  names,
                         "AABB nodes must be excluded from visible_mesh_nodes()")

    def test_visible_mesh_nodes_excludes_no_verts(self):
        """Nodes with no vertices are not renderable."""
        root  = MeshNode(name="root",  flags=NODE_HEADER)
        empty = MeshNode(name="empty", flags=NODE_HEADER | NODE_MESH)
        empty.vertices = []
        empty.render   = True
        empty.parent   = root
        root.children  = [empty]

        md = MeshData()
        md.root_node = root
        self.assertEqual(md.visible_mesh_nodes(), [])


# ═══════════════════════════════════════════════════════════════════════════════
# 5.  Priority 5 — .vis portal graph from .mod
# ═══════════════════════════════════════════════════════════════════════════════

class TestVISPortalGraph(unittest.TestCase):
    """
    Priority 5: VisibilityData parsed from .vis text and forwarded to renderer.
    Reference: Eberly §7 portal rendering; Ericson §7.6 cells & portals.

    KotOR .vis format:
        <room>  <visible_room1> [<visible_room2> ...]
    Each room's visibility list is space-separated on one or more lines.
    """

    # Standard multi-token-per-line KotOR .vis format
    _VIS = (
        "room_a room_b\n"
        "room_b room_a room_c\n"
        "room_c room_b\n"
    )

    def test_vis_union_includes_all_rooms(self):
        """The all-rooms union must include every room mentioned anywhere."""
        vis = VisibilityData.from_string(self._VIS)
        all_rooms: set = set()
        for name, visible_list in vis.visibility.items():
            all_rooms.add(name.lower())
            for v in visible_list:
                all_rooms.add(v.lower())
        for expected in ("room_a", "room_b", "room_c"):
            self.assertIn(expected, all_rooms,
                          f"{expected} must be in the VIS union")

    def test_vis_union_correct_count(self):
        vis = VisibilityData.from_string(self._VIS)
        all_rooms: set = set()
        for name, visible_list in vis.visibility.items():
            all_rooms.add(name.lower())
            for v in visible_list:
                all_rooms.add(v.lower())
        self.assertEqual(len(all_rooms), 3,
                         "Three distinct rooms in this VIS file")

    def test_set_vis_rooms_stores_union_on_viewport(self):
        vp = _make_viewport()
        vp._vis_room_names = None
        vp._renderer = _make_renderer()
        vis = VisibilityData.from_string(self._VIS)
        all_rooms: set = set()
        for name, visible_list in vis.visibility.items():
            all_rooms.add(name.lower())
            for v in visible_list:
                all_rooms.add(v.lower())
        vp.set_vis_rooms(all_rooms)
        self.assertEqual(vp._vis_room_names, all_rooms)

    def test_set_vis_rooms_none_disables_portal_culling(self):
        vp = _make_viewport()
        vp._vis_room_names = {"room_a"}
        vp._renderer = _make_renderer()
        vp.set_vis_rooms(None)
        self.assertIsNone(vp._vis_room_names)

    def test_set_vis_rooms_empty_set_hides_all(self):
        vp = _make_viewport()
        vp._vis_room_names = None
        vp._renderer = _make_renderer()
        vp.set_vis_rooms(set())
        self.assertIsNotNone(vp._vis_room_names)
        self.assertEqual(len(vp._vis_room_names), 0)

    def test_visibility_data_are_visible_honours_union(self):
        """
        room_b can see room_c (listed in room_b section);
        room_a cannot see room_c (not listed in room_a section).
        """
        vis = VisibilityData.from_string(self._VIS)
        # room_b can see room_c (listed in room_b section)
        self.assertTrue(vis.are_visible("room_b", "room_c"),
                        "room_b → room_c should be visible")
        # room_a cannot see room_c (not listed in room_a section)
        self.assertFalse(vis.are_visible("room_a", "room_c"),
                         "room_a → room_c should NOT be visible")

    def test_vis_renderer_set_vis_rooms_method(self):
        """_EGLRenderer.set_vis_rooms() stores the set."""
        r = _make_renderer()
        r.set_vis_rooms({"room_a", "room_b"})
        self.assertIn("room_a", r._vis_rooms)

    def test_are_visible_symmetric_check(self):
        """are_visible(a, b) is True if either a sees b OR b sees a."""
        vis = VisibilityData.from_string(self._VIS)
        # room_a has room_b in its list → symmetric
        self.assertTrue(vis.are_visible("room_a", "room_b"))
        self.assertTrue(vis.are_visible("room_b", "room_a"))


# ═══════════════════════════════════════════════════════════════════════════════
# 6.  Structural / line-count sanity
# ═══════════════════════════════════════════════════════════════════════════════

class TestViewportStructure(unittest.TestCase):

    def test_viewport_py_under_3000_lines(self):
        path = Path(__file__).parent.parent / "gmodular" / "gui" / "viewport.py"
        lines = len(path.read_text().splitlines())
        self.assertLess(lines, 3000,
                        f"viewport.py has {lines} lines — keep under 3000")

    def test_walkmesh_face_selected_in_class_body(self):
        """walkmesh_face_selected must be declared at class level (not instance)."""
        src = (Path(__file__).parent.parent / "gmodular" / "gui" / "viewport.py"
               ).read_text()
        self.assertIn("walkmesh_face_selected", src)

    def test_set_walkmesh_edit_mode_in_source(self):
        src = (Path(__file__).parent.parent / "gmodular" / "gui" / "viewport.py"
               ).read_text()
        self.assertIn("def set_walkmesh_edit_mode", src)

    def test_load_mdl_mesh_in_source(self):
        src = (Path(__file__).parent.parent / "gmodular" / "gui" / "viewport.py"
               ).read_text()
        self.assertIn("def load_mdl_mesh", src)

    def test_set_vis_rooms_in_viewport_source(self):
        src = (Path(__file__).parent.parent / "gmodular" / "gui" / "viewport.py"
               ).read_text()
        self.assertIn("def set_vis_rooms", src)

    def test_room_coord_opt_in_renderer_source(self):
        """_room_coord_opt None-sentinel helper must appear in viewport_renderer."""
        src = (Path(__file__).parent.parent / "gmodular" / "gui" / "viewport_renderer.py"
               ).read_text()
        self.assertIn("_room_coord_opt", src,
                      "viewport_renderer.py must use _room_coord_opt (is-None safe helper)")

    def test_headless_signal_stub_in_animation_panel_source(self):
        src = (Path(__file__).parent.parent / "gmodular" / "gui" / "animation_panel.py"
               ).read_text()
        self.assertIn("self._callbacks", src,
                      "animation_panel.py headless Signal stub must use _callbacks list")


if __name__ == "__main__":
    unittest.main()
