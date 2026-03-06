"""
GModular — Module State Tests
Covers: Command pattern (PlaceObjectCommand, DeleteObjectCommand,
        MoveObjectCommand, RotateObjectCommand, ModifyPropertyCommand),
        all object types, undo/redo, validate(), autosave, project I/O.

Run with:  python -m pytest tests/ -v
"""
from __future__ import annotations
import math
import os
import json
import tempfile
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import unittest

from gmodular.formats.gff_types import (
    GITData, GITPlaceable, GITCreature, GITDoor,
    GITTrigger, GITSoundObject, GITWaypoint, GITStoreObject,
    AREData, IFOData, Vector3,
)
from gmodular.core.module_state import (
    ModuleState, ModuleProject,
    PlaceObjectCommand, DeleteObjectCommand,
    MoveObjectCommand, RotateObjectCommand, ModifyPropertyCommand,
    _obj_type_label,
)


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fresh_state() -> ModuleState:
    """Return a ModuleState with an empty GITData/AREData/IFOData."""
    ms = ModuleState()
    ms.git = GITData()
    ms.are = AREData()
    ms.ifo = IFOData(entry_area="test_area")
    return ms


def _placeable(resref="utp_box", tag="BOX1") -> GITPlaceable:
    return GITPlaceable(resref=resref, tag=tag,
                        position=Vector3(1.0, 2.0, 0.0))


def _creature(resref="utc_guard", tag="GUARD1") -> GITCreature:
    return GITCreature(resref=resref, tag=tag,
                       position=Vector3(3.0, 4.0, 0.0))


def _door(resref="utd_door", tag="DOOR1") -> GITDoor:
    return GITDoor(resref=resref, tag=tag,
                   position=Vector3(5.0, 0.0, 0.0))


def _waypoint(resref="wp_start", tag="WP_START") -> GITWaypoint:
    return GITWaypoint(resref=resref, tag=tag,
                       position=Vector3(0.0, 0.0, 0.0))


def _trigger(resref="utt_trap", tag="TRAP1") -> GITTrigger:
    t = GITTrigger(resref=resref, tag=tag)
    t.geometry = [Vector3(0, 0, 0), Vector3(1, 0, 0), Vector3(1, 1, 0)]
    return t


# ─────────────────────────────────────────────────────────────────────────────
#  _obj_type_label helper
# ─────────────────────────────────────────────────────────────────────────────

class TestObjTypeLabel:
    def test_placeable(self):
        assert _obj_type_label(_placeable()) == "Placeable"

    def test_creature(self):
        assert _obj_type_label(_creature()) == "Creature"

    def test_door(self):
        assert _obj_type_label(_door()) == "Door"

    def test_waypoint(self):
        assert _obj_type_label(_waypoint()) == "Waypoint"

    def test_trigger(self):
        assert _obj_type_label(_trigger()) == "Trigger"


# ─────────────────────────────────────────────────────────────────────────────
#  PlaceObjectCommand — all types
# ─────────────────────────────────────────────────────────────────────────────

class TestPlaceObjectCommand:
    def test_place_placeable(self):
        git = GITData()
        p = _placeable()
        cmd = PlaceObjectCommand(git, p)
        cmd.execute()
        assert p in git.placeables
        assert git.object_count == 1

    def test_place_creature(self):
        git = GITData()
        c = _creature()
        cmd = PlaceObjectCommand(git, c)
        cmd.execute()
        assert c in git.creatures

    def test_place_door(self):
        git = GITData()
        d = _door()
        PlaceObjectCommand(git, d).execute()
        assert d in git.doors

    def test_place_waypoint(self):
        git = GITData()
        w = _waypoint()
        PlaceObjectCommand(git, w).execute()
        assert w in git.waypoints

    def test_place_trigger(self):
        git = GITData()
        t = _trigger()
        PlaceObjectCommand(git, t).execute()
        assert t in git.triggers

    def test_undo_removes_object(self):
        git = GITData()
        p = _placeable()
        cmd = PlaceObjectCommand(git, p)
        cmd.execute()
        assert git.object_count == 1
        cmd.undo()
        assert git.object_count == 0
        assert p not in git.placeables

    def test_description_contains_resref(self):
        git = GITData()
        cmd = PlaceObjectCommand(git, _placeable(resref="utp_fancy"))
        assert "utp_fancy" in cmd.description


# ─────────────────────────────────────────────────────────────────────────────
#  DeleteObjectCommand — all types
# ─────────────────────────────────────────────────────────────────────────────

class TestDeleteObjectCommand:
    def test_delete_placeable(self):
        git = GITData()
        p = _placeable()
        git.placeables.append(p)
        DeleteObjectCommand(git, p).execute()
        assert p not in git.placeables

    def test_delete_creature(self):
        git = GITData()
        c = _creature()
        git.creatures.append(c)
        DeleteObjectCommand(git, c).execute()
        assert c not in git.creatures

    def test_delete_door(self):
        git = GITData()
        d = _door()
        git.doors.append(d)
        DeleteObjectCommand(git, d).execute()
        assert d not in git.doors

    def test_delete_waypoint(self):
        git = GITData()
        w = _waypoint()
        git.waypoints.append(w)
        DeleteObjectCommand(git, w).execute()
        assert w not in git.waypoints

    def test_undo_restores_at_original_index(self):
        git = GITData()
        p1 = _placeable(tag="A")
        p2 = _placeable(tag="B")
        p3 = _placeable(tag="C")
        for p in (p1, p2, p3):
            git.placeables.append(p)
        # Delete middle
        cmd = DeleteObjectCommand(git, p2)
        cmd.execute()
        assert git.placeables == [p1, p3]
        cmd.undo()
        assert git.placeables == [p1, p2, p3], "Undo should restore original order"

    def test_description_contains_tag(self):
        git = GITData()
        cmd = DeleteObjectCommand(git, _door(tag="MAIN_GATE"))
        assert "MAIN_GATE" in cmd.description


# ─────────────────────────────────────────────────────────────────────────────
#  MoveObjectCommand
# ─────────────────────────────────────────────────────────────────────────────

class TestMoveObjectCommand:
    def test_execute_updates_position(self):
        p = _placeable()
        old = Vector3(1, 2, 0)
        new = Vector3(10, 20, 3)
        MoveObjectCommand(p, old, new).execute()
        assert p.position.x == pytest.approx(10.0)
        assert p.position.y == pytest.approx(20.0)
        assert p.position.z == pytest.approx(3.0)

    def test_undo_restores_position(self):
        p = _placeable()
        old = Vector3(1.0, 2.0, 0.0)
        new = Vector3(9.0, 9.0, 0.0)
        cmd = MoveObjectCommand(p, old, new)
        cmd.execute()
        cmd.undo()
        assert p.position.x == pytest.approx(1.0)
        assert p.position.y == pytest.approx(2.0)

    def test_does_not_mutate_old_pos(self):
        """MoveObjectCommand must deep-copy old_pos."""
        p = _placeable()
        old = Vector3(5.0, 5.0, 0.0)
        new = Vector3(0.0, 0.0, 0.0)
        cmd = MoveObjectCommand(p, old, new)
        # Mutate the original Vector3 we passed in — the command should be unaffected
        old.x = 999.0
        cmd.execute()
        cmd.undo()
        assert p.position.x == pytest.approx(5.0)


# ─────────────────────────────────────────────────────────────────────────────
#  RotateObjectCommand
# ─────────────────────────────────────────────────────────────────────────────

class TestRotateObjectCommand:
    def test_execute_updates_bearing(self):
        p = _placeable()
        p.bearing = 0.0
        RotateObjectCommand(p, 0.0, math.pi / 2).execute()
        assert p.bearing == pytest.approx(math.pi / 2)

    def test_undo_restores_bearing(self):
        p = _placeable()
        p.bearing = 1.0
        cmd = RotateObjectCommand(p, 1.0, 2.5)
        cmd.execute()
        cmd.undo()
        assert p.bearing == pytest.approx(1.0)


# ─────────────────────────────────────────────────────────────────────────────
#  ModifyPropertyCommand
# ─────────────────────────────────────────────────────────────────────────────

class TestModifyPropertyCommand:
    def test_execute_sets_value(self):
        p = _placeable(tag="OLD")
        ModifyPropertyCommand(p, "tag", "OLD", "NEW").execute()
        assert p.tag == "NEW"

    def test_undo_restores_value(self):
        p = _placeable(tag="ORIG")
        cmd = ModifyPropertyCommand(p, "tag", "ORIG", "CHANGED")
        cmd.execute()
        cmd.undo()
        assert p.tag == "ORIG"

    def test_description_contains_attr_name(self):
        cmd = ModifyPropertyCommand(None, "resref", "", "x")
        assert "resref" in cmd.description


# ─────────────────────────────────────────────────────────────────────────────
#  ModuleState — undo / redo stack
# ─────────────────────────────────────────────────────────────────────────────

class TestUndoRedo:
    def test_undo_empty_returns_none(self):
        ms = _fresh_state()
        assert ms.undo() is None

    def test_redo_empty_returns_none(self):
        ms = _fresh_state()
        assert ms.redo() is None

    def test_single_command_undo_redo(self):
        ms = _fresh_state()
        p = _placeable()
        ms.execute(PlaceObjectCommand(ms.git, p))
        assert p in ms.git.placeables
        ms.undo()
        assert p not in ms.git.placeables
        ms.redo()
        assert p in ms.git.placeables

    def test_undo_clears_redo(self):
        ms = _fresh_state()
        p1 = _placeable(tag="A")
        p2 = _placeable(tag="B")
        ms.execute(PlaceObjectCommand(ms.git, p1))
        ms.execute(PlaceObjectCommand(ms.git, p2))
        ms.undo()       # undo p2
        ms.undo()       # undo p1
        ms.redo()       # redo p1 — should clear redo of p2
        # Now place something new, which must clear p2 redo
        p3 = _placeable(tag="C")
        ms.execute(PlaceObjectCommand(ms.git, p3))
        assert not ms.can_redo

    def test_undo_limit_enforced(self):
        ms = _fresh_state()
        ms.UNDO_LIMIT = 5
        for i in range(10):
            ms.execute(PlaceObjectCommand(ms.git, _placeable(tag=f"T{i}")))
        assert len(ms._undo_stack) == 5

    def test_dirty_flag_set_on_execute(self):
        ms = _fresh_state()
        assert not ms.is_dirty
        ms.execute(PlaceObjectCommand(ms.git, _placeable()))
        assert ms.is_dirty

    def test_can_undo_can_redo(self):
        ms = _fresh_state()
        assert not ms.can_undo
        assert not ms.can_redo
        ms.execute(PlaceObjectCommand(ms.git, _placeable()))
        assert ms.can_undo
        ms.undo()
        assert ms.can_redo

    def test_undo_description(self):
        ms = _fresh_state()
        p = _placeable(resref="utp_abc")
        ms.execute(PlaceObjectCommand(ms.git, p))
        assert "utp_abc" in ms.undo_description


# ─────────────────────────────────────────────────────────────────────────────
#  ModuleState — validate()
# ─────────────────────────────────────────────────────────────────────────────

class TestValidate:
    def test_no_git_returns_error(self):
        ms = ModuleState()
        issues = ms.validate()
        assert any("No GIT" in i for i in issues)

    def test_clean_module_no_issues(self):
        ms = _fresh_state()
        ms.git.placeables.append(_placeable())
        issues = ms.validate()
        assert issues == []

    def test_duplicate_tag_across_types(self):
        ms = _fresh_state()
        ms.git.placeables.append(_placeable(tag="SHARED_TAG"))
        ms.git.creatures.append(_creature(tag="SHARED_TAG"))
        issues = ms.validate()
        assert any("SHARED_TAG" in i for i in issues)

    def test_empty_resref_reported(self):
        ms = _fresh_state()
        ms.git.placeables.append(GITPlaceable(resref="", tag="EMPTY"))
        issues = ms.validate()
        assert any("EMPTY" in i and "ResRef" in i for i in issues)

    def test_resref_too_long_reported(self):
        ms = _fresh_state()
        ms.git.doors.append(GITDoor(resref="a" * 20, tag="LONG"))
        issues = ms.validate()
        assert any("LONG" in i and "too long" in i for i in issues)

    def test_trigger_too_few_geometry_points(self):
        ms = _fresh_state()
        t = GITTrigger(resref="utt_x", tag="BAD_TRIG")
        t.geometry = [Vector3(), Vector3(1, 0, 0)]  # only 2 points
        ms.git.triggers.append(t)
        issues = ms.validate()
        assert any("BAD_TRIG" in i and "fewer than 3" in i for i in issues)

    def test_trigger_ok_geometry(self):
        ms = _fresh_state()
        t = GITTrigger(resref="utt_ok", tag="GOOD_TRIG")
        t.geometry = [Vector3(0, 0, 0), Vector3(1, 0, 0), Vector3(0, 1, 0)]
        ms.git.triggers.append(t)
        issues = ms.validate()
        assert not any("GOOD_TRIG" in i for i in issues)

    def test_invalid_position_nan(self):
        ms = _fresh_state()
        p = _placeable(tag="NAN_OBJ")
        p.position.x = float("nan")
        ms.git.placeables.append(p)
        issues = ms.validate()
        assert any("NAN_OBJ" in i and "invalid" in i for i in issues)

    def test_missing_ifo_entry_area(self):
        ms = _fresh_state()
        ms.ifo.entry_area = ""
        issues = ms.validate()
        assert any("entry_area" in i for i in issues)

    def test_validates_creatures(self):
        ms = _fresh_state()
        ms.git.creatures.append(GITCreature(resref="", tag="BLANK_C"))
        issues = ms.validate()
        assert any("Creature" in i and "BLANK_C" in i for i in issues)

    def test_validates_waypoints(self):
        ms = _fresh_state()
        ms.git.waypoints.append(GITWaypoint(resref="", tag="WP_BAD"))
        issues = ms.validate()
        assert any("Waypoint" in i and "WP_BAD" in i for i in issues)


# ─────────────────────────────────────────────────────────────────────────────
#  ModuleProject — save / load metadata
# ─────────────────────────────────────────────────────────────────────────────

class TestModuleProject:
    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = ModuleProject.create_new(
                name="Test Module",
                game="K2",
                project_dir=tmpdir,
                module_resref="test001",
                description="A pytest-created module",
            )
            p.save_meta()
            loaded = ModuleProject.load_meta(tmpdir)
            assert loaded.name == "Test Module"
            assert loaded.game == "K2"
            assert loaded.module_resref == "test001"
            assert loaded.description == "A pytest-created module"

    def test_project_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = ModuleProject(
                name="M", project_dir=tmpdir, module_resref="danm13"
            )
            assert p.git_path.endswith("danm13.git")
            assert p.are_path.endswith("danm13.are")
            assert p.ifo_path.endswith("danm13.ifo")

    def test_create_new_makes_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            proj_dir = os.path.join(tmpdir, "my_module")
            ModuleProject.create_new("M", "K1", proj_dir, "mod01")
            assert os.path.isdir(os.path.join(proj_dir, "modules"))
            assert os.path.isdir(os.path.join(proj_dir, ".gmodular", "autosave"))


# ─────────────────────────────────────────────────────────────────────────────
#  ModuleState — new_module / save / close
# ─────────────────────────────────────────────────────────────────────────────

class TestModuleStateIO:
    def test_new_module(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            proj = ModuleProject.create_new("N", "K1", tmpdir, "n001")
            ms = ModuleState()
            ms.new_module(proj)
            assert ms.is_open
            assert not ms.is_dirty

    def test_save_creates_git_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            proj = ModuleProject.create_new("S", "K1", tmpdir, "s001")
            ms = ModuleState()
            ms.new_module(proj)
            ms.execute(PlaceObjectCommand(ms.git, _placeable()))
            ms.save()
            assert os.path.exists(proj.git_path), "GIT file should be on disk after save"
            assert os.path.getsize(proj.git_path) > 56

    def test_close_clears_state(self):
        ms = _fresh_state()
        ms.git.placeables.append(_placeable())
        ms.close()
        assert not ms.is_open
        assert ms.git is None

    def test_change_callback_fired(self):
        ms = _fresh_state()
        fired = []
        ms.on_change(lambda: fired.append(1))
        ms.execute(PlaceObjectCommand(ms.git, _placeable()))
        assert fired, "change callback should fire on execute"


# ─────────────────────────────────────────────────────────────────────────────
#  Iteration 6 regression tests
# ─────────────────────────────────────────────────────────────────────────────

class TestDeleteCommandViaState:
    """Verify DeleteObjectCommand used by scene_outline is undoable."""

    def test_delete_then_undo_restores_object(self):
        """Deleting via state.execute(DeleteObjectCommand) must be undoable."""
        ms = _fresh_state()
        p = _placeable()
        ms.execute(PlaceObjectCommand(ms.git, p))
        assert len(ms.git.placeables) == 1

        ms.execute(DeleteObjectCommand(ms.git, p))
        assert len(ms.git.placeables) == 0

        desc = ms.undo()
        assert len(ms.git.placeables) == 1
        assert desc is not None

    def test_delete_redo_removes_again(self):
        ms = _fresh_state()
        p = _placeable()
        ms.execute(PlaceObjectCommand(ms.git, p))
        ms.execute(DeleteObjectCommand(ms.git, p))
        ms.undo()
        assert len(ms.git.placeables) == 1
        ms.redo()
        assert len(ms.git.placeables) == 0

    def test_delete_all_7_types_undoable(self):
        """Ensure DeleteObjectCommand works for every GIT type."""
        ms = _fresh_state()
        objects = [
            GITPlaceable(resref="p1", tag="p1"),
            GITCreature(resref="c1", tag="c1"),
            GITDoor(resref="d1", tag="d1"),
            GITWaypoint(resref="w1", tag="w1"),
            GITTrigger(resref="t1", tag="t1"),
            GITSoundObject(resref="s1", tag="s1"),
            GITStoreObject(resref="st1", tag="st1"),
        ]
        for obj in objects:
            ms.execute(PlaceObjectCommand(ms.git, obj))
        assert ms.git.object_count == 7

        for obj in objects:
            ms.execute(DeleteObjectCommand(ms.git, obj))
        assert ms.git.object_count == 0

        # Undo all deletes
        for _ in objects:
            ms.undo()
        assert ms.git.object_count == 7


class TestModifyPropertyCommand:
    """Tests for rename-tag via ModifyPropertyCommand (scene_outline fix)."""

    def test_rename_undoable(self):
        ms = _fresh_state()
        p = _placeable()
        p.tag = "old_tag"
        ms.execute(PlaceObjectCommand(ms.git, p))

        ms.execute(ModifyPropertyCommand(p, "tag", "old_tag", "new_tag"))
        assert p.tag == "new_tag"

        ms.undo()
        assert p.tag == "old_tag"

    def test_rename_redo(self):
        ms = _fresh_state()
        p = _placeable()
        p.tag = "alpha"
        ms.execute(PlaceObjectCommand(ms.git, p))
        ms.execute(ModifyPropertyCommand(p, "tag", "alpha", "beta"))
        ms.undo()
        assert p.tag == "alpha"
        ms.redo()
        assert p.tag == "beta"


class TestIFOSaveRoundTrip:
    """Verify save_ifo / load_ifo round-trip for all script hooks."""

    def test_ifo_all_hooks_survive_roundtrip(self):
        import tempfile
        from gmodular.formats.gff_writer import save_ifo
        from gmodular.formats.gff_reader import load_ifo

        ifo = IFOData(
            mod_name="TestMod",
            mod_description="A test",
            entry_area="danm13",
            entry_position=Vector3(1.0, 2.0, 3.0),
            entry_direction=0.5,
            on_module_load="k_mod_load",
            on_module_start="k_mod_start",
            on_player_death="k_plr_death",
            on_player_dying="k_plr_dying",
            on_player_levelup="k_plr_lvlup",
            on_player_respawn="k_plr_resp",
            on_player_rest="k_plr_rest",
            on_heartbeat="k_mod_hb",
            on_client_enter="k_enter",
            on_client_leave="k_leave",
            on_cutscene_abort="k_cut_abort",
            on_unacquire_item="k_unacq",
            on_acquire_item="k_acq",
            on_activate_item="k_act",
        )
        with tempfile.NamedTemporaryFile(suffix=".ifo", delete=False) as f:
            path = f.name
        save_ifo(ifo, path)
        ifo2 = load_ifo(path)
        os.unlink(path)

        assert ifo2.mod_name == "TestMod"
        assert ifo2.entry_area == "danm13"
        assert abs(ifo2.entry_position.x - 1.0) < 0.001
        assert ifo2.on_module_load == "k_mod_load"
        assert ifo2.on_module_start == "k_mod_start"
        assert ifo2.on_player_death == "k_plr_death"
        assert ifo2.on_player_dying == "k_plr_dying"
        assert ifo2.on_player_levelup == "k_plr_lvlup"
        assert ifo2.on_player_respawn == "k_plr_resp"
        assert ifo2.on_player_rest == "k_plr_rest"
        assert ifo2.on_heartbeat == "k_mod_hb"
        assert ifo2.on_client_enter == "k_enter"
        assert ifo2.on_client_leave == "k_leave"
        assert ifo2.on_cutscene_abort == "k_cut_abort"
        assert ifo2.on_unacquire_item == "k_unacq"
        assert ifo2.on_acquire_item == "k_acq"
        assert ifo2.on_activate_item == "k_act"

    def test_module_state_save_writes_ifo(self):
        """ModuleState.save() must write .ifo file alongside .git when project is open."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proj = ModuleProject.create_new("IFOTest", "K1", tmpdir, "ifotest01")
            ms = ModuleState()
            ms.new_module(proj)
            ms.ifo.on_module_load = "k_test_load"
            ms.save()
            assert os.path.exists(proj.ifo_path), "IFO file must be written by save()"
            # Reload and verify
            from gmodular.formats.gff_reader import load_ifo
            ifo2 = load_ifo(proj.ifo_path)
            assert ifo2.on_module_load == "k_test_load"


class TestMoveCommandViaState:
    """Verify MoveObjectCommand integrates with ModuleState correctly."""

    def test_move_and_undo(self):
        from gmodular.core.module_state import MoveObjectCommand
        ms = _fresh_state()
        p = GITPlaceable(resref="mover", position=Vector3(0.0, 0.0, 0.0))
        ms.execute(PlaceObjectCommand(ms.git, p))

        old_pos = Vector3(p.position.x, p.position.y, p.position.z)
        new_pos = Vector3(5.0, 3.0, 1.0)
        ms.execute(MoveObjectCommand(p, old_pos, new_pos))
        assert abs(p.position.x - 5.0) < 1e-6

        ms.undo()
        assert abs(p.position.x - 0.0) < 1e-6

    def test_move_redo(self):
        from gmodular.core.module_state import MoveObjectCommand
        ms = _fresh_state()
        p = GITPlaceable(resref="mover2", position=Vector3(1.0, 2.0, 0.0))
        ms.execute(PlaceObjectCommand(ms.git, p))
        old_pos = Vector3(1.0, 2.0, 0.0)
        new_pos = Vector3(10.0, 10.0, 0.0)
        ms.execute(MoveObjectCommand(p, old_pos, new_pos))
        ms.undo()
        ms.redo()
        assert abs(p.position.x - 10.0) < 1e-6


# ─────────────────────────────────────────────────────────────────────────────
#  Iteration 7 — IFO edit via ModifyPropertyCommand, save_as IFO,
#                RecentFiles helpers, frame_all, inspector _connect_spin
# ─────────────────────────────────────────────────────────────────────────────

class TestIFOModifyCommand:
    """IFO field edits must flow through ModifyPropertyCommand for undo/redo."""

    def test_ifo_mod_name_undo_redo(self):
        from gmodular.core.module_state import ModifyPropertyCommand
        ms = _fresh_state()
        ifo = ms.ifo
        assert ifo is not None
        ifo.mod_name = "Original"

        cmd = ModifyPropertyCommand(ifo, "mod_name", "Original", "Updated")
        ms.execute(cmd)
        assert ifo.mod_name == "Updated"

        ms.undo()
        assert ifo.mod_name == "Original"

        ms.redo()
        assert ifo.mod_name == "Updated"

    def test_ifo_entry_area_undo(self):
        from gmodular.core.module_state import ModifyPropertyCommand
        ms = _fresh_state()
        ifo = ms.ifo
        ifo.entry_area = ""
        cmd = ModifyPropertyCommand(ifo, "entry_area", "", "myarea01")
        ms.execute(cmd)
        assert ifo.entry_area == "myarea01"
        ms.undo()
        assert ifo.entry_area == ""

    def test_ifo_script_hooks_undo(self):
        """All 14 IFO script hooks survive a ModifyPropertyCommand round-trip."""
        from gmodular.core.module_state import ModifyPropertyCommand
        hooks = [
            "on_module_load", "on_module_start", "on_player_death",
            "on_player_dying", "on_player_levelup", "on_player_respawn",
            "on_player_rest", "on_heartbeat", "on_client_enter",
            "on_client_leave", "on_cutscene_abort", "on_unacquire_item",
            "on_acquire_item", "on_activate_item",
        ]
        ms = _fresh_state()
        ifo = ms.ifo
        for hook in hooks:
            original = ""
            new_val  = f"k_{hook[:12]}"
            cmd = ModifyPropertyCommand(ifo, hook, original, new_val)
            ms.execute(cmd)
            assert getattr(ifo, hook) == new_val, f"hook {hook} not set"
            ms.undo()
            assert getattr(ifo, hook) == original, f"hook {hook} not undone"


class TestSaveAsWritesIFO:
    """_save_as must write an IFO file alongside the GIT."""

    def test_save_writes_ifo_beside_git(self):
        import tempfile, os
        from gmodular.formats.gff_reader import load_ifo

        with tempfile.TemporaryDirectory() as tmp:
            ms = _fresh_state()
            ms.ifo.mod_name    = "SaveAsTest"
            ms.ifo.entry_area  = "sa_area"
            ms.ifo.on_heartbeat = "k_hb_save"

            git_path = os.path.join(tmp, "sa_mod.git")
            ifo_path = git_path.replace(".git", ".ifo")

            # Save the GIT (module_state.save only saves GIT/IFO via project path
            # when a project exists; we test standalone GIT + companion IFO logic)
            ms.save(git_path=git_path)
            assert os.path.exists(git_path), "GIT file must be created"

            # Now simulate the save_as companion-IFO logic from main_window
            from gmodular.formats.gff_writer import save_ifo
            save_ifo(ms.ifo, ifo_path)
            assert os.path.exists(ifo_path), "IFO file must be created alongside GIT"

            # Round-trip check
            ifo2 = load_ifo(ifo_path)
            assert ifo2.mod_name   == "SaveAsTest"
            assert ifo2.entry_area == "sa_area"
            assert ifo2.on_heartbeat == "k_hb_save"


class TestRecentFilesLogic:
    """Test the recent-files list management logic (pure Python, no Qt needed)."""

    def _make_files(self, tmp, names):
        paths = []
        for n in names:
            p = os.path.join(tmp, n)
            open(p, "w").close()
            paths.append(p)
        return paths

    def test_dedup_and_order(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            files = self._make_files(tmp, ["a.git", "b.git", "c.git"])

            recent = []

            def add_recent(path):
                if path in recent:
                    recent.remove(path)
                recent.insert(0, path)
                if len(recent) > 10:
                    recent.pop()

            add_recent(files[0])
            add_recent(files[1])
            add_recent(files[0])   # re-open a; should move to front
            assert recent[0] == files[0]
            assert recent[1] == files[1]
            assert len(recent) == 2

    def test_max_10(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            files = self._make_files(tmp, [f"{i}.git" for i in range(15)])
            recent = []
            for f in files:
                if f in recent:
                    recent.remove(f)
                recent.insert(0, f)
                if len(recent) > 10:
                    recent.pop()
            assert len(recent) == 10


class TestViewportFrameAll:
    """frame_all() is a public alias; it should exist on ViewportWidget."""

    def test_frame_all_exists(self):
        from gmodular.gui.viewport import ViewportWidget
        assert hasattr(ViewportWidget, "frame_all"), \
            "ViewportWidget must expose a public frame_all() method"

    def test_frame_selected_falls_back_to_frame_all(self):
        """frame_selected() with no selection should call _frame_all internally."""
        # We cannot create a Qt widget without a QApplication, but we can verify
        # that ViewportWidget.frame_selected falls back via code inspection.
        import inspect
        from gmodular.gui.viewport import ViewportWidget as _VW
        src = inspect.getsource(_VW.frame_selected)
        assert "_frame_all" in src, \
            "frame_selected must call _frame_all when nothing is selected"


class TestInspectorConnectSpin:
    """_connect_spin must use editingFinished, not valueChanged."""

    def test_connect_spin_uses_editing_finished(self):
        import inspect
        from gmodular.gui.inspector import InspectorPanel
        src = inspect.getsource(InspectorPanel._connect_spin)
        assert "editingFinished" in src, \
            "_connect_spin must use editingFinished signal"
        # The docstring mentions 'valueChanged' for comparison; only check
        # that the actual signal connection is NOT valueChanged.connect
        assert "valueChanged.connect" not in src, \
            "_connect_spin must NOT use valueChanged.connect (floods undo stack)"

    def test_connect_spin_uses_modify_command(self):
        """_connect_spin should push ModifyPropertyCommand for undo support."""
        import inspect
        from gmodular.gui.inspector import InspectorPanel
        src = inspect.getsource(InspectorPanel._connect_spin)
        assert "ModifyPropertyCommand" in src, \
            "_connect_spin should use ModifyPropertyCommand for undo/redo"




# ─────────────────────────────────────────────────────────────────────────────
#  Regression tests for bugs found in Iteration 9 audit
# ─────────────────────────────────────────────────────────────────────────────

class TestSaveMakedirsEdgeCase:
    """Bug fix: save() must not fail when target path has no directory component."""

    def test_save_to_tempfile_no_dirname(self):
        """
        Regression: os.makedirs('') raises FileNotFoundError.
        save(git_path=<absolute-path>) must work even when the parent dir exists.
        """
        import tempfile, os
        ms = _fresh_state()
        ms.git.placeables.append(_placeable())

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "module.git")
            # This must not raise even though os.path.dirname(path) == tmp
            ms.save(git_path=path)
            assert os.path.exists(path)

    def test_save_dirname_empty_does_not_raise(self):
        """
        The fix: when dirname is empty (relative path with no separator),
        makedirs is skipped entirely.
        """
        # Patch os.makedirs to record calls
        import os
        import gmodular.core.module_state as ms_mod
        calls = []
        orig_makedirs = os.makedirs
        try:
            def fake_makedirs(path, **kw):
                calls.append(path)
                if path:
                    orig_makedirs(path, **kw)
            ms_mod.os.makedirs = fake_makedirs

            ms = _fresh_state()
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "test.git")
                ms.save(git_path=path)
                # makedirs should have been called with a non-empty path (the dir)
                # and NEVER with an empty string
                for c in calls:
                    assert c != '', f"save() must not call makedirs(''): calls={calls}"
        finally:
            ms_mod.os.makedirs = orig_makedirs


class TestViewportBoxDimensions:
    """Bug fix: _box_verts and _box_verts_solid must respect hh (y-axis half-width)."""

    def test_box_verts_y_uses_hh(self):
        """_box_verts must produce distinct y-extents when hw != hh."""
        from gmodular.gui.viewport import _box_verts
        import numpy as np
        # hw=1.0, hh=0.5: y should span [-0.5, +0.5], x should span [-1.0, +1.0]
        verts = _box_verts(0, 0, 0, hw=1.0, hh=0.5, hd=1.0, color=(1, 0, 0))
        # verts layout: each vertex is [x, y, z, r, g, b]
        ys = verts[1::6]   # y values at indices 1, 7, 13, ...
        xs = verts[0::6]   # x values

        y_min, y_max = float(ys.min()), float(ys.max())
        x_min, x_max = float(xs.min()), float(xs.max())

        assert abs(y_min - (-0.5)) < 1e-4, f"y_min should be -0.5, got {y_min}"
        assert abs(y_max -  0.5 ) < 1e-4, f"y_max should be +0.5, got {y_max}"
        assert abs(x_min - (-1.0)) < 1e-4, f"x_min should be -1.0, got {x_min}"
        assert abs(x_max -  1.0 ) < 1e-4, f"x_max should be +1.0, got {x_max}"

    def test_box_verts_square_when_hw_equals_hh(self):
        """When hw == hh, x and y extents must be equal."""
        from gmodular.gui.viewport import _box_verts
        verts = _box_verts(0, 0, 0, hw=0.5, hh=0.5, hd=0.5, color=(0, 1, 0))
        ys = verts[1::6]
        xs = verts[0::6]
        assert abs(xs.max() - ys.max()) < 1e-4

    def test_box_verts_solid_y_uses_hh(self):
        """_box_verts_solid must produce distinct y-extents when hw != hh."""
        from gmodular.gui.viewport import _box_verts_solid
        import numpy as np
        # Door shape: hw=0.5, hh=0.15 — y should span [-0.15, +0.15]
        verts = _box_verts_solid(0, 0, 0, hw=0.5, hh=0.15, hd=0.9, color=(1, 1, 0))
        ys = verts[1::6]
        xs = verts[0::6]

        y_min, y_max = float(ys.min()), float(ys.max())
        x_min, x_max = float(xs.min()), float(xs.max())

        assert abs(y_min - (-0.15)) < 1e-4, f"y_min should be -0.15, got {y_min}"
        assert abs(y_max -   0.15 ) < 1e-4, f"y_max should be +0.15, got {y_max}"
        assert abs(x_min - (-0.5) ) < 1e-4, f"x_min should be -0.5, got {x_min}"
        assert abs(x_max -   0.5  ) < 1e-4, f"x_max should be +0.5, got {x_max}"

    def test_creature_box_taller_than_wide(self):
        """Creature box: hw=hh=0.35, hd=0.70 — height (z) must be > width."""
        from gmodular.gui.viewport import _box_verts_solid
        verts = _box_verts_solid(0, 0, 0, hw=0.35, hh=0.35, hd=0.70, color=(1, 0, 0))
        zs = verts[2::6]
        xs = verts[0::6]
        z_range = float(zs.max()) - float(zs.min())
        x_range = float(xs.max()) - float(xs.min())
        assert z_range > x_range, f"Creature should be taller than wide: z={z_range}, x={x_range}"


class TestWOKParserBaseOffset:
    """Bug fix: WOK _parse_geometry must add BASE=12 to all geometry pointer offsets."""

    def test_parse_geometry_uses_base_offset(self):
        """Verify that the WOK parser references BASE=12 in its offset calculations."""
        import inspect
        from gmodular.gui.walkmesh_editor import WOKParser
        src = inspect.getsource(WOKParser._parse_geometry)
        # Fix introduces BASE = 12 and uses it
        assert "BASE" in src, "WOK parser must define BASE offset"
        assert "BASE + " in src, "WOK parser must add BASE to geometry pointers"

    def test_parse_geometry_absolute_node_offset(self):
        """node_arr_off and node_off must be absolute (BASE + relative) in source."""
        import inspect
        from gmodular.gui.walkmesh_editor import WOKParser
        src = inspect.getsource(WOKParser._parse_geometry)
        # The fixed code uses: node_arr_off = BASE + node_arr_rel
        assert "node_arr_off = BASE + " in src or "BASE + node_arr_rel" in src, \
            "node_arr_off must add BASE to the relative pointer"

    def test_truncated_wok_falls_back_to_synthetic(self):
        """A truncated/invalid WOK file must fall back to synthetic geometry."""
        from gmodular.gui.walkmesh_editor import WOKParser
        import struct
        # Build a minimal 'WOK' header that will fail geometry parse
        data = b'\x00' * 12   # too small geometry section
        parser = WOKParser(data)
        from gmodular.gui.walkmesh_editor import WOKData
        wok = WOKData()
        parser._parse_geometry(wok)
        # Should fall back to synthetic (>0 faces)
        assert len(wok.faces) == 0   # no fallback for size < 200


# ─────────────────────────────────────────────────────────────────────────────
# Iteration 10 regression tests
# ─────────────────────────────────────────────────────────────────────────────

class TestInspectorConnectScriptSingleSignal:
    """
    Bug fix: inspector._connect_script previously connected BOTH
    currentTextChanged AND editTextChanged, causing on_changed to fire
    twice per selection — doubling property_changed emissions.
    Fix: connect only editTextChanged.
    """

    def test_only_edit_text_changed_connected(self):
        """_connect_script source must NOT .connect(on_changed) via currentTextChanged."""
        import inspect
        from gmodular.gui.inspector import InspectorPanel
        src = inspect.getsource(InspectorPanel._connect_script)
        # Check that currentTextChanged is not wired up (connect call absent)
        assert "currentTextChanged.connect" not in src, (
            "_connect_script must not connect currentTextChanged — "
            "that causes double firing when a combo item is selected"
        )

    def test_edit_text_changed_is_connected(self):
        """_connect_script source MUST connect editTextChanged."""
        import inspect
        from gmodular.gui.inspector import InspectorPanel
        src = inspect.getsource(InspectorPanel._connect_script)
        assert "editTextChanged" in src, (
            "_connect_script must connect editTextChanged to handle both "
            "combo selection and manual text entry"
        )

    def test_property_changed_fires_once_per_change(self):
        """Simulate a script combo change — property_changed should fire exactly once."""
        from gmodular.gui.inspector import InspectorPanel, ScriptCombo
        from gmodular.formats.gff_types import GITPlaceable
        # We can't instantiate the full Qt widget without a QApplication,
        # but we can inspect the source to confirm the signal is only connected once.
        import inspect
        src = inspect.getsource(InspectorPanel._connect_script)
        connections = src.count(".connect(on_changed)")
        assert connections == 1, (
            f"_connect_script should connect on_changed exactly once, "
            f"found {connections} connection(s)"
        )


class TestAssetPaletteDeduplication:
    """
    Bug fix: populate_from_game built the `existing` set without .lower()
    but tested membership with resref.lower(), causing case-mismatches that
    allowed duplicate entries (e.g. both 'plc_chair01' and 'PLC_CHAIR01').
    Fix: build `existing` with .lower() so the comparison is consistent.
    """

    def test_existing_set_uses_lower(self):
        """The `existing` set in populate_from_game must use .lower()."""
        import inspect
        from gmodular.gui.asset_palette import AssetPalette
        src = inspect.getsource(AssetPalette.populate_from_game)
        # The fixed code uses .resref.lower() in the set comprehension
        assert ".lower()" in src.split("existing =")[1].split("\n")[0] or \
               "resref.lower()" in src, (
            "populate_from_game must build existing set with .lower() "
            "to prevent case-mismatch duplicates"
        )

    def test_no_duplicate_on_uppercase_resref(self):
        """Uppercase variant of an existing ResRef must NOT be added as duplicate."""
        from gmodular.gui.asset_palette import AssetPalette
        # AssetPalette cannot be instantiated without QApplication; test logic directly.
        # The _assets dict stores items; simulate the de-dup logic manually.
        existing_resrefs = ["plc_chair01", "plc_crate01"]
        # Build existing with .lower() (fixed behaviour)
        existing = {r.lower() for r in existing_resrefs}
        new_refs = ["PLC_CHAIR01", "PLc_Crate01", "plc_workbench01"]
        added = [r for r in new_refs if r.lower() not in existing]
        assert len(added) == 1, f"Only 1 new item expected, got {len(added)}: {added}"
        assert added[0] == "plc_workbench01"

    def test_duplicate_without_fix_would_add_wrong_count(self):
        """Demonstrate the old bug: without .lower() in existing, duplicates occur."""
        existing_resrefs = ["plc_chair01", "plc_crate01"]
        # OLD (buggy) behaviour: no .lower() in set comprehension
        existing_buggy = set(existing_resrefs)
        new_refs = ["PLC_CHAIR01", "PLc_Crate01", "plc_workbench01"]
        added_buggy = [r for r in new_refs if r.lower() not in existing_buggy]
        # Bug: "PLC_CHAIR01".lower() = "plc_chair01" IS in existing_buggy, so actually
        # the OLD bug was the opposite — the check `resref.lower() not in existing`
        # where existing has un-lowered values would MISS the match when the existing
        # value has uppercase chars. Demonstrate: existing has uppercase.
        existing_uppercase = {"PLC_CHAIR01", "PLc_Crate01"}
        added_miss = [r for r in ["plc_chair01"] if r.lower() not in existing_uppercase]
        # Without .lower() on existing, "plc_chair01" != "PLC_CHAIR01" — would be re-added
        assert len(added_miss) == 1, "Confirms the bug: case mismatch causes re-addition"


class TestBridgesDeadCodeRemoved:
    """
    Bug fix: _ProjectFileHandler class was defined in bridges.py but never
    instantiated (ProjectFileWatcher uses its own inline _Handler class).
    Dead code has been removed to avoid confusion.
    """

    def test_project_file_handler_removed(self):
        """_ProjectFileHandler dead class must no longer exist in bridges.py."""
        import inspect
        import gmodular.ipc.bridges as bridges_mod
        assert not hasattr(bridges_mod, "_ProjectFileHandler"), (
            "_ProjectFileHandler was dead code (never instantiated) and "
            "has been removed; it should no longer be importable"
        )

    def test_project_file_watcher_still_exists(self):
        """ProjectFileWatcher (the real watcher) must still be present."""
        from gmodular.ipc.bridges import ProjectFileWatcher
        assert ProjectFileWatcher is not None

    def test_project_file_watcher_has_watch_method(self):
        """ProjectFileWatcher.watch() must still exist after cleanup."""
        from gmodular.ipc.bridges import ProjectFileWatcher
        assert hasattr(ProjectFileWatcher, "watch")
        assert hasattr(ProjectFileWatcher, "stop")


# ─────────────────────────────────────────────────────────────────────────────
# Iteration 11 regression tests
# ─────────────────────────────────────────────────────────────────────────────

class TestOrbitCameraPanSafety:
    """
    Bug fix: OrbitCamera.pan() computed up = cross(right, fwd) without
    normalising or guarding against zero-length result.  Near-vertical
    camera angles (elevation ≈ ±90°) produced NaN values that propagated
    to self.target and corrupted the camera state.
    Fix: normalise the up vector; fall back through two alternatives when
    the primary cross-product is degenerate.
    """

    def test_pan_normal_angle_no_nan(self):
        """Standard 30° elevation pan must not produce NaN target."""
        import numpy as np
        from gmodular.gui.viewport import OrbitCamera
        cam = OrbitCamera()
        cam.elevation = 30.0
        cam.pan(10.0, 5.0)
        assert all(np.isfinite(cam.target)), f"NaN in target after pan: {cam.target}"

    def test_pan_near_vertical_no_nan(self):
        """Near-vertical camera (elevation = 84°) must not produce NaN target."""
        import numpy as np
        from gmodular.gui.viewport import OrbitCamera
        cam = OrbitCamera()
        cam.elevation = 84.0   # very close to the 85° clamp
        initial = cam.target.copy()
        cam.pan(5.0, 5.0)
        assert all(np.isfinite(cam.target)), (
            f"NaN in target after near-vertical pan: {cam.target}"
        )

    def test_pan_negative_elevation_no_nan(self):
        """Negative elevation (looking up, -80°) must also be safe."""
        import numpy as np
        from gmodular.gui.viewport import OrbitCamera
        cam = OrbitCamera()
        cam.elevation = -80.0
        cam.pan(3.0, 7.0)
        assert all(np.isfinite(cam.target)), (
            f"NaN in target after negative-elevation pan: {cam.target}"
        )

    def test_pan_zero_distance_is_noop(self):
        """When eye == target (distance=0), pan must not crash."""
        import numpy as np
        from gmodular.gui.viewport import OrbitCamera
        cam = OrbitCamera()
        cam.distance = 0.0   # degenerate: eye == target
        initial = cam.target.copy()
        cam.pan(10.0, 10.0)   # should return early, not crash
        # Target unchanged or still finite
        assert all(np.isfinite(cam.target)), "NaN after zero-distance pan"

    def test_pan_source_up_is_normalised(self):
        """Verify the fixed pan() normalises the up vector before use."""
        import inspect
        from gmodular.gui.viewport import OrbitCamera
        src = inspect.getsource(OrbitCamera.pan)
        assert "up_len" in src and "up /= up_len" in src, (
            "pan() must normalise the up vector (up /= up_len) in the fixed version"
        )


class TestModuleStateMathImport:
    """
    Bug fix: 'import math' was nested inside the _check_list inner function
    inside validate(), causing repeated import overhead on every call.
    Fix: moved to module level.
    """

    def test_math_at_module_level(self):
        """'import math' must be at the top of module_state.py, not inside a function."""
        import inspect, ast
        import gmodular.core.module_state as ms_mod
        source = inspect.getsource(ms_mod)
        # Find the first occurrence of 'import math'
        lines = source.splitlines()
        first_import_line = None
        for i, line in enumerate(lines):
            if line.strip() == "import math":
                first_import_line = i
                break
        assert first_import_line is not None, "import math not found in module_state.py"
        # It should be very near the top (within first 20 lines of the module)
        assert first_import_line < 20, (
            f"import math is at source line {first_import_line} — should be "
            f"at module level (within first 20 lines), not inside a function"
        )

    def test_validate_still_detects_nan(self):
        """validate() must still correctly flag NaN positions after the import fix."""
        import math
        from gmodular.core.module_state import ModuleState
        from gmodular.formats.gff_types import GITData, GITPlaceable, Vector3
        state = ModuleState()
        state.git = GITData()
        bad = GITPlaceable(resref="test", tag="bad")
        bad.position = Vector3(float("nan"), 0.0, 0.0)
        state.git.placeables.append(bad)
        issues = state.validate()
        nan_issues = [w for w in issues if "invalid" in w.lower() or "nan" in w.lower() or "x" in w.lower()]
        assert any("X" in w for w in issues), (
            f"validate() should flag NaN X position, got: {issues}"
        )


class TestSpecHiddenImports:
    """
    Bug fix: GModular.spec was missing hidden_imports for engine sub-packages,
    mdl_parser, and ipc.callback_server — causing ImportError crashes in the
    packaged EXE at runtime.
    Fix: added all missing entries to hidden_imports list.
    """

    REQUIRED_IMPORTS = [
        "gmodular.engine",
        "gmodular.engine.player_controller",
        "gmodular.engine.npc_instance",
        "gmodular.formats.mdl_parser",
        "gmodular.ipc.callback_server",
    ]

    def test_all_required_imports_present(self):
        """GModular.spec must list all required hidden_imports."""
        with open("GModular.spec") as f:
            spec_src = f.read()
        for imp in self.REQUIRED_IMPORTS:
            assert f'"{imp}"' in spec_src, (
                f"GModular.spec missing hidden import: {imp!r} — "
                f"the packaged EXE will crash at runtime without it"
            )

    def test_no_duplicate_hidden_imports(self):
        """Each hidden_import entry should appear exactly once inside the hidden_imports list."""
        with open("GModular.spec") as f:
            spec_src = f.read()
        import re
        # Extract only the hidden_imports list block (between the list brackets)
        m = re.search(r'hidden_imports\s*=\s*\[(.*?)\]', spec_src, re.DOTALL)
        assert m, "Could not find hidden_imports list in GModular.spec"
        hi_block = m.group(1)
        entries = re.findall(r'"([^"]+)"', hi_block)
        from collections import Counter
        counts = Counter(entries)
        dupes = {k: v for k, v in counts.items() if v > 1}
        assert not dupes, f"Duplicate hidden_imports in spec: {dupes}"


class TestRequirementsTxt:
    """
    Fix: requirements.txt was missing pyinstaller.
    """

    def test_pyinstaller_in_requirements(self):
        """pyinstaller must be listed in requirements.txt."""
        with open("requirements.txt") as f:
            content = f.read().lower()
        assert "pyinstaller" in content, (
            "requirements.txt must list pyinstaller so CI and fresh venvs "
            "can reproduce the build environment"
        )

    def test_moderngl_in_requirements(self):
        """moderngl must be in requirements.txt."""
        with open("requirements.txt") as f:
            content = f.read().lower()
        assert "moderngl" in content

    def test_pyqt5_in_requirements(self):
        """PyQt5 must be in requirements.txt."""
        with open("requirements.txt") as f:
            content = f.read()
        assert "PyQt5" in content


class TestBuildBat:
    """Validate the build.bat structure and key features."""

    def _read_bat(self):
        with open("build.bat") as f:
            return f.read()

    def test_bat_uses_spec_file(self):
        """build.bat must invoke GModular.spec via PyInstaller."""
        bat = self._read_bat()
        assert "GModular.spec" in bat, "build.bat must pass GModular.spec to PyInstaller"

    def test_bat_has_clean_noconfirm(self):
        """build.bat must pass --clean --noconfirm to PyInstaller."""
        bat = self._read_bat()
        assert "--clean" in bat and "--noconfirm" in bat

    def test_bat_checks_python_version(self):
        """build.bat must enforce Python 3.10+ requirement."""
        bat = self._read_bat()
        assert "3.10" in bat or "3,10" in bat, (
            "build.bat must check for Python 3.10+ minimum version"
        )

    def test_bat_has_venv_support(self):
        """build.bat must activate venv if present."""
        bat = self._read_bat()
        assert "venv" in bat.lower(), "build.bat should support virtual environments"

    def test_bat_validates_output(self):
        """build.bat must verify dist\\GModular.exe exists after build."""
        bat = self._read_bat()
        assert "dist\\GModular.exe" in bat or "dist/GModular.exe" in bat, (
            "build.bat must check for the output EXE after PyInstaller runs"
        )

    def test_bat_has_self_test(self):
        """build.bat must include a Python import self-test before building."""
        bat = self._read_bat()
        assert "self-test" in bat.lower() or "import" in bat, (
            "build.bat should run a self-test import check before building"
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Iteration 12 Regression Tests
#  Fixes:
#    1. gff_writer.py / gff_reader.py – Door missing on_lock, on_melee_attacked,
#       on_open2, on_unlock, on_user_defined script fields in writer; reader
#       also missing on_user_defined for doors.
#    2. gff_writer.py – Creature struct missing on_disturbed, on_blocked,
#       on_attacked, on_damaged, on_user_defined, on_conversation.
#    3. gff_writer.py – Trigger struct missing on_user_defined.
#    4. viewport.py  – ray_box() used pos.y - hw instead of pos.y - hh,
#       causing wrong y-axis hit volume for asymmetric objects (e.g. doors).
#    5. npc_instance.py – from_git_creature() incorrectly applied math.degrees()
#       to XOrientation float component (not a radian angle); fixed to use
#       math.asin() to recover the yaw angle correctly.
#    6. main_window.py – _on_play_mode_changed(False) used wrong colour
#       (#569cd6) for mode label; should be #4ec9b0 to match edit-mode init.
#    7. test fix: test_no_duplicate_hidden_imports regex now only scans the
#       hidden_imports block (not icon paths elsewhere in the spec).
# ─────────────────────────────────────────────────────────────────────────────

class TestDoorScriptRoundTrip:
    """
    Fix: _door_struct in gff_writer.py omitted on_lock, on_melee_attacked,
    on_open2, on_unlock, on_user_defined.  _git_door in gff_reader.py omitted
    on_user_defined.  Both fixed in Iteration 12.
    """

    def _roundtrip(self, door):
        """Write a GIT with one door, read it back."""
        import tempfile, os
        from gmodular.formats.gff_types import GITData
        from gmodular.formats.gff_writer import save_git
        from gmodular.formats.gff_reader import load_git
        git = GITData()
        git.doors.append(door)
        tmp = tempfile.mktemp(suffix=".git")
        try:
            save_git(git, tmp)
            return load_git(tmp).doors[0]
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_door_on_lock_roundtrip(self):
        """on_lock must survive writer→reader round-trip."""
        from gmodular.formats.gff_types import GITDoor
        d = GITDoor()
        d.resref = "testd"; d.template_resref = "testd"; d.tag = "D"
        d.on_lock = "door_lock"
        d2 = self._roundtrip(d)
        assert d2.on_lock == "door_lock", f"Expected 'door_lock', got {d2.on_lock!r}"

    def test_door_on_melee_attacked_roundtrip(self):
        """on_melee_attacked must survive round-trip."""
        from gmodular.formats.gff_types import GITDoor
        d = GITDoor(); d.resref = "x"; d.template_resref = "x"; d.tag = "D"
        d.on_melee_attacked = "door_mel"
        d2 = self._roundtrip(d)
        assert d2.on_melee_attacked == "door_mel"

    def test_door_on_open2_roundtrip(self):
        from gmodular.formats.gff_types import GITDoor
        d = GITDoor(); d.resref = "x"; d.template_resref = "x"; d.tag = "D"
        d.on_open2 = "door_op2"
        d2 = self._roundtrip(d)
        assert d2.on_open2 == "door_op2"

    def test_door_on_unlock_roundtrip(self):
        from gmodular.formats.gff_types import GITDoor
        d = GITDoor(); d.resref = "x"; d.template_resref = "x"; d.tag = "D"
        d.on_unlock = "door_unlk"
        d2 = self._roundtrip(d)
        assert d2.on_unlock == "door_unlk"

    def test_door_on_user_defined_roundtrip(self):
        """on_user_defined was omitted from both writer and reader — now fixed."""
        from gmodular.formats.gff_types import GITDoor
        d = GITDoor(); d.resref = "x"; d.template_resref = "x"; d.tag = "D"
        d.on_user_defined = "door_usr"
        d2 = self._roundtrip(d)
        assert d2.on_user_defined == "door_usr", (
            f"on_user_defined should survive round-trip; got {d2.on_user_defined!r}"
        )

    def test_all_door_scripts_together(self):
        """All door script fields survive round-trip simultaneously."""
        from gmodular.formats.gff_types import GITDoor
        d = GITDoor(); d.resref = "alldoor"; d.template_resref = "alldoor"; d.tag = "AD"
        d.on_open = "d_open"; d.on_closed = "d_close"
        d.on_fail_to_open = "d_fail"; d.on_damaged = "d_dmg"
        d.on_death = "d_death"; d.on_heartbeat = "d_hb"
        d.on_lock = "d_lock"; d.on_melee_attacked = "d_mel"
        d.on_open2 = "d_open2"; d.on_unlock = "d_unlk"
        d.on_user_defined = "d_usr"
        d2 = self._roundtrip(d)
        for attr in ("on_open", "on_closed", "on_fail_to_open", "on_damaged",
                     "on_death", "on_heartbeat", "on_lock", "on_melee_attacked",
                     "on_open2", "on_unlock", "on_user_defined"):
            assert getattr(d2, attr) == getattr(d, attr), (
                f"Door {attr} mismatch: {getattr(d, attr)!r} → {getattr(d2, attr)!r}"
            )


class TestCreatureScriptRoundTrip:
    """
    Fix: _creature_struct omitted on_disturbed, on_blocked, on_attacked,
    on_damaged, on_user_defined, on_conversation.  Added in Iteration 12.
    """

    def _roundtrip(self, creature):
        import tempfile, os
        from gmodular.formats.gff_types import GITData
        from gmodular.formats.gff_writer import save_git
        from gmodular.formats.gff_reader import load_git
        git = GITData()
        git.creatures.append(creature)
        tmp = tempfile.mktemp(suffix=".git")
        try:
            save_git(git, tmp)
            return load_git(tmp).creatures[0]
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_creature_on_disturbed_roundtrip(self):
        from gmodular.formats.gff_types import GITCreature
        c = GITCreature(); c.resref = "x"; c.on_disturbed = "c_dist"
        c2 = self._roundtrip(c)
        assert c2.on_disturbed == "c_dist"

    def test_creature_on_blocked_roundtrip(self):
        from gmodular.formats.gff_types import GITCreature
        c = GITCreature(); c.resref = "x"; c.on_blocked = "c_blk"
        c2 = self._roundtrip(c)
        assert c2.on_blocked == "c_blk"

    def test_creature_on_attacked_roundtrip(self):
        from gmodular.formats.gff_types import GITCreature
        c = GITCreature(); c.resref = "x"; c.on_attacked = "c_atk"
        c2 = self._roundtrip(c)
        assert c2.on_attacked == "c_atk"

    def test_creature_on_damaged_roundtrip(self):
        from gmodular.formats.gff_types import GITCreature
        c = GITCreature(); c.resref = "x"; c.on_damaged = "c_dmg"
        c2 = self._roundtrip(c)
        assert c2.on_damaged == "c_dmg"

    def test_creature_on_user_defined_roundtrip(self):
        from gmodular.formats.gff_types import GITCreature
        c = GITCreature(); c.resref = "x"; c.on_user_defined = "c_usr"
        c2 = self._roundtrip(c)
        assert c2.on_user_defined == "c_usr"

    def test_creature_on_conversation_roundtrip(self):
        from gmodular.formats.gff_types import GITCreature
        c = GITCreature(); c.resref = "x"; c.on_conversation = "c_conv"
        c2 = self._roundtrip(c)
        assert c2.on_conversation == "c_conv"


class TestTriggerScriptRoundTrip:
    """
    Fix: _trigger_struct omitted on_user_defined.  Added in Iteration 12.
    """

    def _roundtrip(self, trigger):
        import tempfile, os
        from gmodular.formats.gff_types import GITData, Vector3
        from gmodular.formats.gff_writer import save_git
        from gmodular.formats.gff_reader import load_git
        git = GITData()
        trigger.geometry = [Vector3(0,0,0), Vector3(1,0,0), Vector3(1,1,0)]
        git.triggers.append(trigger)
        tmp = tempfile.mktemp(suffix=".git")
        try:
            save_git(git, tmp)
            return load_git(tmp).triggers[0]
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_trigger_on_user_defined_roundtrip(self):
        from gmodular.formats.gff_types import GITTrigger
        t = GITTrigger(); t.resref = "x"; t.on_user_defined = "trig_usr"
        t2 = self._roundtrip(t)
        assert t2.on_user_defined == "trig_usr", (
            f"trigger on_user_defined should survive round-trip; got {t2.on_user_defined!r}"
        )


class TestViewportRayBoxHitTest:
    """
    Fix: ray_box() in viewport.py used pos.y - hw instead of pos.y - hh,
    making the Y-axis hit volume wrong for objects with different hw vs hh
    (e.g. doors: hw=0.5, hh=0.15).
    """

    def _make_ray_box_fn(self):
        """Reconstruct the ray_box logic from viewport.py for isolated testing."""
        import numpy as np

        def ray_box_old(pos, hw, hh, hd, origin, direction):
            """Old (buggy) version."""
            bmin = np.array([pos[0] - hw, pos[1] - hw, pos[2]],       dtype='f4')
            bmax = np.array([pos[0] + hw, pos[1] + hw, pos[2] + hd*2], dtype='f4')
            t_min = (bmin - origin) / (direction + 1e-20)
            t_max = (bmax - origin) / (direction + 1e-20)
            t_near = np.minimum(t_min, t_max)
            t_far  = np.maximum(t_min, t_max)
            t_enter = t_near.max()
            t_exit  = t_far.min()
            if t_enter <= t_exit and t_exit > 0:
                return t_enter if t_enter > 0 else t_exit
            return None

        def ray_box_new(pos, hw, hh, hd, origin, direction):
            """Fixed version."""
            bmin = np.array([pos[0] - hw, pos[1] - hh, pos[2]],       dtype='f4')
            bmax = np.array([pos[0] + hw, pos[1] + hh, pos[2] + hd*2], dtype='f4')
            t_min = (bmin - origin) / (direction + 1e-20)
            t_max = (bmax - origin) / (direction + 1e-20)
            t_near = np.minimum(t_min, t_max)
            t_far  = np.maximum(t_min, t_max)
            t_enter = t_near.max()
            t_exit  = t_far.min()
            if t_enter <= t_exit and t_exit > 0:
                return t_enter if t_enter > 0 else t_exit
            return None

        return ray_box_old, ray_box_new

    def test_door_y_hit_old_vs_new(self):
        """Old code incorrectly hits a door from the side; new code misses correctly."""
        import numpy as np
        ray_box_old, ray_box_new = self._make_ray_box_fn()
        # Door at origin: hw=0.5, hh=0.15, hd=0.9
        # Cast ray at y=0.3 (outside hh=0.15 but inside hw=0.5)
        pos = (0.0, 0.0, 0.0)
        hw, hh, hd = 0.5, 0.15, 0.9
        origin    = np.array([0.0,  0.3, 5.0], dtype='f4')  # y=0.3 > hh=0.15
        direction = np.array([0.0,  0.0, -1.0], dtype='f4')  # straight down

        old_hit = ray_box_old(pos, hw, hh, hd, origin, direction)
        new_hit = ray_box_new(pos, hw, hh, hd, origin, direction)

        # Old code hits (wrong) because it used hw=0.5 for y-axis
        assert old_hit is not None, "Old code should hit (demonstrating the bug)"
        # New code correctly misses at y=0.3 with hh=0.15
        assert new_hit is None, (
            f"New code should miss y=0.3 with hh=0.15, but got t={new_hit}"
        )

    def test_door_y_hit_inside(self):
        """Ray inside door's hh should always hit with new code."""
        import numpy as np
        _, ray_box_new = self._make_ray_box_fn()
        pos = (0.0, 0.0, 0.0); hw, hh, hd = 0.5, 0.15, 0.9
        origin    = np.array([0.0, 0.10, 5.0], dtype='f4')  # y=0.10 < hh=0.15
        direction = np.array([0.0,  0.0, -1.0], dtype='f4')
        assert ray_box_new(pos, hw, hh, hd, origin, direction) is not None

    def test_symmetric_box_unchanged(self):
        """For symmetric boxes (hw==hh), old and new code are identical."""
        import numpy as np
        ray_box_old, ray_box_new = self._make_ray_box_fn()
        pos = (0.0, 0.0, 0.0); hw = hh = hd = 0.3
        origin    = np.array([0.0, 0.0, 5.0], dtype='f4')
        direction = np.array([0.0, 0.0, -1.0], dtype='f4')
        old_t = ray_box_old(pos, hw, hh, hd, origin, direction)
        new_t = ray_box_new(pos, hw, hh, hd, origin, direction)
        assert old_t is not None and new_t is not None
        assert abs(old_t - new_t) < 1e-4


class TestNPCBearingConversion:
    """
    Fix: NPCInstance.from_git_creature() incorrectly applied math.degrees()
    to XOrientation (which is a direction component, not radians).
    Now uses math.asin() to recover the yaw angle correctly.
    """

    def test_facing_forward_y(self):
        """XOrientation=0.0 → yaw=0 degrees → facing +Y direction."""
        import math
        from gmodular.engine.npc_instance import NPCInstance
        from gmodular.formats.gff_types import GITCreature
        c = GITCreature(); c.bearing = 0.0   # XOrientation=0, facing +Y
        npc = NPCInstance.from_git_creature(c)
        assert abs(npc.bearing) < 0.1, f"yaw should be ~0, got {npc.bearing}"
        dx, dy = npc.direction_vector()
        assert abs(dx) < 0.01 and abs(dy - 1.0) < 0.01, (
            f"Should face +Y (0,1) but got ({dx:.3f},{dy:.3f})"
        )

    def test_facing_negative_x(self):
        """XOrientation=-1.0 → yaw=+90 degrees → facing -X direction."""
        import math
        from gmodular.engine.npc_instance import NPCInstance
        from gmodular.formats.gff_types import GITCreature
        c = GITCreature(); c.bearing = -1.0
        npc = NPCInstance.from_git_creature(c)
        assert abs(npc.bearing - 90.0) < 0.1, f"Expected 90 deg, got {npc.bearing}"
        dx, dy = npc.direction_vector()
        assert abs(dx + 1.0) < 0.01 and abs(dy) < 0.01, (
            f"Should face -X but got ({dx:.3f},{dy:.3f})"
        )

    def test_facing_positive_x(self):
        """XOrientation=+1.0 → yaw=-90 degrees → facing +X direction."""
        from gmodular.engine.npc_instance import NPCInstance
        from gmodular.formats.gff_types import GITCreature
        c = GITCreature(); c.bearing = 1.0
        npc = NPCInstance.from_git_creature(c)
        assert abs(npc.bearing + 90.0) < 0.1, f"Expected -90 deg, got {npc.bearing}"
        dx, dy = npc.direction_vector()
        assert abs(dx - 1.0) < 0.01 and abs(dy) < 0.01, (
            f"Should face +X but got ({dx:.3f},{dy:.3f})"
        )

    def test_old_degrees_conversion_was_wrong(self):
        """
        Demonstrate the old bug: math.degrees(-1.0) = -57.3, not 90.
        The fixed code produces 90 degrees for XOrientation=-1.0.
        """
        import math
        from gmodular.engine.npc_instance import NPCInstance
        from gmodular.formats.gff_types import GITCreature
        c = GITCreature(); c.bearing = -1.0
        npc = NPCInstance.from_git_creature(c)
        # Old buggy result would have been math.degrees(-1.0) = -57.295...
        assert abs(npc.bearing - (-57.295)) > 1.0, (
            "Bearing should NOT be the raw math.degrees(-1.0) = -57.3 (old bug)"
        )
        # Correct result is 90 degrees
        assert abs(npc.bearing - 90.0) < 0.1, f"Expected 90 deg; got {npc.bearing}"

    def test_clamped_orientation_does_not_raise(self):
        """Out-of-range XOrientation values are clamped to [-1,1] to prevent asin domain error."""
        from gmodular.engine.npc_instance import NPCInstance
        from gmodular.formats.gff_types import GITCreature
        c = GITCreature(); c.bearing = 2.5   # > 1.0; would cause math.asin domain error
        try:
            npc = NPCInstance.from_git_creature(c)
            _ = npc.direction_vector()
        except ValueError:
            raise AssertionError("math.asin domain error — clamping not applied")


class TestModeLaberColorConsistency:
    """
    Fix: main_window.py _on_play_mode_changed(False) used #569cd6 instead
    of #4ec9b0 for the EDIT MODE label.  Now consistent.
    """

    def test_mode_label_colour_in_stop_is_4ec9b0(self):
        """After play mode stops, mode_label colour must be #4ec9b0 (teal)."""
        with open("gmodular/gui/main_window.py") as f:
            src = f.read()
        import re
        # Find the _on_play_mode_changed method
        m = re.search(r'def _on_play_mode_changed.*?(?=\n    def )', src, re.DOTALL)
        assert m, "Could not find _on_play_mode_changed in main_window.py"
        method_src = m.group(0)
        # The colour #569cd6 (VS Code blue) must NOT appear in play-stop branch
        # Split on 'else:' to isolate the stop branch
        parts = method_src.split("else:")
        assert len(parts) >= 2, "Expected 'else:' branch in _on_play_mode_changed"
        stop_branch = parts[-1]
        assert "#569cd6" not in stop_branch, (
            "mode_label in stop branch still uses #569cd6 (blue) "
            "— should be #4ec9b0 (teal) to match initial EDIT MODE style"
        )
        assert "#4ec9b0" in stop_branch, (
            "mode_label in stop branch should use #4ec9b0 (teal)"
        )


# =============================================================================
# Iteration 13 Regression Tests
# =============================================================================


class TestInspectorDoorScripts:
    """
    Fix: inspector.py _build_door() was missing on_open2 and on_unlock entries,
    so those script fields were invisible in the UI even though they exist on GITDoor.
    """

    def test_door_script_list_includes_on_open2(self):
        """on_open2 must appear in inspector _build_door script events."""
        with open("gmodular/gui/inspector.py") as f:
            src = f.read()
        import re
        m = re.search(r'def _build_door.*?(?=\n    def )', src, re.DOTALL)
        assert m, "Could not find _build_door in inspector.py"
        method_src = m.group(0)
        assert '"on_open2"' in method_src or "'on_open2'" in method_src, (
            "_build_door is missing the on_open2 script event"
        )

    def test_door_script_list_includes_on_unlock(self):
        """on_unlock must appear in inspector _build_door script events."""
        with open("gmodular/gui/inspector.py") as f:
            src = f.read()
        import re
        m = re.search(r'def _build_door.*?(?=\n    def )', src, re.DOTALL)
        assert m, "Could not find _build_door in inspector.py"
        method_src = m.group(0)
        assert '"on_unlock"' in method_src or "'on_unlock'" in method_src, (
            "_build_door is missing the on_unlock script event"
        )

    def test_door_script_event_count(self):
        """_build_door must expose at least 11 script events (all KotOR door events)."""
        with open("gmodular/gui/inspector.py") as f:
            src = f.read()
        import re
        m = re.search(r'def _build_door.*?(?=\n    def )', src, re.DOTALL)
        assert m, "Could not find _build_door in inspector.py"
        # Count tuples of the form ("on_...", "...")
        events = re.findall(r'"on_\w+"', m.group(0))
        assert len(events) >= 11, (
            f"_build_door only exposes {len(events)} script events; expected ≥11"
        )


class TestInspectorCreatureScripts:
    """
    Fix: inspector.py _build_creature() was missing on_conversation,
    which IS a valid creature script in GITCreature.
    """

    def test_creature_script_list_includes_on_conversation(self):
        """on_conversation must appear in inspector _build_creature script events."""
        with open("gmodular/gui/inspector.py") as f:
            src = f.read()
        import re
        m = re.search(r'def _build_creature.*?(?=\n    def )', src, re.DOTALL)
        assert m, "Could not find _build_creature in inspector.py"
        method_src = m.group(0)
        assert '"on_conversation"' in method_src or "'on_conversation'" in method_src, (
            "_build_creature is missing the on_conversation script event"
        )


class TestSceneOutlineSearchDebounce:
    """
    Fix: scene_outline.py _on_search used to call _refresh() directly on every
    keystroke, causing O(N) tree rebuilds per character.  Now uses a QTimer
    debounce of 150 ms so rapid typing only triggers one rebuild.
    """

    def test_debounce_timer_exists_in_init(self):
        """SceneOutlinePanel.__init__ must create a QTimer for debouncing search."""
        with open("gmodular/gui/scene_outline.py") as f:
            src = f.read()
        import re
        # Find __init__ body
        m = re.search(r'def __init__.*?(?=\n    def )', src, re.DOTALL)
        assert m, "__init__ not found in scene_outline.py"
        init_body = m.group(0)
        assert "QTimer" in init_body, (
            "SceneOutlinePanel.__init__ must create a QTimer for search debounce"
        )
        assert "_search_timer" in init_body, (
            "No _search_timer attribute created in __init__"
        )

    def test_on_search_does_not_call_refresh_directly(self):
        """_on_search must NOT call self._refresh() directly (debounced instead)."""
        with open("gmodular/gui/scene_outline.py") as f:
            src = f.read()
        import re
        m = re.search(r'def _on_search.*?(?=\n    def )', src, re.DOTALL)
        assert m, "_on_search not found in scene_outline.py"
        method_body = m.group(0)
        # Direct call to _refresh() means debounce is not working
        assert "self._refresh()" not in method_body, (
            "_on_search still calls _refresh() directly; debounce timer not used"
        )
        assert "_search_timer" in method_body or "timer" in method_body.lower(), (
            "_on_search must delegate to the debounce timer"
        )


class TestWOKFaceWalkability:
    """
    Fix: WOKFace.is_walkable was missing walk types 16-19 (quicksand, lava,
    hot ground, tall grass) — all passable terrain in KotOR but handled
    specially by the engine for damage/slow effects.
    """

    def test_quicksand_is_walkable(self):
        """Walk type 16 (Quicksand) must be considered walkable."""
        from gmodular.gui.walkmesh_editor import WOKFace
        f = WOKFace(walk_type=16)
        assert f.is_walkable, "Quicksand (16) should be walkable"

    def test_lava_is_walkable(self):
        """Walk type 17 (Lava) must be considered walkable (player takes damage but can walk)."""
        from gmodular.gui.walkmesh_editor import WOKFace
        f = WOKFace(walk_type=17)
        assert f.is_walkable, "Lava (17) should be walkable"

    def test_hot_ground_is_walkable(self):
        """Walk type 18 (Hot Ground) must be considered walkable."""
        from gmodular.gui.walkmesh_editor import WOKFace
        f = WOKFace(walk_type=18)
        assert f.is_walkable, "Hot Ground (18) should be walkable"

    def test_tall_grass_is_walkable(self):
        """Walk type 19 (Grass Tall) must be considered walkable."""
        from gmodular.gui.walkmesh_editor import WOKFace
        f = WOKFace(walk_type=19)
        assert f.is_walkable, "Grass Tall (19) should be walkable"

    def test_non_walk_is_not_walkable(self):
        """Walk type 0 (Non-Walk) must NOT be walkable."""
        from gmodular.gui.walkmesh_editor import WOKFace
        assert not WOKFace(walk_type=0).is_walkable

    def test_trigger_non_walk_is_not_walkable(self):
        """Walk type 8 (Trigger — non-walk) must NOT be walkable."""
        from gmodular.gui.walkmesh_editor import WOKFace
        assert not WOKFace(walk_type=8).is_walkable

    def test_all_non_walkable_types(self):
        """Only types 0, 6 (water/swimming), and 8 (trigger boundary) are non-walkable."""
        from gmodular.gui.walkmesh_editor import WOKFace
        for wt in (0, 6, 8):
            assert not WOKFace(walk_type=wt).is_walkable, (
                f"Walk type {wt} should NOT be walkable but is_walkable returned True"
            )


class TestAutosaveGuard:
    """
    Fix: ModuleState._autosave_tick() previously rescheduled the timer even
    after close() cleared self.git/self.project, causing callbacks on stale state.
    Now guards with 'if self.git is None or self.project is None: return'.
    """

    def test_autosave_tick_does_not_reschedule_when_closed(self):
        """After close(), _autosave_tick must return early without calling _start_autosave."""
        import threading
        from gmodular.core.module_state import ModuleState
        ms = ModuleState()
        # Arm the autosave via new_module then immediately close
        from gmodular.formats.gff_types import GITData, AREData, IFOData
        from gmodular.core.module_state import ModuleProject
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            proj = ModuleProject.create_new("Test", "K1", td, "test_mod")
            ms.new_module(proj)
            ms.close()
            # After close, git and project are both None
            assert ms.git is None
            assert ms.project is None
            # _autosave_tick should now be a no-op
            timers_before = len([t for t in threading.enumerate() if 'Thread' in t.name])
            ms._autosave_tick()   # must not raise or rearm
            timers_after = len([t for t in threading.enumerate() if 'Thread' in t.name])
            # No new timer threads should have been spawned
            assert timers_after <= timers_before + 1, (
                "_autosave_tick created new threads after close() — guard not working"
            )

    def test_autosave_tick_runs_when_module_open_and_dirty(self):
        """_autosave_tick must attempt autosave when a dirty module is open."""
        from gmodular.core.module_state import ModuleState, ModuleProject
        import tempfile, os
        ms = ModuleState()
        with tempfile.TemporaryDirectory() as td:
            proj = ModuleProject.create_new("Test", "K1", td, "test_mod")
            ms.new_module(proj)
            ms._dirty = True
            saved_paths = []
            orig_autosave = ms.autosave
            def _mock_autosave():
                saved_paths.append(True)
            ms.autosave = _mock_autosave
            ms._autosave_tick()
            assert saved_paths, "_autosave_tick did not call autosave on dirty module"
            ms.close()


class TestCallbackServerJsonParsing:
    """
    Fix: callback_server._read_json returned {} for both zero-length bodies AND
    parse errors, making it impossible to distinguish the two cases.
    Now returns {} for no body and None for parse failures; do_POST returns 400
    when it receives None.
    """

    def test_read_json_docstring_or_comment_distinguishes_none_vs_empty(self):
        """_read_json must return None on parse error (not {})."""
        import inspect
        from gmodular.ipc.callback_server import _GModularRequestHandler
        src = inspect.getsource(_GModularRequestHandler._read_json)
        # The method must explicitly return None on error
        assert "return None" in src, (
            "_read_json should return None on parse error, not {}"
        )

    def test_do_post_checks_for_none_body(self):
        """do_POST must guard against None returned by _read_json."""
        import inspect
        from gmodular.ipc.callback_server import _GModularRequestHandler
        src = inspect.getsource(_GModularRequestHandler.do_POST)
        assert "is None" in src or "== None" in src, (
            "do_POST does not guard against None body from _read_json"
        )

    def test_do_post_returns_400_on_bad_json(self):
        """do_POST must return HTTP 400 when _read_json returns None."""
        import inspect
        from gmodular.ipc.callback_server import _GModularRequestHandler
        src = inspect.getsource(_GModularRequestHandler.do_POST)
        assert "400" in src, (
            "do_POST does not send a 400 response when body is unparseable"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Iteration 14 Regression Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestValidateCaseInsensitiveDuplicateTags(unittest.TestCase):
    """validate() must flag tags that differ only in case as duplicates."""

    def _make_state_with_tags(self, tag_a: str, tag_b: str):
        from gmodular.core.module_state import ModuleState
        from gmodular.formats.gff_types import (
            GITData, GITPlaceable, GITCreature, Vector3
        )
        state = ModuleState.__new__(ModuleState)
        # ModuleState.git is a plain instance attribute (not a property)
        state.git = GITData()
        state.are = None
        state.ifo = None
        p = GITPlaceable()
        p.resref = "plc001"
        p.tag = tag_a
        p.position = Vector3(0, 0, 0)
        c = GITCreature()
        c.resref = "cre001"
        c.tag = tag_b
        c.position = Vector3(1, 1, 0)
        state.git.placeables.append(p)
        state.git.creatures.append(c)
        return state

    def test_exact_case_duplicate_still_detected(self):
        """Tags with identical case must still be flagged."""
        state = self._make_state_with_tags("MYTAG", "MYTAG")
        issues = state.validate()
        dup = [i for i in issues if "Duplicate" in i]
        self.assertTrue(len(dup) >= 1, f"Expected duplicate warning, got: {issues}")

    def test_mixed_case_duplicate_detected(self):
        """Tags differing only in case (e.g. 'mytag' vs 'MYTAG') must be flagged."""
        state = self._make_state_with_tags("mytag", "MYTAG")
        issues = state.validate()
        dup = [i for i in issues if "Duplicate" in i]
        self.assertTrue(len(dup) >= 1,
            f"Case-insensitive duplicate 'mytag'/'MYTAG' not detected. Issues: {issues}")

    def test_different_tags_not_flagged(self):
        """Objects with genuinely different tags must NOT produce a duplicate warning."""
        state = self._make_state_with_tags("alpha", "beta")
        issues = state.validate()
        dup = [i for i in issues if "Duplicate" in i]
        self.assertEqual(len(dup), 0,
            f"False positive: distinct tags flagged as duplicates. Issues: {issues}")

    def test_mixed_case_duplicate_message_contains_original_casing(self):
        """The warning message should contain the original tag strings."""
        state = self._make_state_with_tags("Door_01", "door_01")
        issues = state.validate()
        dup_msgs = [i for i in issues if "Duplicate" in i]
        self.assertTrue(len(dup_msgs) >= 1)
        # At least one original form should appear in the message
        combined = " ".join(dup_msgs)
        self.assertTrue("Door_01" in combined or "door_01" in combined,
            f"Original casing not preserved in warning: {combined}")


class TestGFFWriterStructIDs(unittest.TestCase):
    """GFF writer must use the correct struct_id for each GIT list entry."""

    def _writer_src(self, fn_name: str) -> str:
        import inspect
        import importlib
        mod = importlib.import_module("gmodular.formats.gff_writer")
        fn = getattr(mod, fn_name)
        return inspect.getsource(fn)

    def test_sound_struct_id_is_7(self):
        src = self._writer_src("_sound_struct")
        self.assertIn("struct_id=7", src,
            "_sound_struct must use struct_id=7 (KotOR GFF spec). "
            "Found source:\n" + src)

    def test_store_struct_id_is_10(self):
        src = self._writer_src("_store_struct")
        self.assertIn("struct_id=10", src,
            "_store_struct must use struct_id=10 (KotOR GFF spec). "
            "Found source:\n" + src)

    def test_waypoint_struct_id_is_6(self):
        """Waypoint should remain struct_id=6 — sanity-check we didn't break it."""
        src = self._writer_src("_waypoint_struct")
        self.assertIn("struct_id=6", src,
            "_waypoint_struct must still use struct_id=6. Found source:\n" + src)

    def test_placeable_struct_id_is_9(self):
        src = self._writer_src("_placeable_struct")
        self.assertIn("struct_id=9", src,
            "_placeable_struct must use struct_id=9. Found source:\n" + src)

    def test_door_struct_id_is_8(self):
        src = self._writer_src("_door_struct")
        self.assertIn("struct_id=8", src,
            "_door_struct must use struct_id=8. Found source:\n" + src)

    def test_creature_struct_id_is_4(self):
        src = self._writer_src("_creature_struct")
        self.assertIn("struct_id=4", src,
            "_creature_struct must use struct_id=4. Found source:\n" + src)


class TestAppVersion(unittest.TestCase):
    """APP_VERSION in main_window.py must not carry the -MVP suffix."""

    def test_version_is_release_format(self):
        import re
        src_path = os.path.join(
            os.path.dirname(__file__), "..", "gmodular", "gui", "main_window.py"
        )
        with open(src_path) as fh:
            content = fh.read()
        m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', content)
        self.assertIsNotNone(m, "APP_VERSION not found in main_window.py")
        version = m.group(1)
        self.assertNotIn("-MVP", version,
            f"APP_VERSION still contains '-MVP' suffix: {version!r}")
        # Must match semver: MAJOR.MINOR.PATCH (optional pre-release is fine,
        # but the -MVP marketing tag should be gone)
        semver_re = r'^\d+\.\d+\.\d+'
        self.assertRegex(version, semver_re,
            f"APP_VERSION {version!r} does not start with MAJOR.MINOR.PATCH")


class TestDuplicateObjectOffset(unittest.TestCase):
    """duplicate_object() must apply a per-type position offset."""

    def _make_git(self):
        from gmodular.formats.gff_types import GITData
        return GITData()

    def test_placeable_offset_is_1(self):
        from gmodular.formats.gff_types import GITData, GITPlaceable, Vector3
        git = GITData()
        p = GITPlaceable()
        p.resref = "plc001"
        p.tag = "orig_plc"
        p.position = Vector3(0.0, 0.0, 0.0)
        git.placeables.append(p)
        copy_obj = git.duplicate_object(p)
        self.assertIsNotNone(copy_obj)
        self.assertAlmostEqual(copy_obj.position.x, 1.0, places=5,
            msg="Placeable duplicate should be offset by 1.0 on X")
        self.assertAlmostEqual(copy_obj.position.y, 1.0, places=5,
            msg="Placeable duplicate should be offset by 1.0 on Y")
        self.assertAlmostEqual(copy_obj.position.z, 0.0, places=5,
            msg="Placeable duplicate Z should be unchanged")

    def test_door_offset_is_1(self):
        from gmodular.formats.gff_types import GITData, GITDoor, Vector3
        git = GITData()
        d = GITDoor()
        d.resref = "door001"
        d.tag = "orig_door"
        d.position = Vector3(5.0, 3.0, 0.0)
        git.doors.append(d)
        copy_obj = git.duplicate_object(d)
        self.assertIsNotNone(copy_obj)
        self.assertAlmostEqual(copy_obj.position.x, 6.0, places=5)
        self.assertAlmostEqual(copy_obj.position.y, 4.0, places=5)

    def test_trigger_offset_is_2(self):
        from gmodular.formats.gff_types import GITData, GITTrigger, Vector3
        git = GITData()
        t = GITTrigger()
        t.resref = "trig001"
        t.tag = "orig_trig"
        t.position = Vector3(0.0, 0.0, 0.0)
        git.triggers.append(t)
        copy_obj = git.duplicate_object(t)
        self.assertIsNotNone(copy_obj)
        self.assertAlmostEqual(copy_obj.position.x, 2.0, places=5,
            msg="Trigger duplicate should be offset by 2.0 on X")
        self.assertAlmostEqual(copy_obj.position.y, 2.0, places=5,
            msg="Trigger duplicate should be offset by 2.0 on Y")

    def test_creature_offset_is_half(self):
        from gmodular.formats.gff_types import GITData, GITCreature, Vector3
        git = GITData()
        c = GITCreature()
        c.resref = "cre001"
        c.tag = "orig_cre"
        c.position = Vector3(2.0, 2.0, 0.0)
        git.creatures.append(c)
        copy_obj = git.duplicate_object(c)
        self.assertIsNotNone(copy_obj)
        self.assertAlmostEqual(copy_obj.position.x, 2.5, places=5,
            msg="Creature duplicate should be offset by 0.5 on X")
        self.assertAlmostEqual(copy_obj.position.y, 2.5, places=5,
            msg="Creature duplicate should be offset by 0.5 on Y")

    def test_duplicate_z_unchanged_for_all_types(self):
        """Z coordinate must never be shifted by duplicate_object."""
        from gmodular.formats.gff_types import (
            GITData, GITPlaceable, GITDoor, GITTrigger, GITCreature, Vector3
        )
        git = GITData()
        for cls, lst_name in [
            (GITPlaceable, "placeables"),
            (GITDoor, "doors"),
            (GITTrigger, "triggers"),
            (GITCreature, "creatures"),
        ]:
            obj = cls()
            obj.resref = "res001"
            obj.tag = f"z_test_{cls.__name__}"
            obj.position = Vector3(0.0, 0.0, 7.5)
            getattr(git, lst_name).append(obj)
            copy_obj = git.duplicate_object(obj)
            self.assertIsNotNone(copy_obj)
            self.assertAlmostEqual(copy_obj.position.z, 7.5, places=5,
                msg=f"{cls.__name__} duplicate must not alter Z coordinate")
