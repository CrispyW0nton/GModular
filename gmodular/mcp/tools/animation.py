"""
MCP tools — animation control for KotOR entities.

Exposes two categories of tools:
  1. kotor_list_animations  — query available animation names for a model
  2. kotor_play_animation   — trigger an animation on an entity in the viewport
  3. kotor_stop_animation   — stop animations on an entity
  4. kotor_animation_state  — inspect the current animation state of an entity

These tools communicate with the live GModular ViewportWidget via the shared
ModuleState and EntityRegistry, and optionally forward to GhostRigger (:7001)
for round-trip animation preview in the rigging tool.

All tools gracefully return an error message when GModular is not running or
has no module loaded — they never raise exceptions to the MCP caller.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from gmodular.mcp._formatting import json_content

log = logging.getLogger(__name__)

# ── KOTOR_ANIMATIONS catalogue (from animation_system.py) ─────────────────────

_KOTOR_ANIM_NAMES: List[str] = [
    "PAUSE1", "PAUSE2", "PAUSE3", "PAUSE4",
    "WALK", "RUN",
    "DEADFRONT", "DEADBACK",
    "CONJURE1", "CONJURE2",
    "SPASM", "SLEEP",
    "PRONE",
    "KNEEL_TALK_START", "KNEEL_TALK_NORMAL", "KNEEL_TALK_END",
    "TALK_NORMAL", "TALK_PLEADING", "TALK_FORCEFUL", "TALK_LAUGHING",
    "TALK_SAD",
    "GREETING",
    "LISTEN",
    "MEDITATE",
    "WORSHIP",
    "LOOKAT",
    "SIT_IDLE", "SIT_CROSS_LEGS_IDLE",
    "GETUP",
    "DIVE",
    "DODGE_SIDE", "DODGE_DUCK",
    "ATTACK1", "ATTACK2", "ATTACK3",
    "COUPLEDEAD",
    "DOOR_OPEN1", "DOOR_OPEN2", "DOOR_OPEN3",
    "DOOR_CLOSE1", "DOOR_CLOSE2", "DOOR_CLOSE3",
    "SPASM2",
]

_ANIM_DESCRIPTIONS: Dict[str, str] = {
    "PAUSE1": "Idle breathing #1",
    "PAUSE2": "Idle breathing #2",
    "PAUSE3": "Idle breathing #3",
    "PAUSE4": "Idle breathing #4",
    "WALK":   "Walk cycle",
    "RUN":    "Run cycle",
    "DEADFRONT": "Death (fall forward)",
    "DEADBACK":  "Death (fall backward)",
    "ATTACK1": "Melee attack swing #1",
    "ATTACK2": "Melee attack swing #2",
    "ATTACK3": "Melee attack swing #3",
    "TALK_NORMAL":   "Conversation — neutral",
    "TALK_PLEADING": "Conversation — pleading",
    "TALK_FORCEFUL": "Conversation — forceful",
    "TALK_LAUGHING": "Conversation — laughing",
    "TALK_SAD":      "Conversation — sad",
    "DOOR_OPEN1":  "Door open sequence #1",
    "DOOR_OPEN2":  "Door open sequence #2",
    "DOOR_CLOSE1": "Door close sequence #1",
}


def get_tools() -> List[Dict[str, Any]]:
    return [
        {
            "name": "kotor_list_animations",
            "description": (
                "List all animation names available for a KotOR model.  "
                "Returns the standard KOTOR_ANIMATIONS catalogue and, when "
                "a model resref is provided, the actual animations parsed from "
                "the MDL file.  Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {
                        "type": "string",
                        "description": "k1 or k2 (default k1)",
                    },
                    "model_resref": {
                        "type": "string",
                        "description": (
                            "Model resref without extension (e.g. 'c_bastila', "
                            "'p_bench1').  Optional — omit to get the global catalogue."
                        ),
                    },
                },
                "required": [],
            },
        },
        {
            "name": "kotor_play_animation",
            "description": (
                "Trigger an animation on a named entity in the GModular viewport.  "
                "The entity is matched by tag or resref.  "
                "Also forwards the request to GhostRigger (:7001) if connected.  "
                "Returns success/failure with diagnostic details."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "entity_tag": {
                        "type": "string",
                        "description": "Tag of the entity to animate (e.g. 'Bastila').",
                    },
                    "entity_resref": {
                        "type": "string",
                        "description": (
                            "Resref of the entity template.  Used as fallback when "
                            "entity_tag is not found."
                        ),
                    },
                    "animation": {
                        "type": "string",
                        "description": (
                            "Animation name — use kotor_list_animations to discover "
                            "valid names.  Standard names: PAUSE1, WALK, RUN, ATTACK1, "
                            "TALK_NORMAL, DEADFRONT, DOOR_OPEN1, etc."
                        ),
                    },
                    "loop": {
                        "type": "boolean",
                        "description": "Loop the animation (default true for idle, false for one-shots).",
                        "default": True,
                    },
                    "speed": {
                        "type": "number",
                        "description": "Playback speed multiplier (default 1.0).  Range 0.1–4.0.",
                        "default": 1.0,
                    },
                },
                "required": ["animation"],
            },
        },
        {
            "name": "kotor_stop_animation",
            "description": (
                "Stop the current animation on a named entity in the GModular viewport.  "
                "The entity reverts to its default idle pose."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "entity_tag": {
                        "type": "string",
                        "description": "Tag of the entity to stop animating.",
                    },
                    "entity_resref": {
                        "type": "string",
                        "description": "Resref fallback.",
                    },
                },
                "required": [],
            },
        },
        {
            "name": "kotor_animation_state",
            "description": (
                "Inspect the current animation state of all animated entities in "
                "the GModular viewport.  Returns entity_id, tag, resref, current "
                "animation, loop flag, elapsed time, and available animation names."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "entity_tag": {
                        "type": "string",
                        "description": (
                            "If provided, returns state for this entity only.  "
                            "If omitted, returns state for all animated entities."
                        ),
                    },
                },
                "required": [],
            },
        },
        _ENTITY_INFO_TOOL,
    ]


# ── Handlers ──────────────────────────────────────────────────────────────────

async def handle_list_animations(arguments: Dict[str, Any]) -> Any:
    """List KotOR animation names — catalogue + optional MDL-specific list."""
    model_resref = arguments.get("model_resref", "").strip().lower()
    game_tag     = arguments.get("game", "k1").strip().lower()

    catalogue = [
        {"name": n, "description": _ANIM_DESCRIPTIONS.get(n, "KotOR animation")}
        for n in _KOTOR_ANIM_NAMES
    ]
    result: Dict[str, Any] = {
        "standard_catalogue": catalogue,
        "total": len(catalogue),
    }

    if model_resref:
        # Try to read from a live EntityRegistry or a cached MDL
        mdl_anims = _query_model_animations(model_resref, game_tag)
        result["model_resref"]   = model_resref
        result["model_animations"] = mdl_anims
        result["model_anim_count"] = len(mdl_anims)

    return json_content(result)


async def handle_play_animation(arguments: Dict[str, Any]) -> Any:
    """Play an animation on a viewport entity."""
    anim_name    = arguments.get("animation", "").strip()
    entity_tag   = arguments.get("entity_tag",   "").strip()
    entity_resref= arguments.get("entity_resref","").strip()
    loop         = bool(arguments.get("loop", True))
    speed        = float(arguments.get("speed", 1.0))

    if not anim_name:
        return json_content({"error": "animation name is required"})

    result: Dict[str, Any] = {
        "animation":    anim_name,
        "entity_tag":   entity_tag,
        "entity_resref": entity_resref,
        "loop":         loop,
        "speed":        speed,
        "viewport":     {"success": False, "detail": "EntityRegistry not available"},
        "ghostrigger":  {"success": False, "detail": "GhostRigger not connected"},
    }

    # ── Try to play via live EntityRegistry ────────────────────────────────
    try:
        from gmodular.core.module_state import get_module_state
        state = get_module_state()
        reg = getattr(state, '_entity_registry', None)
        if reg is None:
            result["viewport"]["detail"] = "No entity registry on module state"
        else:
            ent = _find_entity(reg, entity_tag, entity_resref)
            if ent is None:
                result["viewport"]["detail"] = (
                    f"Entity not found: tag='{entity_tag}' resref='{entity_resref}'"
                )
            else:
                player = getattr(ent, '_animation_player', None)
                if player is None:
                    ent.setup_animation_player()
                    player = getattr(ent, '_animation_player', None)
                if player is None:
                    result["viewport"]["detail"] = "Entity has no animation player (model not loaded?)"
                else:
                    player.set_speed(speed)
                    ok = player.play(anim_name.lower(), loop=loop)
                    result["viewport"] = {
                        "success": ok,
                        "entity_id":  ent.entity_id,
                        "entity_tag": getattr(ent, 'tag', ''),
                        "detail":     f"Played '{anim_name}'" if ok else f"Animation '{anim_name}' not found in model",
                        "available":  list(player.animation_names)[:20],
                    }
    except Exception as e:
        result["viewport"] = {"success": False, "detail": str(e)}

    # ── Try to forward to GhostRigger ────────────────────────────────────
    try:
        from gmodular.ipc.bridges import GhostRiggerBridge, GHOSTRIGGER_PORT
        import requests as _req
        model_name = entity_resref or entity_tag or anim_name
        r = _req.post(
            f"http://localhost:{GHOSTRIGGER_PORT}/api/animation/play",
            json={"model": model_name, "anim": anim_name,
                  "loop": loop, "speed": speed},
            timeout=1.0,
        )
        result["ghostrigger"] = {
            "success": r.status_code == 200,
            "status_code": r.status_code,
        }
    except Exception as e:
        result["ghostrigger"] = {"success": False, "detail": str(e)}

    return json_content(result)


async def handle_stop_animation(arguments: Dict[str, Any]) -> Any:
    """Stop animation on a viewport entity."""
    entity_tag    = arguments.get("entity_tag",    "").strip()
    entity_resref = arguments.get("entity_resref", "").strip()

    result: Dict[str, Any] = {
        "entity_tag":    entity_tag,
        "entity_resref": entity_resref,
        "viewport":      {"success": False, "detail": "EntityRegistry not available"},
    }

    try:
        from gmodular.core.module_state import get_module_state
        state = get_module_state()
        reg = getattr(state, '_entity_registry', None)
        if reg is not None:
            ent = _find_entity(reg, entity_tag, entity_resref)
            if ent is not None:
                player = getattr(ent, '_animation_player', None)
                if player:
                    player.stop()
                    result["viewport"] = {
                        "success": True,
                        "entity_id": ent.entity_id,
                        "detail": "Animation stopped",
                    }
                else:
                    result["viewport"]["detail"] = "Entity has no animation player"
            else:
                result["viewport"]["detail"] = (
                    f"Entity not found: tag='{entity_tag}' resref='{entity_resref}'"
                )
    except Exception as e:
        result["viewport"] = {"success": False, "detail": str(e)}

    return json_content(result)


async def handle_animation_state(arguments: Dict[str, Any]) -> Any:
    """Return current animation state of viewport entities."""
    filter_tag = arguments.get("entity_tag", "").strip().lower()
    states: List[Dict] = []

    try:
        from gmodular.core.module_state import get_module_state
        state = get_module_state()
        reg = getattr(state, '_entity_registry', None)
        if reg is None:
            return json_content({
                "error": "No entity registry — load a module first",
                "entities": [],
            })
        for ent in reg.entities:
            tag = getattr(ent, 'tag', '').lower()
            if filter_tag and tag != filter_tag:
                continue
            player = getattr(ent, '_animation_player', None)
            entry: Dict[str, Any] = {
                "entity_id":  ent.entity_id,
                "tag":        getattr(ent, 'tag', ''),
                "resref":     getattr(ent, 'resref', ''),
                "type":       ent.entity_type,
            }
            if player:
                entry["current_animation"] = player.current_animation_name
                entry["loop"]              = getattr(player, '_current_state', None) and getattr(player._current_state, 'loop', False)
                entry["elapsed"]           = round(getattr(getattr(player, '_current_state', None), 'elapsed', 0.0), 3)
                entry["speed"]             = getattr(player, '_speed', 1.0)
                entry["available"]         = list(player.animation_names)
                entry["has_player"]        = True
            else:
                entry["has_player"]        = False
            states.append(entry)
    except Exception as e:
        return json_content({"error": str(e), "entities": []})

    return json_content({
        "entity_count": len(states),
        "filter":       filter_tag or None,
        "entities":     states,
    })


# ── Private helpers ────────────────────────────────────────────────────────────

def _find_entity(registry, tag: str, resref: str):
    """Locate an entity by tag (primary) then resref (fallback)."""
    if tag:
        results = registry.get_by_tag(tag)
        if results:
            return results[0]
    if resref:
        results = registry.get_by_resref(resref)
        if results:
            return results[0]
    # If nothing specified, return first entity
    entities = registry.entities
    return entities[0] if entities else None


def _query_model_animations(model_resref: str, game_tag: str) -> List[str]:
    """Try to resolve animation names from the live model cache or MDL on disk."""
    try:
        from gmodular.core.module_state import get_module_state
        state = get_module_state()
        reg = getattr(state, '_entity_registry', None)
        if reg:
            for ent in reg.entities:
                if getattr(ent, 'resref', '').lower() == model_resref:
                    player = getattr(ent, '_animation_player', None)
                    if player:
                        return list(player.animation_names)
                    mesh = getattr(ent, 'mesh_data', None)
                    if mesh:
                        return [a.name for a in getattr(mesh, 'animations', [])]
    except Exception:
        pass
    return []


def _get_entity_registry():
    """Return the live EntityRegistry from module state, or None."""
    try:
        from gmodular.core.module_state import get_module_state
        state = get_module_state()
        return getattr(state, '_entity_registry', None)
    except Exception:
        return None


# ── kotor_entity_info ─────────────────────────────────────────────────────────

_ENTITY_INFO_TOOL = {
    "name": "kotor_entity_info",
    "description": (
        "Return runtime state for a live entity in the viewport registry: "
        "position, bearing, animation state, patrol route, HP, and faction. "
        "Useful for AI agents that need to inspect or reason about NPC/creature state."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "tag":    {"type": "string",  "description": "Entity tag (preferred)"},
            "resref": {"type": "string",  "description": "Blueprint resref"},
            "entity_id": {"type": "integer", "description": "Numeric entity ID"},
        },
    },
}


async def handle_entity_info(arguments: dict):
    """Return a JSON summary of a live entity's runtime state."""
    tag      = arguments.get("tag", "")
    resref   = arguments.get("resref", "")
    eid      = arguments.get("entity_id")

    registry = _get_entity_registry()
    if registry is None:
        return json_content({"error": "no module loaded", "entity": None})

    ent = None
    if eid is not None:
        ent = registry.get(int(eid))
    if ent is None and tag:
        results = registry.get_by_tag(tag)
        if results:
            ent = results[0]
    if ent is None and resref:
        results = registry.get_by_resref(resref)
        if results:
            ent = results[0]

    if ent is None:
        return json_content({"error": "entity not found", "entity": None})

    # Gather animation player state
    player     = getattr(ent, '_animation_player', None)
    anim_state = {}
    if player:
        cs = getattr(player, '_current_state', None)
        anim_state = {
            "current_animation": getattr(player, '_current_name', None),
            "elapsed": float(getattr(cs, 'elapsed', 0)) if cs else 0.0,
            "looping": bool(getattr(cs, 'loop', False)) if cs else False,
            "paused":  bool(getattr(player, '_paused', True)),
            "speed":   float(getattr(player, '_speed', 1.0)),
        }

    # Patrol state
    patrol = {
        "waypoints": getattr(ent, 'patrol_waypoints', []),
        "current_index": getattr(ent, '_patrol_idx', 0),
        "dwell_wait": getattr(ent, '_patrol_wait', 0.0),
    }

    info = {
        "entity_id":  ent.entity_id,
        "tag":        ent.tag,
        "resref":     ent.resref,
        "entity_type": ent.entity_type,
        "position":   list(ent.position),
        "bearing":    ent.bearing,
        "visible":    ent.visible,
        "hp":         getattr(ent, 'hp', None),
        "max_hp":     getattr(ent, 'max_hp', None),
        "faction":    getattr(ent, 'faction', None),
        "state":      getattr(getattr(ent, 'state', None), 'name', None),
        "animation":  anim_state,
        "patrol":     patrol,
    }
    return json_content({"entity": info})
