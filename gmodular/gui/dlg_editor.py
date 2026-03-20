"""
GModular — DLG (Dialogue) Visual Node-Graph Editor
===================================================

A Qt-based dockable editor for KotOR .dlg conversation trees.

Data model (mirrors PyKotor generics/dlg and Kotor.NET Resources/KotorDLG):
  - DLG: root object with EntryList, ReplyList, StartingList
  - DLGEntry: NPC line (green node)
  - DLGReply: Player option (blue node)
  - DLGLink: directed edge with optional Active condition script

Features
--------
- Headless-safe: the entire Qt import block is guarded; non-Qt code
  (DLGGraph, node/link data-classes) can be used without a display.
- Visual canvas: drag nodes, rubber-band select, zoom with wheel.
- Properties panel: edit text, speaker, script, voice-over, conditions.
- Toolbar: New Node, New Reply, Delete, Zoom In/Out, Fit, Import/Export.
- GFF round-trip: load from GFF bytes (via gff_reader) or save to GFF bytes
  (via gff_writer) — no dependency on the game being installed.
- MCP-ready: DLGGraph is importable from non-Qt code for testing.

References
----------
PyKotor   Libraries/PyKotor/src/pykotor/resource/generics/dlg/
Kotor.NET Kotor.NET/Resources/KotorDLG/DLG.cs
Wiki      wiki/GFF-DLG.md (EntryList / ReplyList / StartingList / Link fields)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# ── Qt availability guard ────────────────────────────────────────────────────
try:
    from qtpy.QtCore import (
        QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer,
        Signal,
    )
    from qtpy.QtGui import (
        QBrush, QColor, QFont, QFontMetrics, QKeySequence,
        QPainter, QPainterPath, QPen, QPolygonF,
    )
    from qtpy.QtWidgets import (
        QAbstractItemView, QAction, QApplication, QComboBox,
        QDockWidget, QFormLayout, QGraphicsEllipseItem,
        QGraphicsItem, QGraphicsLineItem, QGraphicsPathItem,
        QGraphicsRectItem, QGraphicsScene, QGraphicsTextItem,
        QGraphicsView, QGroupBox, QHBoxLayout, QLabel,
        QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
        QMenu, QPlainTextEdit, QPushButton, QScrollArea,
        QSizePolicy, QSplitter, QToolBar, QToolButton,
        QTreeWidget, QTreeWidgetItem, QUndoCommand, QUndoStack,
        QVBoxLayout, QWidget,
    )
    _HAS_QT = True
except ImportError:
    _HAS_QT = False


# ═══════════════════════════════════════════════════════════════════════════
#  Pure-Python data model (no Qt dependency)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class DLGNodeData:
    """In-memory representation of a single DLG node (Entry or Reply)."""
    node_id:   int          = 0
    is_entry:  bool         = True          # True=NPC entry, False=player reply
    text:      str          = ""            # Localised text (English)
    speaker:   str          = ""            # Speaker tag (entries only)
    listener:  str          = ""            # Listener tag
    script:    str          = ""            # Script ResRef executed when reached
    script2:   str          = ""            # KotOR2 second script
    vo_resref: str          = ""            # Voice-over ResRef
    sound:     str          = ""            # Sound effect ResRef
    quest:     str          = ""            # Journal quest tag
    quest_entry: int        = 0
    comment:   str          = ""
    # KotOR 2 extensions
    camera_style: int       = 0             # Camera style index (K2 DLG only)
    anim_list: List[Dict[str, Any]] = field(default_factory=list)  # AnimList entries
    # Visual layout (pixels)
    x: float = 0.0
    y: float = 0.0
    # Child links (outgoing edges)  [(target_node_id, active_script, display_inactive)]
    links: List[Tuple[int, str, bool]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "node_id":      self.node_id,
            "is_entry":     self.is_entry,
            "text":         self.text,
            "speaker":      self.speaker,
            "listener":     self.listener,
            "script":       self.script,
            "vo_resref":    self.vo_resref,
            "sound":        self.sound,
            "quest":        self.quest,
            "quest_entry":  self.quest_entry,
            "comment":      self.comment,
            "x": self.x,  "y": self.y,
            "links": [[t, a, d] for t, a, d in self.links],
        }
        if self.script2:
            d["script2"] = self.script2
        if self.camera_style:
            d["camera_style"] = self.camera_style
        if self.anim_list:
            d["anim_list"] = self.anim_list
        return d


@dataclass
class DLGGraphData:
    """Complete in-memory DLG conversation graph."""
    nodes: Dict[int, DLGNodeData] = field(default_factory=dict)
    starters: List[int] = field(default_factory=list)   # node_ids for StartingList
    on_abort: str = ""
    on_end:   str = ""
    skippable: bool = True
    conversation_type: int = 0  # 0=Human, 1=Computer, 2=Other
    camera_model: str = ""

    def add_entry(self, text: str = "NPC line") -> DLGNodeData:
        nid = max(self.nodes, default=-1) + 1
        node = DLGNodeData(node_id=nid, is_entry=True, text=text,
                           x=50.0 + (nid % 5) * 220, y=50.0 + (nid // 5) * 140)
        self.nodes[nid] = node
        return node

    def add_reply(self, text: str = "Player reply") -> DLGNodeData:
        nid = max(self.nodes, default=-1) + 1
        node = DLGNodeData(node_id=nid, is_entry=False, text=text,
                           x=160.0 + (nid % 5) * 220, y=100.0 + (nid // 5) * 140)
        self.nodes[nid] = node
        return node

    def link(self, src_id: int, tgt_id: int, active: str = "") -> None:
        if src_id in self.nodes:
            node = self.nodes[src_id]
            if not any(t == tgt_id for t, _, _ in node.links):
                node.links.append((tgt_id, active, False))

    def unlink(self, src_id: int, tgt_id: int) -> None:
        if src_id in self.nodes:
            self.nodes[src_id].links = [
                (t, a, d) for t, a, d in self.nodes[src_id].links if t != tgt_id
            ]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "starters": self.starters,
            "on_abort": self.on_abort,
            "on_end":   self.on_end,
            "skippable": self.skippable,
            "conversation_type": self.conversation_type,
            "camera_model": self.camera_model,
            "nodes": {str(k): v.to_dict() for k, v in self.nodes.items()},
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DLGGraphData":
        g = cls(
            starters=list(d.get("starters", [])),
            on_abort=d.get("on_abort", ""),
            on_end=d.get("on_end", ""),
            skippable=bool(d.get("skippable", True)),
            conversation_type=int(d.get("conversation_type", 0)),
            camera_model=d.get("camera_model", ""),
        )
        for nid_str, nd in d.get("nodes", {}).items():
            n = DLGNodeData(
                node_id=int(nid_str),
                is_entry=bool(nd.get("is_entry", True)),
                text=nd.get("text", ""),
                speaker=nd.get("speaker", ""),
                listener=nd.get("listener", ""),
                script=nd.get("script", ""),
                script2=nd.get("script2", ""),
                vo_resref=nd.get("vo_resref", ""),
                sound=nd.get("sound", ""),
                quest=nd.get("quest", ""),
                quest_entry=int(nd.get("quest_entry", 0)),
                comment=nd.get("comment", ""),
                camera_style=int(nd.get("camera_style", 0)),
                anim_list=list(nd.get("anim_list", [])),
                x=float(nd.get("x", 0)),
                y=float(nd.get("y", 0)),
                links=[(int(t), str(a), bool(di)) for t, a, di in nd.get("links", [])],
            )
            g.nodes[n.node_id] = n
        return g

    def to_gff_bytes(self) -> bytes:
        """Serialise the conversation graph to a KotOR-native DLG GFF binary.

        Produces a GFF V3.2 binary (file type "DLG ") with:
          - EntryList  — NPC lines (is_entry=True nodes, sorted by node_id)
          - ReplyList  — Player options (is_entry=False nodes, sorted by node_id)
          - StartingList — indices into EntryList
          - Top-level fields: EndConverAbort, EndConversation, Skippable,
            ConversationType, CameraModel

        Compatible with KotOR 1 & 2; AnimList / CameraStyle fields are written
        only when they carry non-default values so vanilla DLG files remain
        clean and compact.
        """
        from gmodular.formats.gff_writer import GFFWriter
        from gmodular.formats.gff_types import (
            GFFRoot, GFFStruct, GFFField, GFFFieldType,
        )

        # Separate and sort entries / replies
        entries = sorted(
            (n for n in self.nodes.values() if n.is_entry),
            key=lambda n: n.node_id,
        )
        replies = sorted(
            (n for n in self.nodes.values() if not n.is_entry),
            key=lambda n: n.node_id,
        )

        # Build index maps: node_id → list-index
        entry_idx = {n.node_id: i for i, n in enumerate(entries)}
        reply_idx = {n.node_id: i for i, n in enumerate(replies)}

        # ── Helper: build a DLG-link struct ─────────────────────────────────
        def _link_struct(target_list_idx: int, active: str, display_inactive: bool,
                         is_entry_link: bool) -> GFFStruct:
            """Build a Link struct (inside RepliesList or EntriesList)."""
            s = GFFStruct(struct_id=1)
            s.fields["Index"] = GFFField("Index", GFFFieldType.DWORD,    target_list_idx)
            s.fields["Active"] = GFFField("Active", GFFFieldType.RESREF,   active or "")
            s.fields["DisplayInactive"] = GFFField("DisplayInactive", GFFFieldType.BYTE,     int(display_inactive))
            return s

        # ── Helper: build an Entry struct ────────────────────────────────────
        def _entry_struct(node: "DLGNodeData") -> GFFStruct:
            s = GFFStruct(struct_id=1)
            # Localised text: language 0 = English
            s.fields["Text"] = GFFField("Text", GFFFieldType.CEXOLOCSTRING, node.text or "")
            s.fields["Speaker"] = GFFField("Speaker", GFFFieldType.CEXOSTRING, node.speaker or "")
            s.fields["Listener"] = GFFField("Listener", GFFFieldType.CEXOSTRING,
                                            node.listener or "")
            s.fields["Script"] = GFFField("Script", GFFFieldType.RESREF, node.script or "")
            # KotOR 2: Script2 — second conditional script (written only when non-empty)
            if getattr(node, "script2", ""):
                s.fields["Script2"] = GFFField("Script2", GFFFieldType.RESREF, node.script2)
            s.fields["VO_ResRef"] = GFFField("VO_ResRef", GFFFieldType.RESREF, node.vo_resref or "")
            s.fields["Sound"] = GFFField("Sound", GFFFieldType.RESREF, node.sound or "")
            s.fields["Quest"] = GFFField("Quest", GFFFieldType.CEXOSTRING, node.quest or "")
            s.fields["QuestEntry"] = GFFField("QuestEntry", GFFFieldType.DWORD, node.quest_entry or 0)
            s.fields["Comment"] = GFFField("Comment", GFFFieldType.CEXOSTRING, node.comment or "")
            s.fields["Delay"] = GFFField("Delay", GFFFieldType.DWORD, 0xFFFFFFFF)
            # AnimList (K2 extension) — only written when present
            anim_list_structs = []
            for anim in getattr(node, "anim_list", []):
                anim_s = GFFStruct(struct_id=1)
                anim_s.fields["Animation"] = GFFField("Animation", GFFFieldType.DWORD,
                                                       int(anim.get("id", 0)))
                anim_s.fields["Participant"] = GFFField("Participant", GFFFieldType.CEXOSTRING,
                                                         str(anim.get("participant", "")))
                anim_list_structs.append(anim_s)
            s.fields["AnimList"] = GFFField("AnimList", GFFFieldType.LIST, anim_list_structs)
            # CameraStyle (K2 extension) — only when non-zero
            cs = getattr(node, "camera_style", 0)
            if cs:
                s.fields["CameraStyle"] = GFFField("CameraStyle", GFFFieldType.DWORD, int(cs))
            # RepliesList
            reply_structs = []
            for tgt_id, active, disp in node.links:
                if tgt_id in reply_idx:
                    reply_structs.append(
                        _link_struct(reply_idx[tgt_id], active, disp, False)
                    )
            s.fields["RepliesList"] = GFFField("RepliesList", GFFFieldType.LIST, reply_structs)
            return s

        # ── Helper: build a Reply struct ─────────────────────────────────────
        def _reply_struct(node: "DLGNodeData") -> GFFStruct:
            s = GFFStruct(struct_id=1)
            s.fields["Text"] = GFFField("Text", GFFFieldType.CEXOLOCSTRING, node.text or "")
            s.fields["Listener"] = GFFField("Listener", GFFFieldType.CEXOSTRING,
                                            node.listener or "")
            s.fields["Script"] = GFFField("Script", GFFFieldType.RESREF, node.script or "")
            # KotOR 2: Script2 — second conditional script (written only when non-empty)
            if getattr(node, "script2", ""):
                s.fields["Script2"] = GFFField("Script2", GFFFieldType.RESREF, node.script2)
            s.fields["VO_ResRef"] = GFFField("VO_ResRef", GFFFieldType.RESREF, node.vo_resref or "")
            s.fields["Sound"] = GFFField("Sound", GFFFieldType.RESREF, node.sound or "")
            s.fields["Quest"] = GFFField("Quest", GFFFieldType.CEXOSTRING, node.quest or "")
            s.fields["QuestEntry"] = GFFField("QuestEntry", GFFFieldType.DWORD, node.quest_entry or 0)
            s.fields["Comment"] = GFFField("Comment", GFFFieldType.CEXOSTRING, node.comment or "")
            s.fields["Delay"] = GFFField("Delay", GFFFieldType.DWORD, 0xFFFFFFFF)
            # EntriesList
            entry_structs = []
            for tgt_id, active, disp in node.links:
                if tgt_id in entry_idx:
                    entry_structs.append(
                        _link_struct(entry_idx[tgt_id], active, disp, True)
                    )
            s.fields["EntriesList"] = GFFField("EntriesList", GFFFieldType.LIST, entry_structs)
            return s

        # ── Build StartingList ───────────────────────────────────────────────
        def _starter_struct(entry_list_idx: int) -> GFFStruct:
            s = GFFStruct(struct_id=1)
            s.fields["Index"] = GFFField("Index", GFFFieldType.DWORD, entry_list_idx)
            s.fields["Active"] = GFFField("Active", GFFFieldType.RESREF, "")
            return s

        # ── Assemble root ────────────────────────────────────────────────────
        root = GFFRoot(file_type="DLG ")

        root.fields["EndConverAbort"] = GFFField("EndConverAbort", GFFFieldType.RESREF,
                                                  self.on_abort or "")
        root.fields["EndConversation"] = GFFField("EndConversation", GFFFieldType.RESREF,
                                                   self.on_end or "")
        root.fields["Skippable"] = GFFField("Skippable", GFFFieldType.BYTE,
                                                   int(self.skippable))
        root.fields["ConversationType"] = GFFField("ConversationType", GFFFieldType.INT,
                                                    self.conversation_type)
        root.fields["CameraModel"] = GFFField("CameraModel", GFFFieldType.RESREF,
                                                   self.camera_model or "")
        root.fields["NumWords"] = GFFField("NumWords", GFFFieldType.DWORD, 0)
        root.fields["DelayEntry"] = GFFField("DelayEntry", GFFFieldType.DWORD, 0)
        root.fields["DelayReply"] = GFFField("DelayReply", GFFFieldType.DWORD, 0)

        root.fields["EntryList"] = GFFField("EntryList", GFFFieldType.LIST,
                                               [_entry_struct(n) for n in entries])
        root.fields["ReplyList"] = GFFField("ReplyList", GFFFieldType.LIST,
                                               [_reply_struct(n) for n in replies])

        starter_structs = []
        for sid in self.starters:
            if sid in entry_idx:
                starter_structs.append(_starter_struct(entry_idx[sid]))
        root.fields["StartingList"] = GFFField("StartingList", GFFFieldType.LIST, starter_structs)

        return GFFWriter(root).to_bytes()

    @classmethod
    def from_gff_bytes(cls, data: bytes) -> "DLGGraphData":
        """Parse a DLG GFF binary and build a DLGGraphData.

        Reads EntryList, ReplyList, and StartingList from the GFF tree
        without requiring PyKotor — uses our own gff_reader.
        """
        from gmodular.formats.gff_reader import GFFReader
        reader = GFFReader.from_bytes(data)
        root = reader.parse()
        return cls._from_gff_root(root)

    @classmethod
    def _from_gff_dict(cls, d: Dict[str, Any]) -> "DLGGraphData":
        """Legacy: build DLGGraphData from a plain dict (kept for compatibility)."""
        # This path is now only used by external callers passing a dict.
        # Internal parsing uses _from_gff_root via from_gff_bytes.
        g = cls(
            on_abort=str(d.get("EndConverAbort", "")),
            on_end=str(d.get("EndConversation", "")),
            skippable=bool(d.get("Skippable", 1)),
            conversation_type=int(d.get("ConversationType", 0)),
            camera_model=str(d.get("CameraModel", "")),
        )

        def _loc_text(val: Any) -> str:
            if isinstance(val, dict):
                return str(val.get(0, val.get("0", val.get("en", ""))))
            return str(val) if val else ""

        for i, entry in enumerate(d.get("EntryList", [])):
            if not isinstance(entry, dict):
                continue
            nid = len(g.nodes)
            links = []
            for lnk in entry.get("RepliesList", []):
                if isinstance(lnk, dict):
                    links.append((int(lnk.get("Index", 0)) + 10000,
                                  str(lnk.get("Active", "")),
                                  bool(lnk.get("DisplayInactive", False))))
            n = DLGNodeData(
                node_id=nid, is_entry=True,
                text=_loc_text(entry.get("Text", "")),
                speaker=str(entry.get("Speaker", "")),
                listener=str(entry.get("Listener", "")),
                script=str(entry.get("Script", "")),
                vo_resref=str(entry.get("VO_ResRef", "")),
                sound=str(entry.get("Sound", "")),
                quest=str(entry.get("Quest", "")),
                quest_entry=int(entry.get("QuestEntry", 0)),
                comment=str(entry.get("Comment", "")),
                camera_style=int(entry.get("CameraStyle", 0)),
                anim_list=[
                    {"id": int(a.get("Animation", 0)),
                     "participant": str(a.get("Participant", ""))}
                    for a in entry.get("AnimList", [])
                    if isinstance(a, dict)
                ],
                x=50.0 + (i % 4) * 240,
                y=50.0 + (i // 4) * 150,
                links=links,
            )
            g.nodes[nid] = n

        for i, reply in enumerate(d.get("ReplyList", [])):
            if not isinstance(reply, dict):
                continue
            nid = 10000 + i
            links = []
            for lnk in reply.get("EntriesList", []):
                if isinstance(lnk, dict):
                    links.append((int(lnk.get("Index", 0)),
                                  str(lnk.get("Active", "")),
                                  bool(lnk.get("DisplayInactive", False))))
            n = DLGNodeData(
                node_id=nid, is_entry=False,
                text=_loc_text(reply.get("Text", "")),
                listener=str(reply.get("Listener", "")),
                script=str(reply.get("Script", "")),
                vo_resref=str(reply.get("VO_ResRef", "")),
                sound=str(reply.get("Sound", "")),
                quest=str(reply.get("Quest", "")),
                quest_entry=int(reply.get("QuestEntry", 0)),
                comment=str(reply.get("Comment", "")),
                x=160.0 + (i % 4) * 240,
                y=120.0 + (i // 4) * 150,
                links=links,
            )
            g.nodes[nid] = n

        for lnk in d.get("StartingList", []):
            if isinstance(lnk, dict):
                g.starters.append(int(lnk.get("Index", 0)))

        return g

    @classmethod
    def _from_gff_root(cls, root) -> "DLGGraphData":
        """Build DLGGraphData from a parsed GFFRoot (gmodular.formats.gff_types).

        This is the primary parse path for from_gff_bytes.  It reads the
        GFFRoot struct directly without a dict conversion step so that nested
        GFFStruct lists are handled correctly.
        """
        g = cls(
            on_abort=str(root.get("EndConverAbort") or ""),
            on_end=str(root.get("EndConversation") or ""),
            skippable=bool(root.get("Skippable", 1)),
            conversation_type=int(root.get("ConversationType", 0) or 0),
            camera_model=str(root.get("CameraModel") or ""),
        )

        def _loc(val: Any) -> str:
            """Extract English string from a CExoLocString value."""
            if val is None:
                return ""
            if isinstance(val, str):
                return val
            if isinstance(val, dict):
                # CEXOLOCSTRING parsed as {lang_id: text} or {"strref": int, 0: text}
                return str(val.get(0, val.get("0", val.get("en", ""))))
            return str(val)

        def _str(val: Any) -> str:
            return str(val) if val is not None else ""

        # EntryList (NPC lines, is_entry=True)
        entry_list = root.get("EntryList") or []
        for i, entry_struct in enumerate(entry_list):
            nid = i  # entry IDs are their 0-based index in EntryList
            links: List[Tuple[int, str, bool]] = []
            replies_list = entry_struct.get("RepliesList") if hasattr(entry_struct, "get") else []
            for lnk in (replies_list or []):
                if hasattr(lnk, "get"):
                    links.append((
                        int(lnk.get("Index", 0) or 0) + 10000,
                        _str(lnk.get("Active", "")),
                        bool(lnk.get("DisplayInactive", 0)),
                    ))
            anim_structs = entry_struct.get("AnimList") if hasattr(entry_struct, "get") else []
            anim_list = []
            for a in (anim_structs or []):
                if hasattr(a, "get"):
                    anim_list.append({
                        "id": int(a.get("Animation", 0) or 0),
                        "participant": _str(a.get("Participant", "")),
                    })
            n = DLGNodeData(
                node_id=nid, is_entry=True,
                text=_loc(entry_struct.get("Text")) if hasattr(entry_struct, "get") else "",
                speaker=_str(entry_struct.get("Speaker")) if hasattr(entry_struct, "get") else "",
                listener=_str(entry_struct.get("Listener")) if hasattr(entry_struct, "get") else "",
                script=_str(entry_struct.get("Script")) if hasattr(entry_struct, "get") else "",
                script2=_str(entry_struct.get("Script2")) if hasattr(entry_struct, "get") else "",
                vo_resref=_str(entry_struct.get("VO_ResRef")) if hasattr(entry_struct, "get") else "",
                sound=_str(entry_struct.get("Sound")) if hasattr(entry_struct, "get") else "",
                quest=_str(entry_struct.get("Quest")) if hasattr(entry_struct, "get") else "",
                quest_entry=int(entry_struct.get("QuestEntry", 0) or 0) if hasattr(entry_struct, "get") else 0,
                comment=_str(entry_struct.get("Comment")) if hasattr(entry_struct, "get") else "",
                camera_style=int(entry_struct.get("CameraStyle", 0) or 0) if hasattr(entry_struct, "get") else 0,
                anim_list=anim_list,
                x=50.0 + (i % 4) * 240,
                y=50.0 + (i // 4) * 150,
                links=links,
            )
            g.nodes[nid] = n

        # ReplyList (player options, is_entry=False) — IDs offset by 10000
        reply_list = root.get("ReplyList") or []
        for i, reply_struct in enumerate(reply_list):
            nid = 10000 + i
            links = []
            entries_list = reply_struct.get("EntriesList") if hasattr(reply_struct, "get") else []
            for lnk in (entries_list or []):
                if hasattr(lnk, "get"):
                    links.append((
                        int(lnk.get("Index", 0) or 0),
                        _str(lnk.get("Active", "")),
                        bool(lnk.get("DisplayInactive", 0)),
                    ))
            n = DLGNodeData(
                node_id=nid, is_entry=False,
                text=_loc(reply_struct.get("Text")) if hasattr(reply_struct, "get") else "",
                listener=_str(reply_struct.get("Listener")) if hasattr(reply_struct, "get") else "",
                script=_str(reply_struct.get("Script")) if hasattr(reply_struct, "get") else "",
                script2=_str(reply_struct.get("Script2")) if hasattr(reply_struct, "get") else "",
                vo_resref=_str(reply_struct.get("VO_ResRef")) if hasattr(reply_struct, "get") else "",
                sound=_str(reply_struct.get("Sound")) if hasattr(reply_struct, "get") else "",
                quest=_str(reply_struct.get("Quest")) if hasattr(reply_struct, "get") else "",
                quest_entry=int(reply_struct.get("QuestEntry", 0) or 0) if hasattr(reply_struct, "get") else 0,
                comment=_str(reply_struct.get("Comment")) if hasattr(reply_struct, "get") else "",
                x=160.0 + (i % 4) * 240,
                y=120.0 + (i // 4) * 150,
                links=links,
            )
            g.nodes[nid] = n

        # StartingList → starter node_ids (0-based index into EntryList = node_id)
        starting_list = root.get("StartingList") or []
        for lnk in starting_list:
            if hasattr(lnk, "get"):
                g.starters.append(int(lnk.get("Index", 0) or 0))

        return g


# ═══════════════════════════════════════════════════════════════════════════
#  Qt Graphics Items
# ═══════════════════════════════════════════════════════════════════════════

if _HAS_QT:
    _ENTRY_COLOR  = QColor("#2a7a3b")   # green  – NPC lines
    _REPLY_COLOR  = QColor("#2a4a8a")   # blue   – player replies
    _STARTER_RING = QColor("#f5c518")   # gold   – starter outline
    _LINK_COLOR   = QColor("#aaaaaa")
    _LINK_SEL     = QColor("#f5c518")
    _NODE_W, _NODE_H = 200, 70
    _CORNER       = 8

    class DLGNodeItem(QGraphicsRectItem):
        """A rounded-rectangle node card on the canvas."""

        def __init__(self, node: DLGNodeData, is_starter: bool = False):
            super().__init__(0, 0, _NODE_W, _NODE_H)
            self.node       = node
            self.is_starter = is_starter
            self._build()
            self.setPos(node.x, node.y)
            self.setFlag(QGraphicsItem.ItemIsMovable,   True)
            self.setFlag(QGraphicsItem.ItemIsSelectable, True)
            self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)

        def _build(self):
            color = _ENTRY_COLOR if self.node.is_entry else _REPLY_COLOR
            self.setBrush(QBrush(color))
            pen = QPen(_STARTER_RING if self.is_starter else Qt.white, 2 if self.is_starter else 1)
            self.setPen(pen)
            # Label
            label = self.node.speaker or ("Entry" if self.node.is_entry else "Reply")
            kind_txt = f"[{label}]  #{self.node.node_id}"
            preview = (self.node.text[:28] + "…") if len(self.node.text) > 28 else self.node.text
            txt = QGraphicsTextItem(f"{kind_txt}\n{preview}", self)
            txt.setDefaultTextColor(Qt.white)
            f = QFont("Segoe UI", 8)
            txt.setFont(f)
            txt.setPos(6, 4)

        def port_out(self) -> QPointF:
            """Bottom-centre connection point (outgoing edge start)."""
            return self.mapToScene(QPointF(_NODE_W / 2, _NODE_H))

        def port_in(self) -> QPointF:
            """Top-centre connection point (incoming edge end)."""
            return self.mapToScene(QPointF(_NODE_W / 2, 0))

        def itemChange(self, change, value):
            if change == QGraphicsItem.ItemPositionHasChanged:
                scene = self.scene()
                if scene and hasattr(scene, "update_edges"):
                    scene.update_edges(self)
            return super().itemChange(change, value)

        def paint(self, painter: QPainter, option, widget=None):
            color = _ENTRY_COLOR if self.node.is_entry else _REPLY_COLOR
            if self.isSelected():
                color = color.lighter(140)
            pen = QPen(_STARTER_RING if self.is_starter else Qt.white,
                       2 if self.is_starter else 1)
            painter.setPen(pen)
            painter.setBrush(QBrush(color))
            painter.setRenderHint(QPainter.Antialiasing)
            painter.drawRoundedRect(self.rect(), _CORNER, _CORNER)
            # script indicator dot
            if self.node.script:
                painter.setBrush(QBrush(QColor("#f5c518")))
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(int(_NODE_W - 14), 4, 8, 8)

    class DLGEdgeItem(QGraphicsPathItem):
        """A bezier-curve directed edge between two DLGNodeItems."""

        def __init__(self, src: DLGNodeItem, tgt: DLGNodeItem,
                     active_script: str = ""):
            super().__init__()
            self.src = src
            self.tgt = tgt
            self.active_script = active_script
            self.setPen(QPen(
                QColor("#f5c518") if active_script else _LINK_COLOR,
                1.5, Qt.SolidLine, Qt.RoundCap))
            self.setZValue(-1)
            self.update_path()

        def update_path(self):
            p1 = self.src.port_out()
            p2 = self.tgt.port_in()
            mid_y = (p1.y() + p2.y()) / 2
            path = QPainterPath(p1)
            path.cubicTo(QPointF(p1.x(), mid_y),
                         QPointF(p2.x(), mid_y), p2)
            self.setPath(path)
            # arrowhead
            self._draw_arrow(p2, p1)

        def _draw_arrow(self, tip: QPointF, origin: QPointF):
            # Tiny filled triangle at the tip
            dx, dy = tip.x() - origin.x(), tip.y() - origin.y()
            length = (dx**2 + dy**2) ** 0.5 or 1
            dx, dy = dx / length, dy / length
            size = 8
            left  = QPointF(tip.x() - size*dx + size*0.5*dy,
                            tip.y() - size*dy - size*0.5*dx)
            right = QPointF(tip.x() - size*dx - size*0.5*dy,
                            tip.y() - size*dy + size*0.5*dx)
            arrow = QPainterPath()
            arrow.moveTo(tip)
            arrow.lineTo(left)
            arrow.lineTo(right)
            arrow.closeSubpath()
            path = self.path()
            path.addPath(arrow)
            self.setPath(path)

    # ── Canvas ────────────────────────────────────────────────────────────

    class DLGCanvas(QGraphicsScene):
        node_selected   = Signal(object)   # DLGNodeData | None
        graph_modified  = Signal()

        def __init__(self, graph: DLGGraphData, parent=None):
            super().__init__(parent)
            self.graph  = graph
            self._items: Dict[int, DLGNodeItem] = {}
            self._edges: List[DLGEdgeItem]      = []
            self._rebuild()

        # ── Build ──────────────────────────────────────────────────────

        def _rebuild(self):
            self.clear()
            self._items.clear()
            self._edges.clear()
            starters = set(self.graph.starters)
            for nid, node in self.graph.nodes.items():
                item = DLGNodeItem(node, is_starter=(nid in starters))
                self._items[nid] = item
                self.addItem(item)
            self._rebuild_edges()

        def _rebuild_edges(self):
            for edge in list(self._edges):
                self.removeItem(edge)
            self._edges.clear()
            for nid, src_item in self._items.items():
                for tgt_id, active, _disp in self.graph.nodes[nid].links:
                    if tgt_id in self._items:
                        edge = DLGEdgeItem(src_item, self._items[tgt_id], active)
                        self.addItem(edge)
                        self._edges.append(edge)

        def update_edges(self, moved_item: DLGNodeItem):
            for edge in self._edges:
                if edge.src is moved_item or edge.tgt is moved_item:
                    edge.update_path()
            # persist visual position back to data
            node = moved_item.node
            node.x = moved_item.pos().x()
            node.y = moved_item.pos().y()

        # ── Data mutation ──────────────────────────────────────────────

        def add_entry(self) -> DLGNodeData:
            node = self.graph.add_entry()
            item = DLGNodeItem(node)
            self._items[node.node_id] = item
            self.addItem(item)
            self.graph_modified.emit()
            return node

        def add_reply(self) -> DLGNodeData:
            node = self.graph.add_reply()
            item = DLGNodeItem(node)
            self._items[node.node_id] = item
            self.addItem(item)
            self.graph_modified.emit()
            return node

        def delete_selected(self):
            for item in self.selectedItems():
                if isinstance(item, DLGNodeItem):
                    nid = item.node.node_id
                    # remove from all link lists
                    for n in self.graph.nodes.values():
                        n.links = [(t, a, d) for t, a, d in n.links if t != nid]
                    if nid in self.graph.starters:
                        self.graph.starters.remove(nid)
                    del self.graph.nodes[nid]
            self._rebuild()
            self.graph_modified.emit()

        def link_selected(self):
            sel = [i for i in self.selectedItems() if isinstance(i, DLGNodeItem)]
            if len(sel) == 2:
                self.graph.link(sel[0].node.node_id, sel[1].node.node_id)
                self._rebuild_edges()
                self.graph_modified.emit()

        def set_starter(self, nid: int):
            if nid not in self.graph.starters:
                self.graph.starters.append(nid)
            self._rebuild()
            self.graph_modified.emit()

        # ── Selection ──────────────────────────────────────────────────

        def selectionChanged_handler(self):
            sel = [i for i in self.selectedItems() if isinstance(i, DLGNodeItem)]
            self.node_selected.emit(sel[0].node if sel else None)


    # ── Properties Panel ─────────────────────────────────────────────────

    class DLGPropertiesPanel(QWidget):
        """Right-hand panel: editable fields for the selected node."""

        node_changed = Signal()

        def __init__(self, parent=None):
            super().__init__(parent)
            self._node: Optional[DLGNodeData] = None
            self._build_ui()

        def _build_ui(self):
            layout = QVBoxLayout(self)
            layout.setContentsMargins(4, 4, 4, 4)

            title = QLabel("<b>Node Properties</b>")
            layout.addWidget(title)

            form = QFormLayout()
            form.setLabelAlignment(Qt.AlignRight)

            self._kind_lbl       = QLabel("—")
            self._id_lbl         = QLabel("—")
            self._speaker_ed     = QLineEdit()
            self._script_ed      = QLineEdit()
            self._script2_ed     = QLineEdit()
            self._vo_ed          = QLineEdit()
            self._sound_ed       = QLineEdit()
            self._quest_ed       = QLineEdit()
            self._comment_ed     = QLineEdit()
            self._camera_style_ed = QLineEdit()
            self._camera_style_ed.setPlaceholderText("0 = default")
            self._text_ed        = QPlainTextEdit()
            self._text_ed.setFixedHeight(80)

            form.addRow("Type:",         self._kind_lbl)
            form.addRow("ID:",           self._id_lbl)
            form.addRow("Speaker:",      self._speaker_ed)
            form.addRow("Text:",         self._text_ed)
            form.addRow("Script:",       self._script_ed)
            form.addRow("Script2:",      self._script2_ed)
            form.addRow("VO:",           self._vo_ed)
            form.addRow("Sound:",        self._sound_ed)
            form.addRow("Quest:",        self._quest_ed)
            form.addRow("Comment:",      self._comment_ed)
            form.addRow("CameraStyle:",  self._camera_style_ed)
            layout.addLayout(form)

            # AnimList group box
            anim_group = QGroupBox("Animation List (KotOR 2)")
            anim_vbox  = QVBoxLayout(anim_group)
            anim_hbox  = QHBoxLayout()
            self._anim_list = QListWidget()
            self._anim_list.setFixedHeight(80)
            self._anim_add_btn = QPushButton("+")
            self._anim_add_btn.setFixedWidth(28)
            self._anim_del_btn = QPushButton("−")
            self._anim_del_btn.setFixedWidth(28)
            anim_hbox.addWidget(self._anim_add_btn)
            anim_hbox.addWidget(self._anim_del_btn)
            anim_hbox.addStretch()
            anim_vbox.addWidget(self._anim_list)
            anim_vbox.addLayout(anim_hbox)
            layout.addWidget(anim_group)
            layout.addStretch()

            # Wire changes
            for widget in [self._speaker_ed, self._script_ed, self._script2_ed,
                           self._vo_ed, self._sound_ed, self._quest_ed,
                           self._comment_ed]:
                widget.textChanged.connect(self._on_field_change)
            self._text_ed.textChanged.connect(self._on_text_change)
            self._camera_style_ed.textChanged.connect(self._on_field_change)
            self._anim_add_btn.clicked.connect(self._on_anim_add)
            self._anim_del_btn.clicked.connect(self._on_anim_del)

        def load_node(self, node: Optional[DLGNodeData]):
            self._node = node
            if node is None:
                self._kind_lbl.setText("—")
                self._id_lbl.setText("—")
                for w in [self._speaker_ed, self._script_ed, self._script2_ed,
                          self._vo_ed, self._sound_ed, self._quest_ed,
                          self._comment_ed, self._camera_style_ed]:
                    w.setText("")
                self._text_ed.setPlainText("")
                self._anim_list.clear()
                return
            self._kind_lbl.setText("NPC Entry" if node.is_entry else "Player Reply")
            self._id_lbl.setText(str(node.node_id))
            self._speaker_ed.setText(node.speaker)
            self._script_ed.setText(node.script)
            self._script2_ed.setText(node.script2)
            self._vo_ed.setText(node.vo_resref)
            self._sound_ed.setText(node.sound)
            self._quest_ed.setText(node.quest)
            self._comment_ed.setText(node.comment)
            self._camera_style_ed.setText(str(node.camera_style))
            self._text_ed.setPlainText(node.text)
            # Populate anim list
            self._anim_list.clear()
            for anim in (node.anim_list or []):
                anim_id   = anim.get("id", 0)
                particip  = anim.get("participant", "")
                self._anim_list.addItem(f"ID {anim_id}  participant={particip!r}")

        def _on_field_change(self, _=None):
            if not self._node:
                return
            self._node.speaker   = self._speaker_ed.text()
            self._node.script    = self._script_ed.text()
            self._node.script2   = self._script2_ed.text()
            self._node.vo_resref = self._vo_ed.text()
            self._node.sound     = self._sound_ed.text()
            self._node.quest     = self._quest_ed.text()
            self._node.comment   = self._comment_ed.text()
            try:
                self._node.camera_style = int(self._camera_style_ed.text() or "0")
            except ValueError:
                pass
            self.node_changed.emit()

        def _on_anim_add(self):
            """Add a blank animation entry to the node's anim_list."""
            if not self._node:
                return
            new_anim = {"id": 0, "participant": ""}
            self._node.anim_list.append(new_anim)
            self._anim_list.addItem(f"ID 0  participant=''")
            self.node_changed.emit()

        def _on_anim_del(self):
            """Remove the selected animation entry."""
            if not self._node:
                return
            row = self._anim_list.currentRow()
            if row < 0 or row >= len(self._node.anim_list):
                return
            self._node.anim_list.pop(row)
            self._anim_list.takeItem(row)
            self.node_changed.emit()

        def _on_text_change(self):
            if self._node:
                self._node.text = self._text_ed.toPlainText()
                self.node_changed.emit()


    # ── Main DLG Editor Widget ────────────────────────────────────────────

    class DLGEditorPanel(QWidget):
        """Full DLG editor: canvas + properties panel + toolbar.

        Qt .ui migration (Phase 2):
          Attempts to load dlg_editor.ui via load_ui(). If successful,
          self._ui_loaded is True. Python layout always built as fallback.
        """

        graph_saved = Signal(bytes)   # Emitted with GFF bytes when Save is clicked

        def __init__(self, parent=None):
            super().__init__(parent)
            self._graph    = DLGGraphData()
            self._scene    = DLGCanvas(self._graph)
            self._modified = False
            self._ui_loaded = False

            # ── Phase 2: attempt to load Qt Designer layout ───────────────
            try:
                from .ui_loader import load_ui
                self._ui_loaded = load_ui("dlg_editor.ui", self)
            except Exception as _exc:
                log.debug("dlg_editor.ui not loaded (%s) — using Python layout", _exc)

            self._build_ui()
            self._wire()
            self._new_default()

        # ── UI ────────────────────────────────────────────────────────

        def _build_ui(self):
            vbox = QVBoxLayout(self)
            vbox.setContentsMargins(0, 0, 0, 0)

            # Toolbar
            tb = QToolBar("DLG", self)
            tb.setIconSize(QSize(16, 16))
            self._act_entry  = tb.addAction("＋ Entry",   self._add_entry)
            self._act_reply  = tb.addAction("＋ Reply",   self._add_reply)
            tb.addSeparator()
            self._act_link   = tb.addAction("⛓ Link",     self._link_selected)
            self._act_delete = tb.addAction("✕ Delete",   self._delete_selected)
            self._act_start  = tb.addAction("★ Starter",  self._set_starter)
            tb.addSeparator()
            self._act_zoomin  = tb.addAction("⊕ Zoom+",  self._zoom_in)
            self._act_zoomout = tb.addAction("⊖ Zoom−",  self._zoom_out)
            self._act_fit     = tb.addAction("⊡ Fit",    self._fit)
            tb.addSeparator()
            self._act_import = tb.addAction("📂 Load GFF", self._load_gff)
            self._act_export = tb.addAction("💾 Save",     self._save_json)
            vbox.addWidget(tb)

            # Splitter: canvas | properties
            splitter = QSplitter(Qt.Horizontal)
            self._view = QGraphicsView(self._scene)
            self._view.setRenderHint(QPainter.Antialiasing)
            self._view.setDragMode(QGraphicsView.RubberBandDrag)
            self._view.setBackgroundBrush(QBrush(QColor("#1a1a2e")))

            self._props = DLGPropertiesPanel()
            self._props.setMinimumWidth(220)
            self._props.setMaximumWidth(320)

            splitter.addWidget(self._view)
            splitter.addWidget(self._props)
            splitter.setStretchFactor(0, 3)
            splitter.setStretchFactor(1, 1)
            vbox.addWidget(splitter)

            # Status bar
            self._status = QLabel("Ready — 0 nodes")
            vbox.addWidget(self._status)

        def _wire(self):
            self._scene.node_selected.connect(self._props.load_node)
            self._scene.selectionChanged.connect(self._scene.selectionChanged_handler)
            self._scene.graph_modified.connect(self._on_modified)
            self._props.node_changed.connect(self._on_modified)

        def _new_default(self):
            """Populate with a minimal sample graph."""
            entry = self._graph.add_entry("Hello there, traveller.")
            reply1 = self._graph.add_reply("Who are you?")
            reply2 = self._graph.add_reply("Leave me alone.")
            self._graph.starters = [entry.node_id]
            self._graph.link(entry.node_id, reply1.node_id)
            self._graph.link(entry.node_id, reply2.node_id)
            self._scene._rebuild()
            self._update_status()

        # ── Toolbar actions ───────────────────────────────────────────

        def _add_entry(self):
            self._scene.add_entry()
            self._update_status()

        def _add_reply(self):
            self._scene.add_reply()
            self._update_status()

        def _link_selected(self):
            self._scene.link_selected()

        def _delete_selected(self):
            self._scene.delete_selected()
            self._update_status()

        def _set_starter(self):
            sel = [i for i in self._scene.selectedItems()
                   if isinstance(i, DLGNodeItem)]
            if sel:
                self._scene.set_starter(sel[0].node.node_id)

        def _zoom_in(self):
            self._view.scale(1.25, 1.25)

        def _zoom_out(self):
            self._view.scale(0.8, 0.8)

        def _fit(self):
            self._view.fitInView(self._scene.itemsBoundingRect(),
                                 Qt.KeepAspectRatio)

        def _load_gff(self):
            try:
                from qtpy.QtWidgets import QFileDialog
                path, _ = QFileDialog.getOpenFileName(
                    self, "Load DLG GFF", "", "DLG Files (*.dlg);;All Files (*)")
                if not path:
                    return
                with open(path, "rb") as f:
                    data = f.read()
                self.load_gff_bytes(data)
            except Exception as e:
                log.error("DLG load error: %s", e)

        def _save_json(self):
            try:
                from qtpy.QtWidgets import QFileDialog
                path, _ = QFileDialog.getSaveFileName(
                    self, "Save DLG JSON", "", "JSON Files (*.json);;All Files (*)")
                if not path:
                    return
                with open(path, "w") as f:
                    json.dump(self._graph.to_dict(), f, indent=2)
                self._modified = False
                self._update_status()
            except Exception as e:
                log.error("DLG save error: %s", e)

        # ── Public API ────────────────────────────────────────────────

        def load_gff_bytes(self, data: bytes):
            """Load a DLG from raw GFF bytes and rebuild the canvas."""
            self._graph = DLGGraphData.from_gff_bytes(data)
            self._scene.graph = self._graph
            self._scene._rebuild()
            self._fit()
            self._update_status()

        def load_json(self, json_str: str):
            """Load from a JSON string (output of to_dict)."""
            self._graph = DLGGraphData.from_dict(json.loads(json_str))
            self._scene.graph = self._graph
            self._scene._rebuild()
            self._update_status()

        def get_graph(self) -> DLGGraphData:
            return self._graph

        def _on_modified(self):
            self._modified = True
            self._update_status()

        def _update_status(self):
            n = len(self._graph.nodes)
            e = sum(1 for nd in self._graph.nodes.values() if nd.is_entry)
            r = n - e
            mod = " [modified]" if self._modified else ""
            self._status.setText(
                f"{e} entries · {r} replies · {len(self._graph.starters)} starters{mod}")

else:
    # Headless stubs ──────────────────────────────────────────────────────
    class DLGNodeItem:          # type: ignore[no-redef]
        pass
    class DLGEdgeItem:          # type: ignore[no-redef]
        pass
    class DLGCanvas:            # type: ignore[no-redef]
        pass
    class DLGPropertiesPanel:   # type: ignore[no-redef]
        pass
    class DLGEditorPanel:       # type: ignore[no-redef]
        """Headless stub — Qt is not available."""
        def __init__(self, *a, **kw):
            pass


# ═══════════════════════════════════════════════════════════════════════════
#  MCP tool descriptors  (registered in mcp/tools/__init__.py)
# ═══════════════════════════════════════════════════════════════════════════

def get_dlg_editor_tools():
    """Return MCP tool descriptors for DLG editing."""
    return [
        {
            "name": "kotor_dlg_parse",
            "description": (
                "Parse a KotOR DLG (dialogue) GFF file and return the complete conversation "
                "graph as a JSON object. The graph includes all Entry nodes (NPC lines), "
                "Reply nodes (player options), Link edges with conditional scripts, and "
                "top-level metadata (on_abort, on_end, skippable, conversation_type). "
                "Pass base64-encoded DLG bytes via dlg_b64, or a game+resref pair."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "dlg_b64":  {"type": "string",
                                 "description": "Base64-encoded DLG GFF bytes"},
                    "game":     {"type": "string", "description": "k1 or k2"},
                    "resref":   {"type": "string",
                                 "description": "DLG resref (without .dlg extension)"},
                },
            },
        },
        {
            "name": "kotor_dlg_add_node",
            "description": (
                "Add a new Entry or Reply node to a DLG graph. "
                "Pass the current graph JSON (from kotor_dlg_parse) and the node parameters. "
                "Returns the updated graph JSON."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["graph_json", "node_type"],
                "properties": {
                    "graph_json": {"type": "string",
                                  "description": "JSON string of the DLGGraphData"},
                    "node_type":  {"type": "string", "enum": ["entry", "reply"],
                                   "description": "entry = NPC line, reply = player option"},
                    "text":       {"type": "string", "description": "Dialog text"},
                    "speaker":    {"type": "string", "description": "Speaker tag"},
                    "script":     {"type": "string", "description": "Script ResRef"},
                },
            },
        },
        {
            "name": "kotor_dlg_link_nodes",
            "description": (
                "Connect two nodes in a DLG graph with a directed link. "
                "Optionally supply an Active conditional script."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["graph_json", "src_id", "tgt_id"],
                "properties": {
                    "graph_json": {"type": "string"},
                    "src_id":     {"type": "integer", "description": "Source node ID"},
                    "tgt_id":     {"type": "integer", "description": "Target node ID"},
                    "active":     {"type": "string",
                                   "description": "Conditional script ResRef (optional)"},
                },
            },
        },
        {
            "name": "kotor_dlg_summarize",
            "description": (
                "Summarize a DLG conversation tree: list all dialog lines in branching order, "
                "show speakers, scripts, and conditions. Useful for quickly reviewing "
                "a conversation without opening the visual editor."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "dlg_b64":    {"type": "string"},
                    "graph_json": {"type": "string"},
                    "max_lines":  {"type": "integer",
                                   "description": "Max dialog lines to return (default 100)"},
                },
            },
        },
        {
            "name": "kotor_dlg_write",
            "description": (
                "Serialise a DLG graph JSON (from kotor_dlg_parse / kotor_dlg_add_node) back "
                "to a KotOR-native DLG GFF binary. Returns the DLG binary as base64, ready to "
                "write into a module ERF or the override folder. Compatible with KotOR 1 and 2."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["graph_json"],
                "properties": {
                    "graph_json": {
                        "type": "string",
                        "description": "JSON string of the DLGGraphData (from kotor_dlg_parse)",
                    },
                },
            },
        },
    ]


# ═══════════════════════════════════════════════════════════════════════════
#  MCP handlers
# ═══════════════════════════════════════════════════════════════════════════

import json as _json


def _jc(obj):
    return {"content": [{"type": "text", "text": _json.dumps(obj, indent=2)}]}


async def handle_dlg_parse(arguments: Dict[str, Any]) -> Any:
    import base64
    try:
        if arguments.get("dlg_b64"):
            data  = base64.b64decode(arguments["dlg_b64"])
            graph = DLGGraphData.from_gff_bytes(data)
        else:
            return _jc({"error": "Provide dlg_b64 (resref loading coming soon)"})
        return _jc({"ok": True, "graph": graph.to_dict(),
                    "node_count": len(graph.nodes),
                    "entry_count": sum(1 for n in graph.nodes.values() if n.is_entry),
                    "reply_count": sum(1 for n in graph.nodes.values() if not n.is_entry)})
    except Exception as e:
        return _jc({"error": str(e)})


async def handle_dlg_add_node(arguments: Dict[str, Any]) -> Any:
    try:
        graph = DLGGraphData.from_dict(_json.loads(arguments["graph_json"]))
        ntype = arguments.get("node_type", "entry").lower()
        text  = arguments.get("text", "New line")
        node  = graph.add_entry(text) if ntype == "entry" else graph.add_reply(text)
        if arguments.get("speaker"):
            node.speaker = arguments["speaker"]
        if arguments.get("script"):
            node.script = arguments["script"]
        return _jc({"ok": True, "node_id": node.node_id, "graph": graph.to_dict()})
    except Exception as e:
        return _jc({"error": str(e)})


async def handle_dlg_link_nodes(arguments: Dict[str, Any]) -> Any:
    try:
        graph = DLGGraphData.from_dict(_json.loads(arguments["graph_json"]))
        graph.link(int(arguments["src_id"]), int(arguments["tgt_id"]),
                   arguments.get("active", ""))
        return _jc({"ok": True, "graph": graph.to_dict()})
    except Exception as e:
        return _jc({"error": str(e)})


async def handle_dlg_summarize(arguments: Dict[str, Any]) -> Any:
    import base64
    try:
        if arguments.get("dlg_b64"):
            graph = DLGGraphData.from_gff_bytes(base64.b64decode(arguments["dlg_b64"]))
        elif arguments.get("graph_json"):
            graph = DLGGraphData.from_dict(_json.loads(arguments["graph_json"]))
        else:
            return _jc({"error": "Provide dlg_b64 or graph_json"})

        max_lines = int(arguments.get("max_lines", 100))
        lines = []
        visited: set = set()

        def _walk(node_id: int, depth: int = 0):
            if node_id not in graph.nodes or node_id in visited:
                return
            visited.add(node_id)
            node = graph.nodes[node_id]
            prefix = "  " * depth
            kind   = "NPC" if node.is_entry else "PC "
            spk    = f"[{node.speaker}] " if node.speaker else ""
            lines.append(f"{prefix}{kind} {spk}#{node.node_id}: {node.text[:60]}")
            if node.script:
                lines.append(f"{prefix}    script: {node.script}")
            if len(lines) < max_lines:
                for tgt, active, _ in node.links:
                    cond = f" [if {active}]" if active else ""
                    lines.append(f"{prefix}  ↳{cond}")
                    _walk(tgt, depth + 2)

        for sid in (graph.starters or list(graph.nodes)[:1]):
            _walk(sid)

        return _jc({
            "node_count":  len(graph.nodes),
            "summary":     "\n".join(lines[:max_lines]),
            "truncated":   len(lines) > max_lines,
        })
    except Exception as e:
        return _jc({"error": str(e)})


async def handle_dlg_write(arguments: Dict[str, Any]) -> Any:
    """Serialise a DLGGraphData JSON back to a KotOR DLG GFF binary."""
    import base64
    try:
        graph = DLGGraphData.from_dict(_json.loads(arguments["graph_json"]))
        dlg_bytes = graph.to_gff_bytes()
        b64 = base64.b64encode(dlg_bytes).decode()
        return _jc({
            "ok":         True,
            "size":       len(dlg_bytes),
            "dlg_b64":    b64,
            "node_count": len(graph.nodes),
            "entry_count": sum(1 for n in graph.nodes.values() if n.is_entry),
            "reply_count": sum(1 for n in graph.nodes.values() if not n.is_entry),
        })
    except Exception as e:
        return _jc({"error": str(e)})
