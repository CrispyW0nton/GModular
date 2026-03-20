"""
Ghostworks Pipeline — End-to-End Integration Test
===================================================
Tests the complete three-program workflow:

  GModular (bridge) ──► GhostRigger (port 7001) ──► blueprint editing
  GModular (bridge) ──► GhostScripter (port 7002) ──► script editing/compile
  GhostRigger IPC ──► GhostScripter IPC ──► cross-program calls

Scenario tested (PIPELINE_SPEC §1 "under 10 minutes" goal):
  1. GModular pings both programs → both respond ok
  2. GModular opens creature blueprint in GhostRigger → verified in registry
  3. GModular sets creature fields (name, tag, HP, faction) via bulk API
  4. GModular retrieves the blueprint back → verifies field values
  5. GModular opens a patrol script in GhostScripter with starter source
  6. GModular compiles the script → verifies success + ncs hex present
  7. GModular attaches script resref to creature blueprint → verified
  8. GModular lists all blueprints → creature appears
  9. GModular lists all scripts → patrol script appears
  10. MCP tools produce valid JSON for all steps
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
import sys
import unittest

# ── Path setup ────────────────────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "ghostrigger"))
sys.path.insert(0, os.path.join(_ROOT, "ghostscripter"))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run(coro):
    """Run a coroutine, creating a fresh event loop if needed (Python 3.10+ safe)."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("loop closed")
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def _parse(mcp_result) -> dict:
    """Extract dict from json_content result."""
    if isinstance(mcp_result, dict):
        return json.loads(mcp_result["content"][0]["text"])
    return json.loads(mcp_result[0].text)


# ──────────────────────────────────────────────────────────────────────────────
# Shared fixture: spin up both IPC servers before any tests run
# ──────────────────────────────────────────────────────────────────────────────

class _GhostworksFixture:
    """Mixin that starts GhostRigger + GhostScripter servers once per class."""

    gr_port: int = 0
    gs_port: int = 0
    _gr_ipc = None
    _gs_ipc = None

    @classmethod
    def _setup_ghostrigger(cls):
        import ghostrigger.ipc.server as gr_ipc
        gr_ipc = importlib.reload(gr_ipc)
        cls._gr_ipc = gr_ipc

        from ghostrigger.core.blueprint_state import BlueprintRegistry, Blueprint

        cls._gr_registry = BlueprintRegistry()

        def h_open_utc(p):
            resref = p.get("resref", "unnamed")
            fields = p.get("fields") or {"FirstName": "Unnamed", "Tag": resref.upper()}
            bp = Blueprint(resref=resref, blueprint_type="utc", fields=fields)
            cls._gr_registry.add(bp)
            return {"status": "ok", "resref": resref}

        def h_get_blueprint(p):
            bp = cls._gr_registry.get(p.get("resref", ""))
            if bp is None:
                return {"error": "not found"}
            return {"status": "ok", "blueprint": bp.to_dict()}

        def h_set_field(p):
            bp = cls._gr_registry.get(p.get("resref", ""))
            if bp is None:
                return {"error": "not found"}
            bp.set(p["field"], p["value"])
            return {"status": "ok"}

        def h_set_fields_bulk(p):
            bp = cls._gr_registry.get(p.get("resref", ""))
            if bp is None:
                return {"error": "not found"}
            for k, v in p.get("fields", {}).items():
                bp.set(k, v)
            return {"status": "ok", "updated": len(p.get("fields", {}))}

        def h_list_blueprints(p):
            return {"status": "ok",
                    "blueprints": [b.to_dict() for b in cls._gr_registry.list_all()]}

        def h_save_blueprint(p):
            data = p.get("blueprint", {})
            from ghostrigger.core.blueprint_state import Blueprint
            bp = Blueprint.from_dict(data)
            bp.dirty = False
            cls._gr_registry.add(bp)
            return {"status": "ok", "resref": bp.resref}

        gr_ipc.register("open_utc",        h_open_utc)
        gr_ipc.register("get_blueprint",   h_get_blueprint)
        gr_ipc.register("set_field",       h_set_field)
        gr_ipc.register("set_fields_bulk", h_set_fields_bulk)
        gr_ipc.register("list_blueprints", h_list_blueprints)
        gr_ipc.register("save_blueprint",  h_save_blueprint)

        cls.gr_port = _free_port()
        cls._gr_srv = gr_ipc.start(port=cls.gr_port)

    @classmethod
    def _setup_ghostscripter(cls):
        import ghostscripter.ipc.server as gs_ipc
        gs_ipc = importlib.reload(gs_ipc)
        cls._gs_ipc = gs_ipc

        from ghostscripter.core.script_state import ScriptRegistry, Script

        cls._gs_registry = ScriptRegistry()

        def h_open_script(p):
            resref = p.get("resref", "unnamed")
            source = p.get("source", "void main() {}")
            s = Script(resref=resref, source=source)
            cls._gs_registry.add(s)
            return {"status": "ok", "resref": resref}

        def h_get_script(p):
            s = cls._gs_registry.get(p.get("resref", ""))
            if s is None:
                return {"error": "not found"}
            return {"status": "ok", "script": s.to_dict()}

        def h_compile(p):
            resref = p.get("resref", "")
            source = p.get("source", "")
            s = cls._gs_registry.get(resref)
            src = source or (s.source if s else "")
            success = ("void main()" in src or "int StartingConditional()" in src)
            return {
                "status": "ok",
                "resref": resref,
                "success": success,
                "errors": [] if success else ["no entry point"],
                "ncs": "4e435320562e312e30" if success else None,
            }

        def h_list_scripts(p):
            return {"status": "ok",
                    "scripts": [s.to_dict() for s in cls._gs_registry.list_all()]}

        gs_ipc.register("open_script",  h_open_script)
        gs_ipc.register("get_script",   h_get_script)
        gs_ipc.register("compile",      h_compile)
        gs_ipc.register("list_scripts", h_list_scripts)

        cls.gs_port = _free_port()
        cls._gs_srv = gs_ipc.start(port=cls.gs_port)

    @classmethod
    def _patch_bridge_ports(cls):
        import gmodular.ipc.ghostworks_bridge as b
        cls._orig_gr = b.GHOSTRIGGER_PORT
        cls._orig_gs = b.GHOSTSCRIPTER_PORT
        b.GHOSTRIGGER_PORT = cls.gr_port
        b.GHOSTSCRIPTER_PORT = cls.gs_port

    @classmethod
    def _restore_bridge_ports(cls):
        import gmodular.ipc.ghostworks_bridge as b
        b.GHOSTRIGGER_PORT = cls._orig_gr
        b.GHOSTSCRIPTER_PORT = cls._orig_gs


# ──────────────────────────────────────────────────────────────────────────────
# Integration test class
# ──────────────────────────────────────────────────────────────────────────────

class TestGhostworksPipelineE2E(_GhostworksFixture, unittest.TestCase):
    """Full pipeline: GModular orchestrates GhostRigger + GhostScripter."""

    @classmethod
    def setUpClass(cls):
        cls._setup_ghostrigger()
        cls._setup_ghostscripter()
        cls._patch_bridge_ports()

    @classmethod
    def tearDownClass(cls):
        cls._gs_ipc.stop()
        cls._gr_ipc.stop()
        cls._restore_bridge_ports()

    # ── Step 1: Health checks ─────────────────────────────────────────────────

    def test_01_ghostrigger_ping(self):
        from gmodular.ipc.ghostworks_bridge import ghostrigger_ping
        r = ghostrigger_ping()
        self.assertEqual(r["status"], "ok")

    def test_02_ghostscripter_ping(self):
        from gmodular.ipc.ghostworks_bridge import ghostscripter_ping
        r = ghostscripter_ping()
        self.assertEqual(r["status"], "ok")

    def test_03_both_programs_reachable(self):
        from gmodular.ipc.ghostworks_bridge import ghostrigger_ping, ghostscripter_ping
        gr = ghostrigger_ping()
        gs = ghostscripter_ping()
        self.assertEqual(gr["status"], "ok")
        self.assertEqual(gs["status"], "ok")

    # ── Step 2: Open blueprint in GhostRigger ─────────────────────────────────

    def test_04_open_creature_blueprint(self):
        from gmodular.ipc.ghostworks_bridge import ghostrigger_open_blueprint
        r = ghostrigger_open_blueprint("revan001", "utc",
                                        fields={"FirstName": "Revan", "Tag": "REVAN"})
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["resref"], "revan001")

    def test_05_blueprint_in_registry(self):
        from gmodular.ipc.ghostworks_bridge import ghostrigger_get_blueprint
        r = ghostrigger_get_blueprint("revan001")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["blueprint"]["resref"], "revan001")
        self.assertEqual(r["blueprint"]["type"], "utc")

    # ── Step 3: Bulk field update ─────────────────────────────────────────────

    def test_06_set_creature_fields_bulk(self):
        from gmodular.ipc.ghostworks_bridge import (
            ghostrigger_open_blueprint, ghostrigger_set_fields_bulk)
        ghostrigger_open_blueprint("bastila001", "utc")
        r = ghostrigger_set_fields_bulk("bastila001", {
            "FirstName": "Bastila",
            "Tag": "BASTILA001",
            "MaxHitPoints": 45,
            "Faction": 1,
            "GoodEvil": 75,
        })
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["updated"], 5)

    def test_07_verify_bulk_fields(self):
        from gmodular.ipc.ghostworks_bridge import ghostrigger_get_blueprint
        r = ghostrigger_get_blueprint("bastila001")
        fields = r["blueprint"]["fields"]
        self.assertEqual(fields["FirstName"], "Bastila")
        self.assertEqual(fields["MaxHitPoints"], 45)
        self.assertEqual(fields["GoodEvil"], 75)

    # ── Step 4: Single field set/get ──────────────────────────────────────────

    def test_08_set_single_field(self):
        from gmodular.ipc.ghostworks_bridge import (
            ghostrigger_open_blueprint, ghostrigger_set_field,
            ghostrigger_get_blueprint)
        ghostrigger_open_blueprint("carth001", "utc")
        ghostrigger_set_field("carth001", "FirstName", "Carth")
        r = ghostrigger_get_blueprint("carth001")
        self.assertEqual(r["blueprint"]["fields"]["FirstName"], "Carth")

    # ── Step 5: Open script in GhostScripter ─────────────────────────────────

    PATROL_SCRIPT = (
        "// patrol_k_hb.nss — Patrol heartbeat\n"
        "void main() {\n"
        "    object oWP = GetWaypointByTag(\"WP_PATROL_01\");\n"
        "    if (GetDistanceBetween(OBJECT_SELF, oWP) > 2.0) {\n"
        "        ActionMoveToObject(oWP, TRUE);\n"
        "    }\n"
        "}\n"
    )

    def test_09_open_patrol_script(self):
        from gmodular.ipc.ghostworks_bridge import ghostscripter_open_script
        r = ghostscripter_open_script("patrol_k_hb", self.PATROL_SCRIPT)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["resref"], "patrol_k_hb")

    def test_10_get_script_source(self):
        from gmodular.ipc.ghostworks_bridge import ghostscripter_get_script
        r = ghostscripter_get_script("patrol_k_hb")
        self.assertEqual(r["status"], "ok")
        self.assertIn("void main", r["script"]["source"])

    # ── Step 6: Compile ───────────────────────────────────────────────────────

    def test_11_compile_patrol_script(self):
        from gmodular.ipc.ghostworks_bridge import ghostscripter_compile
        r = ghostscripter_compile("patrol_k_hb")
        self.assertEqual(r["status"], "ok")
        self.assertTrue(r["success"])
        self.assertIsNotNone(r.get("ncs"))
        self.assertEqual(len(r.get("errors", [])), 0)

    def test_12_compile_ncs_is_hex_string(self):
        from gmodular.ipc.ghostworks_bridge import ghostscripter_compile
        r = ghostscripter_compile("patrol_k_hb")
        ncs = r.get("ncs", "")
        self.assertIsInstance(ncs, str)
        self.assertGreater(len(ncs), 0)

    def test_13_compile_invalid_script(self):
        from gmodular.ipc.ghostworks_bridge import (
            ghostscripter_open_script, ghostscripter_compile)
        ghostscripter_open_script("bad_script", "int x = 1;")
        r = ghostscripter_compile("bad_script")
        self.assertFalse(r["success"])
        self.assertGreater(len(r.get("errors", [])), 0)

    # ── Step 7: Attach script to blueprint ────────────────────────────────────

    def test_14_attach_script_to_creature(self):
        from gmodular.ipc.ghostworks_bridge import (
            ghostrigger_set_field, ghostrigger_get_blueprint)
        ghostrigger_set_field("bastila001", "ScriptHeartbeat", "patrol_k_hb")
        r = ghostrigger_get_blueprint("bastila001")
        self.assertEqual(r["blueprint"]["fields"]["ScriptHeartbeat"], "patrol_k_hb")

    def test_15_dialogue_resref_attached(self):
        from gmodular.ipc.ghostworks_bridge import (
            ghostrigger_open_blueprint, ghostrigger_set_field,
            ghostrigger_get_blueprint)
        ghostrigger_open_blueprint("dlg_test", "utc")
        ghostrigger_set_field("dlg_test", "Conversation", "bastila_dlg")
        r = ghostrigger_get_blueprint("dlg_test")
        self.assertEqual(r["blueprint"]["fields"]["Conversation"], "bastila_dlg")

    # ── Step 8: List all blueprints ───────────────────────────────────────────

    def test_16_list_all_blueprints(self):
        from gmodular.ipc.ghostworks_bridge import ghostrigger_list_blueprints
        r = ghostrigger_list_blueprints()
        self.assertEqual(r["status"], "ok")
        resrefs = [b["resref"] for b in r["blueprints"]]
        for expected in ("revan001", "bastila001", "carth001"):
            self.assertIn(expected, resrefs)

    def test_17_blueprints_have_correct_types(self):
        from gmodular.ipc.ghostworks_bridge import ghostrigger_list_blueprints
        r = ghostrigger_list_blueprints()
        for bp in r["blueprints"]:
            self.assertEqual(bp["type"], "utc")

    # ── Step 9: List all scripts ──────────────────────────────────────────────

    def test_18_list_all_scripts(self):
        from gmodular.ipc.ghostworks_bridge import ghostscripter_list_scripts
        r = ghostscripter_list_scripts()
        self.assertEqual(r["status"], "ok")
        resrefs = [s["resref"] for s in r["scripts"]]
        self.assertIn("patrol_k_hb", resrefs)

    # ── Step 10: MCP tool round-trips ─────────────────────────────────────────

    def test_19_mcp_ghostrigger_open_and_get(self):
        from gmodular.mcp.tools.ghostworks import (
            handle_ghostrigger_open_blueprint, handle_ghostrigger_get_blueprint)
        r1 = _parse(_run(handle_ghostrigger_open_blueprint(
            {"resref": "mcp_creature", "blueprint_type": "utc",
             "fields": {"FirstName": "HK-47", "Tag": "HK47"}}
        )))
        self.assertEqual(r1["status"], "ok")
        r2 = _parse(_run(handle_ghostrigger_get_blueprint({"resref": "mcp_creature"})))
        self.assertEqual(r2["blueprint"]["fields"]["FirstName"], "HK-47")

    def test_20_mcp_ghostrigger_set_fields_bulk(self):
        from gmodular.mcp.tools.ghostworks import (
            handle_ghostrigger_open_blueprint, handle_ghostrigger_set_fields_bulk,
            handle_ghostrigger_get_blueprint)
        _run(handle_ghostrigger_open_blueprint(
            {"resref": "t3m4", "blueprint_type": "utc"}))
        r = _parse(_run(handle_ghostrigger_set_fields_bulk(
            {"resref": "t3m4", "fields": {"FirstName": "T3-M4", "MaxHitPoints": 50}}
        )))
        self.assertEqual(r["status"], "ok")
        r2 = _parse(_run(handle_ghostrigger_get_blueprint({"resref": "t3m4"})))
        self.assertEqual(r2["blueprint"]["fields"]["FirstName"], "T3-M4")

    def test_21_mcp_ghostscripter_open_and_compile(self):
        from gmodular.mcp.tools.ghostworks import (
            handle_ghostscripter_open_script, handle_ghostscripter_compile)
        _run(handle_ghostscripter_open_script(
            {"resref": "mcp_spawn", "source": "void main() { SpeakString(\"Hello\"); }"}
        ))
        r = _parse(_run(handle_ghostscripter_compile({"resref": "mcp_spawn"})))
        self.assertEqual(r["status"], "ok")
        self.assertTrue(r["success"])

    def test_22_mcp_ghostscripter_list_scripts(self):
        from gmodular.mcp.tools.ghostworks import handle_ghostscripter_list_scripts
        r = _parse(_run(handle_ghostscripter_list_scripts({})))
        self.assertEqual(r["status"], "ok")
        resrefs = [s["resref"] for s in r["scripts"]]
        self.assertIn("patrol_k_hb", resrefs)

    def test_23_mcp_ghostrigger_list_blueprints(self):
        from gmodular.mcp.tools.ghostworks import handle_ghostrigger_list_blueprints
        r = _parse(_run(handle_ghostrigger_list_blueprints({})))
        self.assertEqual(r["status"], "ok")
        self.assertGreater(len(r["blueprints"]), 0)

    # ── Offline graceful degradation ──────────────────────────────────────────

    def test_24_mcp_tools_return_error_when_offline(self):
        """_safe_call should return error dict, never raise."""
        from gmodular.mcp.tools.ghostworks import handle_ghostrigger_ping
        import gmodular.ipc.ghostworks_bridge as b
        orig = b.GHOSTRIGGER_PORT
        b.GHOSTRIGGER_PORT = _free_port()   # nothing listening
        try:
            r = _parse(_run(handle_ghostrigger_ping({"timeout": 0.2})))
            self.assertIn("error", r)
        finally:
            b.GHOSTRIGGER_PORT = orig

    def test_25_ghostscripter_offline_returns_error(self):
        from gmodular.mcp.tools.ghostworks import handle_ghostscripter_ping
        import gmodular.ipc.ghostworks_bridge as b
        orig = b.GHOSTSCRIPTER_PORT
        b.GHOSTSCRIPTER_PORT = _free_port()
        try:
            r = _parse(_run(handle_ghostscripter_ping({"timeout": 0.2})))
            self.assertIn("error", r)
        finally:
            b.GHOSTSCRIPTER_PORT = orig


if __name__ == "__main__":
    unittest.main()
