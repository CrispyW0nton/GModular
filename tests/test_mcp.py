"""Integration tests for gmodular.mcp — Model Context Protocol server.

Tests cover:
  • All tool schema definitions (get_tools)
  • Formatting helpers (json_content, error_content, truncation)
  • State helpers (resolve_game, iter_candidate_paths, detect_installations)
  • _indexer helpers (build_index structure contract)
  • All tool handlers using synthetic in-memory game data
  • Server request dispatch (_handle_request)
  • __main__ module can be imported without errors
  • refs helpers (_extract_refs, _extract_strrefs)
  • walkmesh surface material breakdown
  • MDL node summary helper
  • archive listing with ERF
  • conversion GFF tree / 2DA / TLK
  • discovery _analyse_* helpers

All tests are fully offline — no real KotOR installation required.
"""
from __future__ import annotations

import asyncio
import json
import struct
import tempfile
import textwrap
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch
import pytest


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _content_text(result: Any) -> str:
    """Extract the text payload from an MCP tool result (dict or object)."""
    if hasattr(result, "content"):
        items = result.content
    else:
        items = result.get("content", [])
    if not items:
        return ""
    item = items[0]
    if hasattr(item, "text"):
        return item.text
    return item.get("text", "")


def _content_json(result: Any) -> Any:
    return json.loads(_content_text(result))


def _run(coro):
    return asyncio.run(coro)


# ─────────────────────────────────────────────────────────────────────────────
#  Synthetic game-data fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_minimal_gff(file_type: str = "ARE ") -> bytes:
    """Build a minimal valid GFF binary (header + one struct, no fields)."""
    # GFF V3.2 header layout (48 bytes):
    #   file_type[4]  file_version[4]
    #   struct_offset[4]  struct_count[4]
    #   field_offset[4]   field_count[4]
    #   label_offset[4]   label_count[4]
    #   field_data_offset[4]  field_data_count[4]
    #   field_indices_offset[4]  field_indices_count[4]
    HDR_SIZE = 48
    STRUCT_SIZE = 12
    # One root struct: (type=0xFFFFFFFF, field_index=0, field_count=0)
    struct_data = struct.pack("<III", 0xFFFFFFFF, 0, 0)
    header = struct.pack(
        "<4s4s" + "I" * 10,
        file_type.encode("ascii")[:4].ljust(4, b" "),
        b"V3.2",
        HDR_SIZE,               # struct_offset
        1,                      # struct_count
        HDR_SIZE + STRUCT_SIZE, # field_offset
        0,                      # field_count
        HDR_SIZE + STRUCT_SIZE, # label_offset
        0,                      # label_count
        HDR_SIZE + STRUCT_SIZE, # field_data_offset
        0,                      # field_data_count
        HDR_SIZE + STRUCT_SIZE, # field_indices_offset
        0,                      # field_indices_count
    )
    return header + struct_data


def _make_minimal_tlk(entries: List[str]) -> bytes:
    """Build a minimal valid TLK V3.0 binary with the given string entries."""
    TLK_HEADER_SIZE = 20
    TLK_ENTRY_SIZE = 40
    string_count = len(entries)

    # Build text data
    text_parts = []
    text_offsets = []
    text_lengths = []
    offset = 0
    for text in entries:
        encoded = text.encode("utf-8")
        text_parts.append(encoded)
        text_offsets.append(offset)
        text_lengths.append(len(encoded))
        offset += len(encoded)
    text_blob = b"".join(text_parts)

    # String entries table
    entries_data = b""
    strings_offset = TLK_HEADER_SIZE + string_count * TLK_ENTRY_SIZE
    for i, text in enumerate(entries):
        flags = 0x01  # TLK_FLAG_TEXT_PRESENT
        sound_resref = b"\x00" * 16
        vol = 0
        pitch = 0
        entries_data += struct.pack(
            "<I16sIIIIf",
            flags, sound_resref, vol, pitch,
            text_offsets[i], text_lengths[i], 0.0,
        )

    header = struct.pack(
        "<4s4sIII",
        b"TLK ",
        b"V3.0",
        0,              # language_id = English
        string_count,
        strings_offset,
    )
    return header + entries_data + text_blob


def _make_minimal_2da(columns: List[str], rows: List[List[str]], name: str = "test") -> bytes:
    """Build a minimal 2DA V2.0 plain-text file."""
    lines = ["2DA V2.0", ""]
    col_line = "  ".join(columns)
    lines.append(col_line)
    for i, row in enumerate(rows):
        lines.append(f"{i}  " + "  ".join(row))
    return "\n".join(lines).encode("latin-1")


def _make_minimal_wok() -> bytes:
    """Return the bytes of a valid WOK built from existing test data."""
    test_wok = Path(__file__).parent / "test_data" / "test_simple.wok"
    if test_wok.exists():
        return test_wok.read_bytes()
    # Tiny fallback — just need a parse-able stub
    return b""


def _make_fake_installation(tmp_path: Path) -> Path:
    """Create a minimal fake KotOR installation directory structure."""
    game_dir = tmp_path / "kotor1"
    game_dir.mkdir()

    # dialog.tlk
    tlk_data = _make_minimal_tlk(["Entry zero", "Entry one", "Entry two"])
    (game_dir / "dialog.tlk").write_bytes(tlk_data)

    # override/
    override = game_dir / "override"
    override.mkdir()
    gff_data = _make_minimal_gff("ARE ")
    (override / "testarea.are").write_bytes(gff_data)

    # modules/
    modules = game_dir / "modules"
    modules.mkdir()
    # Write a tiny ERF with one resource
    _write_fake_erf(modules / "testmod.mod", {("testarea", 2012): _make_minimal_gff("ARE ")})

    # chitin.key — skip for now (requires full KEY/BIF builder); tests skip chitin
    return game_dir


def _write_fake_erf(path: Path, resources: Dict) -> None:
    """Write a minimal ERF V1.0 with the given resources {(resref, res_type): data}."""
    # ERF V1.0 layout (160-byte header)
    #   file_type[4]  version[4]  lang_count[4]  lang_size[4]  entry_count[4]
    #   offset_localised_string[4]  offset_key_list[4]  offset_resource_list[4]
    #   build_year[4]  build_day[4]  description_strref[4]  reserved[116]
    HEADER_SIZE = 160
    entries = list(resources.items())
    entry_count = len(entries)

    # Key list: 24 bytes each (resref[16] + res_id[4] + res_type[2] + unused[2])
    KEY_ENTRY_SIZE = 24
    # Resource list: 8 bytes each (offset[4] + size[4])
    RES_ENTRY_SIZE = 8

    key_list_offset = HEADER_SIZE
    res_list_offset = key_list_offset + entry_count * KEY_ENTRY_SIZE
    data_start = res_list_offset + entry_count * RES_ENTRY_SIZE

    key_list = b""
    res_list = b""
    data_blob = b""
    cur_off = data_start

    for i, ((resref, res_type), data) in enumerate(entries):
        rr = resref.encode("ascii")[:16].ljust(16, b"\x00")
        key_list += rr + struct.pack("<IHH", i, res_type, 0)
        res_list += struct.pack("<II", cur_off, len(data))
        data_blob += data
        cur_off += len(data)

    header = struct.pack(
        "<4s4s" + "I" * 9 + "116s",
        b"MOD ",
        b"V1.0",
        0,                     # lang_count
        0,                     # lang_size
        entry_count,
        HEADER_SIZE,           # offset_localised_string
        key_list_offset,       # offset_key_list
        res_list_offset,       # offset_resource_list
        0,                     # build_year
        0,                     # build_day
        0xFFFFFFFF,            # description_strref
        b"\x00" * 116,         # reserved
    )
    path.write_bytes(header + key_list + res_list + data_blob)


# ─────────────────────────────────────────────────────────────────────────────
#  Fixture: fake installation loaded into state
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def fake_game(tmp_path):
    """Provide a fake K1 installation and load it into the MCP state cache."""
    from gmodular.mcp import state as mcp_state

    game_dir = _make_fake_installation(tmp_path)
    inst = mcp_state.KotorInstallation(game_dir, "K1")
    # Inject directly into the cache so load_installation() returns it
    mcp_state._INSTALLATIONS["K1"] = inst
    yield inst
    # Cleanup: remove from cache so other tests don't see it
    mcp_state._INSTALLATIONS.pop("K1", None)


# ─────────────────────────────────────────────────────────────────────────────
#  _formatting tests
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatting:
    def test_json_content_small(self):
        from gmodular.mcp._formatting import json_content
        result = json_content({"hello": "world"})
        data = json.loads(_content_text(result))
        assert data["hello"] == "world"

    def test_json_content_truncation(self):
        from gmodular.mcp._formatting import json_content
        big = {"data": "x" * 30_000}
        result = json_content(big, max_chars=1000)
        text = _content_text(result)
        assert len(text) <= 1100  # some slack for JSON overhead
        parsed = json.loads(text)
        assert parsed.get("truncated") is True

    def test_error_content(self):
        from gmodular.mcp._formatting import error_content
        result = error_content("something went wrong")
        data = json.loads(_content_text(result))
        assert "error" in data
        assert "went wrong" in data["error"]

    def test_json_content_non_serialisable(self):
        from gmodular.mcp._formatting import json_content
        import datetime
        result = json_content({"ts": datetime.datetime(2024, 1, 1)})
        text = _content_text(result)
        assert "2024" in text


# ─────────────────────────────────────────────────────────────────────────────
#  state.py tests
# ─────────────────────────────────────────────────────────────────────────────

class TestState:
    def test_resolve_game_k1(self):
        from gmodular.mcp.state import resolve_game
        assert resolve_game("k1") == "K1"
        assert resolve_game("K1") == "K1"  # lowercased internally
        assert resolve_game("kotor1") == "K1"
        assert resolve_game("swkotor") == "K1"

    def test_resolve_game_k2(self):
        from gmodular.mcp.state import resolve_game
        assert resolve_game("k2") == "K2"
        assert resolve_game("tsl") == "K2"
        assert resolve_game("kotor2") == "K2"

    def test_resolve_game_none(self):
        from gmodular.mcp.state import resolve_game
        assert resolve_game(None) is None
        assert resolve_game("") is None
        assert resolve_game("invalid") is None

    def test_iter_candidate_paths_explicit(self, tmp_path):
        from gmodular.mcp.state import iter_candidate_paths
        explicit = str(tmp_path / "kotor1")
        paths = list(iter_candidate_paths("K1", explicit))
        assert any(str(tmp_path / "kotor1") in str(p) for p in paths)

    def test_detect_installations_structure(self):
        from gmodular.mcp.state import detect_installations
        result = detect_installations()
        assert "K1" in result
        assert "K2" in result
        assert isinstance(result["K1"], list)
        assert isinstance(result["K2"], list)
        for entry in result["K1"] + result["K2"]:
            assert "path" in entry
            assert "exists" in entry
            assert "valid" in entry

    def test_load_installation_not_found(self):
        from gmodular.mcp.state import load_installation, _INSTALLATIONS
        _INSTALLATIONS.pop("K1", None)
        with pytest.raises(ValueError, match="Unable to locate K1"):
            load_installation("K1")

    def test_kotor_installation_summary(self, tmp_path):
        from gmodular.mcp.state import KotorInstallation
        game_dir = _make_fake_installation(tmp_path)
        inst = KotorInstallation(game_dir, "K1")
        summary = inst.summary()
        assert summary["game"] == "K1"
        assert "path" in summary
        assert summary["has_tlk"] is True
        assert summary["override_count"] >= 1
        assert summary["module_count"] >= 1

    def test_kotor_installation_is_valid_no_key(self, tmp_path):
        from gmodular.mcp.state import KotorInstallation
        game_dir = tmp_path / "empty"
        game_dir.mkdir()
        inst = KotorInstallation(game_dir, "K1")
        assert inst.is_valid() is False

    def test_kotor_installation_module_list(self, tmp_path):
        from gmodular.mcp.state import KotorInstallation
        game_dir = _make_fake_installation(tmp_path)
        inst = KotorInstallation(game_dir, "K1")
        mods = inst.module_list()
        assert any("testmod" in m for m in mods)

    def test_kotor_installation_tlk_path(self, tmp_path):
        from gmodular.mcp.state import KotorInstallation
        game_dir = _make_fake_installation(tmp_path)
        inst = KotorInstallation(game_dir, "K1")
        assert inst.tlk_path() is not None


# ─────────────────────────────────────────────────────────────────────────────
#  _indexer tests
# ─────────────────────────────────────────────────────────────────────────────

class TestIndexer:
    def test_build_index_structure(self, tmp_path):
        from gmodular.mcp._indexer import build_index
        game_dir = _make_fake_installation(tmp_path)
        idx = build_index(game_dir)
        assert "by_key" in idx
        assert "by_source" in idx
        assert "path" in idx

    def test_build_index_override_entries(self, tmp_path):
        from gmodular.mcp._indexer import build_index
        game_dir = _make_fake_installation(tmp_path)
        idx = build_index(game_dir)
        assert ("testarea", "are") in idx["by_key"]
        entry = idx["by_key"][("testarea", "are")][0]
        assert entry.source == "override"

    def test_build_index_module_entries(self, tmp_path):
        from gmodular.mcp._indexer import build_index
        game_dir = _make_fake_installation(tmp_path)
        idx = build_index(game_dir)
        # The ERF module contains testarea.are; override also has testarea.are,
        # so override entry is first — but the module entry must also be present.
        found = any(
            k[0] == "testarea" and any("module:" in e.source for e in entries)
            for k, entries in idx["by_key"].items()
        )
        assert found, "Expected module-sourced testarea entry"

    def test_build_index_by_source_keys(self, tmp_path):
        from gmodular.mcp._indexer import build_index
        game_dir = _make_fake_installation(tmp_path)
        idx = build_index(game_dir)
        sources = set(idx["by_source"].keys())
        assert "override" in sources
        assert any("module:" in s for s in sources)


# ─────────────────────────────────────────────────────────────────────────────
#  Tool schema tests (all tools)
# ─────────────────────────────────────────────────────────────────────────────

class TestToolSchemas:
    def _all_tools(self):
        from gmodular.mcp.tools import get_all_tools
        return get_all_tools()

    def test_tool_count(self):
        tools = self._all_tools()
        assert len(tools) >= 20

    def test_tool_schema_required_keys(self):
        for tool in self._all_tools():
            assert "name" in tool, f"Missing 'name' in {tool}"
            assert "description" in tool, f"Missing 'description' in {tool}"
            assert "inputSchema" in tool, f"Missing 'inputSchema' in {tool}"

    def test_tool_names_unique(self):
        tools = self._all_tools()
        names = [t["name"] for t in tools]
        assert len(names) == len(set(names)), f"Duplicate tool names: {names}"

    def test_expected_tool_names(self):
        tools = self._all_tools()
        names = {t["name"] for t in tools}
        expected = {
            "detectInstallations", "loadInstallation", "kotor_installation_info",
            "listResources", "describeResource", "kotor_find_resource", "kotor_search_resources",
            "journalOverview", "kotor_lookup_2da", "kotor_lookup_tlk",
            "kotor_list_archive", "kotor_extract_resource",
            "kotor_read_gff", "kotor_read_2da", "kotor_read_tlk",
            "kotor_list_modules", "kotor_describe_module", "kotor_module_resources",
            "kotor_list_references", "kotor_find_referrers",
            "kotor_describe_dlg", "kotor_describe_jrl",
            "kotor_find_strref_referrers", "kotor_describe_resource_refs",
            "kotor_walkmesh_info", "kotor_mdl_info",
        }
        missing = expected - names
        assert not missing, f"Missing tools: {missing}"


# ─────────────────────────────────────────────────────────────────────────────
#  Installation tool handlers
# ─────────────────────────────────────────────────────────────────────────────

class TestInstallationHandlers:
    def test_detect_installations(self):
        from gmodular.mcp.tools.installation import handle_detect_installations
        result = _run(handle_detect_installations({}))
        data = _content_json(result)
        assert "K1" in data or "K2" in data

    def test_load_installation_bad_game(self, fake_game):
        from gmodular.mcp.tools.installation import handle_load_installation
        with pytest.raises(ValueError, match="Specify game"):
            _run(handle_load_installation({}))

    def test_load_installation_ok(self, fake_game, tmp_path):
        from gmodular.mcp.tools.installation import handle_load_installation
        result = _run(handle_load_installation({"game": "k1"}))
        data = _content_json(result)
        assert data["game"] == "K1"

    def test_installation_info(self, fake_game):
        from gmodular.mcp.tools.installation import handle_installation_info
        result = _run(handle_installation_info({"game": "k1"}))
        data = _content_json(result)
        assert data["game"] == "K1"
        assert "path" in data
        assert "module_count" in data


# ─────────────────────────────────────────────────────────────────────────────
#  Discovery tool handlers
# ─────────────────────────────────────────────────────────────────────────────

class TestDiscoveryHandlers:
    def test_list_resources_all(self, fake_game):
        from gmodular.mcp.tools.discovery import handle_list_resources
        result = _run(handle_list_resources({"game": "k1", "location": "all", "limit": 100}))
        data = _content_json(result)
        assert "items" in data
        assert data["count"] >= 1

    def test_list_resources_override_only(self, fake_game):
        from gmodular.mcp.tools.discovery import handle_list_resources
        result = _run(handle_list_resources({"game": "k1", "location": "override"}))
        data = _content_json(result)
        assert all(item["source"] == "override" for item in data["items"])

    def test_list_resources_type_filter(self, fake_game):
        from gmodular.mcp.tools.discovery import handle_list_resources
        result = _run(handle_list_resources(
            {"game": "k1", "location": "all", "resourceTypes": ["are"]}
        ))
        data = _content_json(result)
        for item in data["items"]:
            assert item["extension"].lower() == "are"

    def test_list_resources_resref_query(self, fake_game):
        from gmodular.mcp.tools.discovery import handle_list_resources
        result = _run(handle_list_resources(
            {"game": "k1", "resrefQuery": "testarea", "limit": 10}
        ))
        data = _content_json(result)
        assert any("testarea" in item["resref"] for item in data["items"])

    def test_describe_resource_gff(self, fake_game):
        from gmodular.mcp.tools.discovery import handle_describe_resource
        result = _run(handle_describe_resource(
            {"game": "k1", "resref": "testarea", "restype": "are"}
        ))
        data = _content_json(result)
        assert data["resref"] == "testarea"
        assert "analysis" in data
        assert "file_type" in data["analysis"]

    def test_describe_resource_not_found(self, fake_game):
        from gmodular.mcp.tools.discovery import handle_describe_resource
        with pytest.raises(ValueError, match="not found"):
            _run(handle_describe_resource(
                {"game": "k1", "resref": "nonexistent", "restype": "are"}
            ))

    def test_find_resource_exact(self, fake_game):
        from gmodular.mcp.tools.discovery import handle_find_resource
        result = _run(handle_find_resource({"game": "k1", "query": "testarea.are"}))
        data = _content_json(result)
        assert data["count"] >= 1

    def test_find_resource_glob(self, fake_game):
        from gmodular.mcp.tools.discovery import handle_find_resource
        result = _run(handle_find_resource({"game": "k1", "query": "test*"}))
        data = _content_json(result)
        assert data["count"] >= 1

    def test_search_resources_regex(self, fake_game):
        from gmodular.mcp.tools.discovery import handle_search_resources
        result = _run(handle_search_resources({"game": "k1", "pattern": "^test"}))
        data = _content_json(result)
        assert data["count"] >= 1

    def test_search_resources_invalid_regex(self, fake_game):
        from gmodular.mcp.tools.discovery import handle_search_resources
        with pytest.raises(ValueError, match="Invalid regex"):
            _run(handle_search_resources({"game": "k1", "pattern": "[invalid"}))

    def test_list_resources_pagination(self, fake_game):
        from gmodular.mcp.tools.discovery import handle_list_resources
        r1 = _run(handle_list_resources({"game": "k1", "limit": 1, "offset": 0}))
        r2 = _run(handle_list_resources({"game": "k1", "limit": 1, "offset": 1}))
        d1 = _content_json(r1)
        d2 = _content_json(r2)
        # items may differ or be the same if only 1 resource; just check structure
        assert "items" in d1 and "items" in d2


# ─────────────────────────────────────────────────────────────────────────────
#  _analyse_* helpers (offline)
# ─────────────────────────────────────────────────────────────────────────────

class TestAnalyseHelpers:
    def test_analyse_gff(self):
        from gmodular.mcp.tools.discovery import _analyse_gff
        data = _make_minimal_gff("ARE ")
        result = _analyse_gff(data)
        assert "file_type" in result
        assert result["file_type"].strip() == "ARE"

    def test_analyse_gff_bad_data(self):
        from gmodular.mcp.tools.discovery import _analyse_gff
        result = _analyse_gff(b"not a gff")
        assert "error" in result

    def test_analyse_2da(self):
        from gmodular.mcp.tools.discovery import _analyse_2da
        raw = _make_minimal_2da(["NAME", "VALUE"], [["foo", "1"], ["bar", "2"]])
        result = _analyse_2da(raw)
        assert "columns" in result
        assert "row_count" in result

    def test_analyse_tlk(self):
        from gmodular.mcp.tools.discovery import _analyse_tlk
        raw = _make_minimal_tlk(["hello", "world"])
        result = _analyse_tlk(raw)
        assert result["string_count"] == 2
        assert result["language_id"] == 0

    def test_analyse_wok_real_file(self):
        from gmodular.mcp.tools.discovery import _analyse_wok
        wok_bytes = _make_minimal_wok()
        if not wok_bytes:
            pytest.skip("No test WOK available")
        result = _analyse_wok(wok_bytes)
        if "error" not in result:
            assert "face_count" in result
            assert result["face_count"] >= 0

    def test_analyse_lyt(self):
        from gmodular.mcp.tools.discovery import _analyse_lyt
        lyt_text = b"room model1 0 0 0\nroom model2 10 0 0\n"
        result = _analyse_lyt(lyt_text)
        assert result["room_count"] == 2


# ─────────────────────────────────────────────────────────────────────────────
#  Gamedata handlers
# ─────────────────────────────────────────────────────────────────────────────

class TestGamedataHandlers:
    def test_lookup_tlk_ok(self, fake_game):
        from gmodular.mcp.tools.gamedata import handle_lookup_tlk
        result = _run(handle_lookup_tlk({"game": "k1", "strref": 0}))
        data = _content_json(result)
        assert data["strref"] == 0
        assert data["text"] == "Entry zero"

    def test_lookup_tlk_strref_1(self, fake_game):
        from gmodular.mcp.tools.gamedata import handle_lookup_tlk
        result = _run(handle_lookup_tlk({"game": "k1", "strref": 1}))
        data = _content_json(result)
        assert data["text"] == "Entry one"

    def test_lookup_tlk_out_of_range(self, fake_game):
        from gmodular.mcp.tools.gamedata import handle_lookup_tlk
        with pytest.raises(ValueError, match="out of range"):
            _run(handle_lookup_tlk({"game": "k1", "strref": 9999}))

    def test_lookup_2da_not_found(self, fake_game):
        from gmodular.mcp.tools.gamedata import handle_lookup_2da
        with pytest.raises(ValueError, match="not found"):
            _run(handle_lookup_2da({"game": "k1", "table_name": "nonexistent"}))

    def test_journal_overview_no_jrl(self, fake_game):
        from gmodular.mcp.tools.gamedata import handle_journal_overview
        with pytest.raises(ValueError, match="not found"):
            _run(handle_journal_overview({"game": "k1"}))


# ─────────────────────────────────────────────────────────────────────────────
#  Conversion handlers
# ─────────────────────────────────────────────────────────────────────────────

class TestConversionHandlers:
    def test_read_gff_ok(self, fake_game):
        from gmodular.mcp.tools.conversion import handle_read_gff
        result = _run(handle_read_gff({"game": "k1", "resref": "testarea", "restype": "are"}))
        data = _content_json(result)
        assert data["resref"] == "testarea"
        assert "root" in data
        assert "file_type" in data

    def test_read_gff_not_found(self, fake_game):
        from gmodular.mcp.tools.conversion import handle_read_gff
        with pytest.raises(ValueError, match="not found"):
            _run(handle_read_gff({"game": "k1", "resref": "missing", "restype": "are"}))

    def test_read_2da_not_found(self, fake_game):
        from gmodular.mcp.tools.conversion import handle_read_2da
        with pytest.raises(ValueError, match="not found"):
            _run(handle_read_2da({"game": "k1", "resref": "missing2da"}))

    def test_read_tlk_range(self, fake_game):
        from gmodular.mcp.tools.conversion import handle_read_tlk
        result = _run(handle_read_tlk({
            "game": "k1", "strref_start": 0, "strref_end": 2, "limit": 10
        }))
        data = _content_json(result)
        assert data["total_entries"] == 3
        assert len(data["entries"]) == 2
        assert data["entries"][0]["text"] == "Entry zero"
        assert data["entries"][1]["text"] == "Entry one"

    def test_read_tlk_text_search(self, fake_game):
        from gmodular.mcp.tools.conversion import handle_read_tlk
        result = _run(handle_read_tlk({"game": "k1", "text_search": "two", "limit": 5}))
        data = _content_json(result)
        assert data["count"] == 1
        assert "two" in data["entries"][0]["text"].lower()

    def test_read_tlk_language_id(self, fake_game):
        from gmodular.mcp.tools.conversion import handle_read_tlk
        result = _run(handle_read_tlk({"game": "k1", "limit": 1}))
        data = _content_json(result)
        assert data["language_id"] == 0  # English


# ─────────────────────────────────────────────────────────────────────────────
#  Module handlers
# ─────────────────────────────────────────────────────────────────────────────

class TestModuleHandlers:
    def test_list_modules(self, fake_game):
        from gmodular.mcp.tools.modules import handle_list_modules
        result = _run(handle_list_modules({"game": "k1"}))
        data = _content_json(result)
        assert "modules" in data
        # Fake installation has testmod.mod
        module_roots = [m["module_root"] for m in data["modules"]]
        assert any("testmod" in r for r in module_roots)

    def test_describe_module(self, fake_game):
        from gmodular.mcp.tools.modules import handle_describe_module
        result = _run(handle_describe_module({"game": "k1", "module_root": "testmod"}))
        data = _content_json(result)
        assert data["module_root"] == "testmod"
        assert "resource_counts" in data

    def test_describe_module_not_found(self, fake_game):
        from gmodular.mcp.tools.modules import handle_describe_module
        with pytest.raises(ValueError, match="not found"):
            _run(handle_describe_module({"game": "k1", "module_root": "nonexistent"}))

    def test_module_resources(self, fake_game):
        from gmodular.mcp.tools.modules import handle_module_resources
        result = _run(handle_module_resources({"game": "k1", "module_root": "testmod"}))
        data = _content_json(result)
        assert "items" in data
        assert data["total"] >= 1

    def test_module_resources_empty(self, fake_game):
        from gmodular.mcp.tools.modules import handle_module_resources
        result = _run(handle_module_resources({"game": "k1", "module_root": "zzznope"}))
        data = _content_json(result)
        assert data["total"] == 0


# ─────────────────────────────────────────────────────────────────────────────
#  Refs handlers and helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestRefsHelpers:
    def _make_gff_struct_with_field(self, label: str, value: Any):
        from gmodular.formats.gff_types import GFFField, GFFStruct, GFFFieldType
        struct_obj = GFFStruct(struct_id=0)
        field = GFFField(label=label, type_id=int(GFFFieldType.RESREF), value=value)
        struct_obj.fields[label] = field
        return struct_obj

    def test_extract_refs_script(self):
        from gmodular.mcp.tools.refs import _extract_refs
        struct_obj = self._make_gff_struct_with_field("OnEnter", "myscript")
        refs = _extract_refs(struct_obj)
        assert any(r["ref_kind"] == "script" and r["value"] == "myscript" for r in refs)

    def test_extract_refs_conversation(self):
        from gmodular.mcp.tools.refs import _extract_refs
        struct_obj = self._make_gff_struct_with_field("Conversation", "myconvo")
        refs = _extract_refs(struct_obj)
        assert any(r["ref_kind"] == "conversation" for r in refs)

    def test_extract_refs_tag(self):
        from gmodular.mcp.tools.refs import _extract_refs
        struct_obj = self._make_gff_struct_with_field("Tag", "NPC_TAG_001")
        refs = _extract_refs(struct_obj)
        assert any(r["ref_kind"] == "tag" for r in refs)

    def test_extract_refs_resref(self):
        from gmodular.mcp.tools.refs import _extract_refs
        struct_obj = self._make_gff_struct_with_field("TemplateResRef", "npc_template")
        refs = _extract_refs(struct_obj)
        assert any(r["ref_kind"] == "resref" for r in refs)

    def test_extract_refs_empty(self):
        from gmodular.mcp.tools.refs import _extract_refs
        from gmodular.formats.gff_types import GFFStruct
        struct_obj = GFFStruct(struct_id=0)
        refs = _extract_refs(struct_obj)
        assert refs == []

    def test_extract_strrefs(self):
        from gmodular.mcp.tools.refs import _extract_strrefs
        from gmodular.formats.gff_types import GFFField, GFFStruct, GFFFieldType
        struct_obj = GFFStruct(struct_id=0)
        field = GFFField(label="Name", type_id=int(GFFFieldType.DWORD), value=42)
        struct_obj.fields["Name"] = field
        strrefs = _extract_strrefs(struct_obj)
        assert 42 in strrefs


class TestRefsHandlers:
    def test_list_references_ok(self, fake_game):
        from gmodular.mcp.tools.refs import handle_list_references
        result = _run(handle_list_references(
            {"game": "k1", "resref": "testarea", "restype": "are"}
        ))
        data = _content_json(result)
        assert "references" in data
        assert data["resref"] == "testarea"

    def test_list_references_not_found(self, fake_game):
        from gmodular.mcp.tools.refs import handle_list_references
        with pytest.raises(ValueError, match="not found"):
            _run(handle_list_references({"game": "k1", "resref": "nope", "restype": "are"}))

    def test_describe_dlg_not_found(self, fake_game):
        from gmodular.mcp.tools.refs import handle_describe_dlg
        with pytest.raises(ValueError, match="not found"):
            _run(handle_describe_dlg({"game": "k1", "resref": "nopedlg"}))

    def test_describe_jrl_not_found(self, fake_game):
        from gmodular.mcp.tools.refs import handle_describe_jrl
        with pytest.raises(ValueError, match="not found"):
            _run(handle_describe_jrl({"game": "k1", "resref": "nopejrl"}))

    def test_find_referrers_no_matches(self, fake_game):
        from gmodular.mcp.tools.refs import handle_find_referrers
        result = _run(handle_find_referrers(
            {"game": "k1", "value": "nonexistent_script_xyz", "reference_kind": "script"}
        ))
        data = _content_json(result)
        assert data["total"] == 0

    def test_find_strref_referrers_structure(self, fake_game):
        from gmodular.mcp.tools.refs import handle_find_strref_referrers
        result = _run(handle_find_strref_referrers({"game": "k1", "strref": 99999}))
        data = _content_json(result)
        assert "items" in data
        assert "total" in data
        assert data["strref"] == 99999

    def test_describe_resource_refs_ok(self, fake_game):
        from gmodular.mcp.tools.refs import handle_describe_resource_refs
        result = _run(handle_describe_resource_refs(
            {"game": "k1", "resref": "testarea", "restype": "are"}
        ))
        data = _content_json(result)
        assert "by_kind" in data
        assert "reference_count" in data

    def test_describe_resource_refs_not_found(self, fake_game):
        from gmodular.mcp.tools.refs import handle_describe_resource_refs
        with pytest.raises(ValueError, match="not found"):
            _run(handle_describe_resource_refs(
                {"game": "k1", "resref": "nope", "restype": "are"}
            ))


# ─────────────────────────────────────────────────────────────────────────────
#  Archives handlers
# ─────────────────────────────────────────────────────────────────────────────

class TestArchivesHandlers:
    def test_list_archive_erf(self, tmp_path):
        from gmodular.mcp.tools.archives import handle_list_archive
        erf_path = tmp_path / "test.mod"
        _write_fake_erf(erf_path, {("myres", 2012): _make_minimal_gff("ARE ")})
        result = _run(handle_list_archive({"file_path": str(erf_path)}))
        data = _content_json(result)
        assert data["total"] == 1
        assert data["items"][0]["resref"] == "myres"

    def test_list_archive_pagination(self, tmp_path):
        from gmodular.mcp.tools.archives import handle_list_archive
        erf_path = tmp_path / "big.mod"
        resources = {(f"res{i:03d}", 2012): _make_minimal_gff("ARE ") for i in range(10)}
        _write_fake_erf(erf_path, resources)
        result = _run(handle_list_archive({"file_path": str(erf_path), "limit": 5, "offset": 0}))
        data = _content_json(result)
        assert data["count"] == 5
        assert data["has_more"] is True

    def test_list_archive_not_found(self):
        from gmodular.mcp.tools.archives import handle_list_archive
        with pytest.raises(ValueError, match="not found"):
            _run(handle_list_archive({"file_path": "/nonexistent/path.erf"}))

    def test_list_archive_unsupported(self, tmp_path):
        from gmodular.mcp.tools.archives import handle_list_archive
        bad = tmp_path / "test.xyz"
        bad.write_bytes(b"junk")
        with pytest.raises(ValueError, match="Unsupported archive"):
            _run(handle_list_archive({"file_path": str(bad)}))

    def test_extract_resource_ok(self, fake_game, tmp_path):
        from gmodular.mcp.tools.archives import handle_extract_resource
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        result = _run(handle_extract_resource({
            "game": "k1", "resref": "testarea", "restype": "are",
            "output_path": str(out_dir),
        }))
        data = _content_json(result)
        assert data["status"] == "ok"
        assert Path(data["path"]).exists()

    def test_extract_resource_not_found(self, fake_game, tmp_path):
        from gmodular.mcp.tools.archives import handle_extract_resource
        with pytest.raises(ValueError, match="not found"):
            _run(handle_extract_resource({
                "game": "k1", "resref": "missing", "restype": "are",
                "output_path": str(tmp_path),
            }))


# ─────────────────────────────────────────────────────────────────────────────
#  Walkmesh handlers
# ─────────────────────────────────────────────────────────────────────────────

class TestWalkmeshHandlers:
    def test_walkmesh_info_not_found(self, fake_game):
        from gmodular.mcp.tools.walkmesh import handle_walkmesh_info
        with pytest.raises(ValueError, match="not found"):
            _run(handle_walkmesh_info({"game": "k1", "resref": "nonexistent"}))

    def test_mdl_info_not_found(self, fake_game):
        from gmodular.mcp.tools.walkmesh import handle_mdl_info
        with pytest.raises(ValueError, match="not found"):
            _run(handle_mdl_info({"game": "k1", "resref": "nonexistent"}))


# ─────────────────────────────────────────────────────────────────────────────
#  Surface material helpers (walkmesh internals)
# ─────────────────────────────────────────────────────────────────────────────

class TestSurfaceMaterials:
    def test_surf_names_contains_walk(self):
        from gmodular.formats.wok_parser import SURF_NAMES
        assert len(SURF_NAMES) > 0

    def test_is_walkable_known(self):
        from gmodular.formats.wok_parser import is_walkable
        # Material 1 is walkable per the lookup table
        assert is_walkable(1) is True

    def test_is_walkable_nonwalk(self):
        from gmodular.formats.wok_parser import is_walkable
        # Material 6 is non-walkable per _SURF_WALKABLE table
        assert is_walkable(6) is False

    def test_real_wok_surface_stats(self):
        from gmodular.formats.wok_parser import WOKParser, SURF_NAMES, is_walkable
        wok_bytes = _make_minimal_wok()
        if not wok_bytes:
            pytest.skip("No test WOK available")
        wok = WOKParser.from_bytes(wok_bytes)
        walkable = sum(1 for f in wok.faces if is_walkable(f.material))
        non_walkable = len(wok.faces) - walkable
        assert walkable + non_walkable == len(wok.faces)


# ─────────────────────────────────────────────────────────────────────────────
#  Server dispatch tests
# ─────────────────────────────────────────────────────────────────────────────

class TestServerDispatch:
    def test_initialize(self):
        from gmodular.mcp.server import _handle_request
        result = _run(_handle_request("initialize", {}))
        assert result["protocolVersion"] == "2024-11-05"
        assert "serverInfo" in result

    def test_tools_list(self):
        from gmodular.mcp.server import _handle_request
        result = _run(_handle_request("tools/list", {}))
        assert "tools" in result
        assert len(result["tools"]) >= 20

    def test_tools_call_unknown(self):
        from gmodular.mcp.server import _handle_request
        result = _run(_handle_request("tools/call", {"name": "nonexistent_tool", "arguments": {}}))
        # Should return an error payload rather than raise
        content_text = result["content"][0]["text"]
        parsed = json.loads(content_text)
        assert "error" in parsed

    def test_tools_call_detect_installations(self):
        from gmodular.mcp.server import _handle_request
        result = _run(_handle_request("tools/call", {
            "name": "detectInstallations", "arguments": {}
        }))
        content_text = result["content"][0]["text"]
        data = json.loads(content_text)
        assert "K1" in data or "K2" in data

    def test_unknown_method_returns_none(self):
        from gmodular.mcp.server import _handle_request
        result = _run(_handle_request("notifications/whatever", {}))
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
#  __main__ module
# ─────────────────────────────────────────────────────────────────────────────

class TestMainModule:
    def test_main_importable(self):
        import gmodular.mcp.__main__  # noqa: F401 — just check import succeeds

    def test_main_function_exists(self):
        from gmodular.mcp.server import main
        assert callable(main)


# ─────────────────────────────────────────────────────────────────────────────
#  GFF types used by refs
# ─────────────────────────────────────────────────────────────────────────────

class TestGFFTypes:
    def test_gff_field_type_enum(self):
        from gmodular.formats.gff_types import GFFFieldType
        assert hasattr(GFFFieldType, "CResRef") or hasattr(GFFFieldType, "DWORD")

    def test_gff_struct_get(self):
        from gmodular.formats.gff_types import GFFField, GFFStruct, GFFFieldType
        s = GFFStruct(struct_id=0)
        field = GFFField(label="Tag", type_id=10, value="hello")
        s.fields["Tag"] = field
        # GFFStruct.get returns field.value
        val = s.get("Tag")
        assert val == "hello"

    def test_gff_root_has_file_type(self):
        from gmodular.formats.gff_types import GFFRoot
        root = GFFRoot()
        assert hasattr(root, "file_type")
        assert hasattr(root, "file_version")


# ─────────────────────────────────────────────────────────────────────────────
#  Edge cases / robustness
# ─────────────────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_handle_tool_unknown_raises(self):
        from gmodular.mcp.tools import handle_tool
        with pytest.raises(ValueError, match="Unknown tool"):
            _run(handle_tool("completely_unknown_tool_xyz", {}))

    def test_json_content_bytes_value(self):
        from gmodular.mcp._formatting import json_content
        # bytes in payload should not raise (default=str covers it)
        result = json_content({"raw": b"binary data"})
        text = _content_text(result)
        assert "binary" in text or "b'" in text or "raw" in text

    def test_list_resources_no_game(self, fake_game):
        from gmodular.mcp.tools.discovery import handle_list_resources
        with pytest.raises(ValueError, match="Specify game"):
            _run(handle_list_resources({}))

    def test_find_resource_no_game(self, fake_game):
        from gmodular.mcp.tools.discovery import handle_find_resource
        with pytest.raises(ValueError, match="Specify game"):
            _run(handle_find_resource({"query": "test*"}))

    def test_describe_resource_no_game(self, fake_game):
        from gmodular.mcp.tools.discovery import handle_describe_resource
        with pytest.raises(ValueError, match="Specify game"):
            _run(handle_describe_resource({"resref": "test", "restype": "are"}))

    def test_gff_to_dict_empty_root(self):
        from gmodular.mcp.tools.conversion import _gff_to_dict
        from gmodular.formats.gff_types import GFFRoot
        root = GFFRoot()
        result = _gff_to_dict(root, max_depth=5, max_fields=100, depth=0, counter=[0])
        assert isinstance(result, dict)

    def test_gff_to_dict_max_fields(self):
        from gmodular.mcp.tools.conversion import _gff_to_dict
        from gmodular.formats.gff_types import GFFField, GFFRoot, GFFFieldType
        root = GFFRoot()
        for i in range(10):
            field = GFFField(label=f"Field{i}", type_id=4, value=i)
            root.fields[f"Field{i}"] = field
        result = _gff_to_dict(root, max_depth=5, max_fields=3, depth=0, counter=[0])
        assert "_truncated" in result

    def test_entry_snapshot_fields(self, tmp_path):
        from gmodular.mcp._indexer import ResourceEntry
        from gmodular.mcp.tools.discovery import _entry_snapshot
        entry = ResourceEntry(
            resref="test", ext="are", source="override",
            filepath=tmp_path / "test.are", size=100,
        )
        snap = _entry_snapshot(entry)
        assert snap["resref"] == "test"
        assert snap["extension"] == "are"
        assert snap["source"] == "override"
        assert snap["size"] == 100
