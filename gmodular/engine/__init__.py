"""
GModular Engine — Preview / walk-simulation subsystem.

Provides:
  - PlayerController  — capsule character controller on walkmesh
  - NPCInstance       — static NPC placement and billboard rendering
  - PlaySession       — coordinate play mode, holds state between frames
"""
from .player_controller import PlayerController, PlaySession
from .npc_instance import NPCInstance, NPCRegistry

__all__ = [
    "PlayerController", "PlaySession",
    "NPCInstance", "NPCRegistry",
]
