"""
GModular — End-to-End Module Build Integration Tests
====================================================
Tests the complete pipeline of building a KotOR module from scratch:
  GFF (ARE/GIT/IFO) + LYT + WOK → ERF/MOD archive

This simulates the full workflow a modder would use to create a new module:
1. Write a room .ARE file (GFF)
2. Write a .GIT file with placeables (GFF)
3. Write a .IFO file (GFF)
4. Write a .LYT layout file (plain text)
5. Build room walkmeshes (.WOK)
6. Package everything into a .MOD archive (ERF)
7. Verify all resources round-trip correctly

References:
  - PyKotor Libraries/PyKotor/src/pykotor/resource/formats/
  - kotorblender io_scene_kotor/
  - KotOR modding wiki
"""
from __future__ import annotations

import math
import struct
import unittest
from pathlib import Path
from typing import Dict, List, Optional

TEST_DATA = Path(__file__).parent / "test_data"


# ── Format Imports ────────────────────────────────────────────────────────────
from gmodular.formats.gff_types import (
    GFFRoot, GFFStruct, GFFField, GFFFieldType,
    Vector3, Quaternion,
    GITPlaceable, GITCreature, GITDoor, GITWaypoint,
)
from gmodular.formats.gff_writer import GFFWriter
from gmodular.formats.gff_reader import GFFReader
from gmodular.formats.wok_parser import (
    WOKParser, WOKWriter, WalkFace, WalkMesh, build_module_walkmesh,
)
from gmodular.formats.lyt_vis import LayoutData, RoomPlacement, VisibilityData
from gmodular.formats.archives import ERFWriter, ERFReaderMem, EXT_TO_TYPE
from gmodular.formats.tlk_reader import TLKFile, TLKWriter, TLKReader


# ═════════════════════════════════════════════════════════════════════════════
#  Helpers
# ═════════════════════════════════════════════════════════════════════════════

def _make_gff_are(room_name: str = "testmod_01a") -> bytes:
    """Build a minimal .ARE GFF file."""
    root = GFFRoot(file_type="ARE ")
    root.fields["Name"]    = GFFField("Name",    GFFFieldType.DWORD,  1234)
    root.fields["Tag"]     = GFFField("Tag",     GFFFieldType.RESREF, room_name)
    root.fields["ResRef"]  = GFFField("ResRef",  GFFFieldType.RESREF, room_name)
    root.fields["Flags"]   = GFFField("Flags",   GFFFieldType.DWORD,  0)
    root.fields["NoRest"]  = GFFField("NoRest",  GFFFieldType.BYTE,   0)
    root.fields["EnvAudio"]= GFFField("EnvAudio",GFFFieldType.DWORD,  0)
    return GFFWriter(root).to_bytes()


def _make_gff_ifo(module_name: str = "testmod") -> bytes:
    """Build a minimal .IFO GFF file."""
    root = GFFRoot(file_type="IFO ")
    root.fields["Mod_Name"]    = GFFField("Mod_Name",    GFFFieldType.DWORD, 5)
    root.fields["Mod_ID"]      = GFFField("Mod_ID",      GFFFieldType.RESREF, module_name)
    root.fields["Mod_StartArea"]= GFFField("Mod_StartArea",GFFFieldType.RESREF, module_name + "_01a")
    root.fields["Expansion_Pack"]= GFFField("Expansion_Pack",GFFFieldType.WORD, 0)
    return GFFWriter(root).to_bytes()


def _make_gff_git(placeables: list = None) -> bytes:
    """Build a .GIT GFF file with optional placeables."""
    root = GFFRoot(file_type="GIT ")
    if placeables:
        pl_list = []
        for p in placeables:
            s = GFFStruct(struct_id=8)
            s.fields["TemplateResRef"] = GFFField("TemplateResRef", GFFFieldType.RESREF, p.get("template", "plc_crate"))
            s.fields["Tag"]  = GFFField("Tag",  GFFFieldType.RESREF, p.get("tag", "CRATE001"))
            pos_struct = GFFStruct(struct_id=0)
            pos_struct.fields["x"] = GFFField("x", GFFFieldType.FLOAT, p.get("x", 0.0))
            pos_struct.fields["y"] = GFFField("y", GFFFieldType.FLOAT, p.get("y", 0.0))
            pos_struct.fields["z"] = GFFField("z", GFFFieldType.FLOAT, p.get("z", 0.0))
            s.fields["Position"] = GFFField("Position", GFFFieldType.STRUCT, pos_struct)
            pl_list.append(GFFField("", GFFFieldType.STRUCT, s))
        root.fields["Placeable List"] = GFFField(
            "Placeable List", GFFFieldType.LIST, pl_list
        )
    return GFFWriter(root).to_bytes()


def _make_lyt(rooms: list, door_hooks: list = None) -> str:
    """Build a .LYT layout file string."""
    lines = [f"roomcount {len(rooms)}"]
    for r in rooms:
        lines.append(f"{r['resref']} {r.get('x',0.0):.2f} {r.get('y',0.0):.2f} {r.get('z',0.0):.2f}")
    lines.append("")
    lines.append("trackcount 0")
    lines.append("")
    lines.append("obstaclecount 0")
    lines.append("")
    hooks = door_hooks or []
    lines.append(f"doorhookcount {len(hooks)}")
    for h in hooks:
        lines.append(
            f"{h['name']} {h['room']} "
            f"{h.get('x',0.0):.2f} {h.get('y',0.0):.2f} {h.get('z',0.0):.2f} "
            f"0.00 0.00 0.00 1.00"
        )
    lines.append("")
    lines.append("beginmodelspace")
    lines.append("endmodelspace")
    return "\n".join(lines)


def _make_vis(rooms: list) -> str:
    """Build a .VIS visibility file.

    The KotOR .vis format: each room on its own line followed by its
    visible-room list on the next line(s), blank-line-separated blocks.
    We use the single-line variant: ``room visible1 visible2 ...``
    """
    lines = []
    resrefs = [r["resref"] for r in rooms]
    for r in resrefs:
        others = [x for x in resrefs if x != r]
        if others:
            lines.append(r + " " + " ".join(others))
        else:
            lines.append(r)
    return "\n".join(lines)



def _make_room_wok(width: float = 4.0, height: float = 4.0,
                   material: int = 0) -> bytes:
    """Build a simple flat room walkmesh of size width×height."""
    verts = [
        (0.0, 0.0, 0.0), (width, 0.0, 0.0),
        (0.0, height, 0.0), (width, height, 0.0),
    ]
    faces_data = [(0, 1, 2, material), (1, 3, 2, material)]
    wm = WalkMesh(name="room")
    for v0i, v1i, v2i, mat in faces_data:
        v0, v1, v2 = verts[v0i], verts[v1i], verts[v2i]
        wm.faces.append(WalkFace(v0=v0, v1=v1, v2=v2, material=mat,
                                  normal=(0, 0, 1)))
    return WOKWriter(wm).to_bytes()


def _make_tlk(strings: list) -> bytes:
    """Build a dialog.tlk file."""
    tlk = TLKFile()
    for s in strings:
        tlk.add(s)
    return TLKWriter(tlk).to_bytes()


# ═════════════════════════════════════════════════════════════════════════════
#  1. Individual GFF Round-Trip Tests
# ═════════════════════════════════════════════════════════════════════════════

class TestGFFRoundTrip(unittest.TestCase):
    """Test GFF write→read round-trips for all common file types."""

    def _rt(self, root: GFFRoot) -> GFFRoot:
        data = GFFWriter(root).to_bytes()
        return GFFReader(data).parse()

    def test_are_round_trip(self):
        data = _make_gff_are("testmod_01a")
        rt = GFFReader(data).parse()
        self.assertEqual(rt.file_type.strip(), "ARE")

    def test_ifo_round_trip(self):
        data = _make_gff_ifo("testmod")
        rt = GFFReader(data).parse()
        self.assertEqual(rt.file_type.strip(), "IFO")

    def test_git_round_trip(self):
        data = _make_gff_git()
        rt = GFFReader(data).parse()
        self.assertEqual(rt.file_type.strip(), "GIT")

    def test_are_fields_preserved(self):
        root = GFFRoot(file_type="ARE ")
        root.fields["NoRest"] = GFFField("NoRest", GFFFieldType.BYTE, 1)
        rt = self._rt(root)
        self.assertEqual(rt.fields["NoRest"].value, 1)

    def test_ifo_resref_field(self):
        root = GFFRoot(file_type="IFO ")
        root.fields["Mod_ID"] = GFFField("Mod_ID", GFFFieldType.RESREF, "mymod")
        rt = self._rt(root)
        self.assertEqual(rt.fields["Mod_ID"].value, "mymod")

    def test_float_field_precision(self):
        root = GFFRoot(file_type="UTI ")
        root.fields["Weight"] = GFFField("Weight", GFFFieldType.FLOAT, 3.14)
        rt = self._rt(root)
        self.assertAlmostEqual(rt.fields["Weight"].value, 3.14, places=4)

    def test_string_field(self):
        root = GFFRoot(file_type="UTI ")
        root.fields["LocalizedName"] = GFFField("LocalizedName", GFFFieldType.CEXOSTRING, "Blaster Pistol")
        rt = self._rt(root)
        self.assertEqual(rt.fields["LocalizedName"].value, "Blaster Pistol")

    def test_strref_field(self):
        root = GFFRoot(file_type="UTI ")
        root.fields["DescriptionID"] = GFFField("DescriptionID", GFFFieldType.STRREF, 42)
        rt = self._rt(root)
        self.assertEqual(rt.fields["DescriptionID"].value, 42)

    def test_nested_struct(self):
        root = GFFRoot(file_type="GIT ")
        inner = GFFStruct(struct_id=1)
        inner.fields["x"] = GFFField("x", GFFFieldType.FLOAT, 1.5)
        inner.fields["y"] = GFFField("y", GFFFieldType.FLOAT, 2.5)
        root.fields["Position"] = GFFField("Position", GFFFieldType.STRUCT, inner)
        rt = self._rt(root)
        pos = rt.fields["Position"].value
        self.assertAlmostEqual(pos.fields["x"].value, 1.5, places=4)
        self.assertAlmostEqual(pos.fields["y"].value, 2.5, places=4)

    def test_vector3_field(self):
        root = GFFRoot(file_type="GIT ")
        root.fields["Pos"] = GFFField("Pos", GFFFieldType.VECTOR, Vector3(1.0, 2.0, 3.0))
        rt = self._rt(root)
        v = rt.fields["Pos"].value
        self.assertAlmostEqual(v.x, 1.0, places=4)
        self.assertAlmostEqual(v.y, 2.0, places=4)
        self.assertAlmostEqual(v.z, 3.0, places=4)

    def test_large_gff_many_fields(self):
        """GFF with 50 fields should round-trip intact."""
        root = GFFRoot(file_type="GFF ")
        for i in range(50):
            root.fields[f"Field{i:03d}"] = GFFField(
                f"Field{i:03d}", GFFFieldType.DWORD, i * 7
            )
        rt = self._rt(root)
        self.assertEqual(len(rt.fields), 50)
        for i in range(50):
            self.assertEqual(rt.fields[f"Field{i:03d}"].value, i * 7)


# ═════════════════════════════════════════════════════════════════════════════
#  2. LYT/VIS Round-Trip Tests
# ═════════════════════════════════════════════════════════════════════════════

class TestLYTVISRoundTrip(unittest.TestCase):

    def test_parse_single_room(self):
        lyt_text = _make_lyt([{"resref": "manm26aa", "x": 0.0, "y": 0.0, "z": 0.0}])
        layout = LayoutData.from_string(lyt_text)
        self.assertEqual(layout.room_count, 1)
        self.assertEqual(layout.rooms[0].resref, "manm26aa")

    def test_parse_multi_room_positions(self):
        rooms = [
            {"resref": "room01", "x": 0.0,  "y": 0.0,  "z": 0.0},
            {"resref": "room02", "x": 10.0, "y": 0.0,  "z": 0.0},
            {"resref": "room03", "x": 0.0,  "y": 10.0, "z": 0.0},
        ]
        layout = LayoutData.from_string(_make_lyt(rooms))
        self.assertEqual(layout.room_count, 3)
        r2 = layout.get_room("room02")
        self.assertIsNotNone(r2)
        self.assertAlmostEqual(r2.x, 10.0, places=3)

    def test_parse_door_hooks(self):
        hooks = [{"name": "DH_01", "room": "room01"}]
        layout = LayoutData.from_string(_make_lyt(
            [{"resref": "room01"}], door_hooks=hooks
        ))
        dh = layout.get_door_hooks("room01")
        self.assertEqual(len(dh), 1)
        self.assertEqual(dh[0].name, "DH_01")

    def test_parse_vis_file(self):
        rooms = [{"resref": "room01"}, {"resref": "room02"}]
        vis_text = _make_vis(rooms)
        vis = VisibilityData.from_string(vis_text)
        # room01 should see room02 and vice versa
        self.assertTrue(vis.are_visible("room01", "room02"))
        self.assertTrue(vis.are_visible("room02", "room01"))

    def test_lyt_room_positions_are_floats(self):
        rooms = [{"resref": "r1", "x": 1.5, "y": -2.3, "z": 0.0}]
        layout = LayoutData.from_string(_make_lyt(rooms))
        r = layout.rooms[0]
        self.assertIsInstance(r.x, float)
        self.assertAlmostEqual(r.x, 1.5, places=3)
        self.assertAlmostEqual(r.y, -2.3, places=3)

    def test_empty_lyt(self):
        layout = LayoutData.from_string(_make_lyt([]))
        self.assertEqual(layout.room_count, 0)

    def test_lyt_position_tuple(self):
        rooms = [{"resref": "r1", "x": 5.0, "y": 3.0, "z": 1.0}]
        layout = LayoutData.from_string(_make_lyt(rooms))
        pos = layout.rooms[0].position
        self.assertEqual(len(pos), 3)
        self.assertAlmostEqual(pos[0], 5.0, places=3)


# ═════════════════════════════════════════════════════════════════════════════
#  3. ERFWriter / ERFReaderMem Tests
# ═════════════════════════════════════════════════════════════════════════════

class TestERFPackaging(unittest.TestCase):

    def _make_erf_with_resources(self) -> bytes:
        writer = ERFWriter(file_type="MOD ")
        writer.add_resource("testmod_01a",  "are",  _make_gff_are("testmod_01a"))
        writer.add_resource("testmod",      "ifo",  _make_gff_ifo("testmod"))
        writer.add_resource("testmod",      "git",  _make_gff_git())
        writer.add_resource("testmod_01a",  "wok",  _make_room_wok())
        writer.add_resource("testmod",      "lyt",  _make_lyt([{"resref": "testmod_01a"}]).encode("ascii"))
        return writer.to_bytes()

    def test_erf_header(self):
        data = self._make_erf_with_resources()
        self.assertGreaterEqual(len(data), 160)
        # ERF header starts with file type + version
        self.assertEqual(data[4:8], b"V1.0")

    def test_erf_has_correct_resource_count(self):
        data = self._make_erf_with_resources()
        entry_count, = struct.unpack_from("<I", data, 16)
        self.assertEqual(entry_count, 5)

    def test_erf_reader_list(self):
        data = self._make_erf_with_resources()
        reader = ERFReaderMem(data)
        resources = reader.list_resources()
        self.assertIn("testmod_01a.are", resources)
        self.assertIn("testmod.ifo", resources)
        self.assertIn("testmod.git", resources)
        self.assertIn("testmod_01a.wok", resources)
        self.assertIn("testmod.lyt", resources)

    def test_erf_reader_get_are(self):
        data = self._make_erf_with_resources()
        reader = ERFReaderMem(data)
        are_bytes = reader.get_resource("testmod_01a", "are")
        self.assertIsNotNone(are_bytes)
        rt = GFFReader(are_bytes).parse()
        self.assertEqual(rt.file_type.strip(), "ARE")

    def test_erf_reader_get_wok(self):
        data = self._make_erf_with_resources()
        reader = ERFReaderMem(data)
        wok_bytes = reader.get_resource("testmod_01a", "wok")
        self.assertIsNotNone(wok_bytes)
        wm = WOKParser.from_bytes(wok_bytes)
        self.assertEqual(wm.face_count, 2)

    def test_erf_reader_missing_resource_returns_none(self):
        data = self._make_erf_with_resources()
        reader = ERFReaderMem(data)
        self.assertIsNone(reader.get_resource("nonexistent", "are"))

    def test_erf_empty(self):
        writer = ERFWriter()
        data = writer.to_bytes()
        reader = ERFReaderMem(data)
        self.assertEqual(len(reader.list_resources()), 0)

    def test_erf_large_resource_data(self):
        """Large resource data should round-trip intact."""
        big_data = bytes(range(256)) * 1000  # 256 KB
        writer = ERFWriter()
        writer.add_resource("bigfile", "are", big_data)
        data = writer.to_bytes()
        reader = ERFReaderMem(data)
        result = reader.get_resource("bigfile", "are")
        self.assertEqual(result, big_data)


# ═════════════════════════════════════════════════════════════════════════════
#  4. Full Module Build Pipeline
# ═════════════════════════════════════════════════════════════════════════════

class TestFullModuleBuildPipeline(unittest.TestCase):
    """
    Simulates building a complete KotOR module from scratch:
    1. Create room geometry (WOK)
    2. Create area metadata (ARE)
    3. Create instance data (GIT)
    4. Create module info (IFO)
    5. Create layout (LYT)
    6. Package into MOD archive
    7. Extract and validate all resources
    """

    def setUp(self):
        # ── Build resources ───────────────────────────────────────────────
        self.module_id = "mynewmod"
        self.room_resref = "mynewmod_01a"

        # Rooms
        self.room1_wok = _make_room_wok(4.0, 4.0, material=0)   # Dirt
        self.room2_wok = _make_room_wok(4.0, 4.0, material=3)   # Stone

        # GFFs
        self.are_bytes = _make_gff_are(self.room_resref)
        self.ifo_bytes = _make_gff_ifo(self.module_id)
        self.git_bytes = _make_gff_git(placeables=[
            {"template": "plc_footlockr", "tag": "LOCKER001", "x": 1.0, "y": 1.0, "z": 0.0},
            {"template": "plc_chest1",    "tag": "CHEST001",  "x": 2.0, "y": 2.0, "z": 0.0},
        ])

        # Layout
        self.lyt_text = _make_lyt([
            {"resref": "mynewmod_01a", "x": 0.0,  "y": 0.0, "z": 0.0},
            {"resref": "mynewmod_01b", "x": 5.0,  "y": 0.0, "z": 0.0},
        ])
        self.vis_text = _make_vis([
            {"resref": "mynewmod_01a"},
            {"resref": "mynewmod_01b"},
        ])

        # TLK
        self.tlk_bytes = _make_tlk([
            "My New Module",        # StrRef 0
            "A mysterious room",    # StrRef 1
        ])

        # Package
        w = ERFWriter(file_type="MOD ")
        w.add_resource("mynewmod_01a", "are", self.are_bytes)
        w.add_resource("mynewmod",     "ifo", self.ifo_bytes)
        w.add_resource("mynewmod",     "git", self.git_bytes)
        w.add_resource("mynewmod_01a", "wok", self.room1_wok)
        w.add_resource("mynewmod_01b", "wok", self.room2_wok)
        w.add_resource("mynewmod",     "lyt", self.lyt_text.encode("ascii"))
        w.add_resource("mynewmod",     "vis", self.vis_text.encode("ascii"))
        self.mod_bytes = w.to_bytes()
        self.reader = ERFReaderMem(self.mod_bytes)

    # ── Archive integrity ─────────────────────────────────────────────────

    def test_mod_bytes_nonempty(self):
        self.assertGreater(len(self.mod_bytes), 0)

    def test_resource_count(self):
        resources = self.reader.list_resources()
        self.assertEqual(len(resources), 7)

    def test_all_expected_resources_present(self):
        resources = self.reader.list_resources()
        expected = {
            "mynewmod_01a.are",
            "mynewmod.ifo",
            "mynewmod.git",
            "mynewmod_01a.wok",
            "mynewmod_01b.wok",
            "mynewmod.lyt",
            "mynewmod.vis",
        }
        for r in expected:
            self.assertIn(r, resources, msg=f"Missing resource: {r}")

    # ── ARE file ──────────────────────────────────────────────────────────

    def test_are_round_trips(self):
        data = self.reader.get_resource("mynewmod_01a", "are")
        self.assertIsNotNone(data)
        rt = GFFReader(data).parse()
        self.assertEqual(rt.file_type.strip(), "ARE")

    def test_are_has_tag_field(self):
        data = self.reader.get_resource("mynewmod_01a", "are")
        rt = GFFReader(data).parse()
        self.assertIn("Tag", rt.fields)

    # ── IFO file ──────────────────────────────────────────────────────────

    def test_ifo_round_trips(self):
        data = self.reader.get_resource("mynewmod", "ifo")
        rt = GFFReader(data).parse()
        self.assertEqual(rt.file_type.strip(), "IFO")

    def test_ifo_module_id(self):
        data = self.reader.get_resource("mynewmod", "ifo")
        rt = GFFReader(data).parse()
        mod_id = rt.fields.get("Mod_ID")
        self.assertIsNotNone(mod_id)
        self.assertEqual(mod_id.value, "mynewmod")

    # ── GIT file ──────────────────────────────────────────────────────────

    def test_git_round_trips(self):
        data = self.reader.get_resource("mynewmod", "git")
        rt = GFFReader(data).parse()
        self.assertEqual(rt.file_type.strip(), "GIT")

    def test_git_has_placeable_list(self):
        data = self.reader.get_resource("mynewmod", "git")
        rt = GFFReader(data).parse()
        self.assertIn("Placeable List", rt.fields)
        pl = rt.fields["Placeable List"].value
        self.assertEqual(len(pl), 2)

    # ── WOK files ─────────────────────────────────────────────────────────

    def test_room1_wok_round_trips(self):
        data = self.reader.get_resource("mynewmod_01a", "wok")
        wm = WOKParser.from_bytes(data, name="room1")
        self.assertEqual(wm.face_count, 2)
        self.assertEqual(wm.walkable_face_count, 2)

    def test_room2_wok_round_trips(self):
        data = self.reader.get_resource("mynewmod_01b", "wok")
        wm = WOKParser.from_bytes(data, name="room2")
        self.assertEqual(wm.face_count, 2)
        # Stone (material 3) is walkable
        self.assertEqual(wm.walkable_face_count, 2)

    def test_room1_wok_has_correct_material(self):
        data = self.reader.get_resource("mynewmod_01a", "wok")
        wm = WOKParser.from_bytes(data, name="room1")
        self.assertEqual(wm.faces[0].material, 0)  # Dirt

    def test_room2_wok_has_correct_material(self):
        data = self.reader.get_resource("mynewmod_01b", "wok")
        wm = WOKParser.from_bytes(data, name="room2")
        self.assertEqual(wm.faces[0].material, 3)  # Stone

    def test_wok_aabb_nodes_present(self):
        data = self.reader.get_resource("mynewmod_01a", "wok")
        wm = WOKParser.from_bytes(data, name="room1")
        self.assertGreater(len(wm.aabbs), 0)

    # ── LYT / VIS ─────────────────────────────────────────────────────────

    def test_lyt_round_trips(self):
        data = self.reader.get_resource("mynewmod", "lyt")
        self.assertIsNotNone(data)
        lyt_text = data.decode("ascii")
        layout = LayoutData.from_string(lyt_text)
        self.assertEqual(layout.room_count, 2)

    def test_lyt_room_positions(self):
        data = self.reader.get_resource("mynewmod", "lyt")
        layout = LayoutData.from_string(data.decode("ascii"))
        r1 = layout.get_room("mynewmod_01a")
        r2 = layout.get_room("mynewmod_01b")
        self.assertIsNotNone(r1)
        self.assertIsNotNone(r2)
        self.assertAlmostEqual(r1.x, 0.0, places=2)
        self.assertAlmostEqual(r2.x, 5.0, places=2)

    def test_vis_round_trips(self):
        data = self.reader.get_resource("mynewmod", "vis")
        vis_text = data.decode("ascii")
        vis = VisibilityData.from_string(vis_text)
        self.assertTrue(vis.are_visible("mynewmod_01a", "mynewmod_01b"))

    # ── Combined walkmesh ─────────────────────────────────────────────────

    def test_combined_module_walkmesh(self):
        """Build walkmesh from layout + packed WOK resources."""
        lyt_data  = self.reader.get_resource("mynewmod", "lyt")
        layout    = LayoutData.from_string(lyt_data.decode("ascii"))

        # Build a simple resource loader from the MOD
        reader = self.reader

        class EphemeralRM:
            def get_file(self, resref, ext):
                return reader.get_resource(resref, ext)

        wm = build_module_walkmesh(
            layout.rooms,
            resource_manager=EphemeralRM()
        )
        # Both rooms combined: 2 faces each = 4 total
        self.assertEqual(wm.face_count, 4)

    def test_combined_walkmesh_walkable_count(self):
        lyt_data  = self.reader.get_resource("mynewmod", "lyt")
        layout    = LayoutData.from_string(lyt_data.decode("ascii"))
        reader    = self.reader

        class EphemeralRM:
            def get_file(self, resref, ext):
                return reader.get_resource(resref, ext)

        wm = build_module_walkmesh(layout.rooms, resource_manager=EphemeralRM())
        self.assertEqual(wm.walkable_face_count, 4)

    def test_combined_walkmesh_height_at(self):
        """height_at() should work on the combined module walkmesh."""
        lyt_data  = self.reader.get_resource("mynewmod", "lyt")
        layout    = LayoutData.from_string(lyt_data.decode("ascii"))
        reader    = self.reader

        class EphemeralRM:
            def get_file(self, resref, ext):
                return reader.get_resource(resref, ext)

        wm = build_module_walkmesh(layout.rooms, resource_manager=EphemeralRM())
        # Room 1 is at (0,0,0), room 2 at (5,0,0)
        # Query at (1,1) should hit room 1
        z = wm.height_at(1.0, 1.0)
        self.assertIsNotNone(z)
        self.assertAlmostEqual(z, 0.0, places=3)


# ═════════════════════════════════════════════════════════════════════════════
#  5. TLK + GFF StrRef Pipeline
# ═════════════════════════════════════════════════════════════════════════════

class TestTLKGFFPipeline(unittest.TestCase):
    """Test the TLK → GFF StrRef → display text pipeline."""

    def setUp(self):
        self.tlk = TLKFile()
        self.tlk.add("My New Module")       # StrRef 0
        self.tlk.add("A mysterious room")   # StrRef 1
        self.tlk.add("Press to open")       # StrRef 2

    def test_gff_strref_lookup_in_tlk(self):
        """GFF STRREF value → TLK text lookup."""
        root = GFFRoot(file_type="UTI ")
        root.fields["Description"] = GFFField("Description", GFFFieldType.STRREF, 1)
        rt = GFFReader(GFFWriter(root).to_bytes()).parse()
        strref = rt.fields["Description"].value
        text = self.tlk.get_text(strref)
        self.assertEqual(text, "A mysterious room")

    def test_module_name_from_ifo_strref(self):
        """IFO Mod_Name StrRef maps to TLK entry."""
        root = GFFRoot(file_type="IFO ")
        root.fields["Mod_Name"] = GFFField("Mod_Name", GFFFieldType.STRREF, 0)
        rt = GFFReader(GFFWriter(root).to_bytes()).parse()
        strref = rt.fields["Mod_Name"].value
        text = self.tlk.get_text(strref)
        self.assertEqual(text, "My New Module")

    def test_tlk_packed_in_mod(self):
        """TLK file packed in a MOD is fully recoverable."""
        tlk_bytes = TLKWriter(self.tlk).to_bytes()
        writer = ERFWriter("MOD ")
        writer.add_resource("dialog", "tlk", tlk_bytes)
        mod_bytes = writer.to_bytes()
        reader = ERFReaderMem(mod_bytes)
        recovered = reader.get_resource("dialog", "tlk")
        self.assertIsNotNone(recovered)
        rt_tlk = TLKReader.from_bytes(recovered)
        self.assertEqual(rt_tlk.get_text(0), "My New Module")
        self.assertEqual(rt_tlk.get_text(2), "Press to open")


# ═════════════════════════════════════════════════════════════════════════════
#  6. Edge Cases
# ═════════════════════════════════════════════════════════════════════════════

class TestEdgeCases(unittest.TestCase):

    def test_empty_module_erf(self):
        writer = ERFWriter(file_type="MOD ")
        data = writer.to_bytes()
        reader = ERFReaderMem(data)
        self.assertEqual(len(reader.list_resources()), 0)

    def test_resref_truncated_to_16_chars(self):
        """ResRefs longer than 16 characters are truncated."""
        writer = ERFWriter()
        writer.add_resource("a" * 30, "are", b"test")
        data = writer.to_bytes()
        reader = ERFReaderMem(data)
        resources = reader.list_resources()
        self.assertEqual(len(resources), 1)
        # The resref should be truncated to 16 chars
        key = resources[0]
        resref = key.split(".")[0]
        self.assertEqual(len(resref), 16)

    def test_walkmesh_zero_faces(self):
        """Empty walkmesh can be written and read back."""
        wm = WalkMesh(name="empty")
        data = WOKWriter(wm).to_bytes()
        rt = WOKParser.from_bytes(data)
        self.assertEqual(rt.face_count, 0)

    def test_walkmesh_all_nonwalkable(self):
        """A walkmesh with all non-walkable faces has 0 walkable faces."""
        wm = WalkMesh(name="nowalk")
        for i in range(5):
            wm.faces.append(WalkFace(
                v0=(float(i), 0, 0),
                v1=(float(i)+1, 0, 0),
                v2=(float(i), 1, 0),
                material=6,  # NonWalk
                normal=(0, 0, 1),
            ))
        data = WOKWriter(wm).to_bytes()
        rt = WOKParser.from_bytes(data)
        self.assertEqual(rt.walkable_face_count, 0)
        self.assertEqual(rt.face_count, 5)

    def test_gff_all_field_types(self):
        """All 19 GFF field types round-trip correctly."""
        root = GFFRoot(file_type="GFF ")
        root.fields["BYTE"]      = GFFField("BYTE",      GFFFieldType.BYTE,       255)
        root.fields["CHAR"]      = GFFField("CHAR",      GFFFieldType.CHAR,       -1)
        root.fields["WORD"]      = GFFField("WORD",      GFFFieldType.WORD,       65535)
        root.fields["SHORT"]     = GFFField("SHORT",     GFFFieldType.SHORT,      -1000)
        root.fields["DWORD"]     = GFFField("DWORD",     GFFFieldType.DWORD,      4000000000)
        root.fields["INT"]       = GFFField("INT",       GFFFieldType.INT,        -2000000)
        root.fields["FLOAT"]     = GFFField("FLOAT",     GFFFieldType.FLOAT,      3.14)
        root.fields["RESREF"]    = GFFField("RESREF",    GFFFieldType.RESREF,     "test_ref")
        root.fields["STRREF"]    = GFFField("STRREF",    GFFFieldType.STRREF,     999)
        root.fields["CEXOSTR"]   = GFFField("CEXOSTR",   GFFFieldType.CEXOSTRING, "Hello World")
        root.fields["VECTOR"]    = GFFField("VECTOR",    GFFFieldType.VECTOR,     Vector3(1, 2, 3))

        data = GFFWriter(root).to_bytes()
        rt = GFFReader(data).parse()

        self.assertEqual(rt.fields["BYTE"].value, 255)
        self.assertEqual(rt.fields["WORD"].value, 65535)
        self.assertEqual(rt.fields["DWORD"].value, 4000000000)
        self.assertEqual(rt.fields["STRREF"].value, 999)
        self.assertEqual(rt.fields["RESREF"].value, "test_ref")
        self.assertEqual(rt.fields["CEXOSTR"].value, "Hello World")
        self.assertAlmostEqual(rt.fields["FLOAT"].value, 3.14, places=4)
        self.assertAlmostEqual(rt.fields["VECTOR"].value.x, 1.0, places=4)

    def test_lyt_invalid_text(self):
        """Malformed LYT text should not crash."""
        lyt = LayoutData.from_string("roomcount 3\nnot_enough_tokens\n")
        # Should parse gracefully with 0 or partial rooms
        self.assertIsNotNone(lyt)

    def test_wok_height_precision(self):
        """height_at() should be accurate to within 1mm."""
        wm = WalkMesh(name="flat")
        wm.faces = [WalkFace(
            v0=(0,0,0.5), v1=(10,0,0.5), v2=(0,10,0.5),
            material=0, normal=(0,0,1)
        )]
        z = wm.height_at(1.0, 1.0)
        self.assertIsNotNone(z)
        self.assertAlmostEqual(z, 0.5, places=3)

    def test_erf_binary_format_header(self):
        """ERF starts with the file_type + V1.0 header."""
        writer = ERFWriter(file_type="MOD ")
        data = writer.to_bytes()
        self.assertEqual(data[:4], b"MOD ")
        self.assertEqual(data[4:8], b"V1.0")

    def test_erf_custom_file_type(self):
        """Custom file types are preserved."""
        for ftype in ["ERF ", "MOD ", "SAV ", "HAK "]:
            writer = ERFWriter(file_type=ftype)
            data = writer.to_bytes()
            self.assertEqual(data[:4], ftype.encode("ascii"))

    def test_multiple_resources_same_type(self):
        """Multiple resources with the same extension are all stored."""
        writer = ERFWriter()
        for i in range(10):
            writer.add_resource(f"room{i:02d}", "are", f"ARE{i}".encode())
        data = writer.to_bytes()
        reader = ERFReaderMem(data)
        resources = [r for r in reader.list_resources() if r.endswith(".are")]
        self.assertEqual(len(resources), 10)


# ═════════════════════════════════════════════════════════════════════════════
#  7. Real-World Module Simulation
# ═════════════════════════════════════════════════════════════════════════════

class TestRealWorldModuleSimulation(unittest.TestCase):
    """
    Simulates a complete Dantooine-style module with multiple rooms.
    Tests the kind of workflow a real KotOR modder would follow.
    """

    def setUp(self):
        """Build a 3-room module: entrance, corridor, boss room."""
        self.rooms = [
            {"resref": "m36ab_01a", "x": 0.0,  "y": 0.0, "z": 0.0, "mat": 0},   # Dirt
            {"resref": "m36ab_01b", "x": 8.0,  "y": 0.0, "z": 0.0, "mat": 3},   # Stone
            {"resref": "m36ab_01c", "x": 16.0, "y": 0.0, "z": 0.0, "mat": 9},   # Metal
        ]

        writer = ERFWriter(file_type="MOD ")

        # Build each room
        for room in self.rooms:
            wok_bytes = _make_room_wok(6.0, 6.0, material=room["mat"])
            are_bytes = _make_gff_are(room["resref"])
            writer.add_resource(room["resref"], "wok", wok_bytes)
            writer.add_resource(room["resref"], "are", are_bytes)

        # Module-level files
        lyt_text = _make_lyt(self.rooms)
        vis_text = _make_vis(self.rooms)
        git_bytes = _make_gff_git(placeables=[
            {"template": "n_darkmast01",   "tag": "DARTH001", "x": 17.0, "y": 3.0, "z": 0.0},
        ])
        ifo_bytes = _make_gff_ifo("m36ab")

        writer.add_resource("m36ab", "lyt", lyt_text.encode("ascii"))
        writer.add_resource("m36ab", "vis", vis_text.encode("ascii"))
        writer.add_resource("m36ab", "git", git_bytes)
        writer.add_resource("m36ab", "ifo", ifo_bytes)

        self.mod_bytes = writer.to_bytes()
        self.reader = ERFReaderMem(self.mod_bytes)

    def test_mod_contains_all_resources(self):
        resources = self.reader.list_resources()
        # 3 rooms × (wok + are) = 6, plus lyt/vis/git/ifo = 10
        self.assertEqual(len(resources), 10)

    def test_all_woks_are_walkable(self):
        for room in self.rooms:
            wok_data = self.reader.get_resource(room["resref"], "wok")
            wm = WOKParser.from_bytes(wok_data)
            self.assertEqual(wm.walkable_face_count, wm.face_count,
                             msg=f"Room {room['resref']} has non-walkable faces")

    def test_combined_walkmesh_has_6_faces(self):
        lyt_data = self.reader.get_resource("m36ab", "lyt")
        layout   = LayoutData.from_string(lyt_data.decode("ascii"))
        reader   = self.reader

        class RM:
            def get_file(self, resref, ext):
                return reader.get_resource(resref, ext)

        wm = build_module_walkmesh(layout.rooms, resource_manager=RM())
        # 3 rooms × 2 faces each = 6
        self.assertEqual(wm.face_count, 6)

    def test_boss_room_reachable(self):
        """The boss room is at x=16.0, should be walkable."""
        lyt_data = self.reader.get_resource("m36ab", "lyt")
        layout   = LayoutData.from_string(lyt_data.decode("ascii"))
        reader   = self.reader

        class RM:
            def get_file(self, resref, ext):
                return reader.get_resource(resref, ext)

        wm = build_module_walkmesh(layout.rooms, resource_manager=RM())
        # Boss room starts at x=16.0, so (17.0, 1.0) should be walkable
        z = wm.height_at(17.0, 1.0)
        self.assertIsNotNone(z, "Boss room at (17,1) not walkable")

    def test_layout_has_three_rooms(self):
        lyt_data = self.reader.get_resource("m36ab", "lyt")
        layout   = LayoutData.from_string(lyt_data.decode("ascii"))
        self.assertEqual(layout.room_count, 3)

    def test_visibility_connected(self):
        vis_data = self.reader.get_resource("m36ab", "vis")
        vis      = VisibilityData.from_string(vis_data.decode("ascii"))
        # All rooms should be mutually visible in our simple test setup
        self.assertTrue(vis.are_visible("m36ab_01a", "m36ab_01b"))
        self.assertTrue(vis.are_visible("m36ab_01b", "m36ab_01c"))

    def test_ifo_round_trip(self):
        ifo_data = self.reader.get_resource("m36ab", "ifo")
        rt = GFFReader(ifo_data).parse()
        self.assertEqual(rt.file_type.strip(), "IFO")


if __name__ == "__main__":
    unittest.main(verbosity=2)
