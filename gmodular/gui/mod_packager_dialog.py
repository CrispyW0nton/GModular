"""
GModular — Module Packager Dialog (P6)
GUI wrapper around ModPackager.
Shows dependency list, validation results, and handles the Pack button.
"""
from __future__ import annotations
import logging
from pathlib import Path
from typing import Optional, List

from qtpy.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QProgressBar, QFileDialog,
    QLineEdit, QGroupBox, QFormLayout, QSplitter, QTextEdit,
    QMessageBox, QFrame, QCheckBox, QWidget,
)
from qtpy.QtCore import Qt, QThread, Signal
from qtpy.QtGui import QFont, QColor, QIcon

from ..formats.mod_packager import ModPackager, PackagerResult, ValidationIssue, ERROR, WARNING, INFO

log = logging.getLogger(__name__)

# ── Severity colors ──────────────────────────────────────────────────────

SEV_COLOR = {
    ERROR:   "#ff6060",
    WARNING: "#ffcc44",
    INFO:    "#7ec8e3",
}


# ── Background worker ────────────────────────────────────────────────────

class _PackWorker(QThread):
    finished = Signal(object)   # PackagerResult

    def __init__(self, packager: ModPackager, output_path: str):
        super().__init__()
        self._packager = packager
        self._output = output_path

    def run(self):
        result = self._packager.build(self._output)
        self.finished.emit(result)


# ── Validation Report Panel (P10) ────────────────────────────────────────

class ValidationReportPanel(QWidget if True else object):
    """
    Standalone scrollable panel showing validation results.
    Used both inside PackagerDialog and as a standalone Module > Validate panel.
    """

    # avoid importing QWidget twice
    pass


from qtpy.QtWidgets import QWidget


class ValidationReportPanel(QWidget):
    """Scrollable list of validation issues with severity icons."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Severity", "Message"])
        self._tree.setColumnWidth(0, 80)
        self._tree.setAlternatingRowColors(True)
        self._tree.setStyleSheet(
            "QTreeWidget { background:#1e1e1e; color:#d4d4d4; "
            "border:1px solid #3c3c3c; font-family:Consolas; font-size:8pt; }"
            "QTreeWidget::item:alternate { background:#252525; }"
            "QHeaderView::section { background:#2d2d2d; color:#9cdcfe; "
            "border:none; padding:3px; }"
        )
        layout.addWidget(self._tree)

        self._summary = QLabel("No results yet")
        self._summary.setStyleSheet("color:#969696; font-size:8pt; padding:2px;")
        layout.addWidget(self._summary)

    def set_issues(self, issues: List[ValidationIssue]):
        self._tree.clear()
        errors = warnings = infos = 0
        for issue in issues:
            item = QTreeWidgetItem([issue.severity.capitalize(), issue.message])
            color = SEV_COLOR.get(issue.severity, "#d4d4d4")
            item.setForeground(0, QColor(color))
            item.setForeground(1, QColor(color))
            self._tree.addTopLevelItem(item)
            if issue.severity == ERROR:
                errors += 1
            elif issue.severity == WARNING:
                warnings += 1
            else:
                infos += 1

        parts = []
        if errors:
            parts.append(f"{errors} error(s)")
        if warnings:
            parts.append(f"{warnings} warning(s)")
        if infos:
            parts.append(f"{infos} note(s)")
        self._summary.setText(", ".join(parts) if parts else "No issues found  ✓")
        color = "#ff6060" if errors else "#ffcc44" if warnings else "#44ff44"
        self._summary.setStyleSheet(f"color:{color}; font-size:8pt; padding:2px;")

    def clear(self):
        self._tree.clear()
        self._summary.setText("No results yet")
        self._summary.setStyleSheet("color:#969696; font-size:8pt; padding:2px;")


# ── Resource List Widget ──────────────────────────────────────────────────

class ResourceListWidget(QTreeWidget):
    """Shows the list of resources that will be packed."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(["ResRef", "Type", "Size", "Source"])
        self.setColumnWidth(0, 140)
        self.setColumnWidth(1, 50)
        self.setColumnWidth(2, 70)
        self.setAlternatingRowColors(True)
        self.setStyleSheet(
            "QTreeWidget { background:#1e1e1e; color:#d4d4d4; "
            "border:1px solid #3c3c3c; font-family:Consolas; font-size:8pt; }"
            "QTreeWidget::item:alternate { background:#252525; }"
            "QHeaderView::section { background:#2d2d2d; color:#9cdcfe; "
            "border:none; padding:3px; }"
        )

    def load_resources(self, resources):
        self.clear()
        for res in resources:
            size_str = f"{len(res.data):,} B"
            item = QTreeWidgetItem([res.resref, res.ext.upper(), size_str, res.source_path])
            item.setForeground(0, QColor("#9cdcfe"))
            self.addTopLevelItem(item)


# ── Packager Dialog ───────────────────────────────────────────────────────

class ModPackagerDialog(QDialog):
    """
    Full Module Packager dialog.
    Shows:
      - Module info (name, object counts)
      - Output path picker
      - Validate button → shows ValidationReportPanel
      - Resource list (dependency walker result)
      - Pack button → builds .mod
      - Progress bar + result message
    """

    pack_complete = Signal(str)   # output path

    def __init__(self, parent=None,
                 module_name: str = "",
                 module_dir: str = "",
                 git=None, are=None, ifo=None,
                 game_dir: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Module Packager — Build .MOD")
        self.setMinimumSize(800, 600)
        self._module_name = module_name
        self._module_dir  = module_dir
        self._git  = git
        self._are  = are
        self._ifo  = ifo
        self._game_dir = game_dir
        self._worker: Optional[_PackWorker] = None

        self._setup_ui()
        self._apply_theme()
        self._refresh_output_path()

    # ── UI ────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # Header
        hdr = QLabel(f"Pack Module:  <b>{self._module_name or '(unnamed)'}</b>")
        hdr.setStyleSheet("color:#9cdcfe; font-size:11pt; padding:4px;")
        root.addWidget(hdr)

        # Output path row
        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Output:"))
        self._output_edit = QLineEdit()
        self._output_edit.setFont(QFont("Consolas", 9))
        self._output_edit.setPlaceholderText("Path to output .mod file…")
        path_row.addWidget(self._output_edit, 1)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_output)
        path_row.addWidget(browse_btn)
        root.addLayout(path_row)

        # Splitter: left=validation, right=resources
        splitter = QSplitter(Qt.Vertical)

        # Validation panel
        val_grp = QGroupBox("Validation")
        val_grp.setStyleSheet("QGroupBox { color:#dcdcaa; font-weight:bold; }")
        val_layout = QVBoxLayout(val_grp)
        self._val_panel = ValidationReportPanel()
        val_layout.addWidget(self._val_panel)
        splitter.addWidget(val_grp)

        # Resource list
        res_grp = QGroupBox("Resources to Pack")
        res_grp.setStyleSheet("QGroupBox { color:#dcdcaa; font-weight:bold; }")
        res_layout = QVBoxLayout(res_grp)
        self._res_list = ResourceListWidget()
        res_layout.addWidget(self._res_list)
        self._res_count = QLabel("0 resources")
        self._res_count.setStyleSheet("color:#969696; font-size:8pt;")
        res_layout.addWidget(self._res_count)
        splitter.addWidget(res_grp)

        splitter.setSizes([250, 250])
        root.addWidget(splitter, 1)

        # Progress bar
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.hide()
        root.addWidget(self._progress)

        # Status label
        self._status = QLabel("")
        self._status.setStyleSheet("color:#7ec8e3; font-size:8pt;")
        root.addWidget(self._status)

        # Button row
        btn_row = QHBoxLayout()
        self._validate_btn = QPushButton("Validate Module")
        self._validate_btn.clicked.connect(self._run_validate)
        self._validate_btn.setToolTip("Check for errors before packing")

        self._preview_btn = QPushButton("Preview Dependencies")
        self._preview_btn.clicked.connect(self._run_preview)
        self._preview_btn.setToolTip("Walk dependencies without building the .mod")

        self._pack_btn = QPushButton("Pack  →  .MOD")
        self._pack_btn.clicked.connect(self._run_pack)
        self._pack_btn.setStyleSheet(
            "QPushButton { background:#1a5a1a; color:white; border:1px solid #2aaa2a; "
            "border-radius:3px; padding:4px 16px; font-weight:bold; }"
            "QPushButton:hover { background:#2a7a2a; }"
            "QPushButton:disabled { background:#2a2a2a; color:#555; }"
        )

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)

        btn_row.addWidget(self._validate_btn)
        btn_row.addWidget(self._preview_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._pack_btn)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    def _apply_theme(self):
        self.setStyleSheet(
            "QDialog { background:#1e1e1e; color:#d4d4d4; }"
            "QLabel { color:#d4d4d4; font-size:9pt; }"
            "QLineEdit { background:#2d2d2d; color:#d4d4d4; border:1px solid #3c3c3c; "
            "            padding:3px; border-radius:2px; }"
            "QPushButton { background:#2d2d2d; color:#d4d4d4; border:1px solid #3c3c3c; "
            "              border-radius:3px; padding:4px 10px; }"
            "QPushButton:hover { background:#3c3c3c; }"
            "QGroupBox { color:#dcdcaa; font-weight:bold; border:1px solid #3c3c3c; "
            "            margin-top:8px; padding-top:8px; border-radius:3px; }"
            "QGroupBox::title { subcontrol-origin:margin; padding:0 4px; }"
            "QProgressBar { background:#2d2d2d; border:1px solid #3c3c3c; "
            "               border-radius:2px; height:12px; }"
            "QProgressBar::chunk { background:#2a7a2a; }"
        )

    def _refresh_output_path(self):
        if self._module_dir and self._module_name:
            default = str(Path(self._module_dir) / f"{self._module_name}.mod")
            self._output_edit.setText(default)

    def _browse_output(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Module As", self._output_edit.text(),
            "KotOR Module (*.mod);;All Files (*)")
        if path:
            if not path.lower().endswith(".mod"):
                path += ".mod"
            self._output_edit.setText(path)

    def _make_packager(self) -> ModPackager:
        return ModPackager(
            module_dir=self._module_dir,
            module_name=self._module_name,
            git=self._git,
            are=self._are,
            ifo=self._ifo,
            game_dir=self._game_dir or None,
        )

    def _run_validate(self):
        self._val_panel.clear()
        self._status.setText("Validating…")
        packager = self._make_packager()
        issues = packager.validate_only()
        self._val_panel.set_issues(issues)
        n_err = sum(1 for i in issues if i.severity == ERROR)
        n_warn = sum(1 for i in issues if i.severity == WARNING)
        if n_err:
            self._status.setText(f"Validation: {n_err} error(s), {n_warn} warning(s). Fix errors before packing.")
            self._status.setStyleSheet("color:#ff6060; font-size:8pt;")
        elif n_warn:
            self._status.setText(f"Validation: {n_warn} warning(s). Packing is allowed.")
            self._status.setStyleSheet("color:#ffcc44; font-size:8pt;")
        else:
            self._status.setText("Validation passed — ready to pack.")
            self._status.setStyleSheet("color:#44ff44; font-size:8pt;")

    def _run_preview(self):
        """Walk dependencies and show resource list without building."""
        from ..formats.mod_packager import _get_all_resrefs, PackageResource, EXT_TO_TYPE
        self._val_panel.clear()
        self._res_list.clear()

        packager = self._make_packager()
        issues = packager.validate_only()
        self._val_panel.set_issues(issues)

        # Build resource preview
        resources = packager._collect_resources()  # noqa: private but acceptable in dialog
        self._res_list.load_resources(resources)
        total_bytes = sum(len(r.data) for r in resources)
        self._res_count.setText(
            f"{len(resources)} resources  ({total_bytes:,} bytes)")
        self._status.setText(f"Preview: {len(resources)} resources found.")
        self._status.setStyleSheet("color:#7ec8e3; font-size:8pt;")

    def _run_pack(self):
        output = self._output_edit.text().strip()
        if not output:
            QMessageBox.warning(self, "No Output", "Choose an output .mod path first.")
            return

        # First validate
        packager = self._make_packager()
        issues = packager.validate_only()
        self._val_panel.set_issues(issues)
        n_err = sum(1 for i in issues if i.severity == ERROR)
        if n_err:
            QMessageBox.critical(
                self, "Validation Errors",
                f"{n_err} error(s) found. Fix them before packing.\n"
                "See the Validation panel for details.")
            return

        # Start worker
        self._pack_btn.setEnabled(False)
        self._progress.show()
        self._status.setText("Building .mod archive…")
        self._status.setStyleSheet("color:#7ec8e3; font-size:8pt;")

        self._worker = _PackWorker(packager, output)
        self._worker.finished.connect(self._on_pack_done)
        self._worker.start()

    def _on_pack_done(self, result: PackagerResult):
        self._progress.hide()
        self._pack_btn.setEnabled(True)
        self._val_panel.set_issues(result.issues)
        self._res_list.load_resources(result.resource_list)
        self._res_count.setText(
            f"{result.resources_packed} resources  "
            f"({result.file_size_bytes:,} bytes)" if result.success else "Build failed")

        if result.success:
            size_mb = result.file_size_bytes / (1024 * 1024)
            self._status.setText(
                f"Done! {result.resources_packed} resources → {Path(result.output_path).name} "
                f"({size_mb:.2f} MB)")
            self._status.setStyleSheet("color:#44ff44; font-size:8pt;")
            self.pack_complete.emit(result.output_path)
            QMessageBox.information(
                self, "Pack Complete",
                f"Module packed successfully!\n\n"
                f"Output: {result.output_path}\n"
                f"Resources: {result.resources_packed}\n"
                f"Size: {size_mb:.2f} MB\n\n"
                f"Drop this file into your game's Modules/ folder.")
        else:
            self._status.setText(f"Build failed — see Validation panel for errors.")
            self._status.setStyleSheet("color:#ff6060; font-size:8pt;")

    def update_module_data(self, git=None, are=None, ifo=None):
        """Called from MainWindow when module data changes."""
        if git is not None:
            self._git = git
        if are is not None:
            self._are = are
        if ifo is not None:
            self._ifo = ifo
