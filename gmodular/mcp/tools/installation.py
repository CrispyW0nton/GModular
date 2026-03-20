"""MCP tools — installation management: detect, load, info."""
from __future__ import annotations

from typing import Any, Dict, List

from gmodular.mcp._formatting import json_content
from gmodular.mcp.state import detect_installations, load_installation, resolve_game


# ── Tool schema definitions ────────────────────────────────────────────────

def get_tools() -> List[Dict[str, Any]]:
    return [
        {
            "name": "detectInstallations",
            "description": (
                "Discover candidate K1/K2 installation paths from environment "
                "variables and platform defaults. Read-only."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "loadInstallation",
            "description": (
                "Activate a KotOR installation in memory for subsequent tool calls. "
                "Read-only; does not modify disk."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string", "description": "Game alias: k1, k2, or tsl"},
                    "path": {"type": "string", "description": "Optional absolute path override"},
                },
                "required": ["game"],
            },
        },
        {
            "name": "kotor_installation_info",
            "description": (
                "Return installation summary: path, game, valid, module count, "
                "override count, TLK presence. Loads the installation if not cached."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string", "description": "Game alias: k1, k2, or tsl"},
                    "path": {"type": "string", "description": "Optional absolute path override"},
                },
                "required": ["game"],
            },
        },
    ]


# ── Handlers ───────────────────────────────────────────────────────────────

async def handle_detect_installations(_arguments: Dict[str, Any]) -> Any:
    return json_content(detect_installations())


async def handle_load_installation(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game parameter (k1/k2/tsl).")
    inst = load_installation(game_key, arguments.get("path"))
    return json_content({"game": game_key, "path": str(inst.path)})


async def handle_installation_info(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game parameter (k1/k2/tsl).")
    inst = load_installation(game_key, arguments.get("path"))
    return json_content(inst.summary())
