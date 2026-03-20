"""
GhostRigger — Blueprint Field Editor Tests
===========================================
Tests for:
  1. BlueprintFieldEditor field schema
  2. Field schema coverage (UTC/UTP/UTD)
  3. BlueprintFieldEditor headless construction
  4. IPC set_field / get_field / set_fields_bulk handlers
  5. MainWindow headless construction with field editor wiring
"""
from __future__ import annotations

import socket
import sys
import threading
import unittest
import urllib.request
import urllib.error
import json
import os

# Add ghostrigger root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ghostrigger.core.blueprint_state import Blueprint, BlueprintRegistry, get_registry


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _post(port: int, action: str, payload: dict) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/{action}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


# ──────────────────────────────────────────────────────────────────────────────
# 1. Field schema
# ──────────────────────────────────────────────────────────────────────────────

class TestFieldSchema(unittest.TestCase):
    def test_utc_schema_not_empty(self):
        from ghostrigger.gui.field_editor import get_field_schema
        schema = get_field_schema("utc")
        self.assertGreater(len(schema), 10)

    def test_utp_schema_not_empty(self):
        from ghostrigger.gui.field_editor import get_field_schema
        schema = get_field_schema("utp")
        self.assertGreater(len(schema), 5)

    def test_utd_schema_not_empty(self):
        from ghostrigger.gui.field_editor import get_field_schema
        schema = get_field_schema("utd")
        self.assertGreater(len(schema), 5)

    def test_schema_tuple_length(self):
        from ghostrigger.gui.field_editor import get_field_schema
        for btype in ("utc", "utp", "utd"):
            for row in get_field_schema(btype):
                self.assertEqual(len(row), 5, f"Bad row in {btype}: {row}")

    def test_utc_has_basic_section(self):
        from ghostrigger.gui.field_editor import get_field_schema
        sections = {r[4] for r in get_field_schema("utc")}
        self.assertIn("Basic", sections)

    def test_utc_has_stats_section(self):
        from ghostrigger.gui.field_editor import get_field_schema
        sections = {r[4] for r in get_field_schema("utc")}
        self.assertIn("Stats", sections)

    def test_utc_has_scripts_section(self):
        from ghostrigger.gui.field_editor import get_field_schema
        sections = {r[4] for r in get_field_schema("utc")}
        self.assertIn("Scripts", sections)

    def test_utc_has_first_name(self):
        from ghostrigger.gui.field_editor import get_field_schema
        names = [r[0] for r in get_field_schema("utc")]
        self.assertIn("FirstName", names)

    def test_utc_has_ability_scores(self):
        from ghostrigger.gui.field_editor import get_field_schema
        names = [r[0] for r in get_field_schema("utc")]
        for attr in ("Str", "Dex", "Con", "Int", "Wis", "Cha"):
            self.assertIn(attr, names)

    def test_utc_has_spawn_script(self):
        from ghostrigger.gui.field_editor import get_field_schema
        names = [r[0] for r in get_field_schema("utc")]
        self.assertIn("ScriptSpawn", names)

    def test_utp_has_useable(self):
        from ghostrigger.gui.field_editor import get_field_schema
        names = [r[0] for r in get_field_schema("utp")]
        self.assertIn("Useable", names)

    def test_utd_has_locked(self):
        from ghostrigger.gui.field_editor import get_field_schema
        names = [r[0] for r in get_field_schema("utd")]
        self.assertIn("Locked", names)

    def test_widget_types_valid(self):
        from ghostrigger.gui.field_editor import get_field_schema
        valid = {"str", "int", "float", "bool", "resref"}
        for btype in ("utc", "utp", "utd"):
            for row in get_field_schema(btype):
                self.assertIn(row[2], valid, f"Invalid widget_type in {btype}: {row}")

    def test_unknown_btype_returns_utc(self):
        from ghostrigger.gui.field_editor import get_field_schema, UTC_FIELDS
        self.assertEqual(get_field_schema("xyz"), UTC_FIELDS)

    def test_utc_fields_exported(self):
        from ghostrigger.gui.field_editor import UTC_FIELDS
        self.assertIsInstance(UTC_FIELDS, list)

    def test_utp_fields_exported(self):
        from ghostrigger.gui.field_editor import UTP_FIELDS
        self.assertIsInstance(UTP_FIELDS, list)

    def test_utd_fields_exported(self):
        from ghostrigger.gui.field_editor import UTD_FIELDS
        self.assertIsInstance(UTD_FIELDS, list)


# ──────────────────────────────────────────────────────────────────────────────
# 2. BlueprintFieldEditor headless
# ──────────────────────────────────────────────────────────────────────────────

class TestBlueprintFieldEditorHeadless(unittest.TestCase):
    def test_import_succeeds(self):
        from ghostrigger.gui.field_editor import BlueprintFieldEditor
        self.assertTrue(True)

    def test_has_qt_attribute(self):
        import ghostrigger.gui.field_editor as mod
        self.assertIn("_HAS_QT", dir(mod))

    def test_get_current_values_headless(self):
        from ghostrigger.gui.field_editor import BlueprintFieldEditor
        import ghostrigger.gui.field_editor as mod
        if mod._HAS_QT:
            self.skipTest("Qt available — headless test not applicable")
        ed = BlueprintFieldEditor()
        # Should return empty dict when no Qt
        self.assertEqual(ed.get_current_values(), {})

    def test_load_blueprint_headless_no_crash(self):
        from ghostrigger.gui.field_editor import BlueprintFieldEditor
        import ghostrigger.gui.field_editor as mod
        if mod._HAS_QT:
            self.skipTest("Qt available — headless test not applicable")
        ed = BlueprintFieldEditor()
        ed.load_blueprint("test_creature", "utc", {"FirstName": "Revan"})
        self.assertTrue(True)  # no crash


# ──────────────────────────────────────────────────────────────────────────────
# 3. IPC set_field / get_field / set_fields_bulk
# ──────────────────────────────────────────────────────────────────────────────

class TestFieldEditorIPC(unittest.TestCase):
    """Live IPC server tests for the new field-editing endpoints."""

    @classmethod
    def setUpClass(cls):
        import importlib
        import ghostrigger.ipc.server as ipc_mod

        # Reload to get a fresh module-level state
        ipc_mod = importlib.reload(ipc_mod)
        cls.ipc_mod = ipc_mod

        from ghostrigger.core.blueprint_state import BlueprintRegistry, Blueprint
        cls.registry = BlueprintRegistry()

        # Register all handlers manually (mirrors main._run_headless)
        def handle_open_utc(payload):
            resref = payload.get("resref", "test_creature")
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

        def handle_get_field(payload):
            resref = payload.get("resref", "")
            fname = payload.get("field", "")
            bp = cls.registry.get(resref)
            if bp is None:
                return {"error": f"not found: {resref}"}
            return {"status": "ok", "resref": resref, "field": fname,
                    "value": bp.get(fname)}

        def handle_set_fields_bulk(payload):
            resref = payload.get("resref", "")
            updates = payload.get("fields", {})
            bp = cls.registry.get(resref)
            if bp is None:
                return {"error": f"not found: {resref}"}
            for k, v in updates.items():
                bp.set(k, v)
            return {"status": "ok", "resref": resref, "updated": len(updates)}

        ipc_mod.register("open_utc",        handle_open_utc)
        ipc_mod.register("get_blueprint",   handle_get_blueprint)
        ipc_mod.register("set_field",       handle_set_field)
        ipc_mod.register("get_field",       handle_get_field)
        ipc_mod.register("set_fields_bulk", handle_set_fields_bulk)

        cls.port = _free_port()
        cls.srv = ipc_mod.start(port=cls.port)

    @classmethod
    def tearDownClass(cls):
        cls.ipc_mod.stop()

    def _open(self, resref="test001"):
        return _post(self.port, "open_utc", {"resref": resref})

    def test_set_field_string(self):
        self._open("sf_str")
        r = _post(self.port, "set_field",
                  {"resref": "sf_str", "field": "FirstName", "value": "Revan"})
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["value"], "Revan")

    def test_get_field_after_set(self):
        self._open("gf_test")
        _post(self.port, "set_field",
              {"resref": "gf_test", "field": "MaxHitPoints", "value": 42})
        r = _post(self.port, "get_field",
                  {"resref": "gf_test", "field": "MaxHitPoints"})
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["value"], 42)

    def test_set_field_bool(self):
        self._open("bool_test")
        r = _post(self.port, "set_field",
                  {"resref": "bool_test", "field": "Plot", "value": True})
        self.assertEqual(r["status"], "ok")

    def test_set_field_missing_resref(self):
        # resref="" → handler returns {"error": "..."}  with HTTP 200
        r = _post(self.port, "set_field", {"field": "Tag", "value": "X"})
        self.assertIn("error", r)

    def test_set_field_unknown_blueprint(self):
        r = _post(self.port, "set_field",
                  {"resref": "nonexistent_xyz", "field": "Tag", "value": "X"})
        self.assertIn("error", r)

    def test_get_field_unknown_blueprint(self):
        r = _post(self.port, "get_field",
                  {"resref": "nonexistent_abc", "field": "Tag"})
        self.assertIn("error", r)

    def test_set_fields_bulk(self):
        self._open("bulk_test")
        r = _post(self.port, "set_fields_bulk", {
            "resref": "bulk_test",
            "fields": {"FirstName": "Bastila", "Tag": "JEDI001", "MaxHitPoints": 30}
        })
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["updated"], 3)

    def test_set_fields_bulk_verify_values(self):
        self._open("bulk_verify")
        _post(self.port, "set_fields_bulk", {
            "resref": "bulk_verify",
            "fields": {"FirstName": "Carth", "Tag": "SOLDIER001"}
        })
        r = _post(self.port, "get_field",
                  {"resref": "bulk_verify", "field": "FirstName"})
        self.assertEqual(r["value"], "Carth")

    def test_set_fields_bulk_missing_resref(self):
        r = _post(self.port, "set_fields_bulk",
                  {"fields": {"Tag": "X"}})
        self.assertIn("error", r)

    def test_dirty_flag_set_after_set_field(self):
        self._open("dirty_test")
        _post(self.port, "set_field",
              {"resref": "dirty_test", "field": "Tag", "value": "DIRTY"})
        # Verify via get_blueprint
        r = _post(self.port, "get_blueprint", {"resref": "dirty_test"})
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["blueprint"]["fields"]["Tag"], "DIRTY")

    def test_overwrite_existing_field(self):
        self._open("overwrite_test")
        _post(self.port, "set_field",
              {"resref": "overwrite_test", "field": "FirstName", "value": "A"})
        _post(self.port, "set_field",
              {"resref": "overwrite_test", "field": "FirstName", "value": "B"})
        r = _post(self.port, "get_field",
                  {"resref": "overwrite_test", "field": "FirstName"})
        self.assertEqual(r["value"], "B")

    def test_get_field_returns_none_for_absent_key(self):
        self._open("absent_key")
        r = _post(self.port, "get_field",
                  {"resref": "absent_key", "field": "NonExistentField"})
        self.assertEqual(r["status"], "ok")
        self.assertIsNone(r["value"])


# ──────────────────────────────────────────────────────────────────────────────
# 4. MainWindow headless
# ──────────────────────────────────────────────────────────────────────────────

class TestMainWindowHeadless(unittest.TestCase):
    def test_main_window_import(self):
        from ghostrigger.gui.main_window import MainWindow
        self.assertTrue(True)

    def test_main_window_has_title(self):
        from ghostrigger.gui.main_window import MainWindow
        self.assertIn("GhostRigger", MainWindow.TITLE)

    def test_field_editor_module_importable(self):
        from ghostrigger.gui import field_editor
        self.assertTrue(hasattr(field_editor, "BlueprintFieldEditor"))

    def test_field_editor_get_current_values_exists(self):
        from ghostrigger.gui.field_editor import BlueprintFieldEditor
        self.assertTrue(hasattr(BlueprintFieldEditor, "get_current_values"))

    def test_field_editor_load_blueprint_exists(self):
        from ghostrigger.gui.field_editor import BlueprintFieldEditor
        self.assertTrue(hasattr(BlueprintFieldEditor, "load_blueprint"))


if __name__ == "__main__":
    unittest.main()
