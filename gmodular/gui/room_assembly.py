"""
GModular — Room Assembly Grid (P1)
Provides a 2D top-down grid for assembling KotOR module rooms.

Features:
  - Drag-drop room MDL filenames onto a grid
  - Snap placement to configurable grid size
  - Auto-generates .lyt (plain text room layout) from placed rooms
  - Auto-generates .vis (visibility list) from room adjacency
  - Room connection arrows (doorway indicators)
  - Export to module ARE + regenerate LYT + VIS

LYT format (plain text):
    filedependency 0
    roomcount 2
    room1 0.00 0.00 0.00
    room2 10.00 0.00 0.00
    obstaclecount 0
    doorhookcount 0
    ...

VIS format (plain text):
    room1
    room2
    room1
    room2
"""
from __future__ import annotations
import logging
import math
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QGroupBox, QScrollArea,
    QSplitter, QFrame, QSizePolicy, QSpinBox, QDoubleSpinBox,
    QLineEdit, QFormLayout, QMessageBox, QMenu, QAction,
    QApplication,
)
from PyQt5.QtCore import Qt, pyqtSignal, QPoint, QRect, QSize
from PyQt5.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QDrag,
    QFontMetrics, QPalette,
)

log = logging.getLogger(__name__)

# ── Room Grid Cell Size ───────────────────────────────────────────────────

CELL_SIZE = 80          # pixels per grid unit
GRID_UNITS = 20         # default grid dimensions (20x20)
DEFAULT_ROOM_W = 10.0   # default room width in KotOR units
DEFAULT_ROOM_H = 10.0   # default room height


@dataclass
class RoomInstance:
    """A room placed on the assembly grid."""
    mdl_name: str           # e.g. "manm26aa"
    grid_x: int             # grid column
    grid_y: int             # grid row
    world_x: float = 0.0    # KotOR world units X
    world_y: float = 0.0    # KotOR world units Y
    world_z: float = 0.0    # KotOR world units Z
    width: float = DEFAULT_ROOM_W
    height: float = DEFAULT_ROOM_H
    connected_to: List[str] = field(default_factory=list)   # mdl_names of adjacent rooms


@dataclass
class LYTData:
    """In-memory .lyt file."""
    rooms: List[RoomInstance] = field(default_factory=list)

    def to_text(self) -> str:
        lines = []
        lines.append(f"filedependency 0")
        lines.append(f"roomcount {len(self.rooms)}")
        for r in self.rooms:
            lines.append(f"{r.mdl_name} {r.world_x:.2f} {r.world_y:.2f} {r.world_z:.2f}")
        lines.append("obstaclecount 0")
        lines.append("doorhookcount 0")
        return "\n".join(lines) + "\n"


def _generate_vis(rooms: List[RoomInstance]) -> str:
    """
    Generate a .vis file from placed rooms.
    Simple heuristic: rooms that share a grid edge are mutually visible.
    In practice, vis should be hand-tuned; this gives a functional default.
    """
    # Build adjacency
    pos_map: Dict[Tuple[int, int], str] = {
        (r.grid_x, r.grid_y): r.mdl_name for r in rooms}

    lines = []
    for r in rooms:
        lines.append(r.mdl_name)
        visible: List[str] = []
        # Self always visible
        visible.append(r.mdl_name)
        # Adjacent rooms (4-directional)
        for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            neighbor = pos_map.get((r.grid_x + dx, r.grid_y + dy))
            if neighbor:
                visible.append(neighbor)
        lines.extend(visible)
        lines.append("")  # empty line separates rooms

    return "\n".join(lines)


# ── Room Grid Widget ──────────────────────────────────────────────────────

class RoomGridWidget(QWidget):
    """
    Interactive 2D top-down grid for room placement.
    Supports drop, move, delete, and selection of room instances.
    """

    rooms_changed   = pyqtSignal()
    room_selected   = pyqtSignal(object)   # RoomInstance or None
    request_place_at = pyqtSignal(int, int) # gx, gy — right-click place request

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rooms: List[RoomInstance] = []
        self._selected: Optional[RoomInstance] = None
        self._drag_room: Optional[RoomInstance] = None
        self._drag_offset: QPoint = QPoint(0, 0)
        self._grid_w = GRID_UNITS
        self._grid_h = GRID_UNITS

        self.setMinimumSize(GRID_UNITS * CELL_SIZE, GRID_UNITS * CELL_SIZE)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)

        self._font = QFont("Consolas", 7)

    # ── Painting ──────────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # Background
        p.fillRect(self.rect(), QColor("#1a1a2e"))

        # Grid lines
        pen_grid = QPen(QColor("#2a2a4a"), 1)
        p.setPen(pen_grid)
        for col in range(self._grid_w + 1):
            x = col * CELL_SIZE
            p.drawLine(x, 0, x, self._grid_h * CELL_SIZE)
        for row in range(self._grid_h + 1):
            y = row * CELL_SIZE
            p.drawLine(0, y, self._grid_w * CELL_SIZE, y)

        # Connection lines between adjacent rooms
        self._draw_connections(p)

        # Room cells
        for room in self._rooms:
            self._draw_room(p, room)

        p.end()

    def _draw_room(self, p: QPainter, room: RoomInstance):
        x = room.grid_x * CELL_SIZE + 2
        y = room.grid_y * CELL_SIZE + 2
        w = CELL_SIZE - 4
        h = CELL_SIZE - 4

        selected = room is self._selected

        # Fill
        fill_color = QColor("#1e4a7a") if selected else QColor("#1a3a5a")
        p.fillRect(x, y, w, h, fill_color)

        # Border
        border_color = QColor("#4ec9b0") if selected else QColor("#3a7aaa")
        p.setPen(QPen(border_color, 2 if selected else 1))
        p.drawRect(x, y, w, h)

        # Label
        p.setFont(self._font)
        p.setPen(QColor("#9cdcfe"))
        fm = QFontMetrics(self._font)
        text = room.mdl_name
        if fm.horizontalAdvance(text) > w - 8:
            # Truncate
            while fm.horizontalAdvance(text + "…") > w - 8 and len(text) > 4:
                text = text[:-1]
            text += "…"
        p.drawText(QRect(x + 4, y + 4, w - 8, h - 8),
                   Qt.AlignCenter | Qt.TextWordWrap, text)

        # Coord label
        coord_text = f"({room.grid_x},{room.grid_y})"
        p.setPen(QColor("#555555"))
        fm2 = QFontMetrics(QFont("Consolas", 6))
        p.setFont(QFont("Consolas", 6))
        p.drawText(QRect(x + 2, y + h - 14, w - 4, 12), Qt.AlignCenter, coord_text)

    def _draw_connections(self, p: QPainter):
        """Draw dashed lines between adjacent rooms."""
        pos_map: Dict[Tuple[int, int], RoomInstance] = {
            (r.grid_x, r.grid_y): r for r in self._rooms}

        pen = QPen(QColor("#4aaaff"), 1, Qt.DashLine)
        p.setPen(pen)

        drawn: Set[Tuple] = set()
        for room in self._rooms:
            cx = room.grid_x * CELL_SIZE + CELL_SIZE // 2
            cy = room.grid_y * CELL_SIZE + CELL_SIZE // 2
            for dx, dy in [(1, 0), (0, 1)]:
                neighbor = pos_map.get((room.grid_x + dx, room.grid_y + dy))
                if neighbor:
                    key = tuple(sorted([(room.grid_x, room.grid_y),
                                        (neighbor.grid_x, neighbor.grid_y)]))
                    if key not in drawn:
                        drawn.add(key)
                        nx = neighbor.grid_x * CELL_SIZE + CELL_SIZE // 2
                        ny = neighbor.grid_y * CELL_SIZE + CELL_SIZE // 2
                        p.drawLine(cx, cy, nx, ny)

    # ── Drag & Drop ───────────────────────────────────────────────────────

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        if not event.mimeData().hasText():
            event.ignore()
            return
        mdl_name = event.mimeData().text().strip()
        pos = event.pos()
        gx = pos.x() // CELL_SIZE
        gy = pos.y() // CELL_SIZE
        gx = max(0, min(gx, self._grid_w - 1))
        gy = max(0, min(gy, self._grid_h - 1))

        # Check if cell is occupied
        existing = self._room_at(gx, gy)
        if existing:
            event.ignore()
            return

        room = RoomInstance(
            mdl_name=mdl_name,
            grid_x=gx, grid_y=gy,
            world_x=gx * DEFAULT_ROOM_W,
            world_y=gy * DEFAULT_ROOM_H,
        )
        self._rooms.append(room)
        self._selected = room
        self.rooms_changed.emit()
        self.room_selected.emit(room)
        self.update()
        event.acceptProposedAction()
        log.info(f"Room placed: {mdl_name} at grid ({gx},{gy})")

    # ── Mouse Events ──────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        gx = event.x() // CELL_SIZE
        gy = event.y() // CELL_SIZE
        room = self._room_at(gx, gy)
        self._selected = room
        self.room_selected.emit(room)
        self.update()

    def _room_at(self, gx: int, gy: int) -> Optional[RoomInstance]:
        for r in self._rooms:
            if r.grid_x == gx and r.grid_y == gy:
                return r
        return None

    # ── Keyboard ──────────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            if self._selected:
                self._delete_room(self._selected)
        else:
            super().keyPressEvent(event)

    def _context_menu(self, pos: QPoint):
        gx = pos.x() // CELL_SIZE
        gy = pos.y() // CELL_SIZE
        room = self._room_at(gx, gy)
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background:#2d2d2d; color:#d4d4d4; border:1px solid #3c3c3c; }"
            "QMenu::item:selected { background:#3c3c3c; }"
        )
        if room:
            act_del = menu.addAction(f"\u2715  Remove '{room.mdl_name}'")
            act_del.triggered.connect(lambda: self._delete_room(room))
            menu.addSeparator()
            act_rename = menu.addAction("Rename…")
            act_rename.triggered.connect(lambda: self._rename_room(room))
        else:
            # Right-click on empty cell to place currently-selected palette item
            act_place = menu.addAction("Place selected room here")
            act_place.triggered.connect(lambda: self.request_place_at.emit(gx, gy))
        menu.exec_(self.mapToGlobal(pos))

    def _rename_room(self, room: RoomInstance):
        from PyQt5.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Rename Room", "MDL name:",
                                        text=room.mdl_name)
        if ok and name.strip():
            room.mdl_name = name.strip().lower()
            self.rooms_changed.emit()
            self.update()

    def _delete_room(self, room: RoomInstance):
        self._rooms.remove(room)
        if self._selected is room:
            self._selected = None
        self.rooms_changed.emit()
        self.room_selected.emit(None)
        self.update()

    # ── Public API ────────────────────────────────────────────────────────

    def get_rooms(self) -> List[RoomInstance]:
        return list(self._rooms)

    def generate_lyt(self) -> LYTData:
        return LYTData(rooms=list(self._rooms))

    def generate_vis_text(self) -> str:
        return _generate_vis(self._rooms)

    def clear(self):
        self._rooms.clear()
        self._selected = None
        self.rooms_changed.emit()
        self.room_selected.emit(None)
        self.update()

    def load_rooms(self, rooms: List[RoomInstance]):
        self._rooms = list(rooms)
        self.update()


# ── Room Palette (left panel) ─────────────────────────────────────────────

class RoomPaletteWidget(QWidget):
    """
    Left panel: list of available room MDL names.
    Drag items to RoomGridWidget to place them.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        header = QLabel("Room Palette")
        header.setStyleSheet("color:#4ec9b0; font-weight:bold; font-size:9pt;")
        layout.addWidget(header)

        hint = QLabel("Drag rooms to the grid →")
        hint.setStyleSheet("color:#555; font-size:7pt; font-style:italic;")
        layout.addWidget(hint)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search rooms…")
        self._search.textChanged.connect(self._filter)
        self._search.setStyleSheet(
            "QLineEdit { background:#2d2d2d; color:#d4d4d4; border:1px solid #3c3c3c; "
            "padding:3px; font-size:8pt; }")
        layout.addWidget(self._search)

        self._list = QListWidget()
        self._list.setDragEnabled(True)
        self._list.setStyleSheet(
            "QListWidget { background:#1e1e1e; color:#9cdcfe; border:1px solid #3c3c3c; }"
            "QListWidget::item { padding:2px; }"
            "QListWidget::item:selected { background:#2d4a6a; }"
        )
        self._list.setFont(QFont("Consolas", 8))
        layout.addWidget(self._list, 1)

        self._all_rooms: List[str] = []

    def startDrag(self, supported_actions):
        """Override for QListWidget drag."""
        item = self._list.currentItem()
        if item:
            from PyQt5.QtCore import QMimeData
            mime = QMimeData()
            mime.setText(item.text())
            drag = QDrag(self._list)
            drag.setMimeData(mime)
            drag.exec_(Qt.CopyAction)

    def set_rooms(self, room_names: List[str]):
        """Populate the palette with MDL names."""
        self._all_rooms = sorted(set(room_names))
        self._filter(self._search.text())

    def _filter(self, text: str):
        self._list.clear()
        text = text.lower()
        for name in self._all_rooms:
            if not text or text in name.lower():
                self._list.addItem(name)


# ── Room Assembly Panel ───────────────────────────────────────────────────

class RoomAssemblyPanel(QWidget):
    """
    Full Room Assembly Grid panel.
    Contains: RoomPaletteWidget (left) + RoomGridWidget (center) + details (right).
    Exported as a tab inside MainWindow's bottom area or as a dedicated panel.
    """

    lyt_changed   = pyqtSignal(str)   # lyt text
    vis_changed   = pyqtSignal(str)   # vis text
    rooms_changed = pyqtSignal(list)  # List[RoomInstance]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self._apply_theme()

    def _setup_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        splitter = QSplitter(Qt.Horizontal)

        # Left: palette
        self._palette = RoomPaletteWidget()
        self._palette.setFixedWidth(160)
        splitter.addWidget(self._palette)

        # Center: grid (in scroll area)
        scroll = QScrollArea()
        scroll.setWidgetResizable(False)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._grid = RoomGridWidget()
        self._grid.rooms_changed.connect(self._on_rooms_changed)
        self._grid.room_selected.connect(self._on_room_selected)
        self._grid.request_place_at.connect(self._on_place_at_request)
        scroll.setWidget(self._grid)
        splitter.addWidget(scroll)

        # Right: room details + actions
        details = QWidget()
        details.setFixedWidth(180)
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(4, 4, 4, 4)

        self._detail_label = QLabel("No room selected")
        self._detail_label.setStyleSheet("color:#9cdcfe; font-weight:bold;")
        self._detail_label.setWordWrap(True)
        details_layout.addWidget(self._detail_label)

        details_layout.addWidget(QLabel("Room count:"))
        self._count_label = QLabel("0")
        self._count_label.setStyleSheet("color:#dcdcaa;")
        details_layout.addWidget(self._count_label)

        details_layout.addSpacing(8)

        export_grp = QGroupBox("Export")
        export_grp.setStyleSheet("QGroupBox { color:#dcdcaa; font-weight:bold; }")
        export_layout = QVBoxLayout(export_grp)

        self._export_lyt_btn = QPushButton("Copy LYT Text")
        self._export_lyt_btn.clicked.connect(self._copy_lyt)
        export_layout.addWidget(self._export_lyt_btn)

        self._export_vis_btn = QPushButton("Copy VIS Text")
        self._export_vis_btn.clicked.connect(self._copy_vis)
        export_layout.addWidget(self._export_vis_btn)

        self._save_lyt_btn = QPushButton("Save LYT + VIS…")
        self._save_lyt_btn.setToolTip("Write .lyt and .vis files to your project folder")
        self._save_lyt_btn.clicked.connect(self._save_lyt_vis)
        export_layout.addWidget(self._save_lyt_btn)

        clear_btn = QPushButton("Clear Grid")
        clear_btn.clicked.connect(self._grid.clear)
        export_layout.addWidget(clear_btn)

        details_layout.addWidget(export_grp)
        details_layout.addStretch()
        splitter.addWidget(details)

        splitter.setSizes([160, 700, 180])
        root.addWidget(splitter)

    def _apply_theme(self):
        self.setStyleSheet(
            "QWidget { background:#1a1a1a; color:#d4d4d4; }"
            "QPushButton { background:#2d2d2d; color:#d4d4d4; "
            "              border:1px solid #3c3c3c; border-radius:3px; padding:4px; }"
            "QPushButton:hover { background:#3c3c3c; }"
            "QScrollArea { background:#1a1a2e; border:1px solid #3c3c3c; }"
            "QSplitter::handle { background:#3c3c3c; }"
        )

    def _on_rooms_changed(self):
        rooms = self._grid.get_rooms()
        self._count_label.setText(str(len(rooms)))
        lyt = self._grid.generate_lyt()
        vis = self._grid.generate_vis_text()
        self.lyt_changed.emit(lyt.to_text())
        self.vis_changed.emit(vis)
        self.rooms_changed.emit(rooms)

    def _on_room_selected(self, room: Optional[RoomInstance]):
        if room is None:
            self._detail_label.setText("No room selected")
        else:
            self._detail_label.setText(
                f"{room.mdl_name}\nGrid: ({room.grid_x}, {room.grid_y})\n"
                f"World: ({room.world_x:.1f}, {room.world_y:.1f})")

    def _on_place_at_request(self, gx: int, gy: int):
        """Place the palette's currently-selected room at (gx, gy) via right-click."""
        item = self._palette._list.currentItem()
        if not item:
            return
        mdl_name = item.text().strip()
        if not mdl_name:
            return
        existing = self._grid._room_at(gx, gy)
        if existing:
            return
        room = RoomInstance(
            mdl_name=mdl_name,
            grid_x=gx, grid_y=gy,
            world_x=gx * DEFAULT_ROOM_W,
            world_y=gy * DEFAULT_ROOM_H,
        )
        self._grid._rooms.append(room)
        self._grid._selected = room
        self._grid.rooms_changed.emit()
        self._grid.room_selected.emit(room)
        self._grid.update()

    def _save_lyt_vis(self):
        """Write .lyt and .vis files to a user-chosen directory."""
        from PyQt5.QtWidgets import QFileDialog
        folder = QFileDialog.getExistingDirectory(
            self, "Choose folder to save LYT + VIS", "")
        if not folder:
            return
        # Determine base name from first room or default
        rooms = self._grid.get_rooms()
        base = rooms[0].mdl_name[:8] if rooms else "module"
        lyt_path = os.path.join(folder, base + ".lyt")
        vis_path = os.path.join(folder, base + ".vis")
        try:
            with open(lyt_path, "w") as f:
                f.write(self._grid.generate_lyt().to_text())
            with open(vis_path, "w") as f:
                f.write(self._grid.generate_vis_text())
            log.info(f"Saved LYT: {lyt_path}  VIS: {vis_path}")
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "Saved",
                f"Saved:\n  {lyt_path}\n  {vis_path}")
        except Exception as e:
            log.error(f"Save LYT/VIS failed: {e}")

    def _copy_lyt(self):
        lyt = self._grid.generate_lyt()
        QApplication.clipboard().setText(lyt.to_text())
        log.info("LYT text copied to clipboard")

    def _copy_vis(self):
        vis = self._grid.generate_vis_text()
        QApplication.clipboard().setText(vis)
        log.info("VIS text copied to clipboard")

    def set_available_rooms(self, room_names: List[str]):
        self._palette.set_rooms(room_names)

    def get_rooms(self) -> List[RoomInstance]:
        return self._grid.get_rooms()

    def get_lyt(self) -> LYTData:
        return self._grid.generate_lyt()

    def get_vis(self) -> str:
        return self._grid.generate_vis_text()
