"""
GModular — Ghostworks Pipeline IPC Bridge + MCP Tools Tests
=============================================================
Tests for:
  1. ghostworks_bridge module API surface
  2. Bridge functions return ConnectionError on unreachable host
  3. MCP ghostworks tool schemas (structure, required fields)
  4. MCP tool count includes ghostworks tools
  5. Live round-trip: bridge → real GhostRigger IPC server
  6. Live round-trip: bridge → real GhostScripter IPC server
  7. _safe_call converts ConnectionError → error dict
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
import sys
import threading
import unittest
from typing import Any, Dict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Sub-project paths so ghostrigger.* and ghostscripter.* are importable
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "ghostrigger"))
sys.path.insert(0, os.path.join(_ROOT, "ghostscripter"))


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_async(coro):
    """Run a coroutine, creating a fresh event loop if needed (Python 3.10+ safe)."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("loop closed")
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


# ──────────────────────────────────────────────────────────────────────────────
# 1. Bridge module API surface
# ──────────────────────────────────────────────────────────────────────────────

class TestGhostworksBridgeAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from gmodular.ipc import ghostworks_bridge as b
        cls.b = b

    def test_ghostrigger_ping_exists(self):
        self.assertTrue(callable(self.b.ghostrigger_ping))

    def test_ghostrigger_open_blueprint_exists(self):
        self.assertTrue(callable(self.b.ghostrigger_open_blueprint))

    def test_ghostrigger_get_blueprint_exists(self):
        self.assertTrue(callable(self.b.ghostrigger_get_blueprint))

    def test_ghostrigger_set_field_exists(self):
        self.assertTrue(callable(self.b.ghostrigger_set_field))

    def test_ghostrigger_get_field_exists(self):
        self.assertTrue(callable(self.b.ghostrigger_get_field))

    def test_ghostrigger_set_fields_bulk_exists(self):
        self.assertTrue(callable(self.b.ghostrigger_set_fields_bulk))

    def test_ghostrigger_save_blueprint_exists(self):
        self.assertTrue(callable(self.b.ghostrigger_save_blueprint))

    def test_ghostrigger_list_blueprints_exists(self):
        self.assertTrue(callable(self.b.ghostrigger_list_blueprints))

    def test_ghostscripter_ping_exists(self):
        self.assertTrue(callable(self.b.ghostscripter_ping))

    def test_ghostscripter_open_script_exists(self):
        self.assertTrue(callable(self.b.ghostscripter_open_script))

    def test_ghostscripter_get_script_exists(self):
        self.assertTrue(callable(self.b.ghostscripter_get_script))

    def test_ghostscripter_compile_exists(self):
        self.assertTrue(callable(self.b.ghostscripter_compile))

    def test_ghostscripter_list_scripts_exists(self):
        self.assertTrue(callable(self.b.ghostscripter_list_scripts))

    def test_ghostscripter_set_resref_exists(self):
        self.assertTrue(callable(self.b.ghostscripter_set_resref))

    def test_port_constants(self):
        self.assertEqual(self.b.GHOSTRIGGER_PORT, 7001)
        self.assertEqual(self.b.GHOSTSCRIPTER_PORT, 7002)


# ──────────────────────────────────────────────────────────────────────────────
# 2. ConnectionError on unreachable host
# ──────────────────────────────────────────────────────────────────────────────

class TestBridgeConnectionErrors(unittest.TestCase):
    """Bridge should raise ConnectionError (not crash) when host is down."""

    def _with_closed_port(self, fn, *args, **kwargs):
        """Override port constants to point to a closed port."""
        port = _free_port()   # allocated but NOT bound → connection refused
        import gmodular.ipc.ghostworks_bridge as b
        old_gr = b.GHOSTRIGGER_PORT
        old_gs = b.GHOSTSCRIPTER_PORT
        b.GHOSTRIGGER_PORT = port
        b.GHOSTSCRIPTER_PORT = port
        try:
            with self.assertRaises(ConnectionError):
                fn(*args, **kwargs)
        finally:
            b.GHOSTRIGGER_PORT = old_gr
            b.GHOSTSCRIPTER_PORT = old_gs

    def test_ghostrigger_ping_raises(self):
        from gmodular.ipc.ghostworks_bridge import ghostrigger_ping
        self._with_closed_port(ghostrigger_ping, timeout=0.3)

    def test_ghostrigger_open_blueprint_raises(self):
        from gmodular.ipc.ghostworks_bridge import ghostrigger_open_blueprint
        self._with_closed_port(
            ghostrigger_open_blueprint, "test", "utc", timeout=0.3
        )

    def test_ghostscripter_ping_raises(self):
        from gmodular.ipc.ghostworks_bridge import ghostscripter_ping
        self._with_closed_port(ghostscripter_ping, timeout=0.3)

    def test_ghostscripter_open_script_raises(self):
        from gmodular.ipc.ghostworks_bridge import ghostscripter_open_script
        self._with_closed_port(ghostscripter_open_script, "test_script", timeout=0.3)

    def test_invalid_blueprint_type_raises_value_error(self):
        from gmodular.ipc.ghostworks_bridge import ghostrigger_open_blueprint
        with self.assertRaises(ValueError):
            ghostrigger_open_blueprint("test", "utx", timeout=0.3)


# ──────────────────────────────────────────────────────────────────────────────
# 3. MCP tool schemas
# ──────────────────────────────────────────────────────────────────────────────

class TestGhostworksMCPToolSchemas(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from gmodular.mcp.tools import ghostworks
        cls.tools = {t["name"]: t for t in ghostworks.get_tools()}

    def test_tool_count(self):
        self.assertGreaterEqual(len(self.tools), 12)

    def test_ghostrigger_ping_schema(self):
        t = self.tools["ghostrigger_ping"]
        self.assertIn("description", t)
        self.assertIn("inputSchema", t)

    def test_ghostrigger_open_blueprint_required(self):
        t = self.tools["ghostrigger_open_blueprint"]
        req = t["inputSchema"]["required"]
        self.assertIn("resref", req)
        self.assertIn("blueprint_type", req)

    def test_ghostrigger_open_blueprint_enum(self):
        t = self.tools["ghostrigger_open_blueprint"]
        bp_type_prop = t["inputSchema"]["properties"]["blueprint_type"]
        self.assertEqual(sorted(bp_type_prop["enum"]), ["utc", "utd", "utp"])

    def test_ghostrigger_set_field_required(self):
        t = self.tools["ghostrigger_set_field"]
        req = t["inputSchema"]["required"]
        self.assertIn("resref", req)
        self.assertIn("field", req)
        self.assertIn("value", req)

    def test_ghostrigger_set_fields_bulk_required(self):
        t = self.tools["ghostrigger_set_fields_bulk"]
        req = t["inputSchema"]["required"]
        self.assertIn("resref", req)
        self.assertIn("fields", req)

    def test_ghostscripter_ping_schema(self):
        t = self.tools["ghostscripter_ping"]
        self.assertIn("description", t)

    def test_ghostscripter_open_script_required(self):
        t = self.tools["ghostscripter_open_script"]
        req = t["inputSchema"]["required"]
        self.assertIn("resref", req)

    def test_ghostscripter_compile_required(self):
        t = self.tools["ghostscripter_compile"]
        req = t["inputSchema"]["required"]
        self.assertIn("resref", req)

    def test_all_tools_have_description(self):
        from gmodular.mcp.tools import ghostworks
        for t in ghostworks.get_tools():
            self.assertIn("description", t, f"Missing description: {t['name']}")

    def test_all_tools_have_input_schema(self):
        from gmodular.mcp.tools import ghostworks
        for t in ghostworks.get_tools():
            self.assertIn("inputSchema", t, f"Missing inputSchema: {t['name']}")


# ──────────────────────────────────────────────────────────────────────────────
# 4. MCP tool count includes ghostworks
# ──────────────────────────────────────────────────────────────────────────────

class TestMCPToolCountGhostworks(unittest.TestCase):
    def test_get_all_tools_includes_ghostworks(self):
        from gmodular.mcp.tools import get_all_tools
        tools = get_all_tools()
        names = {t["name"] for t in tools}
        for expected in (
            "ghostrigger_ping", "ghostrigger_open_blueprint",
            "ghostscripter_ping", "ghostscripter_open_script",
        ):
            self.assertIn(expected, names)

    def test_total_tool_count_at_least_103(self):
        """91 original + 12 ghostworks = 103 minimum."""
        from gmodular.mcp.tools import get_all_tools
        self.assertGreaterEqual(len(get_all_tools()), 103)


# ──────────────────────────────────────────────────────────────────────────────
# 5. _safe_call converts ConnectionError → error dict
# ──────────────────────────────────────────────────────────────────────────────

class TestSafeCall(unittest.TestCase):
    def test_connection_error_becomes_dict(self):
        from gmodular.mcp.tools.ghostworks import _safe_call

        def _raise():
            raise ConnectionError("Cannot reach host")

        result = _safe_call(_raise)
        self.assertIn("error", result)
        self.assertFalse(result.get("connected", True))

    def test_generic_exception_becomes_dict(self):
        from gmodular.mcp.tools.ghostworks import _safe_call

        def _raise():
            raise RuntimeError("Something went wrong")

        result = _safe_call(_raise)
        self.assertIn("error", result)

    def test_success_passes_through(self):
        from gmodular.mcp.tools.ghostworks import _safe_call

        def _ok():
            return {"status": "ok", "data": 42}

        result = _safe_call(_ok)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"], 42)


# ──────────────────────────────────────────────────────────────────────────────
# 6. Live round-trip: GhostRigger IPC server
# ──────────────────────────────────────────────────────────────────────────────

class TestGhostRiggerLiveIPC(unittest.TestCase):
    """Start a real GhostRigger IPC server and exercise the bridge."""

    @classmethod
    def setUpClass(cls):
        import importlib
        import ghostrigger.ipc.server as ipc_mod
        ipc_mod = importlib.reload(ipc_mod)
        cls.ipc_mod = ipc_mod

        from ghostrigger.core.blueprint_state import BlueprintRegistry, Blueprint

        cls.registry = BlueprintRegistry()

        def handle_open_utc(payload):
            resref = payload.get("resref", "test")
            bp = Blueprint(resref=resref, blueprint_type="utc",
                           fields={"FirstName": "Unnamed", "Tag": resref.upper()})
            cls.registry.add(bp)
            return {"status": "ok", "resref": resref}

        def handle_get_blueprint(payload):
            resref = payload.get("resref", "")
            bp = cls.registry.get(resref)
            if bp is None:
                return {"error": f"not found: {resref}"}
            return {"status": "ok", "blueprint": bp.to_dict()}

        def handle_set_field(payload):
            resref = payload.get("resref", "")
            fname = payload.get("field", "")
            value = payload.get("value")
            bp = cls.registry.get(resref)
            if bp is None:
                return {"error": f"not found: {resref}"}
            bp.set(fname, value)
            return {"status": "ok", "resref": resref, "field": fname, "value": value}

        def handle_list_blueprints(payload):
            return {"status": "ok",
                    "blueprints": [b.to_dict() for b in cls.registry.list_all()]}

        def handle_save_blueprint(payload):
            data = payload.get("blueprint", {})
            from ghostrigger.core.blueprint_state import Blueprint
            bp = Blueprint.from_dict(data)
            bp.dirty = False
            cls.registry.add(bp)
            return {"status": "ok", "resref": bp.resref}

        ipc_mod.register("open_utc",       handle_open_utc)
        ipc_mod.register("get_blueprint",  handle_get_blueprint)
        ipc_mod.register("set_field",      handle_set_field)
        ipc_mod.register("list_blueprints",handle_list_blueprints)
        ipc_mod.register("save_blueprint", handle_save_blueprint)

        cls.port = _free_port()
        cls.srv = ipc_mod.start(port=cls.port)

        # Patch bridge port
        import gmodular.ipc.ghostworks_bridge as b
        cls._orig_gr_port = b.GHOSTRIGGER_PORT
        b.GHOSTRIGGER_PORT = cls.port

    @classmethod
    def tearDownClass(cls):
        cls.ipc_mod.stop()
        import gmodular.ipc.ghostworks_bridge as b
        b.GHOSTRIGGER_PORT = cls._orig_gr_port

    def test_ping(self):
        from gmodular.ipc.ghostworks_bridge import ghostrigger_ping
        r = ghostrigger_ping()
        self.assertEqual(r.get("status"), "ok")

    def test_open_utc(self):
        from gmodular.ipc.ghostworks_bridge import ghostrigger_open_blueprint
        r = ghostrigger_open_blueprint("hero001", "utc")
        self.assertEqual(r.get("status"), "ok")
        self.assertEqual(r.get("resref"), "hero001")

    def test_get_blueprint_after_open(self):
        from gmodular.ipc.ghostworks_bridge import (
            ghostrigger_open_blueprint, ghostrigger_get_blueprint)
        ghostrigger_open_blueprint("hero002", "utc")
        r = ghostrigger_get_blueprint("hero002")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["blueprint"]["resref"], "hero002")

    def test_set_field_via_bridge(self):
        from gmodular.ipc.ghostworks_bridge import (
            ghostrigger_open_blueprint, ghostrigger_set_field, ghostrigger_get_blueprint)
        ghostrigger_open_blueprint("hero003", "utc")
        r = ghostrigger_set_field("hero003", "FirstName", "Bastila")
        self.assertEqual(r["status"], "ok")
        r2 = ghostrigger_get_blueprint("hero003")
        self.assertEqual(r2["blueprint"]["fields"]["FirstName"], "Bastila")

    def test_list_blueprints(self):
        from gmodular.ipc.ghostworks_bridge import (
            ghostrigger_open_blueprint, ghostrigger_list_blueprints)
        ghostrigger_open_blueprint("list_test", "utc")
        r = ghostrigger_list_blueprints()
        self.assertEqual(r["status"], "ok")
        resrefs = [b["resref"] for b in r["blueprints"]]
        self.assertIn("list_test", resrefs)

    def _parse_mcp(self, result):
        """Extract JSON dict from json_content return value."""
        if isinstance(result, dict):
            return json.loads(result["content"][0]["text"])
        return json.loads(result[0].text)

    def test_mcp_handler_open_blueprint_async(self):
        from gmodular.mcp.tools.ghostworks import handle_ghostrigger_open_blueprint
        result = _run_async(handle_ghostrigger_open_blueprint(
            {"resref": "mcp_hero", "blueprint_type": "utc"}
        ))
        content = self._parse_mcp(result)
        self.assertEqual(content.get("status"), "ok")

    def test_mcp_handler_ping(self):
        from gmodular.mcp.tools.ghostworks import handle_ghostrigger_ping
        result = _run_async(handle_ghostrigger_ping({}))
        content = self._parse_mcp(result)
        self.assertEqual(content.get("status"), "ok")


# ──────────────────────────────────────────────────────────────────────────────
# 7. Live round-trip: GhostScripter IPC server
# ──────────────────────────────────────────────────────────────────────────────

class TestGhostScripterLiveIPC(unittest.TestCase):
    """Start a real GhostScripter IPC server and exercise the bridge."""

    @classmethod
    def setUpClass(cls):
        import importlib
        import ghostscripter.ipc.server as ipc_mod
        ipc_mod = importlib.reload(ipc_mod)
        cls.ipc_mod = ipc_mod

        from ghostscripter.core.script_state import ScriptRegistry, Script

        cls.registry = ScriptRegistry()

        def handle_open_script(payload):
            resref = payload.get("resref", "new_script")
            source = payload.get("source", "void main() {}")
            s = Script(resref=resref, source=source)
            cls.registry.add(s)
            return {"status": "ok", "resref": resref}

        def handle_get_script(payload):
            resref = payload.get("resref", "")
            s = cls.registry.get(resref)
            if s is None:
                return {"error": f"not found: {resref}"}
            return {"status": "ok", "script": s.to_dict()}

        def handle_compile(payload):
            resref = payload.get("resref", "")
            source = payload.get("source", "")
            s = cls.registry.get(resref)
            if s is None and not source:
                return {"error": f"not found: {resref}"}
            src = source or (s.source if s else "")
            # Stub compile
            success = "void main()" in src or "int StartingConditional()" in src
            return {
                "status": "ok",
                "resref": resref,
                "success": success,
                "errors": [] if success else ["No entry point found"],
                "ncs": "4e435320" if success else None,
            }

        def handle_list_scripts(payload):
            return {"status": "ok",
                    "scripts": [s.to_dict() for s in cls.registry.list_all()]}

        ipc_mod.register("open_script",   handle_open_script)
        ipc_mod.register("get_script",    handle_get_script)
        ipc_mod.register("compile",       handle_compile)
        ipc_mod.register("list_scripts",  handle_list_scripts)

        cls.port = _free_port()
        cls.srv = ipc_mod.start(port=cls.port)

        import gmodular.ipc.ghostworks_bridge as b
        cls._orig_gs_port = b.GHOSTSCRIPTER_PORT
        b.GHOSTSCRIPTER_PORT = cls.port

    @classmethod
    def tearDownClass(cls):
        cls.ipc_mod.stop()
        import gmodular.ipc.ghostworks_bridge as b
        b.GHOSTSCRIPTER_PORT = cls._orig_gs_port

    def test_ping(self):
        from gmodular.ipc.ghostworks_bridge import ghostscripter_ping
        r = ghostscripter_ping()
        self.assertEqual(r.get("status"), "ok")

    def test_open_script(self):
        from gmodular.ipc.ghostworks_bridge import ghostscripter_open_script
        r = ghostscripter_open_script("patrol001", "void main() {}")
        self.assertEqual(r.get("status"), "ok")

    def test_get_script_after_open(self):
        from gmodular.ipc.ghostworks_bridge import (
            ghostscripter_open_script, ghostscripter_get_script)
        ghostscripter_open_script("patrol002", "void main() { }")
        r = ghostscripter_get_script("patrol002")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["script"]["resref"], "patrol002")

    def test_compile_valid_script(self):
        from gmodular.ipc.ghostworks_bridge import (
            ghostscripter_open_script, ghostscripter_compile)
        ghostscripter_open_script("compile001", "void main() { }")
        r = ghostscripter_compile("compile001")
        self.assertEqual(r["status"], "ok")
        self.assertTrue(r["success"])

    def test_compile_invalid_script(self):
        from gmodular.ipc.ghostworks_bridge import (
            ghostscripter_open_script, ghostscripter_compile)
        ghostscripter_open_script("bad001", "int x = 1;")
        r = ghostscripter_compile("bad001")
        self.assertFalse(r["success"])

    def test_compile_with_source_override(self):
        from gmodular.ipc.ghostworks_bridge import ghostscripter_compile
        r = ghostscripter_compile("inline001", source="void main() { }")
        self.assertEqual(r["status"], "ok")
        self.assertTrue(r["success"])

    def test_list_scripts(self):
        from gmodular.ipc.ghostworks_bridge import (
            ghostscripter_open_script, ghostscripter_list_scripts)
        ghostscripter_open_script("list001", "void main() {}")
        r = ghostscripter_list_scripts()
        self.assertEqual(r["status"], "ok")
        resrefs = [s["resref"] for s in r["scripts"]]
        self.assertIn("list001", resrefs)

    def _parse_mcp(self, result):
        if isinstance(result, dict):
            return json.loads(result["content"][0]["text"])
        return json.loads(result[0].text)

    def test_mcp_handler_open_script_async(self):
        from gmodular.mcp.tools.ghostworks import handle_ghostscripter_open_script
        result = _run_async(handle_ghostscripter_open_script(
            {"resref": "mcp_script", "source": "void main() {}"}
        ))
        content = self._parse_mcp(result)
        self.assertEqual(content.get("status"), "ok")

    def test_mcp_handler_compile_async(self):
        from gmodular.mcp.tools.ghostworks import handle_ghostscripter_compile
        # Open first
        from gmodular.ipc.ghostworks_bridge import ghostscripter_open_script
        ghostscripter_open_script("mcp_compile", "void main() {}")
        result = _run_async(handle_ghostscripter_compile(
            {"resref": "mcp_compile"}
        ))
        content = self._parse_mcp(result)
        self.assertEqual(content.get("status"), "ok")
        self.assertTrue(content.get("success"))


if __name__ == "__main__":
    unittest.main()
