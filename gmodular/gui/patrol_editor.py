"""
GModular — Visual Patrol Waypoint Linker (P4)
Provides:
  - PatrolPathEditor: Inspector sub-widget that lets the user draw patrol paths
    for a selected creature by clicking viewport floor positions.
  - Auto-names waypoints: WP_[NPC_TAG]_01, WP_[NPC_TAG]_02, ...
  - Dashed-line overlay in viewport (drawn by viewport.py on request).
  - IPC hook: tells GhostScripter to insert GN_WalkWayPoints() in OnSpawn.

Public API used by InspectorPanel:
    patrol_editor = PatrolPathEditor(parent_widget, creature_obj, state)
    patrol_editor.path_changed.connect(viewport.set_patrol_path)
"""
from __future__ import annotations
import logging
from typing import List, Tuple, Optional

try:
    from qtpy.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
        QListWidget, QListWidgetItem, QGroupBox, QFrame,
        QDoubleSpinBox, QFormLayout, QMessageBox,
    )
    from qtpy.QtCore import Qt, Signal
    from qtpy.QtGui import QFont
    _HAS_QT = True
except ImportError:
    _HAS_QT = False
    QWidget = object  # type: ignore[misc,assignment]
    QListWidget = object  # type: ignore[misc,assignment]
    class Signal:  # type: ignore[no-redef]
        """Stub so class-level signal definitions don't crash without Qt."""
        def __init__(self, *args, **kwargs): pass
        def __set_name__(self, owner, name): pass

from ..formats.gff_types import GITCreature, GITWaypoint

log = logging.getLogger(__name__)


def _wp_name(tag: str, index: int) -> str:
    """Return the auto-generated waypoint tag for a given NPC and index."""
    return f"WP_{tag.upper()}_{index:02d}"


def _resref_from_tag(tag: str, index: int) -> str:
    """Return the ResRef (≤16 chars) for an auto-waypoint."""
    base = f"wp_{tag.lower()}_{index:02d}"
    return base[:16]


class PatrolPathEditor(QWidget):
    """
    Inspector widget for editing an NPC's patrol path.
    Embedded in the creature inspector section.
    """

    # Emitted whenever the waypoint list changes
    path_changed = Signal(object, list)   # (creature, List[GITWaypoint])
    # Emitted when user clicks "Add Waypoint Here" — requests a floor click
    request_click_placement = Signal(object)   # creature

    def __init__(self, creature: GITCreature, state=None, parent=None):
        super().__init__(parent)
        self._creature = creature
        self._state = state   # ModuleState reference
        self._waypoints: List[GITWaypoint] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Waypoint list
        self._list = QListWidget()
        self._list.setMaximumHeight(120)
        self._list.setFont(QFont("Consolas", 8))
        self._list.setStyleSheet(
            "QListWidget { background:#1e1e1e; color:#9cdcfe; "
            "border:1px solid #3c3c3c; font-size:8pt; }"
        )
        layout.addWidget(self._list)

        # Buttons
        btn_row = QHBoxLayout()
        self._add_btn = QPushButton("+ Add Waypoint")
        self._add_btn.setToolTip("Click a floor position in the viewport to add a waypoint")
        self._add_btn.clicked.connect(self._request_add)
        btn_row.addWidget(self._add_btn)

        self._remove_btn = QPushButton("− Remove")
        self._remove_btn.clicked.connect(self._remove_selected)
        btn_row.addWidget(self._remove_btn)

        self._up_btn = QPushButton("▲")
        self._up_btn.setFixedWidth(28)
        self._up_btn.clicked.connect(self._move_up)
        btn_row.addWidget(self._up_btn)

        self._down_btn = QPushButton("▼")
        self._down_btn.setFixedWidth(28)
        self._down_btn.clicked.connect(self._move_down)
        btn_row.addWidget(self._down_btn)
        layout.addLayout(btn_row)

        # Auto-script hint
        self._script_hint = QLabel()
        self._script_hint.setStyleSheet("color:#808080; font-size:7pt; font-style:italic;")
        self._script_hint.setWordWrap(True)
        layout.addWidget(self._script_hint)

        self._refresh_from_state()

    # ── Public API ────────────────────────────────────────────────────────

    def add_waypoint_at(self, x: float, y: float, z: float):
        """Called by MainWindow after the user clicks a floor position."""
        tag = getattr(self._creature, "tag", "NPC").strip() or "NPC"
        idx = len(self._waypoints) + 1
        wp = GITWaypoint()
        wp.tag    = _wp_name(tag, idx)
        wp.resref = _resref_from_tag(tag, idx)
        wp.x = x
        wp.y = y
        wp.z = z
        wp.map_note = f"Patrol: {tag}"

        self._waypoints.append(wp)

        # Inject into state GIT
        if self._state and self._state.git:
            if wp not in self._state.git.waypoints:
                self._state.git.waypoints.append(wp)
                self._state._dirty = True

        self._refresh_list()
        self._emit_changed()
        self._update_script_hint()
        log.info(f"Patrol waypoint added: {wp.tag} at ({x:.2f}, {y:.2f}, {z:.2f})")

    def get_waypoints(self) -> List[GITWaypoint]:
        return list(self._waypoints)

    def get_path_positions(self) -> List[Tuple[float, float, float]]:
        return [(w.x, w.y, w.z) for w in self._waypoints]

    # ── Internal ──────────────────────────────────────────────────────────

    def _refresh_from_state(self):
        """Load existing waypoints for this NPC from the state."""
        if self._state is None or self._state.git is None:
            return
        tag = getattr(self._creature, "tag", "").upper()
        if not tag:
            return
        self._waypoints = [
            w for w in self._state.git.waypoints
            if w.tag.upper().startswith(f"WP_{tag}_")
        ]
        # Sort by tag (WP_TAG_01, WP_TAG_02...)
        self._waypoints.sort(key=lambda w: w.tag.upper())
        self._refresh_list()
        self._update_script_hint()

    def _refresh_list(self):
        self._list.clear()
        for i, wp in enumerate(self._waypoints, 1):
            item = QListWidgetItem(
                f"  {i:02d}  {wp.tag}  "
                f"({wp.x:.1f}, {wp.y:.1f}, {wp.z:.1f})"
            )
            self._list.addItem(item)

    def _emit_changed(self):
        self.path_changed.emit(self._creature, list(self._waypoints))

    def _update_script_hint(self):
        tag = getattr(self._creature, "tag", "?")
        on_spawn = getattr(self._creature, "on_spawn", "").lower()
        if self._waypoints:
            if on_spawn:
                self._script_hint.setText(
                    f"OnSpawn = '{on_spawn}'. Make sure it calls "
                    f"GN_WalkWayPoints() or ActionMoveToObject().")
            else:
                self._script_hint.setText(
                    f"{len(self._waypoints)} waypoint(s). OnSpawn is empty — "
                    f"use GhostScripter to add a patrol script.")
        else:
            self._script_hint.setText("")

    def _request_add(self):
        """Tell MainWindow we want the next floor-click as a waypoint position."""
        self.request_click_placement.emit(self._creature)
        self._add_btn.setText("Click in viewport…")
        self._add_btn.setStyleSheet(
            "QPushButton { background:#1a5a8a; color:white; border:1px solid #2a7aaa; }"
        )

    def cancel_placement(self):
        self._add_btn.setText("+ Add Waypoint")
        self._add_btn.setStyleSheet("")

    def _remove_selected(self):
        row = self._list.currentRow()
        if row < 0 or row >= len(self._waypoints):
            return
        wp = self._waypoints.pop(row)
        if self._state and self._state.git:
            try:
                self._state.git.waypoints.remove(wp)
                self._state._dirty = True
            except ValueError:
                pass
        self._renumber()
        self._refresh_list()
        self._emit_changed()

    def _move_up(self):
        row = self._list.currentRow()
        if row <= 0:
            return
        self._waypoints[row], self._waypoints[row - 1] = \
            self._waypoints[row - 1], self._waypoints[row]
        self._renumber()
        self._refresh_list()
        self._list.setCurrentRow(row - 1)
        self._emit_changed()

    def _move_down(self):
        row = self._list.currentRow()
        if row < 0 or row >= len(self._waypoints) - 1:
            return
        self._waypoints[row], self._waypoints[row + 1] = \
            self._waypoints[row + 1], self._waypoints[row]
        self._renumber()
        self._refresh_list()
        self._list.setCurrentRow(row + 1)
        self._emit_changed()

    def _renumber(self):
        """Re-generate WP_TAG_01, WP_TAG_02... after reorder/delete."""
        tag = getattr(self._creature, "tag", "NPC").strip() or "NPC"
        for i, wp in enumerate(self._waypoints, 1):
            wp.tag    = _wp_name(tag, i)
            wp.resref = _resref_from_tag(tag, i)
