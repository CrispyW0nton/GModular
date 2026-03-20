"""
Tests for Qt Designer .ui file infrastructure and main.py qtpy migration.

Covers:
  - ui_loader utility (list_ui_files, ui_file, load_ui, load_ui_type)
  - .ui file XML validity
  - main.py uses qtpy instead of raw PyQt5
  - GModular.spec includes ui/ directory
  - gui/__init__.py updated docstring
  - ghidra_bridge.py docstring updated
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

# Repo root
ROOT = Path(__file__).parent.parent
UI_DIR = ROOT / "gmodular" / "gui" / "ui"


# ─────────────────────────────────────────────────────────────────────────────
#  ui_loader utility tests
# ─────────────────────────────────────────────────────────────────────────────

class TestUILoader(unittest.TestCase):
    """Tests for gmodular.gui.ui_loader."""

    def test_import_succeeds(self):
        """ui_loader must import without errors."""
        from gmodular.gui import ui_loader  # noqa: F401

    def test_list_ui_files_returns_list(self):
        """list_ui_files() must return a list."""
        from gmodular.gui.ui_loader import list_ui_files
        result = list_ui_files()
        self.assertIsInstance(result, list)

    def test_expected_ui_files_present(self):
        """All four .ui files must be discoverable."""
        from gmodular.gui.ui_loader import list_ui_files
        files = list_ui_files()
        expected = {
            "inspector.ui",
            "twoda_editor.ui",
            "dlg_editor.ui",
            "mod_import_dialog.ui",
        }
        for f in expected:
            self.assertIn(f, files, f"Expected .ui file not found: {f}")

    def test_ui_file_returns_string_path(self):
        """ui_file() must return a string path."""
        from gmodular.gui.ui_loader import ui_file
        result = ui_file("inspector.ui")
        self.assertIsInstance(result, str)
        self.assertTrue(result.endswith("inspector.ui"))

    def test_ui_file_path_exists(self):
        """ui_file() must point to an existing file."""
        from gmodular.gui.ui_loader import ui_file
        for name in ["inspector.ui", "twoda_editor.ui", "dlg_editor.ui", "mod_import_dialog.ui"]:
            path = ui_file(name)
            self.assertTrue(Path(path).exists(), f".ui file missing on disk: {name}")

    def test_load_ui_returns_false_for_missing_file(self):
        """load_ui() must return False (not raise) for a non-existent .ui file."""
        from gmodular.gui.ui_loader import load_ui
        # Pass a dummy widget-like object; the function should fail gracefully
        result = load_ui("nonexistent_widget.ui", object())
        self.assertFalse(result)

    def test_load_ui_type_raises_for_missing(self):
        """load_ui_type() must raise FileNotFoundError for missing files."""
        from gmodular.gui.ui_loader import load_ui_type
        with self.assertRaises(FileNotFoundError):
            load_ui_type("does_not_exist.ui")

    def test_ui_dir_constant(self):
        """The _UI_DIR constant must point to an existing directory."""
        from gmodular.gui import ui_loader
        self.assertTrue(ui_loader._UI_DIR.exists())
        self.assertTrue(ui_loader._UI_DIR.is_dir())


# ─────────────────────────────────────────────────────────────────────────────
#  .ui file XML validity tests
# ─────────────────────────────────────────────────────────────────────────────

class TestUIFileXML(unittest.TestCase):
    """All .ui files must be valid XML with the correct Qt Designer root."""

    def _parse_ui(self, filename: str) -> ET.Element:
        path = UI_DIR / filename
        self.assertTrue(path.exists(), f"Missing .ui file: {filename}")
        tree = ET.parse(str(path))
        return tree.getroot()

    def test_inspector_ui_valid_xml(self):
        root = self._parse_ui("inspector.ui")
        self.assertEqual(root.tag, "ui")
        self.assertEqual(root.attrib.get("version"), "4.0")

    def test_twoda_editor_ui_valid_xml(self):
        root = self._parse_ui("twoda_editor.ui")
        self.assertEqual(root.tag, "ui")
        classes = [c.text for c in root.findall("class")]
        self.assertIn("TwoDAEditor", classes)

    def test_dlg_editor_ui_valid_xml(self):
        root = self._parse_ui("dlg_editor.ui")
        self.assertEqual(root.tag, "ui")
        classes = [c.text for c in root.findall("class")]
        self.assertIn("DLGEditor", classes)

    def test_mod_import_dialog_ui_valid_xml(self):
        root = self._parse_ui("mod_import_dialog.ui")
        self.assertEqual(root.tag, "ui")
        classes = [c.text for c in root.findall("class")]
        self.assertIn("ModImportDialog", classes)

    def test_all_ui_files_valid(self):
        """Every .ui file in the ui/ directory must parse as valid XML."""
        for ui_path in sorted(UI_DIR.glob("*.ui")):
            with self.subTest(file=ui_path.name):
                try:
                    tree = ET.parse(str(ui_path))
                    root = tree.getroot()
                    self.assertEqual(root.tag, "ui",
                        f"{ui_path.name}: root element should be <ui>, got <{root.tag}>")
                except ET.ParseError as e:
                    self.fail(f"{ui_path.name} is not valid XML: {e}")

    def test_inspector_has_scroll_area(self):
        """inspector.ui must contain a QScrollArea for dynamic content."""
        root = self._parse_ui("inspector.ui")
        scroll_areas = root.findall(".//widget[@class='QScrollArea']")
        self.assertGreater(len(scroll_areas), 0,
            "inspector.ui must contain at least one QScrollArea")

    def test_twoda_editor_has_table_view(self):
        """twoda_editor.ui must contain a QTableView."""
        root = self._parse_ui("twoda_editor.ui")
        tables = root.findall(".//widget[@class='QTableView']")
        self.assertGreater(len(tables), 0,
            "twoda_editor.ui must contain a QTableView")

    def test_twoda_editor_has_search_box(self):
        """twoda_editor.ui must have a searchBox QLineEdit."""
        root = self._parse_ui("twoda_editor.ui")
        search = root.findall(".//widget[@name='searchBox']")
        self.assertGreater(len(search), 0,
            "twoda_editor.ui must have a 'searchBox' widget")

    def test_dlg_editor_has_splitter(self):
        """dlg_editor.ui must use a QSplitter for the 3-panel layout."""
        root = self._parse_ui("dlg_editor.ui")
        splitters = root.findall(".//widget[@class='QSplitter']")
        self.assertGreater(len(splitters), 0,
            "dlg_editor.ui must contain a QSplitter")

    def test_dlg_editor_has_node_tree(self):
        """dlg_editor.ui must have a nodeTree QTreeView."""
        root = self._parse_ui("dlg_editor.ui")
        trees = root.findall(".//widget[@name='nodeTree']")
        self.assertGreater(len(trees), 0,
            "dlg_editor.ui must have a 'nodeTree' widget")

    def test_mod_import_dialog_has_button_box(self):
        """mod_import_dialog.ui must have a QDialogButtonBox with Ok/Cancel."""
        root = self._parse_ui("mod_import_dialog.ui")
        buttons = root.findall(".//widget[@class='QDialogButtonBox']")
        self.assertGreater(len(buttons), 0,
            "mod_import_dialog.ui must have a QDialogButtonBox")

    def test_mod_import_dialog_has_resource_list(self):
        """mod_import_dialog.ui must have a lstResources QListWidget."""
        root = self._parse_ui("mod_import_dialog.ui")
        lists = root.findall(".//widget[@name='lstResources']")
        self.assertGreater(len(lists), 0,
            "mod_import_dialog.ui must have a 'lstResources' widget")


# ─────────────────────────────────────────────────────────────────────────────
#  main.py qtpy migration tests
# ─────────────────────────────────────────────────────────────────────────────

class TestMainPyQtpyMigration(unittest.TestCase):
    """Verify main.py uses qtpy, not raw PyQt5 imports."""

    def _read_main(self) -> str:
        path = ROOT / "main.py"
        self.assertTrue(path.exists(), "main.py must exist at repo root")
        return path.read_text(encoding="utf-8")

    def test_main_imports_qtpy_not_pyqt5(self):
        """main.py must import from qtpy, not directly from PyQt5."""
        content = self._read_main()
        # Should use qtpy
        self.assertIn("from qtpy", content,
            "main.py must import Qt via qtpy")

    def test_main_no_bare_pyqt5_imports(self):
        """main.py must not contain bare 'from PyQt5' import statements."""
        content = self._read_main()
        import re
        bare_imports = re.findall(r"^\s*from PyQt5\b", content, re.MULTILINE)
        self.assertEqual(bare_imports, [],
            f"main.py has bare PyQt5 imports: {bare_imports}")

    def test_main_handles_qt6_highdpi(self):
        """main.py must handle Qt6 gracefully (AA_* attrs may not exist)."""
        content = self._read_main()
        # The Qt6-safe code must use try/except or hasattr around AA_ attributes
        self.assertTrue(
            "AttributeError" in content or "try:" in content,
            "main.py must guard Qt5-only attributes (e.g., AA_EnableHighDpiScaling) "
            "for Qt6 compatibility"
        )

    def test_main_no_hardcoded_v1_version(self):
        """main.py log message must say v2.0.0, not v1.0.0."""
        content = self._read_main()
        self.assertNotIn("v1.0.0", content,
            "main.py version string still says v1.0.0; should be v2.0.0")

    def test_main_install_hint_mentions_qtpy(self):
        """main.py error hint must mention qtpy, not just PyQt5."""
        content = self._read_main()
        self.assertIn("qtpy", content,
            "main.py install hint must mention qtpy")

    def test_main_has_qtpy_backend_comment(self):
        """main.py should document the QT_API environment variable."""
        content = self._read_main()
        self.assertIn("QT_API", content,
            "main.py should document the QT_API env var for backend selection")


# ─────────────────────────────────────────────────────────────────────────────
#  GModular.spec .ui bundling test
# ─────────────────────────────────────────────────────────────────────────────

class TestSpecUIBundling(unittest.TestCase):
    """GModular.spec must include the ui/ directory."""

    def _read_spec(self) -> str:
        path = ROOT / "GModular.spec"
        self.assertTrue(path.exists(), "GModular.spec must exist")
        return path.read_text(encoding="utf-8")

    def test_spec_references_ui_dir(self):
        """GModular.spec must bundle the gmodular/gui/ui/ directory."""
        content = self._read_spec()
        self.assertIn("gui/ui", content,
            "GModular.spec must add gmodular/gui/ui to datas")

    def test_spec_includes_ui_loader_hidden_import(self):
        """GModular.spec must list gmodular.gui.ui_loader as a hidden import."""
        content = self._read_spec()
        self.assertIn("gmodular.gui.ui_loader", content,
            "GModular.spec must include gmodular.gui.ui_loader in hidden_imports")

    def test_spec_includes_qtpy_uic(self):
        """GModular.spec must include qtpy.uic for .ui loading at runtime."""
        content = self._read_spec()
        self.assertIn("qtpy.uic", content,
            "GModular.spec must include qtpy.uic in hidden_imports")


# ─────────────────────────────────────────────────────────────────────────────
#  Docstring / comment hygiene
# ─────────────────────────────────────────────────────────────────────────────

class TestDocstringHygiene(unittest.TestCase):
    """Docstrings and module-level comments must not reference bare PyQt5."""

    def _read(self, rel_path: str) -> str:
        path = ROOT / rel_path
        self.assertTrue(path.exists(), f"File not found: {rel_path}")
        return path.read_text(encoding="utf-8")

    def test_gui_init_updated(self):
        """gmodular/gui/__init__.py must not say 'PyQt5 widgets'."""
        content = self._read("gmodular/gui/__init__.py")
        self.assertNotIn("PyQt5 widgets", content,
            "gui/__init__.py docstring still says 'PyQt5 widgets'")
        self.assertIn("qtpy", content,
            "gui/__init__.py docstring should mention qtpy")

    def test_ghidra_bridge_docstring_updated(self):
        """ghidra_bridge.py must not say 'If PyQt5 is available'."""
        content = self._read("gmodular/ipc/ghidra_bridge.py")
        self.assertNotIn("If PyQt5 is available", content,
            "ghidra_bridge.py still has old 'If PyQt5 is available' docstring")

    def test_tpc_reader_docstring_updated(self):
        """tpc_reader.py must not reference PyQt5 in its public docstring."""
        content = self._read("gmodular/formats/tpc_reader.py")
        # Should say qtpy, not PyQt5 in the to_qimage description
        self.assertNotIn("if PyQt5 available", content,
            "tpc_reader.py to_qimage docstring still says 'if PyQt5 available'")

    def test_ui_package_has_init(self):
        """gmodular/gui/ui/__init__.py must exist."""
        path = ROOT / "gmodular" / "gui" / "ui" / "__init__.py"
        self.assertTrue(path.exists(),
            "gmodular/gui/ui/__init__.py is missing")


if __name__ == "__main__":
    unittest.main()
