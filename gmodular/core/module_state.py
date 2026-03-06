"""
GModular — Module State
Central data store for the currently open module.
Manages loading, autosave, undo/redo, and dirty tracking.
"""
from __future__ import annotations
import os
import json
import shutil
import logging
import threading
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Callable, Any

from ..formats.gff_types import GITData, AREData, IFOData, GITPlaceable, Vector3
from ..formats.gff_reader import load_git, load_are, load_ifo
from ..formats.gff_writer import save_git
from ..formats.archives import ResourceManager, get_resource_manager

log = logging.getLogger(__name__)

# ── Command Pattern (Undo/Redo) ───────────────────────────────────────────────

class Command:
    """Base class for undoable actions."""
    description: str = "Action"

    def execute(self): pass
    def undo(self): pass


class PlaceObjectCommand(Command):
    def __init__(self, git: GITData, obj: GITPlaceable):
        self.git = git
        self.obj = obj
        self.description = f"Place {obj.resref or 'object'}"

    def execute(self):
        self.git.placeables.append(self.obj)

    def undo(self):
        if self.obj in self.git.placeables:
            self.git.placeables.remove(self.obj)


class DeleteObjectCommand(Command):
    def __init__(self, git: GITData, obj: GITPlaceable):
        self.git = git
        self.obj = obj
        self._index: int = -1
        self.description = f"Delete {obj.resref or 'object'}"

    def execute(self):
        try:
            self._index = self.git.placeables.index(self.obj)
            self.git.placeables.remove(self.obj)
        except ValueError:
            pass

    def undo(self):
        if self._index >= 0:
            self.git.placeables.insert(self._index, self.obj)
        else:
            self.git.placeables.append(self.obj)


class MoveObjectCommand(Command):
    def __init__(self, obj: GITPlaceable, old_pos: Vector3, new_pos: Vector3):
        self.obj     = obj
        self.old_pos = Vector3(old_pos.x, old_pos.y, old_pos.z)
        self.new_pos = Vector3(new_pos.x, new_pos.y, new_pos.z)
        self.description = f"Move {obj.resref or 'object'}"

    def execute(self):
        self.obj.position = Vector3(self.new_pos.x, self.new_pos.y, self.new_pos.z)

    def undo(self):
        self.obj.position = Vector3(self.old_pos.x, self.old_pos.y, self.old_pos.z)


class RotateObjectCommand(Command):
    def __init__(self, obj: GITPlaceable, old_bearing: float, new_bearing: float):
        self.obj        = obj
        self.old_bearing = old_bearing
        self.new_bearing = new_bearing
        self.description = f"Rotate {obj.resref or 'object'}"

    def execute(self):
        self.obj.bearing = self.new_bearing

    def undo(self):
        self.obj.bearing = self.old_bearing


class ModifyPropertyCommand(Command):
    def __init__(self, obj: Any, attr: str, old_val: Any, new_val: Any):
        self.obj     = obj
        self.attr    = attr
        self.old_val = old_val
        self.new_val = new_val
        self.description = f"Edit {attr}"

    def execute(self):
        setattr(self.obj, self.attr, self.new_val)

    def undo(self):
        setattr(self.obj, self.attr, self.old_val)


# ── Module State ─────────────────────────────────────────────────────────────

@dataclass
class ModuleProject:
    """Metadata for a GModular project on disk."""
    name: str = "Untitled"
    game: str = "K1"               # "K1" or "K2"
    project_dir: str = ""
    module_resref: str = ""        # base name (e.g. "danm13")
    description: str = ""

    @property
    def project_file(self) -> str:
        return os.path.join(self.project_dir, "gmodular.json")

    @property
    def git_path(self) -> str:
        return os.path.join(self.project_dir, "modules", f"{self.module_resref}.git")

    @property
    def are_path(self) -> str:
        return os.path.join(self.project_dir, "modules", f"{self.module_resref}.are")

    @property
    def ifo_path(self) -> str:
        return os.path.join(self.project_dir, "modules", f"{self.module_resref}.ifo")

    @property
    def autosave_dir(self) -> str:
        return os.path.join(self.project_dir, ".gmodular", "autosave")

    def save_meta(self):
        data = {
            "name": self.name,
            "game": self.game,
            "module_resref": self.module_resref,
            "description": self.description,
        }
        os.makedirs(self.project_dir, exist_ok=True)
        with open(self.project_file, "w") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load_meta(cls, project_dir: str) -> "ModuleProject":
        proj_file = os.path.join(project_dir, "gmodular.json")
        p = cls()
        p.project_dir = project_dir
        if os.path.exists(proj_file):
            with open(proj_file) as f:
                data = json.load(f)
            p.name           = data.get("name", "Untitled")
            p.game           = data.get("game", "K1")
            p.module_resref  = data.get("module_resref", "")
            p.description    = data.get("description", "")
        return p

    @classmethod
    def create_new(cls, name: str, game: str, project_dir: str,
                   module_resref: str, description: str = "") -> "ModuleProject":
        p = cls(name=name, game=game, project_dir=project_dir,
                module_resref=module_resref, description=description)
        os.makedirs(os.path.join(project_dir, "modules"), exist_ok=True)
        os.makedirs(os.path.join(project_dir, ".gmodular", "autosave"), exist_ok=True)
        p.save_meta()
        return p


class ModuleState:
    """
    In-memory state for the currently open module.
    Contains: GITData, AREData, IFOData, undo stack, dirty flag.
    """

    UNDO_LIMIT = 100
    AUTOSAVE_INTERVAL_S = 120  # 2 minutes

    def __init__(self):
        self.project:  Optional[ModuleProject] = None
        self.git:      Optional[GITData]       = None
        self.are:      Optional[AREData]       = None
        self.ifo:      Optional[IFOData]       = None
        self._dirty:   bool = False
        self._undo_stack:  List[Command] = []
        self._redo_stack:  List[Command] = []
        self._autosave_timer: Optional[threading.Timer] = None
        self._change_callbacks: List[Callable] = []  # Notify viewport on changes
        self._selection_callbacks: List[Callable] = []

    # ── State queries ─────────────────────────────────────────────────────

    @property
    def is_open(self) -> bool:
        return self.git is not None

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    @property
    def module_name(self) -> str:
        if self.ifo:
            return self.ifo.mod_name
        if self.project:
            return self.project.name
        return "No module"

    # ── Observer pattern ──────────────────────────────────────────────────

    def on_change(self, callback: Callable):
        """Register a callback invoked whenever module data changes."""
        self._change_callbacks.append(callback)

    def _emit_change(self):
        for cb in self._change_callbacks:
            try:
                cb()
            except Exception as e:
                log.debug(f"Change callback error: {e}")

    # ── Load / Save ───────────────────────────────────────────────────────

    def load_from_project(self, project: ModuleProject):
        """Load all module files from a project directory."""
        self.project = project
        self.git = None
        self.are = None
        self.ifo = None

        if os.path.exists(project.git_path):
            try:
                self.git = load_git(project.git_path)
            except Exception as e:
                log.error(f"GIT load error: {e}")
                self.git = GITData()

        if os.path.exists(project.are_path):
            try:
                self.are = load_are(project.are_path)
            except Exception as e:
                log.error(f"ARE load error: {e}")
                self.are = AREData()
        else:
            self.are = AREData()

        if os.path.exists(project.ifo_path):
            try:
                self.ifo = load_ifo(project.ifo_path)
            except Exception as e:
                log.error(f"IFO load error: {e}")
                self.ifo = IFOData()
        else:
            self.ifo = IFOData()

        if self.git is None:
            self.git = GITData()

        self._dirty = False
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._start_autosave()
        self._emit_change()
        log.info(f"Module loaded: {project.module_resref} ({self.git.object_count} objects)")

    def load_from_files(self, git_path: str, are_path: str = "",
                        ifo_path: str = "", game: str = "K1"):
        """Load GIT (and optionally ARE/IFO) directly from file paths."""
        self.project = None
        try:
            self.git = load_git(git_path)
        except Exception as e:
            log.error(f"GIT load error: {e}")
            self.git = GITData()

        if are_path and os.path.exists(are_path):
            try:
                self.are = load_are(are_path)
            except Exception as e:
                log.error(f"ARE load error: {e}")
                self.are = AREData()
        else:
            self.are = AREData()

        if ifo_path and os.path.exists(ifo_path):
            try:
                self.ifo = load_ifo(ifo_path)
            except Exception as e:
                log.error(f"IFO load error: {e}")
                self.ifo = IFOData()
        else:
            self.ifo = IFOData()

        self._dirty = False
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._emit_change()
        log.info(f"Module loaded from files: {self.git.object_count} objects")

    def new_module(self, project: ModuleProject):
        """Create a brand-new empty module."""
        self.project = project
        self.git  = GITData()
        self.are  = AREData(tag=project.module_resref, name=project.name)
        self.ifo  = IFOData(mod_name=project.name, entry_area=project.module_resref)
        self._dirty = False
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._start_autosave()
        self._emit_change()

    def save(self, git_path: Optional[str] = None):
        """Save GIT to disk."""
        if self.git is None:
            log.warning("Nothing to save")
            return
        target = git_path or (self.project.git_path if self.project else None)
        if not target:
            raise ValueError("No save path specified")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        save_git(self.git, target)
        self._dirty = False
        log.info(f"Saved GIT: {target}")

    def autosave(self):
        """Write an autosave backup."""
        if self.git is None or not self.project:
            return
        os.makedirs(self.project.autosave_dir, exist_ok=True)
        backup = os.path.join(self.project.autosave_dir,
                              f"{self.project.module_resref}_autosave.git")
        try:
            save_git(self.git, backup)
            log.debug(f"Autosaved: {backup}")
        except Exception as e:
            log.warning(f"Autosave failed: {e}")

    def _start_autosave(self):
        self._stop_autosave()
        self._autosave_timer = threading.Timer(self.AUTOSAVE_INTERVAL_S, self._autosave_tick)
        self._autosave_timer.daemon = True
        self._autosave_timer.start()

    def _autosave_tick(self):
        if self._dirty:
            self.autosave()
        self._start_autosave()

    def _stop_autosave(self):
        if self._autosave_timer:
            self._autosave_timer.cancel()
            self._autosave_timer = None

    def close(self):
        self._stop_autosave()
        self.git  = None
        self.are  = None
        self.ifo  = None
        self.project = None
        self._dirty = False
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._emit_change()

    # ── Command Execution ────────────────────────────────────────────────

    def execute(self, cmd: Command):
        """Execute a command and push it to the undo stack."""
        cmd.execute()
        self._undo_stack.append(cmd)
        self._redo_stack.clear()
        if len(self._undo_stack) > self.UNDO_LIMIT:
            self._undo_stack.pop(0)
        self._dirty = True
        self._emit_change()

    def undo(self) -> Optional[str]:
        """Undo the last command. Returns description or None."""
        if not self._undo_stack:
            return None
        cmd = self._undo_stack.pop()
        cmd.undo()
        self._redo_stack.append(cmd)
        self._dirty = True
        self._emit_change()
        return cmd.description

    def redo(self) -> Optional[str]:
        """Redo the last undone command."""
        if not self._redo_stack:
            return None
        cmd = self._redo_stack.pop()
        cmd.execute()
        self._undo_stack.append(cmd)
        self._dirty = True
        self._emit_change()
        return cmd.description

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    @property
    def undo_description(self) -> str:
        return self._undo_stack[-1].description if self._undo_stack else ""

    @property
    def redo_description(self) -> str:
        return self._redo_stack[-1].description if self._redo_stack else ""

    # ── Validation ────────────────────────────────────────────────────────

    def validate(self) -> List[str]:
        """Check for common issues. Returns list of warning strings."""
        issues = []
        if self.git is None:
            return ["No GIT loaded"]

        # Duplicate tags
        tags: Dict[str, int] = {}
        for p in self.git.placeables:
            if p.tag:
                tags[p.tag] = tags.get(p.tag, 0) + 1
        for tag, count in tags.items():
            if count > 1:
                issues.append(f"Duplicate tag: '{tag}' ({count} objects)")

        # Empty ResRefs
        for i, p in enumerate(self.git.placeables):
            if not p.resref:
                issues.append(f"Placeable [{i}] '{p.tag}' has no ResRef")
            if len(p.resref) > 16:
                issues.append(f"Placeable '{p.tag}' ResRef too long (>16): {p.resref!r}")

        return issues


# Module state singleton
_module_state: Optional[ModuleState] = None

def get_module_state() -> ModuleState:
    global _module_state
    if _module_state is None:
        _module_state = ModuleState()
    return _module_state
