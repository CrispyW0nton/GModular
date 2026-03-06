"""
GModular — Scene Outline Panel
Shows all GIT objects in a tree grouped by type (Placeables/Creatures/Doors/etc.)
Mirrors the Unreal Engine World Outliner.

Click to select → highlights in viewport + populates Inspector.
Right-click for context menu (Delete, Rename, Duplicate).
"""
from __future__ import annotations
import logging
from typing import Optional

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QTreeWidget, QTreeWidgetItem, QPushButton, QFrame,
    QMenu, QAction, QMessageBox, QInputDialog, QAbstractItemView,
)
from PyQt5.QtCore import Qt, pyqtSignal, QPoint, QTimer
from PyQt5.QtGui import QFont, QColor, QBrush, QIcon

from ..formats.gff_types import (
    GITPlaceable, GITCreature, GITDoor, GITTrigger,
    GITSoundObject, GITWaypoint, GITStoreObject,
)
from ..core.module_state import (
    get_module_state, DeleteObjectCommand, ModifyPropertyCommand,
)

log = logging.getLogger(__name__)

# Object type → (display label, icon letter, color)
_TYPE_META = {
    "placeable": ("Placeables",  "P", "#88aaff"),
    "creature":  ("Creatures",   "C", "#ffaa88"),
    "door":      ("Doors",       "D", "#ffff88"),
    "trigger":   ("Triggers",    "T", "#88ffaa"),
    "sound":     ("Sounds",      "S", "#ffaaff"),
    "waypoint":  ("Waypoints",   "W", "#aa88ff"),
    "store":     ("Stores",      "$", "#aaffaa"),
}


class SceneOutlinePanel(QWidget):
    """
    Scene outliner — lists all GIT objects in a collapsible tree.
    Emits `object_selected` when an item is clicked.
    """

    object_selected = pyqtSignal(object)    # GIT object or None
    request_delete  = pyqtSignal(object)    # GIT object to delete

    def __init__(self, parent=None):
        super().__init__(parent)
        self._building = False
        self._selected_obj = None
        # Debounce timer: waits 150 ms after last keystroke before refreshing tree
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self._refresh)
        self._setup_ui()
        # Subscribe to module changes
        try:
            get_module_state().on_change(self._refresh)
        except Exception:
            pass

    # ── UI Setup ──────────────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # Header
        hdr = QFrame()
        hdr.setFixedHeight(28)
        hdr.setStyleSheet("background:#252526; border-bottom:1px solid #3c3c3c;")
        hdr_layout = QHBoxLayout(hdr)
        hdr_layout.setContentsMargins(8, 2, 8, 2)
        title = QLabel("Scene Outline")
        title.setStyleSheet("color:#4ec9b0; font-weight:bold; font-size:9pt;")
        hdr_layout.addWidget(title)
        hdr_layout.addStretch()
        self._count_lbl = QLabel("0 objects")
        self._count_lbl.setStyleSheet("color:#555; font-size:8pt;")
        hdr_layout.addWidget(self._count_lbl)
        layout.addWidget(hdr)

        # Search
        self._search = QLineEdit()
        self._search.setPlaceholderText("🔍 Filter…")
        self._search.setFixedHeight(24)
        self._search.setFont(QFont("Segoe UI", 8))
        self._search.setStyleSheet(
            "QLineEdit { background:#3c3c3c; color:#d4d4d4; border:1px solid #555;"
            " border-radius:2px; padding:0 4px; }"
            "QLineEdit:focus { border:1px solid #007acc; }"
        )
        self._search.textChanged.connect(self._on_search)
        layout.addWidget(self._search)

        # Tree widget
        self._tree = QTreeWidget()
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels(["Name", "ResRef", "Pos"])
        self._tree.header().setStyleSheet(
            "QHeaderView::section { background:#2d2d30; color:#969696; "
            "border:none; padding:2px; font-size:8pt; }"
        )
        self._tree.header().setDefaultSectionSize(90)
        self._tree.header().resizeSection(0, 120)
        self._tree.header().resizeSection(1, 80)
        self._tree.header().resizeSection(2, 70)
        self._tree.setFont(QFont("Consolas", 8))
        self._tree.setStyleSheet("""
            QTreeWidget {
                background: #1e1e1e;
                color: #d4d4d4;
                border: none;
                outline: none;
            }
            QTreeWidget::item {
                height: 18px;
                border: none;
            }
            QTreeWidget::item:selected {
                background: #094771;
                color: white;
            }
            QTreeWidget::item:hover {
                background: #2a2d2e;
            }
            QTreeWidget::branch {
                background: #1e1e1e;
            }
            QTreeWidget::branch:open:has-children {
                image: url(none);
            }
        """)
        self._tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tree.itemClicked.connect(self._on_item_clicked)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._tree)

        # Bottom toolbar
        btn_bar = QHBoxLayout()
        btn_bar.setContentsMargins(2, 2, 2, 2)
        btn_bar.setSpacing(3)

        refresh_btn = self._make_btn("⟳ Refresh", self._refresh)
        btn_bar.addWidget(refresh_btn)
        btn_bar.addStretch()
        collapse_btn = self._make_btn("⊟ Collapse All", self._tree.collapseAll)
        btn_bar.addWidget(collapse_btn)
        expand_btn = self._make_btn("⊞ Expand All", self._tree.expandAll)
        btn_bar.addWidget(expand_btn)
        layout.addLayout(btn_bar)

    def _make_btn(self, text: str, slot) -> QPushButton:
        b = QPushButton(text)
        b.clicked.connect(slot)
        b.setFixedHeight(22)
        b.setStyleSheet(
            "QPushButton { background:#3c3c3c; color:#cccccc; border:1px solid #555;"
            " border-radius:2px; padding:0 6px; font-size:8pt; }"
            "QPushButton:hover { background:#4a4a4a; color:white; }"
        )
        return b

    # ── Refresh Logic ─────────────────────────────────────────────────────────

    def _refresh(self):
        """Rebuild tree from current module state."""
        self._building = True
        self._tree.clear()

        state = get_module_state()
        if not state.git:
            self._count_lbl.setText("No module")
            self._building = False
            return

        git = state.git
        total = 0
        search = self._search.text().lower()

        def _make_group(type_key: str):
            label, letter, color = _TYPE_META[type_key]
            grp = QTreeWidgetItem(self._tree)
            grp.setText(0, label)
            grp.setForeground(0, QBrush(QColor(color)))
            grp.setFont(0, QFont("Segoe UI", 8, QFont.Bold))
            grp.setExpanded(True)
            grp.setFlags(grp.flags() & ~Qt.ItemIsSelectable)
            return grp

        def _add_obj(grp: QTreeWidgetItem, obj, type_key: str):
            tag    = getattr(obj, "tag",    "") or "(no tag)"
            resref = getattr(obj, "resref", "") or ""
            pos    = getattr(obj, "position", None)
            pos_str = f"{pos.x:.1f},{pos.y:.1f}" if pos else ""

            # Filter
            if search and search not in tag.lower() and search not in resref.lower():
                return False

            _, letter, color = _TYPE_META[type_key]
            item = QTreeWidgetItem(grp)
            item.setText(0, tag)
            item.setText(1, resref)
            item.setText(2, pos_str)
            item.setForeground(0, QBrush(QColor(color)))
            item.setData(0, Qt.UserRole, obj)
            item.setToolTip(0, f"Type: {type_key}\nTag: {tag}\nResRef: {resref}")
            return True

        # Placeables
        if git.placeables:
            grp = _make_group("placeable")
            n = sum(_add_obj(grp, p, "placeable") for p in git.placeables)
            grp.setText(0, f"Placeables ({n})")
            total += n

        # Creatures
        if git.creatures:
            grp = _make_group("creature")
            n = sum(_add_obj(grp, c, "creature") for c in git.creatures)
            grp.setText(0, f"Creatures ({n})")
            total += n

        # Doors
        if git.doors:
            grp = _make_group("door")
            n = sum(_add_obj(grp, d, "door") for d in git.doors)
            grp.setText(0, f"Doors ({n})")
            total += n

        # Triggers
        if git.triggers:
            grp = _make_group("trigger")
            n = sum(_add_obj(grp, t, "trigger") for t in git.triggers)
            grp.setText(0, f"Triggers ({n})")
            total += n

        # Sounds
        if git.sounds:
            grp = _make_group("sound")
            n = sum(_add_obj(grp, s, "sound") for s in git.sounds)
            grp.setText(0, f"Sounds ({n})")
            total += n

        # Waypoints
        if git.waypoints:
            grp = _make_group("waypoint")
            n = sum(_add_obj(grp, w, "waypoint") for w in git.waypoints)
            grp.setText(0, f"Waypoints ({n})")
            total += n

        # Stores
        if git.stores:
            grp = _make_group("store")
            n = sum(_add_obj(grp, s, "store") for s in git.stores)
            grp.setText(0, f"Stores ({n})")
            total += n

        self._count_lbl.setText(f"{total} objects")
        self._building = False

        # Re-select previously selected object
        if self._selected_obj is not None:
            self._highlight_obj(self._selected_obj)

    # ── Interaction ───────────────────────────────────────────────────────────

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int):
        if self._building:
            return
        obj = item.data(0, Qt.UserRole)
        if obj is None:
            return
        self._selected_obj = obj
        self.object_selected.emit(obj)

    def _on_search(self, text: str):
        # Debounce: restart the timer on each keystroke; only fire _refresh once idle
        self._search_timer.start()

    def _on_context_menu(self, pos: QPoint):
        item = self._tree.itemAt(pos)
        if item is None:
            return
        obj = item.data(0, Qt.UserRole)
        if obj is None:
            return

        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background:#252526; color:#cccccc; border:1px solid #3c3c3c; }"
            "QMenu::item:selected { background:#094771; }"
        )

        select_act = QAction("Select in Viewport", self)
        select_act.triggered.connect(lambda: self.object_selected.emit(obj))
        menu.addAction(select_act)

        menu.addSeparator()

        rename_act = QAction("Rename Tag…", self)
        rename_act.triggered.connect(lambda: self._rename_tag(obj))
        menu.addAction(rename_act)

        del_act = QAction("Delete", self)
        del_act.setShortcut("Del")
        del_act.triggered.connect(lambda: self._delete_obj(obj))
        menu.addAction(del_act)

        menu.exec_(self._tree.viewport().mapToGlobal(pos))

    def _rename_tag(self, obj):
        """Rename an object's tag using the command pattern (supports undo)."""
        old_tag = getattr(obj, "tag", "")
        new_tag, ok = QInputDialog.getText(
            self, "Rename Tag",
            f"New tag for '{old_tag}':",
            text=old_tag,
        )
        if ok and new_tag.strip() and new_tag.strip() != old_tag:
            state = get_module_state()
            cmd = ModifyPropertyCommand(obj, "tag", old_tag, new_tag.strip())
            state.execute(cmd)   # marks dirty and emits change

    def _delete_obj(self, obj):
        """Delete an object using the command pattern (supports undo/redo)."""
        state = get_module_state()
        if not state.git:
            return
        cmd = DeleteObjectCommand(state.git, obj)
        state.execute(cmd)   # marks dirty and emits change
        self.request_delete.emit(obj)

    # ── External API ──────────────────────────────────────────────────────────

    def set_selected(self, obj):
        """Called by viewport when it selects an object."""
        self._selected_obj = obj
        self._highlight_obj(obj)

    def _highlight_obj(self, obj):
        """Highlight tree item matching obj."""
        self._tree.clearSelection()
        if obj is None:
            return
        root = self._tree.invisibleRootItem()
        for i in range(root.childCount()):
            group = root.child(i)
            for j in range(group.childCount()):
                item = group.child(j)
                if item.data(0, Qt.UserRole) is obj:
                    self._tree.setCurrentItem(item)
                    self._tree.scrollToItem(item)
                    return
