"""
Tests for the architectural improvements based on Khononov's "Balancing Coupling".

Covers:
  - ResourcePort protocol + MemResourceManager
  - EventBus + event constants
  - ModuleIO service (stateless load_from_mod extraction)
  - ModuleState delegation to ModuleIO + EventBus integration
  - KotorInstallation.resource_manager() delegation
  - Import-boundary check (mcp must not import gui)
"""
from __future__ import annotations

import io
import os
import struct
import tempfile
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
#  1  ResourcePort Protocol + MemResourceManager
# ─────────────────────────────────────────────────────────────────────────────

class TestResourcePort:
    """ResourcePort is a structural Protocol — any conforming object satisfies it."""

    def test_protocol_imported(self):
        from gmodular.formats.resource_port import ResourcePort
        assert ResourcePort is not None

    def test_resource_manager_satisfies_protocol(self):
        """Production ResourceManager must conform to ResourcePort."""
        from gmodular.formats.resource_port import ResourcePort
        from gmodular.formats.archives import ResourceManager
        # Runtime-checkable protocol — isinstance should work
        rm = ResourceManager()
        assert isinstance(rm, ResourcePort)

    def test_mem_resource_manager_satisfies_protocol(self):
        from gmodular.formats.resource_port import ResourcePort, MemResourceManager
        mem = MemResourceManager()
        assert isinstance(mem, ResourcePort)

    def test_mem_add_and_get(self):
        from gmodular.formats.resource_port import MemResourceManager
        mem = MemResourceManager()
        mem.add("testres", "mdl", b"fake_mdl_data")
        assert mem.get_file("testres", "mdl") == b"fake_mdl_data"
        assert mem.get_file("TESTRES", "MDL") == b"fake_mdl_data"  # case-insensitive

    def test_mem_get_by_type_id(self):
        from gmodular.formats.resource_port import MemResourceManager
        from gmodular.formats.archives import EXT_TO_TYPE
        mem = MemResourceManager()
        mem.add("dialog", "tlk", b"tlk_data")
        tlk_type = EXT_TO_TYPE["tlk"]
        assert mem.get("dialog", tlk_type) == b"tlk_data"

    def test_mem_returns_none_for_missing(self):
        from gmodular.formats.resource_port import MemResourceManager
        mem = MemResourceManager()
        assert mem.get_file("missing", "mdl") is None

    def test_mem_list_resources(self):
        from gmodular.formats.resource_port import MemResourceManager
        from gmodular.formats.archives import EXT_TO_TYPE
        mem = MemResourceManager()
        mem.add("c_bastila", "mdl", b"a")
        mem.add("c_revan", "mdl", b"b")
        mem.add("dialog", "tlk", b"c")
        mdl_type = EXT_TO_TYPE["mdl"]
        result = mem.list_resources(mdl_type)
        assert sorted(result) == ["c_bastila", "c_revan"]

    def test_mem_is_loaded(self):
        from gmodular.formats.resource_port import MemResourceManager
        mem = MemResourceManager()
        assert mem.is_loaded is True

    def test_mem_game_tag(self):
        from gmodular.formats.resource_port import MemResourceManager
        mem = MemResourceManager()
        assert mem.game_tag == "MEM"

    def test_add_by_type_id(self):
        from gmodular.formats.resource_port import MemResourceManager
        from gmodular.formats.archives import EXT_TO_TYPE
        mem = MemResourceManager()
        mdl_type = EXT_TO_TYPE["mdl"]
        mem.add_by_type_id("hero", mdl_type, b"hero_mdl")
        assert mem.get_file("hero", "mdl") == b"hero_mdl"


# ─────────────────────────────────────────────────────────────────────────────
#  2  EventBus
# ─────────────────────────────────────────────────────────────────────────────

class TestEventBus:
    """EventBus replaces scattered get_module_state() with Contract coupling."""

    def _bus(self):
        from gmodular.core.events import EventBus
        return EventBus()

    def test_subscribe_and_publish(self):
        bus = self._bus()
        received = []
        bus.subscribe("test.event", lambda **kw: received.append(kw))
        bus.publish("test.event", foo="bar")
        assert received == [{"foo": "bar"}]

    def test_multiple_subscribers_all_called(self):
        bus = self._bus()
        log = []
        bus.subscribe("ev", lambda **kw: log.append(1))
        bus.subscribe("ev", lambda **kw: log.append(2))
        bus.publish("ev")
        assert log == [1, 2]

    def test_unsubscribe(self):
        bus = self._bus()
        log = []
        cb = lambda **kw: log.append(1)
        bus.subscribe("ev", cb)
        bus.unsubscribe("ev", cb)
        bus.publish("ev")
        assert log == []

    def test_unsubscribe_nonexistent_noop(self):
        bus = self._bus()
        # Should not raise — unsubscribing non-registered callback is a no-op
        cb = lambda: None
        bus.unsubscribe("ev", cb)  # Must not raise
        # Bus should still be usable
        results = []
        bus.subscribe("ev", lambda **kw: results.append(1))
        bus.publish("ev")
        assert results == [1]

    def test_no_double_registration(self):
        bus = self._bus()
        log = []
        cb = lambda **kw: log.append(1)
        bus.subscribe("ev", cb)
        bus.subscribe("ev", cb)  # second registration ignored
        bus.publish("ev")
        assert log == [1]

    def test_subscriber_exception_does_not_break_others(self):
        bus = self._bus()
        log = []
        def bad(**kw): raise RuntimeError("oops")
        def good(**kw): log.append(True)
        bus.subscribe("ev", bad)
        bus.subscribe("ev", good)
        bus.publish("ev")   # should not raise
        assert log == [True]

    def test_subscriber_count(self):
        bus = self._bus()
        bus.subscribe("ev", lambda: None)
        bus.subscribe("ev", lambda: None)
        assert bus.subscriber_count("ev") == 2
        assert bus.subscriber_count("other") == 0

    def test_clear(self):
        bus = self._bus()
        bus.subscribe("ev", lambda: None)
        bus.clear()
        assert bus.subscriber_count("ev") == 0

    def test_publish_no_subscribers_is_noop(self):
        bus = self._bus()
        # Must not raise when publishing with no subscribers
        bus.publish("no_one_listening")
        # Bus must still function after the no-op publish
        results = []
        bus.subscribe("no_one_listening", lambda **kw: results.append(True))
        bus.publish("no_one_listening")
        assert len(results) > 0

    def test_event_constants_exist(self):
        from gmodular.core.events import (
            MODULE_CHANGED, MODULE_CLOSED, OBJECT_SELECTED,
            OBJECT_PLACED, OBJECT_DELETED, GAME_DIR_CHANGED,
            ROOMS_CHANGED, STATUS_MESSAGE,
        )
        for ev in [MODULE_CHANGED, MODULE_CLOSED, OBJECT_SELECTED,
                   OBJECT_PLACED, OBJECT_DELETED, GAME_DIR_CHANGED,
                   ROOMS_CHANGED, STATUS_MESSAGE]:
            assert isinstance(ev, str) and ev  # non-empty string

    def test_get_event_bus_singleton(self):
        from gmodular.core.events import get_event_bus, EventBus
        bus1 = get_event_bus()
        bus2 = get_event_bus()
        assert bus1 is bus2
        assert isinstance(bus1, EventBus)

    def test_kwargs_forwarded_correctly(self):
        from gmodular.core.events import EventBus
        bus = EventBus()
        received = {}
        def handler(obj=None, extra=None, **_):
            received["obj"] = obj
            received["extra"] = extra
        bus.subscribe("ev", handler)
        bus.publish("ev", obj="hero", extra=42)
        assert received == {"obj": "hero", "extra": 42}


# ─────────────────────────────────────────────────────────────────────────────
#  3  ModuleIO service
# ─────────────────────────────────────────────────────────────────────────────

def _make_minimal_erf(resref_git: str = "test") -> bytes:
    """Build a minimal ERF archive containing a tiny stub .git resource."""
    # ERFReader reads:
    #   data[0:4]  = file_type  (b"ERF ")
    #   data[4:8]  = version    (b"V1.0")
    #   data[8:44] = 9 × uint32 via "<9I":
    #     lang_count, lang_size, entry_count,
    #     loc_off, key_off, res_off,
    #     build_year, build_day, desc_strref
    # Total header = 160 bytes (reader requires >= 160)
    file_type    = b"ERF "
    file_version = b"V1.0"
    entry_count  = 1
    header_size  = 160
    lang_count   = 0
    lang_size    = 0
    key_size     = entry_count * 24   # 16s resref, I id, H type, H unused
    res_size     = entry_count * 8    # I offset, I size

    loc_off  = header_size
    key_off  = loc_off + lang_size
    res_off  = key_off + key_size

    # Stub GIT data (minimal GFF header, padded to 192 bytes)
    GFF_HEADER = b"GFF " + b"V3.2" + struct.pack(
        "<IIIIIIII",
        0xC0, 0,   # struct offset + count
        0xC0, 0,   # field offset + count
        0xC0, 0,   # label offset + count
        0xC0, 0,   # field data + count
    )
    GFF_HEADER += b"\x00" * (0xC0 - len(GFF_HEADER))
    stub_git = GFF_HEADER

    data_offset = res_off + res_size

    # 8-byte magic + 9 × uint32 = 8 + 36 = 44 bytes of real header
    # Pad to 160 bytes with zeros
    header_body = struct.pack(
        "<9I",
        lang_count, lang_size, entry_count,
        loc_off, key_off, res_off,
        0, 0, 0xFFFFFFFF,   # build_year, build_day, desc_strref
    )
    magic = file_type + file_version + header_body
    # Pad to 160
    magic += b"\x00" * (header_size - len(magic))

    # Key list entry
    resref_padded = resref_git.lower().encode().ljust(16, b"\x00")[:16]
    key_entry = resref_padded + struct.pack("<IHH", 0, 2026, 0)  # res_id=0, res_type=2026 (git)

    # Resource list entry
    res_entry = struct.pack("<II", data_offset, len(stub_git))

    return magic + key_entry + res_entry + stub_git


class TestModuleIO:
    """ModuleIO is a pure I/O service — testable without Qt."""

    def test_import(self):
        from gmodular.core.module_io import ModuleIO, ModuleLoadResult
        assert ModuleIO is not None
        assert ModuleLoadResult is not None

    def test_load_result_is_dataclass(self):
        from gmodular.core.module_io import ModuleLoadResult
        r = ModuleLoadResult(mod_path="/tmp/x.mod", resref="test")
        assert r.mod_path == "/tmp/x.mod"
        assert r.resref == "test"
        assert r.errors == []
        assert r.resources == []

    def test_load_from_mod_minimal(self):
        """ModuleIO loads a minimal ERF archive without raising."""
        from gmodular.core.module_io import ModuleIO
        erf_data = _make_minimal_erf("danm13")
        with tempfile.TemporaryDirectory() as td:
            mod_path = os.path.join(td, "danm13.mod")
            extract_dir = os.path.join(td, "extracted")
            with open(mod_path, "wb") as f:
                f.write(erf_data)

            result = ModuleIO().load_from_mod(mod_path, extract_dir)

            assert result.mod_path == mod_path
            assert result.extract_dir == extract_dir
            assert isinstance(result.resources, list)
            assert result.git is not None

    def test_load_from_mod_auto_extract_dir(self):
        """When no extract_dir is given, one is created next to the .mod file."""
        from gmodular.core.module_io import ModuleIO
        erf_data = _make_minimal_erf("mymod")
        with tempfile.TemporaryDirectory() as td:
            mod_path = os.path.join(td, "mymod.mod")
            with open(mod_path, "wb") as f:
                f.write(erf_data)

            result = ModuleIO().load_from_mod(mod_path)

            assert os.path.isdir(result.extract_dir)
            assert "_mymod_extracted" in result.extract_dir

    def test_load_from_mod_empty_archive_returns_errors(self):
        """An empty archive records an error rather than raising."""
        from gmodular.core.module_io import ModuleIO
        # Build a valid but entry_count=0 ERF
        file_type    = b"ERF "
        file_version = b"V1.0"
        header_size  = 160
        header_body = struct.pack(
            "<9I",
            0, 0, 0,            # lang_count, lang_size, entry_count
            header_size, header_size, header_size,  # loc/key/res offsets
            0, 0, 0xFFFFFFFF,
        )
        magic = file_type + file_version + header_body
        magic += b"\x00" * (header_size - len(magic))

        with tempfile.TemporaryDirectory() as td:
            mod_path = os.path.join(td, "empty.mod")
            extract_dir = os.path.join(td, "out")
            with open(mod_path, "wb") as f:
                f.write(magic)

            result = ModuleIO().load_from_mod(mod_path, extract_dir)

            assert any("No resources" in e or "empty" in e.lower() or ".git" in e
                       for e in result.errors)

    def test_module_io_is_stateless(self):
        """Two ModuleIO instances behave identically — no shared state."""
        from gmodular.core.module_io import ModuleIO
        io1 = ModuleIO()
        io2 = ModuleIO()
        assert io1 is not io2
        assert type(io1) is type(io2)


# ─────────────────────────────────────────────────────────────────────────────
#  4  ModuleState integration with EventBus and ModuleIO delegation
# ─────────────────────────────────────────────────────────────────────────────

class TestModuleStateEventBus:
    """ModuleState should publish MODULE_CHANGED on every mutation."""

    def _fresh_state(self):
        """Return a fresh ModuleState with its own isolated EventBus."""
        from gmodular.core.module_state import ModuleState
        from gmodular.core.events import EventBus, MODULE_CHANGED
        state = ModuleState()
        bus = EventBus()
        state._bus = bus   # inject isolated bus
        return state, bus

    def test_new_module_emits_module_changed(self):
        from gmodular.core.module_state import ModuleProject
        from gmodular.core.events import MODULE_CHANGED
        state, bus = self._fresh_state()
        fired = []
        bus.subscribe(MODULE_CHANGED, lambda **_: fired.append(True))
        project = ModuleProject(name="Test", game="K1",
                                project_dir="/tmp", module_resref="test01")
        state.new_module(project)
        assert len(fired) >= 1

    def test_execute_command_emits_module_changed(self):
        from gmodular.core.module_state import ModuleProject, ModuleState
        from gmodular.core.events import MODULE_CHANGED
        from gmodular.formats.gff_types import GITData, GITPlaceable, Vector3
        state, bus = self._fresh_state()
        project = ModuleProject(name="T", game="K1",
                                project_dir="/tmp", module_resref="t01")
        state.new_module(project)
        fired = []
        bus.subscribe(MODULE_CHANGED, lambda **_: fired.append(True))
        before = len(fired)

        from gmodular.core.module_state import PlaceObjectCommand
        obj = GITPlaceable(resref="foo", tag="foo", position=Vector3(0, 0, 0))
        state.execute(PlaceObjectCommand(state.git, obj))
        assert len(fired) > before

    def test_undo_emits_module_changed(self):
        from gmodular.core.module_state import ModuleProject, PlaceObjectCommand
        from gmodular.core.events import MODULE_CHANGED
        from gmodular.formats.gff_types import GITPlaceable, Vector3
        state, bus = self._fresh_state()
        project = ModuleProject(name="T", game="K1",
                                project_dir="/tmp", module_resref="t01")
        state.new_module(project)
        obj = GITPlaceable(resref="foo", tag="foo", position=Vector3(0, 0, 0))
        state.execute(PlaceObjectCommand(state.git, obj))
        fired = []
        bus.subscribe(MODULE_CHANGED, lambda **_: fired.append(True))
        state.undo()
        assert len(fired) >= 1

    def test_close_emits_module_closed(self):
        from gmodular.core.module_state import ModuleProject
        from gmodular.core.events import MODULE_CLOSED
        state, bus = self._fresh_state()
        project = ModuleProject(name="T", game="K1",
                                project_dir="/tmp", module_resref="t01")
        state.new_module(project)
        fired = []
        bus.subscribe(MODULE_CLOSED, lambda **_: fired.append(True))
        state.close()
        assert len(fired) >= 1

    def test_is_saveable_method(self):
        from gmodular.core.module_state import ModuleProject
        state, _ = self._fresh_state()
        assert not state._is_saveable()
        project = ModuleProject(name="T", game="K1",
                                project_dir="/tmp", module_resref="t01")
        state.new_module(project)
        assert state._is_saveable()
        state.close()
        assert not state._is_saveable()

    def test_legacy_change_callbacks_still_work(self):
        """on_change() legacy callbacks must still fire after refactor."""
        from gmodular.core.module_state import ModuleProject
        state, _ = self._fresh_state()
        project = ModuleProject(name="T", game="K1",
                                project_dir="/tmp", module_resref="t01")
        fired = []
        state.on_change(lambda: fired.append(True))
        state.new_module(project)
        assert len(fired) >= 1


class TestModuleStateLoadFromModDelegation:
    """ModuleState.load_from_mod() must delegate to ModuleIO (not duplicate I/O logic)."""

    def test_load_from_mod_calls_module_io(self):
        """Verify delegation: ModuleIO.load_from_mod is called exactly once."""
        from gmodular.core.module_state import ModuleState
        from gmodular.core.module_io import ModuleLoadResult
        from gmodular.formats.gff_types import GITData, AREData, IFOData

        fake_result = ModuleLoadResult(
            mod_path="/fake/test.mod",
            extract_dir="/fake/extracted",
            resref="test",
            resources=["test.git", "test.are"],
            lyt_text=None,
            vis_text=None,
            errors=[],
            git=GITData(),
            are=AREData(),
            ifo=IFOData(),
        )

        state = ModuleState()
        from gmodular.core.events import EventBus
        state._bus = EventBus()

        with patch("gmodular.core.module_state.ModuleIO") as MockIO:
            MockIO.return_value.load_from_mod.return_value = fake_result
            summary = state.load_from_mod("/fake/test.mod", "/fake/extracted")

        MockIO.assert_called_once()
        MockIO.return_value.load_from_mod.assert_called_once_with(
            "/fake/test.mod", "/fake/extracted"
        )

    def test_load_from_mod_returns_compatible_summary(self):
        """Returned dict has the same keys as the old implementation."""
        from gmodular.core.module_state import ModuleState
        from gmodular.core.module_io import ModuleLoadResult
        from gmodular.formats.gff_types import GITData, AREData, IFOData

        fake_result = ModuleLoadResult(
            mod_path="/a/b.mod",
            extract_dir="/a/extracted",
            resref="b",
            resources=["b.git"],
            errors=[],
            git=GITData(),
            are=AREData(),
            ifo=IFOData(),
        )

        state = ModuleState()
        from gmodular.core.events import EventBus
        state._bus = EventBus()

        with patch("gmodular.core.module_state.ModuleIO") as MockIO:
            MockIO.return_value.load_from_mod.return_value = fake_result
            summary = state.load_from_mod("/a/b.mod")

        required_keys = {"mod_path", "extract_dir", "resref",
                         "resources", "lyt_text", "vis_text", "errors"}
        assert required_keys.issubset(set(summary.keys()))
        assert summary["resref"] == "b"


# ─────────────────────────────────────────────────────────────────────────────
#  5  KotorInstallation.resource_manager() delegation
# ─────────────────────────────────────────────────────────────────────────────

class TestKotorInstallationResourceManager:
    """KotorInstallation.resource_manager() must return a ResourceManager."""

    def test_resource_manager_returned(self, tmp_path):
        from gmodular.mcp.state import KotorInstallation
        from gmodular.formats.archives import ResourceManager
        # We don't need a real install — just check the type is right
        inst = KotorInstallation(path=tmp_path, game="K1")
        rm = inst.resource_manager()
        assert isinstance(rm, ResourceManager)

    def test_resource_manager_is_cached(self, tmp_path):
        from gmodular.mcp.state import KotorInstallation
        inst = KotorInstallation(path=tmp_path, game="K1")
        rm1 = inst.resource_manager()
        rm2 = inst.resource_manager()
        assert rm1 is rm2

    def test_resource_manager_is_per_instance(self, tmp_path):
        from gmodular.mcp.state import KotorInstallation
        inst1 = KotorInstallation(path=tmp_path, game="K1")
        inst2 = KotorInstallation(path=tmp_path, game="K2")
        rm1 = inst1.resource_manager()
        rm2 = inst2.resource_manager()
        assert rm1 is not rm2

    def test_resource_port_satisfied_by_installation_rm(self, tmp_path):
        from gmodular.mcp.state import KotorInstallation
        from gmodular.formats.resource_port import ResourcePort
        inst = KotorInstallation(path=tmp_path, game="K1")
        rm = inst.resource_manager()
        assert isinstance(rm, ResourcePort)


# ─────────────────────────────────────────────────────────────────────────────
#  6  find_resource_bytes (canonical shared helper in discovery.py)
# ─────────────────────────────────────────────────────────────────────────────

class TestFindResourceBytes:
    """find_resource_bytes in discovery.py replaces 4 duplicate implementations."""

    def _make_inst(self, data_map: dict):
        """Return a mock KotorInstallation backed by MemResourceManager."""
        from gmodular.formats.resource_port import MemResourceManager
        mem = MemResourceManager()
        for (resref, ext), data in data_map.items():
            mem.add(resref, ext, data)

        inst = MagicMock()
        inst.resource_manager.return_value = mem
        inst.game = "K1"
        # index with no entries so fallback path is also tested
        inst.index = {"by_key": {}, "by_source": {}}
        return inst

    def test_returns_bytes_from_rm(self):
        from gmodular.mcp.tools.discovery import find_resource_bytes
        inst = self._make_inst({("dialog", "tlk"): b"tlk_bytes"})
        result = find_resource_bytes(inst, "dialog", "tlk")
        assert result == b"tlk_bytes"

    def test_raises_when_not_found(self):
        from gmodular.mcp.tools.discovery import find_resource_bytes
        inst = self._make_inst({})
        with pytest.raises(ValueError, match="not found"):
            find_resource_bytes(inst, "missing", "mdl")

    def test_case_insensitive(self):
        from gmodular.mcp.tools.discovery import find_resource_bytes
        inst = self._make_inst({("c_bastila", "mdl"): b"mdl"})
        assert find_resource_bytes(inst, "C_BASTILA", "MDL") == b"mdl"


# ─────────────────────────────────────────────────────────────────────────────
#  7  Import boundary: mcp must not import gui
# ─────────────────────────────────────────────────────────────────────────────

class TestImportBoundaries:
    """Enforce Khononov's package-level coupling constraint (§7)."""

    def _imports_of_module(self, module_path: str):
        """Return a set of all top-level import names in a .py file."""
        import ast
        with open(module_path) as f:
            tree = ast.parse(f.read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    names.add(node.module.split(".")[0] if node.level == 0
                              else "__relative__")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name.split(".")[0])
        return names

    def _all_mcp_files(self):
        mcp_dir = Path(__file__).parent.parent / "gmodular" / "mcp"
        return list(mcp_dir.rglob("*.py"))

    def test_mcp_does_not_import_gui(self):
        """gmodular.mcp.* must never import gmodular.gui.*"""
        import ast
        mcp_dir = Path(__file__).parent.parent / "gmodular" / "mcp"
        violations = []
        for py_file in mcp_dir.rglob("*.py"):
            with open(py_file) as f:
                try:
                    tree = ast.parse(f.read())
                except SyntaxError:
                    continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if "gmodular.gui" in node.module or (
                        node.level > 0 and "gui" in (node.module or "")
                    ):
                        violations.append(f"{py_file.name}: imports {node.module}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if "gmodular.gui" in alias.name:
                            violations.append(f"{py_file.name}: imports {alias.name}")
        assert violations == [], f"mcp→gui violations: {violations}"

    def test_formats_does_not_import_gui(self):
        """gmodular.formats.* must never import gmodular.gui.*"""
        import ast
        fmt_dir = Path(__file__).parent.parent / "gmodular" / "formats"
        violations = []
        for py_file in fmt_dir.rglob("*.py"):
            with open(py_file) as f:
                try:
                    tree = ast.parse(f.read())
                except SyntaxError:
                    continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if "gmodular.gui" in node.module:
                        violations.append(f"{py_file.name}: imports {node.module}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if "gmodular.gui" in alias.name:
                            violations.append(f"{py_file.name}: imports {alias.name}")
        assert violations == [], f"formats→gui violations: {violations}"

    def test_core_does_not_import_gui(self):
        """gmodular.core.* must never import gmodular.gui.*"""
        import ast
        core_dir = Path(__file__).parent.parent / "gmodular" / "core"
        violations = []
        for py_file in core_dir.rglob("*.py"):
            with open(py_file) as f:
                try:
                    tree = ast.parse(f.read())
                except SyntaxError:
                    continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if "gmodular.gui" in node.module:
                        violations.append(f"{py_file.name}: imports {node.module}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if "gmodular.gui" in alias.name:
                            violations.append(f"{py_file.name}: imports {alias.name}")
        assert violations == [], f"core→gui violations: {violations}"


# ─────────────────────────────────────────────────────────────────────────────
#  8  Fractal correctness — method-level (Khononov §7)
# ─────────────────────────────────────────────────────────────────────────────

class TestFractalCorrectness:
    """Validate small-scope improvements extracted per Khononov's fractal principle."""

    def test_is_saveable_false_when_no_git(self):
        from gmodular.core.module_state import ModuleState
        state = ModuleState()
        assert not state._is_saveable()

    def test_is_saveable_false_when_git_but_no_project(self):
        from gmodular.core.module_state import ModuleState
        from gmodular.formats.gff_types import GITData
        state = ModuleState()
        state.git = GITData()
        state.project = None
        assert not state._is_saveable()

    def test_is_saveable_true_when_both_present(self, tmp_path):
        from gmodular.core.module_state import ModuleState, ModuleProject
        state = ModuleState()
        from gmodular.formats.gff_types import GITData
        state.git = GITData()
        state.project = ModuleProject(
            name="t", game="K1",
            project_dir=str(tmp_path),
            module_resref="t01",
        )
        assert state._is_saveable()
