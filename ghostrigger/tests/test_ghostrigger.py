"""
GhostRigger — Test Suite
=========================
Tests for IPC server, blueprint registry, and round-trip GFF fields.
"""
from __future__ import annotations

import json
import socket
import threading
import time
import unittest
import urllib.request
import urllib.error


# ─── helpers ──────────────────────────────────────────────────────────────────

def _free_port() -> int:
    """Return a free TCP port."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _post(port: int, action: str, payload: dict = None) -> dict:
    body = json.dumps(payload or {}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/{action}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


# ─── Blueprint state ──────────────────────────────────────────────────────────

class TestBlueprintRegistry(unittest.TestCase):
    def setUp(self):
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from ghostrigger.core.blueprint_state import BlueprintRegistry, Blueprint
        self.Registry = BlueprintRegistry
        self.Blueprint = Blueprint

    def _reg(self):
        return self.Registry()

    def test_empty_registry_len_zero(self):
        assert len(self._reg()) == 0

    def test_add_and_get(self):
        r = self._reg()
        bp = self.Blueprint(resref="c_bantha", blueprint_type="utc",
                            fields={"FirstName": "Bantha"})
        r.add(bp)
        assert len(r) == 1
        assert r.get("c_bantha") is bp

    def test_get_case_insensitive(self):
        r = self._reg()
        bp = self.Blueprint(resref="C_Bantha", blueprint_type="utc")
        r.add(bp)
        assert r.get("c_bantha") is bp
        assert r.get("C_BANTHA") is bp

    def test_remove_existing(self):
        r = self._reg()
        r.add(self.Blueprint(resref="x", blueprint_type="utc"))
        assert r.remove("x") is True
        assert len(r) == 0

    def test_remove_missing_returns_false(self):
        assert self._reg().remove("nonexistent") is False

    def test_list_all(self):
        r = self._reg()
        for i in range(3):
            r.add(self.Blueprint(resref=f"bp{i}", blueprint_type="utc"))
        assert len(r.list_all()) == 3

    def test_clear(self):
        r = self._reg()
        r.add(self.Blueprint(resref="a", blueprint_type="utp"))
        r.clear()
        assert len(r) == 0

    def test_blueprint_to_dict(self):
        bp = self.Blueprint(resref="plc_chest", blueprint_type="utp",
                            fields={"Tag": "PLY_CHEST"})
        d = bp.to_dict()
        assert d["resref"] == "plc_chest"
        assert d["type"] == "utp"
        assert d["fields"]["Tag"] == "PLY_CHEST"

    def test_blueprint_from_dict_roundtrip(self):
        original = self.Blueprint(resref="door01", blueprint_type="utd",
                                  fields={"Locked": 1, "Tag": "DOOR01"})
        restored = self.Blueprint.from_dict(original.to_dict())
        assert restored.resref == "door01"
        assert restored.blueprint_type == "utd"
        assert restored.fields["Locked"] == 1

    def test_dirty_flag_set_on_set(self):
        bp = self.Blueprint(resref="x", blueprint_type="utc")
        assert not bp.dirty
        bp.set("FirstName", "Test")
        assert bp.dirty

    def test_blueprint_get_returns_default(self):
        bp = self.Blueprint(resref="x", blueprint_type="utc")
        assert bp.get("NoSuchField", "default_val") == "default_val"

    def test_thread_safety_concurrent_add(self):
        r = self._reg()
        def add_many(prefix):
            for i in range(50):
                r.add(self.Blueprint(resref=f"{prefix}_{i}", blueprint_type="utc"))
        t1 = threading.Thread(target=add_many, args=("a",))
        t2 = threading.Thread(target=add_many, args=("b",))
        t1.start(); t2.start()
        t1.join(); t2.join()
        assert len(r) == 100


# ─── IPC Server ───────────────────────────────────────────────────────────────

class TestGhostRiggerIPC(unittest.TestCase):
    """Live IPC server tests on a random free port."""

    @classmethod
    def setUpClass(cls):
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from ghostrigger.ipc import server as ipc_mod
        from ghostrigger.core.blueprint_state import get_registry, Blueprint

        registry = get_registry()

        def handle_open_utc(payload):
            resref = payload.get("resref", "new_creature")
            bp = Blueprint(resref=resref, blueprint_type="utc",
                           fields={"FirstName": "Unnamed", "Tag": resref.upper()})
            registry.add(bp)
            return {"status": "ok", "resref": resref}

        def handle_open_utp(payload):
            resref = payload.get("resref", "new_placeable")
            bp = Blueprint(resref=resref, blueprint_type="utp",
                           fields={"Tag": resref.upper(), "TemplateResRef": resref})
            registry.add(bp)
            return {"status": "ok", "resref": resref}

        def handle_open_utd(payload):
            resref = payload.get("resref", "new_door")
            bp = Blueprint(resref=resref, blueprint_type="utd",
                           fields={"Tag": resref.upper(), "Locked": 0})
            registry.add(bp)
            return {"status": "ok", "resref": resref}

        def handle_get_blueprint(payload):
            resref = payload.get("resref", "")
            bp = registry.get(resref)
            if bp is None:
                return {"error": f"blueprint not found: {resref}"}
            return {"status": "ok", "blueprint": bp.to_dict()}

        def handle_list_blueprints(payload):
            bps = registry.list_all()
            return {"status": "ok", "blueprints": [b.to_dict() for b in bps]}

        def handle_save_blueprint(payload):
            data = payload.get("blueprint", {})
            bp = Blueprint.from_dict(data)
            bp.dirty = False
            registry.add(bp)
            return {"status": "ok", "resref": bp.resref}

        ipc_mod.register("open_utc",        handle_open_utc)
        ipc_mod.register("open_utp",        handle_open_utp)
        ipc_mod.register("open_utd",        handle_open_utd)
        ipc_mod.register("get_blueprint",   handle_get_blueprint)
        ipc_mod.register("list_blueprints", handle_list_blueprints)
        ipc_mod.register("save_blueprint",  handle_save_blueprint)

        cls.port = _free_port()
        cls.srv = ipc_mod.start(host="127.0.0.1", port=cls.port, daemon=True)
        time.sleep(0.1)   # let the thread start
        cls.ipc = ipc_mod  # keep reference for teardown

    @classmethod
    def tearDownClass(cls):
        cls.ipc.stop()

    def _post(self, action, payload=None):
        return _post(self.port, action, payload)

    # ── Ping ─────────────────────────────────────────────────────────────────

    def test_ping_returns_ok(self):
        r = self._post("ping")
        assert r["status"] == "ok"

    def test_ping_program_name(self):
        r = self._post("ping")
        assert r["program"] == "GhostRigger"

    def test_ping_has_version(self):
        r = self._post("ping")
        assert "version" in r

    def test_ping_reports_port(self):
        r = self._post("ping")
        assert r["port"] == 7001

    # ── Unknown action ────────────────────────────────────────────────────────

    def test_unknown_action_404(self):
        try:
            self._post("no_such_action")
            assert False, "Should have raised HTTPError"
        except urllib.error.HTTPError as e:
            assert e.code == 404

    # ── Malformed JSON ────────────────────────────────────────────────────────

    def test_malformed_json_400(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/ping",
            data=b"not json!!",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False, "Should have raised"
        except urllib.error.HTTPError as e:
            assert e.code == 400

    # ── Open / get / list blueprints ─────────────────────────────────────────

    def test_open_utc_creates_blueprint(self):
        r = self._post("open_utc", {"resref": "test_creature"})
        assert r["status"] == "ok"
        assert r["resref"] == "test_creature"

    def test_get_blueprint_after_open(self):
        self._post("open_utc", {"resref": "my_npc"})
        r = self._post("get_blueprint", {"resref": "my_npc"})
        assert r["status"] == "ok"
        assert r["blueprint"]["resref"] == "my_npc"
        assert r["blueprint"]["type"] == "utc"

    def test_get_blueprint_missing_returns_error(self):
        r = self._post("get_blueprint", {"resref": "this_does_not_exist_xyz"})
        assert "error" in r

    def test_list_blueprints_after_opens(self):
        self._post("open_utp", {"resref": "plc_list_test"})
        r = self._post("list_blueprints")
        assert r["status"] == "ok"
        resrefs = [b["resref"] for b in r["blueprints"]]
        assert "plc_list_test" in resrefs

    def test_open_utd_type(self):
        r = self._post("open_utd", {"resref": "door_test"})
        assert r["status"] == "ok"
        g = self._post("get_blueprint", {"resref": "door_test"})
        assert g["blueprint"]["type"] == "utd"

    def test_save_blueprint_roundtrip(self):
        bp_data = {"resref": "saved_utc", "type": "utc",
                   "fields": {"FirstName": "Saved Hero", "Tag": "SAVED"}}
        r = self._post("save_blueprint", {"blueprint": bp_data})
        assert r["status"] == "ok"
        g = self._post("get_blueprint", {"resref": "saved_utc"})
        assert g["blueprint"]["fields"]["FirstName"] == "Saved Hero"

    def test_is_running_true(self):
        from ghostrigger.ipc import server as ipc_mod
        assert ipc_mod.is_running() is True


# ─── IPC module structure ──────────────────────────────────────────────────────

class TestGhostRiggerIPCModule(unittest.TestCase):
    def setUp(self):
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

    def test_start_function_exists(self):
        from ghostrigger.ipc.server import start
        assert callable(start)

    def test_stop_function_exists(self):
        from ghostrigger.ipc.server import stop
        assert callable(stop)

    def test_register_function_exists(self):
        from ghostrigger.ipc.server import register
        assert callable(register)

    def test_port_constant(self):
        from ghostrigger.ipc.server import PORT
        assert PORT == 7001


if __name__ == "__main__":
    unittest.main()
