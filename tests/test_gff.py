"""
GModular — GFF V3.2 Writer / Reader Round-Trip Tests
Run with:  python -m pytest tests/ -v
"""
from __future__ import annotations
import math
import struct
import tempfile
import os
import sys

# Make sure we can import gmodular from the project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from gmodular.formats.gff_types import (
    GFFFieldType, GFFField, GFFStruct, GFFRoot,
    GITData, GITPlaceable, GITCreature, GITDoor,
    GITTrigger, GITSoundObject, GITWaypoint, GITStoreObject,
    AREData, IFOData, Vector3, Quaternion,
)
from gmodular.formats.gff_writer import GFFWriter, save_git
from gmodular.formats.gff_reader import GFFReader, load_git


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _write_read(root: GFFRoot) -> GFFRoot:
    """Round-trip a GFFRoot through writer → bytes → reader."""
    writer = GFFWriter(root)
    data   = writer.to_bytes()
    reader = GFFReader.from_bytes(data)
    return reader.parse()


def _make_simple_root(**fields) -> GFFRoot:
    root = GFFRoot(file_type="TEST")
    root.struct_id = 0xFFFFFFFF
    for label, (ft, value) in fields.items():
        root.fields[label] = GFFField(label, ft, value)
    return root


# ─────────────────────────────────────────────────────────────────────────────
#  Header / binary layout tests
# ─────────────────────────────────────────────────────────────────────────────

class TestHeader:
    def test_header_size(self):
        root = GFFRoot(file_type="GIT ")
        root.struct_id = 0xFFFFFFFF
        data = GFFWriter(root).to_bytes()
        # Header must be 56 bytes; struct block starts at offset 56
        ft, fv, struct_off = struct.unpack_from("<4s4sI", data, 0)
        assert struct_off == 56, f"struct_off should be 56, got {struct_off}"

    def test_file_type_preserved(self):
        for ft in ("GIT ", "ARE ", "IFO ", "DLG "):
            root = GFFRoot(file_type=ft)
            root.struct_id = 0xFFFFFFFF
            data = GFFWriter(root).to_bytes()
            written_ft = data[:4].decode("ascii")
            assert written_ft == ft, f"Expected {ft!r}, got {written_ft!r}"

    def test_version_is_v32(self):
        root = GFFRoot(file_type="GIT ")
        root.struct_id = 0xFFFFFFFF
        data = GFFWriter(root).to_bytes()
        version = data[4:8].decode("ascii")
        assert version == "V3.2"

    def test_empty_root_struct(self):
        root = GFFRoot(file_type="TEST")
        root.struct_id = 0xFFFFFFFF
        data = GFFWriter(root).to_bytes()
        # Should have exactly 1 struct (root), 0 fields
        (ft, fv, struct_off, struct_count, field_off, field_count,
         *_) = struct.unpack_from("<4s4s12I", data, 0)
        assert struct_count == 1
        assert field_count == 0


# ─────────────────────────────────────────────────────────────────────────────
#  Scalar field round-trip tests
# ─────────────────────────────────────────────────────────────────────────────

class TestScalarFields:
    def test_byte(self):
        root = _make_simple_root(Val=(GFFFieldType.BYTE, 200))
        r2 = _write_read(root)
        assert r2.fields["Val"].value == 200

    def test_word(self):
        root = _make_simple_root(Val=(GFFFieldType.WORD, 65000))
        r2 = _write_read(root)
        assert r2.fields["Val"].value == 65000

    def test_dword(self):
        root = _make_simple_root(Val=(GFFFieldType.DWORD, 0xDEADBEEF))
        r2 = _write_read(root)
        assert r2.fields["Val"].value == 0xDEADBEEF

    def test_int(self):
        root = _make_simple_root(Val=(GFFFieldType.INT, -42))
        r2 = _write_read(root)
        assert r2.fields["Val"].value == -42

    def test_float(self):
        root = _make_simple_root(Val=(GFFFieldType.FLOAT, 3.14159))
        r2 = _write_read(root)
        assert abs(r2.fields["Val"].value - 3.14159) < 1e-5

    def test_double(self):
        root = _make_simple_root(Val=(GFFFieldType.DOUBLE, 2.718281828))
        r2 = _write_read(root)
        assert abs(r2.fields["Val"].value - 2.718281828) < 1e-9

    def test_dword64(self):
        root = _make_simple_root(Val=(GFFFieldType.DWORD64, 0xCAFEBABEDEAD1234))
        r2 = _write_read(root)
        assert r2.fields["Val"].value == 0xCAFEBABEDEAD1234

    def test_short(self):
        root = _make_simple_root(Val=(GFFFieldType.SHORT, -1000))
        r2 = _write_read(root)
        assert r2.fields["Val"].value == -1000

    def test_strref(self):
        root = _make_simple_root(Val=(GFFFieldType.STRREF, 12345))
        r2 = _write_read(root)
        assert r2.fields["Val"].value == 12345


# ─────────────────────────────────────────────────────────────────────────────
#  String field round-trip tests
# ─────────────────────────────────────────────────────────────────────────────

class TestStringFields:
    def test_cexostring(self):
        root = _make_simple_root(Name=(GFFFieldType.CEXOSTRING, "Hello KotOR!"))
        r2 = _write_read(root)
        assert r2.fields["Name"].value == "Hello KotOR!"

    def test_cexostring_empty(self):
        root = _make_simple_root(Name=(GFFFieldType.CEXOSTRING, ""))
        r2 = _write_read(root)
        assert r2.fields["Name"].value == ""

    def test_resref(self):
        root = _make_simple_root(ResRef=(GFFFieldType.RESREF, "chair001"))
        r2 = _write_read(root)
        assert r2.fields["ResRef"].value == "chair001"

    def test_resref_max_length(self):
        ref16 = "a" * 16
        root = _make_simple_root(ResRef=(GFFFieldType.RESREF, ref16))
        r2 = _write_read(root)
        assert r2.fields["ResRef"].value == ref16

    def test_cexolocstring(self):
        root = _make_simple_root(Name=(GFFFieldType.CEXOLOCSTRING, "Player Start"))
        r2 = _write_read(root)
        assert r2.fields["Name"].value == "Player Start"

    def test_multiple_string_fields(self):
        root = GFFRoot(file_type="TEST")
        root.struct_id = 0xFFFFFFFF
        root.fields["Tag"]    = GFFField("Tag",    GFFFieldType.CEXOSTRING, "my_tag")
        root.fields["ResRef"] = GFFField("ResRef", GFFFieldType.RESREF,     "my_ref")
        root.fields["Name"]   = GFFField("Name",   GFFFieldType.CEXOLOCSTRING, "My Name")
        r2 = _write_read(root)
        assert r2.fields["Tag"].value == "my_tag"
        assert r2.fields["ResRef"].value == "my_ref"
        assert r2.fields["Name"].value == "My Name"


# ─────────────────────────────────────────────────────────────────────────────
#  Composite field round-trip tests
# ─────────────────────────────────────────────────────────────────────────────

class TestCompositeFields:
    def test_vector3(self):
        v = Vector3(1.5, 2.75, -3.0)
        root = _make_simple_root(Pos=(GFFFieldType.VECTOR, v))
        r2 = _write_read(root)
        v2 = r2.fields["Pos"].value
        assert isinstance(v2, Vector3)
        assert abs(v2.x - 1.5) < 1e-5
        assert abs(v2.y - 2.75) < 1e-5
        assert abs(v2.z + 3.0) < 1e-5

    def test_orientation_quaternion(self):
        q = Quaternion(0.1, 0.2, 0.3, 0.9)
        root = _make_simple_root(Orient=(GFFFieldType.ORIENTATION, q))
        r2 = _write_read(root)
        q2 = r2.fields["Orient"].value
        assert isinstance(q2, Quaternion)
        assert abs(q2.x - 0.1) < 1e-5
        assert abs(q2.w - 0.9) < 1e-5

    def test_void_data(self):
        blob = bytes(range(16))
        root = _make_simple_root(Data=(GFFFieldType.VOID, blob))
        r2 = _write_read(root)
        assert bytes(r2.fields["Data"].value) == blob

    def test_nested_struct(self):
        sub = GFFStruct(struct_id=42)
        sub.fields["X"] = GFFField("X", GFFFieldType.FLOAT, 9.9)
        root = GFFRoot(file_type="TEST")
        root.struct_id = 0xFFFFFFFF
        root.fields["Sub"] = GFFField("Sub", GFFFieldType.STRUCT, sub)
        r2 = _write_read(root)
        assert "Sub" in r2.fields
        sub2 = r2.fields["Sub"].value
        assert isinstance(sub2, GFFStruct)
        assert abs(sub2.fields["X"].value - 9.9) < 1e-5

    def test_list_field_empty(self):
        root = GFFRoot(file_type="TEST")
        root.struct_id = 0xFFFFFFFF
        root.fields["Items"] = GFFField("Items", GFFFieldType.LIST, [])
        r2 = _write_read(root)
        assert r2.fields["Items"].value == []

    def test_list_field_single(self):
        sub = GFFStruct(struct_id=1)
        sub.fields["Val"] = GFFField("Val", GFFFieldType.DWORD, 999)
        root = GFFRoot(file_type="TEST")
        root.struct_id = 0xFFFFFFFF
        root.fields["Items"] = GFFField("Items", GFFFieldType.LIST, [sub])
        r2 = _write_read(root)
        items = r2.fields["Items"].value
        assert len(items) == 1
        assert items[0].fields["Val"].value == 999

    def test_list_field_multiple(self):
        subs = []
        for i in range(5):
            s = GFFStruct(struct_id=i)
            s.fields["Index"] = GFFField("Index", GFFFieldType.DWORD, i * 10)
            subs.append(s)
        root = GFFRoot(file_type="TEST")
        root.struct_id = 0xFFFFFFFF
        root.fields["List"] = GFFField("List", GFFFieldType.LIST, subs)
        r2 = _write_read(root)
        items = r2.fields["List"].value
        assert len(items) == 5
        for i, item in enumerate(items):
            assert item.fields["Index"].value == i * 10

    def test_multiple_lists(self):
        """Root struct with several named LIST fields (like GIT)."""
        root = GFFRoot(file_type="TEST")
        root.struct_id = 0xFFFFFFFF
        for name in ("Alpha", "Beta", "Gamma"):
            subs = []
            for j in range(2):
                s = GFFStruct(struct_id=j)
                s.fields["Name"] = GFFField("Name", GFFFieldType.CEXOSTRING, f"{name}_{j}")
                subs.append(s)
            root.fields[name] = GFFField(name, GFFFieldType.LIST, subs)
        r2 = _write_read(root)
        for name in ("Alpha", "Beta", "Gamma"):
            items = r2.fields[name].value
            assert len(items) == 2, f"List {name!r} has {len(items)} items"
            assert items[0].fields["Name"].value == f"{name}_0"
            assert items[1].fields["Name"].value == f"{name}_1"


# ─────────────────────────────────────────────────────────────────────────────
#  High-level GIT round-trip tests
# ─────────────────────────────────────────────────────────────────────────────

def _make_full_git() -> GITData:
    git = GITData()

    p = GITPlaceable()
    p.resref = "chair001"; p.template_resref = "chair"; p.tag = "CHAIR_1"
    p.position = Vector3(1.5, 2.5, 0.0); p.bearing = 0.5
    p.on_used = "on_use_sc"; p.on_heartbeat = "on_hb_sc"; p.on_death = "on_die_sc"
    git.placeables.append(p)

    c = GITCreature()
    c.resref = "jedi001"; c.template_resref = "jedi"; c.tag = "JEDI_1"
    c.position = Vector3(3.0, 4.0, 0.0); c.bearing = 1.5707963
    c.on_spawn = "on_spawn_sc"; c.on_death = "on_death_sc"; c.on_notice = "on_notice_sc"
    git.creatures.append(c)

    d = GITDoor()
    d.resref = "door001"; d.template_resref = "metaldoor"; d.tag = "DOOR_1"
    d.position = Vector3(5.0, 0.0, 0.0); d.bearing = 0.0
    d.linked_to = "area002"; d.linked_to_flags = 1
    d.transition_destination = "trans_001"
    d.on_open = "on_open_sc"; d.on_closed = "on_closed_sc"
    git.doors.append(d)

    w = GITWaypoint()
    w.resref = "wp001"; w.template_resref = "waypoint"; w.tag = "WP_SPAWN"
    w.position = Vector3(0.0, 0.0, 0.0)
    w.map_note = "Player Start"; w.map_note_enabled = 1
    git.waypoints.append(w)

    t = GITTrigger()
    t.resref = "trig001"; t.template_resref = "trigger"; t.tag = "TRIG_1"
    t.position = Vector3(2.0, 2.0, 0.0)
    t.geometry = [Vector3(0, 0, 0), Vector3(1, 0, 0), Vector3(1, 1, 0), Vector3(0, 1, 0)]
    t.on_enter = "on_enter_sc"; t.on_exit = "on_exit_sc"
    git.triggers.append(t)

    return git


class TestGITRoundTrip:
    def setup_method(self):
        self.tmp = tempfile.mktemp(suffix=".git")

    def teardown_method(self):
        if os.path.exists(self.tmp):
            os.remove(self.tmp)

    def test_object_counts(self):
        git = _make_full_git()
        save_git(git, self.tmp)
        git2 = load_git(self.tmp)
        assert len(git2.placeables) == 1
        assert len(git2.creatures)  == 1
        assert len(git2.doors)      == 1
        assert len(git2.waypoints)  == 1
        assert len(git2.triggers)   == 1

    def test_placeable_fields(self):
        git = _make_full_git()
        save_git(git, self.tmp)
        git2 = load_git(self.tmp)
        p = git2.placeables[0]
        assert p.tag == "CHAIR_1"
        assert p.resref == "chair001"
        assert p.template_resref == "chair"
        assert abs(p.position.x - 1.5) < 1e-5
        assert abs(p.position.y - 2.5) < 1e-5
        assert abs(p.bearing  - 0.5)   < 1e-5
        assert p.on_used == "on_use_sc"
        assert p.on_heartbeat == "on_hb_sc"

    def test_creature_fields(self):
        git = _make_full_git()
        save_git(git, self.tmp)
        git2 = load_git(self.tmp)
        c = git2.creatures[0]
        assert c.tag == "JEDI_1"
        assert abs(c.bearing - 1.5707963) < 1e-5
        assert abs(c.position.x - 3.0) < 1e-5
        assert c.on_spawn == "on_spawn_sc"

    def test_door_fields(self):
        git = _make_full_git()
        save_git(git, self.tmp)
        git2 = load_git(self.tmp)
        d = git2.doors[0]
        assert d.tag == "DOOR_1"
        assert d.linked_to == "area002"
        assert d.transition_destination == "trans_001"
        assert d.on_open == "on_open_sc"

    def test_waypoint_fields(self):
        git = _make_full_git()
        save_git(git, self.tmp)
        git2 = load_git(self.tmp)
        w = git2.waypoints[0]
        assert w.tag == "WP_SPAWN"
        assert w.map_note == "Player Start"
        assert w.map_note_enabled == 1

    def test_trigger_geometry(self):
        git = _make_full_git()
        save_git(git, self.tmp)
        git2 = load_git(self.tmp)
        t = git2.triggers[0]
        assert t.tag == "TRIG_1"
        assert len(t.geometry) == 4
        assert abs(t.geometry[1].x - 1.0) < 1e-5
        assert t.on_enter == "on_enter_sc"
        assert t.on_exit  == "on_exit_sc"

    def test_multiple_objects_same_type(self):
        git = GITData()
        for i in range(3):
            p = GITPlaceable()
            p.resref = f"obj{i:03d}"; p.template_resref = "obj"; p.tag = f"OBJ_{i}"
            p.position = Vector3(float(i), 0, 0)
            git.placeables.append(p)
        save_git(git, self.tmp)
        git2 = load_git(self.tmp)
        assert len(git2.placeables) == 3
        tags = {p.tag for p in git2.placeables}
        assert tags == {"OBJ_0", "OBJ_1", "OBJ_2"}

    def test_empty_git(self):
        git = GITData()
        save_git(git, self.tmp)
        git2 = load_git(self.tmp)
        assert git2.object_count == 0

    def test_file_written_to_disk(self):
        git = _make_full_git()
        save_git(git, self.tmp)
        assert os.path.exists(self.tmp)
        assert os.path.getsize(self.tmp) > 100

    def test_ambient_audio_fields(self):
        git = GITData()
        git.ambient_sound_day     = "amb_day"
        git.ambient_sound_dayvol  = 80
        git.ambient_sound_night   = "amb_nit"
        git.ambient_sound_nightvol = 60
        git.env_audio             = 5
        save_git(git, self.tmp)
        git2 = load_git(self.tmp)
        assert git2.ambient_sound_day   == "amb_day"
        assert git2.ambient_sound_night == "amb_nit"
        assert git2.env_audio == 5


# ─────────────────────────────────────────────────────────────────────────────
#  GFFWriter API tests
# ─────────────────────────────────────────────────────────────────────────────

class TestGFFWriterAPI:
    def test_to_bytes_returns_bytes(self):
        root = GFFRoot(file_type="GIT ")
        root.struct_id = 0xFFFFFFFF
        result = GFFWriter(root).to_bytes()
        assert isinstance(result, bytes)
        assert len(result) >= 56

    def test_write_file(self):
        root = GFFRoot(file_type="GIT ")
        root.struct_id = 0xFFFFFFFF
        with tempfile.NamedTemporaryFile(suffix=".git", delete=False) as f:
            path = f.name
        try:
            GFFWriter(root).write_file(path)
            assert os.path.exists(path)
            assert os.path.getsize(path) >= 56
        finally:
            os.remove(path)

    def test_idempotent_multiple_calls(self):
        """to_bytes() called twice on same writer should produce identical output."""
        root = GFFRoot(file_type="GIT ")
        root.struct_id = 0xFFFFFFFF
        root.fields["X"] = GFFField("X", GFFFieldType.FLOAT, 1.0)
        w = GFFWriter(root)
        b1 = w.to_bytes()
        b2 = w.to_bytes()
        assert b1 == b2


# ─────────────────────────────────────────────────────────────────────────────
#  GFFReader API tests
# ─────────────────────────────────────────────────────────────────────────────

class TestGFFReaderAPI:
    def test_from_bytes(self):
        root = GFFRoot(file_type="GIT ")
        root.struct_id = 0xFFFFFFFF
        root.fields["Tag"] = GFFField("Tag", GFFFieldType.CEXOSTRING, "test_tag")
        data = GFFWriter(root).to_bytes()
        r2 = GFFReader.from_bytes(data).parse()
        assert r2.file_type == "GIT"
        assert r2.fields["Tag"].value == "test_tag"

    def test_from_file(self):
        root = GFFRoot(file_type="ARE ")
        root.struct_id = 0xFFFFFFFF
        root.fields["Tag"] = GFFField("Tag", GFFFieldType.CEXOSTRING, "module_area")
        data = GFFWriter(root).to_bytes()
        with tempfile.NamedTemporaryFile(suffix=".are", delete=False) as f:
            f.write(data); path = f.name
        try:
            r2 = GFFReader.from_file(path).parse()
            assert r2.fields["Tag"].value == "module_area"
        finally:
            os.remove(path)

    def test_parse_cached(self):
        """Calling parse() twice returns same object."""
        root = GFFRoot(file_type="GIT ")
        root.struct_id = 0xFFFFFFFF
        data = GFFWriter(root).to_bytes()
        reader = GFFReader.from_bytes(data)
        r1 = reader.parse()
        r2 = reader.parse()
        assert r1 is r2

    def test_deeply_nested_struct(self):
        inner = GFFStruct(struct_id=2)
        inner.fields["Deep"] = GFFField("Deep", GFFFieldType.DWORD, 42)
        middle = GFFStruct(struct_id=1)
        middle.fields["Inner"] = GFFField("Inner", GFFFieldType.STRUCT, inner)
        root = GFFRoot(file_type="TEST")
        root.struct_id = 0xFFFFFFFF
        root.fields["Middle"] = GFFField("Middle", GFFFieldType.STRUCT, middle)
        r2 = _write_read(root)
        mid2 = r2.fields["Middle"].value
        inn2 = mid2.fields["Inner"].value
        assert inn2.fields["Deep"].value == 42
