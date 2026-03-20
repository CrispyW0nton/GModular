"""
GhostRigger — Main Window
==========================
Qt main window with live blueprint field editor panel.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

try:
    from qtpy.QtWidgets import (
        QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QPushButton, QStatusBar,
        QTreeWidget, QTreeWidgetItem, QSplitter,
    )
    from qtpy.QtCore import Qt
    from qtpy.QtGui import QFont
    _HAS_QT = True
except ImportError:
    _HAS_QT = False
    QMainWindow = object  # type: ignore


class MainWindow(QMainWindow if _HAS_QT else object):
    """GhostRigger main window — blueprint tree + field editor + IPC status."""

    TITLE = "GhostRigger — KotOR Asset Editor  v1.0"
    STYLE = """
        QMainWindow, QWidget { background: #1e1e1e; color: #d4d4d4; }
        QLabel { color: #d4d4d4; }
        QPushButton {
            background: #2d2d2d; color: #d4d4d4;
            border: 1px solid #3e3e42; border-radius: 4px;
            padding: 4px 10px;
        }
        QPushButton:hover { background: #264f78; }
        QTreeWidget {
            background: #252526; color: #d4d4d4;
            border: 1px solid #3e3e42;
        }
        QStatusBar { background: #252526; color: #9d9d9d; }
    """

    def __init__(self):
        if _HAS_QT:
            super().__init__()
            self.setWindowTitle(self.TITLE)
            self.setStyleSheet(self.STYLE)
            self.resize(1200, 750)
            self._build_ui()
        else:
            log.warning("Qt not available — MainWindow is a no-op stub")

    def _build_ui(self):
        if not _HAS_QT:
            return
        from ghostrigger.gui.field_editor import BlueprintFieldEditor

        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)

        # ── Toolbar ──────────────────────────────────────────────────────────
        toolbar = QWidget()
        toolbar.setFixedHeight(36)
        toolbar.setStyleSheet("background:#252526; border-bottom:1px solid #3e3e42;")
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(8, 2, 8, 2)

        for label, tip, bp_type in [
            ("New UTC", "New Creature blueprint",  "utc"),
            ("New UTP", "New Placeable blueprint", "utp"),
            ("New UTD", "New Door blueprint",       "utd"),
        ]:
            btn = QPushButton(label)
            btn.setToolTip(tip)
            btn.setFixedWidth(80)
            btn.clicked.connect(
                lambda _checked=False, t=bp_type: self._new_blueprint(t)
            )
            tb_layout.addWidget(btn)
        tb_layout.addStretch()

        ipc_lbl = QLabel("IPC: port 7001")
        ipc_lbl.setStyleSheet("color:#4ec9b0; font-size:11px;")
        tb_layout.addWidget(ipc_lbl)
        root_layout.addWidget(toolbar)

        # ── Main splitter ─────────────────────────────────────────────────────
        splitter = QSplitter(Qt.Horizontal)

        # Left: blueprint tree
        self._tree = QTreeWidget()
        self._tree.setHeaderLabel("Blueprints")
        self._tree.setMinimumWidth(220)
        self._tree_groups = {}
        for btype, key in [
            ("Creatures (UTC)", "utc"),
            ("Placeables (UTP)", "utp"),
            ("Doors (UTD)", "utd"),
        ]:
            item = QTreeWidgetItem(self._tree, [btype])
            self._tree_groups[key] = item
        self._tree.expandAll()
        self._tree.itemClicked.connect(self._on_tree_click)
        splitter.addWidget(self._tree)

        # Right: field editor
        self._field_editor = BlueprintFieldEditor()
        self._field_editor.field_changed.connect(self._on_field_changed)
        self._field_editor.save_requested.connect(self._on_save)
        self._field_editor.revert_requested.connect(self._on_revert)
        splitter.addWidget(self._field_editor)

        splitter.setSizes([240, 960])
        root_layout.addWidget(splitter)

        # ── Status bar ────────────────────────────────────────────────────────
        self._status = QStatusBar()
        self._status.showMessage("GhostRigger ready — IPC server on port 7001")
        self.setStatusBar(self._status)

    # ── Blueprint management ──────────────────────────────────────────────────

    def _new_blueprint(self, bp_type: str) -> None:
        if not _HAS_QT:
            return
        from ghostrigger.core.blueprint_state import Blueprint, get_registry
        from ghostrigger.gui.field_editor import get_field_schema

        _defaults = {
            "utc": {"FirstName": "Unnamed", "Tag": "CREATURE001", "MaxHitPoints": 8},
            "utp": {"Name": "Unnamed", "Tag": "PLACEABLE001", "HP": 10},
            "utd": {"LocName": "Unnamed", "Tag": "DOOR001", "HP": 10},
        }
        fields = _defaults.get(bp_type, {})
        resref = f"new_{bp_type}_{len(get_registry())}"
        bp = Blueprint(resref=resref, blueprint_type=bp_type, fields=fields)
        get_registry().add(bp)
        self._add_tree_item(resref, bp_type)
        self._field_editor.load_blueprint(resref, bp_type, fields)
        self._status.showMessage(f"Created {bp_type.upper()} blueprint: {resref}")

    def _add_tree_item(self, resref: str, bp_type: str) -> None:
        if not _HAS_QT:
            return
        group = self._tree_groups.get(bp_type)
        if group:
            QTreeWidgetItem(group, [resref])
            self._tree.expandAll()

    def load_blueprint_in_editor(self, resref: str) -> None:
        """Load a blueprint from the registry into the field editor."""
        if not _HAS_QT:
            return
        from ghostrigger.core.blueprint_state import get_registry
        bp = get_registry().get(resref)
        if bp is None:
            self._status.showMessage(f"Blueprint not found: {resref}")
            return
        self._field_editor.load_blueprint(bp.resref, bp.blueprint_type, bp.fields)
        self._status.showMessage(f"Editing {bp.blueprint_type.upper()}: {resref}")

    def _on_tree_click(self, item: QTreeWidgetItem, _col: int) -> None:
        if not _HAS_QT:
            return
        # Only leaf nodes (blueprints), not group headers
        if item.parent() is None:
            return
        resref = item.text(0)
        self.load_blueprint_in_editor(resref)

    def _on_field_changed(self, resref: str, field_name: str, value) -> None:
        from ghostrigger.core.blueprint_state import get_registry
        bp = get_registry().get(resref)
        if bp:
            bp.set(field_name, value)
        log.debug("field_changed %s.%s = %r", resref, field_name, value)

    def _on_save(self, resref: str) -> None:
        from ghostrigger.core.blueprint_state import get_registry
        bp = get_registry().get(resref)
        if bp and _HAS_QT:
            # Sync editor values into registry
            values = self._field_editor.get_current_values()
            for k, v in values.items():
                bp.set(k, v)
            bp.dirty = False
            self._status.showMessage(f"Saved: {resref}")
            log.info("Blueprint saved: %s", resref)

    def _on_revert(self, resref: str) -> None:
        from ghostrigger.core.blueprint_state import get_registry
        bp = get_registry().get(resref)
        if bp and _HAS_QT:
            self._field_editor.load_blueprint(bp.resref, bp.blueprint_type, bp.fields)
            self._status.showMessage(f"Reverted: {resref}")

