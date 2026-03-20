"""
GhostScripter — Test Suite
============================
Tests for: IPC server, script registry, NWScript compiler stub,
           main window (headless/Qt), and script round-trip.
"""
from __future__ import annotations

import json
import socket
import threading
import time
import unittest
import urllib.request
import urllib.error


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _free_port() -> int:
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


def _gs_path():
    import sys, os
    p = os.path.dirname(os.path.dirname(__file__))
    if p not in sys.path:
        sys.path.insert(0, p)


# ─── Script dataclass ─────────────────────────────────────────────────────────

class TestScript(unittest.TestCase):
    def setUp(self):
        _gs_path()
        from ghostscripter.core.script_state import Script
        self.Script = Script

    def test_default_dirty_false(self):
        s = self.Script(resref="test")
        assert not s.dirty

    def test_to_dict_keys(self):
        s = self.Script(resref="myscript", source="void main() {}")
        d = s.to_dict()
        assert "resref" in d
        assert "source" in d
        assert "compiled" in d
        assert "dirty" in d
        assert "diagnostics" in d

    def test_to_dict_compiled_bool(self):
        s = self.Script(resref="x")
        d = s.to_dict()
        assert d is not None
        assert d["compiled"] is False

    def test_from_dict_roundtrip(self):
        s = self.Script(resref="onenter", source="void main() { SpeakString(\"hi\"); }")
        d = s.to_dict()
        s2 = self.Script.from_dict(d)
        assert s2.resref == "onenter"
        assert s2.source == s.source

    def test_source_defaults_empty(self):
        s = self.Script(resref="x")
        assert s.source == ""

    def test_diagnostics_defaults_empty_list(self):
        s = self.Script(resref="x")
        assert s.diagnostics == []


# ─── Script Registry ──────────────────────────────────────────────────────────

class TestScriptRegistry(unittest.TestCase):
    def setUp(self):
        _gs_path()
        from ghostscripter.core.script_state import ScriptRegistry, Script
        self.Registry = ScriptRegistry
        self.Script = Script

    def _reg(self):
        return self.Registry()

    def test_empty_len_zero(self):
        assert len(self._reg()) == 0

    def test_add_and_get(self):
        r = self._reg()
        s = self.Script(resref="k_hen_death", source="void main() {}")
        r.add(s)
        assert r.get("k_hen_death") is s

    def test_get_case_insensitive(self):
        r = self._reg()
        s = self.Script(resref="K_HEN_Death")
        r.add(s)
        assert r.get("k_hen_death") is s
        assert r.get("K_HEN_DEATH") is s

    def test_remove_returns_true(self):
        r = self._reg()
        r.add(self.Script(resref="x"))
        assert r.remove("x") is True
        assert len(r) == 0

    def test_remove_missing_returns_false(self):
        assert self._reg().remove("nope") is False

    def test_list_all(self):
        r = self._reg()
        for i in range(5):
            r.add(self.Script(resref=f"s{i}"))
        assert len(r.list_all()) == 5

    def test_clear(self):
        r = self._reg()
        r.add(self.Script(resref="a"))
        r.clear()
        assert len(r) == 0

    def test_thread_safety_concurrent_add(self):
        r = self._reg()

        def add_many(prefix):
            for i in range(50):
                r.add(self.Script(resref=f"{prefix}_{i}"))

        t1 = threading.Thread(target=add_many, args=("x",))
        t2 = threading.Thread(target=add_many, args=("y",))
        t1.start(); t2.start()
        t1.join(); t2.join()
        assert len(r) == 100


# ─── NWScript Compiler Stub ───────────────────────────────────────────────────

class TestNWScriptCompiler(unittest.TestCase):
    def setUp(self):
        _gs_path()
        from ghostscripter.core.script_state import NWScriptCompiler
        self.compiler = NWScriptCompiler()

    def _compile(self, src, resref="script"):
        return self.compiler.compile(src, resref)

    def test_valid_void_main_success(self):
        r = self._compile("void main() {\n    SpeakString(\"hi\");\n}\n")
        assert r["success"] is True
        assert r["errors"] == []

    def test_valid_starting_conditional_success(self):
        r = self._compile("int StartingConditional() {\n    return 1;\n}\n")
        assert r["success"] is True

    def test_missing_entry_point_fails(self):
        r = self._compile("int helper() { return 0; }")
        assert r["success"] is False
        assert any("entry point" in e.lower() for e in r["errors"])

    def test_unbalanced_braces_fails(self):
        r = self._compile("void main() { if (1) { }")
        assert r["success"] is False
        assert any("brace" in e.lower() for e in r["errors"])

    def test_unknown_function_generates_warning(self):
        r = self._compile("void main() { MyCustomFunction(); }")
        assert any("MyCustomFunction" in w for w in r["warnings"])

    def test_stdlib_func_no_warning(self):
        r = self._compile("void main() { SpeakString(\"hello\"); }")
        assert not any("SpeakString" in w for w in r["warnings"])

    def test_result_has_required_keys(self):
        r = self._compile("void main() {}")
        for k in ("success", "resref", "ncs", "errors", "warnings"):
            assert k in r, f"missing key: {k}"

    def test_success_ncs_has_header(self):
        r = self._compile("void main() {}")
        assert r["ncs"] == b"NCS V1.0".hex()

    def test_failed_ncs_is_none(self):
        r = self._compile("not a script at all")
        assert r["ncs"] is None

    def test_resref_preserved(self):
        r = self._compile("void main() {}", "k_hen_death")
        assert r["resref"] == "k_hen_death"

    def test_empty_source_fails(self):
        r = self._compile("")
        assert r["success"] is False

    def test_multiple_errors_collected(self):
        # Missing entry point + unbalanced braces
        r = self._compile("int helper() { return 1; {{{")
        assert len(r["errors"]) >= 2

    def test_stdlib_set_populated(self):
        assert len(self.compiler.STDLIB_FUNCS) > 10


# ─── IPC Server ───────────────────────────────────────────────────────────────

class TestGhostScripterIPC(unittest.TestCase):
    """Live IPC server tests on a random free port."""

    @classmethod
    def setUpClass(cls):
        _gs_path()
        from ghostscripter.ipc import server as ipc_mod
        # Register the full set of handlers (as main.py does)
        from ghostscripter.core.script_state import get_registry, Script, NWScriptCompiler

        registry = get_registry()
        compiler = NWScriptCompiler()

        def handle_open_script(payload):
            resref = payload.get("resref", "new_script")
            source = payload.get("source", "void main() {}\n")
            s = Script(resref=resref, source=source)
            registry.add(s)
            return {"status": "ok", "resref": resref}

        def handle_get_script(payload):
            resref = payload.get("resref", "")
            s = registry.get(resref)
            if s is None:
                return {"error": f"script not found: {resref}"}
            return {"status": "ok", "script": s.to_dict()}

        def handle_compile(payload):
            resref = payload.get("resref", "")
            s = registry.get(resref)
            source = s.source if s else payload.get("source", "")
            if not source:
                return {"error": "no source"}
            result = compiler.compile(source, resref)
            return {"status": "ok", **result}

        def handle_decompile(payload):
            return {"status": "ok", "source": "// stub\nvoid main() {}\n"}

        def handle_set_resref(payload):
            return {"status": "ok", "resref": payload.get("resref", ""),
                    "action": "set_resref"}

        def handle_list_scripts(payload):
            return {"status": "ok",
                    "scripts": [s.to_dict() for s in registry.list_all()]}

        for action, fn in [
            ("open_script",  handle_open_script),
            ("get_script",   handle_get_script),
            ("compile",      handle_compile),
            ("decompile",    handle_decompile),
            ("set_resref",   handle_set_resref),
            ("list_scripts", handle_list_scripts),
        ]:
            ipc_mod.register(action, fn)

        cls.port = _free_port()
        cls.srv = ipc_mod.start(host="127.0.0.1", port=cls.port, daemon=True)
        time.sleep(0.1)
        cls.ipc = ipc_mod

    @classmethod
    def tearDownClass(cls):
        cls.ipc.stop()

    def _post(self, action, payload=None):
        return _post(self.port, action, payload)

    # ── Ping ─────────────────────────────────────────────────────────────────

    def test_ping_ok(self):
        r = self._post("ping")
        assert r["status"] == "ok"

    def test_ping_program_name(self):
        r = self._post("ping")
        assert r["program"] == "GhostScripter"

    def test_ping_has_version(self):
        assert "version" in self._post("ping")

    def test_ping_port(self):
        r = self._post("ping")
        assert r["port"] == 7002

    # ── Errors ────────────────────────────────────────────────────────────────

    def test_unknown_action_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post("no_such_action")
        assert ctx.exception.code == 404

    def test_malformed_json_400(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/ping",
            data=b"not json!!",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        assert ctx.exception.code == 400

    # ── Open / Get / List scripts ─────────────────────────────────────────────

    def test_open_script_creates_entry(self):
        r = self._post("open_script", {"resref": "k_test_01",
                                        "source": "void main() {}"})
        assert r["status"] == "ok"
        assert r["resref"] == "k_test_01"

    def test_get_script_after_open(self):
        self._post("open_script", {"resref": "k_test_get",
                                    "source": "void main() { SpeakString(\"hi\"); }"})
        r = self._post("get_script", {"resref": "k_test_get"})
        assert r["status"] == "ok"
        assert "k_test_get" in r["script"]["resref"]
        assert "void main" in r["script"]["source"]

    def test_get_script_missing_returns_error(self):
        r = self._post("get_script", {"resref": "does_not_exist_xyz"})
        assert "error" in r

    def test_list_scripts_contains_opened(self):
        self._post("open_script", {"resref": "k_list_test"})
        r = self._post("list_scripts")
        assert r["status"] == "ok"
        resrefs = [s["resref"] for s in r["scripts"]]
        assert "k_list_test" in resrefs

    # ── Compile ───────────────────────────────────────────────────────────────

    def test_compile_valid_source_via_ipc(self):
        r = self._post("compile", {"source": "void main() { SpeakString(\"hi\"); }"})
        assert r["status"] == "ok"
        assert r["success"] is True

    def test_compile_invalid_source_via_ipc(self):
        r = self._post("compile", {"source": "this is not nwscript"})
        assert r["status"] == "ok"
        assert r["success"] is False
        assert r["errors"]

    def test_compile_by_resref(self):
        self._post("open_script", {"resref": "k_compile_by_ref",
                                    "source": "void main() {}"})
        r = self._post("compile", {"resref": "k_compile_by_ref"})
        assert r["status"] == "ok"
        assert r["success"] is True

    # ── Decompile ─────────────────────────────────────────────────────────────

    def test_decompile_returns_stub_source(self):
        r = self._post("decompile", {"resref": "any"})
        assert r["status"] == "ok"
        assert "source" in r

    # ── set_resref ────────────────────────────────────────────────────────────

    def test_set_resref_returns_action(self):
        r = self._post("set_resref", {"resref": "k_new_ref"})
        assert r["status"] == "ok"
        assert r["action"] == "set_resref"
        assert r["resref"] == "k_new_ref"

    # ── is_running ────────────────────────────────────────────────────────────

    def test_is_running_true(self):
        from ghostscripter.ipc.server import is_running
        assert is_running() is True


# ─── IPC module surface ───────────────────────────────────────────────────────

class TestGhostScripterIPCModule(unittest.TestCase):
    def setUp(self):
        _gs_path()

    def test_start_callable(self):
        from ghostscripter.ipc.server import start
        assert callable(start)

    def test_stop_callable(self):
        from ghostscripter.ipc.server import stop
        assert callable(stop)

    def test_register_callable(self):
        from ghostscripter.ipc.server import register
        assert callable(register)

    def test_port_constant(self):
        from ghostscripter.ipc.server import PORT
        assert PORT == 7002


# ─── Main window (headless) ───────────────────────────────────────────────────

class TestGhostScripterMainWindowHeadless(unittest.TestCase):
    def setUp(self):
        _gs_path()

    def test_main_window_importable(self):
        from ghostscripter.gui.main_window import MainWindow
        assert MainWindow is not None

    def test_highlighter_importable(self):
        from ghostscripter.gui.main_window import NWScriptHighlighter
        assert NWScriptHighlighter is not None

    def test_main_window_title_constant(self):
        from ghostscripter.gui.main_window import MainWindow
        assert "GhostScripter" in MainWindow.TITLE

    def test_main_window_style_has_background(self):
        from ghostscripter.gui.main_window import MainWindow
        assert "#1e1e1e" in MainWindow.STYLE

    def test_main_window_headless_instantiation(self):
        """In headless CI (no Qt) MainWindow should not raise."""
        from ghostscripter.gui import main_window as mw_mod
        # Save and force headless
        orig = mw_mod._HAS_QT
        mw_mod._HAS_QT = False
        try:
            win = mw_mod.MainWindow()
            assert win is not None
        finally:
            mw_mod._HAS_QT = orig


# ─── main.py entry point ──────────────────────────────────────────────────────

class TestGhostScripterMain(unittest.TestCase):
    def setUp(self):
        _gs_path()

    def test_main_module_importable(self):
        import importlib.util, os
        spec = importlib.util.spec_from_file_location(
            "gs_main",
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "main.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert callable(mod.main)

    def test_run_headless_function_exists(self):
        import importlib.util, os
        spec = importlib.util.spec_from_file_location(
            "gs_main2",
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "main.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert callable(mod._run_headless)


if __name__ == "__main__":
    unittest.main()
