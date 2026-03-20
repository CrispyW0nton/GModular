"""
GhostRigger — Blueprint Field Editor
======================================
A structured field-editor panel that displays and edits UTC/UTP/UTD GFF
fields organised into logical sections.

Architecture:
  BlueprintFieldEditor  (QWidget)
    ├─ QTabWidget
    │    ├─ "Basic"  tab  → name/tag/resref/HP/AC/…
    │    ├─ "Stats"  tab  → ability scores, skills, feats (UTC only)
    │    └─ "Scripts" tab → OnSpawn/OnDeath/OnHeartbeat/… script ResRefs
    └─ QDialogButtonBox  → Save / Revert

Each field is represented by a FieldRow (QWidget) containing:
  QLabel(name) + QLineEdit/QSpinBox/QCheckBox + QLabel(type hint)

The editor emits field_changed(resref, field_name, value) when any field
is committed, allowing the IPC layer to sync changes to the registry.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

try:
    from qtpy.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
        QSpinBox, QDoubleSpinBox, QCheckBox, QTabWidget,
        QScrollArea, QFormLayout, QPushButton, QFrame,
        QSizePolicy,
    )
    from qtpy.QtCore import Qt, Signal
    from qtpy.QtGui import QFont
    _HAS_QT = True
except ImportError:
    _HAS_QT = False
    QWidget = object            # type: ignore
    Signal = lambda *a: None    # type: ignore


# ──────────────────────────────────────────────────────────────────────────────
# Field schema: (field_name, display_label, widget_type, default, section)
# widget_type: 'str' | 'int' | 'float' | 'bool' | 'resref'
# ──────────────────────────────────────────────────────────────────────────────

UTC_FIELDS: List[Tuple[str, str, str, Any, str]] = [
    # (field_name, label, widget_type, default, section)
    ("FirstName",       "First Name",       "str",    "",    "Basic"),
    ("LastName",        "Last Name",        "str",    "",    "Basic"),
    ("Tag",             "Tag",              "str",    "",    "Basic"),
    ("TemplateResRef",  "ResRef",           "resref", "",    "Basic"),
    ("Appearance_Type", "Appearance Type",  "int",    0,     "Basic"),
    ("RacialType",      "Racial Type",      "int",    6,     "Basic"),
    ("Gender",          "Gender",           "int",    0,     "Basic"),
    ("MaxHitPoints",    "Max HP",           "int",    8,     "Basic"),
    ("CurrentHitPoints","Current HP",       "int",    8,     "Basic"),
    ("ArmorClass",      "Armor Class",      "int",    10,    "Basic"),
    ("NaturalAC",       "Natural AC",       "int",    0,     "Basic"),
    ("Faction",         "Faction",          "int",    1,     "Basic"),
    ("Plot",            "Plot (unkillable)","bool",   False, "Basic"),
    ("IsPC",            "Is PC",            "bool",   False, "Basic"),
    # Stats tab
    ("Str",             "Strength",         "int",    8,     "Stats"),
    ("Dex",             "Dexterity",        "int",    8,     "Stats"),
    ("Con",             "Constitution",     "int",    8,     "Stats"),
    ("Int",             "Intelligence",     "int",    8,     "Stats"),
    ("Wis",             "Wisdom",           "int",    8,     "Stats"),
    ("Cha",             "Charisma",         "int",    8,     "Stats"),
    ("BaseAttackBonus", "Base Attack Bonus","int",    0,     "Stats"),
    ("ChallengeRating", "Challenge Rating", "float",  1.0,   "Stats"),
    ("ExperiencePoints","XP",               "int",    0,     "Stats"),
    ("GoodEvil",        "Good/Evil (0–100)","int",    50,    "Stats"),
    ("LawfulChaotic",   "Law/Chaos (0–100)","int",    50,    "Stats"),
    # Scripts tab
    ("ScriptSpawn",     "On Spawn",         "resref", "",    "Scripts"),
    ("ScriptDeath",     "On Death",         "resref", "",    "Scripts"),
    ("ScriptHeartbeat", "Heartbeat",        "resref", "",    "Scripts"),
    ("ScriptOnNotice",  "On Notice",        "resref", "",    "Scripts"),
    ("ScriptAttacked",  "On Attacked",      "resref", "",    "Scripts"),
    ("ScriptDamaged",   "On Damaged",       "resref", "",    "Scripts"),
    ("ScriptDialogue",  "Dialogue Script",  "resref", "",    "Scripts"),
    ("ScriptEndDialogu","End Dialogue",     "resref", "",    "Scripts"),
    ("ScriptSpellAt",   "On Spell Cast At", "resref", "",    "Scripts"),
    ("Conversation",    "Dialogue ResRef",  "resref", "",    "Scripts"),
]

UTP_FIELDS: List[Tuple[str, str, str, Any, str]] = [
    ("Name",            "Name",             "str",    "",    "Basic"),
    ("Tag",             "Tag",              "str",    "",    "Basic"),
    ("TemplateResRef",  "ResRef",           "resref", "",    "Basic"),
    ("Appearance",      "Appearance",       "int",    0,     "Basic"),
    ("HasInventory",    "Has Inventory",    "bool",   False, "Basic"),
    ("Useable",         "Useable",          "bool",   True,  "Basic"),
    ("Static",          "Static",           "bool",   False, "Basic"),
    ("Plot",            "Plot",             "bool",   False, "Basic"),
    ("Hardness",        "Hardness",         "int",    5,     "Basic"),
    ("HP",              "Max HP",           "int",    10,    "Basic"),
    ("CurrentHP",       "Current HP",       "int",    10,    "Basic"),
    ("Faction",         "Faction",          "int",    1,     "Basic"),
    ("OnUsed",          "On Used",          "resref", "",    "Scripts"),
    ("OnClosed",        "On Closed",        "resref", "",    "Scripts"),
    ("OnOpen",          "On Open",          "resref", "",    "Scripts"),
    ("OnDamaged",       "On Damaged",       "resref", "",    "Scripts"),
    ("OnDeath",         "On Death",         "resref", "",    "Scripts"),
    ("OnHeartbeat",     "Heartbeat",        "resref", "",    "Scripts"),
    ("OnInventoryFull", "Inventory Full",   "resref", "",    "Scripts"),
    ("Conversation",    "Dialogue ResRef",  "resref", "",    "Scripts"),
]

UTD_FIELDS: List[Tuple[str, str, str, Any, str]] = [
    ("LocName",         "Name",             "str",    "",    "Basic"),
    ("Tag",             "Tag",              "str",    "",    "Basic"),
    ("TemplateResRef",  "ResRef",           "resref", "",    "Basic"),
    ("AppearanceID",    "Appearance",       "int",    0,     "Basic"),
    ("Locked",          "Locked",           "bool",   False, "Basic"),
    ("Lockable",        "Lockable",         "bool",   True,  "Basic"),
    ("OpenLockDC",      "Open Lock DC",     "int",    0,     "Basic"),
    ("CloseLockDC",     "Close Lock DC",    "int",    0,     "Basic"),
    ("Plot",            "Plot",             "bool",   False, "Basic"),
    ("HP",              "Max HP",           "int",    10,    "Basic"),
    ("CurrentHP",       "Current HP",       "int",    10,    "Basic"),
    ("Hardness",        "Hardness",         "int",    5,     "Basic"),
    ("Faction",         "Faction",          "int",    1,     "Basic"),
    ("OnOpen",          "On Open",          "resref", "",    "Scripts"),
    ("OnClosed",        "On Closed",        "resref", "",    "Scripts"),
    ("OnDamaged",       "On Damaged",       "resref", "",    "Scripts"),
    ("OnDeath",         "On Death",         "resref", "",    "Scripts"),
    ("OnHeartbeat",     "Heartbeat",        "resref", "",    "Scripts"),
    ("OnFailToOpen",    "On Fail To Open",  "resref", "",    "Scripts"),
    ("Conversation",    "Dialogue ResRef",  "resref", "",    "Scripts"),
]

_FIELD_SCHEMAS: Dict[str, List[Tuple]] = {
    "utc": UTC_FIELDS,
    "utp": UTP_FIELDS,
    "utd": UTD_FIELDS,
}


def get_field_schema(bp_type: str) -> List[Tuple]:
    """Return field schema list for a given blueprint type."""
    return _FIELD_SCHEMAS.get(bp_type.lower(), UTC_FIELDS)


# ──────────────────────────────────────────────────────────────────────────────
# Qt Widget
# ──────────────────────────────────────────────────────────────────────────────

class BlueprintFieldEditor(QWidget if _HAS_QT else object):
    """
    Structured field editor for a single Blueprint.

    Signals:
        field_changed(resref: str, field_name: str, value)  — emitted on edit
        save_requested(resref: str)                          — Save button
        revert_requested(resref: str)                        — Revert button
    """

    if _HAS_QT:
        field_changed = Signal(str, str, object)
        save_requested = Signal(str)
        revert_requested = Signal(str)

    STYLE = """
        QWidget { background: #1e1e1e; color: #d4d4d4; }
        QLabel { color: #d4d4d4; font-size: 12px; }
        QLineEdit, QSpinBox, QDoubleSpinBox {
            background: #252526; color: #d4d4d4;
            border: 1px solid #3e3e42; border-radius: 3px;
            padding: 2px 4px;
        }
        QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
            border-color: #007acc;
        }
        QCheckBox { color: #d4d4d4; }
        QTabWidget::pane { border: 1px solid #3e3e42; }
        QTabBar::tab {
            background: #252526; color: #9d9d9d;
            padding: 4px 12px; border: 1px solid #3e3e42;
        }
        QTabBar::tab:selected { background: #1e1e1e; color: #d4d4d4; }
        QPushButton {
            background: #264f78; color: #d4d4d4;
            border: 1px solid #3e3e42; border-radius: 3px;
            padding: 4px 12px;
        }
        QPushButton:hover { background: #1a6093; }
        QScrollArea { border: none; }
        QFrame#section_header {
            background: #252526;
            border-bottom: 1px solid #3e3e42;
        }
    """

    def __init__(self, parent=None):
        if not _HAS_QT:
            return
        super().__init__(parent)
        self.setStyleSheet(self.STYLE)

        self._resref: str = ""
        self._bp_type: str = "utc"
        self._widgets: Dict[str, QWidget] = {}   # field_name → input widget
        self._building = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header label
        self._header = QLabel("No blueprint loaded")
        self._header.setStyleSheet(
            "background:#252526; color:#4ec9b0; font-size:13px;"
            "font-weight:bold; padding:6px 10px;"
            "border-bottom:1px solid #3e3e42;"
        )
        layout.addWidget(self._header)

        # Tabs
        self._tabs = QTabWidget()
        layout.addWidget(self._tabs, stretch=1)

        # Button row
        btn_row = QWidget()
        btn_row.setStyleSheet("background:#252526; border-top:1px solid #3e3e42;")
        btn_lay = QHBoxLayout(btn_row)
        btn_lay.setContentsMargins(8, 4, 8, 4)
        btn_lay.addStretch()
        self._btn_revert = QPushButton("Revert")
        self._btn_save = QPushButton("Save")
        btn_lay.addWidget(self._btn_revert)
        btn_lay.addWidget(self._btn_save)
        layout.addWidget(btn_row)

        self._btn_save.clicked.connect(self._on_save)
        self._btn_revert.clicked.connect(self._on_revert)

    # ── Public API ─────────────────────────────────────────────────────────────

    def load_blueprint(self, resref: str, bp_type: str,
                       fields: Dict[str, Any]) -> None:
        """Populate the editor with a blueprint's data."""
        if not _HAS_QT:
            return
        self._resref = resref
        self._bp_type = bp_type.lower()
        self._header.setText(
            f"{bp_type.upper()}  ·  {resref}"
        )
        self._rebuild_tabs(fields)

    def get_current_values(self) -> Dict[str, Any]:
        """Return current values from all input widgets."""
        if not _HAS_QT:
            return {}
        result: Dict[str, Any] = {}
        schema = get_field_schema(self._bp_type)
        for fname, _label, wtype, _default, _section in schema:
            w = self._widgets.get(fname)
            if w is None:
                continue
            if wtype in ("str", "resref"):
                result[fname] = w.text()
            elif wtype == "int":
                result[fname] = w.value()
            elif wtype == "float":
                result[fname] = w.value()
            elif wtype == "bool":
                result[fname] = w.isChecked()
        return result

    # ── Private helpers ────────────────────────────────────────────────────────

    def _rebuild_tabs(self, fields: Dict[str, Any]) -> None:
        if not _HAS_QT:
            return
        self._tabs.clear()
        self._widgets.clear()
        self._building = True

        schema = get_field_schema(self._bp_type)
        # Group fields by section
        sections: Dict[str, List[Tuple]] = {}
        for row in schema:
            section = row[4]
            sections.setdefault(section, []).append(row)

        for section_name, rows in sections.items():
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            container = QWidget()
            form = QFormLayout(container)
            form.setContentsMargins(12, 8, 12, 8)
            form.setSpacing(6)
            form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

            for fname, label, wtype, default, _sec in rows:
                value = fields.get(fname, default)
                w = self._make_widget(fname, wtype, value)
                self._widgets[fname] = w
                lbl = QLabel(label + ":")
                lbl.setStyleSheet("color:#9cdcfe;")
                form.addRow(lbl, w)

            scroll.setWidget(container)
            self._tabs.addTab(scroll, section_name)

        self._building = False

    def _make_widget(self, fname: str, wtype: str, value: Any) -> QWidget:
        if wtype in ("str", "resref"):
            w = QLineEdit()
            w.setText(str(value) if value else "")
            if wtype == "resref":
                w.setMaxLength(16)
                w.setPlaceholderText("resref (≤16 chars)")
            w.editingFinished.connect(
                lambda fn=fname, widget=w: self._on_field_changed(fn, widget.text())
            )
            return w

        if wtype == "int":
            w = QSpinBox()
            w.setRange(-32768, 32767)
            w.setValue(int(value) if value is not None else 0)
            w.valueChanged.connect(
                lambda v, fn=fname: self._on_field_changed(fn, v)
            )
            return w

        if wtype == "float":
            w = QDoubleSpinBox()
            w.setRange(-9999.0, 9999.0)
            w.setDecimals(3)
            w.setValue(float(value) if value is not None else 0.0)
            w.valueChanged.connect(
                lambda v, fn=fname: self._on_field_changed(fn, v)
            )
            return w

        if wtype == "bool":
            w = QCheckBox()
            w.setChecked(bool(value))
            w.stateChanged.connect(
                lambda v, fn=fname: self._on_field_changed(fn, bool(v))
            )
            return w

        # fallback
        w = QLineEdit()
        w.setText(str(value))
        return w

    def _on_field_changed(self, field_name: str, value: Any) -> None:
        if self._building:
            return
        log.debug("Field changed: %s.%s = %r", self._resref, field_name, value)
        if _HAS_QT:
            self.field_changed.emit(self._resref, field_name, value)

    def _on_save(self) -> None:
        if _HAS_QT:
            self.save_requested.emit(self._resref)

    def _on_revert(self) -> None:
        if _HAS_QT:
            self.revert_requested.emit(self._resref)
