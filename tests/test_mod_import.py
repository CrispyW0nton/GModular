"""
GModular — .MOD Archive Import Tests
Tests the full import pipeline:
  - ERF archive reading
  - LYT / VIS type ID recognition
  - ModuleState.load_from_mod()
  - mod_import_dialog.inspect_archive()
  - RoomAssemblyPanel.load_lyt()
"""
from __future__ import annotations
import os
import struct
import tempfile
import shutil
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gmodular.formats.archives import (
    ERFReader, EXT_TO_TYPE, RES_TYPE_MAP, ResourceManager,
)
from gmodular.formats.gff_types import GITData, AREData, IFOData


# ─────────────────────────────────────────────────────────────────────────────
#  Synthetic ERF builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_minimal_gff(file_type: str) -> bytes:
    """
    Build a minimal valid GFF V3.2 header with the given 4-char file_type.
    Struct / field / label / data counts are all 0 so the root struct is empty.
    """
    HEADER_FMT = "<4s4s12I"
    HEADER_SIZE = 56
    hdr = struct.pack(
        HEADER_FMT,
        file_type.ljust(4).encode("ascii"),   # file type
        b"V3.2",                               # version
        HEADER_SIZE, 1,   # struct_off, struct_count (1 root struct)
        HEADER_SIZE + 12, 0,  # field_off (after struct table), field_count
        0, 0,             # label_off, label_count
        0, 0,             # fdata_off, fdata_count
        0, 0,             # findices_off, findices_count
        0, 0,             # lindices_off, lindices_count
    )
    # Root struct: type=0xFFFFFFFF (GFF root), field_offset=0, field_count=0
    root_struct = struct.pack("<III", 0xFFFFFFFF, 0, 0)
    return hdr + root_struct


def _build_erf(resources: list) -> bytes:
    """
    Build a minimal ERF V1.0 binary.

    :param resources: list of (resref: str, ext: str, data: bytes)
    :returns: raw ERF bytes
    """
    HEADER_SIZE = 160
    n = len(resources)
    key_off    = HEADER_SIZE
    res_off    = key_off + n * 24
    data_start = res_off + n * 8

    # Build data blob and calculate offsets/sizes
    offsets: list = []
    sizes:   list = []
    buf_data = b""
    for _resref, _ext, data in resources:
        offsets.append(data_start + len(buf_data))
        sizes.append(len(data))
        buf_data += data

    # 160-byte header (pad after first 9 uint32s)
    header = b"ERF " + b"V1.0" + struct.pack(
        "<9I",
        0, 0, n,          # lang_count, lang_size, entry_count
        HEADER_SIZE,      # loc_off
        key_off,          # key_off
        res_off,          # res_off
        0, 0, 0,          # build_year, build_day, desc_strref
    )
    header = header.ljust(160, b"\x00")

    # Key table: ResRef(16) + ResID(4) + ResType(2) + padding(2)
    key_table = b""
    for i, (resref, ext, _data) in enumerate(resources):
        rr      = resref.encode("ascii")[:16].ljust(16, b"\x00")
        type_id = EXT_TO_TYPE.get(ext.lower(), 0)
        key_table += rr + struct.pack("<IHH", i, type_id, 0)

    # Res table: offset(4) + size(4) per entry
    res_table = b""
    for off, sz in zip(offsets, sizes):
        res_table += struct.pack("<II", off, sz)

    return header + key_table + res_table + buf_data


LYT_TEXT = (
    "filedependency 0\n"
    "roomcount 2\n"
    "slem_ar_m01  0.00  0.00  0.00\n"
    "slem_ar_m02 10.00  0.00  0.00\n"
    "obstaclecount 0\n"
    "doorhookcount 0\n"
)

VIS_TEXT = (
    "slem_ar_m01\n"
    "slem_ar_m02\n"
    "\n"
    "slem_ar_m02\n"
    "slem_ar_m01\n"
)


@pytest.fixture
def mod_file(tmp_path):
    """Create a synthetic .mod archive with GFF and LYT resources."""
    resources = [
        ("slem_ar", "git", _build_minimal_gff("GIT ")),
        ("slem_ar", "are", _build_minimal_gff("ARE ")),
        ("module",  "ifo", _build_minimal_gff("IFO ")),
        ("slem_ar", "lyt", LYT_TEXT.encode("utf-8")),
        ("slem_ar", "vis", VIS_TEXT.encode("utf-8")),
    ]
    data = _build_erf(resources)
    path = str(tmp_path / "slem_ar.mod")
    with open(path, "wb") as f:
        f.write(data)
    return path


# ─────────────────────────────────────────────────────────────────────────────
#  1. ERF type-ID registration for LYT / VIS
# ─────────────────────────────────────────────────────────────────────────────

class TestArchiveTypeIDs:
    def test_lyt_type_registered(self):
        assert "lyt" in EXT_TO_TYPE, "lyt extension must have a registered type ID"
        assert EXT_TO_TYPE["lyt"] == 3006

    def test_vis_type_registered(self):
        assert "vis" in EXT_TO_TYPE, "vis extension must have a registered type ID"
        assert EXT_TO_TYPE["vis"] == 3007

    def test_round_trip_lyt(self):
        assert RES_TYPE_MAP[EXT_TO_TYPE["lyt"]] == "lyt"

    def test_round_trip_vis(self):
        assert RES_TYPE_MAP[EXT_TO_TYPE["vis"]] == "vis"


# ─────────────────────────────────────────────────────────────────────────────
#  2. ERFReader correctly parses a synthetic .mod
# ─────────────────────────────────────────────────────────────────────────────

class TestERFModParsing:
    def test_load_resource_count(self, mod_file):
        erf = ERFReader(mod_file)
        n = erf.load()
        assert n == 5, f"Expected 5 resources, got {n}"

    def test_resource_keys_present(self, mod_file):
        erf = ERFReader(mod_file)
        erf.load()
        keys = set(erf.resources.keys())
        assert "slem_ar.git" in keys
        assert "slem_ar.are" in keys
        assert "module.ifo" in keys
        assert "slem_ar.lyt" in keys
        assert "slem_ar.vis" in keys

    def test_read_lyt_resource(self, mod_file):
        erf = ERFReader(mod_file)
        erf.load()
        entry = erf.resources["slem_ar.lyt"]
        data = erf.read_resource(entry)
        assert data is not None
        text = data.decode("utf-8")
        assert "roomcount 2" in text
        assert "slem_ar_m01" in text
        assert "slem_ar_m02" in text

    def test_read_vis_resource(self, mod_file):
        erf = ERFReader(mod_file)
        erf.load()
        entry = erf.resources["slem_ar.vis"]
        data = erf.read_resource(entry)
        assert data is not None
        assert b"slem_ar_m01" in data

    def test_get_by_resref_and_type(self, mod_file):
        erf = ERFReader(mod_file)
        erf.load()
        data = erf.get("slem_ar", EXT_TO_TYPE["lyt"])
        assert data is not None
        assert b"roomcount 2" in data


# ─────────────────────────────────────────────────────────────────────────────
#  3. inspect_archive() headless helper
# ─────────────────────────────────────────────────────────────────────────────

class TestInspectArchive:
    def test_inspect_returns_correct_count(self, mod_file):
        from gmodular.gui.mod_import_dialog import inspect_archive
        info = inspect_archive(mod_file)
        assert info["error"] is None
        assert len(info["resources"]) == 5

    def test_inspect_lists_core_files(self, mod_file):
        from gmodular.gui.mod_import_dialog import inspect_archive
        info = inspect_archive(mod_file)
        exts = {r["ext"] for r in info["resources"]}
        assert "git" in exts
        assert "are" in exts
        assert "ifo" in exts
        assert "lyt" in exts
        assert "vis" in exts

    def test_inspect_missing_file(self, tmp_path):
        from gmodular.gui.mod_import_dialog import inspect_archive
        info = inspect_archive(str(tmp_path / "nonexistent.mod"))
        assert info["error"] is not None

    def test_inspect_file_type_field(self, mod_file):
        from gmodular.gui.mod_import_dialog import inspect_archive
        info = inspect_archive(mod_file)
        assert info["file_type"] == "MOD"


# ─────────────────────────────────────────────────────────────────────────────
#  4. ModuleState.load_from_mod()
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadFromMod:
    def test_load_returns_summary(self, mod_file, tmp_path):
        from gmodular.core.module_state import ModuleState
        state = ModuleState()
        summary = state.load_from_mod(mod_file,
                                       extract_dir=str(tmp_path / "extracted"))
        assert isinstance(summary, dict)
        assert summary["mod_path"] == mod_file
        assert "resources" in summary

    def test_load_populates_git(self, mod_file, tmp_path):
        from gmodular.core.module_state import ModuleState
        state = ModuleState()
        state.load_from_mod(mod_file, extract_dir=str(tmp_path / "extracted"))
        assert state.git is not None
        assert isinstance(state.git, GITData)

    def test_load_populates_are(self, mod_file, tmp_path):
        from gmodular.core.module_state import ModuleState
        state = ModuleState()
        state.load_from_mod(mod_file, extract_dir=str(tmp_path / "extracted"))
        assert state.are is not None
        assert isinstance(state.are, AREData)

    def test_load_populates_ifo(self, mod_file, tmp_path):
        from gmodular.core.module_state import ModuleState
        state = ModuleState()
        state.load_from_mod(mod_file, extract_dir=str(tmp_path / "extracted"))
        assert state.ifo is not None
        assert isinstance(state.ifo, IFOData)

    def test_load_extracts_lyt(self, mod_file, tmp_path):
        from gmodular.core.module_state import ModuleState
        state = ModuleState()
        summary = state.load_from_mod(mod_file,
                                       extract_dir=str(tmp_path / "extracted"))
        assert summary["lyt_text"] is not None
        assert "slem_ar_m01" in summary["lyt_text"]
        assert "slem_ar_m02" in summary["lyt_text"]

    def test_load_extracts_vis(self, mod_file, tmp_path):
        from gmodular.core.module_state import ModuleState
        state = ModuleState()
        summary = state.load_from_mod(mod_file,
                                       extract_dir=str(tmp_path / "extracted"))
        assert summary["vis_text"] is not None
        assert "slem_ar_m01" in summary["vis_text"]

    def test_load_writes_extracted_files(self, mod_file, tmp_path):
        from gmodular.core.module_state import ModuleState
        extract_dir = str(tmp_path / "extracted")
        state = ModuleState()
        state.load_from_mod(mod_file, extract_dir=extract_dir)
        extracted = os.listdir(extract_dir)
        assert any(f.endswith(".git") for f in extracted)
        assert any(f.endswith(".lyt") for f in extracted)

    def test_load_resref_detection(self, mod_file, tmp_path):
        from gmodular.core.module_state import ModuleState
        state = ModuleState()
        summary = state.load_from_mod(mod_file,
                                       extract_dir=str(tmp_path / "extracted"))
        assert summary["resref"] in ("slem_ar", "module")

    def test_load_creates_project(self, mod_file, tmp_path):
        from gmodular.core.module_state import ModuleState
        state = ModuleState()
        state.load_from_mod(mod_file, extract_dir=str(tmp_path / "extracted"))
        assert state.project is not None

    def test_load_nonexistent_returns_errors(self, tmp_path):
        from gmodular.core.module_state import ModuleState
        state = ModuleState()
        summary = state.load_from_mod(
            str(tmp_path / "missing.mod"),
            extract_dir=str(tmp_path / "extracted")
        )
        assert len(summary["errors"]) > 0

    def test_load_state_not_dirty_after_load(self, mod_file, tmp_path):
        from gmodular.core.module_state import ModuleState
        state = ModuleState()
        state.load_from_mod(mod_file, extract_dir=str(tmp_path / "extracted"))
        assert not state.is_dirty


# ─────────────────────────────────────────────────────────────────────────────
#  5. LYTData round-trip from MOD
# ─────────────────────────────────────────────────────────────────────────────

class TestLYTRoundTrip:
    def test_lyt_from_mod_parses_rooms(self, mod_file, tmp_path):
        from gmodular.core.module_state import ModuleState
        from gmodular.gui.room_assembly import LYTData
        state = ModuleState()
        summary = state.load_from_mod(mod_file,
                                       extract_dir=str(tmp_path / "extracted"))
        lyt = LYTData.from_text(summary["lyt_text"])
        assert len(lyt.rooms) == 2
        names = [r.mdl_name for r in lyt.rooms]
        assert "slem_ar_m01" in names
        assert "slem_ar_m02" in names

    def test_lyt_room_coordinates(self, mod_file, tmp_path):
        from gmodular.core.module_state import ModuleState
        from gmodular.gui.room_assembly import LYTData
        state = ModuleState()
        summary = state.load_from_mod(mod_file,
                                       extract_dir=str(tmp_path / "extracted"))
        lyt = LYTData.from_text(summary["lyt_text"])
        m01 = next(r for r in lyt.rooms if r.mdl_name == "slem_ar_m01")
        assert m01.world_x == pytest.approx(0.0)
        assert m01.world_y == pytest.approx(0.0)
        m02 = next(r for r in lyt.rooms if r.mdl_name == "slem_ar_m02")
        assert m02.world_x == pytest.approx(10.0)

    def test_lyt_to_text_round_trip(self, mod_file, tmp_path):
        from gmodular.core.module_state import ModuleState
        from gmodular.gui.room_assembly import LYTData
        state = ModuleState()
        summary = state.load_from_mod(mod_file,
                                       extract_dir=str(tmp_path / "extracted"))
        lyt = LYTData.from_text(summary["lyt_text"])
        text = lyt.to_text()
        lyt2 = LYTData.from_text(text)
        assert len(lyt2.rooms) == len(lyt.rooms)


# ─────────────────────────────────────────────────────────────────────────────
#  6. ResourceManager with MOD
# ─────────────────────────────────────────────────────────────────────────────

class TestResourceManagerMOD:
    def test_load_erf_into_rm(self, mod_file):
        rm = ResourceManager()
        rm.load_erf(mod_file)
        assert rm.is_loaded

    def test_rm_get_lyt_by_type(self, mod_file):
        rm = ResourceManager()
        rm.load_erf(mod_file)
        data = rm.get("slem_ar", EXT_TO_TYPE["lyt"])
        assert data is not None
        assert b"roomcount 2" in data

    def test_rm_get_by_ext_string(self, mod_file):
        rm = ResourceManager()
        rm.load_erf(mod_file)
        data = rm.get_file("slem_ar", "lyt")
        assert data is not None
        assert b"slem_ar_m01" in data

    def test_rm_list_resources(self, mod_file):
        rm = ResourceManager()
        rm.load_erf(mod_file)
        lyt_list = rm.list_resources(EXT_TO_TYPE["lyt"])
        assert "slem_ar" in lyt_list
