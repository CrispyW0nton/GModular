"""GModular MCP — shared installation state and path resolution.

Design (DESIGN_PHILOSOPHY.md §4, Khononov §4.3):
  - ``KotorInstallation`` is a thin adapter over the canonical pykotor
    ``Installation`` object.  All format I/O goes through pykotor; GModular's
    own parsers are reserved for the GUI/engine layer.
  - If pykotor is not importable (rare), the class falls back to the
    gmodular.formats.archives.ResourceManager (original behaviour), so the
    MCP server still runs without pykotor installed.
  - Common coupling: ``_INSTALLATIONS`` dict is bounded — only this module
    owns it; all callers go through ``load_installation()``.

Coupling level: Common (level 4), bounded — acceptable for session state.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

log = logging.getLogger(__name__)

# ── pykotor availability ───────────────────────────────────────────────────

try:
    from pykotor.common.misc import Game as _PKGame
    from pykotor.extract.installation import Installation as _PKInstallation
    from pykotor.extract.installation import SearchLocation as _PKSearchLocation
    from pykotor.tools.path import find_kotor_paths_from_default as _pk_find_defaults
    _PYKOTOR = True
except ImportError:
    _PYKOTOR = False
    _PKGame = None  # type: ignore[assignment]
    _PKInstallation = None  # type: ignore[assignment]
    _PKSearchLocation = None  # type: ignore[assignment]
    _pk_find_defaults = None  # type: ignore[assignment]

# ── Game alias tables ──────────────────────────────────────────────────────

GAME_ALIASES: Dict[str, str] = {
    "k1":     "K1",
    "kotor1": "K1",
    "kotori": "K1",
    "swkotor":"K1",
    "k2":     "K2",
    "kotor2": "K2",
    "tsl":    "K2",
}

ENV_HINTS: Dict[str, Tuple[str, ...]] = {
    "K1": ("K1_PATH", "KOTOR_PATH", "KOTOR1_PATH"),
    "K2": ("K2_PATH", "TSL_PATH", "KOTOR2_PATH"),
}

# Game key → pykotor Game enum (used when pykotor is available)
_GAME_ENUM: Dict[str, Any] = {}
if _PYKOTOR:
    _GAME_ENUM = {"K1": _PKGame.K1, "K2": _PKGame.K2}

# Default discovery paths (Linux / Wine / common Steam locations)
_DEFAULT_PATHS: Dict[str, List[str]] = {
    "K1": [
        "~/.local/share/Steam/steamapps/common/swkotor",
        "/opt/kotor1",
        "/game/kotor1",
    ],
    "K2": [
        "~/.local/share/Steam/steamapps/common/Knights of the Old Republic II",
        "/opt/kotor2",
        "/game/kotor2",
    ],
}

# Cache: game_key -> KotorInstallation
_INSTALLATIONS: Dict[str, "KotorInstallation"] = {}


# ── Installation adapter ───────────────────────────────────────────────────

class KotorInstallation:
    """Adapter over pykotor.extract.installation.Installation.

    Provides the GModular MCP tools with a stable interface regardless of
    whether pykotor is available.  When pykotor IS available (the standard
    case), all format I/O is delegated to it; GModular's own parsers are
    never called from the MCP layer.

    Interface contract (what MCP tools can rely on):
        inst.game          → "K1" or "K2"
        inst.path          → pathlib.Path to installation root
        inst.is_valid()    → bool
        inst.pykotor_inst  → pykotor Installation | None
        inst.resource(resref, ext)  → bytes | None
        inst.resource_manager()     → fallback gmodular ResourceManager
        inst.tlk_path()    → Path | None
        inst.module_list() → list[str]
        inst.override_files() → list[Path]
        inst.summary()     → dict
        inst.index         → legacy index dict (for tools that need metadata)
    """

    def __init__(self, path: Path, game: str) -> None:
        self.path = path
        self.game = game
        self._pk: Optional[Any] = None   # pykotor Installation (lazy)
        self._rm: Optional[Any] = None   # fallback ResourceManager (lazy)
        self._index: Optional[Dict[str, Any]] = None

    # ── pykotor access (primary) ───────────────────────────────────────────

    @property
    def pykotor_inst(self) -> Optional[Any]:
        """Return a pykotor Installation for this game path, or None."""
        if self._pk is None and _PYKOTOR:
            game_enum = _GAME_ENUM.get(self.game)
            if game_enum is not None:
                try:
                    self._pk = _PKInstallation(str(self.path), game_enum)
                    log.debug("KotorInstallation: pykotor Installation built for %s @ %s", self.game, self.path)
                except Exception as exc:
                    log.warning("KotorInstallation: pykotor init failed (%s) — falling back", exc)
        return self._pk

    def resource(self, resref: str, ext: str) -> Optional[bytes]:
        """Return raw bytes for a resource, or None if not found.

        Resolution order (mirrors KotorMCP / pykotor canonical order):
          1. OVERRIDE
          2. MODULES capsules
          3. CHITIN (BIF/KEY)

        Delegates to pykotor.Installation.resource() when available;
        falls back to gmodular ResourceManager otherwise.
        """
        pk = self.pykotor_inst
        if pk is not None:
            try:
                from pykotor.resource.type import ResourceType
                # resolve ext to ResourceType
                rt = ResourceType.from_extension(ext.lower().lstrip("."))
                result = pk.resource(resref, rt)
                if result is not None:
                    return bytes(result.data)
            except Exception as exc:
                log.debug("pykotor resource() failed for %s.%s: %s", resref, ext, exc)

        # Fallback: gmodular ResourceManager
        rm = self.resource_manager()
        return rm.get_file(resref, ext)

    # ── fallback gmodular ResourceManager ─────────────────────────────────

    def resource_manager(self) -> Any:
        """Return gmodular ResourceManager (fallback when pykotor unavailable).

        MCP tools should prefer ``inst.resource(resref, ext)`` over this.
        This is kept for backwards compatibility and for tools that need
        capsule-level metadata (e.g. listResources).
        """
        if self._rm is None:
            from gmodular.formats.archives import ResourceManager
            rm = ResourceManager()
            rm.set_game(str(self.path), self.game)
            self._rm = rm
            log.debug("KotorInstallation: ResourceManager built for %s @ %s", self.game, self.path)
        return self._rm

    # ── legacy index ──────────────────────────────────────────────────────

    def _ensure_index(self) -> None:
        if self._index is not None:
            return
        from gmodular.mcp._indexer import build_index
        self._index = build_index(self.path)

    @property
    def index(self) -> Dict[str, Any]:
        """Legacy resource index — prefer inst.resource() for new code."""
        self._ensure_index()
        assert self._index is not None
        return self._index

    # ── helpers ───────────────────────────────────────────────────────────

    def is_valid(self) -> bool:
        return (self.path / "chitin.key").exists()

    def modules_dir(self) -> Path:
        return self.path / "modules"

    def override_dir(self) -> Path:
        return self.path / "override"

    def module_list(self) -> List[str]:
        d = self.modules_dir()
        if not d.is_dir():
            return []
        exts = {".rim", ".erf", ".mod"}
        return sorted(f.name for f in d.iterdir() if f.suffix.lower() in exts)

    def override_files(self) -> List[Path]:
        d = self.override_dir()
        if not d.is_dir():
            return []
        return sorted(f for f in d.iterdir() if f.is_file())

    def tlk_path(self) -> Optional[Path]:
        for name in ("dialog.tlk", "Dialog.tlk"):
            p = self.path / name
            if p.exists():
                return p
        return None

    def summary(self) -> Dict[str, Any]:
        modules = self.module_list()
        override = self.override_files()
        return {
            "game": self.game,
            "path": str(self.path),
            "valid": self.is_valid(),
            "module_count": len(modules),
            "override_count": len(override),
            "has_tlk": self.tlk_path() is not None,
            "pykotor_backend": self.pykotor_inst is not None,
        }


# ── Public helpers ─────────────────────────────────────────────────────────

def resolve_game(label: Optional[str]) -> Optional[str]:
    """Resolve alias ('k1', 'tsl', …) → canonical key ('K1'/'K2') or None."""
    if not label:
        return None
    return GAME_ALIASES.get(label.strip().lower())


def iter_candidate_paths(game: str, explicit: Optional[str]) -> Iterator[Path]:
    """Yield candidate installation paths in priority order."""
    seen: set = set()

    def _emit(p: Path) -> Iterator[Path]:
        k = str(p).lower()
        if k not in seen:
            seen.add(k)
            yield p

    if explicit:
        yield from _emit(Path(explicit).expanduser().resolve())

    for env_key in ENV_HINTS.get(game, ()):
        val = os.environ.get(env_key)
        if val:
            yield from _emit(Path(val).expanduser().resolve())

    # pykotor default-path discovery (platform-aware, includes Windows registry)
    if _PYKOTOR and _pk_find_defaults is not None:
        try:
            game_enum = _GAME_ENUM.get(game)
            if game_enum is not None:
                for p in _pk_find_defaults().get(game_enum, []):
                    yield from _emit(Path(str(p)))
        except Exception:
            pass

    for raw in _DEFAULT_PATHS.get(game, []):
        yield from _emit(Path(raw).expanduser())


def load_installation(game: str, explicit_path: Optional[str] = None) -> KotorInstallation:
    """Load (or return cached) KotorInstallation for the given game key."""
    cached = _INSTALLATIONS.get(game)
    if cached is not None:
        return cached

    for candidate in iter_candidate_paths(game, explicit_path):
        if candidate.is_dir():
            inst = KotorInstallation(candidate, game)
            _INSTALLATIONS[game] = inst
            log.info("KotorMCP: loaded %s installation at %s (pykotor=%s)",
                     game, candidate, inst.pykotor_inst is not None)
            return inst

    msg = (
        f"Unable to locate {game} installation. "
        f"Set {ENV_HINTS.get(game, ('K1_PATH',))[0]} or pass explicit path."
    )
    raise ValueError(msg)


def get_cached_installations() -> Dict[str, "KotorInstallation"]:
    return dict(_INSTALLATIONS)


def detect_installations() -> Dict[str, List[Dict[str, Any]]]:
    """Return candidate paths for K1 and K2 with exists / validity info."""
    result: Dict[str, List[Dict[str, Any]]] = {}
    for game in ("K1", "K2"):
        default_set = {
            str(Path(p).expanduser()).lower()
            for p in _DEFAULT_PATHS.get(game, [])
        }
        entries: List[Dict[str, Any]] = []
        for candidate in iter_candidate_paths(game, None):
            key = str(candidate).lower()
            entries.append({
                "path": str(candidate),
                "exists": candidate.is_dir(),
                "valid": (candidate / "chitin.key").exists(),
                "label": "default" if key in default_set else "env",
            })
        result[game] = entries
    return result
