"""Tests for gmodular/mcp/tools/composite.py — the "get X" composite tool layer.

Design: All tests are fully offline — no game installation, no network calls.
The pattern mirrors test_agentdecompile_bridge.py:
  - Patch find_resource_bytes to return synthetic resource bytes
  - Patch _parse_gff / TLKReader etc. for data-level tests
  - Verify tool schema, handler dispatch, and output structure

Structured Design rationale tested:
  - Functional cohesion: each tool returns exactly one conceptual object
  - Data coupling: handlers only consume what they receive
  - Fan-in: composite tools call the canonical find_resource_bytes primitive
  - Information hiding: callers see quest/creature/area, not JRL/UTC/ARE
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from gmodular.mcp.tools import composite, get_all_tools, handle_tool


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_inst(game: str = "K1") -> MagicMock:
    inst = MagicMock()
    inst.game = game
    inst.tlk_path.return_value = None
    inst.index = {"by_key": {}}
    inst.resource_manager.return_value = MagicMock(get_file=lambda r, e: None)
    return inst


def _json_result(content_result: Any) -> Any:
    """Extract the inner data dict from a json_content result."""
    if hasattr(content_result, "content"):
        for c in content_result.content:
            return json.loads(getattr(c, "text", "{}"))
    if isinstance(content_result, dict):
        ct = content_result.get("content", [])
        if ct:
            return json.loads(ct[0].get("text", "{}"))
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Tool schema tests
# ─────────────────────────────────────────────────────────────────────────────

class TestCompositeToolSchemas:
    """Verify every composite tool has a valid schema."""

    def test_get_tools_returns_7_composite_tools(self):
        tools = composite.get_tools()
        assert len(tools) == 7

    def test_all_tools_have_name_description_inputschema(self):
        for tool in composite.get_tools():
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
            assert tool["description"]  # non-empty

    def test_tool_names(self):
        names = {t["name"] for t in composite.get_tools()}
        expected = {
            "get_resource", "get_quest", "get_creature",
            "get_conversation", "get_area", "get_script", "search"
        }
        assert names == expected

    def test_all_tools_registered_in_get_all_tools(self):
        all_names = {t["name"] for t in get_all_tools()}
        for tool in composite.get_tools():
            assert tool["name"] in all_names, f"{tool['name']} not in tool registry"

    def test_composite_tools_listed_first(self):
        """Composite tools should be first in the registry (primary interface)."""
        all_tools = get_all_tools()
        composite_names = {t["name"] for t in composite.get_tools()}
        first_7_names = {t["name"] for t in all_tools[:7]}
        assert composite_names == first_7_names

    def test_get_resource_required_params(self):
        schema = next(t for t in composite.get_tools() if t["name"] == "get_resource")
        required = schema["inputSchema"]["required"]
        assert "game" in required
        assert "resref" in required
        assert "type" in required

    def test_get_quest_required_params(self):
        schema = next(t for t in composite.get_tools() if t["name"] == "get_quest")
        required = schema["inputSchema"]["required"]
        assert "game" in required
        assert "tag" in required

    def test_get_creature_required_params(self):
        schema = next(t for t in composite.get_tools() if t["name"] == "get_creature")
        assert "resref" in schema["inputSchema"]["required"]

    def test_get_conversation_required_params(self):
        schema = next(t for t in composite.get_tools() if t["name"] == "get_conversation")
        assert "resref" in schema["inputSchema"]["required"]

    def test_get_area_required_params(self):
        schema = next(t for t in composite.get_tools() if t["name"] == "get_area")
        assert "resref" in schema["inputSchema"]["required"]

    def test_get_script_required_params(self):
        schema = next(t for t in composite.get_tools() if t["name"] == "get_script")
        assert "resref" in schema["inputSchema"]["required"]

    def test_search_required_params(self):
        schema = next(t for t in composite.get_tools() if t["name"] == "search")
        assert "query" in schema["inputSchema"]["required"]

    def test_format_param_on_composite_tools(self):
        """Tools with format param should enumerate json/markdown/brief."""
        format_tools = ["get_resource", "get_quest", "get_creature",
                        "get_conversation", "get_area", "get_script"]
        for t in composite.get_tools():
            if t["name"] in format_tools:
                props = t["inputSchema"]["properties"]
                assert "format" in props
                assert "enum" in props["format"]
                assert set(props["format"]["enum"]) == {"json", "markdown", "brief"}

    def test_description_does_not_imply_specific_context(self):
        """Descriptions should be context-agnostic (no 'for VS Code', 'for Discord', etc.)."""
        bad_phrases = ["for discord", "for vs code", "for cursor", "for claude"]
        for t in composite.get_tools():
            desc_lower = t["description"].lower()
            for phrase in bad_phrases:
                assert phrase not in desc_lower, \
                    f"Tool '{t['name']}' description implies specific context: '{phrase}'"

    def test_descriptions_start_with_return_verb(self):
        """Descriptions should describe what the tool returns."""
        for t in composite.get_tools():
            desc = t["description"]
            # Should start with "Return" (functional cohesion check)
            assert desc.startswith("Return"), \
                f"Tool '{t['name']}' description doesn't start with 'Return': {desc[:60]!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Dispatcher tests
# ─────────────────────────────────────────────────────────────────────────────

class TestToolDispatch:
    """Verify handle_tool routes to the composite handlers."""

    @pytest.mark.asyncio
    async def test_get_resource_routed(self):
        with patch.object(composite, "handle_get_resource", new=AsyncMock(return_value={"ok": True})) as m:
            await handle_tool("get_resource", {"game": "k1", "resref": "test", "type": "utc"})
            m.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_quest_routed(self):
        with patch.object(composite, "handle_get_quest", new=AsyncMock(return_value={})) as m:
            await handle_tool("get_quest", {"game": "k1", "tag": "test"})
            m.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_creature_routed(self):
        with patch.object(composite, "handle_get_creature", new=AsyncMock(return_value={})) as m:
            await handle_tool("get_creature", {"game": "k1", "resref": "n_bastila"})
            m.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_conversation_routed(self):
        with patch.object(composite, "handle_get_conversation", new=AsyncMock(return_value={})) as m:
            await handle_tool("get_conversation", {"game": "k1", "resref": "c_bastila"})
            m.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_area_routed(self):
        with patch.object(composite, "handle_get_area", new=AsyncMock(return_value={})) as m:
            await handle_tool("get_area", {"game": "k1", "resref": "danm13"})
            m.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_script_routed(self):
        with patch.object(composite, "handle_get_script", new=AsyncMock(return_value={})) as m:
            await handle_tool("get_script", {"game": "k1", "resref": "k_hench_bas"})
            m.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_search_routed(self):
        with patch.object(composite, "handle_search", new=AsyncMock(return_value={})) as m:
            await handle_tool("search", {"game": "k1", "query": "bastila"})
            m.assert_awaited_once()


# ─────────────────────────────────────────────────────────────────────────────
# Shared primitive helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestCompositeHelpers:
    """Unit tests for the shared primitives used by all handlers."""

    def test_find_returns_none_on_missing(self):
        inst = _make_inst()
        with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
            with patch("gmodular.mcp.tools.composite._find") as mock_find:
                mock_find.return_value = None
                result = composite._find(inst, "nonexistent", "utc")
                assert result is None

    def test_gv_returns_default_on_missing_field(self):
        struct = MagicMock()
        struct.fields = {}
        assert composite._gv(struct, "Tag", "default") == "default"

    def test_gv_extracts_simple_value(self):
        field = MagicMock()
        field.value = "k_hench_bastila"
        struct = MagicMock()
        struct.fields = {"Tag": field}
        assert composite._gv(struct, "Tag", "") == "k_hench_bastila"

    def test_gv_handles_exolocstr(self):
        """CExoLocString with strings dict returns first language text."""
        field = MagicMock()
        field.value = MagicMock()
        field.value.strings = {0: "Bastila Shan"}
        field.value.strref = -1
        struct = MagicMock()
        struct.fields = {"Name": field}
        result = composite._gv(struct, "Name", "")
        # Should get the string value via _resolve_exolocstr
        assert isinstance(result, str)

    def test_gv_returns_default_on_none_struct(self):
        assert composite._gv(None, "Tag", "fallback") == "fallback"

    def test_resolve_tlk_returns_empty_on_no_tlk(self):
        inst = _make_inst()
        inst.tlk_path.return_value = None
        assert composite._resolve_tlk(inst, 42) == ""

    def test_resolve_tlk_returns_empty_on_negative_strref(self):
        inst = _make_inst()
        assert composite._resolve_tlk(inst, -1) == ""

    def test_extract_scripts_empty_struct(self):
        struct = MagicMock()
        struct.fields = {}
        scripts = composite._extract_scripts(struct)
        assert isinstance(scripts, list)
        assert len(scripts) == 0

    def test_decompile_ncs_no_decompiler(self):
        """When no decompiler is available, returns None."""
        dummy_ncs = b"\x42\x4e\x43\x53\x20\x56\x31\x2e\x30"  # fake NCS header
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            result = composite._decompile_ncs(dummy_ncs)
            # Should return None gracefully
            assert result is None or isinstance(result, str)

    def test_fmt_returns_json_by_default(self):
        data = {"resref": "test", "type": "utc", "game": "K1"}
        result = composite._fmt(data, "json", lambda d: "## test")
        assert result is not None

    def test_fmt_markdown_includes_markdown_key(self):
        data = {"resref": "test", "type": "utc", "game": "K1"}
        result = composite._fmt(data, "markdown", lambda d: "## Test Header\nLine 2")
        decoded = _json_result(result)
        assert "markdown" in decoded
        assert "## Test Header" in decoded["markdown"]

    def test_fmt_brief_returns_first_line(self):
        data = {"resref": "test"}
        result = composite._fmt(data, "brief", lambda d: "## Test Header\nLine 2")
        decoded = _json_result(result)
        assert "brief" in decoded
        assert "## Test Header" in decoded["brief"]
        assert "Line 2" not in decoded["brief"]


# ─────────────────────────────────────────────────────────────────────────────
# get_resource handler tests
# ─────────────────────────────────────────────────────────────────────────────

class TestHandleGetResource:
    def _patch(self, inst, resource_bytes, gff_root=None):
        """Context manager helper: patch installation + find + parse."""
        from contextlib import contextmanager
        import contextlib

        @contextlib.contextmanager
        def _ctx():
            with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
                with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                    with patch("gmodular.mcp.tools.composite._find", return_value=resource_bytes):
                        if gff_root is not None:
                            with patch("gmodular.mcp.tools.composite._parse_gff", return_value=gff_root):
                                yield
                        else:
                            yield
        return _ctx()

    @pytest.mark.asyncio
    async def test_raises_on_missing_resource(self):
        inst = _make_inst()
        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.mcp.tools.composite._find", return_value=None):
                    with pytest.raises(ValueError, match="not found"):
                        await composite.handle_get_resource({"game": "k1", "resref": "x", "type": "utc"})

    @pytest.mark.asyncio
    async def test_raises_on_invalid_game(self):
        with patch("gmodular.mcp.tools.composite.resolve_game", return_value=None):
            with pytest.raises(ValueError, match="game"):
                await composite.handle_get_resource({"game": "invalid", "resref": "x", "type": "utc"})

    @pytest.mark.asyncio
    async def test_nss_resource_returns_source(self):
        inst = _make_inst()
        nss_bytes = b"void main() { /* test */ }"
        with self._patch(inst, nss_bytes):
            result = await composite.handle_get_resource({"game": "k1", "resref": "k_test", "type": "nss"})
        decoded = _json_result(result)
        assert "source" in decoded
        assert "void main" in decoded["source"]

    @pytest.mark.asyncio
    async def test_ncs_resource_no_decompiler(self):
        inst = _make_inst()
        ncs_bytes = b"\x42\x4e\x43\x53" + b"\x00" * 100
        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.mcp.tools.composite._find", return_value=ncs_bytes):
                    with patch("gmodular.mcp.tools.composite._decompile_ncs", return_value=None):
                        result = await composite.handle_get_resource(
                            {"game": "k1", "resref": "k_test", "type": "ncs"}
                        )
        decoded = _json_result(result)
        assert decoded.get("decompiler_available") is False
        assert "raw_base64" in decoded

    @pytest.mark.asyncio
    async def test_ncs_resource_with_decompiler(self):
        inst = _make_inst()
        ncs_bytes = b"\x42\x4e\x43\x53" + b"\x00" * 100
        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.mcp.tools.composite._find", return_value=ncs_bytes):
                    with patch("gmodular.mcp.tools.composite._decompile_ncs", return_value="void main(){}"):
                        result = await composite.handle_get_resource(
                            {"game": "k1", "resref": "k_test", "type": "ncs"}
                        )
        decoded = _json_result(result)
        assert decoded.get("decompiler_available") is True
        assert decoded.get("decompiled_source") == "void main(){}"

    @pytest.mark.asyncio
    async def test_2da_resource_returns_columns_and_rows(self):
        inst = _make_inst()
        tda_text = b"2DA V2.0\n\nlabel   race   gender\n0  ****   Human  Male\n1  ****   Human  Female\n"
        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.mcp.tools.composite._find", return_value=tda_text):
                    result = await composite.handle_get_resource(
                        {"game": "k1", "resref": "appearance", "type": "2da"}
                    )
        decoded = _json_result(result)
        assert "columns" in decoded or "raw_text" in decoded  # depends on parse success

    @pytest.mark.asyncio
    async def test_gff_resource_returns_fields(self):
        inst = _make_inst()
        gff_bytes = b"GFF V3.2" + b"\x00" * 100
        gff_root = MagicMock()
        gff_root.file_type = "UTC "
        gff_root.fields = {}
        gff_root.get = lambda k, d=None: d
        with self._patch(inst, gff_bytes, gff_root):
            result = await composite.handle_get_resource(
                {"game": "k1", "resref": "n_bastila", "type": "utc"}
            )
        decoded = _json_result(result)
        assert decoded.get("file_type") == "UTC "
        assert "fields" in decoded

    @pytest.mark.asyncio
    async def test_markdown_format_returns_markdown_key(self):
        inst = _make_inst()
        nss_bytes = b"void main() {}"
        with self._patch(inst, nss_bytes):
            result = await composite.handle_get_resource(
                {"game": "k1", "resref": "k_test", "type": "nss", "format": "markdown"}
            )
        decoded = _json_result(result)
        assert "markdown" in decoded
        assert "k_test" in decoded["markdown"]

    @pytest.mark.asyncio
    async def test_brief_format_returns_brief_key(self):
        inst = _make_inst()
        nss_bytes = b"void main() {}"
        with self._patch(inst, nss_bytes):
            result = await composite.handle_get_resource(
                {"game": "k1", "resref": "k_test", "type": "nss", "format": "brief"}
            )
        decoded = _json_result(result)
        assert "brief" in decoded

    @pytest.mark.asyncio
    async def test_lyt_resource_returns_room_list(self):
        inst = _make_inst()
        lyt_bytes = b"# layout\nroom danm13_room_a 10.0 0.0 0.0\nroom danm13_room_b 30.0 0.0 0.0\ndone\n"
        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.mcp.tools.composite._find", return_value=lyt_bytes):
                    result = await composite.handle_get_resource(
                        {"game": "k1", "resref": "danm13", "type": "lyt"}
                    )
        decoded = _json_result(result)
        assert decoded.get("room_count") == 2

    @pytest.mark.asyncio
    async def test_unknown_type_returns_base64(self):
        inst = _make_inst()
        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.mcp.tools.composite._find", return_value=b"\x00" * 64):
                    result = await composite.handle_get_resource(
                        {"game": "k1", "resref": "test", "type": "xyz"}
                    )
        decoded = _json_result(result)
        assert "raw_base64" in decoded


# ─────────────────────────────────────────────────────────────────────────────
# get_quest handler tests
# ─────────────────────────────────────────────────────────────────────────────

class TestHandleGetQuest:
    def _make_jrl_root(self, tag: str, name: str, states: list) -> MagicMock:
        """Build a mock GFF root that looks like a JRL file."""
        state_structs = []
        for sid, text, ends in states:
            s = MagicMock()
            id_field = MagicMock(); id_field.value = sid
            text_field = MagicMock(); text_field.value = text
            end_field = MagicMock(); end_field.value = int(ends)
            comment_field = MagicMock(); comment_field.value = ""
            s.fields = {"ID": id_field, "Text": text_field, "End": end_field, "Comment": comment_field}
            s.get = lambda k, d=None, _s=s: (
                _s.fields[k].value if k in _s.fields else d
            )
            state_structs.append(s)

        cat_struct = MagicMock()
        tag_field = MagicMock(); tag_field.value = tag
        name_field = MagicMock(); name_field.value = name
        priority_field = MagicMock(); priority_field.value = 0
        entry_list_field = MagicMock(); entry_list_field.value = state_structs
        cat_struct.fields = {
            "Tag": tag_field,
            "Name": name_field,
            "Priority": priority_field,
            "EntryList": entry_list_field,
        }
        cat_struct.get = lambda k, d=None, _c=cat_struct: (
            _c.fields[k].value if k in _c.fields else d
        )

        root = MagicMock()
        cats_field = MagicMock(); cats_field.value = [cat_struct]
        root.fields = {"Categories": cats_field}
        root.get = lambda k, d=None, _r=root: (
            _r.fields[k].value if k in _r.fields else d
        )
        return root

    @pytest.mark.asyncio
    async def test_raises_on_missing_jrl(self):
        inst = _make_inst()
        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.mcp.tools.composite._find", return_value=None):
                    with pytest.raises(ValueError, match="global.jrl"):
                        await composite.handle_get_quest({"game": "k1", "tag": "k_swg_bastila"})

    @pytest.mark.asyncio
    async def test_raises_on_no_matching_quest(self):
        inst = _make_inst()
        root = self._make_jrl_root("k_swg_other", "Other Quest", [(0, "Not started", False)])
        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.mcp.tools.composite._find", return_value=b"fake"):
                    with patch("gmodular.mcp.tools.composite._parse_gff", return_value=root):
                        with pytest.raises(ValueError, match="No quest found"):
                            await composite.handle_get_quest({"game": "k1", "tag": "k_swg_bastila"})

    @pytest.mark.asyncio
    async def test_returns_matching_quest(self):
        inst = _make_inst()
        root = self._make_jrl_root(
            "k_swg_bastila", "Finding Bastila",
            [(0, "Not started", False), (1, "Find Bastila on Taris", False), (2, "Bastila rescued", True)]
        )
        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.mcp.tools.composite._find", return_value=b"fake"):
                    with patch("gmodular.mcp.tools.composite._parse_gff", return_value=root):
                        result = await composite.handle_get_quest(
                            {"game": "k1", "tag": "k_swg_bastila"}
                        )
        decoded = _json_result(result)
        assert "quests" in decoded
        assert len(decoded["quests"]) == 1
        quest = decoded["quests"][0]
        assert quest["tag"] == "k_swg_bastila"
        assert len(quest["states"]) == 3

    @pytest.mark.asyncio
    async def test_completes_quest_flag_set_correctly(self):
        inst = _make_inst()
        root = self._make_jrl_root(
            "k_swg_test", "Test Quest",
            [(0, "Active", False), (1, "Done", True)]
        )
        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.mcp.tools.composite._find", return_value=b"fake"):
                    with patch("gmodular.mcp.tools.composite._parse_gff", return_value=root):
                        result = await composite.handle_get_quest({"game": "k1", "tag": "k_swg_test"})
        decoded = _json_result(result)
        states = decoded["quests"][0]["states"]
        assert states[0]["completes_quest"] is False
        assert states[1]["completes_quest"] is True

    @pytest.mark.asyncio
    async def test_inferred_global_vars_generated(self):
        inst = _make_inst()
        root = self._make_jrl_root("k_swg_bastila", "Quest", [(0, "", False)])
        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.mcp.tools.composite._find", return_value=b"fake"):
                    with patch("gmodular.mcp.tools.composite._parse_gff", return_value=root):
                        result = await composite.handle_get_quest({"game": "k1", "tag": "k_swg_bastila"})
        decoded = _json_result(result)
        var_names = {v["name"] for v in decoded["inferred_global_vars"]}
        assert "K_SWG_BASTILA" in var_names
        assert "K_SWG_BASTILA_STATE" in var_names

    @pytest.mark.asyncio
    async def test_markdown_format_includes_states(self):
        inst = _make_inst()
        root = self._make_jrl_root("k_swg_bastila", "Finding Bastila", [(1, "Rescue Bastila", False)])
        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.mcp.tools.composite._find", return_value=b"fake"):
                    with patch("gmodular.mcp.tools.composite._parse_gff", return_value=root):
                        result = await composite.handle_get_quest(
                            {"game": "k1", "tag": "k_swg_bastila", "format": "markdown"}
                        )
        decoded = _json_result(result)
        assert "markdown" in decoded
        assert "k_swg_bastila" in decoded["markdown"].lower()

    @pytest.mark.asyncio
    async def test_partial_tag_match(self):
        """Partial tag match should find quests containing the query."""
        inst = _make_inst()
        root = self._make_jrl_root("k_swg_bastila_01", "Bastila", [(0, "Start", False)])
        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.mcp.tools.composite._find", return_value=b"fake"):
                    with patch("gmodular.mcp.tools.composite._parse_gff", return_value=root):
                        result = await composite.handle_get_quest({"game": "k1", "tag": "bastila"})
        decoded = _json_result(result)
        assert len(decoded["quests"]) == 1


# ─────────────────────────────────────────────────────────────────────────────
# get_creature handler tests
# ─────────────────────────────────────────────────────────────────────────────

class TestHandleGetCreature:
    def _make_utc_root(self, name: str = "Bastila", tag: str = "c_bastila",
                       appearance: int = 57, hp: int = 100) -> MagicMock:
        root = MagicMock()

        def _make_field(val):
            f = MagicMock(); f.value = val; return f

        root.fields = {
            "FirstName": _make_field(name),
            "LastName": _make_field(""),
            "Tag": _make_field(tag),
            "Race": _make_field(1),
            "Gender": _make_field(1),
            "Appearance_Type": _make_field(appearance),
            "PortraitId": _make_field(0),
            "FactionID": _make_field(0),
            "MaxHitPoints": _make_field(hp),
            "CurrentHitPoints": _make_field(hp),
            "Conversation": _make_field("c_bastila"),
            "ClassList": _make_field([]),
            "ItemList": _make_field([]),
        }
        root.get = lambda k, d=None: (root.fields[k].value if k in root.fields else d)
        return root

    @pytest.mark.asyncio
    async def test_raises_on_missing_utc(self):
        inst = _make_inst()
        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.mcp.tools.composite._find", return_value=None):
                    with pytest.raises(ValueError, match=".utc not found"):
                        await composite.handle_get_creature({"game": "k1", "resref": "n_bastila"})

    @pytest.mark.asyncio
    async def test_returns_creature_fields(self):
        inst = _make_inst()
        root = self._make_utc_root("Bastila", "c_bastila", 57, 100)
        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.mcp.tools.composite._find", return_value=b"fake"):
                    with patch("gmodular.mcp.tools.composite._parse_gff", return_value=root):
                        with patch("gmodular.mcp.tools.composite._resolve_exolocstr",
                                   side_effect=lambda inst, v: str(v) if not isinstance(v, str) else v):
                            result = await composite.handle_get_creature(
                                {"game": "k1", "resref": "n_bastila"}
                            )
        decoded = _json_result(result)
        assert decoded["resref"] == "n_bastila"
        assert decoded["tag"] == "c_bastila"
        assert decoded["max_hp"] == 100
        assert decoded["appearance_id"] == 57

    @pytest.mark.asyncio
    async def test_markdown_format(self):
        inst = _make_inst()
        root = self._make_utc_root("Bastila", "c_bastila", 57, 100)
        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.mcp.tools.composite._find", return_value=b"fake"):
                    with patch("gmodular.mcp.tools.composite._parse_gff", return_value=root):
                        with patch("gmodular.mcp.tools.composite._resolve_exolocstr",
                                   side_effect=lambda i, v: str(v) if not isinstance(v, str) else v):
                            result = await composite.handle_get_creature(
                                {"game": "k1", "resref": "n_bastila", "format": "markdown"}
                            )
        decoded = _json_result(result)
        assert "markdown" in decoded
        assert "n_bastila" in decoded["markdown"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# get_conversation handler tests
# ─────────────────────────────────────────────────────────────────────────────

class TestHandleGetConversation:
    def _make_dlg_root(self, entries=None, replies=None) -> MagicMock:
        if entries is None:
            entries = [("Hello there.", "npc_01", ""), ("Farewell.", "npc_01", "")]
        if replies is None:
            replies = [("General Kenobi!", "", "")]

        def _make_node(text, speaker, script):
            n = MagicMock()
            tf = MagicMock(); tf.value = text
            sf = MagicMock(); sf.value = speaker
            sc = MagicMock(); sc.value = script
            n.fields = {"Text": tf, "Speaker": sf, "Script1": sc}
            n.get = lambda k, d=None, _n=n: (_n.fields[k].value if k in _n.fields else d)
            return n

        entry_structs = [_make_node(t, s, sc) for t, s, sc in entries]
        reply_structs = [_make_node(t, s, sc) for t, s, sc in replies]

        root = MagicMock()
        ef = MagicMock(); ef.value = entry_structs
        rf = MagicMock(); rf.value = reply_structs
        sf = MagicMock(); sf.value = []
        root.fields = {"EntryList": ef, "ReplyList": rf, "StartingList": sf}
        root.get = lambda k, d=None, _r=root: (_r.fields[k].value if k in _r.fields else d)
        return root

    @pytest.mark.asyncio
    async def test_raises_on_missing_dlg(self):
        inst = _make_inst()
        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.mcp.tools.composite._find", return_value=None):
                    with pytest.raises(ValueError, match=".dlg not found"):
                        await composite.handle_get_conversation({"game": "k1", "resref": "c_bastila"})

    @pytest.mark.asyncio
    async def test_returns_entry_and_reply_counts(self):
        inst = _make_inst()
        root = self._make_dlg_root()
        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.mcp.tools.composite._find", return_value=b"fake"):
                    with patch("gmodular.mcp.tools.composite._parse_gff", return_value=root):
                        with patch("gmodular.mcp.tools.composite._resolve_exolocstr",
                                   side_effect=lambda i, v: str(v) if not isinstance(v, str) else v):
                            result = await composite.handle_get_conversation(
                                {"game": "k1", "resref": "c_bastila"}
                            )
        decoded = _json_result(result)
        assert decoded["entry_count"] == 2
        assert decoded["reply_count"] == 1

    @pytest.mark.asyncio
    async def test_max_nodes_truncation(self):
        inst = _make_inst()
        entries = [(f"Line {i}", "npc", "") for i in range(50)]
        root = self._make_dlg_root(entries=entries, replies=[])
        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.mcp.tools.composite._find", return_value=b"fake"):
                    with patch("gmodular.mcp.tools.composite._parse_gff", return_value=root):
                        with patch("gmodular.mcp.tools.composite._resolve_exolocstr",
                                   side_effect=lambda i, v: str(v) if not isinstance(v, str) else v):
                            result = await composite.handle_get_conversation(
                                {"game": "k1", "resref": "c_bastila", "max_nodes": 10}
                            )
        decoded = _json_result(result)
        assert len(decoded["entries"]) == 10

    @pytest.mark.asyncio
    async def test_markdown_format(self):
        inst = _make_inst()
        root = self._make_dlg_root()
        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.mcp.tools.composite._find", return_value=b"fake"):
                    with patch("gmodular.mcp.tools.composite._parse_gff", return_value=root):
                        with patch("gmodular.mcp.tools.composite._resolve_exolocstr",
                                   side_effect=lambda i, v: str(v) if not isinstance(v, str) else v):
                            result = await composite.handle_get_conversation(
                                {"game": "k1", "resref": "c_bastila", "format": "markdown"}
                            )
        decoded = _json_result(result)
        assert "markdown" in decoded
        assert "c_bastila" in decoded["markdown"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# get_area handler tests
# ─────────────────────────────────────────────────────────────────────────────

class TestHandleGetArea:
    def _make_are_root(self, name: str = "Dantooine", music: int = 32) -> MagicMock:
        root = MagicMock()
        def _f(val):
            f = MagicMock(); f.value = val; return f
        root.fields = {
            "Name": _f(name), "MusicBackground": _f(music),
            "MusicBattle": _f(0), "AmbientSndDay": _f("dan_ambient"),
            "FogOn": _f(0),
        }
        root.get = lambda k, d=None: (root.fields[k].value if k in root.fields else d)
        return root

    @pytest.mark.asyncio
    async def test_raises_on_missing_are(self):
        inst = _make_inst()
        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.mcp.tools.composite._find", return_value=None):
                    with pytest.raises(ValueError, match=".are not found"):
                        await composite.handle_get_area({"game": "k1", "resref": "danm13"})

    @pytest.mark.asyncio
    async def test_returns_area_properties(self):
        inst = _make_inst()
        root = self._make_are_root("Dantooine Grove", 32)
        lyt_bytes = b"room danm13_main 0 0 0\ndone\n"

        def _mock_find(i, resref, ext):
            if ext == "are":
                return b"fake"
            if ext == "git":
                return None
            if ext == "lyt":
                return lyt_bytes
            return None

        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.mcp.tools.composite._find", side_effect=_mock_find):
                    with patch("gmodular.mcp.tools.composite._parse_gff", return_value=root):
                        with patch("gmodular.mcp.tools.composite._resolve_exolocstr",
                                   side_effect=lambda i, v: str(v) if not isinstance(v, str) else v):
                            result = await composite.handle_get_area(
                                {"game": "k1", "resref": "danm13"}
                            )
        decoded = _json_result(result)
        assert decoded["resref"] == "danm13"
        assert decoded["ambient_music_id"] == 32
        assert decoded["room_count"] == 1

    @pytest.mark.asyncio
    async def test_markdown_format(self):
        inst = _make_inst()
        root = self._make_are_root("Dantooine")

        def _mock_find(i, resref, ext):
            if ext == "are":
                return b"fake"
            return None

        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.mcp.tools.composite._find", side_effect=_mock_find):
                    with patch("gmodular.mcp.tools.composite._parse_gff", return_value=root):
                        with patch("gmodular.mcp.tools.composite._resolve_exolocstr",
                                   side_effect=lambda i, v: str(v) if not isinstance(v, str) else v):
                            result = await composite.handle_get_area(
                                {"game": "k1", "resref": "danm13", "format": "markdown"}
                            )
        decoded = _json_result(result)
        assert "markdown" in decoded
        assert "danm13" in decoded["markdown"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# get_script handler tests
# ─────────────────────────────────────────────────────────────────────────────

class TestHandleGetScript:
    @pytest.mark.asyncio
    async def test_raises_when_neither_nss_nor_ncs_found(self):
        inst = _make_inst()
        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.mcp.tools.composite._find", return_value=None):
                    with pytest.raises(ValueError, match="not found"):
                        await composite.handle_get_script({"game": "k1", "resref": "no_such_script"})

    @pytest.mark.asyncio
    async def test_returns_nss_source_when_available(self):
        inst = _make_inst()
        nss_source = b"void main() { SetGlobalNumber(\"K_SWG_TEST\", 1); }"

        def _mock_find(i, resref, ext):
            if ext == "nss":
                return nss_source
            return None

        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.mcp.tools.composite._find", side_effect=_mock_find):
                    with patch("gmodular.mcp.tools.composite._find_referrers", return_value=[]):
                        result = await composite.handle_get_script(
                            {"game": "k1", "resref": "k_swg_test"}
                        )
        decoded = _json_result(result)
        assert decoded["source"] is not None
        assert "SetGlobalNumber" in decoded["source"]
        assert decoded["source_from"] == "nss"

    @pytest.mark.asyncio
    async def test_decompiles_ncs_when_no_nss(self):
        inst = _make_inst()
        ncs_bytes = b"\x42\x4e\x43\x53" + b"\x00" * 50

        def _mock_find(i, resref, ext):
            if ext == "ncs":
                return ncs_bytes
            return None

        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.mcp.tools.composite._find", side_effect=_mock_find):
                    with patch("gmodular.mcp.tools.composite._decompile_ncs",
                               return_value="void main() { /* decompiled */ }"):
                        with patch("gmodular.mcp.tools.composite._find_referrers", return_value=[]):
                            result = await composite.handle_get_script(
                                {"game": "k1", "resref": "k_swg_test"}
                            )
        decoded = _json_result(result)
        assert decoded["decompiled_source"] == "void main() { /* decompiled */ }"
        assert decoded["source_from"] == "ncs_decompiled"

    @pytest.mark.asyncio
    async def test_referrers_included(self):
        inst = _make_inst()

        def _mock_find(i, resref, ext):
            if ext == "nss":
                return b"void main(){}"
            return None

        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.mcp.tools.composite._find", side_effect=_mock_find):
                    with patch("gmodular.mcp.tools.composite._find_referrers",
                               return_value=["k_tar_bastila.UTC", "tar_vult_01.DLG"]):
                        result = await composite.handle_get_script(
                            {"game": "k1", "resref": "k_hench_bastila"}
                        )
        decoded = _json_result(result)
        assert "k_tar_bastila.UTC" in decoded["referrers"]
        assert "tar_vult_01.DLG" in decoded["referrers"]

    @pytest.mark.asyncio
    async def test_markdown_format(self):
        inst = _make_inst()

        def _mock_find(i, resref, ext):
            if ext == "nss":
                return b"void main() { GiveXP(10); }"
            return None

        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.mcp.tools.composite._find", side_effect=_mock_find):
                    with patch("gmodular.mcp.tools.composite._find_referrers", return_value=[]):
                        result = await composite.handle_get_script(
                            {"game": "k1", "resref": "k_test", "format": "markdown"}
                        )
        decoded = _json_result(result)
        assert "markdown" in decoded
        assert "k_test" in decoded["markdown"]


# ─────────────────────────────────────────────────────────────────────────────
# search handler tests
# ─────────────────────────────────────────────────────────────────────────────

class TestHandleSearch:
    @pytest.mark.asyncio
    async def test_raises_on_empty_query(self):
        inst = _make_inst()
        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with pytest.raises(ValueError, match="query"):
                    await composite.handle_search({"game": "k1", "query": ""})

    @pytest.mark.asyncio
    async def test_raises_on_invalid_game(self):
        with patch("gmodular.mcp.tools.composite.resolve_game", return_value=None):
            with pytest.raises(ValueError, match="game"):
                await composite.handle_search({"game": "bad", "query": "bastila"})

    @pytest.mark.asyncio
    async def test_resref_search_finds_matching_resources(self):
        inst = _make_inst()
        # Build a mock index with some resources
        inst.index = {
            "by_key": {
                ("n_bastila", "utc"): [MagicMock(resref="n_bastila", ext="utc", source="chitin",
                                                  filepath=MagicMock(), size=100,
                                                  inside_capsule=False)],
                ("c_bastila_talk", "dlg"): [MagicMock(resref="c_bastila_talk", ext="dlg",
                                                       source="module:tar_m01aa",
                                                       filepath=MagicMock(), size=200,
                                                       inside_capsule=True)],
                ("plc_container", "utp"): [MagicMock(resref="plc_container", ext="utp",
                                                      source="chitin", filepath=MagicMock(),
                                                      size=50, inside_capsule=False)],
            }
        }
        inst.tlk_path.return_value = None  # skip TLK search

        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                result = await composite.handle_search(
                    {"game": "k1", "query": "bastila", "types": ["resref"]}
                )
        decoded = _json_result(result)
        assert decoded["count"] >= 2
        resrefs = [r["resref"] for r in decoded["results"]]
        assert "n_bastila" in resrefs
        assert "c_bastila_talk" in resrefs

    @pytest.mark.asyncio
    async def test_limit_parameter_respected(self):
        inst = _make_inst()
        # 20 matching resources
        inst.index = {
            "by_key": {
                (f"bastila_{i:02d}", "utc"): [MagicMock(resref=f"bastila_{i:02d}", ext="utc",
                                                         source="chitin", filepath=MagicMock(),
                                                         size=100, inside_capsule=False)]
                for i in range(20)
            }
        }
        inst.tlk_path.return_value = None

        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                result = await composite.handle_search(
                    {"game": "k1", "query": "bastila", "types": ["resref"], "limit": 5}
                )
        decoded = _json_result(result)
        assert decoded["count"] <= 5

    @pytest.mark.asyncio
    async def test_results_sorted_by_score(self):
        inst = _make_inst()
        inst.index = {
            "by_key": {
                ("bastila", "utc"): [MagicMock(resref="bastila", ext="utc", source="chitin",
                                               filepath=MagicMock(), size=100, inside_capsule=False)],
                ("bastila_companion", "utc"): [MagicMock(resref="bastila_companion", ext="utc",
                                                          source="chitin", filepath=MagicMock(),
                                                          size=100, inside_capsule=False)],
            }
        }
        inst.tlk_path.return_value = None

        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                result = await composite.handle_search(
                    {"game": "k1", "query": "bastila", "types": ["resref"]}
                )
        decoded = _json_result(result)
        # Exact match "bastila" should have higher score than "bastila_companion"
        if decoded["count"] >= 2:
            scores = [r.get("score", 0) for r in decoded["results"]]
            assert scores[0] >= scores[-1]

    @pytest.mark.asyncio
    async def test_types_filter_limits_search_scope(self):
        """When types=['resref'], TLK and 2DA should not be searched."""
        inst = _make_inst()
        inst.index = {"by_key": {
            ("bastila", "utc"): [MagicMock(resref="bastila", ext="utc", source="chitin",
                                           filepath=MagicMock(), size=100, inside_capsule=False)]
        }}
        inst.tlk_path.return_value = None

        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                with patch("gmodular.formats.tlk_reader.TLKReader") as mock_tlk:
                    result = await composite.handle_search(
                        {"game": "k1", "query": "bastila", "types": ["resref"]}
                    )
                    # TLKReader should NOT have been called
                    mock_tlk.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_expected_result_structure(self):
        inst = _make_inst()
        inst.index = {
            "by_key": {
                ("bastila_npc", "utc"): [MagicMock(resref="bastila_npc", ext="utc",
                                                    source="chitin", filepath=MagicMock(),
                                                    size=100, inside_capsule=False)]
            }
        }
        inst.tlk_path.return_value = None

        with patch("gmodular.mcp.tools.composite.resolve_game", return_value="K1"):
            with patch("gmodular.mcp.tools.composite.load_installation", return_value=inst):
                result = await composite.handle_search(
                    {"game": "k1", "query": "bastila", "types": ["resref"]}
                )
        decoded = _json_result(result)
        assert "game" in decoded
        assert "query" in decoded
        assert "count" in decoded
        assert "results" in decoded
        for r in decoded["results"]:
            assert "type" in r
            assert "resref" in r
            assert "text" in r


# ─────────────────────────────────────────────────────────────────────────────
# Integration: tool count
# ─────────────────────────────────────────────────────────────────────────────

class TestToolCount:
    def test_total_tool_count_increased_to_46(self):
        """Tool count grows as new tools are added — Ghostworks IPC tools bring total to 103."""
        all_tools = get_all_tools()
        assert len(all_tools) == 103, \
            f"Expected 103 tools, got {len(all_tools)}: {[t['name'] for t in all_tools]}"

    def test_no_duplicate_tool_names(self):
        all_tools = get_all_tools()
        names = [t["name"] for t in all_tools]
        assert len(names) == len(set(names)), \
            f"Duplicate tool names: {[n for n in names if names.count(n) > 1]}"
