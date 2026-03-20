"""MCP tools — walkmesh/BWM analysis: validation diagram, surface stats."""
from __future__ import annotations

from typing import Any, Dict, List

from gmodular.mcp._formatting import json_content
from gmodular.mcp.state import load_installation, resolve_game


def get_tools() -> List[Dict[str, Any]]:
    return [
        {
            "name": "kotor_walkmesh_validation_diagram",
            "description": (
                "Return a text validation diagram for a walkmesh (BWM/WOK): "
                "perimeter, transitions, outer boundary. "
                "Use when you need to understand an area's walkable layout, "
                "door links, or boundary for modding or debugging. "
                "Read-only; returns plain text (no ANSI by default)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string", "description": "k1 or k2"},
                    "resref": {"type": "string", "description": "Walkmesh resref (e.g. 203tell for 203tell.wok)"},
                    "use_color": {
                        "type": "boolean",
                        "default": False,
                        "description": "Include ANSI color codes (for terminal); default false for plain text.",
                    },
                },
                "required": ["game", "resref"],
            },
        },
        {
            "name": "kotor_walkmesh_info",
            "description": (
                "Return walkmesh (WOK/BWM) summary: vertex count, face count, "
                "surface material breakdown. "
                "Use when you need counts and material stats rather than a visual diagram. "
                "Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string", "description": "k1 or k2"},
                    "resref": {"type": "string", "description": "Walkmesh resref (e.g. 203tell)"},
                    "restype": {
                        "type": "string",
                        "description": "wok | pwk | dwk",
                        "default": "wok",
                    },
                },
                "required": ["game", "resref"],
            },
        },
        {
            "name": "kotor_mdl_info",
            "description": (
                "Return MDL model summary: name, node tree, mesh nodes, "
                "texture references, animation names. "
                "Use when you need to inspect a model's structure or animation list. "
                "Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string"},
                    "resref": {"type": "string", "description": "MDL resref (e.g. p_bastilah)"},
                },
                "required": ["game", "resref"],
            },
        },
    ]


# ── Handlers ───────────────────────────────────────────────────────────────

async def handle_walkmesh_validation_diagram(arguments: Dict[str, Any]) -> Any:
    """Load BWM/WOK and return a plain-text validation diagram via pykotor."""
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game (k1/k2).")
    inst = load_installation(game_key)

    resref = (arguments.get("resref") or "").strip().lower()
    resref = resref.removesuffix(".wok").removesuffix(".pwk").removesuffix(".dwk")
    if not resref:
        raise ValueError("resref is required.")

    use_color: bool = bool(arguments.get("use_color", False))

    from gmodular.mcp.tools.discovery import find_resource_bytes

    # Try WOK first, then PWK/DWK
    data: bytes | None = None
    chosen_ext = "wok"
    for ext in ("wok", "pwk", "dwk"):
        try:
            data = find_resource_bytes(inst, resref, ext)
            chosen_ext = ext
            break
        except ValueError:
            continue
    if data is None:
        raise ValueError(f"Walkmesh {resref}.wok/.pwk/.dwk not found in {inst.game} installation.")

    # Prefer pykotor BWM reader + render_bwm_validation_diagram_lines
    try:
        from io import BytesIO
        from pykotor.resource.formats.bwm import read_bwm
        from pykotor.tools.walkmesh_render_diagram import render_bwm_validation_diagram_lines

        bwm = read_bwm(BytesIO(data))
        lines = render_bwm_validation_diagram_lines(bwm, use_color=use_color)
        diagram_text = "\n".join(lines)
        return {"content": [{"type": "text", "text": diagram_text}]}
    except Exception as exc:
        # Fallback: surface stats only (gmodular parser)
        from gmodular.formats.wok_parser import WOKParser, SURF_NAMES, is_walkable
        wok = WOKParser.from_bytes(data, resref)
        if wok is None:
            raise ValueError(f"Failed to parse {resref}.{chosen_ext}: {exc}") from exc
        faces = wok.faces
        verts: set = set()
        for face in faces:
            verts.update([face.v0, face.v1, face.v2])
        surf_counts: Dict[str, int] = {}
        for face in faces:
            name = SURF_NAMES.get(face.material, f"SURF_{face.material}")
            surf_counts[name] = surf_counts.get(name, 0) + 1
        summary = (
            f"# Walkmesh {resref}.{chosen_ext} (diagram unavailable — pykotor BWM render failed)\n\n"
            f"Vertices: {len(verts)}\nFaces: {len(faces)}\n\n"
            + "\n".join(f"  {k}: {v}" for k, v in sorted(surf_counts.items()))
        )
        return {"content": [{"type": "text", "text": summary}]}


async def handle_walkmesh_info(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game (k1/k2).")
    inst = load_installation(game_key)

    resref = (arguments.get("resref") or "").lower().removesuffix(".wok").removesuffix(".pwk").removesuffix(".dwk")
    restype = (arguments.get("restype") or "wok").lower().lstrip(".")

    from gmodular.mcp.tools.discovery import find_resource_bytes
    data = find_resource_bytes(inst, resref, restype)

    from gmodular.formats.wok_parser import WOKParser
    wok = WOKParser.from_bytes(data, resref)
    if wok is None:
        raise ValueError(f"Failed to parse {resref}.{restype}.")

    faces = wok.faces
    # Count unique vertices from face corners
    verts: set = set()
    for face in faces:
        verts.update([face.v0, face.v1, face.v2])

    # Surface material breakdown
    from gmodular.formats.wok_parser import SURF_NAMES, is_walkable
    surf_counts: Dict[str, int] = {}
    walkable_count = 0
    non_walkable_count = 0
    for face in faces:
        mat = face.material
        name = SURF_NAMES.get(mat, f"SURF_{mat}")
        surf_counts[name] = surf_counts.get(name, 0) + 1
        if is_walkable(mat):
            walkable_count += 1
        else:
            non_walkable_count += 1

    return json_content({
        "resref": resref,
        "restype": restype,
        "vertex_count": len(verts),
        "face_count": len(faces),
        "walkable_faces": walkable_count,
        "non_walkable_faces": non_walkable_count,
        "surface_materials": surf_counts,
    })


async def handle_mdl_info(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game (k1/k2).")
    inst = load_installation(game_key)

    resref = (arguments.get("resref") or "").lower()

    from gmodular.mcp.tools.discovery import find_resource_bytes
    mdl_data = find_resource_bytes(inst, resref, "mdl")
    try:
        mdx_data = find_resource_bytes(inst, resref, "mdx")
    except ValueError:
        mdx_data = b""

    from gmodular.formats.mdl_parser import MDLParser
    mesh_data = MDLParser(mdl_data, mdx_data).parse()
    if mesh_data is None:
        raise ValueError(f"Failed to parse {resref}.mdl.")

    all_nodes = mesh_data.all_nodes()
    mesh_nodes = mesh_data.mesh_nodes()
    textures = mesh_data.scan_textures()
    anim_names = [a.name for a in mesh_data.animations]

    # Build simplified node tree
    def _node_summary(node: Any) -> Dict[str, Any]:
        children = getattr(node, "children", None) or []
        return {
            "name": node.name,
            "flags": node.flags,
            "is_mesh": node.is_mesh,
            "is_skin": node.is_skin,
            "texture": getattr(node, "texture", None),
            "children": len(children),
        }

    return json_content({
        "model_name": mesh_data.name,
        "game_version": mesh_data.game_version,
        "node_count": len(all_nodes),
        "mesh_node_count": len(mesh_nodes),
        "animation_count": len(anim_names),
        "animations": anim_names[:30],
        "texture_refs": textures[:40],
        "root_node": _node_summary(mesh_data.root_node) if mesh_data.root_node else None,
    })
