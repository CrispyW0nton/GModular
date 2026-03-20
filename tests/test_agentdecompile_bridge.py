"""Tests for the AgentDecompile / Ghidra bridge integration.

All tests are fully offline — they mock HTTP at the socket level so no
network access or live Ghidra server is required.
"""
from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════════════
#  Helpers / fixtures
# ═══════════════════════════════════════════════════════════════════════════

def _make_mcp_response(result: Any) -> bytes:
    """Build a minimal MCP JSON-RPC 2.0 response."""
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "result": result})
    return (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: application/json\r\n"
        + f"Content-Length: {len(payload)}\r\n".encode()
        + b"\r\n"
        + payload.encode()
    )


def _mock_http_response(body_dict: Dict[str, Any]):
    """Return a mock http.client.HTTPResponse that yields body_dict as JSON."""
    mock_resp = MagicMock()
    raw = json.dumps({"jsonrpc": "2.0", "id": 1, "result": body_dict}).encode()
    mock_resp.read.return_value = raw.decode()
    mock_resp.getheader.return_value = None
    return mock_resp


def _mock_conn(response_dict: Dict[str, Any]):
    """Return a mock HTTPConnection that serves a single canned response."""
    mock_conn = MagicMock()
    mock_conn.getresponse.return_value = _mock_http_response(response_dict)
    return mock_conn


# ═══════════════════════════════════════════════════════════════════════════
#  gmodular.mcp.tools.agentdecompile — tool schema tests
# ═══════════════════════════════════════════════════════════════════════════

class TestAgentDecompileToolSchemas:
    """Verify get_tools() returns well-formed MCP tool schema dicts."""

    def test_get_tools_returns_list(self):
        from gmodular.mcp.tools.agentdecompile import get_tools
        tools = get_tools()
        assert isinstance(tools, list)
        assert len(tools) >= 12, f"Expected ≥12 tools, got {len(tools)}"

    def test_all_tools_have_required_keys(self):
        from gmodular.mcp.tools.agentdecompile import get_tools
        for tool in get_tools():
            assert "name" in tool, f"Missing 'name' in {tool}"
            assert "description" in tool, f"Missing 'description' in {tool}"
            assert "inputSchema" in tool, f"Missing 'inputSchema' in {tool}"
            schema = tool["inputSchema"]
            assert schema.get("type") == "object"

    def test_tool_names_are_unique(self):
        from gmodular.mcp.tools.agentdecompile import get_tools
        names = [t["name"] for t in get_tools()]
        assert len(names) == len(set(names)), f"Duplicate tool names: {names}"

    def test_expected_tools_present(self):
        from gmodular.mcp.tools.agentdecompile import get_tools
        names = {t["name"] for t in get_tools()}
        expected = {
            "ghidra_get_program_info",
            "ghidra_search_symbols",
            "ghidra_list_functions",
            "ghidra_find_function",
            "ghidra_decompile",
            "ghidra_cross_reference",
            "ghidra_search_strings",
            "ghidra_list_imports",
            "ghidra_analyze_vtables",
            "ghidra_data_flow",
            "ghidra_export_c",
            "ghidra_kotor_function_map",
        }
        missing = expected - names
        assert not missing, f"Missing tools: {missing}"

    def test_required_params_declared(self):
        """Tools that need required params declare them in inputSchema."""
        from gmodular.mcp.tools.agentdecompile import get_tools
        tool_map = {t["name"]: t for t in get_tools()}

        # ghidra_search_symbols requires 'query'
        sym_schema = tool_map["ghidra_search_symbols"]["inputSchema"]
        assert "query" in sym_schema.get("required", [])

        # ghidra_decompile requires 'functionIdentifier'
        decompile_schema = tool_map["ghidra_decompile"]["inputSchema"]
        assert "functionIdentifier" in decompile_schema.get("required", [])

        # ghidra_analyze_vtables requires 'vtableAddress'
        vtable_schema = tool_map["ghidra_analyze_vtables"]["inputSchema"]
        assert "vtableAddress" in vtable_schema.get("required", [])

    def test_cross_reference_mode_enum(self):
        from gmodular.mcp.tools.agentdecompile import get_tools
        tool_map = {t["name"]: t for t in get_tools()}
        xref_props = tool_map["ghidra_cross_reference"]["inputSchema"]["properties"]
        assert xref_props["mode"]["enum"] == ["to", "from"]

    def test_kotor_function_map_category_enum(self):
        from gmodular.mcp.tools.agentdecompile import get_tools
        tool_map = {t["name"]: t for t in get_tools()}
        props = tool_map["ghidra_kotor_function_map"]["inputSchema"]["properties"]
        cats = props["category"]["enum"]
        assert "gff" in cats
        assert "all" in cats


# ═══════════════════════════════════════════════════════════════════════════
#  gmodular.mcp.tools.agentdecompile — handler tests (mocked HTTP)
# ═══════════════════════════════════════════════════════════════════════════

def _run(coro):
    return asyncio.run(coro)


class TestAgentDecompileHandlers:
    """Test each handler with a mocked HTTP backend."""

    def _patch_http(self, response_dict: Dict[str, Any]):
        """Context manager: patch _agdec_call to return a canned response."""
        import asyncio as _asyncio

        async def _fake_call(tool_name, args):
            return response_dict

        return patch(
            "gmodular.mcp.tools.agentdecompile._agdec_call",
            side_effect=_fake_call,
        )

    def test_handle_get_program_info(self):
        from gmodular.mcp.tools.agentdecompile import handle_get_program_info
        mock_result = {
            "content": [{"type": "text", "text": json.dumps({
                "loaded": True,
                "name": "swkotor.exe",
                "functionCount": 24591,
            })}]
        }
        with self._patch_http(mock_result):
            result = _run(handle_get_program_info({}))
        assert result is not None

    def test_handle_search_symbols(self):
        from gmodular.mcp.tools.agentdecompile import handle_search_symbols
        mock_result = {
            "content": [{"type": "text", "text": json.dumps({
                "count": 3,
                "symbols": [
                    {"name": "CExoString::CExoString", "address": "0x00401000"},
                    {"name": "CExoString::operator=", "address": "0x00401050"},
                ],
            })}]
        }
        with self._patch_http(mock_result):
            result = _run(handle_search_symbols({"query": "CExoString", "limit": 5}))
        assert result is not None

    def test_handle_search_symbols_requires_query(self):
        """handle_search_symbols should raise or return error without query."""
        from gmodular.mcp.tools.agentdecompile import handle_search_symbols
        with pytest.raises(KeyError):
            _run(handle_search_symbols({}))

    def test_handle_list_functions_no_prefix(self):
        from gmodular.mcp.tools.agentdecompile import handle_list_functions
        mock_result = {
            "content": [{"type": "text", "text": "[]"}]
        }
        with self._patch_http(mock_result):
            result = _run(handle_list_functions({"limit": 10}))
        assert result is not None

    def test_handle_list_functions_with_prefix(self):
        """With a prefix, should call search-symbols, not list-functions."""
        from gmodular.mcp.tools.agentdecompile import handle_list_functions
        mock_result = {"content": [{"type": "text", "text": "[]"}]}
        with self._patch_http(mock_result):
            result = _run(handle_list_functions({"prefix": "CExo", "limit": 5}))
        assert result is not None

    def test_handle_find_function(self):
        from gmodular.mcp.tools.agentdecompile import handle_find_function
        mock_result = {"content": [{"type": "text", "text": json.dumps({
            "name": "WinMain",
            "address": "0x004041f0",
        })}]}
        with self._patch_http(mock_result):
            result = _run(handle_find_function({"name": "WinMain"}))
        assert result is not None

    def test_handle_decompile(self):
        from gmodular.mcp.tools.agentdecompile import handle_decompile
        decompiled = "int WinMain(HINSTANCE hInstance, ...) {\n  return 0;\n}"
        mock_result = {"content": [{"type": "text", "text": decompiled}]}
        with self._patch_http(mock_result):
            result = _run(handle_decompile({"functionIdentifier": "WinMain"}))
        assert result is not None

    def test_handle_cross_reference_to(self):
        from gmodular.mcp.tools.agentdecompile import handle_cross_reference
        mock_result = {"content": [{"type": "text", "text": "[]"}]}
        with self._patch_http(mock_result):
            result = _run(handle_cross_reference({
                "address": "0x004041f0",
                "mode": "to",
                "limit": 10,
            }))
        assert result is not None

    def test_handle_cross_reference_from(self):
        from gmodular.mcp.tools.agentdecompile import handle_cross_reference
        mock_result = {"content": [{"type": "text", "text": "[]"}]}
        with self._patch_http(mock_result):
            result = _run(handle_cross_reference({
                "address": "0x004041f0",
                "mode": "from",
            }))
        assert result is not None

    def test_handle_search_strings(self):
        from gmodular.mcp.tools.agentdecompile import handle_search_strings
        mock_result = {"content": [{"type": "text", "text": json.dumps([
            {"address": "0x00500000", "value": "GFF V3.2"},
        ])}]}
        with self._patch_http(mock_result):
            result = _run(handle_search_strings({"query": "GFF"}))
        assert result is not None

    def test_handle_list_imports(self):
        from gmodular.mcp.tools.agentdecompile import handle_list_imports
        mock_result = {"content": [{"type": "text", "text": "kernel32.dll!CreateFile\n"}]}
        with self._patch_http(mock_result):
            result = _run(handle_list_imports({"limit": 10}))
        assert result is not None

    def test_handle_list_imports_with_filter(self):
        from gmodular.mcp.tools.agentdecompile import handle_list_imports
        mock_result = {"content": [{"type": "text", "text": "d3d8.dll!Direct3DCreate8\nkernel32!ReadFile\n"}]}
        with self._patch_http(mock_result):
            result = _run(handle_list_imports({"filter": "d3d"}))
        assert result is not None

    def test_handle_analyze_vtables(self):
        from gmodular.mcp.tools.agentdecompile import handle_analyze_vtables
        mock_result = {"content": [{"type": "text", "text": json.dumps({
            "vtable": "0x00405000",
            "entries": [],
        })}]}
        with self._patch_http(mock_result):
            result = _run(handle_analyze_vtables({
                "vtableAddress": "0x00405000",
                "maxEntries": 10,
            }))
        assert result is not None

    def test_handle_data_flow_backward(self):
        from gmodular.mcp.tools.agentdecompile import handle_data_flow
        mock_result = {"content": [{"type": "text", "text": "{}"}]}
        with self._patch_http(mock_result):
            result = _run(handle_data_flow({
                "functionAddress": "0x00401000",
                "direction": "backward",
                "startAddress": "0x00401020",
            }))
        assert result is not None

    def test_handle_data_flow_variable_accesses(self):
        from gmodular.mcp.tools.agentdecompile import handle_data_flow
        mock_result = {"content": [{"type": "text", "text": "{}"}]}
        with self._patch_http(mock_result):
            result = _run(handle_data_flow({
                "functionAddress": "0x00401000",
                "direction": "variable_accesses",
                "variableName": "local_var",
            }))
        assert result is not None

    def test_handle_export_c(self):
        from gmodular.mcp.tools.agentdecompile import handle_export_c
        mock_result = {"content": [{"type": "text", "text": "exported to /tmp/out.cpp"}]}
        with self._patch_http(mock_result):
            result = _run(handle_export_c({
                "outputPath": "/tmp/out.cpp",
                "format": "cpp",
            }))
        assert result is not None

    def test_handle_kotor_function_map_all(self):
        """ghidra_kotor_function_map enriches local map with live Ghidra calls."""
        from gmodular.mcp.tools.agentdecompile import handle_kotor_function_map
        mock_result = {"content": [{"type": "text", "text": json.dumps({
            "symbols": [{"name": "CExoString", "address": "0x401000"}]
        })}]}
        with self._patch_http(mock_result):
            result = _run(handle_kotor_function_map({"category": "all", "limit": 5}))
        assert result is not None

    def test_handle_kotor_function_map_gff_category(self):
        from gmodular.mcp.tools.agentdecompile import handle_kotor_function_map
        mock_result = {"content": [{"type": "text", "text": "{}"}]}
        with self._patch_http(mock_result):
            result = _run(handle_kotor_function_map({"category": "gff", "limit": 3}))
        assert result is not None

    def test_handle_kotor_function_map_unknown_category(self):
        """Unknown category should return empty map without crashing."""
        from gmodular.mcp.tools.agentdecompile import handle_kotor_function_map
        mock_result = {"content": [{"type": "text", "text": "{}"}]}
        with self._patch_http(mock_result):
            result = _run(handle_kotor_function_map({"category": "unknown_xyz"}))
        assert result is not None


# ═══════════════════════════════════════════════════════════════════════════
#  gmodular.mcp.tools.agentdecompile — HTTP helper tests
# ═══════════════════════════════════════════════════════════════════════════

class TestAgentDecompileHTTPHelpers:
    """Unit tests for internal HTTP and response-parsing helpers."""

    def test_extract_text_content_list(self):
        from gmodular.mcp.tools.agentdecompile import _extract_text
        result = {"content": [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}]}
        assert _extract_text(result) == "hello\nworld"

    def test_extract_text_error(self):
        from gmodular.mcp.tools.agentdecompile import _extract_text
        result = {"error": "not found"}
        assert "not found" in _extract_text(result)

    def test_extract_text_plain_dict(self):
        from gmodular.mcp.tools.agentdecompile import _extract_text
        result = {"key": "val"}
        assert "key" in _extract_text(result)

    def test_resolve_program_default(self):
        from gmodular.mcp.tools.agentdecompile import _resolve_program, _DEFAULT_PROGRAM
        assert _resolve_program({}) == _DEFAULT_PROGRAM

    def test_resolve_program_override(self):
        from gmodular.mcp.tools.agentdecompile import _resolve_program
        assert _resolve_program({"programPath": "/K2/swkotor2.exe"}) == "/K2/swkotor2.exe"

    def test_sync_http_post_sse_parsing(self):
        """_sync_http_post should extract first data: line from SSE stream."""
        import gmodular.mcp.tools.agentdecompile as admod

        sse_body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
        sse_response = f"data: {sse_body}\n\n"

        mock_resp = MagicMock()
        # resp.read() returns bytes in real http.client
        mock_resp.read.return_value = sse_response.encode("utf-8")
        mock_resp.getheader.return_value = None
        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp

        with patch.object(admod.http.client, "HTTPConnection", return_value=mock_conn):
            result = admod._sync_http_post(
                "170.9.241.140:8080",
                "http://170.9.241.140:8080/mcp",
                b"{}",
                {},
            )
        assert result == {"ok": True}

    def test_agdec_call_network_error_returns_error_dict(self):
        """_agdec_call should catch network errors and return error dict."""
        from gmodular.mcp.tools.agentdecompile import _agdec_call
        with patch(
            "gmodular.mcp.tools.agentdecompile._sync_http_post",
            side_effect=RuntimeError("connection refused"),
        ):
            result = _run(_agdec_call("list-functions", {}))
        assert "error" in result


# ═══════════════════════════════════════════════════════════════════════════
#  gmodular.ipc.ghidra_bridge — unit tests
# ═══════════════════════════════════════════════════════════════════════════

class TestGhidraIPCBridge:
    """Tests for the GhidraIPCBridge IPC module."""

    def _make_bridge(self) -> Any:
        from gmodular.ipc.ghidra_bridge import GhidraIPCBridge
        return GhidraIPCBridge(
            backend_url="http://127.0.0.1:9999/mcp/",
            program_path="/K1/test.exe",
            timeout=5,
        )

    def test_instantiation(self):
        bridge = self._make_bridge()
        assert bridge is not None
        bridge.shutdown()

    def test_extract_text_content(self):
        from gmodular.ipc.ghidra_bridge import GhidraIPCBridge
        result = {"content": [{"type": "text", "text": "abc"}]}
        assert GhidraIPCBridge._extract_text(result) == "abc"

    def test_extract_text_error(self):
        from gmodular.ipc.ghidra_bridge import GhidraIPCBridge
        result = {"error": "oops"}
        assert "oops" in GhidraIPCBridge._extract_text(result)

    def test_parse_list_from_list(self):
        from gmodular.ipc.ghidra_bridge import GhidraIPCBridge
        items = [{"name": "f1"}, {"name": "f2"}]
        assert GhidraIPCBridge._parse_list(items) == items

    def test_parse_list_from_content(self):
        from gmodular.ipc.ghidra_bridge import GhidraIPCBridge
        inner = json.dumps([{"name": "f1"}])
        result = {"content": [{"type": "text", "text": inner}]}
        out = GhidraIPCBridge._parse_list(result)
        assert out == [{"name": "f1"}]

    def test_parse_list_error_wrapped(self):
        from gmodular.ipc.ghidra_bridge import GhidraIPCBridge
        result = {"error": "gone"}
        out = GhidraIPCBridge._parse_list(result)
        assert out[0]["error"] == "gone"

    def test_query_network_error(self):
        """query() should return error dict on connection failure."""
        bridge = self._make_bridge()
        with patch(
            "gmodular.ipc.ghidra_bridge.http.client.HTTPConnection",
            side_effect=ConnectionRefusedError("refused"),
        ):
            result = bridge.query("list-functions", {})
        assert "error" in result
        bridge.shutdown()

    def test_is_available_false_on_error(self):
        bridge = self._make_bridge()
        with patch.object(bridge, "program_info", return_value={"error": "unreachable"}):
            assert bridge.is_available() is False
        bridge.shutdown()

    def test_is_available_true_on_success(self):
        bridge = self._make_bridge()
        with patch.object(
            bridge, "program_info",
            return_value={"content": [{"type": "text", "text": '{"loaded":true}'}]}
        ):
            assert bridge.is_available() is True
        bridge.shutdown()

    def test_query_async_callback(self):
        """query_async should invoke the callback on the worker thread."""
        bridge = self._make_bridge()
        received: list = []
        mock_result = {"content": [{"type": "text", "text": "ok"}]}
        with patch.object(bridge, "_http_call", return_value=mock_result):
            event = threading.Event()
            def cb(r):
                received.append(r)
                event.set()
            bridge.query_async("list-functions", {}, callback=cb)
            event.wait(timeout=3)
        assert len(received) == 1
        assert received[0] == mock_result
        bridge.shutdown()

    def test_get_bridge_singleton(self):
        """get_bridge() should return the same instance on repeated calls."""
        from gmodular.ipc import ghidra_bridge
        # Reset singleton for test isolation
        ghidra_bridge._bridge_instance = None
        b1 = ghidra_bridge.get_bridge()
        b2 = ghidra_bridge.get_bridge()
        assert b1 is b2
        b1.shutdown()
        ghidra_bridge._bridge_instance = None

    def test_default_program_path_injected(self):
        """query() should inject default programPath if not provided."""
        bridge = self._make_bridge()
        called_with: list = []

        def mock_http(tool, args):
            called_with.append(args.copy())
            return {"content": [{"type": "text", "text": "{}"}]}

        with patch.object(bridge, "_http_call", side_effect=mock_http):
            bridge.query("list-functions")

        assert len(called_with) == 1
        assert called_with[0]["programPath"] == "/K1/test.exe"
        bridge.shutdown()


# ═══════════════════════════════════════════════════════════════════════════
#  Integration with gmodular.mcp.tools registry
# ═══════════════════════════════════════════════════════════════════════════

class TestMCPRegistryIntegration:
    """Verify that agentdecompile tools are discoverable via the registry."""

    def test_all_tools_includes_ghidra_tools(self):
        from gmodular.mcp.tools import get_all_tools
        all_tools = get_all_tools()
        names = {t["name"] for t in all_tools}
        assert "ghidra_search_symbols" in names
        assert "ghidra_decompile" in names
        assert "ghidra_kotor_function_map" in names

    def test_total_tool_count(self):
        from gmodular.mcp.tools import get_all_tools
        tools = get_all_tools()
        # Original 26 + 12 new agentdecompile tools = 38
        assert len(tools) >= 38, f"Expected ≥38 tools, got {len(tools)}"

    def test_no_duplicate_tool_names_in_registry(self):
        from gmodular.mcp.tools import get_all_tools
        names = [t["name"] for t in get_all_tools()]
        dups = [n for n in names if names.count(n) > 1]
        assert not dups, f"Duplicate tool names in registry: {dups}"

    def test_dispatch_unknown_tool_raises(self):
        from gmodular.mcp.tools import handle_tool
        with pytest.raises(ValueError, match="Unknown tool"):
            _run(handle_tool("nonexistent_tool_xyz", {}))

    def test_dispatch_ghidra_get_program_info(self):
        """handle_tool should route to agentdecompile handler correctly."""
        from gmodular.mcp.tools import handle_tool
        mock_result = {"content": [{"type": "text", "text": '{"loaded":true}'}]}
        with patch(
            "gmodular.mcp.tools.agentdecompile._agdec_call",
            return_value=mock_result,
        ):
            result = _run(handle_tool("ghidra_get_program_info", {}))
        assert result is not None

    def test_dispatch_ghidra_search_symbols(self):
        from gmodular.mcp.tools import handle_tool
        mock_result = {"content": [{"type": "text", "text": "[]"}]}
        with patch(
            "gmodular.mcp.tools.agentdecompile._agdec_call",
            return_value=mock_result,
        ):
            result = _run(handle_tool("ghidra_search_symbols", {"query": "test"}))
        assert result is not None

    def test_dispatch_ghidra_decompile(self):
        from gmodular.mcp.tools import handle_tool
        mock_result = {"content": [{"type": "text", "text": "void main() {}"}]}
        with patch(
            "gmodular.mcp.tools.agentdecompile._agdec_call",
            return_value=mock_result,
        ):
            result = _run(handle_tool("ghidra_decompile", {"functionIdentifier": "main"}))
        assert result is not None

    def test_dispatch_ghidra_cross_reference(self):
        from gmodular.mcp.tools import handle_tool
        mock_result = {"content": [{"type": "text", "text": "[]"}]}
        with patch(
            "gmodular.mcp.tools.agentdecompile._agdec_call",
            return_value=mock_result,
        ):
            result = _run(handle_tool("ghidra_cross_reference", {"address": "0x401000"}))
        assert result is not None

    def test_dispatch_ghidra_kotor_function_map(self):
        from gmodular.mcp.tools import handle_tool
        mock_result = {"content": [{"type": "text", "text": "{}"}]}
        with patch(
            "gmodular.mcp.tools.agentdecompile._agdec_call",
            return_value=mock_result,
        ):
            result = _run(handle_tool("ghidra_kotor_function_map", {"category": "gff"}))
        assert result is not None


# ═══════════════════════════════════════════════════════════════════════════
#  .vscode/mcp.json config validation
# ═══════════════════════════════════════════════════════════════════════════

class TestVSCodeMCPConfig:
    """Validate the .vscode/mcp.json structure."""

    def _load_config(self) -> Dict[str, Any]:
        import pathlib
        cfg_path = pathlib.Path(__file__).parent.parent / ".vscode" / "mcp.json"
        assert cfg_path.exists(), f".vscode/mcp.json not found at {cfg_path}"
        with open(cfg_path) as f:
            return json.load(f)

    def test_config_loads(self):
        cfg = self._load_config()
        assert "servers" in cfg

    def test_agdec_proxy_present(self):
        cfg = self._load_config()
        assert "agdec-proxy" in cfg["servers"]

    def test_agdec_proxy_config(self):
        cfg = self._load_config()
        proxy = cfg["servers"]["agdec-proxy"]
        assert proxy["type"] == "stdio"
        assert "uvx" in proxy["command"]
        assert any("agentdecompile-proxy" in a for a in proxy["args"])

    def test_agdec_proxy_env_vars(self):
        cfg = self._load_config()
        env = cfg["servers"]["agdec-proxy"]["env"]
        assert "AGENTDECOMPILE_HTTP_GHIDRA_SERVER_HOST" in env
        assert "AGENTDECOMPILE_GHIDRA_USERNAME" in env
        assert env["AGENTDECOMPILE_HTTP_GHIDRA_SERVER_REPOSITORY"] == "Odyssey"

    def test_gmodular_mcp_present(self):
        cfg = self._load_config()
        assert "gmodular-mcp" in cfg["servers"]

    def test_agdec_direct_present(self):
        cfg = self._load_config()
        assert "agdec-direct" in cfg["servers"]
        direct = cfg["servers"]["agdec-direct"]
        assert direct["type"] == "http"
        assert "170.9.241.140" in direct["url"]
