"""
GModular Engine — Preview / walk-simulation subsystem.

Provides:
  - AnimationPlayer   — MDL animation controller interpolation (KotOR.js-grade)
  - AnimationSet      — manages animation players for all scene entities
  - SceneGraph        — engine-quality scene graph with VIS culling
  - SceneRoom         — room entity with AABB + VIS links
  - SceneEntity       — GIT object (placeable, door, creature, etc.)
  - VisibilitySystem  — VIS file-based room culling
  - Entity3D          — base 3D entity class
  - Door3D            — door entity with open/close animation state machine
  - Placeable3D       — placeable entity
  - Creature3D        — creature/NPC entity with animation
  - EntityRegistry    — manages all scene entities
  - PlayModeController— full first/third-person play mode
  - PlaySession       — viewport-facing play mode session handle
  - PlayerController  — legacy capsule controller (kept for compatibility)
  - NPCInstance       — static NPC placement (legacy)
  - NPCRegistry       — NPC registry (legacy)
"""

# ── New engine modules (KotOR.js-grade) ──────────────────────────────────────
from .animation_system import (
    AnimationPlayer, AnimationSet, AnimationState,
    NodeTransform, get_default_idle_animation,
    sample_position, sample_orientation, sample_alpha,
    KOTOR_ANIMATIONS,
)

from .scene_manager import (
    SceneGraph, SceneRoom, SceneEntity, VisibilitySystem,
    RenderBucket, RenderItem, Frustum, AABB, SceneStats,
    ENTITY_ROOM, ENTITY_PLACEABLE, ENTITY_DOOR,
    ENTITY_CREATURE, ENTITY_WAYPOINT, ENTITY_TRIGGER,
    ENTITY_ENCOUNTER, ENTITY_SOUND, ENTITY_STORE, ENTITY_CAMERA,
)

from .entity_system import (
    Entity3D, Door3D, Placeable3D, Creature3D, Waypoint3D,
    EntityRegistry, DoorState, CreatureState, PlaceableState,
)

from .play_mode import (
    PlayModeController, PlaySession, PlayCamera,
    PlayerState, MovementInput, CameraMode,
)

# ── Legacy modules (kept for compatibility) ────────────────────────────────
try:
    from .player_controller import PlayerController
except ImportError:
    PlayerController = None  # type: ignore

try:
    from .npc_instance import NPCInstance, NPCRegistry
except ImportError:
    NPCInstance = None   # type: ignore
    NPCRegistry = None   # type: ignore


__all__ = [
    # Animation
    "AnimationPlayer", "AnimationSet", "AnimationState",
    "NodeTransform", "get_default_idle_animation",
    "sample_position", "sample_orientation", "sample_alpha",
    "KOTOR_ANIMATIONS",

    # Scene graph
    "SceneGraph", "SceneRoom", "SceneEntity", "VisibilitySystem",
    "RenderBucket", "RenderItem", "Frustum", "AABB", "SceneStats",
    "ENTITY_ROOM", "ENTITY_PLACEABLE", "ENTITY_DOOR",
    "ENTITY_CREATURE", "ENTITY_WAYPOINT", "ENTITY_TRIGGER",
    "ENTITY_ENCOUNTER", "ENTITY_SOUND", "ENTITY_STORE", "ENTITY_CAMERA",

    # Entities
    "Entity3D", "Door3D", "Placeable3D", "Creature3D", "Waypoint3D",
    "EntityRegistry", "DoorState", "CreatureState", "PlaceableState",

    # Play mode
    "PlayModeController", "PlaySession", "PlayCamera",
    "PlayerState", "MovementInput", "CameraMode",

    # Legacy
    "PlayerController",
    "NPCInstance", "NPCRegistry",
]
