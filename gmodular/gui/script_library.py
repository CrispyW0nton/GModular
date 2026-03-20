"""
GModular — Script Library Panel
Built-in NWScript template library for KotOR/TSL modders.

Provides one-click script templates for the most common scripting tasks:
  - Door / placeable event handlers
  - NPC patrol waypoints
  - Conversation triggers
  - Store/merchant scripts
  - Conditional checks
  - Spawn-on-enter patterns

Templates are editable and can be copied directly into .nss files.
"""
from __future__ import annotations
import logging

from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QPlainTextEdit, QTreeWidget, QTreeWidgetItem, QSplitter,
    QSizePolicy, QComboBox, QLineEdit, QGroupBox,
)
from qtpy.QtCore import Qt, Signal
from qtpy.QtGui import QFont, QColor

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  NWScript Template Library
#  Each entry: (category, name, description, code)
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_TEMPLATES = [
    # ── Doors ────────────────────────────────────────────────────────────────
    ("Doors", "Open door by tag", "Open a door remotely from any script or placeable event.",
     """\
// open_door_by_tag.nss
// Call this from a placeable OnUsed, trigger OnEnter, etc.
// Replace "my_door_tag" with your door's Tag value.
void main()
{
    object oDoor = GetObjectByTag("my_door_tag");
    ActionOpenDoor(oDoor);
}
"""),
    ("Doors", "Lock / Unlock door", "Lock or unlock a door by tag.",
     """\
// lock_door.nss
void main()
{
    object oDoor = GetObjectByTag("my_door_tag");
    SetLocked(oDoor, TRUE);   // TRUE = lock, FALSE = unlock
}
"""),
    ("Doors", "Door auto-open on enter trigger", "Auto-open a door when player enters a trigger area.",
     """\
// trigger_open_door.nss  (assign to trigger OnEnter)
void main()
{
    object oEntering = GetEnteringObject();
    if (!GetIsPartyLeader(oEntering)) return;
    object oDoor = GetObjectByTag("my_door_tag");
    ActionOpenDoor(oDoor);
}
"""),

    # ── NPCs / Creatures ─────────────────────────────────────────────────────
    ("NPCs", "Walk waypoints on spawn", "Make an NPC walk along WP_<Tag>_01, _02, etc. at spawn.",
     """\
// walk_waypoints_spawn.nss  (assign to creature OnSpawn)
#include "k_inc_generic"

void main()
{
    GN_SetDayNightPresence(AMBIENT_PRESENCE_ALWAYS_PRESENT);
    GN_SetListeningPatterns();
    GN_WalkWayPoints();   // NPC walks WP_<Tag>_01, WP_<Tag>_02 ...
}
"""),
    ("NPCs", "Random walk heartbeat", "NPC wanders randomly — assign to OnHeartbeat.",
     """\
// random_walk.nss  (assign to creature OnHeartbeat)
void main()
{
    AssignCommand(OBJECT_SELF, ActionRandomWalk());
}
"""),
    ("NPCs", "Spawn NPC on area enter", "Spawn creatures from .utc files when player enters area.",
     """\
// spawn_on_enter.nss  (assign to area OnEnter in IFO / GIT)
void main()
{
    object oEntering = GetEnteringObject();
    if (!GetIsPartyLeader(oEntering)) return;

    // Only spawn once
    if (GetIsObjectValid(GetObjectByTag("my_npc_01", 0))) return;

    CreateObject(OBJECT_TYPE_CREATURE, "my_npc_01",
                 GetLocation(GetObjectByTag("wp_spawn_01", 0)), FALSE);
    CreateObject(OBJECT_TYPE_CREATURE, "my_npc_02",
                 GetLocation(GetObjectByTag("wp_spawn_02", 0)), FALSE);
}
"""),
    ("NPCs", "Make NPCs hostile on event", "Switch spawned NPCs to hostile faction and attack player.",
     """\
// make_hostile.nss
void main()
{
    object oNPC = GetObjectByTag("my_npc_01", 0);
    ChangeToStandardFaction(oNPC, STANDARD_FACTION_HOSTILE_1);
    AssignCommand(oNPC, ActionAttack(GetFirstPC(), FALSE));
}
"""),
    ("NPCs", "Place dead NPC on spawn", "Spawn NPC already dead (for environmental storytelling).",
     """\
// spawn_dead.nss  (assign to creature OnSpawn)
void main()
{
    effect efDeath = EffectDeath(FALSE, FALSE, TRUE);
    ApplyEffectToObject(DURATION_TYPE_INSTANT, efDeath, OBJECT_SELF, 0.0f);
}
"""),

    # ── Placeables ───────────────────────────────────────────────────────────
    ("Placeables", "Computer panel opens door", "Invisible panel placeable that opens a door on use.",
     """\
// panel_open_door.nss  (assign to placeable OnUsed)
void main()
{
    object oDoor = GetObjectByTag("my_door_tag");
    ActionOpenDoor(oDoor);
}
"""),
    ("Placeables", "Placeable triggers conversation", "Placeable OnUsed that starts a dialog.",
     """\
// placeable_conversation.nss  (assign to placeable OnUsed)
void main()
{
    object oPC = GetFirstPC();
    AssignCommand(OBJECT_SELF,
        ActionStartConversation(oPC, "my_dialog_file", FALSE, FALSE));
}
"""),
    ("Placeables", "Placeable use sets global var", "Set a global number when placeable is used.",
     """\
// set_global_on_use.nss  (assign to placeable OnUsed)
void main()
{
    int nCurrent = GetGlobalNumber("MY_GLOBAL_VAR");
    SetGlobalNumber("MY_GLOBAL_VAR", nCurrent + 1);
}
"""),

    # ── Triggers ─────────────────────────────────────────────────────────────
    ("Triggers", "Trigger starts conversation", "Walk into trigger to start NPC dialog.",
     """\
// trigger_conversation.nss  (assign to trigger OnEnter)
void main()
{
    object oEntering = GetEnteringObject();
    if (!GetIsPartyLeader(oEntering)) return;
    // Only fire once
    if (GetGlobalNumber("TRG_CONV_FIRED") == 1) return;
    SetGlobalNumber("TRG_CONV_FIRED", 1);

    object oNPC = GetObjectByTag("my_npc_01", 0);
    AssignCommand(oNPC,
        ActionStartConversation(oEntering, "my_dialog_file",
                                FALSE, FALSE, FALSE,
                                "", "", "", "", "", "", FALSE,
                                0xFFFFFFFF, 0xFFFFFFFF, FALSE));
    DestroyObject(OBJECT_SELF, 0.0f, FALSE, 0.0f);  // remove trigger after use
}
"""),
    ("Triggers", "One-shot trigger (fires once)", "Trigger that destroys itself after first player entry.",
     """\
// one_shot_trigger.nss  (assign to trigger OnEnter)
void main()
{
    object oEntering = GetEnteringObject();
    if (!GetIsPartyLeader(oEntering)) return;

    // --- YOUR CODE HERE ---

    DestroyObject(OBJECT_SELF, 0.1f, FALSE, 0.0f);
}
"""),

    # ── Merchants / Stores ───────────────────────────────────────────────────
    ("Merchants", "Open store from dialog", "Open a merchant inventory from a dialog node's script.",
     """\
// open_store.nss  (assign to dialog action node)
void main()
{
    object oStore = GetObjectByTag("my_store_tag");
    if (!GetIsObjectValid(oStore))
        oStore = CreateObject(OBJECT_TYPE_STORE, "my_store_resref",
                              GetLocation(OBJECT_SELF));
    if (GetIsObjectValid(oStore))
        DelayCommand(0.5f, OpenStore(oStore, GetPCSpeaker()));
}
"""),
    ("Merchants", "Dynamic store (changes by var)", "Open different stores based on a global variable.",
     """\
// dynamic_store.nss
void store(string sName)
{
    object oStore = GetObjectByTag(sName);
    if (!GetIsObjectValid(oStore))
        oStore = CreateObject(OBJECT_TYPE_STORE, sName, GetLocation(OBJECT_SELF));
    if (GetIsObjectValid(oStore))
        DelayCommand(0.5f, OpenStore(oStore, GetPCSpeaker()));
}
void main()
{
    int nStage = GetGlobalNumber("QUEST_STAGE");
    store("my_store_" + IntToString(nStage));
}
"""),

    # ── Conditionals ─────────────────────────────────────────────────────────
    ("Conditionals", "Check current module", "Dialog conditional — returns TRUE if in named module.",
     """\
// c_check_module.nss  (starting conditional)
int StartingConditional()
{
    string sParam = GetScriptStringParameter();
    return (GetModuleName() == sParam);
}
"""),
    ("Conditionals", "Check global variable", "Conditional: TRUE if global number equals value.",
     """\
// c_check_global.nss  (starting conditional)
int StartingConditional()
{
    int nExpected = GetScriptParameter(1);
    return (GetGlobalNumber("MY_GLOBAL_VAR") == nExpected);
}
"""),
    ("Conditionals", "Check player has feat", "Conditional: TRUE if player has a specific feat.",
     """\
// c_has_feat.nss  (starting conditional)
int StartingConditional()
{
    int nFeat = GetScriptParameter(1);  // feat row in feat.2da
    return GetHasFeat(nFeat, GetFirstPC());
}
"""),
    ("Conditionals", "Check item in inventory", "Conditional: TRUE if player has an item with given tag.",
     """\
// c_has_item.nss  (starting conditional)
int StartingConditional()
{
    string sTag = GetScriptStringParameter();
    return GetIsObjectValid(GetItemPossessedBy(GetFirstPC(), sTag));
}
"""),

    # ── Utility ──────────────────────────────────────────────────────────────
    ("Utility", "Fade to black + area transition", "Fade out, transition to another module.",
     """\
// area_transition.nss
void main()
{
    FadeToBlack(GetFirstPC(), FADE_SPEED_MEDIUM);
    // After fade, change to new module "my_module" at waypoint "wp_start"
    DelayCommand(1.5f, JumpToArea("my_module", "wp_start"));
}
"""),
    ("Utility", "Give item to player", "Give the player an item by ResRef.",
     """\
// give_item.nss
void main()
{
    CreateItemOnObject("my_item_resref", GetFirstPC(), 1);
}
"""),
    ("Utility", "Heal / restore party", "Fully heal all party members.",
     """\
// heal_party.nss
void main()
{
    int i;
    for (i = 0; i < GetPartyMemberCount(); i++) {
        object oMember = GetPartyMemberByIndex(i);
        if (GetIsObjectValid(oMember))
            ApplyEffectToObject(DURATION_TYPE_INSTANT,
                                EffectHeal(GetMaxHitPoints(oMember)),
                                oMember, 0.0f);
    }
}
"""),
    ("Utility", "Play sound / ambient", "Play a sound object or ambient sound.",
     """\
// play_sound.nss
void main()
{
    // Play a sound object in the area
    object oSound = GetObjectByTag("my_sound_tag");
    SoundObjectPlay(oSound);
    // Or play a one-shot sound:
    // PlaySound("my_sound_resref");
}
"""),
]


# ─────────────────────────────────────────────────────────────────────────────
#  Script Library Panel
# ─────────────────────────────────────────────────────────────────────────────

class ScriptLibraryPanel(QWidget):
    """
    Built-in NWScript template library panel.
    Left: categorised tree of templates.
    Right: editable code preview with Copy button.
    """

    script_copied = Signal(str)   # emits script name when copied

    def __init__(self, parent=None):
        super().__init__(parent)
        self._templates = SCRIPT_TEMPLATES
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QLabel("NWScript Template Library")
        hdr.setStyleSheet("color:#4ec9b0; font-weight:bold; font-size:9pt;")
        layout.addWidget(hdr)

        sub = QLabel("Click a template → preview code → Copy to clipboard")
        sub.setStyleSheet("color:#808080; font-size:8pt;")
        layout.addWidget(sub)

        # ── Search ────────────────────────────────────────────────────────────
        search_row = QHBoxLayout()
        search_lbl = QLabel("Filter:")
        search_lbl.setStyleSheet("color:#969696; font-size:8pt;")
        search_lbl.setFixedWidth(36)
        self._search = QLineEdit()
        self._search.setPlaceholderText("type to filter...")
        self._search.setStyleSheet("background:#1e1e1e; color:#dcdcaa; "
                                   "border:1px solid #555; padding:2px;")
        self._search.textChanged.connect(self._on_search)
        search_row.addWidget(search_lbl)
        search_row.addWidget(self._search)
        layout.addLayout(search_row)

        # ── Splitter ─────────────────────────────────────────────────────────
        splitter = QSplitter(Qt.Vertical)
        layout.addWidget(splitter, 1)

        # Tree
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setStyleSheet(
            "QTreeWidget { background:#1e1e1e; color:#dcdcdc; "
            "  border:1px solid #3c3c3c; font-size:8pt; }"
            "QTreeWidget::item:selected { background:#264f78; }"
            "QTreeWidget::item:hover { background:#2a2d2e; }"
        )
        self._tree.currentItemChanged.connect(self._on_select)
        splitter.addWidget(self._tree)

        # Code preview
        code_group = QGroupBox("Preview (editable)")
        code_group.setStyleSheet("QGroupBox { color:#ce9178; font-weight:bold; }")
        code_layout = QVBoxLayout(code_group)
        code_layout.setContentsMargins(4, 8, 4, 4)
        code_layout.setSpacing(4)

        self._desc_label = QLabel("")
        self._desc_label.setStyleSheet("color:#969696; font-size:8pt;")
        self._desc_label.setWordWrap(True)
        code_layout.addWidget(self._desc_label)

        self._code_edit = QPlainTextEdit()
        self._code_edit.setFont(QFont("Consolas", 9))
        self._code_edit.setStyleSheet(
            "background:#1e1e1e; color:#d4d4d4; "
            "border:1px solid #3c3c3c; selection-background-color:#264f78;"
        )
        self._code_edit.setPlaceholderText("Select a template from the list above...")
        code_layout.addWidget(self._code_edit)

        btn_row = QHBoxLayout()
        self._copy_btn = QPushButton("Copy to Clipboard")
        self._copy_btn.setStyleSheet(
            "background:#0e639c; color:white; font-weight:bold; "
            "padding:4px 12px; border:none;"
        )
        self._copy_btn.clicked.connect(self._copy)
        self._name_lbl = QLabel("")
        self._name_lbl.setStyleSheet("color:#569cd6; font-size:8pt;")
        btn_row.addWidget(self._copy_btn)
        btn_row.addWidget(self._name_lbl)
        btn_row.addStretch()
        code_layout.addLayout(btn_row)

        splitter.addWidget(code_group)
        splitter.setSizes([200, 300])

        self._populate_tree(self._templates)

    def _populate_tree(self, templates):
        self._tree.clear()
        cats = {}
        for cat, name, desc, code in templates:
            if cat not in cats:
                item = QTreeWidgetItem([cat])
                item.setForeground(0, QColor("#4ec9b0"))
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)
                item.setData(0, Qt.UserRole, None)
                self._tree.addTopLevelItem(item)
                cats[cat] = item
            child = QTreeWidgetItem([name])
            child.setForeground(0, QColor("#dcdcdc"))
            child.setData(0, Qt.UserRole, (name, desc, code))
            cats[cat].addChild(child)
        self._tree.expandAll()

    def _on_search(self, text: str):
        text = text.lower()
        if not text:
            filtered = self._templates
        else:
            filtered = [
                t for t in self._templates
                if text in t[1].lower() or text in t[0].lower() or text in t[2].lower()
            ]
        self._populate_tree(filtered)

    def _on_select(self, current, _prev):
        if current is None:
            return
        data = current.data(0, Qt.UserRole)
        if data is None:
            return  # category node
        name, desc, code = data
        self._desc_label.setText(desc)
        self._code_edit.setPlainText(code.strip())
        self._name_lbl.setText(name)

    def _copy(self):
        code = self._code_edit.toPlainText()
        if not code:
            return
        from qtpy.QtWidgets import QApplication
        QApplication.clipboard().setText(code)
        name = self._name_lbl.text()
        self._copy_btn.setText("Copied!")
        self._copy_btn.setStyleSheet(
            "background:#16825d; color:white; font-weight:bold; "
            "padding:4px 12px; border:none;"
        )
        from qtpy.QtCore import QTimer
        QTimer.singleShot(1500, self._reset_copy_btn)
        self.script_copied.emit(name)
        log.info(f"Script template copied: {name}")

    def _reset_copy_btn(self):
        self._copy_btn.setText("Copy to Clipboard")
        self._copy_btn.setStyleSheet(
            "background:#0e639c; color:white; font-weight:bold; "
            "padding:4px 12px; border:none;"
        )
