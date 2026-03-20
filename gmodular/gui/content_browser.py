"""
GModular — Content Browser
===========================
Unreal Engine–style content browser for KotOR module assets.

Features
--------
- Tile / list view toggle (like UE5 Content Browser)
- Asset type filtering via left-side category tree
- Search with live filtering
- Drag-to-viewport support
- Preview icons per asset type (coloured icon tiles)
- "Favourites" pinning
- Recently used assets section
- Context-menu: Place, Duplicate template, Properties
- Game-directory scanning to populate dynamic asset pools
- Star-rating / favourites stored in memory
"""
from __future__ import annotations

import logging
import os
from typing import Optional, List, Dict, Set

log = logging.getLogger(__name__)

try:
    from qtpy.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
        QPushButton, QListWidget, QListWidgetItem, QTreeWidget,
        QTreeWidgetItem, QSplitter, QFrame, QScrollArea,
        QAbstractItemView, QSizePolicy, QMenu, QAction,
        QToolButton, QButtonGroup, QGridLayout, QApplication,
        QTabWidget, QComboBox, QInputDialog, QMessageBox,
    )
    from qtpy.QtCore import (
        Qt, Signal, QMimeData, QSize, QTimer, QPoint,
        QPropertyAnimation, QEasingCurve,
    )
    from qtpy.QtGui import (
        QFont, QColor, QBrush, QIcon, QPainter, QPixmap, QImage,
        QPen, QLinearGradient, QDrag, QCursor, QPoint,
    )
    _HAS_QT = True
except ImportError:
    _HAS_QT = False
    QWidget = object  # type: ignore[misc,assignment]

    class Signal:  # type: ignore[no-redef]
        def __init__(self, *a, **kw): pass
        def __set_name__(self, o, n): pass


# ── Asset definitions ──────────────────────────────────────────────────────────

ASSET_CATEGORIES = {
    "Placeables": {
        "icon_color": "#5588ff",
        "letter": "P",
        "type_key": "placeable",
        "subcategories": {
            "Containers": ["plc_cratemedium", "plc_cratelarge", "plc_cratesmall",
                           "plc_footlkr01", "plc_container01"],
            "Furniture": ["plc_chair01", "plc_bar", "plc_workbnch01"],
            "Electronics": ["plc_comp01", "plc_monitor01", "plc_datapad01"],
            "Medical": ["plc_medical"],
            "Misc": ["plc_holocrn01", "plc_corpse01", "plc_barrel01"],
        },
    },
    "Creatures": {
        "icon_color": "#ff8844",
        "letter": "C",
        "type_key": "creature",
        "subcategories": {
            "Humanoids": ["n_commoner01m", "n_commoner01f", "n_jediknight01",
                          "n_drkjedi01", "n_sandpeople", "n_tusken"],
            "Military": ["n_rpbsldur", "n_sthsldr01"],
            "Droids": ["c_drdastro", "c_drdhrk"],
            "Beasts": ["c_bantha", "c_rancor"],
        },
    },
    "Doors": {
        "icon_color": "#ffee44",
        "letter": "D",
        "type_key": "door",
        "subcategories": {
            "Metal": ["door_metal01", "door_metal02", "door_vault01"],
            "Wood": ["door_wood01", "door_rust01"],
            "Special": ["door_airlock", "door_hatch01"],
        },
    },
    "Waypoints": {
        "icon_color": "#cc44ff",
        "letter": "W",
        "type_key": "waypoint",
        "subcategories": {
            "All": ["wp_start", "wp_patrol01", "wp_spawn01", "wp_shopentry"],
        },
    },
    "Triggers": {
        "icon_color": "#44ffaa",
        "letter": "T",
        "type_key": "trigger",
        "subcategories": {
            "Transitions": ["trg_trans01"],
            "Traps": ["trg_trap01", "trg_trap02"],
            "Events": ["trg_generic01", "trg_cutscene01"],
        },
    },
    "Sounds": {
        "icon_color": "#44ffff",
        "letter": "S",
        "type_key": "sound",
        "subcategories": {
            "Ambient": ["as_an_forest1", "as_an_cave1", "as_an_space1",
                        "as_an_wind1", "as_mu_cantina1"],
        },
    },
    "Stores": {
        "icon_color": "#88ff88",
        "letter": "$",
        "type_key": "store",
        "subcategories": {
            "All": ["shop_general", "shop_weapons", "shop_armor",
                    "shop_medical", "shop_jawa01"],
        },
    },
}

# Flat display table mapping resref → (display_name, category, subcategory)
_ASSET_TABLE: Dict[str, tuple] = {}
for _cat_name, _cat_data in ASSET_CATEGORIES.items():
    for _sub_name, _refs in _cat_data["subcategories"].items():
        for _ref in _refs:
            _ASSET_TABLE[_ref] = (_ref.replace("_", " ").title(),
                                  _cat_name, _sub_name)


def _make_asset_icon(letter: str, color: str, size: int = 48) -> "QPixmap":
    """Render a UE5-style coloured tile icon with letter + subtle inner highlight."""
    px = QPixmap(size, size)
    px.fill(QColor(0, 0, 0, 0))
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)

    # Gradient background
    grad = QLinearGradient(0, 0, 0, size)
    base   = QColor(color)
    mid    = base.darker(140)
    dark   = base.darker(210)
    grad.setColorAt(0.0, base)
    grad.setColorAt(0.55, mid)
    grad.setColorAt(1.0, dark)
    p.setBrush(QBrush(grad))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(1, 1, size - 2, size - 2, int(size * 0.18), int(size * 0.18))

    # Inner top highlight — simulates a light from top
    highlight = QLinearGradient(0, 1, 0, size * 0.45)
    highlight.setColorAt(0.0, QColor(255, 255, 255, 55))
    highlight.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.setBrush(QBrush(highlight))
    p.drawRoundedRect(2, 2, size - 4, int(size * 0.50),
                      int(size * 0.14), int(size * 0.14))

    # Border
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor(255, 255, 255, 40), 1))
    p.drawRoundedRect(1, 1, size - 2, size - 2,
                      int(size * 0.18), int(size * 0.18))

    # Letter with subtle drop shadow
    font = QFont("Segoe UI", int(size * 0.42), QFont.Bold)
    p.setFont(font)
    p.setPen(QColor(0, 0, 0, 100))
    p.drawText(1, 1, size, size, Qt.AlignCenter, letter)
    p.setPen(QColor("#ffffffee"))
    p.drawText(0, 0, size, size, Qt.AlignCenter, letter)
    p.end()
    return px


# ─────────────────────────────────────────────────────────────────────────────
#  AssetItem data model
# ─────────────────────────────────────────────────────────────────────────────

class AssetItem:
    """Represents a single asset in the browser."""
    __slots__ = ("display_name", "resref", "template_resref", "asset_type",
                 "category", "subcategory", "description", "starred",
                 "file_path")

    def __init__(self, display_name: str, resref: str,
                 template_resref: str = "",
                 asset_type: str = "placeable",
                 category: str = "",
                 subcategory: str = "",
                 description: str = "",
                 file_path: str = ""):
        self.display_name    = display_name
        self.resref          = resref[:16]
        self.template_resref = (template_resref or resref)[:16]
        self.asset_type      = asset_type
        self.category        = category
        self.subcategory     = subcategory
        self.description     = description
        self.starred         = False
        self.file_path       = file_path   # absolute path on disk (may be empty)


# ─────────────────────────────────────────────────────────────────────────────
#  AssetTileWidget — single tile in the grid view
# ─────────────────────────────────────────────────────────────────────────────

class AssetTileWidget(QWidget):
    """A single asset tile — UE5-style icon + label, drag-and-drop capable."""

    clicked = Signal(object)          # AssetItem
    double_clicked = Signal(object)   # AssetItem

    _TILE_W, _TILE_H = 90, 100
    _ICON_SIZE       = 56

    def __init__(self, asset: AssetItem, icon_color: str, parent=None):
        super().__init__(parent)
        self.asset      = asset
        self._icon_color = icon_color
        self._selected  = False
        self._hovered   = False
        self.setFixedSize(self._TILE_W, self._TILE_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(
            f"<b>{asset.display_name}</b><br>"
            f"ResRef: <code>{asset.resref}</code><br>"
            f"Type: {asset.asset_type}<br>"
            f"Category: {asset.category} / {asset.subcategory}"
        )
        # For texture assets with a known file path, try to load a thumbnail.
        # Falls back to the generated letter icon if loading fails or no path.
        self._icon_pix = self._load_texture_thumbnail(
            asset, self._ICON_SIZE
        ) or _make_asset_icon(
            ASSET_CATEGORIES.get(asset.category, {}).get(
                "letter", "T" if asset.asset_type == "texture" else "?"),
            icon_color,
            self._ICON_SIZE
        )
        # Star overlay
        self._star_pix = _make_star_icon(16)

    @staticmethod
    def _load_texture_thumbnail(asset: "AssetItem", size: int) -> "Optional[QPixmap]":
        """
        Attempt to load a TGA/TPC thumbnail for a texture or room asset.

        For texture assets: loads the actual texture image.
        For room assets: generates a coloured schematic placeholder.

        Returns a QPixmap scaled to *size* x *size* on success, or None.
        """
        # ── Texture assets: load actual texture image ──────────────────────
        if asset.asset_type == "texture" and getattr(asset, "file_path", ""):
            fpath = asset.file_path
            if os.path.isfile(fpath):
                try:
                    if fpath.lower().endswith('.tga'):
                        return _tga_to_pixmap(fpath, size)
                    if fpath.lower().endswith('.tpc'):
                        return _tpc_to_pixmap(fpath, size)
                except Exception:
                    pass

        # ── Room model assets: generate colored MDL schematic ─────────────
        if asset.asset_type == "room" and _HAS_QT:
            try:
                # Create a distinctive room floor-plan style thumbnail
                px = QPixmap(size, size)
                px.fill(QColor(0, 0, 0, 0))
                p = QPainter(px)
                p.setRenderHint(QPainter.Antialiasing)
                # Dark background
                p.fillRect(0, 0, size, size, QColor("#0d1117"))
                # Room outline (schematic style)
                margin = int(size * 0.12)
                room_rect_size = size - 2 * margin
                p.setPen(QPen(QColor("#3b8beb"), 1.5))
                p.setBrush(QBrush(QColor("#1c2a3a")))
                p.drawRect(margin, margin, room_rect_size, room_rect_size)
                # Grid lines inside
                p.setPen(QPen(QColor("#1e3a5a"), 0.8))
                step = room_rect_size // 4
                for i in range(1, 4):
                    x = margin + i * step
                    y = margin + i * step
                    p.drawLine(x, margin, x, margin + room_rect_size)
                    p.drawLine(margin, y, margin + room_rect_size, y)
                # Door markers
                p.setPen(QPen(QColor("#f0a030"), 1.5))
                mid = size // 2
                # Bottom center door
                p.drawLine(mid - 4, size - margin, mid + 4, size - margin)
                # Center dot
                p.setBrush(QBrush(QColor("#3b8beb")))
                p.setPen(Qt.NoPen)
                p.drawEllipse(mid - 2, mid - 2, 4, 4)
                p.end()
                return px
            except Exception:
                pass

        return None

    def setSelected(self, selected: bool):
        self._selected = selected
        self.update()

    def isSelected(self) -> bool:
        return self._selected

    def enterEvent(self, e):
        self._hovered = True
        self.update()

    def leaveEvent(self, e):
        self._hovered = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.asset)
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.double_clicked.emit(self.asset)
        super().mouseDoubleClickEvent(e)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            drag = QDrag(self)
            mime = QMimeData()
            mime.setText(f"asset:{self.asset.asset_type}:{self.asset.resref}")
            drag.setMimeData(mime)
            drag.setPixmap(self._icon_pix.scaled(32, 32, Qt.KeepAspectRatio,
                                                  Qt.SmoothTransformation))
            drag.exec_(Qt.CopyAction)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        W, H = self.width(), self.height()

        # ── Background with gradient ────────────────────────────────────────
        if self._selected:
            bg_top    = QColor("#1a3a6a")
            bg_bot    = QColor("#0f2248")
            border    = QColor("#58a6ff")
            bw        = 2.0
        elif self._hovered:
            bg_top    = QColor("#252d3d")
            bg_bot    = QColor("#1c2230")
            border    = QColor("#4a6285")
            bw        = 1.5
        else:
            bg_top    = QColor("#1c2230")
            bg_bot    = QColor("#161b25")
            border    = QColor("#252d3d")
            bw        = 1.0

        from qtpy.QtGui import QLinearGradient
        grad = QLinearGradient(0, 0, 0, H)
        grad.setColorAt(0.0, bg_top)
        grad.setColorAt(1.0, bg_bot)
        p.setBrush(QBrush(grad))
        p.setPen(QPen(border, bw))
        p.drawRoundedRect(1, 1, W - 2, H - 2, 5, 5)

        # ── Icon — centred, with a subtle inner glow when hovered ───────────
        icon_x = (W - self._ICON_SIZE) // 2
        icon_y = 6
        if self._hovered or self._selected:
            # Draw a faint glow behind the icon
            glow_col = QColor(self._icon_color)
            glow_col.setAlpha(50 if self._selected else 30)
            p.setBrush(QBrush(glow_col))
            p.setPen(Qt.NoPen)
            gpad = 4
            p.drawRoundedRect(icon_x - gpad, icon_y - gpad,
                              self._ICON_SIZE + gpad*2, self._ICON_SIZE + gpad*2, 6, 6)
        p.drawPixmap(icon_x, icon_y, self._icon_pix)

        # ── Star badge ──────────────────────────────────────────────────────
        if self.asset.starred:
            p.drawPixmap(W - 18, 3, self._star_pix)

        # ── Label area ──────────────────────────────────────────────────────
        label_y = icon_y + self._ICON_SIZE + 5
        label_h = H - label_y - 3
        lbl_color = QColor("#dde4ee") if self._selected else (
                    QColor("#c0cad8") if self._hovered else QColor("#8a96a8"))
        p.setPen(lbl_color)
        font = QFont("Segoe UI", 7)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 0.2)
        p.setFont(font)
        text = self.asset.display_name
        fm = p.fontMetrics()
        text = fm.elidedText(text, Qt.ElideRight, W - 8)
        p.drawText(4, label_y, W - 8, label_h, Qt.AlignHCenter | Qt.AlignTop, text)

        # ── Type chip (tiny coloured dot bottom-right) ───────────────────────
        chip_col = QColor(self._icon_color)
        chip_col.setAlpha(200)
        p.setBrush(QBrush(chip_col))
        p.setPen(Qt.NoPen)
        p.drawEllipse(W - 9, H - 9, 5, 5)

        p.end()


def _tga_to_pixmap(path: str, size: int) -> "Optional[QPixmap]":
    """
    Load an uncompressed TGA file (24-bit or 32-bit) and return a
    QPixmap scaled to *size* × *size*.
    Returns None on any error.
    """
    import struct
    try:
        data = open(path, 'rb').read()
        if len(data) < 18:
            return None
        id_len   = data[0]
        img_type = data[2]
        w = struct.unpack_from('<H', data, 12)[0]
        h = struct.unpack_from('<H', data, 14)[0]
        bpp = data[16]
        descriptor = data[17]          # bit 5 = top-left origin
        if img_type not in (2, 3) or bpp not in (24, 32) or w == 0 or h == 0:
            return None
        off    = 18 + id_len
        stride = bpp // 8
        raw    = data[off: off + w * h * stride]

        # Try numpy fast path first
        try:
            import numpy as np
            arr  = np.frombuffer(raw, dtype=np.uint8).reshape(h * w, stride).copy()
            rgba = np.empty((h * w, 4), dtype=np.uint8)
            rgba[:, 0] = arr[:, 2]   # R ← B
            rgba[:, 1] = arr[:, 1]   # G
            rgba[:, 2] = arr[:, 0]   # B ← R
            rgba[:, 3] = arr[:, 3] if bpp == 32 else 255
            # Flip rows if bottom-origin (bit-5 of descriptor = 0)
            if not (descriptor & 0x20):
                rgba = rgba.reshape(h, w, 4)[::-1].reshape(h * w, 4)
            img_bytes = rgba.tobytes()
        except ImportError:
            # Pure-Python fallback
            px_count = w * h
            buf = bytearray(px_count * 4)
            for i in range(px_count):
                b, g, r = raw[i*stride], raw[i*stride+1], raw[i*stride+2]
                a = raw[i*stride+3] if bpp == 32 else 255
                buf[i*4:i*4+4] = bytes([r, g, b, a])
            if not (descriptor & 0x20):
                row_bytes = w * 4
                flipped = bytearray(px_count * 4)
                for row in range(h):
                    src = (h - 1 - row) * row_bytes
                    dst = row * row_bytes
                    flipped[dst:dst+row_bytes] = buf[src:src+row_bytes]
                buf = flipped
            img_bytes = bytes(buf)

        qi = QImage(img_bytes, w, h, w * 4, QImage.Format_RGBA8888)
        if qi.isNull():
            return None
        px = QPixmap.fromImage(qi)
        return px.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    except Exception:
        return None


def _tpc_to_pixmap(path: str, size: int) -> "Optional[QPixmap]":
    """
    Load a KotOR TPC file and return a QPixmap scaled to *size* × *size*.
    Uses the GModular TPCReader; returns None on any error.
    """
    try:
        from ..formats.tpc_reader import TPCReader
        tpc  = TPCReader.from_bytes(open(path, 'rb').read())
        rgba = tpc.to_rgba()            # bytes: width × height × 4 RGBA8
        w, h = tpc.width, tpc.height
        if w == 0 or h == 0:
            return None
        qi = QImage(rgba, w, h, w * 4, QImage.Format_RGBA8888)
        if qi.isNull():
            return None
        px = QPixmap.fromImage(qi)
        return px.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    except Exception:
        return None


def _make_star_icon(size: int) -> "QPixmap":
    px = QPixmap(size, size)
    px.fill(QColor(0, 0, 0, 0))
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QColor("#ffd700"))
    p.setBrush(QBrush(QColor("#ffd700")))
    import math
    cx, cy, r_out, r_in = size/2, size/2, size/2-1, size/4
    pts = []
    for i in range(10):
        angle = math.radians(i * 36 - 90)
        r = r_out if i % 2 == 0 else r_in
        pts.append(QPoint(int(cx + r * math.cos(angle)),
                           int(cy + r * math.sin(angle))))
    from qtpy.QtGui import QPolygon
    p.drawPolygon(QPolygon(pts))
    p.end()
    return px


# ─────────────────────────────────────────────────────────────────────────────
#  ContentBrowser main widget
# ─────────────────────────────────────────────────────────────────────────────

class ContentBrowser(QWidget):
    """
    Unreal Engine–style content browser.

    Left side  — folder/category tree
    Right side — tile grid (or list) of assets in the selected category
    Bottom     — path bar + status + view controls
    """

    place_asset = Signal(object)    # AssetItem — user wants to place this

    # ── View modes ──────────────────────────────────────────────────────────
    VIEW_TILES = "tiles"
    VIEW_LIST  = "list"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._view_mode        = self.VIEW_TILES
        self._all_assets: List[AssetItem] = []
        self._filtered: List[AssetItem]   = []
        self._selected_asset: Optional[AssetItem] = None
        self._tile_size        = 90   # adjustable via buttons (sm=72, md=90, lg=112)
        self._current_category = ""
        self._current_subcat   = ""
        self._starred_refs: Set[str] = set()
        self._recent_refs: List[str] = []  # most-recent first, max 20
        self._search_text      = ""
        self._status_text      = ""

        self._setup_ui()
        self._populate_defaults()
        # Start on "All" category
        self._select_category("All", "")

    # ── UI Construction ───────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Top toolbar ──────────────────────────────────────────────────────
        toolbar = self._build_toolbar()
        root.addWidget(toolbar)

        # ── Main splitter (tree | content area) ─────────────────────────────
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(2)
        splitter.setStyleSheet("QSplitter::handle { background:#2a2d30; }")

        # Left category tree
        self._cat_tree = self._build_category_tree()
        splitter.addWidget(self._cat_tree)

        # Right content area
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # Path / breadcrumb bar
        self._path_bar = self._build_path_bar()
        right_layout.addWidget(self._path_bar)

        # Asset area (tiles or list)
        self._content_scroll = QScrollArea()
        self._content_scroll.setWidgetResizable(True)
        self._content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._content_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._content_scroll.setStyleSheet(
            "QScrollArea { background:#16191f; border:none; }"
            "QScrollBar:vertical { background:#1e2230; width:10px; }"
            "QScrollBar::handle:vertical { background:#3a3e50; border-radius:4px; min-height:24px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }"
        )
        right_layout.addWidget(self._content_scroll, 1)

        # Status bar
        self._status_bar = self._build_status_bar()
        right_layout.addWidget(self._status_bar)

        splitter.addWidget(right)
        splitter.setSizes([170, 430])
        root.addWidget(splitter, 1)

    def _build_toolbar(self) -> QFrame:
        bar = QFrame()
        bar.setFixedHeight(34)
        bar.setStyleSheet(
            "QFrame { background:#1c1f27; border-bottom:1px solid #2a2d38; }"
        )
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(4)

        # Search
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("  Search assets…")
        self._search_edit.setFixedHeight(24)
        self._search_edit.setFont(QFont("Segoe UI", 8))
        self._search_edit.setStyleSheet(
            "QLineEdit { background:#0d1117; color:#c9d1d9; border:1px solid #30363d;"
            " border-radius:12px; padding:0 10px; font-size:8pt; }"
            "QLineEdit:focus { border:1px solid #58a6ff; background:#161b22; }"
        )
        self._search_edit.textChanged.connect(self._on_search)
        layout.addWidget(self._search_edit, 1)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet("QFrame { color:#2a2d38; }")
        layout.addWidget(sep)

        # View mode buttons
        self._btn_tiles = self._tool_btn("⊞", "Tile view", True)
        self._btn_list  = self._tool_btn("≡", "List view", False)
        self._btn_tiles.clicked.connect(lambda: self._set_view(self.VIEW_TILES))
        self._btn_list.clicked.connect(lambda:  self._set_view(self.VIEW_LIST))
        layout.addWidget(self._btn_tiles)
        layout.addWidget(self._btn_list)

        # Separator
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.VLine)
        sep2.setStyleSheet("QFrame { color:#2a2d38; }")
        layout.addWidget(sep2)

        # Place button
        self._place_btn = QPushButton("⊕  Place")
        self._place_btn.setFixedHeight(24)
        self._place_btn.setToolTip("Place selected asset in viewport (or double-click tile)")
        self._place_btn.setStyleSheet(
            "QPushButton { background:#238636; color:#fff; border:none; "
            "border-radius:4px; padding:0 12px; font-size:8pt; font-weight:bold; }"
            "QPushButton:hover { background:#2ea043; }"
            "QPushButton:pressed { background:#196127; }"
        )
        self._place_btn.clicked.connect(self._on_place_clicked)
        layout.addWidget(self._place_btn)

        # Custom ResRef
        self._custom_btn = QPushButton("+ Custom")
        self._custom_btn.setFixedHeight(24)
        self._custom_btn.setToolTip("Enter a custom ResRef to place")
        self._custom_btn.setStyleSheet(
            "QPushButton { background:#21262d; color:#c9d1d9; border:1px solid #30363d;"
            " border-radius:4px; padding:0 10px; font-size:8pt; }"
            "QPushButton:hover { background:#30363d; border-color:#58a6ff; }"
        )
        self._custom_btn.clicked.connect(self._on_custom_resref)
        layout.addWidget(self._custom_btn)

        return bar

    def _tool_btn(self, text: str, tooltip: str, checked: bool) -> QPushButton:
        b = QPushButton(text)
        b.setFixedSize(24, 24)
        b.setCheckable(True)
        b.setChecked(checked)
        b.setToolTip(tooltip)
        b.setFont(QFont("Segoe UI", 9))
        b.setStyleSheet(
            "QPushButton { background:#21262d; color:#8b949e; border:1px solid #30363d;"
            " border-radius:3px; }"
            "QPushButton:checked { background:#0d419d; color:#58a6ff; border-color:#1f6feb; }"
            "QPushButton:hover { background:#30363d; color:#c9d1d9; }"
        )
        return b

    def _build_category_tree(self) -> QTreeWidget:
        tree = QTreeWidget()
        tree.setHeaderHidden(True)
        tree.setColumnCount(1)
        tree.setIndentation(16)
        tree.setFont(QFont("Segoe UI", 8))
        tree.setStyleSheet("""
            QTreeWidget {
                background: #0d1117;
                color: #c9d1d9;
                border: none;
                border-right: 1px solid #21262d;
                outline: none;
            }
            QTreeWidget::item {
                height: 22px;
                padding-left: 2px;
                border-radius: 4px;
                margin: 1px 3px;
            }
            QTreeWidget::item:selected {
                background: #1f6feb22;
                color: #58a6ff;
                border: 1px solid #1f6feb44;
            }
            QTreeWidget::item:hover:!selected {
                background: #21262d;
            }
            QTreeWidget::branch {
                background: #0d1117;
            }
        """)
        tree.setSelectionMode(QAbstractItemView.SingleSelection)
        tree.itemClicked.connect(self._on_tree_item_clicked)

        # "All" item at top
        all_item = QTreeWidgetItem(tree)
        all_item.setText(0, "  ⊞  All Assets")
        all_item.setData(0, Qt.UserRole, ("All", ""))
        all_item.setFont(0, QFont("Segoe UI", 8, QFont.Bold))
        all_item.setForeground(0, QBrush(QColor("#58a6ff")))

        # Starred / Recent
        star_item = QTreeWidgetItem(tree)
        star_item.setText(0, "  ★  Starred")
        star_item.setData(0, Qt.UserRole, ("_starred", ""))
        star_item.setForeground(0, QBrush(QColor("#ffd700")))

        recent_item = QTreeWidgetItem(tree)
        recent_item.setText(0, "  ⟲  Recent")
        recent_item.setData(0, Qt.UserRole, ("_recent", ""))
        recent_item.setForeground(0, QBrush(QColor("#aaaaaa")))

        # Separator line
        sep_item = QTreeWidgetItem(tree)
        sep_item.setFlags(Qt.NoItemFlags)
        sep_item.setText(0, "")
        sep_item.setSizeHint(0, QSize(0, 6))

        # Category tree
        for cat_name, cat_data in ASSET_CATEGORIES.items():
            color = cat_data["icon_color"]
            letter = cat_data["letter"]
            cat_item = QTreeWidgetItem(tree)
            cat_item.setText(0, f"  {letter}  {cat_name}")
            cat_item.setData(0, Qt.UserRole, (cat_name, ""))
            cat_item.setForeground(0, QBrush(QColor(color)))
            cat_item.setFont(0, QFont("Segoe UI", 8, QFont.Bold))
            cat_item.setExpanded(False)

            for sub_name in cat_data["subcategories"]:
                sub_item = QTreeWidgetItem(cat_item)
                sub_item.setText(0, f"      {sub_name}")
                sub_item.setData(0, Qt.UserRole, (cat_name, sub_name))
                sub_item.setForeground(0, QBrush(QColor("#8b949e")))

        tree.setCurrentItem(all_item)
        return tree

    def _build_path_bar(self) -> QFrame:
        bar = QFrame()
        bar.setFixedHeight(26)
        bar.setStyleSheet(
            "QFrame { background:#13161d; border-bottom:1px solid #21262d; }"
        )
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(4)

        self._path_label = QLabel("All Assets")
        self._path_label.setStyleSheet(
            "color:#8b949e; font-size:7pt; font-family:Segoe UI;"
        )
        layout.addWidget(self._path_label)
        layout.addStretch()

        self._count_label = QLabel("")
        self._count_label.setStyleSheet("color:#484f58; font-size:7pt;")
        layout.addWidget(self._count_label)

        return bar

    def _build_status_bar(self) -> QFrame:
        bar = QFrame()
        bar.setFixedHeight(22)
        bar.setStyleSheet(
            "QFrame { background:#13161d; border-top:1px solid #21262d; }"
        )
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(6)

        self._status_label = QLabel("Ready")
        self._status_label.setStyleSheet("color:#58a6ff; font-size:7pt;")
        layout.addWidget(self._status_label, 1)

        # Tile size slider (simulate with buttons)
        size_lbl = QLabel("Size:")
        size_lbl.setStyleSheet("color:#484f58; font-size:7pt;")
        layout.addWidget(size_lbl)

        sm_btn = self._mini_btn("S", "Small tiles")
        md_btn = self._mini_btn("M", "Medium tiles")
        lg_btn = self._mini_btn("L", "Large tiles")
        sm_btn.clicked.connect(lambda: self._set_tile_size(72))
        md_btn.clicked.connect(lambda: self._set_tile_size(90))
        lg_btn.clicked.connect(lambda: self._set_tile_size(114))
        layout.addWidget(sm_btn)
        layout.addWidget(md_btn)
        layout.addWidget(lg_btn)

        return bar

    def _mini_btn(self, text: str, tip: str) -> QPushButton:
        b = QPushButton(text)
        b.setFixedSize(18, 18)
        b.setToolTip(tip)
        b.setFont(QFont("Segoe UI", 6))
        b.setStyleSheet(
            "QPushButton { background:#21262d; color:#8b949e; border:1px solid #30363d;"
            " border-radius:2px; }"
            "QPushButton:hover { background:#30363d; color:#c9d1d9; }"
        )
        return b

    # ── Asset population ──────────────────────────────────────────────────────

    def _populate_defaults(self):
        """Build the asset list from built-in definitions."""
        self._all_assets.clear()
        for cat_name, cat_data in ASSET_CATEGORIES.items():
            type_key = cat_data["type_key"]
            for sub_name, refs in cat_data["subcategories"].items():
                for ref in refs:
                    display = ref.replace("_", " ").title()
                    a = AssetItem(
                        display_name=display,
                        resref=ref,
                        template_resref=ref,
                        asset_type=type_key,
                        category=cat_name,
                        subcategory=sub_name,
                    )
                    self._all_assets.append(a)

    def populate_from_game(self, resrefs: List[str], asset_type: str = "placeable"):
        """Add game-directory ResRefs not already present."""
        # Map type_key back to category name
        type_to_cat = {v["type_key"]: k for k, v in ASSET_CATEGORIES.items()}
        cat = type_to_cat.get(asset_type, "Placeables")
        existing = {a.resref.lower() for a in self._all_assets}
        added = 0
        for ref in sorted(resrefs):
            if ref.lower() not in existing:
                display = ref.replace("_", " ").title()
                a = AssetItem(
                    display_name=display,
                    resref=ref,
                    template_resref=ref,
                    asset_type=asset_type,
                    category=cat,
                    subcategory="Game Assets",
                )
                self._all_assets.append(a)
                existing.add(ref.lower())
                added += 1
        if added > 0:
            self._status("Loaded", f"{added} {asset_type}s from game")
            self._refresh_content()

    # ── Category selection ────────────────────────────────────────────────────

    def _on_tree_item_clicked(self, item: "QTreeWidgetItem", col: int):
        data = item.data(0, Qt.UserRole)
        if data is None:
            return
        cat, sub = data
        self._select_category(cat, sub)

    def _select_category(self, cat: str, sub: str):
        self._current_category = cat
        self._current_subcat   = sub

        # Update path bar
        if cat == "All":
            self._path_label.setText("All Assets")
        elif cat == "_starred":
            self._path_label.setText("★  Starred")
        elif cat == "_recent":
            self._path_label.setText("⟲  Recently Used")
        elif sub:
            self._path_label.setText(f"{cat}  ›  {sub}")
        else:
            self._path_label.setText(cat)

        self._refresh_content()

    def _get_filtered_assets(self) -> List[AssetItem]:
        cat  = self._current_category
        sub  = self._current_subcat
        text = self._search_text.lower()

        if cat == "_starred":
            pool = [a for a in self._all_assets if a.starred]
        elif cat == "_recent":
            recent_refs = set(self._recent_refs)
            pool = [a for a in self._all_assets if a.resref in recent_refs]
            pool.sort(key=lambda a: self._recent_refs.index(a.resref)
                      if a.resref in self._recent_refs else 999)
        elif cat == "All":
            pool = list(self._all_assets)
        elif sub:
            pool = [a for a in self._all_assets
                    if a.category == cat and a.subcategory == sub]
        else:
            pool = [a for a in self._all_assets if a.category == cat]

        if text:
            pool = [a for a in pool
                    if text in a.display_name.lower() or text in a.resref.lower()
                    or text in a.subcategory.lower()]

        return pool

    # ── Content rendering ─────────────────────────────────────────────────────

    def _refresh_content(self):
        self._filtered = self._get_filtered_assets()
        self._count_label.setText(f"{len(self._filtered)} items")

        # Clear and rebuild content widget
        if self._view_mode == self.VIEW_TILES:
            self._render_tiles()
        else:
            self._render_list()

    def _render_tiles(self):
        container = QWidget()
        container.setStyleSheet("background:#16191f;")
        layout = QGridLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        tile_w = self._tile_size
        cols   = max(1, (self.width() - 190) // (tile_w + 8))

        for i, asset in enumerate(self._filtered):
            cat_data = ASSET_CATEGORIES.get(asset.category, {})
            icon_color = cat_data.get("icon_color", "#888888")
            tile = AssetTileWidget(asset, icon_color)
            tile.setFixedSize(tile_w, int(tile_w * 1.15))
            tile.clicked.connect(self._on_tile_clicked)
            tile.double_clicked.connect(self._on_tile_double_clicked)
            tile.setContextMenuPolicy(Qt.CustomContextMenu)
            tile.customContextMenuRequested.connect(
                lambda pos, a=asset, t=tile: self._on_tile_context(pos, a, t))
            if asset is self._selected_asset:
                tile.setSelected(True)
            row, col = divmod(i, cols)
            layout.addWidget(tile, row, col)

        # Filler
        layout.setColumnStretch(cols, 1)
        layout.setRowStretch(layout.rowCount(), 1)

        self._content_scroll.setWidget(container)

    def _render_list(self):
        lst = QListWidget()
        lst.setStyleSheet("""
            QListWidget {
                background: #0d1117;
                color: #c9d1d9;
                border: none;
                outline: none;
                font-family: Consolas;
                font-size: 8pt;
            }
            QListWidget::item {
                height: 24px;
                padding: 2px 8px;
                border-bottom: 1px solid #21262d;
            }
            QListWidget::item:selected {
                background: #1f6feb22;
                color: #58a6ff;
                border-left: 2px solid #1f6feb;
            }
            QListWidget::item:hover:!selected {
                background: #161b22;
            }
        """)
        lst.setSelectionMode(QAbstractItemView.SingleSelection)
        lst.itemClicked.connect(self._on_list_item_clicked)
        lst.itemDoubleClicked.connect(self._on_list_item_double_clicked)
        lst.setContextMenuPolicy(Qt.CustomContextMenu)
        lst.customContextMenuRequested.connect(self._on_list_context)

        for asset in self._filtered:
            cat_data = ASSET_CATEGORIES.get(asset.category, {})
            color    = cat_data.get("icon_color", "#888888")
            letter   = cat_data.get("letter", "?")
            star     = "★ " if asset.starred else "   "
            text     = f"  {star}{letter}  {asset.display_name}  —  {asset.resref}"
            item = QListWidgetItem(text)
            item.setForeground(QBrush(QColor(color)))
            item.setData(Qt.UserRole, asset)
            item.setToolTip(
                f"ResRef: {asset.resref}\nType: {asset.asset_type}\n"
                f"Category: {asset.category} / {asset.subcategory}"
            )
            lst.addItem(item)

        self._content_scroll.setWidget(lst)

    # ── Interaction ───────────────────────────────────────────────────────────

    def _on_tile_clicked(self, asset: AssetItem):
        self._selected_asset = asset
        self._status("Selected", asset.resref)
        # Deselect all other tiles
        w = self._content_scroll.widget()
        if w:
            for tile in w.findChildren(AssetTileWidget):
                tile.setSelected(tile.asset is asset)

    def _on_tile_double_clicked(self, asset: AssetItem):
        self._selected_asset = asset
        self._emit_place(asset)

    def _on_list_item_clicked(self, item: "QListWidgetItem"):
        asset = item.data(Qt.UserRole)
        if asset:
            self._selected_asset = asset
            self._status("Selected", asset.resref)

    def _on_list_item_double_clicked(self, item: "QListWidgetItem"):
        asset = item.data(Qt.UserRole)
        if asset:
            self._emit_place(asset)

    def _on_tile_context(self, pos: "QPoint", asset: AssetItem, tile: "AssetTileWidget"):
        self._selected_asset = asset
        self._show_context_menu(tile.mapToGlobal(pos), asset)

    def _on_list_context(self, pos: "QPoint"):
        lst = self._content_scroll.widget()
        if not isinstance(lst, QListWidget):
            return
        item = lst.itemAt(pos)
        if item is None:
            return
        asset = item.data(Qt.UserRole)
        if asset:
            self._selected_asset = asset
            self._show_context_menu(lst.viewport().mapToGlobal(pos), asset)

    def _show_context_menu(self, global_pos, asset: AssetItem):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #161b22;
                color: #c9d1d9;
                border: 1px solid #30363d;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                padding: 5px 20px 5px 10px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background: #1f6feb;
                color: white;
            }
            QMenu::separator {
                height: 1px;
                background: #21262d;
                margin: 3px 6px;
            }
        """)

        place_act = QAction(f"⊕  Place  '{asset.display_name}'", self)
        place_act.triggered.connect(lambda: self._emit_place(asset))
        menu.addAction(place_act)

        menu.addSeparator()

        star_label = "★  Remove from Starred" if asset.starred else "☆  Add to Starred"
        star_act = QAction(star_label, self)
        star_act.triggered.connect(lambda: self._toggle_star(asset))
        menu.addAction(star_act)

        info_act = QAction("ⓘ  Asset Info", self)
        info_act.triggered.connect(lambda: self._show_asset_info(asset))
        menu.addAction(info_act)

        menu.addSeparator()

        copy_act = QAction("⎘  Copy ResRef", self)
        copy_act.triggered.connect(lambda: QApplication.clipboard().setText(asset.resref))
        menu.addAction(copy_act)

        menu.exec_(global_pos)

    def _on_place_clicked(self):
        if self._selected_asset:
            self._emit_place(self._selected_asset)
        else:
            self._status("Select an asset first", "")

    def _on_custom_resref(self):
        resref, ok = QInputDialog.getText(
            self, "Custom ResRef",
            "Enter ResRef to place (max 16 chars):\n"
            "(e.g. plc_chair01, c_bantha, door_metal01)"
        )
        if ok and resref.strip():
            resref = resref.strip()[:16]
            a = AssetItem(resref, resref, resref, "placeable",
                          "Custom", "Custom")
            self._emit_place(a)

    def _emit_place(self, asset: AssetItem):
        # Record as recently used
        ref = asset.resref
        if ref in self._recent_refs:
            self._recent_refs.remove(ref)
        self._recent_refs.insert(0, ref)
        self._recent_refs = self._recent_refs[:20]

        self._status(f"Placing  {asset.resref}  — click in viewport", "")
        self.place_asset.emit(asset)

    def _toggle_star(self, asset: AssetItem):
        asset.starred = not asset.starred
        if asset.starred:
            self._starred_refs.add(asset.resref)
        else:
            self._starred_refs.discard(asset.resref)
        self._refresh_content()

    def _show_asset_info(self, asset: AssetItem):
        msg = QMessageBox(self)
        msg.setWindowTitle("Asset Info")
        msg.setStyleSheet(
            "QMessageBox { background:#161b22; color:#c9d1d9; }"
            "QLabel { color:#c9d1d9; }"
        )
        msg.setText(
            f"<b>{asset.display_name}</b><br><br>"
            f"ResRef: <code>{asset.resref}</code><br>"
            f"Type: {asset.asset_type}<br>"
            f"Category: {asset.category}<br>"
            f"Subcategory: {asset.subcategory}"
        )
        msg.exec_()

    def _on_search(self, text: str):
        self._search_text = text
        self._refresh_content()

    def _set_view(self, mode: str):
        self._view_mode = mode
        self._btn_tiles.setChecked(mode == self.VIEW_TILES)
        self._btn_list.setChecked(mode  == self.VIEW_LIST)
        self._refresh_content()

    def _set_tile_size(self, size: int):
        self._tile_size = size
        if self._view_mode == self.VIEW_TILES:
            self._refresh_content()

    def _status(self, msg: str, detail: str = ""):
        if detail:
            self._status_label.setText(f"{msg}:  {detail}")
        else:
            self._status_label.setText(msg)

    # ── Public API ────────────────────────────────────────────────────────────

    def clear_status(self):
        self._status_label.setText("Ready")

    def get_selected(self) -> Optional[AssetItem]:
        return self._selected_asset

    def populate_from_module(self, extract_dir: str = "", git=None, are=None):
        """
        Populate the content browser from a loaded module.

        Scans the extract directory for textures, MDL room models, and other
        resources, and populates them as browsable assets.  Also loads GIT
        objects (placeables, creatures, doors, waypoints) as placeable assets.

        Args:
            extract_dir: Path to the extracted module directory.
            git:         GITData object (optional) for GIT object population.
            are:         AREData object (optional) for area metadata.
        """
        # ── Populate GIT objects ────────────────────────────────────────────
        if git is not None:
            self._populate_from_git(git)

        # ── Scan extract_dir for textures and MDL assets ────────────────────
        if extract_dir and os.path.isdir(extract_dir):
            self._populate_from_extract_dir(extract_dir)

        self._refresh_content()
        n = len(self._all_assets)
        self._status(f"Module loaded — {n} assets")

    def _populate_from_git(self, git):
        """Add GIT objects (in-module instances) to the content browser."""
        type_map = {
            "placeables": ("placeable", "Placeables", "Module Instances"),
            "creatures":  ("creature",  "Creatures",  "Module Instances"),
            "doors":      ("door",      "Doors",      "Module Instances"),
            "waypoints":  ("waypoint",  "Waypoints",  "Module Instances"),
            "triggers":   ("trigger",   "Triggers",   "Module Instances"),
            "sounds":     ("sound",     "Sounds",     "Module Instances"),
            "stores":     ("store",     "Stores",     "Module Instances"),
        }
        existing = {a.resref.lower() for a in self._all_assets}
        added = 0
        for attr, (asset_type, cat_name, sub_name) in type_map.items():
            objects = getattr(git, attr, []) or []
            for obj in objects:
                # Use tag or template_resref as identifier
                resref = (getattr(obj, 'template_resref', '') or
                          getattr(obj, 'tag', '') or
                          getattr(obj, 'resref', '') or f'{asset_type}_instance').strip().lower()
                if not resref or resref in existing:
                    continue
                display = resref.replace('_', ' ').title()
                # Append position info if available
                pos = getattr(obj, 'position', None)
                if pos:
                    display = f"{display}  ({pos.x:.1f}, {pos.y:.1f})"
                a = AssetItem(
                    display_name=display,
                    resref=resref,
                    template_resref=resref,
                    asset_type=asset_type,
                    category=cat_name,
                    subcategory=sub_name,
                    description=f"From module GIT ({attr})",
                )
                self._all_assets.append(a)
                existing.add(resref)
                added += 1

        if added:
            log.debug("ContentBrowser: added %d GIT objects", added)

    def _populate_from_extract_dir(self, extract_dir: str):
        """
        Scan the extract directory for MDL/TGA/TPC assets and add them.

        - .mdl files → added as "Room Models" (category: Rooms)
        - .tga / .tpc files → added as "Textures" (category: Textures)
        - .uto / .utc / .utp / .utd files → blueprint templates
        """
        existing = {a.resref.lower() for a in self._all_assets}
        added = 0
        try:
            files = os.listdir(extract_dir)
        except OSError:
            return

        for fn in sorted(files):
            fn_lo = fn.lower()
            stem = os.path.splitext(fn_lo)[0]
            ext  = os.path.splitext(fn_lo)[1]

            if stem in existing:
                continue

            if ext == '.mdl':
                a = AssetItem(
                    display_name=stem.replace('_', ' ').title(),
                    resref=stem,
                    template_resref=stem,
                    asset_type="room",
                    category="Rooms",
                    subcategory="Module Rooms",
                    description=f"Room model from module: {fn}",
                    file_path=os.path.join(extract_dir, fn),
                )
                self._all_assets.append(a)
                existing.add(stem)
                added += 1

            elif ext in ('.tga', '.tpc'):
                # Only add if not already in existing (avoids duplicates for aliases)
                a = AssetItem(
                    display_name=stem.replace('_', ' ').title(),
                    resref=stem,
                    template_resref=stem,
                    asset_type="texture",
                    category="Textures",
                    subcategory="Module Textures",
                    description=f"Texture from module: {fn}",
                    file_path=os.path.join(extract_dir, fn),
                )
                self._all_assets.append(a)
                existing.add(stem)
                added += 1

            elif ext in ('.uto', '.utc', '.utp', '.utd', '.dft'):
                asset_type_map = {
                    '.uto': ('store', 'Stores'),
                    '.utc': ('creature', 'Creatures'),
                    '.utp': ('placeable', 'Placeables'),
                    '.utd': ('door', 'Doors'),
                    '.dft': ('placeable', 'Placeables'),
                }
                asset_type, cat_name = asset_type_map.get(ext, ('placeable', 'Placeables'))
                a = AssetItem(
                    display_name=stem.replace('_', ' ').title(),
                    resref=stem,
                    template_resref=stem,
                    asset_type=asset_type,
                    category=cat_name,
                    subcategory="Module Blueprints",
                    description=f"Blueprint from module: {fn}",
                )
                self._all_assets.append(a)
                existing.add(stem)
                added += 1

        if added:
            log.debug("ContentBrowser: added %d assets from %s", added, extract_dir)

        # Ensure Rooms and Textures categories are in the tree
        self._refresh_category_tree()

    def _refresh_category_tree(self):
        """Update the category tree to include dynamically added categories."""
        existing_cats = set()
        root = self._cat_tree.invisibleRootItem()
        for i in range(root.childCount()):
            item = root.child(i)
            data = item.data(0, Qt.UserRole)
            if data:
                existing_cats.add(data[0])

        # Dynamic categories from loaded assets
        dynamic_cats = {}
        for a in self._all_assets:
            cat = a.category
            sub = a.subcategory
            if cat not in ASSET_CATEGORIES and cat not in ('All', '_starred', '_recent'):
                if cat not in dynamic_cats:
                    dynamic_cats[cat] = set()
                if sub:
                    dynamic_cats[cat].add(sub)

        for cat_name, subcats in dynamic_cats.items():
            if cat_name in existing_cats:
                continue
            # Color by type
            color_map = {
                "Rooms":    "#44ddaa",
                "Textures": "#ddaa44",
            }
            color = color_map.get(cat_name, "#aaaaaa")
            cat_item = QTreeWidgetItem(self._cat_tree)
            cat_item.setText(0, f"  ◈  {cat_name}")
            cat_item.setData(0, Qt.UserRole, (cat_name, ""))
            cat_item.setForeground(0, QBrush(QColor(color)))
            cat_item.setFont(0, QFont("Segoe UI", 8, QFont.Bold))
            # Auto-expand Rooms and Textures so users see them immediately
            cat_item.setExpanded(cat_name in ("Rooms", "Textures"))
            for sub in sorted(subcats):
                sub_item = QTreeWidgetItem(cat_item)
                sub_item.setText(0, f"      {sub}")
                sub_item.setData(0, Qt.UserRole, (cat_name, sub))
                sub_item.setForeground(0, QBrush(QColor("#8b949e")))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Re-layout tiles on resize
        if self._view_mode == self.VIEW_TILES and self._filtered:
            QTimer.singleShot(50, self._refresh_content)
