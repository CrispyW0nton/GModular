"""
GModular — Main Application Window
Unreal Engine-style module editor for KotOR 1 & 2.

Layout (v2 — full suite integration):
  ┌──────────────┬───────────┬──────────────────────┬───────────────┐
  │  Asset       │  Scene    │                      │               │
  │  Palette     │  Outline  │   3D Viewport         │  Inspector    │
  │  (left)      │  (left2)  │   (center)            │  (right)      │
  │              │           │                      │               │
  ├──────────────┴───────────┴──────────────────────┴───────────────┤
  │  Tabs: Output Log | Walkmesh Editor | Area Properties           │
  └──────────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations
import json
import os
import sys
import logging
from pathlib import Path
from typing import Optional

from qtpy.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QLabel, QPushButton, QStatusBar, QMenuBar, QMenu, QAction,
    QFileDialog, QMessageBox, QToolBar, QPlainTextEdit, QFrame,
    QSizePolicy, QApplication, QInputDialog, QComboBox, QTabWidget,
    QDialog, QFormLayout, QLineEdit, QDialogButtonBox, QScrollArea,
    QStackedWidget,
)
from qtpy.QtCore import Qt, QSize, QTimer, Slot
from qtpy.QtGui import QIcon, QFont, QKeySequence

from .viewport        import ViewportWidget
from .inspector       import InspectorPanel
from .asset_palette   import AssetPalette, AssetItem
from .content_browser import ContentBrowser, AssetItem as CBAssetItem
from .scene_outline   import SceneOutlinePanel
from .walkmesh_editor import WalkmeshPanel
from ..core.module_state import (
    get_module_state, ModuleProject, ModuleState,
    PlaceObjectCommand, DeleteObjectCommand, MoveObjectCommand
)
from ..formats.gff_types import GITPlaceable, GITCreature, GITDoor, GITWaypoint, Vector3
from ..formats.archives  import get_resource_manager
from ..ipc.bridges       import GhostScripterBridge, GhostRiggerBridge, ProjectFileWatcher
try:
    from ..ipc.callback_server import GModularIPCServer
    _HAS_CALLBACK_SERVER = True
except ImportError:
    _HAS_CALLBACK_SERVER = False
from .app_controller import AppController

log = logging.getLogger(__name__)

APP_NAME    = "GModular"
APP_VERSION = "2.0.0"

# ─────────────────────────────────────────────────────────────────────────────
#  Welcome / Quick-Start Panel  (shown over viewport when no module is open)
# ─────────────────────────────────────────────────────────────────────────────

class WelcomePanel(QWidget):
    """
    Shown in the center when no module is open.
    Gives the user the three actions needed to start building.
    """

    new_module_requested  = None   # set by MainWindow after construction
    open_git_requested    = None
    open_room_requested   = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background:#1e1e1e;")
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(16)

        title = QLabel("GModular")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color:#4ec9b0; font-size:22pt; font-weight:bold;")
        layout.addWidget(title)

        sub = QLabel("KotOR Module Editor")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet("color:#569cd6; font-size:10pt;")
        layout.addWidget(sub)

        layout.addSpacing(20)

        # Card style using QFrame — no QGroupBox dependency
        card_style = (
            "QFrame { border:1px solid #3c3c3c; border-radius:4px; "
            "background:#252526; padding:4px; }"
        )
        hdr_style  = "color:#dcdcaa; font-weight:bold; font-size:9pt; padding-bottom:2px;"
        btn_style  = (
            "QPushButton { background:#0e639c; color:#ffffff; border:none; "
            "border-radius:4px; padding:8px 20px; font-size:10pt; font-weight:bold; }"
            "QPushButton:hover { background:#1177bb; }"
        )
        btn_style2 = (
            "QPushButton { background:#3a3a3a; color:#d4d4d4; border:1px solid #555; "
            "border-radius:4px; padding:8px 20px; font-size:9pt; }"
            "QPushButton:hover { background:#4a4a4a; }"
        )

        def _make_card(header_text):
            """Return (card_frame, inner_vbox_layout) using only QFrame+QLabel."""
            card = QFrame()
            card.setStyleSheet(card_style)
            vbox = QVBoxLayout(card)
            vbox.setContentsMargins(10, 8, 10, 8)
            vbox.setSpacing(6)
            hdr = QLabel(header_text)
            hdr.setStyleSheet(hdr_style)
            vbox.addWidget(hdr)
            return card, vbox

        # Step 1
        card1, g1l = _make_card("Step 1  \u2014  Set Your Game Directory")
        lbl1 = QLabel(
            "Go to  Tools \u203a Set Game Directory\n"
            "and point GModular at your KotOR install folder\n"
            "(the one containing chitin.key).")
        lbl1.setStyleSheet("color:#9cdcfe; font-size:8pt;")
        g1l.addWidget(lbl1)
        layout.addWidget(card1)

        # Step 2
        card2, g2l = _make_card("Step 2  \u2014  Create or Open a Module")
        row = QHBoxLayout()
        self._btn_new = QPushButton("New Module\u2026")
        self._btn_new.setStyleSheet(btn_style)
        self._btn_open_mod = QPushButton("\u2b07 Open .MOD File\u2026")
        self._btn_open_mod.setStyleSheet(btn_style)
        self._btn_open = QPushButton("Open .GIT File\u2026")
        self._btn_open.setStyleSheet(btn_style2)
        row.addWidget(self._btn_new)
        row.addSpacing(8)
        row.addWidget(self._btn_open_mod)
        row.addSpacing(8)
        row.addWidget(self._btn_open)
        row.addStretch()
        g2l.addLayout(row)
        note = QLabel(
            "\u2b07 Open .MOD imports a full KotOR module archive (.mod / .erf / .rim).  "
            "Open .GIT loads a loose GIT file.  New Module creates a blank module.")
        note.setStyleSheet("color:#666; font-size:7pt; margin-top:2px;")
        note.setWordWrap(True)
        g2l.addWidget(note)
        layout.addWidget(card2)

        # Step 3
        card3, g3l = _make_card("Step 3  \u2014  Assemble Rooms")
        desc = QLabel(
            "Click the  \u25b6 Room Grid  tab at the bottom of the screen.\n"
            "Select a room MDL from the palette on the left, then\n"
            "drag it onto the grid  \u2014  or right-click a grid cell to place it.\n"
            "Adjacent rooms are automatically connected in the .vis file.\n"
            "When done, click  Save LYT + VIS\u2026  to write the layout files.")
        desc.setStyleSheet("color:#9cdcfe; font-size:8pt;")
        desc.setWordWrap(True)
        g3l.addWidget(desc)
        self._btn_room = QPushButton("Open Room Grid Now")
        self._btn_room.setStyleSheet(btn_style2)
        g3l.addWidget(self._btn_room)
        layout.addWidget(card3)

        layout.addStretch()

    def connect_actions(self, new_cb, open_cb, room_cb, open_mod_cb=None):
        self._btn_new.clicked.connect(new_cb)
        self._btn_open.clicked.connect(open_cb)
        self._btn_room.clicked.connect(room_cb)
        if open_mod_cb and hasattr(self, '_btn_open_mod'):
            self._btn_open_mod.clicked.connect(open_mod_cb)


# ─────────────────────────────────────────────────────────────────────────────
#  New Module / Open File Dialogs
# ─────────────────────────────────────────────────────────────────────────────

class NewModuleDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Module")
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        form   = QFormLayout()

        self._name    = QLineEdit("MyModule")
        self._resref  = QLineEdit("mymod01")
        self._resref.setMaxLength(16)
        self._game    = QComboBox()
        self._game.addItems(["K1", "K2"])
        self._desc    = QLineEdit()

        form.addRow("Module Name:", self._name)
        form.addRow("ResRef (max 16):", self._resref)
        form.addRow("Game:", self._game)
        form.addRow("Description:", self._desc)
        layout.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def get_data(self) -> dict:
        return {
            "name":    self._name.text().strip() or "MyModule",
            "resref":  self._resref.text().strip()[:16] or "mymod01",
            "game":    self._game.currentText(),
            "desc":    self._desc.text().strip(),
        }


# ─────────────────────────────────────────────────────────────────────────────
#  Main Window
# ─────────────────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    """
    GModular primary window.
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME}  ·  v{APP_VERSION}")
        self.setMinimumSize(1200, 700)
        self.resize(1440, 860)
        self.setAcceptDrops(True)   # enable drag-drop of .mod / .git files

        self._state = get_module_state()
        self._rm    = get_resource_manager()
        self._game_dir: Optional[Path] = None
        self._extract_dir: str = ""   # last successful mod extract dir (for 2D↔3D sync)
        self._placement_active = False
        self._recent_files: list = []   # populated by _load_settings
        self._room_panel = None         # set by _build_bottom_tabs

        # Use-case coordinator (no Qt dependency — pure business logic)
        self._app_ctrl = AppController(self._state, self._rm)

        # IPC
        self._gs_bridge = GhostScripterBridge(self)
        self._gr_bridge = GhostRiggerBridge(self)
        self._file_watcher = ProjectFileWatcher(self)

        # Self-hosted callback server
        self._ipc_server = None
        if _HAS_CALLBACK_SERVER:
            self._ipc_server = GModularIPCServer(self)

        self._connect_ipc_signals()

        self._load_settings()
        self._setup_ui()
        self._setup_menus()
        self._setup_toolbar()
        self._setup_statusbar()
        self._apply_theme()

        # Start IPC after UI is ready
        self._gs_bridge.start()
        self._gr_bridge.start()
        if self._ipc_server:
            ok = self._ipc_server.start()
            if ok:
                self.log(f"🔌 GModular IPC callback server on port {self._ipc_server.port}")
            else:
                self.log("⚠ IPC callback server could not start (port in use?)")

        # Autosave status timer
        self._dirty_timer = QTimer(self)
        self._dirty_timer.setInterval(10000)
        self._dirty_timer.timeout.connect(self._check_dirty)
        self._dirty_timer.start()

        self.log("GModular initialized. Ready.")
        self.log(f"Version {APP_VERSION}  |  Ghostworks Pipeline")
        self.log("Suite: GModular (7003) ↔ GhostScripter (7002) ↔ GhostRigger (7001)")
        if self._game_dir:
            self.log(f"Game directory: {self._game_dir}")

        # ── Auto-show tutorial on first launch ────────────────────────────────
        if getattr(self, "_is_first_launch", True):
            QTimer.singleShot(800, lambda: self._open_tutorial(0))
            self._save_settings()   # mark seen

    # ── UI Setup ─────────────────────────────────────────────────────────────

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Outer horizontal splitter: left | center | right ─────────────────
        self._outer_splitter = QSplitter(Qt.Horizontal)
        self._outer_splitter.setHandleWidth(2)
        self._outer_splitter.setStyleSheet(
            "QSplitter::handle { background:#21262d; }"
            "QSplitter::handle:hover { background:#388bfd; }"
        )

        # ═══════════════════════════════════════════════════════════════════
        # LEFT PANEL — vertically split:
        #   Top:    World Outliner (scene tree)
        #   Bottom: Content Browser (UE-style asset browser)
        # ═══════════════════════════════════════════════════════════════════
        left_vsplitter = QSplitter(Qt.Vertical)
        left_vsplitter.setHandleWidth(2)
        left_vsplitter.setStyleSheet(
            "QSplitter::handle { background:#21262d; }"
        )
        left_vsplitter.setMinimumWidth(240)
        left_vsplitter.setMaximumWidth(340)

        # World Outliner (Scene Outline)
        self._scene_outline = SceneOutlinePanel()
        self._scene_outline.object_selected.connect(self._on_object_selected_from_outline)
        self._scene_outline.request_delete.connect(self._on_outline_delete)
        self._scene_outline.setMinimumHeight(160)
        left_vsplitter.addWidget(self._scene_outline)

        # Content Browser
        self._content_browser = ContentBrowser()
        self._content_browser.place_asset.connect(self._on_place_cb_asset)
        self._content_browser.setMinimumHeight(200)
        left_vsplitter.addWidget(self._content_browser)

        left_vsplitter.setSizes([350, 400])
        left_vsplitter.setStretchFactor(0, 1)
        left_vsplitter.setStretchFactor(1, 1)
        self._outer_splitter.addWidget(left_vsplitter)

        # Legacy palette (kept for compatibility, hidden)
        self._palette = AssetPalette()
        self._palette.place_asset.connect(self._on_place_asset)
        self._palette.hide()

        # ═══════════════════════════════════════════════════════════════════
        # CENTER — viewport header + stacked (Welcome / 3D Viewport) + bottom tabs
        # ═══════════════════════════════════════════════════════════════════
        center_widget = QWidget()
        center_layout = QVBoxLayout(center_widget)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)

        # 3D Viewport — create FIRST (viewport header references it)
        self._viewport = ViewportWidget()
        self._viewport.object_selected.connect(self._on_object_selected)
        self._viewport.object_placed.connect(self._on_object_placed)
        self._viewport.camera_moved.connect(self._on_camera_moved)
        self._viewport.play_mode_changed.connect(self._on_play_mode_changed)

        # Viewport header toolbar
        self._viewport_header = self._build_viewport_header()
        center_layout.addWidget(self._viewport_header)

        # Stacked: 0=Welcome, 1=Viewport
        self._center_stack = QStackedWidget()
        self._welcome_panel = WelcomePanel()
        self._welcome_panel.connect_actions(
            self.new_module, self.open_git, self._focus_room_tab,
            open_mod_cb=self.open_mod)
        self._center_stack.addWidget(self._welcome_panel)   # index 0
        self._center_stack.addWidget(self._viewport)         # index 1
        self._center_stack.setCurrentIndex(0)

        # Bottom tabs (Room Grid / Output / Walkmesh / Animation / Area / IFO)
        self._bottom_tabs = self._build_bottom_tabs()

        # Wire animation panel to viewport after both are constructed
        if getattr(self, '_anim_panel', None) is not None:
            try:
                self._anim_panel.set_viewport(self._viewport)
            except Exception as e:
                log.debug(f"Anim panel viewport wiring: {e}")

        self._center_vsplitter = QSplitter(Qt.Vertical)
        self._center_vsplitter.setHandleWidth(3)
        self._center_vsplitter.setChildrenCollapsible(False)
        self._center_vsplitter.setStyleSheet(
            "QSplitter::handle { background:#21262d; }"
            "QSplitter::handle:hover { background:#388bfd; }"
        )
        self._center_vsplitter.addWidget(self._center_stack)
        self._center_vsplitter.addWidget(self._bottom_tabs)
        self._center_vsplitter.setSizes([620, 180])
        self._center_vsplitter.setStretchFactor(0, 4)
        self._center_vsplitter.setStretchFactor(1, 1)
        center_layout.addWidget(self._center_vsplitter, stretch=1)

        self._outer_splitter.addWidget(center_widget)

        # ═══════════════════════════════════════════════════════════════════
        # RIGHT PANEL — Details / Inspector
        # ═══════════════════════════════════════════════════════════════════
        self._inspector = InspectorPanel()
        self._inspector.setMinimumWidth(230)
        self._inspector.setMaximumWidth(360)
        self._inspector.property_changed.connect(self._on_property_changed)
        self._inspector.request_patrol_click.connect(self._on_patrol_click_requested)
        self._inspector.patrol_path_changed.connect(self._on_patrol_path_changed)
        self._inspector.open_in_rigger.connect(self._on_open_in_rigger)
        self._inspector.set_state(self._state)
        self._outer_splitter.addWidget(self._inspector)

        self._patrol_placement_creature = None

        self._outer_splitter.setSizes([280, 900, 280])
        self._outer_splitter.setStretchFactor(0, 0)
        self._outer_splitter.setStretchFactor(1, 1)
        self._outer_splitter.setStretchFactor(2, 0)

        root.addWidget(self._outer_splitter)

        # Connect module state changes to viewport refresh
        self._state.on_change(self._on_module_changed)

    def _build_viewport_header(self) -> QWidget:
        """Build the UE-style viewport toolbar / header bar."""
        bar = QFrame()
        bar.setFixedHeight(34)
        bar.setStyleSheet(
            "QFrame { background:#1c1f27; border-bottom:1px solid #21262d; }"
        )
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(4)

        # ── Mode badge ──────────────────────────────────────────────────────
        self._mode_label = QLabel("EDIT")
        self._mode_label.setFixedHeight(22)
        self._mode_label.setStyleSheet(
            "QLabel { color:#4ec9b0; font-weight:bold; font-size:8pt;"
            " background:#1a3a2a; border:1px solid #2a5a3a;"
            " border-radius:3px; padding:0 8px; }"
        )
        layout.addWidget(self._mode_label)

        # ── Mode switcher ────────────────────────────────────────────────────
        self._mode_combo = QComboBox()
        self._mode_combo.addItem("⬜  Level Builder", "level_builder")
        self._mode_combo.addItem("✏  Module Editor", "module_editor")
        self._mode_combo.setFixedHeight(26)
        self._mode_combo.setFixedWidth(170)
        self._mode_combo.setToolTip(
            "Level Builder — assemble rooms + build new areas\n"
            "Module Editor — place & edit GIT objects, scripts, WOK"
        )
        self._mode_combo.setStyleSheet(
            "QComboBox { background:#0d1117; color:#c9d1d9;"
            " border:1px solid #30363d; border-radius:4px;"
            " padding:0 8px; font-size:8pt; font-weight:bold; }"
            "QComboBox:hover { background:#161b22; border-color:#58a6ff; }"
            "QComboBox::drop-down { border:none; width:16px; }"
            "QComboBox QAbstractItemView { background:#161b22; color:#c9d1d9;"
            " border:1px solid #30363d; selection-background-color:#1f6feb; }"
        )
        self._mode_combo.currentIndexChanged.connect(self._on_app_mode_changed)
        layout.addWidget(self._mode_combo)

        layout.addSpacing(4)

        # ── Module name ──────────────────────────────────────────────────────
        self._module_label = QLabel("No module loaded")
        self._module_label.setStyleSheet(
            "color:#484f58; font-size:8pt; font-family:Segoe UI;"
        )
        layout.addWidget(self._module_label)

        layout.addStretch()

        # ── Viewport quick-action buttons ────────────────────────────────────
        def _vp_btn(text: str, tip: str = "", checkable: bool = False,
                    checked: bool = False, color: str = "") -> QPushButton:
            b = QPushButton(text)
            b.setFixedHeight(26)
            b.setToolTip(tip)
            b.setCheckable(checkable)
            if checkable:
                b.setChecked(checked)
            if color:
                b.setStyleSheet(
                    f"QPushButton {{ background:{color}; color:white;"
                    f" border:none; border-radius:4px; padding:0 10px;"
                    f" font-size:8pt; font-weight:bold; }}"
                    f"QPushButton:hover {{ filter:brightness(1.2); }}"
                )
            else:
                b.setStyleSheet(
                    "QPushButton { background:#21262d; color:#8b949e;"
                    " border:1px solid #30363d; border-radius:4px;"
                    " padding:0 8px; font-size:8pt; }"
                    "QPushButton:hover { background:#30363d; color:#c9d1d9;"
                    " border-color:#8b949e; }"
                    "QPushButton:checked { background:#1f3a5f; color:#58a6ff;"
                    " border-color:#388bfd; }"
                )
            return b

        # Frame all
        frame_btn = _vp_btn("⊡ Frame All", "Fit camera to all objects (F)")
        frame_btn.clicked.connect(self._viewport.frame_all)
        layout.addWidget(frame_btn)

        # Walkmesh toggle
        wm_btn = _vp_btn("◼ Navmesh", "Toggle walkmesh overlay (W)",
                          checkable=True, checked=True)
        wm_btn.clicked.connect(lambda c: self._viewport.toggle_walkmesh(c))
        self._walkmesh_btn = wm_btn
        layout.addWidget(wm_btn)

        # Validate
        val_btn = _vp_btn("✓ Validate", "Run module validation")
        val_btn.clicked.connect(self._validate_module)
        layout.addWidget(val_btn)

        layout.addSpacing(4)

        # Save button (accent blue)
        save_btn = _vp_btn("💾 Save", "Save .GIT (Ctrl+S)", color="#1f6feb")
        save_btn.clicked.connect(self._save_module)
        layout.addWidget(save_btn)

        layout.addSpacing(8)

        # ── Play/Stop button (green/red) ─────────────────────────────────────
        self._play_btn = _vp_btn("▶  Play", "Start walk preview (P)",
                                  color="#238636")
        self._play_btn.clicked.connect(self._toggle_play_mode)
        layout.addWidget(self._play_btn)

        layout.addSpacing(4)

        # Help button
        help_btn = _vp_btn("?", "Open Tutorial (F1)")
        help_btn.setFixedWidth(28)
        help_btn.setStyleSheet(
            "QPushButton { background:#21262d; color:#8b949e;"
            " border:1px solid #30363d; border-radius:4px; font-size:9pt; }"
            "QPushButton:hover { background:#30363d; color:#58a6ff; }"
        )
        help_btn.clicked.connect(self._open_tutorial)
        layout.addWidget(help_btn)

        layout.addSpacing(8)

        # ── IPC status dots ──────────────────────────────────────────────────
        self._gs_dot = QLabel("●")
        self._gs_dot.setToolTip("GhostScripter IPC (port 7002)")
        self._gs_dot.setStyleSheet("color:#333a45; font-size:8pt; margin-left:4px;")
        layout.addWidget(self._gs_dot)

        self._gr_dot = QLabel("●")
        self._gr_dot.setToolTip("GhostRigger IPC (port 7001)")
        self._gr_dot.setStyleSheet("color:#333a45; font-size:8pt; margin-right:4px;")
        layout.addWidget(self._gr_dot)

        return bar

    def _build_bottom_tabs(self) -> QTabWidget:
        """Build the bottom panel with Output Log, Room Grid, Walkmesh, and Area Properties tabs."""
        tabs = QTabWidget()
        tabs.setMinimumHeight(120)
        tabs.setFont(QFont("Segoe UI", 8))
        tabs.setStyleSheet(
            "QTabWidget::pane {"
            "  border-top: 2px solid #1f6feb;"
            "  background: #0d1117;"
            "}"
            "QTabWidget::tab-bar { left: 0px; }"
            "QTabBar::tab {"
            "  background: #161b22;"
            "  color: #8b949e;"
            "  padding: 5px 14px;"
            "  border: 1px solid #21262d;"
            "  border-bottom: none;"
            "  font-size: 8pt;"
            "  margin-right: 1px;"
            "  min-width: 80px;"
            "}"
            "QTabBar::tab:selected {"
            "  background: #0d1117;"
            "  color: #e6edf3;"
            "  border-top: 2px solid #1f6feb;"
            "  border-left: 1px solid #30363d;"
            "  border-right: 1px solid #30363d;"
            "  font-weight: bold;"
            "}"
            "QTabBar::tab:hover:!selected {"
            "  background: #21262d;"
            "  color: #c9d1d9;"
            "}"
            "QTabBar::tab:first {"
            "  color: #4ec9b0;"
            "  font-weight: bold;"
            "}"
        )

        # ── Room Assembly Grid tab ─────────────────────────────────────────
        try:
            from .room_assembly import RoomAssemblyPanel
            self._room_panel = RoomAssemblyPanel()
            self._room_panel.lyt_changed.connect(
                lambda t: self.log(f"LYT updated ({len(t.splitlines())} lines)"))
            # ── Connect rooms_changed → viewport so 3-D scene updates live ──
            self._room_panel.rooms_changed.connect(self._on_rooms_changed_in_grid)
            # Populate with game rooms if available
            try:
                rm = get_resource_manager()
                room_names = [r for r in rm.list_resources("mdl")
                              if len(r) > 4 and not r.startswith("c_")
                              and not r.startswith("p_")]
                self._room_panel.set_available_rooms(room_names[:300])
            except Exception:
                # Fallback demo rooms so panel is usable before game dir is set
                self._room_panel.set_available_rooms([
                    "manm26aa", "manm26ab", "manm26ac", "manm26ad",
                    "manm26ba", "manm26bb", "manm26bc",
                    "tarc_m17aa", "tarc_m17ab", "tarc_m17ba",
                    "tar_m02aa", "tar_m02ab", "tar_m02ba",
                    "danm14aa", "danm14ab", "danm14ba",
                ])
            tabs.addTab(self._room_panel, "\u25b6 Room Grid")
        except Exception as e:
            log.warning(f"Room Assembly unavailable: {e}")
            self._room_panel = None
            tabs.addTab(QLabel("Room Grid unavailable"), "\u25b6 Room Grid")

        # ── Output Log tab ─────────────────────────────────────────────────
        log_widget = QWidget()
        log_layout = QVBoxLayout(log_widget)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(0)

        log_hdr = QFrame()
        log_hdr.setFixedHeight(22)
        log_hdr.setStyleSheet("background:#252526;")
        log_hdr_layout = QHBoxLayout(log_hdr)
        log_hdr_layout.setContentsMargins(8, 0, 8, 0)
        log_hdr_layout.addStretch()
        clear_btn = QPushButton("Clear")
        clear_btn.setFixedSize(46, 18)
        clear_btn.setStyleSheet(
            "QPushButton{background:#3c3c3c;color:#969696;border:1px solid #555;"
            "border-radius:2px;font-size:7pt;padding:0;}"
            "QPushButton:hover{background:#4a4a4a;color:#cccccc;}"
        )
        clear_btn.clicked.connect(lambda: self._output_log.clear())
        log_hdr_layout.addWidget(clear_btn)
        log_layout.addWidget(log_hdr)

        self._output_log = QPlainTextEdit()
        self._output_log.setReadOnly(True)
        self._output_log.setMaximumBlockCount(800)
        self._output_log.setFont(QFont("Consolas", 8))
        self._output_log.setStyleSheet(
            "QPlainTextEdit {"
            "  background: #0d1117;"
            "  color: #e6edf3;"
            "  border: none;"
            "  selection-background-color: #1f3a5f;"
            "}"
            "QScrollBar:vertical {"
            "  background: #161b22;"
            "  width: 10px;"
            "  border: none;"
            "}"
            "QScrollBar::handle:vertical {"
            "  background: #30363d;"
            "  border-radius: 4px;"
            "  min-height: 20px;"
            "}"
            "QScrollBar::handle:vertical:hover { background: #484f58; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }"
        )
        log_layout.addWidget(self._output_log)
        tabs.addTab(log_widget, "Output Log")

        # ── Walkmesh Editor tab ────────────────────────────────────────────
        self._walkmesh_panel = WalkmeshPanel()
        self._walkmesh_panel.wok_loaded.connect(self._on_wok_loaded)
        tabs.addTab(self._walkmesh_panel, "Walkmesh (WOK)")

        # ── Animation Timeline tab ─────────────────────────────────────────
        try:
            from .animation_panel import AnimationTimelinePanel
            self._anim_panel = AnimationTimelinePanel()
            tabs.addTab(self._anim_panel, "🎬 Animation")
        except Exception as e:
            log.warning(f"Animation panel unavailable: {e}")
            self._anim_panel = None
            tabs.addTab(QLabel("Animation panel unavailable"), "🎬 Animation")

        # ── Area Properties tab ────────────────────────────────────────────
        area_widget = self._build_area_props_tab()
        tabs.addTab(area_widget, "Area Properties")

        # ── IFO / Module Properties tab ────────────────────────────────────
        ifo_widget = self._build_ifo_tab()
        tabs.addTab(ifo_widget, "Module IFO")

        return tabs

    def _build_area_props_tab(self) -> QWidget:
        """Area .ARE properties quick editor."""
        widget = QScrollArea()
        widget.setWidgetResizable(True)
        widget.setStyleSheet("QScrollArea { border:none; background:#1e1e1e; }")

        content = QWidget()
        form = QFormLayout(content)
        form.setContentsMargins(12, 8, 12, 8)
        form.setSpacing(4)
        form.setLabelAlignment(Qt.AlignRight)

        def _lbl(text):
            l = QLabel(text)
            l.setStyleSheet("color:#969696; font-size:8pt;")
            return l

        def _val(text):
            l = QLabel(str(text))
            l.setFont(QFont("Consolas", 8))
            l.setStyleSheet("color:#9cdcfe;")
            return l

        self._are_tag_lbl      = _val("-")
        self._are_name_lbl     = _val("-")
        self._are_rooms_lbl    = _val("-")
        self._are_tileset_lbl  = _val("-")
        self._are_skybox_lbl   = _val("-")
        self._are_fog_lbl      = _val("-")
        self._are_ambient_lbl  = _val("-")

        form.addRow(_lbl("Tag:"),          self._are_tag_lbl)
        form.addRow(_lbl("Name:"),         self._are_name_lbl)
        form.addRow(_lbl("Room count:"),   self._are_rooms_lbl)
        form.addRow(_lbl("Tileset:"),      self._are_tileset_lbl)
        form.addRow(_lbl("Skybox:"),       self._are_skybox_lbl)
        form.addRow(_lbl("Fog:"),          self._are_fog_lbl)
        form.addRow(_lbl("Ambient:"),      self._are_ambient_lbl)

        widget.setWidget(content)
        return widget

    def _build_log_panel(self) -> QFrame:
        """Legacy — kept for backward compatibility."""
        frame = QFrame()
        frame.setFixedHeight(0)   # Hidden; bottom tabs used instead
        return frame

    def _build_ifo_tab(self) -> QWidget:
        """Module .IFO properties editor (entry area, scripts).

        All editable string fields push a ModifyPropertyCommand onto the undo
        stack via editingFinished so IFO changes integrate with undo/redo.
        Read-only fields (entry position, counts) remain as QLabels.
        """
        widget = QScrollArea()
        widget.setWidgetResizable(True)
        widget.setStyleSheet("QScrollArea { border:none; background:#1e1e1e; }")

        content = QWidget()
        form = QFormLayout(content)
        form.setContentsMargins(12, 8, 12, 8)
        form.setSpacing(4)
        form.setLabelAlignment(Qt.AlignRight)

        _field_style = (
            "QLineEdit { background:#3c3c3c; color:#9cdcfe; border:1px solid #555; "
            "border-radius:2px; padding:1px 4px; font-family:Consolas; font-size:8pt; }"
            "QLineEdit:focus { border:1px solid #007acc; }"
        )

        def _lbl(text):
            l = QLabel(text)
            l.setStyleSheet("color:#969696; font-size:8pt;")
            return l

        def _val(text=""):
            l = QLabel(str(text))
            l.setFont(QFont("Consolas", 8))
            l.setStyleSheet("color:#9cdcfe;")
            return l

        def _edit(text="", max_len=0):
            e = QLineEdit(str(text))
            e.setFont(QFont("Consolas", 8))
            e.setStyleSheet(_field_style)
            if max_len:
                e.setMaxLength(max_len)
            return e

        # Editable IFO fields
        self._ifo_name_edit    = _edit()
        self._ifo_desc_edit    = _edit()
        self._ifo_area_edit    = _edit(max_len=16)
        self._ifo_pos_lbl      = _val("-")          # read-only (position needs 3 spins)
        self._ifo_onload_edit  = _edit(max_len=16)
        self._ifo_onstart_edit = _edit(max_len=16)
        self._ifo_onenter_edit = _edit(max_len=16)
        self._ifo_onleave_edit = _edit(max_len=16)
        self._ifo_onhb_edit    = _edit(max_len=16)
        self._ifo_ondeath_edit = _edit(max_len=16)

        form.addRow(_lbl("Module Name:"),      self._ifo_name_edit)
        form.addRow(_lbl("Description:"),      self._ifo_desc_edit)
        form.addRow(_lbl("Entry Area:"),       self._ifo_area_edit)
        form.addRow(_lbl("Entry Position:"),   self._ifo_pos_lbl)
        form.addRow(_lbl("On Module Load:"),   self._ifo_onload_edit)
        form.addRow(_lbl("On Module Start:"),  self._ifo_onstart_edit)
        form.addRow(_lbl("On Client Enter:"),  self._ifo_onenter_edit)
        form.addRow(_lbl("On Client Leave:"),  self._ifo_onleave_edit)
        form.addRow(_lbl("On Heartbeat:"),     self._ifo_onhb_edit)
        form.addRow(_lbl("On Player Death:"),  self._ifo_ondeath_edit)

        # Wire edits — push ModifyPropertyCommand to the IFO object
        def _wire_ifo(widget, attr):
            def on_finished():
                ifo = self._state.ifo
                if ifo is None:
                    return
                old = getattr(ifo, attr, "")
                new = widget.text().strip()
                if old == new:
                    return
                try:
                    from ..core.module_state import ModifyPropertyCommand
                    cmd = ModifyPropertyCommand(ifo, attr, old, new)
                    self._state.execute(cmd)
                    self.log(f"  IFO edit: {attr} = {new!r}")
                except Exception as e:
                    setattr(ifo, attr, new)
                    log.debug(f"IFO edit fallback: {e}")
            widget.editingFinished.connect(on_finished)

        _wire_ifo(self._ifo_name_edit,    "mod_name")
        _wire_ifo(self._ifo_desc_edit,    "mod_description")
        _wire_ifo(self._ifo_area_edit,    "entry_area")
        _wire_ifo(self._ifo_onload_edit,  "on_module_load")
        _wire_ifo(self._ifo_onstart_edit, "on_module_start")
        _wire_ifo(self._ifo_onenter_edit, "on_client_enter")
        _wire_ifo(self._ifo_onleave_edit, "on_client_leave")
        _wire_ifo(self._ifo_onhb_edit,    "on_heartbeat")
        _wire_ifo(self._ifo_ondeath_edit, "on_player_death")

        widget.setWidget(content)
        return widget

    # ── Menus ─────────────────────────────────────────────────────────────────

    def _setup_menus(self):
        mb = self.menuBar()

        # File
        fm = mb.addMenu("File")
        fm.addAction(self._action("New Module…",           self.new_module,  "Ctrl+Shift+N"))
        fm.addAction(self._action("Open Module (.mod/.erf)…", self.open_mod, "Ctrl+O"))
        fm.addAction(self._action("Open GIT File…",          self.open_git,    "Ctrl+Shift+O"))
        fm.addAction(self._action("Open Project…",           self.open_project))
        fm.addSeparator()
        fm.addAction(self._action("Save GIT",       self._save_module, "Ctrl+S"))
        fm.addAction(self._action("Save GIT As…",   self._save_as))
        fm.addSeparator()

        # Recent Files submenu
        self._recent_menu = fm.addMenu("Recent Files")
        self._recent_files: list = []
        self._rebuild_recent_menu()
        fm.addSeparator()

        fm.addAction(self._action("Exit",            self.close, "Alt+F4"))

        # Edit
        em = mb.addMenu("Edit")
        em.addAction(self._action("Undo",   self._undo, "Ctrl+Z"))
        em.addAction(self._action("Redo",   self._redo, "Ctrl+Y"))
        em.addSeparator()
        em.addAction(self._action("Delete Selected", self._viewport._delete_selected, "Delete"))
        em.addAction(self._action("Frame All",        self._viewport.frame_all, "F"))
        em.addSeparator()
        em.addAction(self._action("Validate Module",  self._validate_module))

        # View
        vm = mb.addMenu("View")
        vm.addAction(self._action("Frame All Objects",  self._viewport.frame_all))
        vm.addAction(self._action("Frame Selected",     self._viewport.frame_selected))

        # Module
        mm = mb.addMenu("Module")
        mm.addAction(self._action("Module Properties…", self._show_module_props))
        mm.addSeparator()
        mm.addAction(self._action("Import Module Archive…", self.open_mod, "Ctrl+I"))
        mm.addSeparator()
        mm.addAction(self._action("Validate",      self._validate_module))
        mm.addAction(self._action("Validate Module (Full Report)", self._open_validation_report))
        mm.addSeparator()
        mm.addAction(self._action("Pack Module (.MOD)...", self._open_mod_packager))
        mm.addSeparator()
        mm.addAction(self._action("Export .GIT",   self._save_module))
        mm.addSeparator()
        mm.addAction(self._action("Room Assembly Grid", self._open_room_assembly))

        # Tools
        tm = mb.addMenu("Tools")
        tm.addAction(self._action("🎮 Set Game Directory…", self._set_game_dir))
        tm.addAction(self._action("Load Assets from Game",   self._load_game_assets))
        tm.addSeparator()
        tm.addAction(self._action("GhostScripter IPC Status", self._show_ipc_status))
        tm.addAction(self._action("GhostRigger IPC Status",   self._show_ipc_status))

        # Help
        hm = mb.addMenu("Help")
        hm.addAction(self._action("📖 Interactive Tutorial…",  self._open_tutorial,   "F1"))
        hm.addAction(self._action("How To Build a Module",      self._print_howto_guide))
        hm.addSeparator()
        hm.addAction(self._action("🏗  Room Grid Guide",   lambda: self._open_tutorial(3)))
        hm.addAction(self._action("🎥  Viewport Controls", lambda: self._open_tutorial(5)))
        hm.addAction(self._action("📦  Module Packager",   lambda: self._open_tutorial(10)))
        hm.addSeparator()
        hm.addAction(self._action("About GModular", self._show_about))


    def _action(self, text: str, slot, shortcut: str = "") -> QAction:
        act = QAction(text, self)
        act.triggered.connect(slot)
        if shortcut:
            act.setShortcut(QKeySequence(shortcut))
        return act

    # ── Toolbar ───────────────────────────────────────────────────────────────

    def _setup_toolbar(self):
        """UE5-style main toolbar with grouped actions."""
        tb = QToolBar("Main Toolbar")
        tb.setMovable(False)
        tb.setObjectName("mainToolbar")
        tb.setFixedHeight(38)
        tb.setStyleSheet(
            "QToolBar { background:#161b22; border-bottom:1px solid #21262d;"
            " spacing:2px; padding:3px 8px; }"
            "QToolBar::separator { background:#30363d; width:1px; margin:4px 4px; }"
        )
        self.addToolBar(tb)

        _btn_base = (
            "QPushButton { background:#21262d; color:#c9d1d9;"
            " border:1px solid #30363d; border-radius:4px;"
            " padding:0 10px; font-size:8pt; height:26px; }"
            "QPushButton:hover { background:#30363d; border-color:#484f58; }"
            "QPushButton:pressed { background:#1f3a5f; }"
        )
        _btn_accent = (
            "QPushButton { background:#1f6feb; color:white;"
            " border:none; border-radius:4px;"
            " padding:0 12px; font-size:8pt; font-weight:bold; height:26px; }"
            "QPushButton:hover { background:#388bfd; }"
            "QPushButton:pressed { background:#0d419d; }"
        )
        _btn_green = (
            "QPushButton { background:#238636; color:white;"
            " border:none; border-radius:4px;"
            " padding:0 10px; font-size:8pt; font-weight:bold; height:26px; }"
            "QPushButton:hover { background:#2ea043; }"
        )

        def _btn(label, slot, tip="", style=_btn_base):
            b = QPushButton(label)
            b.setFixedHeight(28)
            b.clicked.connect(slot)
            b.setToolTip(tip)
            b.setStyleSheet(style)
            return b

        # File group
        tb.addWidget(_btn("New Module", self.new_module, "Create a new module (Ctrl+Shift+N)"))
        tb.addWidget(_btn("⬇ Open .MOD", self.open_mod, "Import .mod/.erf/.rim archive (Ctrl+O)", _btn_accent))
        tb.addWidget(_btn("Open GIT", self.open_git, "Open .git file (Ctrl+Shift+O)"))
        tb.addWidget(_btn("💾 Save", self._save_module, "Save .GIT (Ctrl+S)", _btn_green))
        tb.addSeparator()

        # Edit group
        tb.addWidget(_btn("↩ Undo", self._undo, "Undo (Ctrl+Z)"))
        tb.addWidget(_btn("↪ Redo", self._redo, "Redo (Ctrl+Y)"))
        tb.addSeparator()

        # View group
        tb.addWidget(_btn("⊡ Frame", self._viewport.frame_all, "Frame all (F)"))
        tb.addWidget(_btn("✓ Validate", self._validate_module, "Validate module"))
        tb.addSeparator()

        # Tools group
        tb.addWidget(_btn("🎮 Game Dir", self._set_game_dir, "Set KotOR install directory"))
        tb.addWidget(_btn("Load Assets", self._load_game_assets, "Load assets from game directory"))

    # ── Status bar ────────────────────────────────────────────────────────────

    def _setup_statusbar(self):
        sb = self.statusBar()
        sb.setStyleSheet(
            "QStatusBar { background:#238636; color:white; font-size:8pt; }"
            "QStatusBar QLabel { color:white; padding:0 6px; }"
        )

        self._status_main = QLabel(
            "No module open  —  File › New Module  or  Open .MOD  to start")
        self._status_main.setStyleSheet("color:white; padding:0 8px;")
        sb.addWidget(self._status_main)

        self._status_objects = QLabel("0 objects")
        self._status_objects.setStyleSheet(
            "color:white; padding:0 8px; border-left:1px solid rgba(255,255,255,0.3);")
        sb.addPermanentWidget(self._status_objects)

        self._status_cam = QLabel("Camera: 0,0,0")
        self._status_cam.setStyleSheet(
            "color:rgba(255,255,255,0.7); padding:0 8px;"
            " border-left:1px solid rgba(255,255,255,0.3);")
        sb.addPermanentWidget(self._status_cam)

        ver_lbl = QLabel(f"GModular {APP_VERSION}")
        ver_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.8); padding:0 8px;"
            " border-left:1px solid rgba(255,255,255,0.3);")
        sb.addPermanentWidget(ver_lbl)

    # ── Theme ─────────────────────────────────────────────────────────────────

    def _apply_theme(self):
        """Apply GitHub Dark / UE5-inspired dark theme throughout the window."""
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background: #0d1117;
                color: #c9d1d9;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            QMenuBar {
                background: #161b22;
                color: #c9d1d9;
                border-bottom: 1px solid #21262d;
                font-size: 8pt;
            }
            QMenuBar::item { padding: 4px 10px; }
            QMenuBar::item:selected { background: #1f6feb22; color: #58a6ff; }
            QMenuBar::item:pressed { background: #1f3a5f; }
            QMenu {
                background: #161b22;
                color: #c9d1d9;
                border: 1px solid #30363d;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item { padding: 5px 20px 5px 10px; border-radius: 4px; }
            QMenu::item:selected { background: #1f6feb; color: white; }
            QMenu::separator { height: 1px; background: #21262d; margin: 3px 6px; }
            QToolBar {
                background: #161b22;
                border-bottom: 1px solid #21262d;
                spacing: 4px;
                padding: 2px 6px;
            }
            QToolBar::separator { background: #21262d; width: 1px; margin: 2px; }
            QSplitter::handle { background: #21262d; }
            QSplitter::handle:hover { background: #388bfd; }
            QScrollBar:vertical {
                background: #0d1117;
                width: 10px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #30363d;
                border-radius: 4px;
                min-height: 24px;
            }
            QScrollBar::handle:vertical:hover { background: #484f58; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar:horizontal {
                background: #0d1117;
                height: 10px;
                border-radius: 4px;
            }
            QScrollBar::handle:horizontal {
                background: #30363d;
                border-radius: 4px;
                min-width: 24px;
            }
            QGroupBox {
                color: #8b949e;
                border: 1px solid #21262d;
                border-radius: 4px;
                margin-top: 10px;
                padding-top: 8px;
                font-size: 8pt;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                color: #58a6ff;
            }
            QTabWidget::pane {
                border: 1px solid #21262d;
                background: #0d1117;
                border-radius: 0;
            }
            QTabBar::tab {
                background: #161b22;
                color: #8b949e;
                padding: 5px 14px;
                border: 1px solid #21262d;
                border-bottom: none;
                font-size: 8pt;
            }
            QTabBar::tab:selected {
                background: #0d1117;
                color: #c9d1d9;
                border-bottom: 2px solid #388bfd;
            }
            QTabBar::tab:hover:!selected {
                background: #1c2128;
                color: #c9d1d9;
            }
            QLineEdit, QDoubleSpinBox {
                background: #161b22;
                color: #c9d1d9;
                border: 1px solid #30363d;
                border-radius: 4px;
                padding: 3px 6px;
                font-size: 8pt;
            }
            QLineEdit:focus, QDoubleSpinBox:focus {
                border-color: #388bfd;
                background: #1c2128;
            }
            QComboBox {
                background: #161b22;
                color: #c9d1d9;
                border: 1px solid #30363d;
                border-radius: 4px;
                padding: 3px 6px;
            }
            QComboBox:hover { border-color: #484f58; }
            QComboBox::drop-down { border: none; width: 16px; }
            QComboBox QAbstractItemView {
                background: #161b22;
                color: #c9d1d9;
                border: 1px solid #30363d;
                selection-background-color: #1f6feb;
            }
            QPushButton {
                background: #21262d;
                color: #c9d1d9;
                border: 1px solid #30363d;
                border-radius: 4px;
                padding: 4px 12px;
                font-size: 8pt;
            }
            QPushButton:hover { background: #30363d; border-color: #484f58; }
            QPushButton:pressed { background: #1f6feb; color: white; border-color: #388bfd; }
            QPlainTextEdit, QTextEdit {
                background: #0d1117;
                color: #c9d1d9;
                border: 1px solid #21262d;
                font-family: 'Consolas', 'Courier New', monospace;
            }
            QStatusBar {
                background: #238636;
                color: white;
                font-size: 8pt;
            }
            QStatusBar QLabel { color: white; }
            QHeaderView::section {
                background: #161b22;
                color: #8b949e;
                border: none;
                border-bottom: 1px solid #21262d;
                padding: 3px 6px;
                font-size: 8pt;
            }
            QToolTip {
                background: #161b22;
                color: #c9d1d9;
                border: 1px solid #30363d;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 8pt;
            }
        """)

    # ── Module Operations ─────────────────────────────────────────────────────

    def new_module(self):
        dlg = NewModuleDialog(self)
        if dlg.exec_() != QDialog.Accepted:
            return
        data = dlg.get_data()
        folder = QFileDialog.getExistingDirectory(self, "Choose Project Folder",
                                                   str(Path.home()))
        if not folder:
            return
        project_dir = os.path.join(folder, data["name"].replace(" ", "_"))
        project = ModuleProject.create_new(
            name=data["name"],
            game=data["game"],
            project_dir=project_dir,
            module_resref=data["resref"],
            description=data["desc"],
        )
        self._state.new_module(project)
        self._update_title()
        self._file_watcher.watch(project_dir)
        self.log(f"✓ Created module: {data['name']} ({data['game']})")
        self.log(f"  ResRef: {data['resref']}  |  Path: {project_dir}")
        self._print_howto_guide()

    def open_git(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open GIT File", "",
            "KotOR GIT Files (*.git);;All Files (*)"
        )
        if not path:
            return
        # Try to find matching .are and .ifo
        stem    = Path(path).stem
        are_p   = str(Path(path).with_suffix(".are"))
        ifo_p   = str(Path(path).with_suffix(".ifo"))
        self._state.load_from_files(
            path,
            are_p if os.path.exists(are_p) else "",
            ifo_p if os.path.exists(ifo_p) else "",
        )
        self._update_title()
        self._update_object_count()
        self.log(f"✓ Opened GIT: {path}")
        self.log(f"  Objects: {self._state.git.object_count}")
        self._scene_outline._refresh()
        self._add_recent_file(path)
        if self._ipc_server:
            self._ipc_server.update_module_info(path, self._state.git.object_count)

    def open_mod(self, path: str = ""):
        """
        Import a KotOR .mod / .erf / .rim archive as the active module.

        Opens the ModImportDialog which lets the user browse archive contents
        before importing. Loads GIT, ARE, IFO, and (if present) LYT into the
        viewport and Room Assembly Grid.
        """
        try:
            from .mod_import_dialog import ModImportDialog
        except Exception as e:
            QMessageBox.critical(self, "Import Error",
                                 f"Could not load import dialog:\n{e}")
            return

        # If path was supplied (e.g. drag-drop or CLI) skip the browse step
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, "Open Module Archive", "",
                "KotOR Module Archives (*.mod *.erf *.rim);;All Files (*)"
            )
        if not path:
            return

        dlg = ModImportDialog(self, mod_path=path)
        dlg.module_loaded.connect(self._on_mod_loaded)
        dlg.exec_()

    def _on_mod_loaded(self, summary: dict):
        """Called by ModImportDialog after a successful import."""
        mod_path    = summary.get("mod_path", "")
        resref      = summary.get("resref", "")
        lyt_text    = summary.get("lyt_text")
        errors      = summary.get("errors", [])
        extract_dir = summary.get("extract_dir", "")
        # Persist extract_dir so Room Grid 2D↔3D sync can find textures and MDLs
        if extract_dir:
            self._extract_dir = extract_dir

        # ── Update UI ────────────────────────────────────────────────────
        self._update_title()
        self._update_object_count()
        self._scene_outline._refresh()
        if mod_path:
            self._add_recent_file(mod_path)

        n_obj = self._state.git.object_count if self._state.git else 0
        n_res = len(summary.get("resources", []))
        self.log(f"✓ Opened module archive: {Path(mod_path).name}")
        self.log(f"  ResRef: {resref}  |  Objects: {n_obj}  |  Archive resources: {n_res}")
        if extract_dir:
            self.log(f"  Extracted to: {extract_dir}")
        for err in errors:
            self.log(f"  ⚠ {err}")

        # ── Parse LYT and build room list with MDL paths ─────────────────
        lyt_rooms = []
        if lyt_text:
            try:
                from .room_assembly import LYTData
                from ..formats.lyt_vis import LYTParser as _LYTParser
                # Try robust parser first (handles all LYT format variants)
                layout = _LYTParser.from_string(lyt_text)
                if layout.rooms:
                    from .room_assembly import RoomInstance as _RI
                    lyt_rooms = [
                        _RI(mdl_name=rp.resref, grid_x=0, grid_y=0,
                            world_x=rp.x, world_y=rp.y, world_z=rp.z)
                        for rp in layout.rooms
                    ]
                else:
                    # Fallback to LYTData.from_text (handles legacy formats)
                    lyt = LYTData.from_text(lyt_text)
                    lyt_rooms = lyt.rooms
                if lyt_rooms:
                    self.log(f"  LYT: {len(lyt_rooms)} rooms")
                    # Push into room panel if available
                    try:
                        lyt_obj = LYTData()
                        lyt_obj.rooms = list(lyt_rooms)
                        self._room_panel.load_lyt(lyt_obj)
                    except AttributeError:
                        pass
                    # Push into viewport (handles MDL path resolution internally)
                    self._load_lyt_into_viewport(lyt_rooms, extract_dir)
            except Exception as e:
                self.log(f"  ⚠ LYT parse error: {e}")
                log.warning(f"LYT parse from MOD: {e}", exc_info=True)

        # ── Auto-load WOK walkmesh overlays ───────────────────────────────
        if extract_dir and os.path.isdir(extract_dir):
            self._auto_load_walkmesh_from_dir(extract_dir, lyt_rooms)

        # ── Populate content browser from loaded module ────────────────────
        try:
            self._content_browser.populate_from_module(
                extract_dir=extract_dir,
                git=self._state.git,
                are=self._state.are,
            )
        except Exception as e:
            log.debug(f"ContentBrowser populate_from_module: {e}")

        # ── Switch to viewport / Module Editor mode ──────────────────────
        self._center_stack.setCurrentIndex(1)
        # Switch mode combo to Module Editor if not already there
        for i in range(self._mode_combo.count()):
            if self._mode_combo.itemData(i) == "module_editor":
                if self._mode_combo.currentIndex() != i:
                    self._mode_combo.setCurrentIndex(i)
                break
        # ── Force a full rebuild of object VAOs
        self._viewport._on_module_changed()
        self._viewport.update()

        # ── Refresh animation panel entities ─────────────────────────────
        if getattr(self, '_anim_panel', None) is not None:
            try:
                self._anim_panel.refresh_entities()
            except Exception as e:
                log.debug(f"anim_panel refresh: {e}")

        # ── Update IPC ───────────────────────────────────────────────────
        if self._ipc_server and self._state.git:
            self._ipc_server.update_module_info(mod_path, n_obj)

    def _auto_load_walkmesh_from_dir(self, extract_dir: str, rooms: list):
        """
        Scan extract_dir for .wok files matching the loaded rooms (or any
        .wok files), parse them, and push the combined walkmesh triangles
        into the viewport overlay.
        """
        try:
            from .walkmesh_editor import WOKParser
        except ImportError:
            self.log("  ⚠ WOK: walkmesh_editor not available")
            return

        # walk_tris / nowalk_tris: each element is a 3-tuple of (x,y,z) vertices
        # i.e. [ ((x1,y1,z1),(x2,y2,z2),(x3,y3,z3)), ... ]
        walk_tris:   list = []
        nowalk_tris: list = []

        # Build ordered list of WOK filenames to try (rooms first, then any others)
        wok_names = []
        seen = set()
        for room in rooms:
            n = room.mdl_name.lower() + ".wok"
            if n not in seen:
                wok_names.append(n)
                seen.add(n)
        # Also pick up any WOK files not already listed
        try:
            all_files = os.listdir(extract_dir)
        except OSError:
            all_files = []
        for fname in all_files:
            fl = fname.lower()
            if fl.endswith(".wok") and fl not in seen:
                wok_names.append(fl)
                seen.add(fl)

        loaded_count = 0
        for fname in wok_names:
            # Resolve actual filename (case-insensitive on Windows/Linux)
            wok_path = os.path.join(extract_dir, fname)
            if not os.path.exists(wok_path):
                # Try case-insensitive match
                for actual in all_files:
                    if actual.lower() == fname:
                        wok_path = os.path.join(extract_dir, actual)
                        break
            if not os.path.exists(wok_path):
                continue
            try:
                # WOKParser.from_file() returns a WalkMesh directly (no .parse() step)
                wok = WOKParser.from_file(wok_path)

                if not wok.faces:
                    log.debug(f"WOK {fname}: no geometry (faces={len(wok.faces)})")
                    continue

                # Find matching room for translation offset
                stem = fname[:-4]  # strip .wok extension
                tx, ty, tz = 0.0, 0.0, 0.0
                for room in rooms:
                    if room.mdl_name.lower() == stem:
                        tx = float(getattr(room, 'world_x', 0.0) or 0.0)
                        ty = float(getattr(room, 'world_y', 0.0) or 0.0)
                        tz = float(getattr(room, 'world_z', 0.0) or 0.0)
                        break

                for face in wok.faces:
                    # WOKFace.v0/v1/v2 are already vertex coordinate tuples (x,y,z),
                    # not integer indices — build the triangle directly.
                    try:
                        v0, v1, v2 = face.v0, face.v1, face.v2
                        # Skip degenerate faces (all-zero, etc.)
                        if (len(v0) < 3 or len(v1) < 3 or len(v2) < 3):
                            continue
                        tri = (
                            (float(v0[0]) + tx, float(v0[1]) + ty, float(v0[2]) + tz),
                            (float(v1[0]) + tx, float(v1[1]) + ty, float(v1[2]) + tz),
                            (float(v2[0]) + tx, float(v2[1]) + ty, float(v2[2]) + tz),
                        )
                    except (TypeError, IndexError):
                        continue
                    if face.walkable:
                        walk_tris.append(tri)
                    else:
                        nowalk_tris.append(tri)
                loaded_count += 1
                log.debug(f"WOK {fname}: {len(wok.faces)} faces, "
                          f"offset=({tx:.1f},{ty:.1f},{tz:.1f})")
            except Exception as e:
                log.debug(f"WOK load {fname}: {e}", exc_info=True)

        if walk_tris or nowalk_tris:
            self._viewport.load_walkmesh(walk_tris, nowalk_tris)
            self._viewport.toggle_walkmesh(True)
            self.log(f"  WOK: {loaded_count} file(s) → "
                     f"{len(walk_tris)} walkable, "
                     f"{len(nowalk_tris)} non-walkable faces")
        else:
            self.log(f"  WOK: no walkable geometry found in {extract_dir}")

    def _load_lyt_into_viewport(self, rooms, extract_dir: str = ""):
        """Push a list of RoomInstance objects into the viewport for 3D display."""
        try:
            # Resolve MDL paths if extract_dir given and mdl_path not already set
            if extract_dir and os.path.isdir(extract_dir):
                # Build case-insensitive filename lookup for the extract dir
                try:
                    dir_files = {f.lower(): f for f in os.listdir(extract_dir)}
                except OSError:
                    dir_files = {}
                for r in rooms:
                    if not getattr(r, 'mdl_path', ''):
                        mdl_lower = r.mdl_name.lower() + ".mdl"
                        actual = dir_files.get(mdl_lower)
                        if actual:
                            r.mdl_path = os.path.join(extract_dir, actual)
                    # Log what we found for debug
                    mdl_resolved = getattr(r, 'mdl_path', '') or ''
                    if mdl_resolved:
                        log.debug(f"  Room '{r.mdl_name}': MDL -> {os.path.basename(mdl_resolved)}")
                    else:
                        log.debug(f"  Room '{r.mdl_name}': no MDL found in {extract_dir}")

            # Also set game_dir on viewport to allow rebuild_room_vaos to search
            if extract_dir:
                self._viewport.set_game_dir(extract_dir)

            # Use the public load_rooms() API which properly triggers VAO rebuild
            self._viewport.load_rooms(rooms)

            # Frame the camera on the loaded rooms (use room-specific framing,
            # falling back to frame_all if no rooms)
            if rooms:
                self._viewport._frame_rooms()
            else:
                self._viewport.frame_all()
            self._viewport.update()
            log.debug(f"_load_lyt_into_viewport: {len(rooms)} rooms loaded into viewport")
        except Exception as e:
            log.warning(f"_load_lyt_into_viewport: {e}", exc_info=True)

    def open_project(self):
        folder = QFileDialog.getExistingDirectory(self, "Open GModular Project")
        if not folder:
            return
        project = ModuleProject.load_meta(folder)
        if not project.module_resref:
            QMessageBox.warning(self, "Invalid Project",
                                "gmodular.json not found or missing module_resref.")
            return
        self._state.load_from_project(project)
        self._update_title()
        self._update_object_count()
        self._file_watcher.watch(folder)
        self.log(f"✓ Opened project: {project.name}")

    def _save_module(self):
        ok, msg = self._app_ctrl.save_module()
        if ok:
            self.log(f"✓ {msg}")
        elif msg == "save_as_needed":
            self._save_as()
        else:
            self.log(f"⚠ {msg}")

    def _save_as(self):
        if not self._state.is_open:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save GIT As", "", "KotOR GIT Files (*.git)"
        )
        if path:
            ok, msg = self._app_ctrl.save_module(git_path=path)
            self.log(f"{'✓' if ok else '⚠'} {msg}")
            if ok:
                self._add_recent_file(path)

    # ── Edit Actions ──────────────────────────────────────────────────────────

    def _undo(self):
        desc = self._app_ctrl.undo()
        if desc:
            self.log(f"↩ Undo: {desc}")
            self._update_object_count()
        else:
            self.log("⚠ Nothing to undo")

    def _redo(self):
        desc = self._app_ctrl.redo()
        if desc:
            self.log(f"↪ Redo: {desc}")
            self._update_object_count()
        else:
            self.log("⚠ Nothing to redo")

    # ── Game Directory / Assets ───────────────────────────────────────────────

    def _set_game_dir(self):
        start = str(self._game_dir) if self._game_dir else str(Path.home())
        d = QFileDialog.getExistingDirectory(
            self, "Select KotOR Game Directory (must contain chitin.key)", start
        )
        if not d:
            return
        ok, msg = self._app_ctrl.set_game_dir(d)
        if ok:
            self._game_dir = self._app_ctrl.game_dir
            self._save_settings()
            self.log(f"✓ {msg}")
            self._status_main.setText(f"Game: {Path(d).name}")
            QMessageBox.information(self, "Game Directory Set",
                f"KotOR directory set:\n{d}\n\nClick 'Load Assets' to populate the palette.")
        else:
            QMessageBox.warning(self, "Invalid", msg)

    def _load_game_assets(self):
        if not self._game_dir or not self._game_dir.exists():
            self._set_game_dir()
            return
        tag = "K2" if (self._game_dir / "swkotor2.exe").exists() else "K1"
        self.log(f"Scanning {tag} game assets…")

        ok, result = self._app_ctrl.load_game_assets()
        if not ok and not any(result.values()):
            self.log("✗ Asset load error — no resources found")
            return

        placeables = result.get("placeables", [])
        creatures  = result.get("creatures",  [])
        doors      = result.get("doors",      [])
        rooms      = result.get("rooms",      [])

        if placeables:
            self._palette.populate_from_game(placeables, "placeable")
            self._content_browser.populate_from_game(placeables, "placeable")
            self.log(f"  Loaded {len(placeables)} placeables")
        if creatures:
            self._palette.populate_from_game(creatures, "creature")
            self._content_browser.populate_from_game(creatures, "creature")
            self.log(f"  Loaded {len(creatures)} creatures")
        if doors:
            self._palette.populate_from_game(doors, "door")
            self._content_browser.populate_from_game(doors, "door")
            self.log(f"  Loaded {len(doors)} doors")
        if rooms and self._room_panel:
            self._room_panel.set_available_rooms(rooms)
            self.log(f"  Loaded {len(rooms)} room MDLs into Room Grid palette")

    # ── Validation ────────────────────────────────────────────────────────────

    def _validate_module(self):
        issues = self._app_ctrl.validate_module()
        if not issues:
            self.log("✓ Validation passed — no issues found")
            QMessageBox.information(self, "Validation", "Module is valid. No issues found.")
        else:
            self.log(f"⚠ Validation: {len(issues)} issue(s) found")
            for issue in issues:
                self.log(f"  ⚠ {issue}")
            QMessageBox.warning(self, "Validation Issues",
                                "\n".join(issues))

    # ── Slot Handlers ─────────────────────────────────────────────────────────

    def _on_place_asset(self, asset: AssetItem):
        """Called when user clicks 'Place' in the legacy palette."""
        self._viewport.set_placement_mode(True, asset.resref,
                                          getattr(asset, "asset_type", "placeable"))
        self._placement_active = True
        kind = getattr(asset, "asset_type", "placeable").capitalize()
        self._mode_label.setText(f"PLACE MODE  [ {asset.resref} ({kind}) ]")
        self._mode_label.setStyleSheet("color:#ff8c00; font-weight:bold; font-size:8pt;")
        self.log(f"Placement mode: {asset.resref} ({kind}) — click in viewport to place")

    def _on_place_cb_asset(self, asset):
        """Called when user places an asset from the Content Browser."""
        resref     = getattr(asset, "resref",     "")
        asset_type = getattr(asset, "asset_type", "placeable")
        self._viewport.set_placement_mode(True, resref, asset_type)
        self._placement_active = True
        kind = asset_type.capitalize()
        self._mode_label.setText(f"PLACE MODE  [ {resref} ({kind}) ]")
        self._mode_label.setStyleSheet(
            "color:#ff8c00; font-weight:bold; font-size:8pt;"
        )
        # Bring viewport to focus
        self._center_stack.setCurrentIndex(1)
        self.log(f"Placement mode: {resref} ({kind}) — click in viewport to place")

    def _toggle_play_mode(self):
        """Start or stop walk preview mode."""
        if self._viewport.is_play_mode:
            self._viewport.stop_play_mode()
        else:
            # Pass game dir to viewport before starting
            if self._game_dir:
                self._viewport.set_game_dir(str(self._game_dir))
            self._viewport.start_play_mode()

    def _on_play_mode_changed(self, active: bool):
        """Update UI when play mode starts or stops."""
        if active:
            self._play_btn.setText("■  Stop")
            self._play_btn.setStyleSheet(
                "QPushButton{background:#8a1a1a;color:white;border:1px solid #aa2a2a;"
                "border-radius:3px;padding:0 10px;font-size:8pt;font-weight:bold;}"
                "QPushButton:hover{background:#aa2a2a;}"
            )
            self._mode_label.setText(
                "PLAY MODE  [ WASD = move · A/D = turn · Shift = run · Esc = exit ]")
            self._mode_label.setStyleSheet(
                "color:#2aff6a; font-weight:bold; font-size:8pt;")
            self.log("▶ Play mode started — WASD to walk, A/D to turn, Esc to exit")
        else:
            self._play_btn.setText("▶  Play")
            self._play_btn.setStyleSheet(
                "QPushButton{background:#1a8a3a;color:white;border:1px solid #2aaa4a;"
                "border-radius:3px;padding:0 10px;font-size:8pt;font-weight:bold;}"
                "QPushButton:hover{background:#2aaa4a;}"
                "QPushButton:pressed{background:#0f6028;}"
            )
            self._mode_label.setText("EDIT MODE")
            self._mode_label.setStyleSheet("color:#4ec9b0; font-weight:bold; font-size:8pt;")
            self.log("■ Play mode stopped — back to edit mode")

    def _on_app_mode_changed(self, index: int):
        """Handle switch between Level Builder and Module Editor modes."""
        mode = self._mode_combo.currentData()
        if not mode:
            return
        self._viewport.set_app_mode(mode)
        if mode == "level_builder":
            # ── Level Builder mode ──────────────────────────────────────────
            self._mode_label.setText("LEVEL BUILDER")
            self._mode_label.setStyleSheet(
                "QLabel { color:#4ec9b0; font-weight:bold; font-size:8pt;"
                " background:#0a2a1a; border:1px solid #1a5a2a;"
                " border-radius:3px; padding:0 8px; }"
            )
            # Show Room Grid first in the bottom tabs
            if self._bottom_tabs:
                for i in range(self._bottom_tabs.count()):
                    if "Room" in self._bottom_tabs.tabText(i):
                        self._bottom_tabs.setCurrentIndex(i)
                        break
            # Content Browser shows room-focused assets in level builder
            self._content_browser._select_category("All", "")
            self.log(
                "⬜  Mode: Level Builder — assemble room geometry, "
                "build new areas, place walkmesh"
            )
        else:
            # ── Module Editor mode ──────────────────────────────────────────
            self._mode_label.setText("MODULE EDITOR")
            self._mode_label.setStyleSheet(
                "QLabel { color:#79c0ff; font-weight:bold; font-size:8pt;"
                " background:#0d2040; border:1px solid #1a4070;"
                " border-radius:3px; padding:0 8px; }"
            )
            # Show Output Log in module editor
            if self._bottom_tabs:
                for i in range(self._bottom_tabs.count()):
                    if "Output" in self._bottom_tabs.tabText(i):
                        self._bottom_tabs.setCurrentIndex(i)
                        break
            self.log(
                "✏  Mode: Module Editor — place/edit GIT objects, "
                "scripts, walkmesh, MDL import/export"
            )

    def _on_object_selected(self, obj):
        """Called when user selects an object in the viewport."""
        self._inspector.inspect(obj)
        self._scene_outline.set_selected(obj)
        if obj is None:
            self._status_main.setText("Nothing selected")
        else:
            kind = type(obj).__name__.replace("GIT", "")
            tag  = getattr(obj, "tag", "")
            resref = getattr(obj, "resref", "")
            self._status_main.setText(f"Selected: {kind}  {tag!r}  ({resref})")

        # Sync animation panel to selected entity
        if getattr(self, '_anim_panel', None) is not None and obj is not None:
            try:
                reg = getattr(self._viewport, '_entity_registry', None)
                if reg:
                    tag_s = str(getattr(obj, 'tag', '') or '')
                    matches = reg.get_by_tag(tag_s) if tag_s else []
                    if not matches:
                        resref_s = str(getattr(obj, 'resref', '') or '')
                        matches = reg.get_by_resref(resref_s) if resref_s else []
                    if matches:
                        self._anim_panel.set_selected_entity(matches[0].entity_id)
            except Exception as e:
                log.debug(f"anim_panel set_selected: {e}")

    def _on_object_placed(self, obj):
        """Called when an object is successfully placed."""
        # P4: If in patrol placement mode, route the position to inspector
        if getattr(obj, "resref", "") == "__patrol__" or self._patrol_placement_creature is not None:
            if self._patrol_placement_creature is not None:
                x, y, z = getattr(obj, "x", 0), getattr(obj, "y", 0), getattr(obj, "z", 0)
                self._inspector.add_patrol_waypoint_at(x, y, z)
                self._patrol_placement_creature = None
                self._viewport.set_placement_mode(False)
                self._mode_label.setText("EDIT MODE")
                self._mode_label.setStyleSheet("color:#4ec9b0; font-weight:bold; font-size:8pt;")
                return
        self._placement_active = False
        self._viewport.set_placement_mode(False)
        self._mode_label.setText("EDIT")
        self._mode_label.setStyleSheet(
            "QLabel { color:#4ec9b0; font-weight:bold; font-size:8pt;"
            " background:#1a3a2a; border:1px solid #2a5a3a;"
            " border-radius:3px; padding:0 8px; }"
        )
        self._inspector.inspect(obj)
        self._update_object_count()
        self._scene_outline._refresh()
        kind = type(obj).__name__.replace("GIT", "")
        self.log(f"✓ Placed {kind}: {obj.resref}")
        if self._ipc_server and self._state.git:
            self._ipc_server.update_module_info(
                self._state.module_name, self._state.git.object_count)

    def _on_camera_moved(self, x: float, y: float, z: float):
        self._status_cam.setText(f"Camera: {x:.1f}, {y:.1f}, {z:.1f}")

    def _on_module_changed(self):
        self._update_title()
        self._update_object_count()
        self._refresh_area_props()
        if self._ipc_server and self._state.git:
            self._ipc_server.update_module_info(
                self._state.module_name, self._state.git.object_count)

    def _on_rooms_changed_in_grid(self, rooms: list):
        """
        Fired whenever rooms are added / removed / moved in the Room Grid tab.
        Passes the current room list to the viewport so it can rebuild its
        3-D VAOs and frame the camera.

        Also ensures the extract_dir / game_dir is set so MDL geometry and
        textures are found (Kotor.NET-quality rendering on every room change).
        """
        try:
            # Pass extract_dir so viewport can resolve MDL paths and textures
            if self._extract_dir:
                self._viewport.set_game_dir(self._extract_dir)
            elif self._game_dir:
                self._viewport.set_game_dir(str(self._game_dir))

            self._viewport.load_rooms(rooms)

            # Also register MDL file paths so the viewport can find geometry
            if self._room_panel and hasattr(self._room_panel, 'get_mdl_paths'):
                for name, path in self._room_panel.get_mdl_paths().items():
                    # Propagate mdl_path back onto each room instance
                    for ri in rooms:
                        n = getattr(ri, 'model_name', None) or getattr(ri, 'name', '')
                        if n.lower() == name.lower():
                            ri.mdl_path = path

            # Reload textures whenever room layout changes
            texture_dir = self._extract_dir or (str(self._game_dir) if self._game_dir else "")
            if texture_dir and self._viewport._renderer.ready:
                try:
                    self._viewport.load_textures_for_rooms(texture_dir)
                except Exception:
                    pass

            self.log(f"Room Grid → Viewport: {len(rooms)} room(s) loaded")
        except Exception as e:
            log.debug(f"_on_rooms_changed_in_grid error: {e}")

    def _on_property_changed(self, obj, attr: str, old, new):
        """Called when Inspector edits a field."""
        if attr == "_open_script":
            # P7: open script in GhostScripter
            if new:
                self._gs_bridge.open_script(str(new))
                self.log(f"→ Opening {new} in GhostScripter…")
            else:
                self.log("⚠ No script assigned to this field")
        elif attr == "_compile_script":
            game = self._state.project.game if self._state.project else "K1"
            self._gs_bridge.compile_script(str(new), game)
            self.log(f"→ Compiling {new}…")
        elif attr == "_open_in_rigger":
            # P9: handled by open_in_rigger signal
            pass
        else:
            self._state._dirty = True
            self.log(f"  Edit: {attr} = {new!r}")

    # ── IPC ───────────────────────────────────────────────────────────────────

    # ── P6: Module Packager ───────────────────────────────────────────────────

    def _open_mod_packager(self):
        """Open the MOD Packager dialog."""
        try:
            from .mod_packager_dialog import ModPackagerDialog
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Packager unavailable: {e}")
            return
        module_name = self._state.module_name or "unnamed"
        module_dir = ""
        if self._state.project:
            module_dir = str(self._state.project.module_dir)
        elif self._game_dir:
            module_dir = str(self._game_dir / "Modules")
        dlg = ModPackagerDialog(
            parent=self,
            module_name=module_name,
            module_dir=module_dir,
            git=self._state.git,
            are=self._state.are,
            ifo=self._state.ifo,
            game_dir=str(self._game_dir) if self._game_dir else "",
        )
        dlg.pack_complete.connect(lambda path: self.log(f"✓ Module packed: {path}"))
        dlg.exec_()

    def _open_validation_report(self):
        """Open the full validation report dialog."""
        try:
            from .mod_packager_dialog import ModPackagerDialog
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Validation unavailable: {e}")
            return
        module_name = self._state.module_name or "unnamed"
        module_dir = ""
        if self._state.project:
            module_dir = str(self._state.project.module_dir)
        dlg = ModPackagerDialog(
            parent=self,
            module_name=module_name,
            module_dir=module_dir,
            git=self._state.git,
            are=self._state.are,
            ifo=self._state.ifo,
            game_dir=str(self._game_dir) if self._game_dir else "",
        )
        dlg._run_validate()
        dlg.exec_()

    def _open_room_assembly(self):
        """Open the Room Assembly Grid as a floating dialog."""
        try:
            from .room_assembly import RoomAssemblyPanel
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Room Assembly unavailable: {e}")
            return
        from qtpy.QtWidgets import QDialog, QVBoxLayout
        dlg = QDialog(self)
        dlg.setWindowTitle("Room Assembly Grid")
        dlg.setMinimumSize(960, 600)
        layout = QVBoxLayout(dlg)
        panel = RoomAssemblyPanel(dlg)
        panel.lyt_changed.connect(lambda t: self.log(f"LYT updated ({len(t)} chars)"))
        try:
            rm = get_resource_manager()
            room_names = [r for r in rm.list_resources("mdl")
                          if len(r) > 4 and not r.startswith("c_") and not r.startswith("p_")]
            panel.set_available_rooms(room_names[:200])
        except Exception:
            panel.set_available_rooms([
                "manm26aa", "manm26ab", "manm26ac", "manm26ad",
                "tarc_m17aa", "tarc_m17ab", "tar_m02aa",
            ])
        layout.addWidget(panel)
        dlg.exec_()

    # ── P9: Blueprint IPC ─────────────────────────────────────────────────────

    def _on_open_in_rigger(self, resref: str, ext: str, module_dir: str):
        """P9: Relay inspector's 'Edit in GhostRigger' to the IPC bridge."""
        self.log(f"→ Opening {resref}.{ext} in GhostRigger…")
        ok = self._gr_bridge.open_blueprint(resref, ext, module_dir)
        if not ok:
            self.log(f"  ⚠ GhostRigger not connected — {resref}.{ext} cannot be opened remotely")
            QMessageBox.information(
                self,
                "GhostRigger Not Connected",
                f"GhostRigger is not running.\n\n"
                f"Start GhostRigger, then try again.\n"
                f"Port: 7001  |  Resource: {resref}.{ext}"
            )

    # ── P4: Patrol Waypoint Linker ────────────────────────────────────────────

    def _on_patrol_click_requested(self, creature):
        """P4: Inspector wants a floor-click to place a patrol waypoint."""
        self._patrol_placement_creature = creature
        self._viewport.set_placement_mode(True, "__patrol__", "waypoint")
        tag = getattr(creature, "tag", "?")
        self._mode_label.setText(f"PATROL MODE  [ Click floor to place waypoint for {tag!r} ]")
        self._mode_label.setStyleSheet("color:#ffcc44; font-weight:bold; font-size:8pt;")
        self.log(f"Patrol mode: click viewport floor to add waypoint for {tag!r}")

    def _on_patrol_path_changed(self, creature, waypoints: list):
        """P4: Patrol path changed — update viewport overlay."""
        positions = [(w.x, w.y, w.z) for w in waypoints]
        tag = getattr(creature, "tag", "?")
        if hasattr(self._viewport, "set_patrol_path"):
            self._viewport.set_patrol_path(tag, positions)
        self.log(f"Patrol: {tag} — {len(waypoints)} waypoints")


    def _on_object_selected_from_outline(self, obj):
        """Called when user selects in scene outline (sync to viewport)."""
        self._inspector.inspect(obj)
        self._viewport.select_object(obj)
        if obj is not None:
            kind   = type(obj).__name__.replace("GIT", "")
            tag    = getattr(obj, "tag", "")
            resref = getattr(obj, "resref", "")
            self._status_main.setText(f"Selected: {kind}  {tag!r}  ({resref})")
            self._viewport.frame_selected()

    def _on_outline_delete(self, obj):
        """Called when outline deletes an object (command already executed by outline)."""
        self._viewport.select_object(None)
        self._inspector.inspect(None)
        self._update_object_count()
        self._scene_outline._refresh()   # force refresh in case change callback is slow
        tag = getattr(obj, "tag", "")
        kind = type(obj).__name__.replace("GIT", "")
        self.log(f"✗ Deleted {kind}: {tag}")

    def _on_wok_loaded(self, wok):
        """Called when walkmesh panel loads a WOK file."""
        self.log(f"⊡ WOK loaded: {wok.model_name} — {wok.face_count} faces "
                 f"({wok.walkable_face_count} walkable)")
        # Switch to walkmesh tab to show it
        for i in range(self._bottom_tabs.count()):
            if "Walkmesh" in self._bottom_tabs.tabText(i):
                self._bottom_tabs.setCurrentIndex(i)
                break

    def _refresh_area_props(self):
        """Update the Area Properties and IFO tabs from current state."""
        try:
            state = self._state
            if not state.is_open:
                for lbl in (self._are_tag_lbl, self._are_name_lbl, self._are_rooms_lbl,
                            self._are_tileset_lbl, self._are_skybox_lbl,
                            self._are_fog_lbl, self._are_ambient_lbl):
                    lbl.setText("-")
                # Clear IFO editable fields
                for edit in (self._ifo_name_edit, self._ifo_desc_edit, self._ifo_area_edit,
                             self._ifo_onload_edit, self._ifo_onstart_edit,
                             self._ifo_onenter_edit, self._ifo_onleave_edit,
                             self._ifo_onhb_edit, self._ifo_ondeath_edit):
                    edit.setText("")
                self._ifo_pos_lbl.setText("-")
                return

            are = state.are
            if are:
                self._are_tag_lbl.setText(are.tag or "(none)")
                self._are_name_lbl.setText(are.name or "(none)")
                self._are_rooms_lbl.setText(str(are.room_count))
                self._are_tileset_lbl.setText(are.tileset_resref or "(none)")
                self._are_skybox_lbl.setText(are.sky_box or "(none)")
                fog = f"{'On' if are.fog_enabled else 'Off'}  near={are.fog_near:.0f}  far={are.fog_far:.0f}"
                self._are_fog_lbl.setText(fog)
                self._are_ambient_lbl.setText(f"#{are.ambient_color:06x}")

            ifo = state.ifo
            if ifo:
                # Populate editable fields (suppress editingFinished by blockSignals)
                for edit, val in [
                    (self._ifo_name_edit,    ifo.mod_name or ""),
                    (self._ifo_desc_edit,    ifo.mod_description or ""),
                    (self._ifo_area_edit,    ifo.entry_area or ""),
                    (self._ifo_onload_edit,  ifo.on_module_load or ""),
                    (self._ifo_onstart_edit, ifo.on_module_start or ""),
                    (self._ifo_onenter_edit, ifo.on_client_enter or ""),
                    (self._ifo_onleave_edit, ifo.on_client_leave or ""),
                    (self._ifo_onhb_edit,    ifo.on_heartbeat or ""),
                    (self._ifo_ondeath_edit, ifo.on_player_death or ""),
                ]:
                    edit.blockSignals(True)
                    edit.setText(val)
                    edit.blockSignals(False)
                pos = ifo.entry_position
                self._ifo_pos_lbl.setText(
                    f"{pos.x:.2f}, {pos.y:.2f}, {pos.z:.2f}" if pos else "-"
                )
        except AttributeError:
            pass   # Panels not yet created

    def _connect_ipc_signals(self):
        self._gs_bridge.connected.connect(
            lambda v: (self._gs_dot.setStyleSheet("color:#4ec9b0; font-size:8pt; margin-left:8px;"),
                       self.log(f"🔗 GhostScripter connected (v{v})")))
        self._gs_bridge.disconnected.connect(
            lambda: self._gs_dot.setStyleSheet("color:#555555; font-size:8pt; margin-left:8px;"))
        self._gs_bridge.scripts_updated.connect(self._on_scripts_updated)
        self._gs_bridge.compile_done.connect(self._on_compile_done)
        # Wire callback server compile_result → same handler
        if self._ipc_server:
            self._ipc_server.compile_result.connect(
                lambda ok, script, msg: self._on_compile_done(ok, f"{script}: {msg}")
            )
            self._ipc_server.model_ready.connect(
                lambda name, mdl, mdx: self.log(f"📦 IPC model ready: {name}  mdl={mdl}")
            )
            self._ipc_server.git_updated.connect(
                lambda path, n: self.log(f"⟳ IPC git_updated: {path} ({n} objects)")
            )

        self._gr_bridge.connected.connect(
            lambda v: (self._gr_dot.setStyleSheet("color:#4ec9b0; font-size:8pt;"),
                       self.log(f"🔗 GhostRigger connected (v{v})")))
        self._gr_bridge.disconnected.connect(
            lambda: self._gr_dot.setStyleSheet("color:#555555; font-size:8pt;"))
        self._gr_bridge.model_ready.connect(
            lambda p: self.log(f"📦 Model ready: {p.model_name}"))

        self._file_watcher.script_changed.connect(self._on_script_file_changed)
        self._file_watcher.model_changed.connect(self._on_model_file_changed)

    def _on_scripts_updated(self, scripts: list):
        self._inspector.set_scripts(scripts)
        log.debug(f"Scripts updated from GhostScripter: {len(scripts)}")

    def _on_compile_done(self, success: bool, message: str):
        icon = "✓" if success else "✗"
        self.log(f"{icon} Compile: {message}")

    def _on_script_file_changed(self, path: str):
        self.log(f"⟳ Script changed: {Path(path).name} — auto-reload triggered")

    def _on_model_file_changed(self, path: str):
        self.log(f"⟳ Model changed: {Path(path).name}")

    def _show_ipc_status(self):
        gs = "Connected" if self._gs_bridge.is_connected else "Disconnected"
        gr = "Connected" if self._gr_bridge.is_connected else "Disconnected"
        cb = "Running" if (self._ipc_server and self._ipc_server.is_running) else "Stopped"
        QMessageBox.information(self, "IPC Status",
            f"GhostScripter (port 5002): {gs}\n"
            f"GhostRigger (port 5001): {gr}\n"
            f"GModular Callback Server (port 5003): {cb}\n\n"
            "Launch GhostScripter/GhostRigger to enable IPC features."
        )

    # ── Module Info / Dialogs ─────────────────────────────────────────────────

    def _show_module_props(self):
        state = self._state
        if not state.is_open:
            QMessageBox.information(self, "Module Properties", "No module loaded.")
            return
        name = state.module_name
        git  = state.git
        obj_count = git.object_count if git else 0
        ifo  = state.ifo
        are  = state.are
        info_lines = [
            f"Module: {name}",
            f"ResRef: {state.project.module_resref if state.project else '-'}",
            f"Game:   {state.project.game if state.project else '-'}",
            "",
            f"Objects: {obj_count}",
        ]
        if git:
            info_lines += [
                f"  Placeables:  {len(git.placeables)}",
                f"  Creatures:   {len(git.creatures)}",
                f"  Doors:       {len(git.doors)}",
                f"  Triggers:    {len(git.triggers)}",
                f"  Sounds:      {len(git.sounds)}",
                f"  Waypoints:   {len(git.waypoints)}",
                f"  Stores:      {len(git.stores)}",
            ]
        if are:
            info_lines += [
                "",
                f"Area tag:    {are.tag}",
                f"Rooms:       {are.room_count}",
                f"Tileset:     {are.tileset_resref}",
            ]
        if ifo:
            info_lines += [
                "",
                f"Module name: {ifo.mod_name}",
                f"Entry area:  {ifo.entry_area}",
                f"On load:     {ifo.on_module_load or '-'}",
            ]
        info_lines += [
            "",
            f"Dirty:       {state.is_dirty}",
            f"Undo steps:  {len(state._undo_stack)}",
        ]
        QMessageBox.information(self, "Module Properties", "\n".join(info_lines))

    def _show_about(self):
        QMessageBox.about(self, f"About {APP_NAME}",
            f"<b>GModular</b> v{APP_VERSION}<br><br>"
            "KotOR Module Editor — Unreal Engine experience for the Odyssey Engine<br><br>"
            "Part of the Ghostworks Pipeline:<br>"
            "• GModular — Module Editor<br>"
            "• GhostScripter — Script IDE<br>"
            "• GhostRigger — Model Rigger<br><br>"
            "MIT License  |  KotOR Community"
        )

    # ── Settings ──────────────────────────────────────────────────────────────

    def _load_settings(self):
        settings_path = Path.home() / ".gmodular" / "settings.json"
        self._is_first_launch = True
        try:
            if settings_path.exists():
                data = json.loads(settings_path.read_text())
                gd = data.get("game_dir", "")
                if gd and Path(gd).exists():
                    self._game_dir = Path(gd)
                # Load recent files list
                self._recent_files = [
                    p for p in data.get("recent_files", [])
                    if isinstance(p, str) and Path(p).exists()
                ][:10]
                # If settings file exists, not first launch
                self._is_first_launch = data.get("first_launch", True)
        except Exception:
            pass

    def _save_settings(self):
        settings_path = Path.home() / ".gmodular" / "settings.json"
        try:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "game_dir": str(self._game_dir) if self._game_dir else "",
                "recent_files": getattr(self, "_recent_files", [])[:10],
                "first_launch": False,   # mark as seen after first save
            }
            settings_path.write_text(json.dumps(data, indent=2))
        except Exception:
            pass

    def _add_recent_file(self, path: str):
        """Add a file path to the recent files list (max 10, deduped, newest first)."""
        if not hasattr(self, "_recent_files"):
            self._recent_files = []
        path = str(path)
        if path in self._recent_files:
            self._recent_files.remove(path)
        self._recent_files.insert(0, path)
        self._recent_files = self._recent_files[:10]
        self._rebuild_recent_menu()
        self._save_settings()

    def _rebuild_recent_menu(self):
        """Repopulate the Recent Files submenu."""
        if not hasattr(self, "_recent_menu"):
            return
        self._recent_menu.clear()
        files = getattr(self, "_recent_files", [])
        if not files:
            no_act = self._recent_menu.addAction("(no recent files)")
            no_act.setEnabled(False)
        else:
            for i, path in enumerate(files[:10]):
                label = f"&{i+1}  {Path(path).name}"
                act = self._recent_menu.addAction(label)
                act.setData(path)
                act.triggered.connect(lambda checked, p=path: self._open_recent(p))
            self._recent_menu.addSeparator()
            self._recent_menu.addAction("Clear Recent Files").triggered.connect(
                self._clear_recent_files)

    def _open_recent(self, path: str):
        """Open a recently used GIT file."""
        if not Path(path).exists():
            QMessageBox.warning(self, "File Not Found",
                                f"File no longer exists:\n{path}")
            if path in self._recent_files:
                self._recent_files.remove(path)
            self._rebuild_recent_menu()
            return
        self._state.load_from_files(path)
        self._update_title()
        self._update_object_count()
        self._add_recent_file(path)
        self.log(f"✓ Opened (recent): {path}")
        self._scene_outline._refresh()

    def _clear_recent_files(self):
        """Clear the recent files list."""
        self._recent_files = []
        self._rebuild_recent_menu()
        self._save_settings()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _open_tutorial(self, step: int = 0):
        """Open (or raise) the interactive tutorial dialog."""
        try:
            from .tutorial_dialog import show_tutorial
            show_tutorial(parent=self, step=step)
        except Exception as e:
            log.warning(f"Tutorial unavailable: {e}")
            # Fallback: print howto to log
            self._print_howto_guide()

    def _print_howto_guide(self):
        """Print a concise how-to-build guide to the Output Log."""
        lines = [
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "  HOW TO BUILD A MODULE IN GMODULAR",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "  STEP 1 — ASSEMBLE ROOMS (Room Grid tab, bottom panel)",
            "    • Click the 'Room Grid' tab at the bottom of the screen.",
            "    • The left palette lists every .mdl room from your game dir.",
            "    • Drag a room name from the palette onto a grid cell, OR",
            "      right-click a cell and choose 'Place room here'.",
            "    • Repeat for each room tile you need.",
            "    • Adjacent rooms are auto-connected in the .vis export.",
            "    • Click 'Save LYT + VIS...' to write layout files to disk.",
            "",
            "  STEP 2 — PLACE OBJECTS (Asset Palette, left panel)",
            "    • Click the 'Assets' tab on the left.",
            "    • Double-click any Placeable / Creature / Door to enter",
            "      PLACE MODE (orange banner appears).",
            "    • Click anywhere in the 3-D viewport to drop the object.",
            "    • Repeat for every object in your module.",
            "",
            "  STEP 3 — INSPECT & EDIT (Inspector, right panel)",
            "    • Click any object in the viewport or Scene Outline to select.",
            "    • The Inspector shows Tag, ResRef, Position, Bearing etc.",
            "    • Edit fields directly; changes are tracked for undo (Ctrl+Z).",
            "    • The pencil icon next to script fields opens GhostScripter.",
            "    • 'Edit in GhostRigger' opens the blueprint for that object.",
            "",
            "  STEP 4 — MOVE OBJECTS (Gimbal, 3-D Viewport)",
            "    • Select an object — three colour arrows appear on it:",
            "        Red   = X axis   Green = Y axis   Blue = Z axis",
            "        Yellow dashed ring = Rotate around Z",
            "    • Left-click and drag an arrow to translate along that axis.",
            "    • Drag the ring to rotate.",
            "    • Hold Ctrl while dragging  → snap to 1.0 u grid.",
            "    • Hold Shift while dragging → snap to 0.25 u (fine).",
            "    • Hold Ctrl+Shift           → snap to 0.5 u (medium).",
            "    • Press F to frame-all; Delete to remove selected object.",
            "",
            "  STEP 5 — SAVE (Ctrl+S  or  File > Save GIT)",
            "    • Saves the .git file (and .ifo) to your project folder.",
            "    • Autosave runs every 2 minutes while the module is open.",
            "",
            "  STEP 6 — VALIDATE & PACK (.MOD export)",
            "    • Module > Pack Module (.MOD)... — opens the Packager dialog.",
            "    • The Packager validates tag uniqueness, resref lengths,",
            "      script presence, door links, and patrol waypoints.",
            "    • Fix any errors shown, then click 'Pack' to write the .MOD.",
            "    • Copy the resulting .MOD into KotOR's Modules/ folder to test.",
            "",
            "  KEYBOARD SHORTCUTS",
            "    Ctrl+S  Save    Ctrl+Z  Undo    Ctrl+Y  Redo",
            "    F       Frame all objects in viewport",
            "    Delete  Remove selected object",
            "    Escape  Cancel placement / cancel gizmo drag",
            "    Ctrl    Snap 1.0 u  |  Shift  Snap 0.25 u  |  Ctrl+Shift  0.5 u",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "  TIP: You can re-read this guide any time via Help > How To Build",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
        ]
        for line in lines:
            self.log(line)

    def log(self, message: str):
        if hasattr(self, "_output_log"):
            self._output_log.appendPlainText(message)

    def _update_title(self):
        state = self._state
        dirty_marker = " *" if state.is_dirty else ""
        if state.is_open:
            self.setWindowTitle(
                f"{APP_NAME}  ·  {state.module_name}{dirty_marker}  ·  v{APP_VERSION}"
            )
            self._module_label.setText(
                f"{state.module_name}{dirty_marker}"
            )
            # Switch center panel to viewport now that a module is open
            if self._center_stack.currentIndex() == 0:
                self._center_stack.setCurrentIndex(1)
                self.statusBar().showMessage(
                    "Module loaded — Assets tab: double-click to place  |  "
                    "Room Grid tab: drag or right-click to assemble rooms", 6000)
        else:
            self.setWindowTitle(f"{APP_NAME}  ·  v{APP_VERSION}")
            self._module_label.setText("No module loaded")
            # Return to welcome screen when no module is loaded
            self._center_stack.setCurrentIndex(0)

    def _focus_room_tab(self):
        """Switch the bottom panel to the Room Grid tab and expand it."""
        for i in range(self._bottom_tabs.count()):
            if "Room" in self._bottom_tabs.tabText(i):
                self._bottom_tabs.setCurrentIndex(i)
                # Expand bottom area via the vertical splitter so the
                # Room Grid is usable (user can still resize afterwards).
                try:
                    total = self._center_vsplitter.height()
                    if total > 0:
                        bottom = max(420, total // 2)
                        top = max(200, total - bottom)
                        self._center_vsplitter.setSizes([top, bottom])
                except Exception:
                    pass
                break

    def _update_object_count(self):
        state = self._state
        if state.git:
            self._status_objects.setText(f"{state.git.object_count} objects")
        else:
            self._status_objects.setText("0 objects")

    def _check_dirty(self):
        self._update_title()

    def closeEvent(self, event):
        if self._state.is_dirty:
            reply = QMessageBox.question(
                self, "Unsaved Changes",
                "The module has unsaved changes.\nSave before closing?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save,
            )
            if reply == QMessageBox.Save:
                self._save_module()
            elif reply == QMessageBox.Cancel:
                event.ignore()
                return

        self._gs_bridge.stop()
        self._gr_bridge.stop()
        self._file_watcher.stop()
        if self._ipc_server:
            self._ipc_server.stop()
        self._state.close()
        self._save_settings()
        super().closeEvent(event)

    # ── Drag-and-Drop ─────────────────────────────────────────────────────────

    def dragEnterEvent(self, event):
        """Accept drops of .mod / .erf / .rim / .git files."""
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                ext = Path(url.toLocalFile()).suffix.lower()
                if ext in (".mod", ".erf", ".rim", ".git"):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        """Handle dropped .mod / .erf / .rim / .git files."""
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            ext  = Path(path).suffix.lower()
            if ext in (".mod", ".erf", ".rim"):
                event.acceptProposedAction()
                self.open_mod(path)
                return
            elif ext == ".git":
                event.acceptProposedAction()
                stem  = Path(path).stem
                are_p = str(Path(path).with_suffix(".are"))
                ifo_p = str(Path(path).with_suffix(".ifo"))
                self._state.load_from_files(
                    path,
                    are_p if os.path.exists(are_p) else "",
                    ifo_p if os.path.exists(ifo_p) else "",
                )
                self._update_title()
                self._update_object_count()
                self._scene_outline._refresh()
                self._add_recent_file(path)
                self.log(f"✓ Dropped GIT: {Path(path).name}")
                return
        event.ignore()
