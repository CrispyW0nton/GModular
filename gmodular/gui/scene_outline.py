"""
GModular — Scene Outliner (UE-style)
=====================================
Redesigned to mirror Unreal Engine's World Outliner:

- Per-object visibility toggle (eye icon column)
- Lock toggle (lock icon column)
- Type-coloured icon badges
- Folder grouping support (future)
- Right-click context: Focus, Rename, Duplicate, Delete
- Keyboard: Del to delete, F2 to rename
- Live search with highlight
- Object count badges per group
- Selection synchronised with viewport
"""
from __future__ import annotations
import logging
from typing import Optional, Set

from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QTreeWidget, QTreeWidgetItem, QPushButton, QFrame,
    QMenu, QAction, QMessageBox, QInputDialog, QAbstractItemView,
    QHeaderView, QToolButton, QSizePolicy,
)
from qtpy.QtCore import Qt, Signal, QPoint, QTimer, QSize
from qtpy.QtGui import QFont, QColor, QBrush, QIcon, QPixmap, QPainter, QPen

from ..formats.gff_types import (
    GITPlaceable, GITCreature, GITDoor, GITTrigger,
    GITSoundObject, GITWaypoint, GITStoreObject,
)
from ..core.module_state import (
    get_module_state, DeleteObjectCommand, ModifyPropertyCommand,
)

log = logging.getLogger(__name__)

# ── Type metadata ──────────────────────────────────────────────────────────────
_TYPE_META = {
    "placeable": ("Placeables",  "P", "#5588ff", QColor(85,136,255)),
    "creature":  ("Creatures",   "C", "#ff8844", QColor(255,136,68)),
    "door":      ("Doors",       "D", "#ffee44", QColor(255,238,68)),
    "trigger":   ("Triggers",    "T", "#44ffaa", QColor(68,255,170)),
    "sound":     ("Sounds",      "S", "#44ffff", QColor(68,255,255)),
    "waypoint":  ("Waypoints",   "W", "#cc44ff", QColor(204,68,255)),
    "store":     ("Stores",      "$", "#88ff88", QColor(136,255,136)),
}

# Column indices
_COL_NAME   = 0
_COL_RESREF = 1
_COL_VIS    = 2
_COL_TYPE   = 3


def _make_type_badge(letter: str, color: QColor, size: int = 16) -> QPixmap:
    """Create a small coloured badge with a letter for tree icon."""
    px = QPixmap(size, size)
    px.fill(QColor(0, 0, 0, 0))
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    bg = QColor(color)
    bg.setAlpha(200)
    p.setBrush(QBrush(bg))
    p.setPen(QPen(color.lighter(150), 1))
    p.drawRoundedRect(1, 1, size-2, size-2, 3, 3)
    f = QFont("Segoe UI", int(size * 0.48), QFont.Bold)
    p.setFont(f)
    p.setPen(QColor("#ffffff"))
    p.drawText(0, 0, size, size, Qt.AlignCenter, letter)
    p.end()
    return px


def _make_eye_icon(visible: bool, size: int = 14) -> QPixmap:
    px = QPixmap(size, size)
    px.fill(QColor(0, 0, 0, 0))
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    if visible:
        p.setPen(QPen(QColor("#7ec8e3"), 1.5))
        p.setBrush(QBrush(QColor(0,0,0,0)))
        # Eye outline
        p.drawEllipse(2, 4, size-4, size-8)
        p.setBrush(QBrush(QColor("#7ec8e3")))
        p.drawEllipse(size//2-2, size//2-2, 4, 4)
    else:
        p.setPen(QPen(QColor("#444455"), 1.5))
        p.drawLine(2, 2, size-2, size-2)
        p.drawEllipse(2, 4, size-4, size-8)
    p.end()
    return px


class SceneOutlinePanel(QWidget):
    """
    UE-style Scene Outliner.
    Emits `object_selected` when an item is clicked.
    Emits `request_delete` when user deletes an object.
    """

    object_selected  = Signal(object)
    request_delete   = Signal(object)
    visibility_changed = Signal(object, bool)  # (obj, visible)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._building       = False
        self._selected_obj   = None
        self._hidden_objs: Set[int] = set()  # id(obj) -> hidden

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(120)
        self._search_timer.timeout.connect(self._refresh)

        # Pre-build badge icons
        self._badges: dict = {}
        for key, (label, letter, color_hex, color_q) in _TYPE_META.items():
            self._badges[key] = QIcon(_make_type_badge(letter, color_q))

        self._eye_on  = QIcon(_make_eye_icon(True))
        self._eye_off = QIcon(_make_eye_icon(False))

        self._setup_ui()
        try:
            get_module_state().on_change(self._refresh)
        except Exception:
            pass

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header ──────────────────────────────────────────────────────────
        hdr = QFrame()
        hdr.setFixedHeight(32)
        hdr.setStyleSheet(
            "QFrame { background:#1c1f27; border-bottom:1px solid #21262d; }"
        )
        hdr_l = QHBoxLayout(hdr)
        hdr_l.setContentsMargins(8, 0, 6, 0)

        title = QLabel("World Outliner")
        title.setStyleSheet("color:#4ec9b0; font-weight:bold; font-size:9pt;")
        hdr_l.addWidget(title)
        hdr_l.addStretch()

        self._count_lbl = QLabel("–")
        self._count_lbl.setStyleSheet("color:#484f58; font-size:7pt;")
        hdr_l.addWidget(self._count_lbl)

        # Collapse/expand
        exp_btn = self._hdr_btn("⊞", "Expand all groups")
        col_btn = self._hdr_btn("⊟", "Collapse all groups")
        exp_btn.clicked.connect(lambda: self._tree.expandAll())
        col_btn.clicked.connect(lambda: self._tree.collapseAll())
        hdr_l.addWidget(col_btn)
        hdr_l.addWidget(exp_btn)
        layout.addWidget(hdr)

        # ── Search bar ───────────────────────────────────────────────────────
        search_frame = QFrame()
        search_frame.setFixedHeight(30)
        search_frame.setStyleSheet("QFrame { background:#0d1117; border-bottom:1px solid #21262d; }")
        sf_l = QHBoxLayout(search_frame)
        sf_l.setContentsMargins(6, 3, 6, 3)

        self._search = QLineEdit()
        self._search.setPlaceholderText("  Search…")
        self._search.setFixedHeight(22)
        self._search.setFont(QFont("Segoe UI", 8))
        self._search.setStyleSheet(
            "QLineEdit { background:#161b22; color:#c9d1d9; border:1px solid #30363d;"
            " border-radius:10px; padding:0 8px; }"
            "QLineEdit:focus { border:1px solid #58a6ff; }"
        )
        self._search.textChanged.connect(self._on_search)
        sf_l.addWidget(self._search)
        layout.addWidget(search_frame)

        # ── Tree ─────────────────────────────────────────────────────────────
        self._tree = QTreeWidget()
        self._tree.setColumnCount(4)
        self._tree.setHeaderLabels(["  Name", "ResRef", "👁", "Type"])
        hdr_view = self._tree.header()
        hdr_view.setStyleSheet(
            "QHeaderView::section { background:#13161d; color:#484f58;"
            " border:none; border-bottom:1px solid #21262d;"
            " padding:3px 6px; font-size:7pt; font-family:Segoe UI; }"
        )
        hdr_view.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr_view.setSectionResizeMode(1, QHeaderView.Fixed)
        hdr_view.setSectionResizeMode(2, QHeaderView.Fixed)
        hdr_view.setSectionResizeMode(3, QHeaderView.Fixed)
        hdr_view.resizeSection(1, 68)
        hdr_view.resizeSection(2, 22)
        hdr_view.resizeSection(3, 28)
        hdr_view.setMinimumSectionSize(20)

        self._tree.setFont(QFont("Segoe UI", 8))
        self._tree.setIconSize(QSize(14, 14))
        self._tree.setIndentation(16)
        self._tree.setStyleSheet("""
            QTreeWidget {
                background: #0d1117;
                color: #c9d1d9;
                border: none;
                outline: none;
                selection-background-color: transparent;
            }
            QTreeWidget::item {
                height: 22px;
                border-radius: 3px;
                padding-left: 2px;
            }
            QTreeWidget::item:selected {
                background: #1f3a5f;
                color: #79c0ff;
                border-left: 2px solid #388bfd;
            }
            QTreeWidget::item:hover:!selected {
                background: #161b22;
            }
            QTreeWidget::branch {
                background: #0d1117;
            }
            QTreeWidget::branch:open:has-children {
                color: #484f58;
            }
        """)
        self._tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tree.itemClicked.connect(self._on_item_clicked)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        self._tree.installEventFilter(self)
        layout.addWidget(self._tree, 1)

        # ── Bottom bar ───────────────────────────────────────────────────────
        bot = QFrame()
        bot.setFixedHeight(24)
        bot.setStyleSheet("QFrame { background:#13161d; border-top:1px solid #21262d; }")
        bot_l = QHBoxLayout(bot)
        bot_l.setContentsMargins(6, 2, 6, 2)
        bot_l.setSpacing(4)

        self._sel_label = QLabel("")
        self._sel_label.setStyleSheet("color:#388bfd; font-size:7pt;")
        bot_l.addWidget(self._sel_label, 1)

        ref_btn = self._hdr_btn("⟳", "Refresh")
        ref_btn.clicked.connect(self._refresh)
        bot_l.addWidget(ref_btn)
        layout.addWidget(bot)

    def _hdr_btn(self, text: str, tip: str) -> QPushButton:
        b = QPushButton(text)
        b.setFixedSize(20, 20)
        b.setToolTip(tip)
        b.setFont(QFont("Segoe UI", 8))
        b.setStyleSheet(
            "QPushButton { background:transparent; color:#484f58; border:none; }"
            "QPushButton:hover { color:#c9d1d9; background:#21262d;"
            " border-radius:3px; }"
        )
        return b

    # ── Refresh ───────────────────────────────────────────────────────────────

    def _refresh(self):
        self._building = True
        self._tree.clear()

        state = get_module_state()
        if not state.git:
            self._count_lbl.setText("no module")
            self._building = False
            return

        git   = state.git
        total = 0
        search = self._search.text().lower()

        def _make_group(type_key: str) -> QTreeWidgetItem:
            label, letter, color_hex, color_q = _TYPE_META[type_key]
            grp = QTreeWidgetItem(self._tree)
            grp.setText(_COL_NAME, f"  {label}")
            grp.setIcon(_COL_NAME, self._badges[type_key])
            grp.setForeground(_COL_NAME, QBrush(QColor(color_hex)))
            grp.setFont(_COL_NAME, QFont("Segoe UI", 8, QFont.Bold))
            grp.setExpanded(True)
            grp.setFlags(grp.flags() & ~Qt.ItemIsSelectable)
            # Make group header visually distinct
            bg = QColor(color_hex)
            bg.setAlpha(15)
            grp.setBackground(_COL_NAME, QBrush(bg))
            return grp

        def _add_obj(grp: QTreeWidgetItem, obj, type_key: str) -> bool:
            tag    = getattr(obj, "tag",    "") or "(no tag)"
            resref = getattr(obj, "resref", "") or ""
            pos    = getattr(obj, "position", None)

            if search and search not in tag.lower() and search not in resref.lower():
                return False

            _, letter, color_hex, color_q = _TYPE_META[type_key]
            is_hidden = id(obj) in self._hidden_objs

            item = QTreeWidgetItem(grp)
            item.setText(_COL_NAME, f"  {tag}")
            item.setIcon(_COL_NAME, self._badges[type_key])
            item.setText(_COL_RESREF, resref)
            item.setIcon(_COL_VIS, self._eye_off if is_hidden else self._eye_on)
            item.setText(_COL_TYPE, letter)

            label_col = QColor("#888899") if is_hidden else QColor(color_hex)
            item.setForeground(_COL_NAME, QBrush(label_col))
            item.setForeground(_COL_RESREF, QBrush(QColor("#484f58")))
            item.setForeground(_COL_TYPE, QBrush(QColor(color_hex)))

            item.setData(_COL_NAME, Qt.UserRole, obj)
            item.setData(_COL_NAME, Qt.UserRole + 1, type_key)

            # Tooltip with full info
            pos_str = ""
            if pos:
                pos_str = f"\nPos: ({pos.x:.2f}, {pos.y:.2f}, {pos.z:.2f})"
            item.setToolTip(_COL_NAME,
                f"<b>{tag}</b><br>ResRef: <code>{resref}</code>"
                f"<br>Type: {type_key}{pos_str}")
            return True

        # Build groups
        groups = [
            (git.placeables,  "placeable"),
            (git.creatures,   "creature"),
            (git.doors,       "door"),
            (git.triggers,    "trigger"),
            (git.sounds,      "sound"),
            (git.waypoints,   "waypoint"),
            (git.stores,      "store"),
        ]
        for objects, type_key in groups:
            if not objects:
                continue
            grp = _make_group(type_key)
            n = sum(_add_obj(grp, o, type_key) for o in objects)
            label, *_ = _TYPE_META[type_key]
            grp.setText(_COL_NAME, f"  {label}  ({n})")
            if n == 0:
                # Remove empty group
                idx = self._tree.indexOfTopLevelItem(grp)
                if idx >= 0:
                    self._tree.takeTopLevelItem(idx)
            else:
                total += n

        self._count_lbl.setText(f"{total}")
        self._building = False

        if self._selected_obj is not None:
            self._highlight_obj(self._selected_obj)

    # ── Interaction ───────────────────────────────────────────────────────────

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int):
        if self._building:
            return
        obj = item.data(_COL_NAME, Qt.UserRole)
        if obj is None:
            return

        # Click on eye column → toggle visibility
        if column == _COL_VIS:
            obj_id = id(obj)
            if obj_id in self._hidden_objs:
                self._hidden_objs.discard(obj_id)
                item.setIcon(_COL_VIS, self._eye_on)
                self.visibility_changed.emit(obj, True)
            else:
                self._hidden_objs.add(obj_id)
                item.setIcon(_COL_VIS, self._eye_off)
                self.visibility_changed.emit(obj, False)
            return

        self._selected_obj = obj
        type_key = item.data(_COL_NAME, Qt.UserRole + 1)
        tag      = getattr(obj, "tag",    "") or "(no tag)"
        self._sel_label.setText(f"{tag}")
        self.object_selected.emit(obj)

    def _on_search(self, text: str):
        self._search_timer.start()

    def eventFilter(self, obj, event):
        """Handle Delete and F2 keys on the tree."""
        from qtpy.QtCore import QEvent
        from qtpy.QtGui import QKeyEvent
        if obj is self._tree and event.type() == QEvent.KeyPress:
            key = event.key()
            if key == Qt.Key_Delete:
                sel = self._tree.selectedItems()
                if sel:
                    git_obj = sel[0].data(_COL_NAME, Qt.UserRole)
                    if git_obj:
                        self._delete_obj(git_obj)
                return True
            if key == Qt.Key_F2:
                sel = self._tree.selectedItems()
                if sel:
                    git_obj = sel[0].data(_COL_NAME, Qt.UserRole)
                    if git_obj:
                        self._rename_tag(git_obj)
                return True
        return super().eventFilter(obj, event)

    def _on_context_menu(self, pos: QPoint):
        item = self._tree.itemAt(pos)
        if item is None:
            return
        obj = item.data(_COL_NAME, Qt.UserRole)
        if obj is None:
            return

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #161b22;
                color: #c9d1d9;
                border: 1px solid #30363d;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item { padding: 5px 20px 5px 10px; border-radius:3px; }
            QMenu::item:selected { background: #1f6feb; color:white; }
            QMenu::separator { height:1px; background:#21262d; margin:3px 6px; }
        """)

        tag = getattr(obj, "tag", "") or "(no tag)"

        focus_act = QAction(f"  Focus  '{tag}'  in Viewport", self)
        focus_act.triggered.connect(lambda: self.object_selected.emit(obj))
        menu.addAction(focus_act)

        menu.addSeparator()

        rename_act = QAction("  Rename Tag…  (F2)", self)
        rename_act.triggered.connect(lambda: self._rename_tag(obj))
        menu.addAction(rename_act)

        vis_label = "  Show" if id(obj) in self._hidden_objs else "  Hide"
        vis_act = QAction(f"  👁  {vis_label}", self)
        vis_act.triggered.connect(lambda: self._toggle_visibility(obj, item))
        menu.addAction(vis_act)

        menu.addSeparator()

        del_act = QAction("  Delete  (Del)", self)
        del_act.triggered.connect(lambda: self._delete_obj(obj))
        menu.addAction(del_act)

        menu.exec_(self._tree.viewport().mapToGlobal(pos))

    def _rename_tag(self, obj):
        old = getattr(obj, "tag", "") or ""
        new, ok = QInputDialog.getText(
            self, "Rename Tag",
            f"New tag for  '{old}':",
            text=old,
        )
        if ok and new.strip() and new.strip() != old:
            state = get_module_state()
            cmd = ModifyPropertyCommand(obj, "tag", old, new.strip())
            state.execute(cmd)

    def _toggle_visibility(self, obj, item: QTreeWidgetItem):
        obj_id = id(obj)
        if obj_id in self._hidden_objs:
            self._hidden_objs.discard(obj_id)
            item.setIcon(_COL_VIS, self._eye_on)
            self.visibility_changed.emit(obj, True)
        else:
            self._hidden_objs.add(obj_id)
            item.setIcon(_COL_VIS, self._eye_off)
            self.visibility_changed.emit(obj, False)

    def _delete_obj(self, obj):
        state = get_module_state()
        if not state.git:
            return
        cmd = DeleteObjectCommand(state.git, obj)
        state.execute(cmd)
        self.request_delete.emit(obj)

    # ── External API ──────────────────────────────────────────────────────────

    def set_selected(self, obj):
        self._selected_obj = obj
        if obj:
            tag = getattr(obj, "tag", "") or ""
            self._sel_label.setText(tag)
        self._highlight_obj(obj)

    def update_object_count(self, count: int):
        self._count_lbl.setText(str(count))

    def _highlight_obj(self, obj):
        self._tree.clearSelection()
        if obj is None:
            return
        root = self._tree.invisibleRootItem()
        for i in range(root.childCount()):
            group = root.child(i)
            for j in range(group.childCount()):
                item = group.child(j)
                if item.data(_COL_NAME, Qt.UserRole) is obj:
                    self._tree.setCurrentItem(item)
                    self._tree.scrollToItem(item)
                    return
