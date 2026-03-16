"""
GModular — .MOD / .RIM / .ERF Import Dialog
Lets the user browse an archive's contents and load it as the active module.
"""
from __future__ import annotations
import os
import logging
from pathlib import Path
from typing import Optional, List

log = logging.getLogger(__name__)

try:
    from PyQt5.QtWidgets import (
        QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
        QTreeWidget, QTreeWidgetItem, QSplitter, QTextEdit,
        QDialogButtonBox, QProgressBar, QFrame, QFileDialog,
        QMessageBox, QLineEdit, QGroupBox, QFormLayout,
        QAbstractItemView, QHeaderView, QWidget,
    )
    from PyQt5.QtCore import Qt, QThread, pyqtSignal, QSize
    from PyQt5.QtGui import QFont, QColor
    _HAS_QT = True
except ImportError:
    _HAS_QT = False

# ── Icon / colour helpers ──────────────────────────────────────────────────

_EXT_ICON: dict = {
    "git":  "🎮",
    "are":  "🗺",
    "ifo":  "📋",
    "lyt":  "📐",
    "vis":  "👁",
    "mdl":  "🧱",
    "wok":  "🚶",
    "nss":  "📝",
    "ncs":  "⚙",
    "dlg":  "💬",
    "utc":  "👤",
    "utp":  "📦",
    "utd":  "🚪",
    "uts":  "🔊",
    "utt":  "🔲",
    "2da":  "📊",
    "tga":  "🖼",
    "tpc":  "🖼",
    "dds":  "🖼",
}

_CORE_EXTS = {"git", "are", "ifo", "lyt", "vis"}
_MODEL_EXTS = {"mdl", "wok", "mdx"}
_SCRIPT_EXTS = {"nss", "ncs"}
_DIALOG_EXTS = {"dlg"}
_BLUEPRINT_EXTS = {"utc", "utp", "utd", "ute", "uts", "utt", "uti", "utw", "utm"}
_TEXTURE_EXTS = {"tga", "tpc", "dds", "bmp", "plt"}


def _ext_color(ext: str) -> Optional[str]:
    if ext in _CORE_EXTS:
        return "#4ec9b0"
    if ext in _MODEL_EXTS:
        return "#9cdcfe"
    if ext in _SCRIPT_EXTS:
        return "#dcdcaa"
    if ext in _DIALOG_EXTS:
        return "#ce9178"
    if ext in _BLUEPRINT_EXTS:
        return "#c586c0"
    if ext in _TEXTURE_EXTS:
        return "#569cd6"
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Archive summary helper (headless, no Qt required)
# ─────────────────────────────────────────────────────────────────────────────

def inspect_archive(path: str) -> dict:
    """
    Return a summary dict of an ERF/MOD/RIM archive:

    {
        "path":      str,
        "file_type": str,    # ERF / MOD / RIM / ?
        "resources": [{"key": str, "resref": str, "ext": str, "size": int}, ...],
        "error":     str | None,
    }
    """
    from ..formats.archives import ERFReader
    summary = {
        "path": path,
        "file_type": Path(path).suffix.upper().lstrip("."),
        "resources": [],
        "error": None,
    }
    try:
        erf = ERFReader(path)
        count = erf.load()
        if count == 0 and not os.path.exists(path):
            summary["error"] = f"File not found: {path}"
            return summary
        for key, entry in sorted(erf.resources.items()):
            ext = entry.ext
            summary["resources"].append({
                "key":    key,
                "resref": entry.resref,
                "ext":    ext,
                "size":   entry.size,
            })
    except Exception as e:
        summary["error"] = str(e)
        log.error(f"inspect_archive failed for {path}: {e}")
    return summary


# ─────────────────────────────────────────────────────────────────────────────
#  Dialog
# ─────────────────────────────────────────────────────────────────────────────

if _HAS_QT:

    class ModImportDialog(QDialog):
        """
        Browse a .mod / .erf / .rim archive and import it as the active module.

        Usage::

            dlg = ModImportDialog(parent, mod_path="/path/to/file.mod")
            if dlg.exec_() == QDialog.Accepted:
                summary = dlg.summary        # dict returned by load_from_mod()
                lyt_text = summary["lyt_text"]
        """

        # Emitted after successful import (summary dict)
        module_loaded = pyqtSignal(dict)

        def __init__(self, parent=None, mod_path: str = ""):
            super().__init__(parent)
            self.setWindowTitle("Import Module Archive")
            self.setMinimumSize(820, 560)
            self.resize(940, 640)
            self.setModal(True)
            self.setStyleSheet("""
                QDialog { background: #1e1e1e; color: #d4d4d4; }
                QLabel  { color: #d4d4d4; }
                QGroupBox { border: 1px solid #3c3c3c; border-radius: 4px;
                            color: #569cd6; font-weight: bold; padding-top: 8px; }
                QGroupBox::title { subcontrol-origin: margin; left: 8px; }
                QTreeWidget { background: #252526; color: #d4d4d4;
                              border: 1px solid #3c3c3c; alternate-background-color: #2d2d30; }
                QTreeWidget::item:selected { background: #094771; }
                QHeaderView::section { background: #3c3c3c; color: #cccccc;
                                       border: none; padding: 4px; }
                QTextEdit { background: #1e1e1e; color: #d4d4d4;
                            border: 1px solid #3c3c3c; font-family: Consolas, monospace; }
                QLineEdit { background: #3c3c3c; color: #d4d4d4; border: 1px solid #555;
                            border-radius: 3px; padding: 3px 6px; }
                QPushButton { background: #3c3c3c; color: #cccccc;
                              border: 1px solid #555; border-radius: 3px; padding: 4px 12px; }
                QPushButton:hover { background: #4a4a4a; color: white; }
                QPushButton.accent { background: #0e639c; color: white; border: none; }
                QPushButton.accent:hover { background: #1177bb; }
                QProgressBar { background: #3c3c3c; border: none;
                               border-radius: 3px; color: white; text-align: center; }
                QProgressBar::chunk { background: #007acc; border-radius: 3px; }
            """)

            self._mod_path = mod_path
            self._summary: Optional[dict] = None
            self._archive_info: Optional[dict] = None

            self._build_ui()

            if mod_path:
                self._set_path(mod_path)

        # ── Properties ────────────────────────────────────────────────────

        @property
        def summary(self) -> Optional[dict]:
            """Import summary returned by load_from_mod(), available after accept()."""
            return self._summary

        # ── UI construction ───────────────────────────────────────────────

        def _build_ui(self):
            root = QVBoxLayout(self)
            root.setSpacing(8)
            root.setContentsMargins(12, 12, 12, 8)

            # ── Path row ──────────────────────────────────────────────────
            path_row = QHBoxLayout()
            path_row.addWidget(QLabel("Archive:"))
            self._path_edit = QLineEdit()
            self._path_edit.setPlaceholderText("Path to .mod / .erf / .rim file…")
            self._path_edit.setReadOnly(True)
            path_row.addWidget(self._path_edit, 1)
            browse_btn = QPushButton("Browse…")
            browse_btn.clicked.connect(self._browse)
            path_row.addWidget(browse_btn)
            root.addLayout(path_row)

            # ── Info bar ──────────────────────────────────────────────────
            self._info_label = QLabel("No archive loaded.")
            self._info_label.setStyleSheet("color: #888; font-size: 8pt;")
            root.addWidget(self._info_label)

            # ── Splitter: left = resource tree, right = details ───────────
            splitter = QSplitter(Qt.Horizontal)
            splitter.setHandleWidth(3)
            root.addWidget(splitter, 1)

            # Left: resource tree
            left = QWidget()
            left_layout = QVBoxLayout(left)
            left_layout.setContentsMargins(0, 0, 0, 0)
            left_layout.setSpacing(4)

            filter_row = QHBoxLayout()
            filter_row.addWidget(QLabel("Filter:"))
            self._filter_edit = QLineEdit()
            self._filter_edit.setPlaceholderText("Type to filter resources…")
            self._filter_edit.textChanged.connect(self._apply_filter)
            filter_row.addWidget(self._filter_edit, 1)
            left_layout.addLayout(filter_row)

            self._tree = QTreeWidget()
            self._tree.setHeaderLabels(["Resource", "Type", "Size"])
            self._tree.setAlternatingRowColors(True)
            self._tree.setSelectionMode(QAbstractItemView.SingleSelection)
            self._tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
            self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
            self._tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
            self._tree.itemSelectionChanged.connect(self._on_tree_selection)
            left_layout.addWidget(self._tree, 1)

            self._count_label = QLabel("")
            self._count_label.setStyleSheet("color: #888; font-size: 8pt;")
            left_layout.addWidget(self._count_label)

            splitter.addWidget(left)

            # Right: details / preview pane
            right = QWidget()
            right_layout = QVBoxLayout(right)
            right_layout.setContentsMargins(0, 0, 0, 0)
            right_layout.setSpacing(4)

            grp = QGroupBox("Module Summary")
            grp_layout = QVBoxLayout(grp)
            self._detail_text = QTextEdit()
            self._detail_text.setReadOnly(True)
            self._detail_text.setFont(QFont("Consolas", 9))
            self._detail_text.setMinimumWidth(280)
            grp_layout.addWidget(self._detail_text)
            right_layout.addWidget(grp, 1)

            splitter.addWidget(right)
            splitter.setSizes([520, 320])

            # ── Progress bar (hidden until import) ───────────────────────
            self._progress = QProgressBar()
            self._progress.setRange(0, 0)   # indeterminate
            self._progress.setFixedHeight(4)
            self._progress.hide()
            root.addWidget(self._progress)

            # ── Buttons ───────────────────────────────────────────────────
            btn_row = QHBoxLayout()
            btn_row.addStretch()

            self._import_btn = QPushButton("⇩ Import Module")
            self._import_btn.setFixedHeight(32)
            self._import_btn.setStyleSheet(
                "QPushButton { background:#0e639c; color:white; border:none; "
                "border-radius:4px; padding:0 18px; font-weight:bold; font-size:10pt; }"
                "QPushButton:hover { background:#1177bb; }"
                "QPushButton:disabled { background:#3c3c3c; color:#666; }"
            )
            self._import_btn.setEnabled(False)
            self._import_btn.clicked.connect(self._do_import)
            btn_row.addWidget(self._import_btn)

            cancel_btn = QPushButton("Cancel")
            cancel_btn.setFixedHeight(32)
            cancel_btn.clicked.connect(self.reject)
            btn_row.addWidget(cancel_btn)

            root.addLayout(btn_row)

        # ── Slots ─────────────────────────────────────────────────────────

        def _browse(self):
            path, _ = QFileDialog.getOpenFileName(
                self, "Open Module Archive", "",
                "KotOR Module Archives (*.mod *.erf *.rim);;All Files (*)"
            )
            if path:
                self._set_path(path)

        def _set_path(self, path: str):
            self._path_edit.setText(path)
            self._mod_path = path
            self._load_archive(path)

        def _load_archive(self, path: str):
            self._tree.clear()
            self._detail_text.clear()
            self._import_btn.setEnabled(False)
            self._info_label.setText("Loading archive…")

            info = inspect_archive(path)
            self._archive_info = info

            if info["error"]:
                self._info_label.setText(f"⚠ Error: {info['error']}")
                return

            resources = info["resources"]
            n = len(resources)

            # Group by extension for the tree
            groups: dict = {}
            for r in resources:
                groups.setdefault(r["ext"], []).append(r)

            # Core files first
            order = sorted(groups.keys(),
                           key=lambda x: (0 if x in _CORE_EXTS else
                                          1 if x in _MODEL_EXTS else
                                          2 if x in _BLUEPRINT_EXTS else
                                          3 if x in _SCRIPT_EXTS else 4, x))

            for ext in order:
                group_items = groups[ext]
                parent = QTreeWidgetItem(self._tree, [f"[{ext.upper()}]",
                                                      f"{len(group_items)} files", ""])
                parent.setExpanded(ext in _CORE_EXTS)
                color = _ext_color(ext)
                if color:
                    parent.setForeground(0, QColor(color))

                for r in group_items:
                    icon = _EXT_ICON.get(ext, "📄")
                    size_str = f"{r['size']:,} B" if r["size"] else ""
                    child = QTreeWidgetItem(parent, [
                        f"{icon} {r['resref']}.{r['ext']}",
                        r["ext"].upper(),
                        size_str,
                    ])
                    child.setData(0, Qt.UserRole, r)
                    if color:
                        child.setForeground(0, QColor(color))

            self._info_label.setText(
                f"Archive: {Path(path).name}  •  {n} resources  •  "
                f"{info['file_type']} format"
            )
            self._count_label.setText(f"{n} resources")
            self._import_btn.setEnabled(True)
            self._update_summary()

        def _apply_filter(self, text: str):
            text = text.lower()
            root = self._tree.invisibleRootItem()
            for i in range(root.childCount()):
                group = root.child(i)
                visible_in_group = 0
                for j in range(group.childCount()):
                    item = group.child(j)
                    r = item.data(0, Qt.UserRole)
                    match = (not text) or (text in (r["key"] if r else "").lower())
                    item.setHidden(not match)
                    if match:
                        visible_in_group += 1
                group.setHidden(visible_in_group == 0)

        def _on_tree_selection(self):
            items = self._tree.selectedItems()
            if not items:
                return
            item = items[0]
            r = item.data(0, Qt.UserRole)
            if r:
                self._detail_text.setPlainText(
                    f"ResRef : {r['resref']}\n"
                    f"Type   : {r['ext'].upper()}\n"
                    f"Size   : {r['size']:,} bytes\n"
                )

        def _update_summary(self):
            if not self._archive_info:
                return
            info = self._archive_info
            resources = info["resources"]

            core = [r for r in resources if r["ext"] in _CORE_EXTS]
            models = [r for r in resources if r["ext"] in _MODEL_EXTS]
            scripts = [r for r in resources if r["ext"] in _SCRIPT_EXTS]
            blueprints = [r for r in resources if r["ext"] in _BLUEPRINT_EXTS]
            textures = [r for r in resources if r["ext"] in _TEXTURE_EXTS]
            other = [r for r in resources
                     if r["ext"] not in _CORE_EXTS | _MODEL_EXTS |
                     _SCRIPT_EXTS | _BLUEPRINT_EXTS | _TEXTURE_EXTS]

            lines = [
                f"File : {Path(info['path']).name}",
                f"Type : {info['file_type']}",
                f"Total: {len(resources)} resources",
                "",
                "── Core Files ──────────────────",
            ]
            for r in core:
                lines.append(f"  {_EXT_ICON.get(r['ext'],'📄')} {r['resref']}.{r['ext']}")

            if models:
                lines.append(f"\n── Models ({len(models)}) ──────────────")
                for r in models[:12]:
                    lines.append(f"  {_EXT_ICON.get(r['ext'],'📄')} {r['resref']}.{r['ext']}")
                if len(models) > 12:
                    lines.append(f"  … and {len(models)-12} more")

            if blueprints:
                lines.append(f"\n── Blueprints ({len(blueprints)}) ─────────────")
                for r in blueprints[:10]:
                    lines.append(f"  {_EXT_ICON.get(r['ext'],'📄')} {r['resref']}.{r['ext']}")
                if len(blueprints) > 10:
                    lines.append(f"  … and {len(blueprints)-10} more")

            if scripts:
                lines.append(f"\n── Scripts ({len(scripts)}) ───────────────")
                for r in scripts[:8]:
                    lines.append(f"  {_EXT_ICON.get(r['ext'],'📄')} {r['resref']}.{r['ext']}")

            if textures:
                lines.append(f"\n── Textures: {len(textures)}")

            if other:
                lines.append(f"\n── Other: {len(other)}")

            lines.append("\n── Import will load ─────────────")
            lines.append("  GIT → game objects (placeables,")
            lines.append("        creatures, doors, etc.)")
            lines.append("  ARE → area properties")
            lines.append("  IFO → module info / scripts")
            lines.append("  LYT → room layout → 3D viewport")
            lines.append("  VIS → room visibility list")

            self._detail_text.setPlainText("\n".join(lines))

        def _do_import(self):
            """Run load_from_mod() and accept the dialog on success."""
            if not self._mod_path:
                return

            self._import_btn.setEnabled(False)
            self._progress.show()

            try:
                from ..core.module_state import get_module_state
                state = get_module_state()
                summary = state.load_from_mod(self._mod_path)
                self._summary = summary

                if summary.get("errors"):
                    err_list = "\n".join(f"  • {e}" for e in summary["errors"])
                    QMessageBox.warning(
                        self, "Import Warnings",
                        f"Module imported with warnings:\n{err_list}\n\n"
                        f"You can still work with the loaded data."
                    )

                self.module_loaded.emit(summary)
                self.accept()

            except Exception as e:
                log.exception(f"MOD import failed: {e}")
                QMessageBox.critical(
                    self, "Import Failed",
                    f"Could not import module:\n\n{e}"
                )
                self._import_btn.setEnabled(True)
            finally:
                self._progress.hide()

else:
    # Headless stub
    class ModImportDialog:  # type: ignore
        def __init__(self, *a, **kw):
            pass
