"""
GhostScripter — NWScript Syntax Highlighter + Function Browser
===============================================================
Provides:
  NWScriptHighlighter  — full-token QSyntaxHighlighter for NWScript (.nss)
  FunctionBrowserPanel — searchable sidebar listing all NWScript stdlib functions
    organised by category (Action, Effect, Object, Math, String, …)
  NWScriptTokenizer    — pure-Python tokenizer for headless analysis (no Qt dep)

This module is designed to work both with Qt (full highlighting) and headlessly
(NWScriptTokenizer, NWSCRIPT_STDLIB constant, category table).
"""
from __future__ import annotations

import re
import logging
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

try:
    from qtpy.QtWidgets import (
        QWidget, QVBoxLayout, QLabel, QLineEdit,
        QTreeWidget, QTreeWidgetItem, QTextEdit,
        QSplitter,
    )
    from qtpy.QtCore import Qt, Signal
    from qtpy.QtGui import (
        QColor, QTextCharFormat, QSyntaxHighlighter, QTextDocument,
        QFont,
    )
    _HAS_QT = True
except ImportError:
    _HAS_QT = False
    QWidget = object                # type: ignore
    QSyntaxHighlighter = object     # type: ignore
    Signal = lambda *a: None        # type: ignore


# ──────────────────────────────────────────────────────────────────────────────
# Token color palette (VS Code dark+ inspired)
# ──────────────────────────────────────────────────────────────────────────────
_COL_KEYWORD    = "#569cd6"   # blue — language keywords
_COL_TYPE       = "#4ec9b0"   # teal — built-in types
_COL_STDLIB     = "#dcdcaa"   # yellow — stdlib functions
_COL_CONSTANT   = "#9cdcfe"   # light-blue — defined constants
_COL_NUMBER     = "#b5cea8"   # green — numeric literals
_COL_STRING     = "#ce9178"   # orange — string literals
_COL_COMMENT    = "#6a9955"   # green — comments
_COL_PREPROC    = "#c586c0"   # purple — #include / #define
_COL_OPERATOR   = "#d4d4d4"   # white — operators
_COL_ENTRYPOINT = "#e6c07b"   # gold — void main / int StartingConditional


# ──────────────────────────────────────────────────────────────────────────────
# NWScript language tables
# ──────────────────────────────────────────────────────────────────────────────

NWS_KEYWORDS = frozenset([
    "if", "else", "while", "for", "do", "return", "break", "continue",
    "switch", "case", "default", "const", "struct", "action",
])

NWS_TYPES = frozenset([
    "void", "int", "float", "string", "object", "vector",
    "effect", "event", "talent", "location", "itemproperty",
])

NWS_ENTRY_POINTS = frozenset([
    "main", "StartingConditional",
])

# Constants defined in nwscript.nss (most common subset)
NWS_CONSTANTS = frozenset([
    # Object constants
    "OBJECT_SELF", "OBJECT_INVALID",
    # TRUE / FALSE
    "TRUE", "FALSE",
    # Class / race constants
    "CLASS_TYPE_JEDI_GUARDIAN", "CLASS_TYPE_JEDI_CONSULAR",
    "CLASS_TYPE_JEDI_SENTINEL", "CLASS_TYPE_SCOUNDREL",
    "CLASS_TYPE_SCOUT", "CLASS_TYPE_SOLDIER",
    "RACIAL_TYPE_HUMAN", "RACIAL_TYPE_RODIAN", "RACIAL_TYPE_WOOKIEE",
    "RACIAL_TYPE_TWILEK", "RACIAL_TYPE_ZABRAK",
    # Inventory slots
    "INVENTORY_SLOT_HEAD", "INVENTORY_SLOT_CHEST",
    "INVENTORY_SLOT_ARMS", "INVENTORY_SLOT_RIGHTHAND",
    "INVENTORY_SLOT_LEFTHAND",
    # Alignment
    "ALIGNMENT_GOOD", "ALIGNMENT_EVIL",
    "ALIGNMENT_LAWFUL", "ALIGNMENT_CHAOTIC", "ALIGNMENT_NEUTRAL",
    # Damage / attack
    "DAMAGE_TYPE_PHYSICAL", "DAMAGE_TYPE_FIRE", "DAMAGE_TYPE_COLD",
    "ATTACK_BONUS_ONHAND", "ATTACK_BONUS_OFFHAND",
    # Effect types
    "EFFECT_TYPE_INVALID", "EFFECT_TYPE_DAMAGE",
    "EFFECT_TYPE_HEAL", "EFFECT_TYPE_DEATH",
    # Ability scores
    "ABILITY_STRENGTH", "ABILITY_DEXTERITY", "ABILITY_CONSTITUTION",
    "ABILITY_INTELLIGENCE", "ABILITY_WISDOM", "ABILITY_CHARISMA",
    # Creature size
    "CREATURE_SIZE_SMALL", "CREATURE_SIZE_MEDIUM", "CREATURE_SIZE_LARGE",
    # Standard tags
    "SWVAW_PC", "WALKWAY_PC",
    # Faction constants
    "STANDARD_FACTION_FRIENDLY1", "STANDARD_FACTION_HOSTILE1",
    "STANDARD_FACTION_COMMONER", "STANDARD_FACTION_MERCHANT",
    # Animation
    "ANIMATION_LOOPING_PAUSE", "ANIMATION_LOOPING_PAUSE2",
    "ANIMATION_LOOPING_LISTEN", "ANIMATION_LOOPING_MEDITATE",
    "ANIMATION_FIREFORGET_HEAD_TURN_LEFT",
    "ANIMATION_FIREFORGET_HEAD_TURN_RIGHT",
    # Status effects
    "EFFECT_STUNNED", "EFFECT_PARALYZED",
    # Item property
    "ITEM_PROPERTY_DAMAGE_BONUS",
    "ITEM_PROPERTY_EXTRA_MELEE_DAMAGE_TYPE",
    # Misc
    "INVALID_OBJECT_ID",
])

# Full stdlib function table organised by category
# Format: (function_name, signature_hint, category)
NWSCRIPT_STDLIB: List[Tuple[str, str, str]] = [
    # ── Action / AI ──────────────────────────────────────────────────────────
    ("ActionAttack",              "ActionAttack(object oAttackee, int bPassive=FALSE)",  "Action/AI"),
    ("ActionCastSpellAtObject",   "ActionCastSpellAtObject(int nSpell, object oTarget, ...)", "Action/AI"),
    ("ActionDoCommand",           "ActionDoCommand(action aAction)",                    "Action/AI"),
    ("ActionEquipItem",           "ActionEquipItem(object oItem, int nInventorySlot)",  "Action/AI"),
    ("ActionFollowLeader",        "ActionFollowLeader()",                               "Action/AI"),
    ("ActionForceFollowObject",   "ActionForceFollowObject(object oFollow, float fFollowDistance=0.0)", "Action/AI"),
    ("ActionInteractObject",      "ActionInteractObject(object oPlaceable)",            "Action/AI"),
    ("ActionJumpToObject",        "ActionJumpToObject(object oToJumpTo, int bWalkStraightLineToPoint=TRUE)", "Action/AI"),
    ("ActionJumpToLocation",      "ActionJumpToLocation(location lLocation)",           "Action/AI"),
    ("ActionMoveToObject",        "ActionMoveToObject(object oMoveTo, int bRun=FALSE, float fRange=1.0)", "Action/AI"),
    ("ActionMoveToLocation",      "ActionMoveToLocation(location lDestination, int bRun=FALSE)", "Action/AI"),
    ("ActionMoveAwayFromLocation","ActionMoveAwayFromLocation(location lFleeFrom, int bRun=FALSE, float fMoveAwayRange=40.0)", "Action/AI"),
    ("ActionOpenDoor",            "ActionOpenDoor(object oDoor)",                       "Action/AI"),
    ("ActionCloseDoor",           "ActionCloseDoor(object oDoor)",                      "Action/AI"),
    ("ActionPause",               "ActionPause(float fSeconds)",                        "Action/AI"),
    ("ActionPlayAnimation",       "ActionPlayAnimation(int nAnimation, float fSpeed=1.0, float fSeconds=0.0)", "Action/AI"),
    ("ActionRandomWalk",          "ActionRandomWalk()",                                 "Action/AI"),
    ("ActionSpeakString",         "ActionSpeakString(string sStringToSpeak, int nTalkVolume=TALKVOLUME_TALK)", "Action/AI"),
    ("ActionSpeakStringByStrRef", "ActionSpeakStringByStrRef(int nStrRef, int nTalkVolume=TALKVOLUME_TALK)", "Action/AI"),
    ("ActionStartConversation",   "ActionStartConversation(object oObjectToConverseWith, string sDialogResRef=\"\", int bPrivateConversation=FALSE, ...)", "Action/AI"),
    ("ActionUnequipItem",         "ActionUnequipItem(object oItem)",                    "Action/AI"),
    ("ActionUseSkill",            "ActionUseSkill(int nSkill, object oTarget, ...)",    "Action/AI"),
    ("AssignCommand",             "AssignCommand(object oActionSubject, action aActionToAssign)", "Action/AI"),
    ("ClearAllActions",           "ClearAllActions(int nClearCombatState=FALSE)",        "Action/AI"),
    ("DelayCommand",              "DelayCommand(float fSeconds, action aActionToDelay)", "Action/AI"),
    # ── Object / Creature ────────────────────────────────────────────────────
    ("CreateObject",              "CreateObject(int nObjectType, string sTemplate, location lLocation, int bUseAppearAnimation=FALSE, string sNewTag=\"\")", "Object"),
    ("DestroyObject",             "DestroyObject(object oDestroy, float fDelay=0.0)",   "Object"),
    ("GetArea",                   "GetArea(object oTarget)",                            "Object"),
    ("GetEnteringObject",         "GetEnteringObject()",                                "Object"),
    ("GetExitingObject",          "GetExitingObject()",                                 "Object"),
    ("GetFactionEqual",           "GetFactionEqual(object oFirstObject, object oSecondObject=OBJECT_SELF)", "Object"),
    ("GetFactionMostDamagedMember","GetFactionMostDamagedMember(object oMember)",       "Object"),
    ("GetFirstFactionMember",     "GetFirstFactionMember(object oMemberOfFaction, int bPCSOnly=TRUE)", "Object"),
    ("GetFirstPC",                "GetFirstPC()",                                       "Object"),
    ("GetIsCreature",             "GetIsCreature(object oCreature)",                    "Object"),
    ("GetIsDead",                 "GetIsDead(object oCreature)",                        "Object"),
    ("GetIsEnemy",                "GetIsEnemy(object oTarget, object oSource=OBJECT_SELF)", "Object"),
    ("GetIsFriend",               "GetIsFriend(object oTarget, object oSource=OBJECT_SELF)", "Object"),
    ("GetIsNeutral",              "GetIsNeutral(object oTarget, object oSource=OBJECT_SELF)", "Object"),
    ("GetIsPC",                   "GetIsPC(object oCreature)",                          "Object"),
    ("GetLastAttacker",           "GetLastAttacker(object oAttackee=OBJECT_SELF)",      "Object"),
    ("GetLastDamager",            "GetLastDamager(object oDamaged=OBJECT_SELF)",        "Object"),
    ("GetLastHostileActor",       "GetLastHostileActor(object oReactor=OBJECT_SELF)",   "Object"),
    ("GetLastOpenedBy",           "GetLastOpenedBy()",                                  "Object"),
    ("GetLastUsedBy",             "GetLastUsedBy()",                                    "Object"),
    ("GetNearestCreature",        "GetNearestCreature(int nFirstCriteriaType, int nFirstCriteriaValue, object oTarget=OBJECT_SELF, ...)", "Object"),
    ("GetNearestObject",          "GetNearestObject(int nObjectType=OBJECT_TYPE_ALL, object oTarget=OBJECT_SELF, int nNth=1)", "Object"),
    ("GetNearestObjectByTag",     "GetNearestObjectByTag(string sTag, object oTarget=OBJECT_SELF, int nNth=1)", "Object"),
    ("GetObjectByTag",            "GetObjectByTag(string sTag, int nNth=0)",            "Object"),
    ("GetResRef",                 "GetResRef(object oObject)",                          "Object"),
    ("GetTag",                    "GetTag(object oObject)",                             "Object"),
    ("GetObjectType",             "GetObjectType(object oTarget)",                      "Object"),
    # ── Effect ───────────────────────────────────────────────────────────────
    ("ApplyEffectToObject",       "ApplyEffectToObject(int nDurationType, effect eEffect, object oTarget, float fDuration=0.0)", "Effect"),
    ("ApplyEffectAtLocation",     "ApplyEffectAtLocation(int nDurationType, effect eEffect, location lLocation, float fDuration=0.0)", "Effect"),
    ("EffectDamage",              "EffectDamage(int nDamageAmount, int nDamageType=DAMAGE_TYPE_PHYSICAL, int nDamagePower=DAMAGE_POWER_NORMAL)", "Effect"),
    ("EffectDeath",               "EffectDeath(int nSpectacularDeath=FALSE, int nDisplayFeedback=TRUE)", "Effect"),
    ("EffectHeal",                "EffectHeal(int nDamageToHeal)",                      "Effect"),
    ("EffectKnockdown",           "EffectKnockdown()",                                  "Effect"),
    ("EffectParalyze",            "EffectParalyze()",                                   "Effect"),
    ("EffectStunned",             "EffectStunned()",                                    "Effect"),
    ("EffectVisualEffect",        "EffectVisualEffect(int nVisualEffectId, int bMissEffect=FALSE)", "Effect"),
    ("RemoveEffect",              "RemoveEffect(object oCreature, effect eEffect)",      "Effect"),
    # ── Combat ───────────────────────────────────────────────────────────────
    ("GetCurrentHitPoints",       "GetCurrentHitPoints(object oObject=OBJECT_SELF)",    "Combat"),
    ("GetMaxHitPoints",           "GetMaxHitPoints(object oObject=OBJECT_SELF)",        "Combat"),
    ("GetHitDice",                "GetHitDice(object oCreature)",                       "Combat"),
    ("SetMaxHitPoints",           "SetMaxHitPoints(object oObject, int nMaxHP)",        "Combat"),
    ("GetAbilityScore",           "GetAbilityScore(object oCreature, int nAbilityType)", "Combat"),
    ("GetSkillRank",              "GetSkillRank(int nSkill, object oTarget=OBJECT_SELF)", "Combat"),
    ("GetClassByPosition",        "GetClassByPosition(int nClassPosition, object oCreature=OBJECT_SELF)", "Combat"),
    ("GetLevelByPosition",        "GetLevelByPosition(int nClassPosition, object oCreature=OBJECT_SELF)", "Combat"),
    ("GetXP",                     "GetXP(object oCreature)",                            "Combat"),
    ("SetXP",                     "SetXP(object oCreature, int nXPAmount)",             "Combat"),
    ("GiveXPToCreature",          "GiveXPToCreature(object oCreature, int nXP)",        "Combat"),
    # ── Inventory ────────────────────────────────────────────────────────────
    ("AddItemProperty",           "AddItemProperty(int nDurationType, itemproperty ipProperty, object oItem, float fDuration=0.0)", "Inventory"),
    ("CreateItemOnObject",        "CreateItemOnObject(string sItemTemplate, object oTarget=OBJECT_SELF, int nStackSize=1)", "Inventory"),
    ("DestroyItem",               "DestroyItem(object oDestroyItem)",                   "Inventory"),
    ("GetFirstItemInInventory",   "GetFirstItemInInventory(object oTarget=OBJECT_SELF)", "Inventory"),
    ("GetNextItemInInventory",    "GetNextItemInInventory(object oTarget=OBJECT_SELF)",  "Inventory"),
    ("GetItemInSlot",             "GetItemInSlot(int nInventorySlot, object oTarget=OBJECT_SELF)", "Inventory"),
    ("GetItemPossessedBy",        "GetItemPossessedBy(object oCreature, string sItemTag)", "Inventory"),
    ("GetItemStackSize",          "GetItemStackSize(object oItem)",                     "Inventory"),
    ("SetItemStackSize",          "SetItemStackSize(object oItem, int nSize)",          "Inventory"),
    ("GiveItemToCreature",        "GiveItemToCreature(object oItem, object oGiveTo)",   "Inventory"),
    ("TakeItemFromCreature",      "TakeItemFromCreature(object oItem, object oTakeFrom, int bDisplayFeedback=FALSE)", "Inventory"),
    # ── Location / Area ───────────────────────────────────────────────────────
    ("GetLocation",               "GetLocation(object oObject)",                        "Location/Area"),
    ("GetPositionFromItself",     "GetPositionFromItself(object oTarget)",               "Location/Area"),
    ("Location",                  "Location(vector vPosition, float fOrientation)",      "Location/Area"),
    ("GetFacing",                 "GetFacing(object oTarget)",                           "Location/Area"),
    ("GetDistanceBetween",        "GetDistanceBetween(object oObjectA, object oObjectB)", "Location/Area"),
    ("GetDistanceBetweenLocations","GetDistanceBetweenLocations(location lLocationA, location lLocationB)", "Location/Area"),
    ("JumpToLocation",            "JumpToLocation(location lDestination)",               "Location/Area"),
    ("SetFacing",                 "SetFacing(float fDirection)",                         "Location/Area"),
    ("GetPositionFromLocation",   "GetPositionFromLocation(location lLocation)",         "Location/Area"),
    ("GetAreaFromLocation",       "GetAreaFromLocation(location lLocation)",             "Location/Area"),
    # ── String ───────────────────────────────────────────────────────────────
    ("GetStringLength",           "GetStringLength(string sString)",                     "String"),
    ("GetStringLowerCase",        "GetStringLowerCase(string sString)",                  "String"),
    ("GetStringUpperCase",        "GetStringUpperCase(string sString)",                  "String"),
    ("GetStringLeft",             "GetStringLeft(string sString, int nCount)",           "String"),
    ("GetStringRight",            "GetStringRight(string sString, int nCount)",          "String"),
    ("GetSubString",              "GetSubString(string sString, int nStart, int nCount)", "String"),
    ("FindSubString",             "FindSubString(string sString, string sSubString, int nStart=0)", "String"),
    ("InsertString",              "InsertString(string sDestination, string sString, int nPosition)", "String"),
    ("IntToString",               "IntToString(int nInteger)",                           "String"),
    ("FloatToString",             "FloatToString(float fFloat, int nWidth=18, int nDecimals=9)", "String"),
    ("StringToInt",               "StringToInt(string sNumber)",                        "String"),
    ("StringToFloat",             "StringToFloat(string sFloat)",                       "String"),
    ("SendMessageToPC",           "SendMessageToPC(object oPlayer, string sMessage)",   "String"),
    ("SpeakString",               "SpeakString(string sStringToSpeak, int nTalkVolume=TALKVOLUME_TALK)", "String"),
    ("SpeakStringByStrRef",       "SpeakStringByStrRef(int nStrRef, int nTalkVolume=TALKVOLUME_TALK)", "String"),
    # ── Math ─────────────────────────────────────────────────────────────────
    ("abs",                       "abs(int nValue)",                                     "Math"),
    ("fabs",                      "fabs(float fValue)",                                  "Math"),
    ("cos",                       "cos(float fValue)",                                   "Math"),
    ("sin",                       "sin(float fValue)",                                   "Math"),
    ("tan",                       "tan(float fValue)",                                   "Math"),
    ("acos",                      "acos(float fValue)",                                  "Math"),
    ("asin",                      "asin(float fValue)",                                  "Math"),
    ("atan",                      "atan(float fValue)",                                  "Math"),
    ("sqrt",                      "sqrt(float fValue)",                                  "Math"),
    ("pow",                       "pow(float fValue, float fExponent)",                  "Math"),
    ("log",                       "log(float fValue)",                                   "Math"),
    ("Random",                    "Random(int nMaxInteger)",                             "Math"),
    ("d2",                        "d2(int nNumDice=1)",                                  "Math"),
    ("d4",                        "d4(int nNumDice=1)",                                  "Math"),
    ("d6",                        "d6(int nNumDice=1)",                                  "Math"),
    ("d8",                        "d8(int nNumDice=1)",                                  "Math"),
    ("d10",                       "d10(int nNumDice=1)",                                 "Math"),
    ("d12",                       "d12(int nNumDice=1)",                                 "Math"),
    ("d20",                       "d20(int nNumDice=1)",                                 "Math"),
    ("d100",                      "d100(int nNumDice=1)",                                "Math"),
    ("Vector",                    "Vector(float x=0.0, float y=0.0, float z=0.0)",       "Math"),
    ("VectorNormalize",           "VectorNormalize(vector vVector)",                     "Math"),
    ("VectorMagnitude",           "VectorMagnitude(vector vVector)",                     "Math"),
    ("AngleToVector",             "AngleToVector(float fAngle)",                         "Math"),
    ("VectorToAngle",             "VectorToAngle(vector vVector)",                       "Math"),
    # ── Global/State ─────────────────────────────────────────────────────────
    ("GetGlobalBoolean",          "GetGlobalBoolean(string sIdentifier)",                "Global"),
    ("GetGlobalNumber",           "GetGlobalNumber(string sIdentifier)",                 "Global"),
    ("GetGlobalString",           "GetGlobalString(string sIdentifier)",                 "Global"),
    ("SetGlobalBoolean",          "SetGlobalBoolean(string sIdentifier, int nValue)",    "Global"),
    ("SetGlobalNumber",           "SetGlobalNumber(string sIdentifier, int nValue)",     "Global"),
    ("SetGlobalString",           "SetGlobalString(string sIdentifier, string sValue)",  "Global"),
    ("GetLocalBoolean",           "GetLocalBoolean(object oObject, int nIndex)",         "Global"),
    ("GetLocalNumber",            "GetLocalNumber(object oObject, int nIndex)",          "Global"),
    ("SetLocalBoolean",           "SetLocalBoolean(object oObject, int nIndex, int bValue)", "Global"),
    ("SetLocalNumber",            "SetLocalNumber(object oObject, int nIndex, int nValue)", "Global"),
    # ── Miscellaneous ────────────────────────────────────────────────────────
    ("ExecuteScript",             "ExecuteScript(string sScript, object oTarget, int bClearActions=FALSE)", "Misc"),
    ("GetModule",                 "GetModule()",                                         "Misc"),
    ("GetModuleFileName",         "GetModuleFileName()",                                 "Misc"),
    ("GetTimeSecond",             "GetTimeSecond()",                                     "Misc"),
    ("GetTimeMinute",             "GetTimeMinute()",                                     "Misc"),
    ("GetTimeHour",               "GetTimeHour()",                                       "Misc"),
    ("SetTime",                   "SetTime(int nHour, int nMinute, int nSecond, int nMillisecond)", "Misc"),
    ("MusicBackgroundPlay",       "MusicBackgroundPlay(object oArea)",                   "Misc"),
    ("MusicBackgroundStop",       "MusicBackgroundStop(object oArea)",                   "Misc"),
    ("SetCameraFacing",           "SetCameraFacing(float fDirection, float fDistance=0.0, float fPitch=0.0, int nTransitionType=CAMERA_TRANSITION_TYPE_SNAP)", "Misc"),
    ("FadeToBlack",               "FadeToBlack(object oPC, float fSpeed=FADE_SPEED_MEDIUM)", "Misc"),
    ("FadeFromBlack",             "FadeFromBlack(object oPC, float fSpeed=FADE_SPEED_MEDIUM)", "Misc"),
    ("StopFade",                  "StopFade(object oPC)",                                "Misc"),
    ("BlackScreen",               "BlackScreen(object oPC)",                             "Misc"),
]

# Set of all stdlib names for quick lookup
_STDLIB_NAMES = frozenset(fn for fn, _sig, _cat in NWSCRIPT_STDLIB)


def get_categories() -> List[str]:
    """Return sorted list of unique function categories."""
    seen = {}
    for _fn, _sig, cat in NWSCRIPT_STDLIB:
        seen[cat] = True
    return sorted(seen.keys())


def get_functions_by_category(category: str) -> List[Tuple[str, str]]:
    """Return (name, signature) list for a given category."""
    return [(fn, sig) for fn, sig, cat in NWSCRIPT_STDLIB if cat == category]


def search_functions(query: str) -> List[Tuple[str, str, str]]:
    """Full-text search across names and signatures."""
    q = query.lower()
    return [
        (fn, sig, cat) for fn, sig, cat in NWSCRIPT_STDLIB
        if q in fn.lower() or q in sig.lower()
    ]


# ──────────────────────────────────────────────────────────────────────────────
# Pure-Python tokenizer (no Qt dependency)
# ──────────────────────────────────────────────────────────────────────────────

class NWScriptTokenizer:
    """
    Lightweight tokenizer for NWScript source.
    Returns a list of (token_type, start, end, text) tuples.
    Token types: keyword, type, entrypoint, constant, stdlib, number,
                 string, comment, preproc, operator, identifier, other
    """

    _TOKEN_RE = re.compile(
        r'(?P<comment_block>/\*.*?\*/)' +
        r'|(?P<comment_line>//[^\n]*)' +
        r'|(?P<preproc>^\s*#\w+[^\n]*)' +
        r'|(?P<string>"(?:[^"\\]|\\.)*")' +
        r'|(?P<number>\b\d+\.?\d*[fF]?\b)' +
        r'|(?P<word>\b[A-Za-z_]\w*\b)' +
        r'|(?P<operator>[+\-*/=<>!&|^~%]+)' +
        r'|(?P<other>.)',
        re.MULTILINE | re.DOTALL,
    )

    def tokenize(self, source: str) -> List[Tuple[str, int, int, str]]:
        tokens = []
        for m in self._TOKEN_RE.finditer(source):
            kind = m.lastgroup
            text = m.group()
            start, end = m.start(), m.end()
            if kind in ("comment_block", "comment_line"):
                tokens.append(("comment", start, end, text))
            elif kind == "preproc":
                tokens.append(("preproc", start, end, text))
            elif kind == "string":
                tokens.append(("string", start, end, text))
            elif kind == "number":
                tokens.append(("number", start, end, text))
            elif kind == "word":
                if text in NWS_ENTRY_POINTS:
                    tokens.append(("entrypoint", start, end, text))
                elif text in NWS_KEYWORDS:
                    tokens.append(("keyword", start, end, text))
                elif text in NWS_TYPES:
                    tokens.append(("type", start, end, text))
                elif text in NWS_CONSTANTS:
                    tokens.append(("constant", start, end, text))
                elif text in _STDLIB_NAMES:
                    tokens.append(("stdlib", start, end, text))
                else:
                    tokens.append(("identifier", start, end, text))
            elif kind == "operator":
                tokens.append(("operator", start, end, text))
            else:
                tokens.append(("other", start, end, text))
        return tokens


# ──────────────────────────────────────────────────────────────────────────────
# Qt Syntax Highlighter
# ──────────────────────────────────────────────────────────────────────────────

class NWScriptHighlighter(QSyntaxHighlighter if _HAS_QT else object):
    """Full-token QSyntaxHighlighter for NWScript .nss files."""

    def __init__(self, document):
        if not _HAS_QT:
            return
        super().__init__(document)
        self._tokenizer = NWScriptTokenizer()
        self._formats: Dict[str, QTextCharFormat] = {}
        self._build_formats()

    def _fmt(self, color: str, bold: bool = False,
             italic: bool = False) -> "QTextCharFormat":
        f = QTextCharFormat()
        f.setForeground(QColor(color))
        if bold:
            f.setFontWeight(700)
        if italic:
            f.setFontItalic(True)
        return f

    def _build_formats(self):
        self._formats = {
            "keyword":    self._fmt(_COL_KEYWORD, bold=True),
            "type":       self._fmt(_COL_TYPE, bold=True),
            "entrypoint": self._fmt(_COL_ENTRYPOINT, bold=True),
            "constant":   self._fmt(_COL_CONSTANT),
            "stdlib":     self._fmt(_COL_STDLIB),
            "number":     self._fmt(_COL_NUMBER),
            "string":     self._fmt(_COL_STRING),
            "comment":    self._fmt(_COL_COMMENT, italic=True),
            "preproc":    self._fmt(_COL_PREPROC),
            "operator":   self._fmt(_COL_OPERATOR),
            "identifier": self._fmt(_COL_OPERATOR),
            "other":      self._fmt(_COL_OPERATOR),
        }

    def highlightBlock(self, text: str):
        if not _HAS_QT:
            return
        # Qt calls this per-line; we need the global offset for the tokenizer
        # but for per-line operation we use simple regex per line
        import re
        # Build combined pattern same as tokenizer but single-line
        rules = [
            (re.compile(r'//[^\n]*'),                        "comment"),
            (re.compile(r'/\*.*?\*/', re.DOTALL),            "comment"),
            (re.compile(r'^#\w+.*'),                         "preproc"),
            (re.compile(r'"(?:[^"\\]|\\.)*"'),               "string"),
            (re.compile(r'\b\d+\.?\d*[fF]?\b'),              "number"),
            (re.compile(
                r'\b(' + '|'.join(sorted(NWS_ENTRY_POINTS)) + r')\b'),   "entrypoint"),
            (re.compile(
                r'\b(' + '|'.join(sorted(NWS_KEYWORDS)) + r')\b'),       "keyword"),
            (re.compile(
                r'\b(' + '|'.join(sorted(NWS_TYPES)) + r')\b'),          "type"),
            (re.compile(
                r'\b(' + '|'.join(sorted(NWS_CONSTANTS)) + r')\b'),      "constant"),
            (re.compile(
                r'\b(' + '|'.join(sorted(_STDLIB_NAMES)) + r')\b'),      "stdlib"),
        ]
        for pattern, token_type in rules:
            fmt = self._formats.get(token_type)
            if fmt is None:
                continue
            for m in pattern.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)


# ──────────────────────────────────────────────────────────────────────────────
# Function Browser Panel (Qt widget)
# ──────────────────────────────────────────────────────────────────────────────

class FunctionBrowserPanel(QWidget if _HAS_QT else object):
    """
    Searchable sidebar listing all NWScript stdlib functions grouped by category.

    Signals:
        function_selected(name: str, signature: str)  — double-click or Enter
    """

    if _HAS_QT:
        function_selected = Signal(str, str)

    STYLE = """
        QWidget { background: #1e1e1e; color: #d4d4d4; }
        QLabel { color: #4fc3f7; font-weight: bold; padding: 2px 0; }
        QLineEdit {
            background: #2d2d2d; color: #d4d4d4;
            border: 1px solid #3e3e42; border-radius: 3px; padding: 2px 6px;
        }
        QTreeWidget {
            background: #252526; color: #d4d4d4;
            border: 1px solid #3e3e42; font-size: 11px;
        }
        QTreeWidget::item:selected { background: #264f78; }
        QTextEdit {
            background: #252526; color: #9cdcfe;
            border: 1px solid #3e3e42; font-family: Consolas, monospace;
            font-size: 10px;
        }
    """

    def __init__(self, parent=None):
        if not _HAS_QT:
            return
        super().__init__(parent)
        self.setStyleSheet(self.STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        lbl = QLabel("Function Browser")
        layout.addWidget(lbl)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search functions…")
        self._search.textChanged.connect(self._on_search)
        layout.addWidget(self._search)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.itemDoubleClicked.connect(self._on_item_dbl_click)
        layout.addWidget(self._tree, stretch=3)

        self._sig_label = QTextEdit()
        self._sig_label.setReadOnly(True)
        self._sig_label.setMaximumHeight(80)
        self._sig_label.setPlaceholderText("Function signature…")
        layout.addWidget(self._sig_label)

        self._populate_tree("")

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_selected_function(self) -> Optional[Tuple[str, str]]:
        """Return (name, signature) of the currently selected item, or None."""
        if not _HAS_QT:
            return None
        item = self._tree.currentItem()
        if item is None or item.parent() is None:
            return None
        name = item.text(0)
        sig = item.data(0, Qt.UserRole) or ""
        return name, sig

    # ── Private ────────────────────────────────────────────────────────────────

    def _populate_tree(self, filter_text: str) -> None:
        if not _HAS_QT:
            return
        self._tree.clear()
        if filter_text:
            hits = search_functions(filter_text)
            if hits:
                parent = QTreeWidgetItem(self._tree, [f"Results ({len(hits)})"])
                for fn, sig, _cat in hits:
                    child = QTreeWidgetItem(parent, [fn])
                    child.setData(0, Qt.UserRole, sig)
                self._tree.expandAll()
        else:
            for cat in get_categories():
                cat_item = QTreeWidgetItem(self._tree, [cat])
                for fn, sig in get_functions_by_category(cat):
                    child = QTreeWidgetItem(cat_item, [fn])
                    child.setData(0, Qt.UserRole, sig)

    def _on_search(self, text: str) -> None:
        self._populate_tree(text.strip())

    def _on_item_dbl_click(self, item, _col: int) -> None:
        if item.parent() is None:
            return
        name = item.text(0)
        sig = item.data(0, Qt.UserRole) or ""
        self._sig_label.setPlainText(sig)
        if _HAS_QT:
            self.function_selected.emit(name, sig)
