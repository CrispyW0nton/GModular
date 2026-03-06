"""
GModular — Archive / Resource Manager Tests
Covers: KEYReader header parsing, ERFReader (mod/rim), ResourceManager
        priority chain, EXT_TO_TYPE mapping, list_resources, shim import.

Run with:  python -m pytest tests/ -v
"""
from __future__ import annotations
import os
import struct
import tempfile
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from gmodular.formats.archives import (
    KEYReader, ERFReader, ResourceManager, ResourceEntry,
    RES_TYPE_MAP, EXT_TO_TYPE, get_resource_manager,
)


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers — minimal synthetic binary builders
# ─────────────────────────────────────────────────────────────────────────────

def _build_key(bif_names: list, resources: list) -> bytes:
    """
    Build a minimal chitin.key binary.
    resources: list of (resref:str, res_type:int, bif_idx:int, res_idx:int)
    """
    # Compute layout
    HEADER_SIZE = 64
    bif_entry_size  = 12
    key_entry_size  = 22

    # BIF name strings come right after BIF table
    bif_table_off   = HEADER_SIZE
    # Build name string blob
    name_strings = b""
    name_offsets = []
    name_sizes   = []

    name_blob_base = bif_table_off + len(bif_names) * bif_entry_size

    for name in bif_names:
        encoded = (name + "\x00").encode("ascii")
        name_offsets.append(name_blob_base + len(name_strings))
        name_sizes.append(len(encoded))
        name_strings += encoded

    key_table_off = name_blob_base + len(name_strings)

    # Header
    bif_count  = len(bif_names)
    key_count  = len(resources)
    build_year = 100   # 2000
    build_day  = 1

    header = struct.pack(
        "<4s4sIIIIII32s",
        b"KEY ",
        b"V1  ",
        bif_count,
        key_count,
        bif_table_off,
        key_table_off,
        build_year,
        build_day,
        b"\x00" * 32,
    )

    # BIF entries (file_size=0, name_offset, name_size, drives=0)
    bif_table = b""
    for i, name in enumerate(bif_names):
        bif_table += struct.pack("<IIHH", 0, name_offsets[i], name_sizes[i], 0)

    # KEY entries: ResRef(16) + res_type(H) + res_id(I)
    key_table = b""
    for resref, res_type, bif_idx, res_idx in resources:
        raw_resref = resref.encode("ascii")[:16].ljust(16, b"\x00")
        res_id = ((bif_idx & 0xFFF) << 20) | (res_idx & 0xFFFFF)
        key_table += struct.pack("<16sHI", raw_resref, res_type, res_id)

    return header + bif_table + name_strings + key_table


def _build_bif(entries: list) -> bytes:
    """
    Build a minimal BIFF V1 binary.
    entries: list of (data: bytes, res_type: int)
    Returns raw BIFF bytes.
    """
    header_size = 20   # "BIFF" + "V1  " + var_count + fix_count + var_table_off
    var_table_off = header_size
    var_entry_size = 16
    var_table_size = len(entries) * var_entry_size

    # Compute data offsets
    data_base = header_size + var_table_size
    offsets = []
    blob = b""
    for data, _ in entries:
        offsets.append(data_base + len(blob))
        blob += data

    header = struct.pack("<4s4sIII",
                         b"BIFF", b"V1  ",
                         len(entries), 0, var_table_off)

    var_table = b""
    for i, (data, res_type) in enumerate(entries):
        # r_id, r_offset, r_size, r_type
        var_table += struct.pack("<IIII", i, offsets[i], len(data), res_type)

    return header + var_table + blob


def _build_erf(resources: list, file_type: str = "MOD ") -> bytes:
    """
    Build a minimal ERF/MOD binary.
    resources: list of (resref:str, res_type:int, data:bytes)
    """
    HEADER_SIZE = 160
    key_entry_size = 24
    res_entry_size = 8

    entry_count = len(resources)
    key_off = HEADER_SIZE
    res_off = key_off + entry_count * key_entry_size

    # Compute data offsets
    data_base = res_off + entry_count * res_entry_size
    offsets = []
    blob = b""
    for _, _, data in resources:
        offsets.append(data_base + len(blob))
        blob += data

    # 160-byte header
    header = bytearray(160)
    struct.pack_into("<4s4s", header, 0, file_type.encode("ascii"), b"V1.0")
    # lang_count=0, lang_size=0, entry_count, loc_off, key_off, res_off
    struct.pack_into("<9I", header, 8,
                     0, 0, entry_count, 0, key_off, res_off, 0, 0, 0)

    key_table = b""
    for i, (resref, res_type, _) in enumerate(resources):
        raw = resref.encode("ascii")[:16].ljust(16, b"\x00")
        # ResRef(16) + res_id(I) + res_type(H) + unused(H)
        key_table += struct.pack("<16sIHH", raw, i, res_type, 0)

    res_table = b""
    for i, (_, _, data) in enumerate(resources):
        res_table += struct.pack("<II", offsets[i], len(data))

    return bytes(header) + key_table + res_table + blob


# ─────────────────────────────────────────────────────────────────────────────
#  RES_TYPE_MAP / EXT_TO_TYPE
# ─────────────────────────────────────────────────────────────────────────────

class TestResMaps:
    def test_roundtrip_ext_to_type(self):
        for tid, ext in RES_TYPE_MAP.items():
            assert EXT_TO_TYPE.get(ext) == tid, f"EXT_TO_TYPE[{ext!r}] should be {tid}"

    def test_common_types_present(self):
        assert EXT_TO_TYPE["git"]  == 2026
        assert EXT_TO_TYPE["are"]  == 2017
        assert EXT_TO_TYPE["ifo"]  == 2019
        assert EXT_TO_TYPE["utp"]  == 2043
        assert EXT_TO_TYPE["utc"]  == 2030
        assert EXT_TO_TYPE["ncs"]  == 2010
        assert EXT_TO_TYPE["mdl"]  == 2002
        assert EXT_TO_TYPE["wok"]  == 2021

    def test_ext_to_type_reverse_coverage(self):
        for ext, tid in EXT_TO_TYPE.items():
            assert RES_TYPE_MAP.get(tid) == ext


# ─────────────────────────────────────────────────────────────────────────────
#  KEYReader
# ─────────────────────────────────────────────────────────────────────────────

class TestKEYReader:
    def test_header_size(self):
        """The format string must produce exactly 64 bytes."""
        assert struct.calcsize(KEYReader._KEY_HEADER_FMT) == 64

    def test_load_empty_key(self):
        """A valid KEY with 0 BIFs and 0 resources loads without error."""
        key_data = _build_key([], [])
        with tempfile.TemporaryDirectory() as tmpdir:
            key_path = os.path.join(tmpdir, "chitin.key")
            with open(key_path, "wb") as f:
                f.write(key_data)
            reader = KEYReader(key_path)
            count = reader.load()
            assert count == 0
            assert reader.resources == {}

    def test_load_with_resources(self):
        """A KEY with one BIF and two resources indexes them correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bif_rel = "data/templates.bif"
            bif_abs = os.path.join(tmpdir, bif_rel)
            os.makedirs(os.path.dirname(bif_abs), exist_ok=True)

            # Build BIF with two resources
            payload_a = b"chair_data_bytes"
            payload_b = b"guard_data_bytes"
            bif_data = _build_bif([
                (payload_a, EXT_TO_TYPE["utp"]),
                (payload_b, EXT_TO_TYPE["utc"]),
            ])
            with open(bif_abs, "wb") as f:
                f.write(bif_data)

            # Build KEY referencing those two resources
            key_data = _build_key(
                [bif_rel],
                [
                    ("utp_chair", EXT_TO_TYPE["utp"], 0, 0),
                    ("utc_guard", EXT_TO_TYPE["utc"], 0, 1),
                ],
            )
            key_path = os.path.join(tmpdir, "chitin.key")
            with open(key_path, "wb") as f:
                f.write(key_data)

            reader = KEYReader(key_path)
            count = reader.load()
            assert count == 2
            assert "utp_chair.utp" in reader.resources
            assert "utc_guard.utc" in reader.resources

    def test_read_resource_from_bif(self):
        """KEYReader.get() correctly retrieves the payload from the BIF."""
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = b"Hello from BIF!"
            bif_abs = os.path.join(tmpdir, "data", "misc.bif")
            os.makedirs(os.path.dirname(bif_abs), exist_ok=True)
            bif_data = _build_bif([(payload, EXT_TO_TYPE["txt"])])
            with open(bif_abs, "wb") as f:
                f.write(bif_data)

            key_data = _build_key(
                ["data/misc.bif"],
                [("readme", EXT_TO_TYPE["txt"], 0, 0)],
            )
            key_path = os.path.join(tmpdir, "chitin.key")
            with open(key_path, "wb") as f:
                f.write(key_data)

            reader = KEYReader(key_path)
            reader.load()
            data = reader.get("readme", EXT_TO_TYPE["txt"])
            assert data == payload

    def test_file_too_small_returns_zero(self):
        """A KEY file smaller than 64 bytes should return 0 resources."""
        with tempfile.TemporaryDirectory() as tmpdir:
            key_path = os.path.join(tmpdir, "bad.key")
            with open(key_path, "wb") as f:
                f.write(b"\x00" * 10)
            reader = KEYReader(key_path)
            assert reader.load() == 0

    def test_missing_bif_index_skipped(self):
        """Resources pointing to a bif_idx beyond the BIF table are skipped."""
        key_data = _build_key(
            [],   # 0 BIFs in table
            [("ghost_res", EXT_TO_TYPE["utp"], 5, 0)],  # bif_idx=5 out of range
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            key_path = os.path.join(tmpdir, "chitin.key")
            with open(key_path, "wb") as f:
                f.write(key_data)
            reader = KEYReader(key_path)
            count = reader.load()
            assert count == 0

    def test_case_insensitive_lookup(self):
        """get() should be case-insensitive on resref."""
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = b"data"
            bif_abs = os.path.join(tmpdir, "data.bif")
            bif_data = _build_bif([(payload, EXT_TO_TYPE["utp"])])
            with open(bif_abs, "wb") as f:
                f.write(bif_data)

            key_data = _build_key(
                ["data.bif"],
                [("MyItem", EXT_TO_TYPE["utp"], 0, 0)],
            )
            key_path = os.path.join(tmpdir, "chitin.key")
            with open(key_path, "wb") as f:
                f.write(key_data)

            reader = KEYReader(key_path)
            reader.load()
            # The key table lowercases the entry; lookup should work
            assert reader.get("myitem", EXT_TO_TYPE["utp"]) == payload


# ─────────────────────────────────────────────────────────────────────────────
#  ERFReader
# ─────────────────────────────────────────────────────────────────────────────

class TestERFReader:
    def test_load_mod_basic(self):
        """Load a minimal MOD archive with two resources."""
        payload_a = b"area_data"
        payload_b = b"git_data"
        erf_data = _build_erf([
            ("danm13", EXT_TO_TYPE["are"], payload_a),
            ("danm13", EXT_TO_TYPE["git"], payload_b),
        ], file_type="MOD ")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.mod")
            with open(path, "wb") as f:
                f.write(erf_data)
            reader = ERFReader(path)
            count = reader.load()
            assert count == 2
            assert reader.get("danm13", EXT_TO_TYPE["are"]) == payload_a
            assert reader.get("danm13", EXT_TO_TYPE["git"]) == payload_b

    def test_load_erf_type(self):
        """ERF file type is parsed correctly."""
        payload = b"hello"
        erf_data = _build_erf([("test", EXT_TO_TYPE["nss"], payload)], "ERF ")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "scripts.erf")
            with open(path, "wb") as f:
                f.write(erf_data)
            reader = ERFReader(path)
            count = reader.load()
            assert count == 1
            assert reader.get("test", EXT_TO_TYPE["nss"]) == payload

    def test_unknown_resref_returns_none(self):
        erf_data = _build_erf([("known", EXT_TO_TYPE["txt"], b"x")])
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "t.mod")
            with open(path, "wb") as f:
                f.write(erf_data)
            reader = ERFReader(path)
            reader.load()
            assert reader.get("unknown", EXT_TO_TYPE["txt"]) is None

    def test_missing_file_returns_zero(self):
        reader = ERFReader("/nonexistent/path/file.mod")
        assert reader.load() == 0


# ─────────────────────────────────────────────────────────────────────────────
#  ResourceManager priority chain
# ─────────────────────────────────────────────────────────────────────────────

class TestResourceManager:
    def test_override_takes_priority_over_erf(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            override_dir = os.path.join(tmpdir, "override")
            os.makedirs(override_dir)

            # Write override file
            override_payload = b"from_override"
            with open(os.path.join(override_dir, "item.utp"), "wb") as f:
                f.write(override_payload)

            # Write ERF with different content
            erf_payload = b"from_erf"
            erf_data = _build_erf([("item", EXT_TO_TYPE["utp"], erf_payload)])
            erf_path = os.path.join(tmpdir, "module.mod")
            with open(erf_path, "wb") as f:
                f.write(erf_data)

            rm = ResourceManager()
            rm.add_override_dir(override_dir)
            rm.load_erf(erf_path)

            result = rm.get("item", EXT_TO_TYPE["utp"])
            assert result == override_payload, "Override should take priority over ERF"

    def test_erf_takes_priority_over_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # KEY/BIF
            bif_abs = os.path.join(tmpdir, "data.bif")
            bif_payload = b"from_key"
            bif_data = _build_bif([(bif_payload, EXT_TO_TYPE["utp"])])
            with open(bif_abs, "wb") as f:
                f.write(bif_data)

            key_data = _build_key(
                ["data.bif"],
                [("item", EXT_TO_TYPE["utp"], 0, 0)],
            )
            key_path = os.path.join(tmpdir, "chitin.key")
            with open(key_path, "wb") as f:
                f.write(key_data)

            # ERF with different payload
            erf_payload = b"from_erf"
            erf_data = _build_erf([("item", EXT_TO_TYPE["utp"], erf_payload)])
            erf_path = os.path.join(tmpdir, "module.mod")
            with open(erf_path, "wb") as f:
                f.write(erf_data)

            rm = ResourceManager()
            rm.load_erf(erf_path)
            kr = KEYReader(key_path)
            kr.load()
            rm._keys.append(kr)

            result = rm.get("item", EXT_TO_TYPE["utp"])
            assert result == erf_payload, "ERF should take priority over KEY/BIF"

    def test_missing_resource_returns_none(self):
        rm = ResourceManager()
        assert rm.get("nonexistent", EXT_TO_TYPE["git"]) is None

    def test_is_loaded_false_by_default(self):
        rm = ResourceManager()
        assert not rm.is_loaded

    def test_list_resources_from_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            override_dir = os.path.join(tmpdir, "override")
            os.makedirs(override_dir)
            for name in ("chair.utp", "table.utp", "npc.utc"):
                open(os.path.join(override_dir, name), "wb").close()

            rm = ResourceManager()
            rm.add_override_dir(override_dir)
            utps = rm.list_resources(EXT_TO_TYPE["utp"])
            assert sorted(utps) == ["chair", "table"]

    def test_list_resources_from_erf(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            erf_data = _build_erf([
                ("alpha", EXT_TO_TYPE["nss"], b"a"),
                ("beta",  EXT_TO_TYPE["nss"], b"b"),
            ])
            erf_path = os.path.join(tmpdir, "s.erf")
            with open(erf_path, "wb") as f:
                f.write(erf_data)

            rm = ResourceManager()
            rm.load_erf(erf_path)
            scripts = rm.list_resources(EXT_TO_TYPE["nss"])
            assert sorted(scripts) == ["alpha", "beta"]

    def test_get_file_by_extension(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            override_dir = os.path.join(tmpdir, "override")
            os.makedirs(override_dir)
            payload = b"some script"
            with open(os.path.join(override_dir, "k_ai_master.ncs"), "wb") as f:
                f.write(payload)

            rm = ResourceManager()
            rm.add_override_dir(override_dir)
            result = rm.get_file("k_ai_master", "ncs")
            assert result == payload

    def test_game_tag_defaults_to_k1(self):
        rm = ResourceManager()
        assert rm.game_tag == "K1"

    def test_singleton_returns_same_instance(self):
        rm1 = get_resource_manager()
        rm2 = get_resource_manager()
        assert rm1 is rm2


# ─────────────────────────────────────────────────────────────────────────────
#  ResourceEntry
# ─────────────────────────────────────────────────────────────────────────────

class TestResourceEntry:
    def test_ext_from_res_type(self):
        e = ResourceEntry(resref="chair", res_type=EXT_TO_TYPE["utp"], source="bif")
        assert e.ext == "utp"

    def test_filename(self):
        e = ResourceEntry(resref="npc_guard", res_type=EXT_TO_TYPE["utc"], source="erf")
        assert e.filename == "npc_guard.utc"

    def test_unknown_type_ext_is_bin(self):
        e = ResourceEntry(resref="x", res_type=0xFFFF, source="bif")
        assert e.ext == "bin"

    def test_repr(self):
        e = ResourceEntry(resref="area1", res_type=EXT_TO_TYPE["are"], source="erf")
        assert "area1" in repr(e)
        assert "are" in repr(e)


# ─────────────────────────────────────────────────────────────────────────────
#  Shim import compatibility
# ─────────────────────────────────────────────────────────────────────────────

class TestShimImport:
    def test_shim_exports_same_classes(self):
        from gmodular.utils.resource_manager import (
            ResourceManager as RM_shim,
            KEYReader as KR_shim,
            ERFReader as ER_shim,
            get_resource_manager as grm_shim,
            RES_TYPE_MAP as rtm_shim,
            EXT_TO_TYPE as ett_shim,
        )
        assert RM_shim is ResourceManager
        assert KR_shim is KEYReader
        assert ER_shim is ERFReader
        assert grm_shim is get_resource_manager
        assert rtm_shim is RES_TYPE_MAP
        assert ett_shim is EXT_TO_TYPE
