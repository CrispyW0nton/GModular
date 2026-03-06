"""
GModular — NPC Instance (preview walk mode)
===========================================
Represents a static NPC placed in preview mode.
Reads position/heading from a GITCreature object and renders it as
either an MDL-backed mesh (if the game directory is set) or a coloured
capsule billboard (fallback when no model data is available).

No AI, pathfinding, or combat is implemented — NPCs stand idle.
"""

from __future__ import annotations
import logging
import math
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  NPC Instance
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class NPCInstance:
    """
    A single NPC standing at a fixed position in the preview scene.

    Attributes
    ----------
    resref        : Template resref (e.g. 'n_jediknight01').
    tag           : Tag from GIT entry.
    x, y, z       : World-space position (capsule base / feet).
    bearing       : Facing angle in degrees (0 = +Y, KotOR convention).
    capsule_radius: Visual/collision radius.
    capsule_height: Height of the NPC.
    model_loaded  : True when MDL mesh data has been loaded.
    mesh_data     : Optional parsed MeshData (from MDLParser).
    """
    resref:         str   = "unknown"
    tag:            str   = ""
    x:              float = 0.0
    y:              float = 0.0
    z:              float = 0.0
    bearing:        float = 0.0   # degrees

    # Capsule dimensions (used for rendering/collision when no MDL)
    capsule_radius: float = 0.35
    capsule_height: float = 1.8

    # Model (optional, loaded asynchronously)
    model_loaded:   bool  = False
    mesh_data:      Optional[object] = field(default=None, repr=False)

    # GL render handle (managed by viewport)
    _vao_handle: Optional[object] = field(default=None, repr=False)

    @classmethod
    def from_git_creature(cls, creature) -> 'NPCInstance':
        """Create an NPCInstance from a GITCreature data object.

        `creature.bearing` stores the raw GFF XOrientation float component
        (range -1.0 to 1.0, where XOrientation = -sin(yaw)).  We convert it
        to a yaw angle in degrees here so the rest of NPCInstance can use
        degrees throughout (matching PlayerController).

        Fix: previously called math.degrees() on a float that was already a
        direction component, not radians — this produced wrong angles.
        """
        try:
            pos = creature.position
            x, y, z = pos.x, pos.y, pos.z
        except Exception:
            x, y, z = 0.0, 0.0, 0.0
        # XOrientation is -sin(yaw_radians); clamp to [-1,1] before asin
        x_orient = float(getattr(creature, 'bearing', 0.0))
        x_orient = max(-1.0, min(1.0, x_orient))
        yaw_deg   = math.degrees(-math.asin(x_orient))   # recover yaw from XOrientation
        return cls(
            resref=getattr(creature, 'resref', 'unknown')[:16],
            tag=getattr(creature, 'tag', ''),
            x=x, y=y, z=z,
            bearing=yaw_deg,
        )

    def position_tuple(self) -> Tuple[float, float, float]:
        return (self.x, self.y, self.z)

    def eye_height(self) -> float:
        return self.z + self.capsule_height * 0.88

    def direction_vector(self) -> Tuple[float, float]:
        """Facing direction as (dx, dy) unit vector.

        `self.bearing` is in degrees (converted from XOrientation in
        `from_git_creature`), matching PlayerController convention.
        """
        rad = math.radians(self.bearing)
        return (-math.sin(rad), math.cos(rad))

    def load_model(self, mdl_path: str, mdx_path: str = '') -> bool:
        """
        Attempt to load MDL mesh data for this NPC.
        Returns True on success, False on failure.
        """
        try:
            from ..formats.mdl_parser import MDLParser
            self.mesh_data   = MDLParser.parse_files(mdl_path, mdx_path)
            self.model_loaded = True
            log.debug(f"NPC '{self.resref}': model loaded from {mdl_path}")
            return True
        except Exception as e:
            log.debug(f"NPC '{self.resref}': model load failed — {e}")
            return False


# ─────────────────────────────────────────────────────────────────────────────
#  NPC Registry
# ─────────────────────────────────────────────────────────────────────────────

class NPCRegistry:
    """
    Manages all NPCInstances in the preview scene.

    Populated from GIT creature data when play mode starts.
    The viewport queries the registry each frame to render NPCs.
    """

    def __init__(self):
        self._npcs: List[NPCInstance] = []

    def clear(self):
        self._npcs.clear()

    def populate_from_git(self, git_data) -> int:
        """
        Build NPCInstances from all creatures in a GITData object.
        Returns count of NPCs created.
        """
        self.clear()
        try:
            for creature in git_data.creatures:
                npc = NPCInstance.from_git_creature(creature)
                self._npcs.append(npc)
        except Exception as e:
            log.debug(f"NPCRegistry.populate_from_git error: {e}")
        log.info(f"NPCRegistry: {len(self._npcs)} NPCs loaded")
        return len(self._npcs)

    @property
    def npcs(self) -> List[NPCInstance]:
        return list(self._npcs)

    def __len__(self) -> int:
        return len(self._npcs)

    def try_load_models(self, game_dir: str) -> int:
        """
        Attempt to load MDL mesh data for all NPCs from the game directory.
        Looks for <game_dir>/models/<resref>.mdl (case-insensitive).
        Returns the number of models successfully loaded.
        """
        import os
        loaded = 0
        models_dir = os.path.join(game_dir, 'models')
        if not os.path.isdir(models_dir):
            log.debug(f"No models dir at '{models_dir}'")
            return 0
        for npc in self._npcs:
            resref = npc.resref.lower()
            mdl_path = os.path.join(models_dir, resref + '.mdl')
            if os.path.exists(mdl_path):
                if npc.load_model(mdl_path):
                    loaded += 1
        log.info(f"NPCRegistry: {loaded}/{len(self._npcs)} models loaded from {game_dir}")
        return loaded

    def capsule_summary(self) -> str:
        """Human-readable summary for UI display."""
        total   = len(self._npcs)
        modeled = sum(1 for n in self._npcs if n.model_loaded)
        return f"{total} NPCs ({modeled} with models, {total - modeled} capsule fallback)"
