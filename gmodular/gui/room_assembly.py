"""
GModular — Room Assembly Grid (P1)
Provides a 2D top-down grid for assembling KotOR module rooms.

Features:
  - Drag-drop room MDL filenames onto a grid
  - Single-click from palette also places rooms (no drag required)
  - Snap placement to configurable grid size
  - Auto-generates .lyt (plain text room layout) from placed rooms
  - Auto-generates .vis (visibility list) from room adjacency
  - Room connection arrows (doorway indicators)
  - Export to module ARE + regenerate LYT + VIS
  - Zoom in/out on the grid (mouse wheel or buttons)

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
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

try:
    from PyQt5.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
        QListWidget, QListWidgetItem, QScrollArea,
        QSplitter, QFrame, QSizePolicy,
        QLineEdit, QMessageBox, QMenu,
        QApplication, QFileDialog, QInputDialog,
    )
    from PyQt5.QtCore import Qt, pyqtSignal, QPoint, QRect, QSize, QMimeData
    from PyQt5.QtGui import (
        QPainter, QColor, QPen, QFont,
        QFontMetrics, QDrag,
    )
    _HAS_QT = True
except ImportError:
    _HAS_QT = False
    QWidget = object  # type: ignore[misc,assignment]
    QListWidget = object  # type: ignore[misc,assignment]
    class pyqtSignal:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs): pass
        def __set_name__(self, owner, name): pass

log = logging.getLogger(__name__)

# ── Room Grid Cell Size ───────────────────────────────────────────────────

CELL_SIZE     = 80          # pixels per grid unit (default)
CELL_SIZE_MIN = 32
CELL_SIZE_MAX = 120
GRID_UNITS    = 20          # default grid dimensions (20x20)
DEFAULT_ROOM_W = 10.0       # default room width in KotOR units
DEFAULT_ROOM_H = 10.0       # default room height


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
    connected_to: List[str] = field(default_factory=list)


@dataclass
class LYTData:
    """In-memory .lyt file."""
    rooms: List[RoomInstance] = field(default_factory=list)

    def to_text(self) -> str:
        lines = [
            "filedependency 0",
            f"roomcount {len(self.rooms)}",
        ]
        for r in self.rooms:
            lines.append(f"{r.mdl_name} {r.world_x:.2f} {r.world_y:.2f} {r.world_z:.2f}")
        lines += ["obstaclecount 0", "doorhookcount 0"]
        return "\n".join(lines) + "\n"


def _generate_vis(rooms: List[RoomInstance]) -> str:
    """
    Generate a .vis file from placed rooms.
    Rooms sharing a grid edge are mutually visible.
    """
    pos_map: Dict[Tuple[int, int], str] = {
        (r.grid_x, r.grid_y): r.mdl_name for r in rooms}
    lines = []
    for r in rooms:
        lines.append(r.mdl_name)
        visible: List[str] = [r.mdl_name]
        for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            neighbor = pos_map.get((r.grid_x + dx, r.grid_y + dy))
            if neighbor:
                visible.append(neighbor)
        lines.extend(visible)
        lines.append("")
    return "\n".join(lines)


# ── Draggable list widget ────────────────────────────────────────────────────

class _DragList(QListWidget):
    """
    QListWidget subclass where startDrag() is properly overridden.
    The override MUST be on the QListWidget itself — putting it on a
    parent QWidget has no effect because Qt calls startDrag() on the
    widget that owns the viewport, not on arbitrary parents.
    """
    def startDrag(self, supported_actions):
        item = self.currentItem()
        if not item:
            return
        mime = QMimeData()
        mime.setText(item.text())
        drag = QDrag(self)
        drag.setMimeData(mime)
        # Use CopyAction | MoveAction so the target can accept either
        drag.exec_(Qt.CopyAction | Qt.MoveAction, Qt.CopyAction)

    def mouseMoveEvent(self, event):
        # Ensure drag starts on left-button drag even if Qt threshold varies
        if event.buttons() & Qt.LeftButton:
            super().mouseMoveEvent(event)
        else:
            super().mouseMoveEvent(event)


# ── Room Grid Widget ──────────────────────────────────────────────────────

class RoomGridWidget(QWidget):
    """
    Interactive 2D top-down grid for room placement.
    Supports drag-drop, single-click placement, move, delete, zoom.
    """

    rooms_changed    = pyqtSignal()
    room_selected    = pyqtSignal(object)    # RoomInstance or None
    request_place_at = pyqtSignal(int, int)  # gx, gy — right-click place

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rooms: List[RoomInstance] = []
        self._selected: Optional[RoomInstance] = None
        self._grid_w = GRID_UNITS
        self._grid_h = GRID_UNITS
        self._cell = CELL_SIZE            # current zoom level
        self._hover_cell: Optional[Tuple[int, int]] = None  # cell under drag
        self._drag_room: Optional[RoomInstance] = None      # room being moved
        self._drag_start_pos: Optional[QPoint] = None

        self._update_size()
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self._font = QFont("Consolas", 7)

    def _update_size(self):
        w = self._grid_w * self._cell
        h = self._grid_h * self._cell
        self.setMinimumSize(w, h)
        self.setFixedSize(w, h)

    # ── Zoom ─────────────────────────────────────────────────────────────

    def zoom_in(self):
        self._cell = min(CELL_SIZE_MAX, self._cell + 8)
        self._update_size()
        self.update()

    def zoom_out(self):
        self._cell = max(CELL_SIZE_MIN, self._cell - 8)
        self._update_size()
        self.update()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            if event.angleDelta().y() > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            event.accept()
        else:
            super().wheelEvent(event)

    # ── Painting ──────────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#1a1a2e"))

        # Grid lines
        pen_minor = QPen(QColor("#2a2a4a"), 1)
        pen_major = QPen(QColor("#3a3a6a"), 1)
        for col in range(self._grid_w + 1):
            x = col * self._cell
            p.setPen(pen_major if col % 5 == 0 else pen_minor)
            p.drawLine(x, 0, x, self._grid_h * self._cell)
        for row in range(self._grid_h + 1):
            y = row * self._cell
            p.setPen(pen_major if row % 5 == 0 else pen_minor)
            p.drawLine(0, y, self._grid_w * self._cell, y)

        # Drop-target highlight
        if self._hover_cell is not None:
            hx, hy = self._hover_cell
            c = self._cell
            p.fillRect(hx * c, hy * c, c, c, QColor(80, 200, 120, 60))
            p.setPen(QPen(QColor("#50c878"), 2))
            p.drawRect(hx * c + 1, hy * c + 1, c - 2, c - 2)

        # Connection lines between adjacent rooms
        self._draw_connections(p)

        # Room cells
        for room in self._rooms:
            self._draw_room(p, room)

        p.end()

    def _draw_room(self, p: QPainter, room: RoomInstance):
        c = self._cell
        x = room.grid_x * c + 2
        y = room.grid_y * c + 2
        w = c - 4
        h = c - 4
        selected = room is self._selected
        p.fillRect(x, y, w, h,
                   QColor("#1e4a7a") if selected else QColor("#1a3a5a"))
        p.setPen(QPen(QColor("#4ec9b0") if selected else QColor("#3a7aaa"),
                      2 if selected else 1))
        p.drawRect(x, y, w, h)

        # Label
        p.setFont(self._font)
        p.setPen(QColor("#9cdcfe"))
        fm = QFontMetrics(self._font)
        text = room.mdl_name
        max_w = w - 8
        while fm.horizontalAdvance(text) > max_w and len(text) > 3:
            text = text[:-1]
        if text != room.mdl_name:
            text += "…"
        p.drawText(QRect(x + 4, y + 4, w - 8, h - 8),
                   Qt.AlignCenter | Qt.TextWordWrap, text)

        # Coord hint (only when cell is large enough)
        if c >= 48:
            p.setFont(QFont("Consolas", 6))
            p.setPen(QColor("#555555"))
            p.drawText(QRect(x + 2, y + h - 14, w - 4, 12),
                       Qt.AlignCenter, f"({room.grid_x},{room.grid_y})")

    def _draw_connections(self, p: QPainter):
        pos_map = {(r.grid_x, r.grid_y): r for r in self._rooms}
        c = self._cell
        pen = QPen(QColor("#4aaaff"), 1, Qt.DashLine)
        p.setPen(pen)
        drawn: Set[Tuple] = set()
        for room in self._rooms:
            cx = room.grid_x * c + c // 2
            cy = room.grid_y * c + c // 2
            for dx, dy in [(1, 0), (0, 1)]:
                nb = pos_map.get((room.grid_x + dx, room.grid_y + dy))
                if nb:
                    key = tuple(sorted([(room.grid_x, room.grid_y),
                                        (nb.grid_x, nb.grid_y)]))
                    if key not in drawn:
                        drawn.add(key)
                        nx = nb.grid_x * c + c // 2
                        ny = nb.grid_y * c + c // 2
                        p.drawLine(cx, cy, nx, ny)

    # ── Drag & Drop (from palette) ─────────────────────────────────────────

    def dragEnterEvent(self, event):
        if event.mimeData().hasText() or event.mimeData().hasFormat("text/plain"):
            event.setDropAction(Qt.CopyAction)
            event.accept()
            pos = event.pos()
            self._hover_cell = (max(0, min(pos.x() // self._cell, self._grid_w - 1)),
                                max(0, min(pos.y() // self._cell, self._grid_h - 1)))
            self.update()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasText() or event.mimeData().hasFormat("text/plain"):
            event.setDropAction(Qt.CopyAction)
            event.accept()
            pos = event.pos()
            self._hover_cell = (max(0, min(pos.x() // self._cell, self._grid_w - 1)),
                                max(0, min(pos.y() // self._cell, self._grid_h - 1)))
            self.update()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._hover_cell = None
        self.update()

    def dropEvent(self, event):
        self._hover_cell = None
        # Determine target cell
        pos = event.pos()
        gx = max(0, min(pos.x() // self._cell, self._grid_w - 1))
        gy = max(0, min(pos.y() // self._cell, self._grid_h - 1))

        # Check for internal move (dragging a placed room to new position)
        is_move = event.mimeData().hasFormat("application/x-gmodular-room-move")

        text = ""
        if event.mimeData().hasText():
            text = event.mimeData().text().strip()
        elif event.mimeData().hasFormat("text/plain"):
            text = bytes(event.mimeData().data("text/plain")).decode("utf-8", errors="replace").strip()

        if not text:
            event.ignore()
            self.update()
            return

        mdl_name = text
        placed = self._place_room(mdl_name, gx, gy)
        if not placed:
            # Cell was occupied — find nearest empty cell
            for radius in range(1, max(self._grid_w, self._grid_h)):
                for dx in range(-radius, radius + 1):
                    for dy in range(-radius, radius + 1):
                        nx, ny = gx + dx, gy + dy
                        if 0 <= nx < self._grid_w and 0 <= ny < self._grid_h:
                            if self._place_room(mdl_name, nx, ny):
                                action = Qt.MoveAction if is_move else Qt.CopyAction
                                event.setDropAction(action)
                                event.accept()
                                self.update()
                                return
        action = Qt.MoveAction if is_move else Qt.CopyAction
        event.setDropAction(action)
        event.accept()
        self.update()

    # ── Mouse (select / drag-to-move existing rooms) ─────────────────────

    def mousePressEvent(self, event):
        gx = event.x() // self._cell
        gy = event.y() // self._cell
        room = self._room_at(gx, gy)
        self._selected = room
        self.room_selected.emit(room)
        if room and event.button() == Qt.LeftButton:
            # Start a drag to move this room to another cell
            self._drag_room = room
            self._drag_start_pos = event.pos()
        else:
            self._drag_room = None
        self.update()

    def mouseMoveEvent(self, event):
        if (self._drag_room is not None
                and event.buttons() & Qt.LeftButton
                and self._drag_start_pos is not None):
            # Threshold: start drag after 4px movement
            delta = event.pos() - self._drag_start_pos
            if delta.manhattanLength() >= 4:
                mime = QMimeData()
                mime.setText(self._drag_room.mdl_name)
                # Tag as internal move so drop handler can remove the source
                mime.setData("application/x-gmodular-room-move",
                             f"{self._drag_room.grid_x},{self._drag_room.grid_y}".encode())
                drag = QDrag(self)
                drag.setMimeData(mime)
                # Remove from grid while dragging
                self._rooms.remove(self._drag_room)
                self.update()
                result = drag.exec_(Qt.MoveAction | Qt.CopyAction, Qt.MoveAction)
                # If drag was cancelled (not accepted), put the room back
                if result == Qt.IgnoreAction or drag.target() is None:
                    self._rooms.append(self._drag_room)
                    self.update()
                self._drag_room = None
                self._drag_start_pos = None

    def mouseReleaseEvent(self, event):
        self._drag_room = None
        self._drag_start_pos = None

    # ── Keyboard ──────────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            if self._selected:
                self._delete_room(self._selected)
        else:
            super().keyPressEvent(event)

    # ── Context menu ──────────────────────────────────────────────────────

    def _context_menu(self, pos: QPoint):
        gx = pos.x() // self._cell
        gy = pos.y() // self._cell
        room = self._room_at(gx, gy)
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#2d2d2d;color:#d4d4d4;border:1px solid #3c3c3c;}"
            "QMenu::item:selected{background:#3c3c3c;}"
        )
        if room:
            menu.addAction(f"\u2715  Remove '{room.mdl_name}'",
                           lambda: self._delete_room(room))
            menu.addSeparator()
            menu.addAction("Rename\u2026", lambda: self._rename_room(room))
        else:
            menu.addAction(
                "Place selected palette room here",
                lambda: self.request_place_at.emit(gx, gy)
            )
        menu.exec_(self.mapToGlobal(pos))

    # ── Helpers ───────────────────────────────────────────────────────────

    def _place_room(self, mdl_name: str, gx: int, gy: int) -> bool:
        """Place a room at (gx, gy). Returns False if cell occupied."""
        if self._room_at(gx, gy):
            return False
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
        log.info(f"Room placed: {mdl_name} at ({gx},{gy})")
        return True

    def _room_at(self, gx: int, gy: int) -> Optional[RoomInstance]:
        for r in self._rooms:
            if r.grid_x == gx and r.grid_y == gy:
                return r
        return None

    def _delete_room(self, room: RoomInstance):
        self._rooms.remove(room)
        if self._selected is room:
            self._selected = None
        self.rooms_changed.emit()
        self.room_selected.emit(None)
        self.update()

    def _rename_room(self, room: RoomInstance):
        name, ok = QInputDialog.getText(self, "Rename Room", "MDL name:",
                                        text=room.mdl_name)
        if ok and name.strip():
            room.mdl_name = name.strip().lower()
            self.rooms_changed.emit()
            self.update()

    # ── Public API ────────────────────────────────────────────────────────

    def place_room_by_name(self, mdl_name: str, gx: int, gy: int) -> bool:
        return self._place_room(mdl_name, gx, gy)

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


# ── Room Palette ─────────────────────────────────────────────────────────────

class RoomPaletteWidget(QWidget):
    """
    Left panel: list of available room MDL names.
    - Drag items from list to RoomGridWidget to place
    - Single-click in list then click 'Place' button
    - Double-click in list emits place_requested signal
    """

    place_requested = pyqtSignal(str)   # mdl_name — single-click place

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        hdr = QLabel("Room Palette")
        hdr.setStyleSheet("color:#4ec9b0; font-weight:bold; font-size:9pt;")
        layout.addWidget(hdr)

        hint = QLabel("Drag to grid  OR  select + click Place")
        hint.setStyleSheet("color:#666; font-size:7pt; font-style:italic;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search rooms\u2026")
        self._search.textChanged.connect(self._filter)
        self._search.setStyleSheet(
            "QLineEdit{background:#2d2d2d;color:#d4d4d4;"
            "border:1px solid #3c3c3c;padding:3px;font-size:8pt;}"
        )
        layout.addWidget(self._search)

        # Use _DragList so startDrag override actually fires
        self._list = _DragList()
        self._list.setDragEnabled(True)
        self._list.setDefaultDropAction(Qt.CopyAction)
        self._list.setStyleSheet(
            "QListWidget{background:#1e1e1e;color:#9cdcfe;"
            "border:1px solid #3c3c3c;}"
            "QListWidget::item{padding:3px;}"
            "QListWidget::item:selected{background:#2d4a6a;}"
            "QListWidget::item:hover{background:#253a4a;}"
        )
        self._list.setFont(QFont("Consolas", 8))
        # Double-click to place
        self._list.itemDoubleClicked.connect(
            lambda item: self.place_requested.emit(item.text())
        )
        layout.addWidget(self._list, 1)

        # Place button — click once in list, then click this
        self._place_btn = QPushButton("\u25bc  Place Selected Room")
        self._place_btn.setToolTip(
            "Select a room in the list above, then click here to place it\n"
            "at the next empty cell — or drag directly to the grid"
        )
        self._place_btn.setStyleSheet(
            "QPushButton{background:#0e639c;color:#fff;border:none;"
            "border-radius:3px;padding:6px;font-weight:bold;}"
            "QPushButton:hover{background:#1177bb;}"
        )
        self._place_btn.clicked.connect(self._on_place_btn)
        layout.addWidget(self._place_btn)

        self._all_rooms: List[str] = []

    def _on_place_btn(self):
        item = self._list.currentItem()
        if item:
            self.place_requested.emit(item.text())

    def set_rooms(self, room_names: List[str]):
        self._all_rooms = sorted(set(room_names))
        self._filter(self._search.text())

    def _filter(self, text: str):
        self._list.clear()
        text = text.lower()
        for name in self._all_rooms:
            if not text or text in name.lower():
                self._list.addItem(name)

    def current_room(self) -> Optional[str]:
        item = self._list.currentItem()
        return item.text() if item else None


# ── Room Assembly Panel ───────────────────────────────────────────────────────

class RoomAssemblyPanel(QWidget):
    """
    Full Room Assembly Grid panel.
    Left: palette  |  Center: scrollable grid  |  Right: details + actions
    """

    lyt_changed   = pyqtSignal(str)
    vis_changed   = pyqtSignal(str)
    rooms_changed = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._next_place_x = 0   # auto-advance cursor for Place button
        self._next_place_y = 0
        self._setup_ui()
        self._apply_theme()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Toolbar ──────────────────────────────────────────────────────
        toolbar = QWidget()
        toolbar.setFixedHeight(32)
        toolbar.setStyleSheet("background:#252526; border-bottom:1px solid #3c3c3c;")
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(6, 2, 6, 2)
        tb_layout.setSpacing(6)

        tb_layout.addWidget(QLabel("Room Assembly Grid"))

        tb_layout.addStretch()

        zoom_out_btn = QPushButton("\u2212")
        zoom_out_btn.setFixedSize(24, 24)
        zoom_out_btn.setToolTip("Zoom out grid (or Ctrl+scroll)")
        zoom_out_btn.clicked.connect(self._zoom_out)

        zoom_in_btn = QPushButton("+")
        zoom_in_btn.setFixedSize(24, 24)
        zoom_in_btn.setToolTip("Zoom in grid (or Ctrl+scroll)")
        zoom_in_btn.clicked.connect(self._zoom_in)

        for b in (zoom_out_btn, zoom_in_btn):
            b.setStyleSheet(
                "QPushButton{background:#3c3c3c;color:#d4d4d4;border:1px solid #555;"
                "border-radius:3px;font-size:11pt;font-weight:bold;}"
                "QPushButton:hover{background:#505050;}"
            )

        tb_layout.addWidget(QLabel("Zoom:"))
        tb_layout.addWidget(zoom_out_btn)
        tb_layout.addWidget(zoom_in_btn)

        clear_btn = QPushButton("Clear Grid")
        clear_btn.setStyleSheet(
            "QPushButton{background:#5a1a1a;color:#d4d4d4;border:1px solid #7a2a2a;"
            "border-radius:3px;padding:2px 8px;}"
            "QPushButton:hover{background:#7a2a2a;}"
        )
        clear_btn.clicked.connect(self._clear_grid)
        tb_layout.addWidget(clear_btn)

        save_btn = QPushButton("Save LYT + VIS\u2026")
        save_btn.setStyleSheet(
            "QPushButton{background:#1a5a1a;color:#d4d4d4;border:1px solid #2a7a2a;"
            "border-radius:3px;padding:2px 8px;font-weight:bold;}"
            "QPushButton:hover{background:#2a7a2a;}"
        )
        save_btn.clicked.connect(self._save_lyt_vis)
        tb_layout.addWidget(save_btn)

        root.addWidget(toolbar)

        # ── Main splitter: palette | grid | details ───────────────────────
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(4)
        splitter.setChildrenCollapsible(False)

        # Left: palette
        self._palette = RoomPaletteWidget()
        self._palette.setMinimumWidth(140)
        self._palette.setMaximumWidth(220)
        self._palette.place_requested.connect(self._place_from_palette)
        splitter.addWidget(self._palette)

        # Center: grid in scroll area
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setStyleSheet(
            "QScrollArea{background:#1a1a2e;border:none;}"
        )
        self._grid = RoomGridWidget()
        self._grid.rooms_changed.connect(self._on_rooms_changed)
        self._grid.room_selected.connect(self._on_room_selected)
        self._grid.request_place_at.connect(self._on_place_at_request)
        self._scroll.setWidget(self._grid)
        splitter.addWidget(self._scroll)

        # Right: details panel (using QFrame cards, no QGroupBox)
        details = QWidget()
        details.setMinimumWidth(150)
        details.setMaximumWidth(200)
        dl = QVBoxLayout(details)
        dl.setContentsMargins(6, 6, 6, 6)
        dl.setSpacing(6)

        # Selected room info card
        info_card = QFrame()
        info_card.setStyleSheet(
            "QFrame{border:1px solid #3c3c3c;border-radius:3px;background:#252526;}"
        )
        il = QVBoxLayout(info_card)
        il.setContentsMargins(8, 6, 8, 6)
        hdr1 = QLabel("Selected Room")
        hdr1.setStyleSheet("color:#dcdcaa;font-weight:bold;font-size:8pt;")
        il.addWidget(hdr1)
        self._detail_label = QLabel("None")
        self._detail_label.setStyleSheet("color:#9cdcfe;font-size:8pt;")
        self._detail_label.setWordWrap(True)
        il.addWidget(self._detail_label)
        dl.addWidget(info_card)

        # Stats card
        stats_card = QFrame()
        stats_card.setStyleSheet(
            "QFrame{border:1px solid #3c3c3c;border-radius:3px;background:#252526;}"
        )
        sl = QVBoxLayout(stats_card)
        sl.setContentsMargins(8, 6, 8, 6)
        hdr2 = QLabel("Grid Stats")
        hdr2.setStyleSheet("color:#dcdcaa;font-weight:bold;font-size:8pt;")
        sl.addWidget(hdr2)
        self._count_label = QLabel("Rooms: 0")
        self._count_label.setStyleSheet("color:#9cdcfe;font-size:8pt;")
        sl.addWidget(self._count_label)
        dl.addWidget(stats_card)

        # Export card
        exp_card = QFrame()
        exp_card.setStyleSheet(
            "QFrame{border:1px solid #3c3c3c;border-radius:3px;background:#252526;}"
        )
        el = QVBoxLayout(exp_card)
        el.setContentsMargins(8, 6, 8, 6)
        hdr3 = QLabel("Export")
        hdr3.setStyleSheet("color:#dcdcaa;font-weight:bold;font-size:8pt;")
        el.addWidget(hdr3)

        for label, slot in [
            ("Copy LYT Text",  self._copy_lyt),
            ("Copy VIS Text",  self._copy_vis),
        ]:
            b = QPushButton(label)
            b.setStyleSheet(
                "QPushButton{background:#2d2d2d;color:#d4d4d4;"
                "border:1px solid #3c3c3c;border-radius:2px;padding:3px;font-size:8pt;}"
                "QPushButton:hover{background:#3c3c3c;}"
            )
            b.clicked.connect(slot)
            el.addWidget(b)

        dl.addWidget(exp_card)
        dl.addStretch()
        splitter.addWidget(details)

        splitter.setSizes([160, 600, 160])
        root.addWidget(splitter, 1)

        # ── Status bar ────────────────────────────────────────────────────
        status = QLabel(
            "  Drag rooms from palette to grid  |  Double-click or use Place button  |  "
            "Right-click grid cell for options  |  Ctrl+scroll to zoom"
        )
        status.setStyleSheet(
            "background:#252526;color:#666;font-size:7pt;"
            "padding:2px 6px;border-top:1px solid #3c3c3c;"
        )
        status.setFixedHeight(18)
        root.addWidget(status)

    def _apply_theme(self):
        self.setStyleSheet(
            "RoomAssemblyPanel{background:#1a1a1a;}"
            "QLabel{color:#d4d4d4;}"
            "QSplitter::handle{background:#3c3c3c;}"
        )

    # ── Placement helpers ─────────────────────────────────────────────────

    def _place_from_palette(self, mdl_name: str):
        """Place the given room at the next available cell."""
        # Find next empty cell scanning left-to-right, top-to-bottom
        gw = self._grid._grid_w
        gh = self._grid._grid_h
        for gy in range(gh):
            for gx in range(gw):
                if not self._grid._room_at(gx, gy):
                    self._grid.place_room_by_name(mdl_name, gx, gy)
                    # Scroll to make it visible
                    c = self._grid._cell
                    self._scroll.ensureVisible(
                        gx * c + c // 2, gy * c + c // 2, 40, 40)
                    return

    def _on_place_at_request(self, gx: int, gy: int):
        """Right-click 'Place selected room here'."""
        room_name = self._palette.current_room()
        if room_name:
            self._grid.place_room_by_name(room_name, gx, gy)

    # ── Zoom ─────────────────────────────────────────────────────────────

    def _zoom_in(self):
        self._grid.zoom_in()

    def _zoom_out(self):
        self._grid.zoom_out()

    # ── Slots ─────────────────────────────────────────────────────────────

    def _on_rooms_changed(self):
        rooms = self._grid.get_rooms()
        self._count_label.setText(f"Rooms: {len(rooms)}")
        lyt = self._grid.generate_lyt()
        vis = self._grid.generate_vis_text()
        self.lyt_changed.emit(lyt.to_text())
        self.vis_changed.emit(vis)
        self.rooms_changed.emit(rooms)

    def _on_room_selected(self, room: Optional[RoomInstance]):
        if room is None:
            self._detail_label.setText("None")
        else:
            self._detail_label.setText(
                f"{room.mdl_name}\n"
                f"Grid ({room.grid_x}, {room.grid_y})\n"
                f"World ({room.world_x:.1f}, {room.world_y:.1f})"
            )

    def _clear_grid(self):
        reply = QMessageBox.question(
            self, "Clear Grid",
            "Remove all rooms from the grid?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._grid.clear()

    # ── Export ────────────────────────────────────────────────────────────

    def _save_lyt_vis(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Choose folder to save LYT + VIS", "")
        if not folder:
            return
        rooms = self._grid.get_rooms()
        base = rooms[0].mdl_name[:8] if rooms else "module"
        lyt_path = os.path.join(folder, base + ".lyt")
        vis_path = os.path.join(folder, base + ".vis")
        try:
            with open(lyt_path, "w") as f:
                f.write(self._grid.generate_lyt().to_text())
            with open(vis_path, "w") as f:
                f.write(self._grid.generate_vis_text())
            QMessageBox.information(
                self, "Saved",
                f"Saved:\n  {lyt_path}\n  {vis_path}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))

    def _copy_lyt(self):
        QApplication.clipboard().setText(self._grid.generate_lyt().to_text())

    def _copy_vis(self):
        QApplication.clipboard().setText(self._grid.generate_vis_text())

    # ── Public API ────────────────────────────────────────────────────────

    def set_available_rooms(self, room_names: List[str]):
        self._palette.set_rooms(room_names)

    def get_rooms(self) -> List[RoomInstance]:
        return self._grid.get_rooms()

    def get_lyt(self) -> LYTData:
        return self._grid.generate_lyt()

    def get_vis(self) -> str:
        return self._grid.generate_vis_text()
