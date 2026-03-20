"""
GModular — AppController
========================
Application use-case coordinator.

This module contains **no Qt widget imports**.  It owns the business-logic
layer between the presentation (MainWindow) and the domain (ModuleState,
ResourceManager, formats).

Architecture note (ARCHITECTURE.md §4.5):
    gui/app_controller.py   ← use-case coordinator  (this file)
    gui/main_window.py      ← pure presentation; delegates to AppController

Responsibilities:
    • open_module(path)       — import + unpack a .mod / .git file
    • save_module(path=None)  — serialize and write the current module
    • set_game_dir(path)      — validate + set game directory
    • load_game_assets()      — populate palette from BIF/KEY archives
    • validate_module()       — run structural validation, return issues list
    • undo() / redo()         — command-stack wrappers

All public methods return plain Python values (bool / list / dict) so the
presentation layer can display results without coupling to domain details.

Signal protocol: AppController does **not** emit Qt signals itself.
    The MainWindow connects its own signals/slots and calls AppController
    methods, then uses the return value to update the UI.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, List, Tuple

log = logging.getLogger(__name__)

# ── Domain imports (no Qt) ────────────────────────────────────────────────────
from ..core.module_state import get_module_state
from ..utils.resource_manager import get_resource_manager


class AppController:
    """
    Use-case coordinator for GModular.

    Instantiated once by MainWindow.  MainWindow keeps a reference as
    ``self._app_ctrl`` and delegates business operations to it.

    Parameters
    ----------
    state : ModuleState, optional
        Shared module-state singleton.  If None, resolved via
        ``get_module_state()``.
    resource_manager : ResourceManager, optional
        Shared resource-manager singleton.  If None, resolved via
        ``get_resource_manager()``.
    """

    def __init__(self, state=None, resource_manager=None):
        self._state = state or get_module_state()
        self._rm    = resource_manager or get_resource_manager()
        self._game_dir: Optional[Path] = None

    # ──────────────────────────────────────────────────────────────────────────
    # Properties
    # ──────────────────────────────────────────────────────────────────────────

    @property
    def state(self):
        """The shared ModuleState singleton."""
        return self._state

    @property
    def resource_manager(self):
        """The shared ResourceManager singleton."""
        return self._rm

    @property
    def game_dir(self) -> Optional[Path]:
        """Currently configured KotOR game directory, or None."""
        return self._game_dir

    @game_dir.setter
    def game_dir(self, path: Optional[Path]):
        self._game_dir = path

    # ──────────────────────────────────────────────────────────────────────────
    # Module I/O
    # ──────────────────────────────────────────────────────────────────────────

    def open_module(self, path: str) -> Tuple[bool, str]:
        """
        Import a module archive (.mod / .erf / .rim) or open a bare .git file.

        Returns
        -------
        (success, message)
            success : bool
            message : human-readable result string suitable for status-bar/log.

        Side-effects
        ------------
        On success the shared ModuleState is populated and the ``module_loaded``
        signal is emitted by ModuleState if connected.
        """
        p = Path(path)
        if not p.exists():
            return False, f"File not found: {path}"

        ext = p.suffix.lower()
        try:
            if ext in (".mod", ".erf", ".rim"):
                summary = self._state.load_from_mod(str(p))
                name = summary.get("module_name", p.stem)
                obj_count = summary.get("object_count", 0)
                return True, (f"Opened module '{name}'  "
                              f"({obj_count} objects)")
            elif ext == ".git":
                self._state.load_git(str(p))
                return True, f"Opened GIT: {p.name}"
            else:
                return False, f"Unsupported file type: {ext}"
        except Exception as e:
            log.error(f"open_module({path}): {e}")
            return False, f"Open failed: {e}"

    def save_module(self, git_path: Optional[str] = None) -> Tuple[bool, str]:
        """
        Save the current module.

        Parameters
        ----------
        git_path : str, optional
            If provided, performs a Save As to this path.
            If None, saves to the existing project path.

        Returns
        -------
        (success, message)
        """
        if not self._state.is_open:
            return False, "No module open"

        try:
            if git_path:
                self._state.save(git_path=git_path)
                # Save IFO alongside the GIT
                ifo_path = git_path.replace(".git", ".ifo").replace(".GIT", ".ifo")
                try:
                    from ..formats.gff_writer import save_ifo
                    save_ifo(self._state.ifo, ifo_path)
                    log.debug(f"IFO saved to: {ifo_path}")
                except Exception as e:
                    log.warning(f"IFO save skipped: {e}")
                return True, f"Saved to: {git_path}"
            elif self._state.project:
                self._state.save()
                return True, f"Saved: {self._state.project.git_path}"
            else:
                return False, "save_as_needed"   # caller must show Save As dialog
        except Exception as e:
            log.error(f"save_module: {e}")
            return False, f"Save failed: {e}"

    # ──────────────────────────────────────────────────────────────────────────
    # Game directory
    # ──────────────────────────────────────────────────────────────────────────

    def set_game_dir(self, directory: str) -> Tuple[bool, str]:
        """
        Validate and configure the KotOR game installation directory.

        Parameters
        ----------
        directory : str
            Path to the KotOR root directory (must contain ``chitin.key``).

        Returns
        -------
        (success, message)
        """
        d = Path(directory)
        if not d.is_dir():
            return False, f"Not a directory: {directory}"
        key = d / "chitin.key"
        if not key.exists():
            return False, f"chitin.key not found in: {directory}"

        self._game_dir = d
        # Determine K1 vs K2 from executable presence
        tag = "K2" if (d / "swkotor2.exe").exists() else "K1"
        try:
            self._rm.set_game(str(d), tag)
        except Exception as e:
            log.warning(f"ResourceManager.set_game failed: {e}")

        return True, f"Game directory set: {directory} ({tag})"

    def load_game_assets(self) -> Tuple[bool, dict]:
        """
        Scan game archives and return resource lists for palette/browser.

        Returns
        -------
        (success, result_dict)
            result_dict keys: 'placeables', 'creatures', 'doors', 'rooms'
            Each value is a list of resref strings (may be empty on failure).
        """
        if not self._game_dir or not self._game_dir.exists():
            return False, {}

        result: dict = {
            "placeables": [],
            "creatures":  [],
            "doors":      [],
            "rooms":      [],
        }
        try:
            from ..formats.archives import EXT_TO_TYPE
            placeables = self._rm.list_resources(EXT_TO_TYPE.get("utp", 2043))
            creatures  = self._rm.list_resources(EXT_TO_TYPE.get("utc", 2030))
            doors      = self._rm.list_resources(EXT_TO_TYPE.get("utd", 2041))
            result["placeables"] = placeables or []
            result["creatures"]  = creatures  or []
            result["doors"]      = doors      or []
        except Exception as e:
            log.error(f"load_game_assets: {e}")
            return False, result

        # Room MDLs
        try:
            from ..formats.archives import EXT_TO_TYPE
            mdl_type = EXT_TO_TYPE.get("mdl", 2002)
            room_mdls = self._rm.list_resources(mdl_type) or []
            rooms = [r for r in room_mdls
                     if len(r) > 4
                     and not r.startswith("c_")
                     and not r.startswith("p_")
                     and not r.startswith("w_")
                     and not r.startswith("i_")]
            result["rooms"] = rooms[:400]
        except Exception:
            pass

        return True, result

    # ──────────────────────────────────────────────────────────────────────────
    # Validation
    # ──────────────────────────────────────────────────────────────────────────

    def validate_module(self) -> List[str]:
        """
        Run structural validation on the current module.

        Returns
        -------
        list of str
            Empty list on pass; one string per issue on failure.
        """
        try:
            return self._state.validate() or []
        except Exception as e:
            log.error(f"validate_module: {e}")
            return [f"Validation error: {e}"]

    # ──────────────────────────────────────────────────────────────────────────
    # Undo / Redo
    # ──────────────────────────────────────────────────────────────────────────

    def undo(self) -> Optional[str]:
        """
        Undo the last command.

        Returns the description string if undone, or None if the stack is empty.
        """
        try:
            return self._state.undo()
        except Exception as e:
            log.error(f"undo: {e}")
            return None

    def redo(self) -> Optional[str]:
        """
        Redo the last undone command.

        Returns the description string if redone, or None if nothing to redo.
        """
        try:
            return self._state.redo()
        except Exception as e:
            log.error(f"redo: {e}")
            return None

    # ──────────────────────────────────────────────────────────────────────────
    # Module status helpers
    # ──────────────────────────────────────────────────────────────────────────

    def is_open(self) -> bool:
        """True if a module is currently loaded."""
        return bool(getattr(self._state, 'is_open', False))

    def module_name(self) -> str:
        """Current module name, or empty string."""
        ifo = getattr(self._state, 'ifo', None)
        if ifo is None:
            return ""
        return getattr(ifo, 'mod_name', getattr(ifo, 'name', "")) or ""

    def object_count(self) -> int:
        """Total GIT object count for the current module."""
        git = getattr(self._state, 'git', None)
        if git is None:
            return 0
        try:
            return (len(getattr(git, 'placeables', [])) +
                    len(getattr(git, 'creatures',  [])) +
                    len(getattr(git, 'doors',      [])) +
                    len(getattr(git, 'waypoints',  [])) +
                    len(getattr(git, 'triggers',   [])) +
                    len(getattr(git, 'sounds',     [])) +
                    len(getattr(git, 'stores',     [])))
        except Exception:
            return 0
