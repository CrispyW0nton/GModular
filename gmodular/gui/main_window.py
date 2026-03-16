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

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QLabel, QPushButton, QStatusBar, QMenuBar, QMenu, QAction,
    QFileDialog, QMessageBox, QToolBar, QPlainTextEdit, QFrame,
    QSizePolicy, QApplication, QInputDialog, QComboBox, QTabWidget,
    QDialog, QFormLayout, QLineEdit, QDialogButtonBox, QScrollArea,
    QStackedWidget,
)
from PyQt5.QtCore import Qt, QSize, QTimer, pyqtSlot
from PyQt5.QtGui import QIcon, QFont, QKeySequence

from .viewport        import ViewportWidget
from .inspector       import InspectorPanel
from .asset_palette   import AssetPalette, AssetItem
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

log = logging.getLogger(__name__)

APP_NAME    = "GModular"
APP_VERSION = "1.0.0"

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
        self._btn_open = QPushButton("Open .GIT File\u2026")
        self._btn_open.setStyleSheet(btn_style2)
        row.addWidget(self._btn_new)
        row.addSpacing(8)
        row.addWidget(self._btn_open)
        row.addStretch()
        g2l.addLayout(row)
        note = QLabel("New Module creates a blank module. Open .GIT loads an existing one.")
        note.setStyleSheet("color:#666; font-size:7pt; margin-top:2px;")
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

    def connect_actions(self, new_cb, open_cb, room_cb):
        self._btn_new.clicked.connect(new_cb)
        self._btn_open.clicked.connect(open_cb)
        self._btn_room.clicked.connect(room_cb)


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

        self._state = get_module_state()
        self._rm    = get_resource_manager()
        self._game_dir: Optional[Path] = None
        self._placement_active = False
        self._recent_files: list = []   # populated by _load_settings
        self._room_panel = None         # set by _build_bottom_tabs

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
        self.log(f"Version {APP_VERSION}  |  KotorModTools Suite")
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

        # Outer splitter: left palette | center | right inspector
        self._outer_splitter = QSplitter(Qt.Horizontal)
        self._outer_splitter.setHandleWidth(3)

        # ── LEFT: Tabbed panel (Asset Palette + Scene Outline) ─────────────
        left_tabs = QTabWidget()
        left_tabs.setTabPosition(QTabWidget.West)
        left_tabs.setMinimumWidth(210)
        left_tabs.setMaximumWidth(290)
        left_tabs.setFont(QFont("Segoe UI", 8))
        left_tabs.setStyleSheet(
            "QTabWidget::pane { border:none; background:#1e1e1e; }"
            "QTabBar::tab { background:#2d2d30; color:#969696; padding:6px 4px;"
            " border:1px solid #3c3c3c; margin:1px; font-size:8pt; }"
            "QTabBar::tab:selected { background:#1e1e1e; color:#4ec9b0; }"
        )

        self._palette = AssetPalette()
        self._palette.place_asset.connect(self._on_place_asset)
        left_tabs.addTab(self._palette, "Assets")

        self._scene_outline = SceneOutlinePanel()
        self._scene_outline.object_selected.connect(self._on_object_selected_from_outline)
        self._scene_outline.request_delete.connect(self._on_outline_delete)
        left_tabs.addTab(self._scene_outline, "Scene")

        self._outer_splitter.addWidget(left_tabs)

        # ── CENTER: Stacked widget (Welcome ↔ Viewport) + log ────────────────
        center_widget = QWidget()
        center_layout = QVBoxLayout(center_widget)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)

        # 3D Viewport — create FIRST (before viewport header uses it)
        self._viewport = ViewportWidget()
        self._viewport.object_selected.connect(self._on_object_selected)
        self._viewport.object_placed.connect(self._on_object_placed)
        self._viewport.camera_moved.connect(self._on_camera_moved)
        self._viewport.play_mode_changed.connect(self._on_play_mode_changed)

        # Viewport header bar (mode indicator + quick buttons)
        self._viewport_header = self._build_viewport_header()
        center_layout.addWidget(self._viewport_header)

        # Stacked: page 0 = Welcome, page 1 = Viewport
        self._center_stack = QStackedWidget()
        self._welcome_panel = WelcomePanel()
        self._welcome_panel.connect_actions(
            self.new_module, self.open_git, self._focus_room_tab)
        self._center_stack.addWidget(self._welcome_panel)   # index 0
        self._center_stack.addWidget(self._viewport)         # index 1
        self._center_stack.setCurrentIndex(0)               # show welcome first

        # Bottom panel: tabbed (log + walkmesh + area props)
        self._bottom_tabs = self._build_bottom_tabs()

        # Vertical splitter between viewport area and bottom tabs —
        # lets the user drag the divider to resize the Room Grid panel.
        self._center_vsplitter = QSplitter(Qt.Vertical)
        self._center_vsplitter.setHandleWidth(4)
        self._center_vsplitter.setChildrenCollapsible(False)
        self._center_vsplitter.addWidget(self._center_stack)   # top: 3D view
        self._center_vsplitter.addWidget(self._bottom_tabs)    # bottom: tabs
        self._center_vsplitter.setSizes([600, 200])            # sensible default
        self._center_vsplitter.setStretchFactor(0, 3)          # viewport gets more space
        self._center_vsplitter.setStretchFactor(1, 1)
        center_layout.addWidget(self._center_vsplitter, stretch=1)

        self._outer_splitter.addWidget(center_widget)

        # ── RIGHT: Inspector ──────────────────────────────────────────────────
        self._inspector = InspectorPanel()
        self._inspector.setMinimumWidth(220)
        self._inspector.setMaximumWidth(340)
        self._inspector.property_changed.connect(self._on_property_changed)
        # P4: patrol path signals
        self._inspector.request_patrol_click.connect(self._on_patrol_click_requested)
        self._inspector.patrol_path_changed.connect(self._on_patrol_path_changed)
        # P9: blueprint IPC signal
        self._inspector.open_in_rigger.connect(self._on_open_in_rigger)
        # Give inspector access to state for patrol editor
        self._inspector.set_state(self._state)
        self._outer_splitter.addWidget(self._inspector)
        # Track patrol placement mode
        self._patrol_placement_creature = None

        self._outer_splitter.setSizes([240, 900, 280])
        self._outer_splitter.setStretchFactor(1, 1)

        root.addWidget(self._outer_splitter)

        # Connect module state changes to viewport refresh
        self._state.on_change(self._on_module_changed)

    def _build_viewport_header(self) -> QWidget:
        bar = QFrame()
        bar.setFixedHeight(30)
        bar.setStyleSheet("background:#2d2d30; border-bottom:1px solid #3c3c3c;")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(6)

        self._mode_label = QLabel("EDIT MODE")
        self._mode_label.setStyleSheet("color:#4ec9b0; font-weight:bold; font-size:8pt;")
        layout.addWidget(self._mode_label)

        layout.addSpacing(8)

        # ── App Mode Switcher ────────────────────────────────────────────────
        self._mode_combo = QComboBox()
        self._mode_combo.addItem("⬛  Level Builder", "level_builder")
        self._mode_combo.addItem("✏  Module Editor", "module_editor")
        self._mode_combo.setFixedHeight(22)
        self._mode_combo.setFixedWidth(160)
        self._mode_combo.setToolTip(
            "Level Builder: assemble rooms and build new maps\n"
            "Module Editor: full KotOR-style editing with Maya-like tools")
        self._mode_combo.setStyleSheet(
            "QComboBox{background:#1e3a5f;color:#4ec9b0;border:1px solid #2a5a8f;"
            "border-radius:3px;padding:0 6px;font-size:8pt;font-weight:bold;}"
            "QComboBox:hover{background:#1a4a7a;border-color:#4ec9b0;}"
            "QComboBox::drop-down{border:none;}"
        )
        self._mode_combo.currentIndexChanged.connect(self._on_app_mode_changed)
        layout.addWidget(self._mode_combo)

        layout.addSpacing(8)

        self._module_label = QLabel("No module loaded")
        self._module_label.setStyleSheet("color:#969696; font-size:8pt;")
        layout.addWidget(self._module_label)

        layout.addStretch()

        # Quick mode buttons
        def quick_btn(text, tooltip="", accent=False):
            b = QPushButton(text)
            b.setFixedHeight(22)
            b.setToolTip(tooltip)
            if accent:
                b.setStyleSheet(
                    "QPushButton{background:#0078d4;color:white;border:1px solid #1a8fe0;"
                    "border-radius:3px;padding:0 8px;font-size:8pt;font-weight:bold;}"
                    "QPushButton:hover{background:#1a8fe0;}"
                )
            else:
                b.setStyleSheet(
                    "QPushButton{background:#3c3c3c;color:#cccccc;border:1px solid #555;"
                    "border-radius:3px;padding:0 6px;font-size:8pt;}"
                    "QPushButton:hover{background:#4a4a4a;color:white;}"
                )
            return b

        frame_btn = quick_btn("⊡ Frame All", "Fit camera to all objects (F)")
        frame_btn.clicked.connect(self._viewport.frame_all)
        layout.addWidget(frame_btn)

        wm_btn = quick_btn("⊞ Walkmesh", "Toggle walkmesh overlay (W)")
        wm_btn.setCheckable(True)
        wm_btn.setChecked(True)
        wm_btn.clicked.connect(lambda checked: self._viewport.toggle_walkmesh(checked))
        self._walkmesh_btn = wm_btn
        layout.addWidget(wm_btn)

        validate_btn = quick_btn("✓ Validate", "Check for errors")
        validate_btn.clicked.connect(self._validate_module)
        layout.addWidget(validate_btn)

        save_btn = quick_btn("💾 Save GIT", "Save .GIT to disk (Ctrl+S)", accent=True)
        save_btn.clicked.connect(self._save_module)
        layout.addWidget(save_btn)

        layout.addStretch()

        # ── Play / Stop button ────────────────────────────────────────────────
        self._play_btn = quick_btn("▶  Play", "Start walk preview mode", accent=True)
        self._play_btn.setStyleSheet(
            "QPushButton{background:#1a8a3a;color:white;border:1px solid #2aaa4a;"
            "border-radius:3px;padding:0 10px;font-size:8pt;font-weight:bold;}"
            "QPushButton:hover{background:#2aaa4a;}"
            "QPushButton:pressed{background:#0f6028;}"
        )
        self._play_btn.clicked.connect(self._toggle_play_mode)
        layout.addWidget(self._play_btn)

        # Tutorial help button
        tutorial_btn = quick_btn("?  Help", "Open Interactive Tutorial (F1)")
        tutorial_btn.setStyleSheet(
            "QPushButton{background:#2a2a4a;color:#88aaee;border:1px solid #3a3a6a;"
            "border-radius:3px;padding:0 8px;font-size:8pt;font-weight:bold;}"
            "QPushButton:hover{background:#3a3a6a;color:#aaccff;border-color:#4ec9b0;}"
        )
        tutorial_btn.clicked.connect(self._open_tutorial)
        layout.addWidget(tutorial_btn)

        # IPC status dots
        self._gs_dot = QLabel("●")
        self._gs_dot.setToolTip("GhostScripter IPC")
        self._gs_dot.setStyleSheet("color:#555555; font-size:8pt; margin-left:8px;")
        layout.addWidget(self._gs_dot)

        self._gr_dot = QLabel("●")
        self._gr_dot.setToolTip("GhostRigger IPC")
        self._gr_dot.setStyleSheet("color:#555555; font-size:8pt;")
        layout.addWidget(self._gr_dot)

        return bar

    def _build_bottom_tabs(self) -> QTabWidget:
        """Build the bottom panel with Output Log, Room Grid, Walkmesh, and Area Properties tabs."""
        tabs = QTabWidget()
        tabs.setMinimumHeight(120)
        tabs.setFont(QFont("Segoe UI", 8))
        tabs.setStyleSheet(
            "QTabWidget::pane { border-top:1px solid #3c3c3c; background:#1e1e1e; }"
            "QTabBar::tab { background:#252526; color:#969696; padding:3px 10px;"
            " border:1px solid #3c3c3c; font-size:8pt; }"
            "QTabBar::tab:selected { background:#1e1e1e; color:#d4d4d4; border-bottom:none; }"
            "QTabBar::tab:first { color:#4ec9b0; font-weight:bold; }"  # highlight Room Grid
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
        self._output_log.setMaximumBlockCount(500)
        self._output_log.setFont(QFont("Consolas", 8))
        self._output_log.setStyleSheet(
            "QPlainTextEdit { background:#1e1e1e; color:#d4d4d4; border:none; }"
        )
        log_layout.addWidget(self._output_log)
        tabs.addTab(log_widget, "Output Log")

        # ── Walkmesh Editor tab ────────────────────────────────────────────
        self._walkmesh_panel = WalkmeshPanel()
        self._walkmesh_panel.wok_loaded.connect(self._on_wok_loaded)
        tabs.addTab(self._walkmesh_panel, "Walkmesh (WOK)")

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
        fm.addAction(self._action("New Module…",    self.new_module,  "Ctrl+Shift+N"))
        fm.addAction(self._action("Open GIT File…", self.open_git,    "Ctrl+O"))
        fm.addAction(self._action("Open Project…",  self.open_project))
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
        tb = QToolBar("Main Toolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(16, 16))
        tb.setObjectName("mainToolbar")
        tb.setStyleSheet("QToolBar { background:#2d2d30; border-bottom:1px solid #3c3c3c; "
                         "spacing:3px; padding:2px; }")
        self.addToolBar(tb)

        def btn(label, slot, tooltip="", accent=False):
            b = QPushButton(label)
            b.clicked.connect(slot)
            b.setToolTip(tooltip)
            b.setFixedHeight(26)
            if accent:
                b.setStyleSheet(
                    "QPushButton{background:#0078d4;color:white;border:1px solid #1a8fe0;"
                    "border-radius:3px;padding:0 10px;font-weight:bold;}"
                    "QPushButton:hover{background:#1a8fe0;}"
                )
            else:
                b.setStyleSheet(
                    "QPushButton{background:#3c3c3c;color:#cccccc;border:1px solid #555;"
                    "border-radius:3px;padding:0 8px;}"
                    "QPushButton:hover{background:#4a4a4a;color:white;}"
                )
            return b

        tb.addWidget(btn("New Module",  self.new_module,     "Create new module"))
        tb.addWidget(btn("Open GIT",    self.open_git,       "Open .GIT file"))
        tb.addWidget(btn("Save",        self._save_module,   "Save .GIT", accent=True))
        tb.addSeparator()
        tb.addWidget(btn("Undo",        self._undo,          "Undo last action (Ctrl+Z)"))
        tb.addWidget(btn("Redo",        self._redo,          "Redo (Ctrl+Y)"))
        tb.addSeparator()
        tb.addWidget(btn("⊡ Frame All",  self._viewport.frame_all, "Fit camera to scene (F)"))
        tb.addWidget(btn("✓ Validate",  self._validate_module, "Check for errors"))
        tb.addSeparator()
        tb.addWidget(btn("Set Game Dir", self._set_game_dir, "Set KotOR game directory"))
        tb.addWidget(btn("Load Assets",  self._load_game_assets, "Load assets from game"))

    # ── Status bar ────────────────────────────────────────────────────────────

    def _setup_statusbar(self):
        sb = self.statusBar()
        sb.setStyleSheet("QStatusBar { background:#007acc; color:white; font-size:8pt; }")

        self._status_main = QLabel(
            "No module open  —  File › New Module…  or  open the Room Grid tab below")
        self._status_main.setStyleSheet("color:white; padding: 0 8px;")
        sb.addWidget(self._status_main)

        self._status_objects = QLabel("0 objects")
        self._status_objects.setStyleSheet(
            "color:white; padding: 0 8px; border-left:1px solid rgba(255,255,255,0.3);")
        sb.addPermanentWidget(self._status_objects)

        self._status_cam = QLabel("Camera: 0,0,0")
        self._status_cam.setStyleSheet(
            "color:white; padding: 0 8px; border-left:1px solid rgba(255,255,255,0.3);")
        sb.addPermanentWidget(self._status_cam)

        ver_lbl = QLabel(f"GModular {APP_VERSION}")
        ver_lbl.setStyleSheet(
            "color:white; padding: 0 8px; border-left:1px solid rgba(255,255,255,0.3);")
        sb.addPermanentWidget(ver_lbl)

    # ── Theme ─────────────────────────────────────────────────────────────────

    def _apply_theme(self):
        """Apply VS Code-inspired dark theme."""
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background: #1e1e1e;
                color: #d4d4d4;
            }
            QMenuBar {
                background: #3c3c3c;
                color: #cccccc;
                border-bottom: 1px solid #555;
            }
            QMenuBar::item:selected { background: #094771; }
            QMenu {
                background: #252526;
                color: #cccccc;
                border: 1px solid #3c3c3c;
            }
            QMenu::item:selected { background: #094771; }
            QToolBar {
                background: #2d2d30;
                border-bottom: 1px solid #3c3c3c;
                spacing: 3px;
            }
            QSplitter::handle { background: #3c3c3c; }
            QScrollBar:vertical {
                background: #1e1e1e;
                width: 10px;
            }
            QScrollBar::handle:vertical {
                background: #555;
                border-radius: 4px;
            }
            QGroupBox {
                color: #dcdcaa;
                border: 1px solid #3c3c3c;
                border-radius: 3px;
                margin-top: 8px;
                padding-top: 6px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
            }
            QTabWidget::pane { border: 1px solid #3c3c3c; background: #1e1e1e; }
            QTabBar::tab {
                background: #2d2d30;
                color: #969696;
                padding: 4px 10px;
                border: 1px solid #3c3c3c;
            }
            QTabBar::tab:selected {
                background: #1e1e1e;
                color: #d4d4d4;
                border-bottom: none;
            }
            QLineEdit, QDoubleSpinBox, QComboBox {
                background: #3c3c3c;
                color: #d4d4d4;
                border: 1px solid #555;
                border-radius: 2px;
                padding: 2px 4px;
            }
            QLineEdit:focus, QDoubleSpinBox:focus { border: 1px solid #007acc; }
            QComboBox::drop-down { border: none; }
            QPushButton {
                background: #3c3c3c;
                color: #cccccc;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 3px 8px;
            }
            QPushButton:hover { background: #4a4a4a; color: white; }
            QPushButton:pressed { background: #0078d4; color: white; }
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
        if not self._state.is_open:
            self.log("⚠ No module open")
            return
        if self._state.project:
            self._state.save()
            self.log(f"✓ Saved: {self._state.project.git_path}")
        else:
            self._save_as()

    def _save_as(self):
        if not self._state.is_open:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save GIT As", "", "KotOR GIT Files (*.git)"
        )
        if path:
            self._state.save(git_path=path)
            # Also save IFO alongside the GIT when using Save As
            if self._state.ifo:
                ifo_path = path.replace(".git", ".ifo").replace(".GIT", ".ifo")
                try:
                    from ..formats.gff_writer import save_ifo
                    save_ifo(self._state.ifo, ifo_path)
                    self.log(f"✓ IFO saved to: {ifo_path}")
                except Exception as e:
                    self.log(f"⚠ IFO save failed: {e}")
            self.log(f"✓ Saved to: {path}")
            self._add_recent_file(path)

    # ── Edit Actions ──────────────────────────────────────────────────────────

    def _undo(self):
        desc = self._state.undo()
        if desc:
            self.log(f"↩ Undo: {desc}")
            self._update_object_count()
        else:
            self.log("⚠ Nothing to undo")

    def _redo(self):
        desc = self._state.redo()
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
        key = Path(d) / "chitin.key"
        if not key.exists():
            QMessageBox.warning(self, "Invalid",
                                f"chitin.key not found in:\n{d}")
            return
        self._game_dir = Path(d)
        self._save_settings()
        self.log(f"✓ Game directory: {d}")
        self._status_main.setText(f"Game: {Path(d).name}")
        QMessageBox.information(self, "Game Directory Set",
            f"KotOR directory set:\n{d}\n\nClick 'Load Assets' to populate the palette.")

    def _load_game_assets(self):
        if not self._game_dir or not self._game_dir.exists():
            self._set_game_dir()
            return
        # Determine game tag (K1 vs K2)
        tag = "K2" if (self._game_dir / "swkotor2.exe").exists() else "K1"
        self._rm.set_game(str(self._game_dir), tag)
        self.log(f"Scanning {tag} game assets…")

        # List placeables (UTP files), creatures (UTC), etc.
        try:
            from ..formats.archives import EXT_TO_TYPE
            placeables = self._rm.list_resources(EXT_TO_TYPE.get("utp", 2043))
            creatures  = self._rm.list_resources(EXT_TO_TYPE.get("utc", 2030))
            doors      = self._rm.list_resources(EXT_TO_TYPE.get("utd", 2041))

            if placeables:
                self._palette.populate_from_game(placeables, "placeable")
                self.log(f"  Loaded {len(placeables)} placeables")
            if creatures:
                self._palette.populate_from_game(creatures, "creature")
                self.log(f"  Loaded {len(creatures)} creatures")
            if doors:
                self._palette.populate_from_game(doors, "door")
                self.log(f"  Loaded {len(doors)} doors")
        except Exception as e:
            self.log(f"✗ Asset load error: {e}")

        # Refresh Room Assembly palette with MDL room names from game
        try:
            mdl_type = EXT_TO_TYPE.get("mdl", 2002)
            room_mdls = self._rm.list_resources(mdl_type)
            # Filter to environment/room meshes (not creature/placeable models)
            rooms = [r for r in room_mdls
                     if len(r) > 4
                     and not r.startswith("c_")
                     and not r.startswith("p_")
                     and not r.startswith("w_")
                     and not r.startswith("i_")]
            if rooms and self._room_panel:
                self._room_panel.set_available_rooms(rooms[:400])
                self.log(f"  Loaded {len(rooms)} room MDLs into Room Grid palette")
        except Exception:
            pass  # Room palette update is non-critical

    # ── Validation ────────────────────────────────────────────────────────────

    def _validate_module(self):
        issues = self._state.validate()
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
        """Called when user clicks 'Place' in the palette."""
        self._viewport.set_placement_mode(True, asset.resref,
                                          getattr(asset, "asset_type", "placeable"))
        self._placement_active = True
        kind = getattr(asset, "asset_type", "placeable").capitalize()
        self._mode_label.setText(f"PLACE MODE  [ {asset.resref} ({kind}) ]")
        self._mode_label.setStyleSheet("color:#ff8c00; font-weight:bold; font-size:8pt;")
        self.log(f"Placement mode: {asset.resref} ({kind}) — click in viewport to place")

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
            self._mode_label.setText("LEVEL BUILDER")
            self._mode_label.setStyleSheet("color:#4ec9b0; font-weight:bold; font-size:8pt;")
            self.log("⬛ Mode: Level Builder — assemble rooms, place objects, build your map")
            # Show Room Grid tab
            if self._bottom_tabs:
                self._bottom_tabs.setCurrentIndex(0)
        else:
            self._mode_label.setText("MODULE EDITOR")
            self._mode_label.setStyleSheet("color:#9cdcfe; font-weight:bold; font-size:8pt;")
            self.log("✏  Mode: Module Editor — full KotOR module editing, "
                     "walkmesh tools, MDL import/export")

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
        self._mode_label.setText("EDIT MODE")
        self._mode_label.setStyleSheet("color:#4ec9b0; font-weight:bold; font-size:8pt;")
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
        """
        try:
            self._viewport.load_rooms(rooms)
            # Also register MDL file paths so the viewport can find geometry
            if self._room_panel and hasattr(self._room_panel, 'get_mdl_paths'):
                for name, path in self._room_panel.get_mdl_paths().items():
                    # Propagate mdl_path back onto each room instance
                    for ri in rooms:
                        n = getattr(ri, 'model_name', None) or getattr(ri, 'name', '')
                        if n.lower() == name.lower():
                            ri.mdl_path = path
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
        from PyQt5.QtWidgets import QDialog, QVBoxLayout
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
            "Part of the KotorModTools Suite:<br>"
            "• GModular — Module Editor<br>"
            "• GhostScripter — Script IDE<br>"
            "• GhostRigger — Model Rigger<br><br>"
            "GPL-3.0  |  KotOR Community"
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
