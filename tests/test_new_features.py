"""
GModular — Tests for new features (Iteration 20+):
  - P6: Module Packager (ERFWriter, ModPackager, dependency walker, validation)
  - P4: Patrol Waypoint Linker (naming, renumbering)
  - P8: 2DA Loader (parse, lookup, options)
  - P1: Room Assembly Grid (LYT generation, VIS generation)
  - IPC: Bridge port constants match blueprint

Run with:  python -m pytest tests/test_new_features.py -v
"""
from __future__ import annotations
import os
import sys
import struct
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest



# ─────────────────────────────────────────────────────────────────────────────
#  P6: ERF/MOD Writer
# ─────────────────────────────────────────────────────────────────────────────

class TestERFWriter:
    """Binary ERF/MOD writer produces valid archives."""

    def setup_method(self):
        from gmodular.formats.mod_packager import ERFWriter, PackageResource
        self.ERFWriter = ERFWriter
        self.PackageResource = PackageResource

    def _make_res(self, resref: str, ext: str, data: bytes) -> "PackageResource":
        from gmodular.formats.archives import EXT_TO_TYPE
        type_id = EXT_TO_TYPE.get(ext, 0)
        return self.PackageResource(resref=resref, res_type=type_id, ext=ext, data=data)

    def test_empty_archive_has_valid_header(self):
        writer = self.ERFWriter("MOD ")
        data = writer.to_bytes()
        # Header is 156 bytes: FileType(4)+Version(4)+8I(32)+Pad(116)
        assert len(data) >= 156, f"Empty MOD header should be at least 156 bytes, got {len(data)}"
        # FileType
        assert data[0:4] == b"MOD ", "File type should be 'MOD '"
        # Version
        assert data[4:8] == b"V1.0", "Version should be V1.0"
        # EntryCount = 0
        entry_count = struct.unpack_from("<I", data, 0x10)[0]
        assert entry_count == 0

    def test_single_resource_archive(self):
        writer = self.ERFWriter("MOD ")
        content = b"test script content"
        res = self._make_res("myscript", "ncs", content)
        writer.add(res)
        data = writer.to_bytes()
        # Header
        assert data[0:4] == b"MOD "
        assert data[4:8] == b"V1.0"
        # EntryCount = 1
        entry_count = struct.unpack_from("<I", data, 0x10)[0]
        assert entry_count == 1
        # Resource data is embedded
        assert content in data

    def test_multiple_resources(self):
        writer = self.ERFWriter("MOD ")
        resources = [
            ("module1", "are", b"ARE data"),
            ("module1", "ifo", b"IFO data"),
            ("module1", "git", b"GIT data"),
            ("myscript", "ncs", b"NCS bytecode"),
        ]
        for resref, ext, content in resources:
            writer.add(self._make_res(resref, ext, content))
        data = writer.to_bytes()
        entry_count = struct.unpack_from("<I", data, 0x10)[0]
        assert entry_count == 4
        for _, _, content in resources:
            assert content in data

    def test_resref_truncated_to_16_chars(self):
        writer = self.ERFWriter("MOD ")
        # 20-char name truncated to 16 - fills entire 16-byte field with 'a'
        writer.add(self._make_res("a" * 20, "ncs", b"data"))
        data = writer.to_bytes()
        key_start = 156
        resref_bytes = data[key_start:key_start + 16]
        assert resref_bytes[:16] == b"aaaaaaaaaaaaaaaa", \
            f"ResRef should be truncated to 16 'a' chars, got {resref_bytes!r}"

    def test_resref_short_is_null_padded(self):
        writer = self.ERFWriter("MOD ")
        writer.add(self._make_res("hello", "ncs", b"data"))
        data = writer.to_bytes()
        key_start = 156
        resref_bytes = data[key_start:key_start + 16]
        assert resref_bytes[:5] == b"hello"
        assert resref_bytes[5:] == b"\x00" * 11, "Short ResRef should be null-padded"
    def test_write_to_file(self, tmp_path):
        writer = self.ERFWriter("MOD ")
        writer.add(self._make_res("test", "git", b"git content"))
        output = tmp_path / "test.mod"
        writer.write(output)
        assert output.exists()
        assert output.stat().st_size > 160
        # Verify header
        header = output.read_bytes()[:8]
        assert header == b"MOD V1.0"

    def test_erf_type(self):
        writer = self.ERFWriter("ERF ")
        writer.add(self._make_res("res", "are", b"data"))
        data = writer.to_bytes()
        assert data[0:4] == b"ERF "


# ─────────────────────────────────────────────────────────────────────────────
#  P6: ModPackager validation
# ─────────────────────────────────────────────────────────────────────────────

class TestModPackagerValidation:
    """ModPackager validation rules."""

    def _make_git(self):
        from gmodular.formats.gff_types import GITData, GITCreature, GITDoor, GITWaypoint
        git = GITData()
        return git

    def _make_packager(self, git, module_dir="", module_name="testmod"):
        from gmodular.formats.mod_packager import ModPackager
        return ModPackager(
            module_dir=module_dir or tempfile.mkdtemp(),
            module_name=module_name,
            git=git,
            are=None,
            ifo=None,
        )

    def test_no_git_returns_error(self):
        from gmodular.formats.mod_packager import ModPackager, ERROR
        packager = ModPackager(
            module_dir=tempfile.mkdtemp(),
            module_name="test",
            git=None, are=None, ifo=None,
        )
        issues = packager.validate_only()
        errors = [i for i in issues if i.severity == ERROR]
        assert errors, "No GIT should produce an error"

    def test_duplicate_tag_raises_error(self):
        from gmodular.formats.gff_types import GITData, GITCreature, GITPlaceable
        from gmodular.formats.mod_packager import ERROR
        git = GITData()
        c = GITCreature(); c.tag = "same_tag"; c.resref = "cre001"
        p = GITPlaceable(); p.tag = "same_tag"; p.resref = "plc001"
        git.creatures.append(c)
        git.placeables.append(p)
        packager = self._make_packager(git)
        issues = packager.validate_only()
        errors = [i for i in issues if i.severity == ERROR and "same_tag" in i.message]
        assert errors, "Duplicate tag should produce an error"

    def test_long_resref_raises_error(self):
        from gmodular.formats.gff_types import GITData, GITCreature
        from gmodular.formats.mod_packager import ERROR
        git = GITData()
        c = GITCreature()
        c.tag = "ok"
        c.resref = "a" * 17   # 17 chars, over limit
        git.creatures.append(c)
        packager = self._make_packager(git)
        issues = packager.validate_only()
        errors = [i for i in issues if i.severity == ERROR and "17" in i.message]
        assert errors, "ResRef >16 chars should produce an error"

    def test_valid_module_passes(self):
        from gmodular.formats.gff_types import GITData, GITCreature
        from gmodular.formats.mod_packager import ERROR
        git = GITData()
        c = GITCreature(); c.tag = "npc001"; c.resref = "creature01"
        git.creatures.append(c)
        packager = self._make_packager(git)
        issues = packager.validate_only()
        errors = [i for i in issues if i.severity == ERROR]
        assert not errors, f"Valid module should not have errors: {errors}"

    def test_empty_module_has_info(self):
        from gmodular.formats.gff_types import GITData
        from gmodular.formats.mod_packager import INFO
        git = GITData()
        packager = self._make_packager(git)
        issues = packager.validate_only()
        infos = [i for i in issues if i.severity == INFO]
        assert infos, "Empty module should produce an info note"

    def test_door_linked_to_nonexistent_warns(self):
        from gmodular.formats.gff_types import GITData, GITDoor
        from gmodular.formats.mod_packager import WARNING
        git = GITData()
        d = GITDoor(); d.tag = "door01"; d.resref = "dr001"
        d.linked_to = "nonexistent_door"
        git.doors.append(d)
        packager = self._make_packager(git)
        issues = packager.validate_only()
        warnings = [i for i in issues if i.severity == WARNING and "linked" in i.message.lower()]
        assert warnings, "Invalid LinkedTo should warn"


# ─────────────────────────────────────────────────────────────────────────────
#  P6: Dependency Walker
# ─────────────────────────────────────────────────────────────────────────────

class TestDependencyWalker:
    """_get_all_resrefs collects the right dependencies."""

    def test_creature_with_scripts(self):
        from gmodular.formats.gff_types import GITData, GITCreature
        from gmodular.formats.mod_packager import _get_all_resrefs
        git = GITData()
        c = GITCreature()
        c.resref = "rodian01"
        c.on_spawn = "k_ai_master"
        c.on_death = "k_hen_death"
        git.creatures.append(c)
        deps = _get_all_resrefs(git)
        resrefs = [r for r, _ in deps]
        exts = [e for _, e in deps]
        assert "rodian01" in resrefs
        assert "k_ai_master" in resrefs
        assert "k_hen_death" in resrefs
        # Check utc type for blueprint
        utc_entries = [(r, e) for r, e in deps if r == "rodian01"]
        assert ("rodian01", "utc") in utc_entries

    def test_placeable_with_scripts(self):
        from gmodular.formats.gff_types import GITData, GITPlaceable
        from gmodular.formats.mod_packager import _get_all_resrefs
        git = GITData()
        p = GITPlaceable()
        p.resref = "chest01"
        p.on_used = "open_chest"
        git.placeables.append(p)
        deps = _get_all_resrefs(git)
        assert ("chest01", "utp") in deps
        assert ("open_chest", "ncs") in deps

    def test_no_duplicates(self):
        from gmodular.formats.gff_types import GITData, GITCreature
        from gmodular.formats.mod_packager import _get_all_resrefs
        git = GITData()
        for i in range(3):
            c = GITCreature(); c.resref = "same"; c.on_spawn = "samescript"
            git.creatures.append(c)
        deps = _get_all_resrefs(git)
        resrefs = [r for r, _ in deps]
        # Each unique (resref, ext) pair appears only once
        assert len(set(deps)) == len(deps), "Should have no duplicates"

    def test_empty_scripts_excluded(self):
        from gmodular.formats.gff_types import GITData, GITCreature
        from gmodular.formats.mod_packager import _get_all_resrefs
        git = GITData()
        c = GITCreature(); c.resref = "cre01"
        # All script fields empty
        git.creatures.append(c)
        deps = _get_all_resrefs(git)
        # Only the blueprint resref, no empty script entries
        empty = [(r, e) for r, e in deps if not r]
        assert not empty, "Empty resrefs should not appear"


# ─────────────────────────────────────────────────────────────────────────────
#  P8: 2DA Loader
# ─────────────────────────────────────────────────────────────────────────────

class TestTwoDALoader:
    """TwoDALoader parses and returns correct values."""

    SAMPLE_2DA = """\
2DA V2.0

LABEL  RACE  GENDER
0  "Commoner"  1  0
1  "Soldier"   1  0
2  "Rodian"    4  0
3  "Wookiee"   2  0
47  "Gamorrean"  5  1
"""

    def setup_method(self):
        from gmodular.formats.twoda_loader import TwoDALoader, TwoDATable
        self.TwoDALoader = TwoDALoader
        self.TwoDATable = TwoDATable

    def test_parse_sample(self):
        loader = self.TwoDALoader()
        table = loader.load_from_text("appearance", self.SAMPLE_2DA)
        assert table is not None
        assert len(table) == 5  # 5 rows

    def test_get_label(self):
        loader = self.TwoDALoader()
        table = loader.load_from_text("appearance", self.SAMPLE_2DA)
        assert table.get_label(0) == "Commoner"
        assert table.get_label(2) == "Rodian"
        assert table.get_label(47) == "Gamorrean"

    def test_get_label_missing_row(self):
        loader = self.TwoDALoader()
        table = loader.load_from_text("appearance", self.SAMPLE_2DA)
        # Row 99 doesn't exist
        label = table.get_label(99)
        # get_label for missing row returns "Row N" or ""
        assert label == "" or "99" in label

    def test_get_options_returns_sorted_list(self):
        loader = self.TwoDALoader()
        table = loader.load_from_text("appearance", self.SAMPLE_2DA)
        opts = table.options()
        # Should include all 5 rows
        assert len(opts) == 5
        # Should be sorted by row index
        indices = [idx for idx, _ in opts]
        assert indices == sorted(indices)

    def test_options_contain_row_number(self):
        loader = self.TwoDALoader()
        table = loader.load_from_text("appearance", self.SAMPLE_2DA)
        opts = table.options()
        # Each display string should contain the row index
        for idx, display in opts:
            assert str(idx) in display

    def test_get_name_via_loader(self):
        loader = self.TwoDALoader()
        loader.load_from_text("appearance", self.SAMPLE_2DA)
        name = loader.get_name("appearance", 2)
        assert name == "Rodian"

    def test_get_options_via_loader(self):
        loader = self.TwoDALoader()
        loader.load_from_text("appearance", self.SAMPLE_2DA)
        opts = loader.get_options("appearance")
        assert len(opts) == 5

    def test_unknown_table_returns_empty(self):
        loader = self.TwoDALoader()
        opts = loader.get_options("nonexistent")
        assert opts == []

    def test_get_column_value(self):
        loader = self.TwoDALoader()
        table = loader.load_from_text("appearance", self.SAMPLE_2DA)
        # Get RACE column for row 3 (Wookiee)
        race = table.get(3, "RACE")
        assert race == "2"

    def test_is_loaded(self):
        loader = self.TwoDALoader()
        assert not loader.is_loaded("appearance")
        loader.load_from_text("appearance", self.SAMPLE_2DA)
        assert loader.is_loaded("appearance")
        assert loader.is_loaded("APPEARANCE")  # case-insensitive

    def test_fallback_tables_load(self):
        from gmodular.formats.twoda_loader import load_fallback_tables, get_2da_loader
        # Reset loader
        import gmodular.formats.twoda_loader as m
        m._loader = None
        load_fallback_tables()
        loader = get_2da_loader()
        assert loader.is_loaded("faction")
        assert loader.is_loaded("gender")
        assert loader.is_loaded("classes")

    def test_parse_handles_quoted_strings(self):
        sample = """\
2DA V2.0

LABEL
0  "Some Long Name"
1  Simple
"""
        loader = self.TwoDALoader()
        table = loader.load_from_text("test", sample)
        assert table.get_label(0) == "Some Long Name"
        assert table.get_label(1) == "Simple"


# ─────────────────────────────────────────────────────────────────────────────
#  P4: Patrol Waypoint Naming
# ─────────────────────────────────────────────────────────────────────────────

class TestPatrolNaming:
    """Patrol waypoint auto-naming follows WP_[TAG]_NN convention."""

    def test_wp_name_format(self):
        from gmodular.gui.patrol_editor import _wp_name
        assert _wp_name("rodian01", 1) == "WP_RODIAN01_01"
        assert _wp_name("rodian01", 2) == "WP_RODIAN01_02"
        assert _wp_name("rodian01", 10) == "WP_RODIAN01_10"

    def test_wp_name_uppercase(self):
        from gmodular.gui.patrol_editor import _wp_name
        # Tag is always uppercased
        assert _wp_name("myNpc", 1) == "WP_MYNPC_01"

    def test_resref_truncated_to_16(self):
        from gmodular.gui.patrol_editor import _resref_from_tag
        # Generated resref must not exceed 16 chars
        resref = _resref_from_tag("verylongtagname1234567", 1)
        assert len(resref) <= 16

    def test_resref_lowercase(self):
        from gmodular.gui.patrol_editor import _resref_from_tag
        resref = _resref_from_tag("Rodian01", 1)
        assert resref == resref.lower()



class TestRoomAssemblyGrid:
    """Room grid generates valid LYT and VIS."""

    def test_lyt_empty(self):
        from gmodular.gui.room_assembly import LYTData, RoomInstance
        lyt = LYTData(rooms=[])
        text = lyt.to_text()
        assert "roomcount 0" in text
        assert "filedependency" in text

    def test_lyt_with_rooms(self):
        from gmodular.gui.room_assembly import LYTData, RoomInstance
        rooms = [
            RoomInstance("room_a", 0, 0, 0.0, 0.0, 0.0),
            RoomInstance("room_b", 1, 0, 10.0, 0.0, 0.0),
        ]
        lyt = LYTData(rooms=rooms)
        text = lyt.to_text()
        assert "roomcount 2" in text
        assert "room_a" in text
        assert "room_b" in text
        assert "10.00" in text

    def test_vis_adjacent_rooms(self):
        from gmodular.gui.room_assembly import _generate_vis, RoomInstance
        rooms = [
            RoomInstance("room_a", 0, 0, 0.0, 0.0, 0.0),
            RoomInstance("room_b", 1, 0, 10.0, 0.0, 0.0),
        ]
        vis = _generate_vis(rooms)
        # Both rooms should appear in vis
        assert "room_a" in vis
        assert "room_b" in vis

    def test_vis_non_adjacent_rooms(self):
        from gmodular.gui.room_assembly import _generate_vis, RoomInstance
        rooms = [
            RoomInstance("room_a", 0, 0),
            RoomInstance("room_b", 5, 5),  # Far away, not adjacent
        ]
        vis = _generate_vis(rooms)
        # room_a should be listed but may not see room_b
        lines_for_a = vis.split('\n')
        idx_a = next((i for i, l in enumerate(lines_for_a) if l.strip() == "room_a"), -1)
        assert idx_a >= 0, "room_a should appear in vis"

    def test_room_naming(self):
        from gmodular.gui.room_assembly import RoomInstance
        r = RoomInstance("manm26aa", 2, 3, 20.0, 30.0, 0.0)
        assert r.mdl_name == "manm26aa"
        assert r.grid_x == 2
        assert r.grid_y == 3

    def test_lyt_world_coordinates(self):
        """LYT world coordinates are floats with 2 decimal places."""
        from gmodular.gui.room_assembly import LYTData, RoomInstance
        rooms = [RoomInstance("r", 0, 0, 1.5, 2.7, -0.1)]
        lyt = LYTData(rooms=rooms)
        text = lyt.to_text()
        assert "1.50" in text
        assert "2.70" in text
        assert "-0.10" in text


# ─────────────────────────────────────────────────────────────────────────────
#  IPC: Port Constants
# ─────────────────────────────────────────────────────────────────────────────

class TestIPCPorts:
    """IPC port constants match PIPELINE_SPEC v1.0."""

    def test_ghostscripter_port(self):
        from gmodular.ipc.bridges import GHOSTSCRIPTER_PORT
        assert GHOSTSCRIPTER_PORT == 7002, \
            "GhostScripter must be on port 7002 per PIPELINE_SPEC"

    def test_ghostrigger_port(self):
        from gmodular.ipc.bridges import GHOSTRIGGER_PORT
        assert GHOSTRIGGER_PORT == 7001, \
            "GhostRigger must be on port 7001 per PIPELINE_SPEC"

    def test_gmodular_port(self):
        from gmodular.ipc.bridges import GMODULAR_PORT
        assert GMODULAR_PORT == 7003, \
            "GModular callback server must be on port 7003 per PIPELINE_SPEC"

    def test_callback_server_port(self):
        from gmodular.ipc.callback_server import GMODULAR_CALLBACK_PORT
        assert GMODULAR_CALLBACK_PORT == 7003

    def test_ghostrigger_bridge_has_open_blueprint(self):
        """GhostRiggerBridge must have open_blueprint, open_utc, open_utp, open_utd."""
        from gmodular.ipc.bridges import GhostRiggerBridge
        assert hasattr(GhostRiggerBridge, "open_blueprint")
        assert hasattr(GhostRiggerBridge, "open_utc")
        assert hasattr(GhostRiggerBridge, "open_utp")
        assert hasattr(GhostRiggerBridge, "open_utd")

    def test_ghostscripter_bridge_has_open_script(self):
        from gmodular.ipc.bridges import GhostScripterBridge
        assert hasattr(GhostScripterBridge, "open_script")
        assert hasattr(GhostScripterBridge, "compile_script")


# ─────────────────────────────────────────────────────────────────────────────
#  P6: MOD Packager build with temp files
# ─────────────────────────────────────────────────────────────────────────────

class TestModPackagerBuild:
    """ModPackager.build() produces a valid .mod file."""

    def _make_git_with_creature(self):
        from gmodular.formats.gff_types import GITData, GITCreature
        git = GITData()
        c = GITCreature()
        c.resref = "rodian01"
        c.tag = "rodian_guard"
        c.on_spawn = "k_ai_master"
        git.creatures.append(c)
        return git

    def test_build_creates_file(self, tmp_path):
        from gmodular.formats.mod_packager import ModPackager
        git = self._make_git_with_creature()
        # Write minimal core files so packager finds them
        module_dir = tmp_path / "module"
        module_dir.mkdir()
        (module_dir / "testmod.are").write_bytes(b"ARE dummy")
        (module_dir / "testmod.ifo").write_bytes(b"IFO dummy")
        (module_dir / "testmod.git").write_bytes(b"GIT dummy")
        (module_dir / "testmod.lyt").write_bytes(b"LYT dummy")

        packager = ModPackager(
            module_dir=module_dir,
            module_name="testmod",
            git=git, are=None, ifo=None,
        )
        output = tmp_path / "testmod.mod"
        result = packager.build(output)

        # Should succeed (warnings for missing scripts are OK)
        assert result.success, f"Build failed: {result.summary()}"
        assert output.exists()
        assert output.stat().st_size > 160
        # Verify MOD header
        header = output.read_bytes()[:8]
        assert header == b"MOD V1.0", f"Bad header: {header!r}"

    def test_build_packs_core_files(self, tmp_path):
        from gmodular.formats.mod_packager import ModPackager
        from gmodular.formats.gff_types import GITData
        git = GITData()
        module_dir = tmp_path / "module"
        module_dir.mkdir()
        core_content = {
            "testmod.are": b"ARE V3.2",
            "testmod.ifo": b"IFO V3.2",
            "testmod.git": b"GIT V3.2",
        }
        for name, content in core_content.items():
            (module_dir / name).write_bytes(content)

        packager = ModPackager(
            module_dir=module_dir,
            module_name="testmod",
            git=git, are=None, ifo=None,
        )
        output = tmp_path / "testmod.mod"
        result = packager.build(output)
        assert result.success
        assert result.resources_packed >= 3

    def test_build_result_has_resource_list(self, tmp_path):
        from gmodular.formats.mod_packager import ModPackager
        from gmodular.formats.gff_types import GITData
        git = GITData()
        module_dir = tmp_path / "module"
        module_dir.mkdir()
        (module_dir / "m.are").write_bytes(b"data")
        packager = ModPackager(
            module_dir=module_dir, module_name="m",
            git=git, are=None, ifo=None,
        )
        result = packager.build(tmp_path / "m.mod")
        # resource_list may be empty if only warnings, but it's always a list
        assert isinstance(result.resource_list, list)

    def test_validate_only_does_not_create_file(self, tmp_path):
        from gmodular.formats.mod_packager import ModPackager
        from gmodular.formats.gff_types import GITData
        git = GITData()
        packager = ModPackager(
            module_dir=tmp_path, module_name="test",
            git=git, are=None, ifo=None,
        )
        output = tmp_path / "test.mod"
        issues = packager.validate_only()
        assert not output.exists(), "validate_only should not create a file"
        assert isinstance(issues, list)
