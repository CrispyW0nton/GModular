"""
GModular — Module State
Central data store for the currently open module.
Manages loading, autosave, undo/redo, and dirty tracking.
"""
from __future__ import annotations
import math
import os
import json
import shutil
import logging
import threading
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Callable, Any

from ..formats.gff_types import (
    GITData, AREData, IFOData,
    GITPlaceable, GITCreature, GITDoor, GITTrigger,
    GITSoundObject, GITWaypoint, GITStoreObject,
    Vector3,
)
from ..formats.gff_reader import load_git, load_are, load_ifo
from ..formats.gff_writer import save_git, save_ifo
from ..formats.archives import ResourceManager, get_resource_manager

log = logging.getLogger(__name__)

# ── Command Pattern (Undo/Redo) ───────────────────────────────────────────────

class Command:
    """Base class for undoable actions."""
    description: str = "Action"

    def execute(self): pass
    def undo(self): pass


def _obj_type_label(obj) -> str:
    """Return a human-readable type label for any GIT object."""
    return type(obj).__name__.replace("GIT", "")


class PlaceObjectCommand(Command):
    """
    Generic placement command — works for all GIT object types
    (GITPlaceable, GITCreature, GITDoor, GITTrigger, GITWaypoint, etc.).
    Delegates add/remove to GITData.add_object / GITData.remove_object.
    """
    def __init__(self, git: GITData, obj):
        self.git = git
        self.obj = obj
        label = _obj_type_label(obj)
        self.description = f"Place {label} '{getattr(obj, 'resref', '') or 'object'}'"

    def execute(self):
        self.git.add_object(self.obj)

    def undo(self):
        self.git.remove_object(self.obj)


class DeleteObjectCommand(Command):
    """
    Generic delete command — works for all GIT object types.
    Records the list index so undo can restore insertion order.
    """
    def __init__(self, git: GITData, obj):
        self.git  = git
        self.obj  = obj
        self._list: Optional[List] = None   # which sub-list the obj lived in
        self._index: int = -1
        label = _obj_type_label(obj)
        self.description = f"Delete {label} '{getattr(obj, 'tag', '') or 'object'}'"

    def _target_list(self) -> Optional[List]:
        """Return the specific sub-list that holds self.obj."""
        for lst in (self.git.placeables, self.git.creatures, self.git.doors,
                    self.git.waypoints, self.git.triggers,
                    self.git.sounds, self.git.stores):
            if self.obj in lst:
                return lst
        return None

    def execute(self):
        lst = self._target_list()
        if lst is not None:
            self._list  = lst
            self._index = lst.index(self.obj)
            lst.remove(self.obj)

    def undo(self):
        if self._list is not None and self._index >= 0:
            self._list.insert(self._index, self.obj)
        elif self._list is not None:
            self._list.append(self.obj)


class MoveObjectCommand(Command):
    def __init__(self, obj, old_pos: Vector3, new_pos: Vector3):
        self.obj     = obj
        self.old_pos = Vector3(old_pos.x, old_pos.y, old_pos.z)
        self.new_pos = Vector3(new_pos.x, new_pos.y, new_pos.z)
        self.description = f"Move {obj.resref or 'object'}"

    def execute(self):
        self.obj.position = Vector3(self.new_pos.x, self.new_pos.y, self.new_pos.z)

    def undo(self):
        self.obj.position = Vector3(self.old_pos.x, self.old_pos.y, self.old_pos.z)


class RotateObjectCommand(Command):
    def __init__(self, obj, old_bearing: float, new_bearing: float):
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

    def load_from_mod(self, mod_path: str, extract_dir: Optional[str] = None) -> dict:
        """
        Load a KotOR .mod (ERF) archive as the active module.

        Extracts GIT / ARE / IFO (and optionally LYT / VIS) from the archive,
        writes them to *extract_dir* (a temp folder next to the .mod by default),
        then populates self.git / self.are / self.ifo.

        Returns a summary dict::

            {
                "mod_path":    str,
                "extract_dir": str,
                "resref":      str,           # guessed from IFO or filename
                "resources":   List[str],     # all resource keys in the archive
                "lyt_text":    str | None,    # raw .lyt content if found
                "vis_text":    str | None,    # raw .vis content if found
                "errors":      List[str],
            }
        """
        from ..formats.archives import ERFReader, EXT_TO_TYPE, RES_TYPE_MAP
        from ..formats.gff_reader import load_git, load_are, load_ifo, GFFReader
        import tempfile

        errors: list = []
        summary: dict = {
            "mod_path":    mod_path,
            "extract_dir": "",
            "resref":      "",
            "resources":   [],
            "lyt_text":    None,
            "vis_text":    None,
            "errors":      errors,
        }

        # ── 1. Open the archive ───────────────────────────────────────────
        erf = ERFReader(mod_path)
        count = erf.load()
        if count == 0:
            errors.append(f"No resources found in {mod_path}")
            log.error(f"MOD load: empty archive {mod_path}")

        summary["resources"] = sorted(erf.resources.keys())

        # ── 2. Choose / create extraction directory ───────────────────────
        if extract_dir is None:
            mod_stem = Path(mod_path).stem
            base_dir = Path(mod_path).parent
            extract_dir = str(base_dir / f"_{mod_stem}_extracted")
        os.makedirs(extract_dir, exist_ok=True)
        summary["extract_dir"] = extract_dir

        def _extract(resref: str, ext: str) -> Optional[bytes]:
            # Try exact key (resref already lowercased + stripped by ERFReader)
            key = f"{resref.lower().strip()}.{ext}"
            entry = erf.resources.get(key)
            if entry is None:
                # Fallback: search case-insensitively for any matching key
                for k, e in erf.resources.items():
                    if k.lower() == key.lower():
                        entry = e
                        break
            if entry is None:
                return None
            return erf.read_resource(entry)

        def _write(filename: str, data: bytes) -> str:
            p = os.path.join(extract_dir, filename)
            with open(p, "wb") as fh:
                fh.write(data)
            return p

        # ── 3. Identify the module resref ─────────────────────────────────
        # Prefer IFO entry_area; fall back to first .are resref; fall back to filename stem.
        resref = Path(mod_path).stem.lower()

        # ── Helper: find first entry by extension (type-ID agnostic fallback) ──
        def _find_by_ext(ext: str):
            """Return (key, entry) for first resource matching ext, or (None, None)."""
            for k, e in erf.resources.items():
                if k.lower().endswith(f".{ext}"):
                    return k, e
            return None, None

        # Find the real module resref from the first .are file
        are_resref = None
        _are_key, _are_entry = _find_by_ext("are")
        if _are_key:
            # Strip the .are suffix to get the resref
            are_resref = _are_key[:-4].strip()
            resref = are_resref
            log.debug(f"MOD: area resref detected as '{resref}'")

        summary["resref"] = resref

        # ── 4. Load GIT ───────────────────────────────────────────────────
        self.project = None
        git_data = _extract(resref, "git")
        if git_data is None:
            # Try any .git in the archive (type-ID agnostic)
            _git_key, _git_entry = _find_by_ext("git")
            if _git_entry:
                git_data = erf.read_resource(_git_entry)
                resref = _git_key[:-4].strip()
                summary["resref"] = resref
                log.debug(f"MOD: GIT found via fallback scan, resref='{resref}'")

        if git_data:
            git_path = _write(f"{resref}.git", git_data)
            try:
                self.git = load_git(git_path)
                log.info(f"MOD: GIT loaded — {self.git.object_count} objects")
            except Exception as e:
                errors.append(f"GIT parse error: {e}")
                log.error(f"MOD GIT parse error: {e}")
                self.git = GITData()
        else:
            errors.append("No .git resource found in archive")
            log.warning(f"MOD: No .git found; archive keys: {sorted(erf.resources.keys())[:10]}")
            self.git = GITData()

        # ── 5. Load ARE ───────────────────────────────────────────────────
        are_data = _extract(resref, "are")
        if are_data is None and _are_entry:
            are_data = erf.read_resource(_are_entry)
        if are_data:
            are_path = _write(f"{resref}.are", are_data)
            try:
                self.are = load_are(are_path)
            except Exception as e:
                errors.append(f"ARE parse error: {e}")
                self.are = AREData()
        else:
            self.are = AREData()

        # ── 6. Load IFO ───────────────────────────────────────────────────
        ifo_data = _extract("module", "ifo")
        if ifo_data is None:
            ifo_data = _extract(resref, "ifo")
        if ifo_data is None:
            _, _ifo_entry = _find_by_ext("ifo")
            if _ifo_entry:
                ifo_data = erf.read_resource(_ifo_entry)

        if ifo_data:
            ifo_path = _write("module.ifo", ifo_data)
            try:
                self.ifo = load_ifo(ifo_path)
                # Prefer IFO's entry_area as the resref
                if self.ifo and self.ifo.entry_area:
                    summary["resref"] = self.ifo.entry_area.lower().strip()
            except Exception as e:
                errors.append(f"IFO parse error: {e}")
                self.ifo = IFOData()
        else:
            self.ifo = IFOData()

        # ── 7. Extract LYT / VIS (for room display) ───────────────────────
        lyt_resref = summary["resref"]
        lyt_data = _extract(lyt_resref, "lyt")
        if lyt_data is None:
            # Try any .lyt
            for key in erf.resources:
                if key.endswith(".lyt"):
                    entry = erf.resources[key]
                    lyt_data = erf.read_resource(entry)
                    lyt_resref = key[:-4]
                    break

        if lyt_data:
            lyt_text = lyt_data.decode("utf-8", errors="replace")
            summary["lyt_text"] = lyt_text
            _write(f"{lyt_resref}.lyt", lyt_data)
            log.info(f"MOD: LYT extracted ({len(lyt_text)} chars)")

        vis_resref = lyt_resref
        vis_data = _extract(vis_resref, "vis")
        if vis_data:
            vis_text = vis_data.decode("utf-8", errors="replace")
            summary["vis_text"] = vis_text
            _write(f"{vis_resref}.vis", vis_data)

        # ── 8. Extract remaining resources to extract_dir ─────────────────
        for key, entry in erf.resources.items():
            ext = key.rsplit(".", 1)[-1] if "." in key else "bin"
            # Skip what we already wrote
            dest = os.path.join(extract_dir, key)
            if not os.path.exists(dest):
                try:
                    raw = erf.read_resource(entry)
                    if raw:
                        with open(dest, "wb") as fh:
                            fh.write(raw)
                except Exception as e:
                    log.debug(f"MOD extract {key}: {e}")

        # Store extraction dir as a light-weight "project" so Save As works
        # We create a minimal ModuleProject pointing at extract_dir
        try:
            project = ModuleProject(
                name=self.ifo.mod_name if (self.ifo and self.ifo.mod_name) else Path(mod_path).stem,
                game="K1",
                module_resref=summary["resref"],
                project_dir=extract_dir,
            )
            self.project = project
        except Exception as e:
            log.warning(f"Could not create project from MOD: {e}")

        self._dirty = False
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._emit_change()
        log.info(f"MOD loaded: {mod_path} → {self.git.object_count} objects, "
                 f"{len(summary['resources'])} resources")
        return summary

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
        """Save GIT (and IFO if project open) to disk."""
        if self.git is None:
            log.warning("Nothing to save")
            return
        target = git_path or (self.project.git_path if self.project else None)
        if not target:
            raise ValueError("No save path specified")
        target_dir = os.path.dirname(target)
        if target_dir:
            os.makedirs(target_dir, exist_ok=True)
        save_git(self.git, target)
        # Also save IFO when project is open and IFO has been loaded/edited
        if self.project and self.ifo:
            try:
                os.makedirs(os.path.dirname(self.project.ifo_path), exist_ok=True)
                save_ifo(self.ifo, self.project.ifo_path)
            except Exception as e:
                log.warning(f"IFO save failed: {e}")
        self._dirty = False
        log.info(f"Saved: {target}")

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
        # Stop and don't reschedule if module has been closed since timer was armed
        if self.git is None or self.project is None:
            return
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
        """Check for common issues across ALL object types. Returns list of warning strings."""
        issues = []
        if self.git is None:
            return ["No GIT loaded"]

        # ── Duplicate tags (across all object types, case-insensitive) ───────
        # Keys are lowercased for detection; we report the original casing.
        tags_lower: Dict[str, List[str]] = {}  # lower(tag) → list of "TYPE:OrigTag"
        for obj in self.git.all_objects():
            tag = getattr(obj, 'tag', '').strip()
            if tag:
                label = _obj_type_label(obj)
                tags_lower.setdefault(tag.lower(), []).append(f"{label}:{tag}")
        for _key, entries in tags_lower.items():
            if len(entries) > 1:
                orig_tags = ", ".join(e.split(":", 1)[1] for e in entries)
                labels    = ", ".join(e.split(":", 1)[0] for e in entries)
                issues.append(
                    f"Duplicate tag (case-insensitive) '{orig_tags}' used by "
                    f"{len(entries)} objects ({labels})"
                )

        # ── Per-type checks ────────────────────────────────────────────────
        def _check_list(lst, kind: str, max_resref: int = 16):
            for i, obj in enumerate(lst):
                resref = getattr(obj, 'resref', '')
                tag    = getattr(obj, 'tag', '')
                if not resref:
                    issues.append(f"{kind} [{i}] '{tag}' has no ResRef")
                elif len(resref) > max_resref:
                    issues.append(
                        f"{kind} [{i}] '{tag}' ResRef too long (>{max_resref}): {resref!r}"
                    )
                # Check for NaN / Inf in position
                pos = getattr(obj, 'position', None)
                if pos is not None:
                    for axis, val in (("X", pos.x), ("Y", pos.y), ("Z", pos.z)):
                        if not math.isfinite(val):
                            issues.append(
                                f"{kind} [{i}] '{tag}' has invalid position.{axis}: {val}"
                            )

        _check_list(self.git.placeables, "Placeable")
        _check_list(self.git.creatures,  "Creature")
        _check_list(self.git.doors,      "Door")
        _check_list(self.git.waypoints,  "Waypoint")
        _check_list(self.git.triggers,   "Trigger")
        _check_list(self.git.sounds,     "Sound")
        _check_list(self.git.stores,     "Store")

        # ── Trigger geometry check ─────────────────────────────────────────
        for i, trig in enumerate(self.git.triggers):
            if len(trig.geometry) < 3:
                issues.append(
                    f"Trigger [{i}] '{trig.tag}' has fewer than 3 geometry points "
                    f"({len(trig.geometry)})"
                )

        # ── IFO entry area ────────────────────────────────────────────────
        if self.ifo and not self.ifo.entry_area:
            issues.append("IFO: entry_area is empty — module has no starting area")

        return issues


# Module state singleton
_module_state: Optional[ModuleState] = None

def get_module_state() -> ModuleState:
    global _module_state
    if _module_state is None:
        _module_state = ModuleState()
    return _module_state
