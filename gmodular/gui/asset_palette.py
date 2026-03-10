"""
GModular — Asset Palette Panel
Displays available placeables, creatures, doors from:
  - Loaded KotOR game directories (via ResourceManager)
  - Custom project templates
  - Pre-built common objects

The user can drag/double-click to place objects in the viewport.
"""
from __future__ import annotations
import os
import logging
from typing import Optional, List, Dict
from pathlib import Path

try:
    from PyQt5.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
        QPushButton, QListWidget, QListWidgetItem, QTabWidget,
        QComboBox, QGroupBox, QScrollArea, QFrame, QSplitter,
        QAbstractItemView, QSizePolicy, QToolTip,
    )
    from PyQt5.QtCore import Qt, pyqtSignal, QMimeData
    from PyQt5.QtGui import QFont, QDrag, QColor
    _HAS_QT = True
except ImportError:
    _HAS_QT = False
    QWidget = object      # type: ignore[misc,assignment]
    QListWidget = object  # type: ignore[misc,assignment]
    QListWidgetItem = object  # type: ignore[misc,assignment]
    class pyqtSignal:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs): pass
        def __set_name__(self, owner, name): pass

log = logging.getLogger(__name__)


# ── Pre-built common KotOR placeables ──────────────────────────────────────

_COMMON_PLACEABLES = [
    # (display_name, resref, template_resref)
    ("Chair",                   "plc_chair01",    "plc_chair01"),
    ("Crate (Med)",             "plc_cratemedium","plc_cratemedium"),
    ("Crate (Large)",           "plc_cratelarge", "plc_cratelarge"),
    ("Crate (Small)",           "plc_cratesmall", "plc_cratesmall"),
    ("Computer Panel",          "plc_comp01",     "plc_comp01"),
    ("Footlocker",              "plc_footlkr01",  "plc_footlkr01"),
    ("Workbench",               "plc_workbnch01", "plc_workbnch01"),
    ("Medical Station",         "plc_medical",    "plc_medical"),
    ("Cantina Bar",             "plc_bar",        "plc_bar"),
    ("Monitor",                 "plc_monitor01",  "plc_monitor01"),
    ("Datapad",                 "plc_datapad01",  "plc_datapad01"),
    ("Holocron",                "plc_holcrn01",   "plc_holcrn01"),
    ("Corpse",                  "plc_corpse01",   "plc_corpse01"),
    ("Barrel",                  "plc_barrel01",   "plc_barrel01"),
    ("Container (Generic)",     "plc_container01","plc_container01"),
]

_COMMON_CREATURES = [
    ("Human Commoner (M)",  "n_commoner01m",  "n_commoner01m"),
    ("Human Commoner (F)",  "n_commoner01f",  "n_commoner01f"),
    ("Jedi Knight",         "n_jediknight01", "n_jediknight01"),
    ("Dark Jedi",           "n_drkjedi01",    "n_drkjedi01"),
    ("Republic Soldier",    "n_rpbsldur",     "n_rpbsldur"),
    ("Sith Soldier",        "n_sthsldr01",    "n_sthsldr01"),
    ("Battle Droid",        "c_drdastro",     "c_drdastro"),
    ("Protocol Droid",      "c_drdhrk",       "c_drdhrk"),
    ("Bantha",              "c_bantha",       "c_bantha"),
    ("Rancor",              "c_rancor",       "c_rancor"),
    ("Sand People",         "n_sandpeople",   "n_sandpeople"),
    ("Tusken Raider",       "n_tusken",       "n_tusken"),
]

_COMMON_DOORS = [
    ("Metal Door (Standard)", "door_metal01", "door_metal01"),
    ("Metal Door (Blast)",    "door_metal02", "door_metal02"),
    ("Wooden Door",           "door_wood01",  "door_wood01"),
    ("Rusted Door",           "door_rust01",  "door_rust01"),
    ("Steel Vault",           "door_vault01", "door_vault01"),
    ("Airlock",               "door_airlock", "door_airlock"),
    ("Hatch",                 "door_hatch01", "door_hatch01"),
]

_COMMON_WAYPOINTS = [
    ("Player Start",     "wp_start",      "wp_start"),
    ("Patrol Point",     "wp_patrol01",   "wp_patrol01"),
    ("Spawn Point",      "wp_spawn01",    "wp_spawn01"),
    ("Shop Entry",       "wp_shopentry",  "wp_shopentry"),
]

_COMMON_TRIGGERS = [
    ("Area Transition",  "trg_trans01",   "trg_trans01"),
    ("Trap (Moderate)",  "trg_trap01",    "trg_trap01"),
    ("Trap (Strong)",    "trg_trap02",    "trg_trap02"),
    ("Trigger (Generic)","trg_generic01", "trg_generic01"),
    ("Cut-scene Trigger","trg_cutscene01","trg_cutscene01"),
]

_COMMON_SOUNDS = [
    ("Ambient Forest",   "as_an_forest1", "as_an_forest1"),
    ("Ambient Caves",    "as_an_cave1",   "as_an_cave1"),
    ("Ambient Space",    "as_an_space1",  "as_an_space1"),
    ("Ambient Cantina",  "as_mu_cantina1","as_mu_cantina1"),
    ("Ambient Wind",     "as_an_wind1",   "as_an_wind1"),
]

_COMMON_STORES = [
    ("General Store",    "shop_general",  "shop_general"),
    ("Weapons Dealer",   "shop_weapons",  "shop_weapons"),
    ("Armor Dealer",     "shop_armor",    "shop_armor"),
    ("Medical Supplies", "shop_medical",  "shop_medical"),
    ("Jawa Trader",      "shop_jawa01",   "shop_jawa01"),
]


# ─────────────────────────────────────────────────────────────────────────────
#  Asset Palette Widget
# ─────────────────────────────────────────────────────────────────────────────

class AssetItem:
    """Represents a single asset in the palette."""
    __slots__ = ("display_name", "resref", "template_resref", "asset_type")

    def __init__(self, display_name: str, resref: str,
                 template_resref: str = "", asset_type: str = "placeable"):
        self.display_name   = display_name
        self.resref         = resref[:16]
        self.template_resref = (template_resref or resref)[:16]
        self.asset_type     = asset_type  # "placeable", "creature", "door", "waypoint"


class AssetPalette(QWidget):
    """
    Asset palette with tabs: Placeables / Creatures / Doors / Waypoints / Custom.
    Double-click or 'Place' button to activate placement mode in viewport.
    """

    # Emitted when user wants to place an asset
    place_asset = pyqtSignal(object)   # AssetItem

    def __init__(self, parent=None):
        super().__init__(parent)
        self._assets: Dict[str, List[AssetItem]] = {
            "placeable": [],
            "creature":  [],
            "door":      [],
            "waypoint":  [],
            "trigger":   [],
            "sound":     [],
            "store":     [],
        }
        self._search_text = ""
        self._setup_ui()
        self._populate_defaults()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(3)

        # Search bar
        search_row = QHBoxLayout()
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("🔍  Filter assets…")
        self._search_edit.setFont(QFont("Segoe UI", 8))
        self._search_edit.textChanged.connect(self._on_search)
        search_row.addWidget(self._search_edit)
        layout.addLayout(search_row)

        # Tab widget
        self._tabs = QTabWidget()
        self._tabs.setFont(QFont("Segoe UI", 8))

        self._placeables_list = self._make_list()
        self._creatures_list  = self._make_list()
        self._doors_list      = self._make_list()
        self._waypoints_list  = self._make_list()
        self._triggers_list   = self._make_list()
        self._sounds_list     = self._make_list()
        self._stores_list     = self._make_list()

        self._tabs.addTab(self._placeables_list,  "Placeables")
        self._tabs.addTab(self._creatures_list,   "Creatures")
        self._tabs.addTab(self._doors_list,       "Doors")
        self._tabs.addTab(self._waypoints_list,   "Waypoints")
        self._tabs.addTab(self._triggers_list,    "Triggers")
        self._tabs.addTab(self._sounds_list,      "Sounds")
        self._tabs.addTab(self._stores_list,      "Stores")
        layout.addWidget(self._tabs)

        # Bottom action buttons
        btn_row = QHBoxLayout()

        self._place_btn = QPushButton("⊕  Place Selected")
        self._place_btn.setToolTip("Activate placement mode — click in viewport to place")
        self._place_btn.clicked.connect(self._on_place_clicked)
        self._place_btn.setStyleSheet("""
            QPushButton { background:#0078d4; color:white; border:1px solid #1a8fe0;
                          border-radius:3px; padding:4px 8px; font-weight:bold; }
            QPushButton:hover { background:#1a8fe0; }
            QPushButton:pressed { background:#005a9e; }
        """)
        btn_row.addWidget(self._place_btn)

        self._custom_btn = QPushButton("+ Custom ResRef")
        self._custom_btn.setToolTip("Place a custom ResRef by typing it manually")
        self._custom_btn.clicked.connect(self._add_custom)
        self._custom_btn.setStyleSheet(
            "QPushButton { background:#3c3c3c; color:#cccccc; border:1px solid #555; "
            "border-radius:3px; padding:4px 8px; } "
            "QPushButton:hover { background:#4a4a4a; }"
        )
        btn_row.addWidget(self._custom_btn)
        layout.addLayout(btn_row)

        # Status label
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color:#569cd6; font-size:8pt;")
        layout.addWidget(self._status_lbl)

    def _make_list(self) -> QListWidget:
        lst = QListWidget()
        lst.setFont(QFont("Consolas", 8))
        lst.setSelectionMode(QAbstractItemView.SingleSelection)
        lst.setStyleSheet("""
            QListWidget { background:#1e1e1e; color:#d4d4d4; border:1px solid #3c3c3c; }
            QListWidget::item:selected { background:#094771; color:white; }
            QListWidget::item:hover { background:#2a2d2e; }
        """)
        lst.itemDoubleClicked.connect(self._on_double_click)
        return lst

    def _populate_defaults(self):
        for name, resref, template in _COMMON_PLACEABLES:
            self._add_item("placeable", AssetItem(name, resref, template, "placeable"))
        for name, resref, template in _COMMON_CREATURES:
            self._add_item("creature", AssetItem(name, resref, template, "creature"))
        for name, resref, template in _COMMON_DOORS:
            self._add_item("door", AssetItem(name, resref, template, "door"))
        for name, resref, template in _COMMON_WAYPOINTS:
            self._add_item("waypoint", AssetItem(name, resref, template, "waypoint"))
        for name, resref, template in _COMMON_TRIGGERS:
            self._add_item("trigger", AssetItem(name, resref, template, "trigger"))
        for name, resref, template in _COMMON_SOUNDS:
            self._add_item("sound", AssetItem(name, resref, template, "sound"))
        for name, resref, template in _COMMON_STORES:
            self._add_item("store", AssetItem(name, resref, template, "store"))

    def _add_item(self, asset_type: str, asset: AssetItem):
        self._assets[asset_type].append(asset)
        list_widget = self._list_for_type(asset_type)
        if list_widget is None:
            return
        item = QListWidgetItem(f"{asset.display_name}")
        item.setToolTip(f"ResRef: {asset.resref}\nTemplate: {asset.template_resref}")
        item.setData(Qt.UserRole, asset)
        item.setForeground(QColor(self._color_for_type(asset_type)))
        list_widget.addItem(item)

    def _list_for_type(self, asset_type: str) -> Optional[QListWidget]:
        return {
            "placeable": self._placeables_list,
            "creature":  self._creatures_list,
            "door":      self._doors_list,
            "waypoint":  self._waypoints_list,
            "trigger":   self._triggers_list,
            "sound":     self._sounds_list,
            "store":     self._stores_list,
        }.get(asset_type)

    def _color_for_type(self, asset_type: str) -> str:
        return {
            "placeable": "#88aaff",
            "creature":  "#ffaa88",
            "door":      "#ffff88",
            "waypoint":  "#aa88ff",
            "trigger":   "#88ffaa",
            "sound":     "#aaffff",
            "store":     "#aaffaa",
        }.get(asset_type, "#d4d4d4")

    def _current_item(self) -> Optional[AssetItem]:
        """Get the currently selected asset from whichever tab is active."""
        _all_lists = [
            self._placeables_list, self._creatures_list,
            self._doors_list,      self._waypoints_list,
            self._triggers_list,   self._sounds_list,
            self._stores_list,
        ]
        idx = self._tabs.currentIndex()
        if idx < 0 or idx >= len(_all_lists):
            return None
        lst = _all_lists[idx]
        sel = lst.selectedItems()
        if sel:
            return sel[0].data(Qt.UserRole)
        return None

    def _on_place_clicked(self):
        asset = self._current_item()
        if asset:
            self.place_asset.emit(asset)
            self._status_lbl.setText(f"Placing: {asset.resref} — click in viewport")
        else:
            self._status_lbl.setText("Select an asset first")

    def _on_double_click(self, item: QListWidgetItem):
        asset = item.data(Qt.UserRole)
        if asset:
            self.place_asset.emit(asset)
            self._status_lbl.setText(f"Placing: {asset.resref} — click in viewport")

    def _add_custom(self):
        from PyQt5.QtWidgets import QInputDialog
        resref, ok = QInputDialog.getText(
            self, "Custom ResRef",
            "Enter ResRef to place (max 16 chars):\n"
            "(e.g. plc_chair01, c_bantha, n_jediknight01)"
        )
        if ok and resref.strip():
            resref = resref.strip()[:16]
            # Detect type from active tab
            _type_map = [
                "placeable", "creature", "door", "waypoint",
                "trigger", "sound", "store"
            ]
            idx = self._tabs.currentIndex()
            asset_type = _type_map[idx] if 0 <= idx < len(_type_map) else "placeable"
            asset = AssetItem(resref, resref, resref, asset_type)
            self.place_asset.emit(asset)
            self._status_lbl.setText(f"Placing custom {asset_type}: {resref} — click in viewport")

    def _on_search(self, text: str):
        self._search_text = text.lower()
        for lst in [self._placeables_list, self._creatures_list,
                    self._doors_list, self._waypoints_list,
                    self._triggers_list, self._sounds_list,
                    self._stores_list]:
            for i in range(lst.count()):
                item = lst.item(i)
                asset = item.data(Qt.UserRole)
                if asset:
                    visible = (not text or
                               text in asset.display_name.lower() or
                               text in asset.resref.lower())
                    item.setHidden(not visible)

    def populate_from_game(self, resrefs: List[str], asset_type: str = "placeable"):
        """Populate palette with ResRefs from the loaded game directory."""
        lst = self._list_for_type(asset_type)
        if lst is None:
            return
        # Add any new ResRefs not already in the list.
        # Build the set with .lower() so the membership test (also .lower())
        # is case-insensitive — prevents duplicate entries when the game
        # returns uppercase ResRefs (e.g. "PLC_CHAIR01") for existing items.
        existing = {self._assets[asset_type][i].resref.lower()
                    for i in range(len(self._assets[asset_type]))}
        added = 0
        for resref in sorted(resrefs):
            if resref.lower() not in existing:
                asset = AssetItem(resref, resref, resref, asset_type)
                self._add_item(asset_type, asset)
                added += 1
        if added:
            self._status_lbl.setText(f"Loaded {added} {asset_type}s from game directory")
        log.info(f"Palette: added {added} {asset_type}s from game")

    def clear_status(self):
        self._status_lbl.setText("")
