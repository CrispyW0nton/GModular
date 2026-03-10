"""
GModular — Inspector Panel
Displays and edits properties of selected GIT objects.

P7 — each script ResRef field has a pencil icon button (opens in GhostScripter).
P9 — "Edit in GhostRigger" button for Creature, Placeable, Door.
P4 — PatrolPathEditor embedded in creature inspector.
"""
from __future__ import annotations
import logging
from typing import Optional, Any

try:
    from PyQt5.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
        QPushButton, QDoubleSpinBox, QGroupBox, QFormLayout,
        QComboBox, QPlainTextEdit, QScrollArea, QSizePolicy,
        QTabWidget, QFrame, QSpacerItem,
    )
    from PyQt5.QtCore import Qt, pyqtSignal
    from PyQt5.QtGui import QFont
    _HAS_QT = True
except ImportError:
    _HAS_QT = False
    QWidget = object       # type: ignore[misc,assignment]
    QLineEdit = object     # type: ignore[misc,assignment]
    QDoubleSpinBox = object  # type: ignore[misc,assignment]
    QComboBox = object     # type: ignore[misc,assignment]
    class pyqtSignal:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs): pass
        def __set_name__(self, owner, name): pass

from ..formats.gff_types import (
    GITPlaceable, GITCreature, GITDoor, GITWaypoint,
    GITTrigger, GITSoundObject, GITStoreObject,
)

# Lazy import to avoid circular imports at class-definition time
def _get_patrol_editor_class():
    try:
        from .patrol_editor import PatrolPathEditor
        return PatrolPathEditor
    except Exception:
        return None

log = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("color:#4ec9b0; font-weight:bold; font-size:8pt; "
                      "padding-bottom:2px; border-bottom:1px solid #3c3c3c;")
    return lbl


def _make_hwidget(layout: QHBoxLayout) -> QWidget:
    """Wrap a QHBoxLayout in a plain QWidget for use in QFormLayout rows."""
    w = QWidget()
    w.setLayout(layout)
    return w


def _form_row(label: str, widget: QWidget) -> QHBoxLayout:
    row = QHBoxLayout()
    lbl = QLabel(label + ":")
    lbl.setStyleSheet("color:#969696; font-size:8pt;")
    lbl.setFixedWidth(90)
    row.addWidget(lbl)
    row.addWidget(widget)
    return row


class ResRefEdit(QLineEdit):
    """LineEdit that enforces 16-char ASCII ResRef."""
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.setMaxLength(16)
        self.setFont(QFont("Consolas", 9))


class ScriptCombo(QComboBox):
    """ComboBox for script ResRef dropdown (populated from GhostScripter IPC)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.addItem("(none)")
        self.setFont(QFont("Consolas", 8))
        self.setInsertPolicy(QComboBox.InsertAtTop)

    def set_scripts(self, scripts: list):
        current = self.currentText()
        self.clear()
        self.addItem("(none)")
        for s in sorted(scripts):
            # Strip .ncs extension if present
            name = s.replace(".ncs", "").replace(".nss", "")
            self.addItem(name)
        idx = self.findText(current)
        if idx >= 0:
            self.setCurrentIndex(idx)

    def selected_script(self) -> str:
        t = self.currentText()
        return "" if t in ("(none)", "") else t[:16]


# ─────────────────────────────────────────────────────────────────────────────
#  Inspector Panel
# ─────────────────────────────────────────────────────────────────────────────

class InspectorPanel(QWidget):
    """
    Right-side inspector panel.
    Shows editable properties for any selected GIT object.

    P7: each script field has a pencil icon button (opens in GhostScripter).
    P9: "Edit in GhostRigger" button for Creature, Placeable, Door.
    P4: PatrolPathEditor section for Creature.
    """

    property_changed     = pyqtSignal(object, str, object, object)  # obj, attr, old, new
    open_in_rigger       = pyqtSignal(str, str, str)     # P9: resref, ext, module_dir
    request_patrol_click = pyqtSignal(object)            # P4: creature object
    patrol_path_changed  = pyqtSignal(object, list)      # P4: creature, [GITWaypoint]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._obj = None
        self._scripts: list = []
        self._state = None           # ModuleState, set by MainWindow
        self._module_dir: str = ""
        self._building = False       # suppress signals during rebuild
        self._patrol_editor = None   # current PatrolPathEditor if any

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Header
        self._type_label = QLabel("Nothing selected")
        self._type_label.setStyleSheet("color:#9cdcfe; font-weight:bold; font-size:10pt;"
                                        " padding:4px;")
        layout.addWidget(self._type_label)

        # Scrollable content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border:none; }")
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(2, 2, 2, 2)
        self._content_layout.setSpacing(4)
        scroll.setWidget(self._content)
        layout.addWidget(scroll)

        # Bottom: quick actions
        self._actions_frame = QFrame()
        actions_layout = QVBoxLayout(self._actions_frame)
        actions_layout.setContentsMargins(4, 4, 4, 4)
        actions_layout.setSpacing(3)

        self._open_script_btn = QPushButton("Open Script in GhostScripter")
        self._open_script_btn.setToolTip("Open the assigned script in GhostScripter editor")
        self._open_script_btn.clicked.connect(self._open_in_ghostscripter)
        self._open_script_btn.hide()
        actions_layout.addWidget(self._open_script_btn)

        self._compile_btn = QPushButton("Compile Script (via GhostScripter)")
        self._compile_btn.clicked.connect(self._compile_script)
        self._compile_btn.hide()
        actions_layout.addWidget(self._compile_btn)

        # P9: Edit in GhostRigger button
        self._edit_in_rigger_btn = QPushButton("Edit Blueprint in GhostRigger")
        self._edit_in_rigger_btn.setToolTip(
            "Open this object's blueprint (.utc/.utp/.utd) in GhostRigger for editing")
        self._edit_in_rigger_btn.setStyleSheet(
            "QPushButton { background:#1a3a5a; color:#9cdcfe; "
            "border:1px solid #2a5a8a; border-radius:3px; padding:4px; }"
            "QPushButton:hover { background:#2a5a8a; }")
        self._edit_in_rigger_btn.clicked.connect(self._open_in_ghostrigger)
        self._edit_in_rigger_btn.hide()
        actions_layout.addWidget(self._edit_in_rigger_btn)

        layout.addWidget(self._actions_frame)

        self._show_empty()

    # ── Public API ──────────────────────────────────────────────────────────

    def set_state(self, state):
        """Set the ModuleState reference (for patrol editor)."""
        self._state = state

    def set_module_dir(self, module_dir: str):
        """Set the module directory (for P9 IPC payload)."""
        self._module_dir = module_dir or ""

    def add_patrol_waypoint_at(self, x: float, y: float, z: float):
        """P4: Called by MainWindow when user clicks floor for patrol waypoint."""
        if self._patrol_editor is not None:
            self._patrol_editor.add_waypoint_at(x, y, z)
            if hasattr(self._patrol_editor, 'cancel_placement'):
                self._patrol_editor.cancel_placement()

    def set_scripts(self, scripts: list):
        """Update script dropdown options from GhostScripter IPC."""
        self._scripts = scripts
        # Update all visible script combos
        for combo in self._content.findChildren(ScriptCombo):
            combo.set_scripts(scripts)

    def inspect(self, obj):
        """Display properties for the given GIT object."""
        self._obj = obj
        self._rebuild()

    # ── Internal ────────────────────────────────────────────────────────────

    def _clear_content(self):
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _show_empty(self):
        self._clear_content()
        self._type_label.setText("Nothing selected")
        lbl = QLabel("Select an object in the viewport\nto view its properties.")
        lbl.setStyleSheet("color:#555555; font-size:9pt;")
        lbl.setAlignment(Qt.AlignCenter)
        self._content_layout.addWidget(lbl)
        self._content_layout.addStretch()
        self._open_script_btn.hide()
        self._compile_btn.hide()
        self._edit_in_rigger_btn.hide()
        self._patrol_editor = None

    def _rebuild(self):
        obj = self._obj
        if obj is None:
            self._show_empty()
            return

        self._building = True
        self._clear_content()

        if isinstance(obj, GITPlaceable):
            self._type_label.setText("Placeable")
            self._build_placeable(obj)
        elif isinstance(obj, GITCreature):
            self._type_label.setText("Creature")
            self._build_creature(obj)
        elif isinstance(obj, GITDoor):
            self._type_label.setText("Door")
            self._build_door(obj)
        elif isinstance(obj, GITWaypoint):
            self._type_label.setText("Waypoint")
            self._build_waypoint(obj)
        elif isinstance(obj, GITTrigger):
            self._type_label.setText("Trigger")
            self._build_trigger(obj)
        elif isinstance(obj, GITSoundObject):
            self._type_label.setText("Sound")
            self._build_sound(obj)
        elif isinstance(obj, GITStoreObject):
            self._type_label.setText("Store")
            self._build_store(obj)
        else:
            self._type_label.setText(type(obj).__name__)

        # P9: show "Edit in GhostRigger" for blueprint types
        has_bp = isinstance(obj, (GITCreature, GITPlaceable, GITDoor))
        self._edit_in_rigger_btn.setVisible(has_bp)

        self._content_layout.addStretch()
        self._building = False

    def _spin(self, value: float, minimum=-9999.0, maximum=9999.0,
              decimals=3, step=0.1) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(minimum, maximum)
        s.setDecimals(decimals)
        s.setSingleStep(step)
        s.setValue(value)
        s.setFont(QFont("Consolas", 9))
        return s

    def _line(self, value: str) -> QLineEdit:
        e = QLineEdit(str(value))
        e.setFont(QFont("Consolas", 9))
        return e

    def _resref(self, value: str) -> ResRefEdit:
        return ResRefEdit(str(value))

    def _script_combo(self, value: str) -> ScriptCombo:
        c = ScriptCombo()
        c.set_scripts(self._scripts)
        idx = c.findText(value)
        if idx >= 0:
            c.setCurrentIndex(idx)
        else:
            c.setEditText(value)
        return c

    def _connect_resref(self, widget: ResRefEdit, obj, attr: str):
        def on_changed():
            if self._building:
                return
            old = getattr(obj, attr, "")
            new = widget.text()[:16]
            if old != new:
                setattr(obj, attr, new)
                self.property_changed.emit(obj, attr, old, new)
        widget.editingFinished.connect(on_changed)

    def _connect_spin(self, widget: QDoubleSpinBox, obj, attr: str):
        """Connect a spin box to a plain float attribute via editingFinished.

        Uses editingFinished (fires only when the user presses Enter or moves
        focus away) rather than valueChanged (fires on every increment click /
        key stroke).  This avoids flooding the undo stack and the property_changed
        signal bus with dozens of near-identical events while the user types a
        number.
        """
        def on_finished():
            if self._building:
                return
            val = widget.value()
            old = getattr(obj, attr, 0.0)
            if abs(float(old) - val) > 1e-6:
                try:
                    from ..core.module_state import get_module_state, ModifyPropertyCommand
                    state = get_module_state()
                    cmd = ModifyPropertyCommand(obj, attr, old, val)
                    state.execute(cmd)
                except Exception:
                    setattr(obj, attr, val)
                self.property_changed.emit(obj, attr, old, val)
        widget.editingFinished.connect(on_finished)

    def _connect_script(self, widget: ScriptCombo, obj, attr: str):
        def on_changed():
            if self._building:
                return
            old = getattr(obj, attr, "")
            new = widget.selected_script()
            if old != new:
                setattr(obj, attr, new)
                self.property_changed.emit(obj, attr, old, new)
                self._open_script_btn.setVisible(bool(new))
                self._compile_btn.setVisible(bool(new))
        # Use only editTextChanged: covers both combo selection and manual typing.
        # Do NOT also connect currentTextChanged — that would fire on_changed twice
        # per selection change, doubling property_changed emissions and undo entries.
        widget.editTextChanged.connect(on_changed)

    def _connect_line(self, widget: QLineEdit, obj, attr: str):
        def on_changed():
            if self._building:
                return
            old = getattr(obj, attr, "")
            new = widget.text()
            if old != new:
                setattr(obj, attr, new)
                self.property_changed.emit(obj, attr, old, new)
        widget.editingFinished.connect(on_changed)

    # ── Object-specific builders ─────────────────────────────────────────────

    def _build_common(self, obj):
        """Build Identity section (Tag, ResRef, Template ResRef)."""
        grp = QGroupBox("Identity")
        grp.setStyleSheet("QGroupBox { color:#dcdcaa; font-weight:bold; }")
        form = QFormLayout(grp)
        form.setLabelAlignment(Qt.AlignRight)
        form.setContentsMargins(8, 8, 8, 8)
        form.setSpacing(4)

        tag_edit = self._line(obj.tag)
        form.addRow("Tag:", tag_edit)
        self._connect_line(tag_edit, obj, "tag")

        resref_edit = self._resref(obj.resref)
        form.addRow("ResRef:", resref_edit)
        self._connect_resref(resref_edit, obj, "resref")

        tpl_edit = self._resref(obj.template_resref)
        form.addRow("Template:", tpl_edit)
        self._connect_resref(tpl_edit, obj, "template_resref")

        self._content_layout.addWidget(grp)

    def _build_position(self, obj):
        """Build Position section (bearing row only shown when object has it)."""
        grp = QGroupBox("Position & Rotation")
        grp.setStyleSheet("QGroupBox { color:#dcdcaa; font-weight:bold; }")
        form = QFormLayout(grp)
        form.setLabelAlignment(Qt.AlignRight)
        form.setContentsMargins(8, 8, 8, 8)
        form.setSpacing(4)

        pos = obj.position
        px = self._spin(pos.x)
        py = self._spin(pos.y)
        pz = self._spin(pos.z)

        form.addRow("X:", px)
        form.addRow("Y:", py)
        form.addRow("Z:", pz)

        has_bearing = hasattr(obj, "bearing")
        if has_bearing:
            import math as _math
            # Show bearing in DEGREES (0-360) for human readability.
            # Radians stored internally; convert on input/output.
            deg_val = _math.degrees(obj.bearing) % 360.0
            br = self._spin(deg_val, minimum=0.0, maximum=360.0, decimals=1, step=5.0)
            br.setSuffix("°")
            form.addRow("Rotation (deg):", br)
            # Preset buttons for cardinal directions
            card_row = QHBoxLayout()
            for label, angle in (("N", 180.0), ("E", 270.0), ("S", 0.0), ("W", 90.0)):
                btn = QPushButton(label)
                btn.setFixedWidth(30)
                btn.setFixedHeight(22)
                btn.setStyleSheet("font-size:8pt; padding:0;")
                btn.clicked.connect(lambda _, a=angle: br.setValue(a))
                card_row.addWidget(btn)
            form.addRow("Cardinal:", _make_hwidget(card_row))
        else:
            br = None

        def on_pos(val):
            if self._building:
                return
            try:
                from ..core.module_state import get_module_state, MoveObjectCommand
                from ..formats.gff_types import Vector3
                old_pos = Vector3(obj.position.x, obj.position.y, obj.position.z)
                new_pos = Vector3(px.value(), py.value(), pz.value())
                # Only push a command if position actually changed
                if (abs(old_pos.x - new_pos.x) > 1e-6 or
                        abs(old_pos.y - new_pos.y) > 1e-6 or
                        abs(old_pos.z - new_pos.z) > 1e-6):
                    state = get_module_state()
                    cmd = MoveObjectCommand(obj, old_pos, new_pos)
                    state.execute(cmd)
                    self.property_changed.emit(obj, "position", old_pos, new_pos)
            except Exception:
                # Fallback: direct update without command
                obj.position.x = px.value()
                obj.position.y = py.value()
                obj.position.z = pz.value()
                self.property_changed.emit(obj, "position", None, obj.position)

        px.editingFinished.connect(lambda: on_pos(None))
        py.editingFinished.connect(lambda: on_pos(None))
        pz.editingFinished.connect(lambda: on_pos(None))

        if br is not None:
            def on_bearing(val):
                if self._building:
                    return
                import math as _math
                # Convert degrees → radians for storage
                rad_val = _math.radians(val)
                try:
                    from ..core.module_state import get_module_state, RotateObjectCommand
                    old = obj.bearing
                    if abs(old - rad_val) > 1e-6:
                        state = get_module_state()
                        cmd = RotateObjectCommand(obj, old, rad_val)
                        state.execute(cmd)
                        self.property_changed.emit(obj, "bearing", old, rad_val)
                except Exception:
                    old = obj.bearing
                    obj.bearing = rad_val
                    self.property_changed.emit(obj, "bearing", old, rad_val)
            br.editingFinished.connect(lambda: on_bearing(br.value()))

        self._content_layout.addWidget(grp)

    def _pencil_btn(self, attr: str, obj) -> QPushButton:
        """P7: Pencil button that opens a script field in GhostScripter."""
        btn = QPushButton("✏")
        btn.setFixedWidth(22)
        btn.setFixedHeight(22)
        btn.setToolTip("Open this script in GhostScripter")
        btn.setStyleSheet(
            "QPushButton { background:#1a3a1a; color:#4ec9b0; "
            "border:1px solid #2a6a2a; border-radius:2px; font-size:9pt; padding:0; }"
            "QPushButton:hover { background:#2a5a2a; }"
        )
        def on_clicked(checked=False, _attr=attr, _obj=obj):
            val = getattr(_obj, _attr, "")
            self.property_changed.emit(_obj, "_open_script", None, val or "")
        btn.clicked.connect(on_clicked)
        return btn

    def _build_scripts_section(self, obj, script_attrs: list):
        """Build Scripts section with P7 pencil icons per event."""
        grp = QGroupBox("Scripts")
        grp.setStyleSheet("QGroupBox { color:#dcdcaa; font-weight:bold; }")
        vbox = QVBoxLayout(grp)
        vbox.setContentsMargins(8, 8, 8, 8)
        vbox.setSpacing(3)

        for attr, label in script_attrs:
            val   = getattr(obj, attr, "")
            combo = self._script_combo(val)
            self._connect_script(combo, obj, attr)

            # P7: row = label + combo + pencil btn
            row_w = QWidget()
            row_l = QHBoxLayout(row_w)
            row_l.setContentsMargins(0, 0, 0, 0)
            row_l.setSpacing(2)
            lbl = QLabel(f"{label}:")
            lbl.setStyleSheet("color:#969696; font-size:8pt;")
            lbl.setFixedWidth(90)
            row_l.addWidget(lbl)
            row_l.addWidget(combo, 1)
            row_l.addWidget(self._pencil_btn(attr, obj))
            vbox.addWidget(row_w)

            if val:
                self._open_script_btn.show()
                self._compile_btn.show()

        self._content_layout.addWidget(grp)

    def _build_placeable(self, obj: GITPlaceable):
        self._build_common(obj)
        self._build_position(obj)
        script_events = [
            ("on_used",              "OnUsed"),
            ("on_heartbeat",         "OnHeartbeat"),
            ("on_closed",            "OnClosed"),
            ("on_damaged",           "OnDamaged"),
            ("on_death",             "OnDeath"),
            ("on_end_conversation",  "OnEndConversation"),
            ("on_inventory_disturbed","OnInvDisturbed"),
            ("on_lock",              "OnLock"),
            ("on_melee_attacked",    "OnMeleeAtk"),
            ("on_open",              "OnOpen"),
            ("on_user_defined",      "OnUserDef"),
        ]
        self._build_scripts_section(obj, script_events)

    def _build_creature(self, obj: GITCreature):
        self._build_common(obj)
        self._build_position(obj)
        script_events = [
            ("on_heartbeat",         "OnHeartbeat"),
            ("on_death",             "OnDeath"),
            ("on_end_conversation",  "OnEndConvers"),
            ("on_disturbed",         "OnDisturbed"),
            ("on_blocked",           "OnBlocked"),
            ("on_attacked",          "OnAttacked"),
            ("on_damaged",           "OnDamaged"),
            ("on_notice",            "OnNotice"),
            ("on_conversation",      "OnConversation"),
            ("on_user_defined",      "OnUserDef"),
            ("on_spawn",             "OnSpawn"),
        ]
        self._build_scripts_section(obj, script_events)
        self._build_patrol_section(obj)

    def _build_door(self, obj: GITDoor):
        self._build_common(obj)
        self._build_position(obj)

        # Link section
        grp = QGroupBox("Link")
        grp.setStyleSheet("QGroupBox { color:#dcdcaa; font-weight:bold; }")
        form = QFormLayout(grp)
        form.setLabelAlignment(Qt.AlignRight)
        form.setContentsMargins(8, 8, 8, 8)

        linked_to = self._line(obj.linked_to)
        form.addRow("LinkedTo:", linked_to)
        self._connect_line(linked_to, obj, "linked_to")

        trans_dest = self._resref(obj.transition_destination)
        form.addRow("Transition:", trans_dest)
        self._connect_resref(trans_dest, obj, "transition_destination")

        self._content_layout.addWidget(grp)

        script_events = [
            ("on_open",          "OnOpen"),
            ("on_open2",         "OnOpen2"),
            ("on_closed",        "OnClosed"),
            ("on_fail_to_open",  "OnFailToOpen"),
            ("on_damaged",       "OnDamaged"),
            ("on_death",         "OnDeath"),
            ("on_heartbeat",     "OnHeartbeat"),
            ("on_lock",          "OnLock"),
            ("on_unlock",        "OnUnlock"),
            ("on_melee_attacked","OnMeleeAtk"),
            ("on_user_defined",  "OnUserDef"),
        ]
        self._build_scripts_section(obj, script_events)

    def _build_waypoint(self, obj: GITWaypoint):
        self._build_common(obj)
        self._build_position(obj)

        grp = QGroupBox("Map Note")
        grp.setStyleSheet("QGroupBox { color:#dcdcaa; font-weight:bold; }")
        form = QFormLayout(grp)
        form.setLabelAlignment(Qt.AlignRight)
        form.setContentsMargins(8, 8, 8, 8)
        note = self._line(obj.map_note)
        form.addRow("Map Note:", note)
        self._connect_line(note, obj, "map_note")
        self._content_layout.addWidget(grp)

    def _build_trigger(self, obj: GITTrigger):
        self._build_common(obj)
        self._build_position(obj)
        # Geometry vertex count
        grp = QGroupBox("Geometry")
        grp.setStyleSheet("QGroupBox { color:#dcdcaa; font-weight:bold; }")
        form = QFormLayout(grp)
        form.setLabelAlignment(Qt.AlignRight)
        form.setContentsMargins(8, 8, 8, 8)
        n_verts = QLabel(str(len(getattr(obj, "geometry", []))))
        n_verts.setStyleSheet("color:#9cdcfe; font-family:Consolas;")
        form.addRow("Vertices:", n_verts)
        self._content_layout.addWidget(grp)
        script_events = [
            ("on_enter",        "OnEnter"),
            ("on_exit",         "OnExit"),
            ("on_heartbeat",    "OnHeartbeat"),
            ("on_user_defined", "OnUserDef"),
        ]
        self._build_scripts_section(obj, script_events)

    def _build_sound(self, obj: GITSoundObject):
        self._build_common(obj)
        self._build_position(obj)
        # No scripts for ambient sounds in KotOR GIT
        pass

    def _build_patrol_section(self, obj: GITCreature):
        """P4: Embed the patrol waypoint linker in creature inspector."""
        PatrolPathEditor = _get_patrol_editor_class()
        if PatrolPathEditor is None:
            return
        grp = QGroupBox("Patrol Path")
        grp.setStyleSheet("QGroupBox { color:#dcdcaa; font-weight:bold; }")
        layout = QVBoxLayout(grp)
        layout.setContentsMargins(6, 8, 6, 6)
        editor = PatrolPathEditor(obj, state=self._state, parent=grp)
        editor.path_changed.connect(self.patrol_path_changed)
        editor.request_click_placement.connect(self.request_patrol_click)
        self._patrol_editor = editor
        layout.addWidget(editor)
        self._content_layout.addWidget(grp)

    def _build_store(self, obj: GITStoreObject):
        self._build_common(obj)
        self._build_position(obj)

    # ── GhostScripter integration ────────────────────────────────────────────

    def _get_first_script(self) -> str:
        """Get the first non-empty script from the current object."""
        obj = self._obj
        if obj is None:
            return ""
        for attr in ("on_used", "on_heartbeat", "on_open", "on_enter",
                     "on_death", "on_conversation", "on_spawn"):
            val = getattr(obj, attr, "")
            if val:
                return val
        return ""

    def _open_in_ghostscripter(self):
        script = self._get_first_script()
        if not script:
            return
        try:
            from ..ipc.bridges import GhostScripterBridge
            # The bridge is managed by MainWindow; we emit a signal or call directly
            # For now just log — MainWindow wires up the actual call
            log.info(f"Request: open {script} in GhostScripter")
            # Emit a generic signal that MainWindow connects to
            self.property_changed.emit(self._obj, "_open_script", None, script)
        except Exception as e:
            log.debug(f"Open script error: {e}")

    def _compile_script(self):
        script = self._get_first_script()
        if not script:
            return
        log.info(f"Request: compile {script}")
        self.property_changed.emit(self._obj, "_compile_script", None, script)

    # ── GhostRigger integration (P9) ─────────────────────────────────────────

    def _open_in_ghostrigger(self):
        """P9: Open the selected object's blueprint in GhostRigger."""
        obj = self._obj
        if obj is None:
            return
        resref = getattr(obj, "resref", "").strip()
        if not resref:
            return
        if isinstance(obj, GITCreature):
            ext = "utc"
        elif isinstance(obj, GITPlaceable):
            ext = "utp"
        elif isinstance(obj, GITDoor):
            ext = "utd"
        else:
            return
        log.info(f"P9: open {resref}.{ext} in GhostRigger")
        self.open_in_rigger.emit(resref, ext, self._module_dir)
        self.property_changed.emit(obj, "_open_in_rigger", None, (resref, ext))
