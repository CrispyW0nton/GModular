"""
GModular MCP — Format Tools
============================
Exposes the new KotOR format library (SSF, LIP, TXI, VIS, PTH, 2DA write,
TLK write, NCS disasm) as MCP tools for AI agents.

All tools are read-only or produce JSON-safe output.  Write operations produce
base64-encoded binary blobs that the agent can save via the filesystem.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any, Dict, List

log = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────

def _json_content(obj: Any) -> Dict[str, Any]:
    text = json.dumps(obj, indent=2, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}]}


def _b64_content(label: str, data: bytes, extra: dict | None = None) -> Dict[str, Any]:
    payload = {label: base64.b64encode(data).decode(), "size_bytes": len(data)}
    if extra:
        payload.update(extra)
    return _json_content(payload)


def _get_resource_bytes(resref: str, ext: str) -> bytes | None:
    """Try to load raw bytes for a resource from the live ResourceManager."""
    try:
        from gmodular.formats.archives import ResourceManager
        rm = ResourceManager.instance()
        return rm.get_file(resref, ext)
    except Exception:
        return None


# ── Tool schemas ────────────────────────────────────────────────────────────

def get_tools() -> List[Dict[str, Any]]:
    return [
        {
            "name": "kotor_read_ssf",
            "description": (
                "Parse a KotOR Sound Set File (.ssf) and return the 28 creature sound "
                "StrRef assignments as a JSON object.  Provide either raw base64 data "
                "or a resource resref (SSF resref without extension)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "resref": {"type": "string", "description": "SSF resref (e.g. 'c_bastila')"},
                    "data_b64": {"type": "string", "description": "Base64-encoded SSF binary"},
                },
            },
        },
        {
            "name": "kotor_read_lip",
            "description": (
                "Parse a KotOR Lip-Sync File (.lip) and return the keyframe list "
                "(time + viseme shape).  Provide resref or base64 data."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "resref":   {"type": "string"},
                    "data_b64": {"type": "string"},
                },
            },
        },
        {
            "name": "kotor_read_txi",
            "description": (
                "Parse a KotOR texture extended-info file (.txi) and return its "
                "key-value pairs.  Useful for identifying animated, procedural, or "
                "environment-mapped textures."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "resref":   {"type": "string", "description": "Texture resref (no extension)"},
                    "data_b64": {"type": "string", "description": "Base64 TXI bytes"},
                },
            },
        },
        {
            "name": "kotor_read_vis",
            "description": (
                "Parse a KotOR .vis visibility file and return the room-to-room "
                "visibility graph as a JSON adjacency list."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "resref":   {"type": "string"},
                    "data_b64": {"type": "string"},
                },
            },
        },
        {
            "name": "kotor_read_ncs",
            "description": (
                "Disassemble a KotOR NCS compiled NWScript file and return a human-"
                "readable instruction listing.  For full decompilation use the Ghidra "
                "AgentDecompile tools."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "resref":   {"type": "string", "description": "Script resref (without .ncs)"},
                    "data_b64": {"type": "string"},
                },
            },
        },
        {
            "name": "kotor_write_ssf",
            "description": (
                "Build a KotOR SSF binary from a JSON mapping of sound-event names "
                "to StrRef integers.  Returns base64-encoded .ssf bytes."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "sounds": {
                        "type": "object",
                        "description": (
                            "Map of SSFSound name → StrRef integer.  "
                            "Valid keys: BATTLE_CRY_1 … POISONED.  "
                            "Omitted keys default to -1 (no sound)."
                        ),
                    },
                },
                "required": ["sounds"],
            },
        },
        {
            "name": "kotor_write_2da_csv",
            "description": (
                "Convert a 2DA table (provided as JSON rows) to KotOR ASCII 2DA text. "
                "Returns the ASCII content as a string (ready to write to a .2da file)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Ordered list of column names",
                    },
                    "rows": {
                        "type": "array",
                        "description": "List of row objects (column → value)",
                    },
                },
                "required": ["columns", "rows"],
            },
        },
        {
            "name": "kotor_write_tlk_patch",
            "description": (
                "Produce a minimal TLK file containing only the provided StrRef entries. "
                "Useful for building dialog.tlk patch fragments.  Returns base64 .tlk bytes."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "entries": {
                        "type": "array",
                        "description": (
                            "List of objects with keys: strref (int), text (str), "
                            "sound_resref (str, optional), sound_length (float, optional)."
                        ),
                    },
                    "language_id": {
                        "type": "integer",
                        "description": "Language ID (0=English, default 0)",
                    },
                },
                "required": ["entries"],
            },
        },
        {
            "name": "kotor_describe_ssf",
            "description": (
                "High-level description of a creature's sound set — which sounds are "
                "assigned and which TLK entries they reference.  Combines SSF + TLK lookup."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "resref": {"type": "string", "description": "SSF resref (e.g. 'c_bastila')"},
                },
                "required": ["resref"],
            },
        },
        {
            "name": "kotor_read_ltr",
            "description": (
                "Parse a KotOR Letter (.ltr) file containing Markov chain probability "
                "tables for random name generation.  Returns letter count, and the "
                "first-letter start/middle/end probability triplets."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "resref":   {"type": "string", "description": "LTR resref (without extension)"},
                    "data_b64": {"type": "string", "description": "Base64-encoded LTR binary"},
                },
            },
        },
        {
            "name": "kotor_write_ltr",
            "description": (
                "Build a KotOR .ltr binary from flat probability arrays.  "
                "Returns base64-encoded .ltr bytes."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "letter_count": {
                        "type": "integer",
                        "description": "Number of letters (26 or 28, default 28)",
                    },
                    "single": {
                        "type": "array",
                        "description": "Flat float list: letter_count × 3 start/mid/end probs",
                        "items": {"type": "number"},
                    },
                    "double": {
                        "type": "array",
                        "description": "Flat float list: letter_count² × 3",
                        "items": {"type": "number"},
                    },
                    "triple": {
                        "type": "array",
                        "description": "Flat float list: letter_count³ × 3",
                        "items": {"type": "number"},
                    },
                },
            },
        },
        {
            "name": "kotor_write_ncs",
            "description": (
                "Re-assemble a KotOR NCS binary from a flat instruction list.  "
                "Each instruction is {opcode, subtype, operands_b64}.  "
                "Useful for patching disassembled scripts.  Returns base64 .ncs bytes."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "instructions": {
                        "type": "array",
                        "description": "List of {opcode (int), subtype (int), operands_b64 (str, optional)}",
                    },
                },
                "required": ["instructions"],
            },
        },
        {
            "name": "kotor_read_lyt",
            "description": (
                "Parse a KotOR .lyt room-layout file and return structured JSON with "
                "all rooms (resref, x, y, z), door hooks, tracks, and obstacles. "
                "Pass data_b64 (base64 .lyt bytes) or resref (looked up from ResourceManager)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "data_b64": {"type": "string", "description": "Base64-encoded .lyt file bytes."},
                    "resref": {"type": "string", "description": "ResRef (without extension) to look up."},
                },
            },
        },
        {
            "name": "kotor_write_lyt",
            "description": (
                "Serialise a KotOR room layout back to a .lyt text file. "
                "Accepts 'rooms' (list of {resref, x, y, z} objects), optional 'door_hooks', "
                "'tracks', and 'obstacles'. Returns the .lyt text content and a base64-encoded "
                "bytes version. Produces canonical BioWare CRLF-terminated format."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["rooms"],
                "properties": {
                    "rooms": {
                        "type": "array",
                        "description": "List of room objects with 'resref', 'x', 'y', 'z'.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "resref": {"type": "string"},
                                "x": {"type": "number"},
                                "y": {"type": "number"},
                                "z": {"type": "number"},
                            },
                        },
                    },
                    "door_hooks": {
                        "type": "array",
                        "description": "Optional door hook objects with 'name', 'room', 'x','y','z', and quaternion 'qx','qy','qz','qw'.",
                        "items": {"type": "object"},
                    },
                    "tracks": {
                        "type": "array",
                        "description": "Optional track model resrefs (strings).",
                        "items": {"type": "string"},
                    },
                    "obstacles": {
                        "type": "array",
                        "description": "Optional obstacle model resrefs (strings).",
                        "items": {"type": "string"},
                    },
                },
            },
        },
        {
            "name": "kotor_read_bwm",
            "description": (
                "Parse a KotOR .wok/.dwk/.pwk walkmesh binary and return face data, "
                "materials, adjacency info, and AABB tree summary. "
                "Pass data_b64 (base64 bytes) or resref."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "data_b64": {"type": "string", "description": "Base64-encoded walkmesh bytes."},
                    "resref": {"type": "string", "description": "ResRef (without extension) to look up."},
                    "ext": {"type": "string", "description": "Extension: 'wok', 'dwk', or 'pwk'. Default 'wok'."},
                },
            },
        },
        {
            "name": "kotor_resource_type_lookup",
            "description": (
                "Look up KotOR resource type information by extension or numeric type ID. "
                "Returns the canonical extension, numeric ID, and category description. "
                "Useful for decoding binary archives or mapping restype integers to file names."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ext": {"type": "string", "description": "File extension without dot (e.g. 'git', 'are', 'tpc')."},
                    "type_id": {"type": "integer", "description": "Numeric resource type ID (e.g. 2023 for .git)."},
                },
            },
        },
        {
            "name": "kotor_read_tpc_info",
            "description": (
                "Return metadata about a KotOR .tpc texture: width, height, format, "
                "mipmap count, whether compressed (DXT1/DXT5), and embedded TXI string. "
                "Reads the 128-byte header only — no full pixel decode required."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "data_b64": {"type": "string", "description": "Base64-encoded .tpc file bytes."},
                    "resref": {"type": "string", "description": "ResRef (without extension) to look up."},
                },
            },
        },
        {
            "name": "kotor_read_pth",
            "description": (
                "Parse a KotOR .pth (path graph) file. PTH files are GFF-wrapped graphs of "
                "XY waypoint nodes used by the AI pathfinder. Returns all waypoints (x, y) "
                "and their connections as a JSON adjacency list. Useful for analysing or "
                "editing creature patrol paths in a module."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "data_b64": {
                        "type": "string",
                        "description": "Base64-encoded .pth GFF file bytes.",
                    },
                    "resref": {
                        "type": "string",
                        "description": "ResRef (without .pth extension) to look up from the active installation.",
                    },
                },
            },
        },
        {
            "name": "kotor_write_pth",
            "description": (
                "Serialise a KotOR path graph back to a binary .pth GFF file. "
                "Accepts a JSON array of waypoints (each with 'x', 'y', and 'connections' list of "
                "destination indices) and produces a base64-encoded .pth GFF binary. "
                "Useful for creating or editing AI pathfinding networks in a module."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["waypoints"],
                "properties": {
                    "waypoints": {
                        "type": "array",
                        "description": (
                            "List of waypoint objects. Each has 'x' (float), 'y' (float), "
                            "and 'connections' (list of integer indices of connected waypoints)."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "x": {"type": "number"},
                                "y": {"type": "number"},
                                "connections": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                },
                            },
                        },
                    },
                },
            },
        },
        {
            "name": "kotor_write_bwm",
            "description": (
                "Export a KotOR walkmesh to a native BWM V1.0 binary (.wok/.dwk/.pwk). "
                "Accepts a JSON walkmesh descriptor with 'vertices' (list of [x,y,z] triples) and "
                "'faces' (list of {v0,v1,v2,material,walkable} objects). "
                "Returns a base64-encoded BWM binary suitable for inclusion in a KotOR module. "
                "Use bwm_type 'wok' (default) for room walkmeshes, 'dwk' for door walkmeshes, "
                "'pwk' for placeable walkmeshes."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["vertices", "faces"],
                "properties": {
                    "vertices": {
                        "type": "array",
                        "description": "List of [x, y, z] vertex positions.",
                        "items": {"type": "array", "items": {"type": "number"}},
                    },
                    "faces": {
                        "type": "array",
                        "description": (
                            "List of face objects, each with 'v0', 'v1', 'v2' (vertex indices), "
                            "'material' (integer walkable-surface type, default 0), "
                            "and 'walkable' (boolean, default true)."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "v0": {"type": "integer"},
                                "v1": {"type": "integer"},
                                "v2": {"type": "integer"},
                                "material": {"type": "integer"},
                                "walkable": {"type": "boolean"},
                            },
                        },
                    },
                    "bwm_type": {
                        "type": "string",
                        "enum": ["wok", "dwk", "pwk"],
                        "description": "Walkmesh type: 'wok' (room), 'dwk' (door), 'pwk' (placeable). Default: 'wok'.",
                    },
                },
            },
        },
        {
            "name": "kotor_write_lip",
            "description": (
                "Serialise a KotOR lip-sync animation back to a binary .lip file. "
                "Accepts a duration (float seconds) and a list of keyframes, each with "
                "'time' (float) and 'shape' (int 0-15 or shape name string). "
                "Returns base64-encoded .lip bytes (format: 'LIP V1.0'). "
                "The 16 viseme shapes follow the Preston Blair phoneme set used by BioWare."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["duration", "keyframes"],
                "properties": {
                    "duration": {
                        "type": "number",
                        "description": "Total animation length in seconds.",
                    },
                    "keyframes": {
                        "type": "array",
                        "description": (
                            "List of keyframe objects, each with 'time' (float seconds) "
                            "and 'shape' (int 0-15 or name: neutral, ee, eh, schwa, "
                            "ah, oh, ooh, ch_sh, ae, th, d, f_v, l_n, m_p_b, t_d, ee2)."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "time": {"type": "number"},
                                "shape": {},
                            },
                        },
                    },
                },
            },
        },
        {
            "name": "kotor_write_vis",
            "description": (
                "Serialise a KotOR room-visibility graph back to a .vis text file. "
                "Accepts a list of visibility pairs (observer room → list of visible rooms). "
                "Returns the .vis text content and base64-encoded bytes. "
                "Useful for authoring or editing inter-room visibility in a custom module."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["visibility"],
                "properties": {
                    "visibility": {
                        "type": "array",
                        "description": (
                            "List of objects with 'room' (observer resref) and "
                            "'visible' (list of room resrefs visible from observer)."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "room": {"type": "string"},
                                "visible": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            },
        },
        {
            "name": "kotor_write_txi",
            "description": (
                "Serialise KotOR texture extended info back to a .txi text file. "
                "Accepts a dict of TXI key-value pairs and returns the .txi text and "
                "base64-encoded bytes. Common keys: envmaptexture, bumpyshinytexture, "
                "blending (additive/punchthrough), proceduretype, numx, numy, fps, "
                "decal (1/0), clamp, filter (LINEAR/NEAREST)."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["fields"],
                "properties": {
                    "fields": {
                        "type": "object",
                        "description": (
                            "Dict of TXI key-value pairs (all values as strings or numbers). "
                            "Example: {\"envmaptexture\": \"CM_baremetal\", \"fps\": 10}."
                        ),
                        "additionalProperties": {},
                    },
                },
            },
        },
    ]


# ── Handlers ────────────────────────────────────────────────────────────────

async def handle_read_ssf(arguments: Dict[str, Any]) -> Any:
    from gmodular.formats.kotor_formats import read_ssf, SSFSound, _SSF_SOUND_NAMES
    data = _resolve_bytes(arguments, "ssf")
    if data is None:
        return _json_content({"error": "no SSF data found"})
    try:
        ssf = read_ssf(data)
    except Exception as exc:
        return _json_content({"error": str(exc)})
    result = {_SSF_SOUND_NAMES[s]: ssf.get(s) for s in SSFSound}
    return _json_content({"sounds": result, "assigned": sum(1 for v in result.values() if v != -1)})


async def handle_read_lip(arguments: Dict[str, Any]) -> Any:
    from gmodular.formats.kotor_formats import read_lip
    data = _resolve_bytes(arguments, "lip")
    if data is None:
        return _json_content({"error": "no LIP data found"})
    try:
        lip = read_lip(data)
    except Exception as exc:
        return _json_content({"error": str(exc)})
    frames = [{"time": kf.time, "shape": kf.shape.name, "shape_id": int(kf.shape)}
              for kf in lip.keyframes]
    return _json_content({"length": lip.length, "frame_count": len(frames), "keyframes": frames})


async def handle_read_txi(arguments: Dict[str, Any]) -> Any:
    from gmodular.formats.kotor_formats import read_txi
    data = _resolve_bytes(arguments, "txi")
    if data is None:
        return _json_content({"error": "no TXI data found"})
    try:
        txi = read_txi(data)
    except Exception as exc:
        return _json_content({"error": str(exc)})
    return _json_content({
        "fields":        txi.all_fields,
        "is_animated":   txi.is_animated,
        "is_procedural": txi.is_procedural,
        "is_decal":      txi.is_decal,
        "blending":      txi.blending,
        "num_frames":    txi.num_frames,
        "envmap":        txi.envmap,
        "bumpmap":       txi.bumpmap,
    })


async def handle_read_vis(arguments: Dict[str, Any]) -> Any:
    from gmodular.formats.kotor_formats import read_vis
    data = _resolve_bytes(arguments, "vis")
    if data is None:
        return _json_content({"error": "no VIS data found"})
    try:
        vis = read_vis(data)
    except Exception as exc:
        return _json_content({"error": str(exc)})
    graph = {r: vis.visible_from(r) for r in vis.all_rooms()}
    return _json_content({"rooms": vis.all_rooms(), "visibility": graph})


async def handle_read_ncs(arguments: Dict[str, Any]) -> Any:
    from gmodular.formats.kotor_formats import read_ncs
    data = _resolve_bytes(arguments, "ncs")
    if data is None:
        return _json_content({"error": "no NCS data found"})
    try:
        ncs = read_ncs(data)
    except Exception as exc:
        return _json_content({"error": str(exc)})
    return _json_content({
        "instruction_count": len(ncs),
        "code_size_bytes":   ncs.code_size,
        "disassembly":       ncs.disassembly_text(),
    })


async def handle_write_ssf(arguments: Dict[str, Any]) -> Any:
    from gmodular.formats.kotor_formats import write_ssf, SSFData, SSFSound
    sounds_map = arguments.get("sounds", {})
    ssf = SSFData()
    for name, strref in sounds_map.items():
        try:
            slot = SSFSound[name.upper()]
            ssf.set(slot, int(strref))
        except (KeyError, ValueError) as exc:
            log.warning("kotor_write_ssf: ignoring invalid sound %r: %s", name, exc)
    data = write_ssf(ssf)
    return _b64_content("ssf_b64", data, {"size_bytes": len(data)})


async def handle_write_2da_csv(arguments: Dict[str, Any]) -> Any:
    from gmodular.formats.kotor_formats import TwoDAData, write_2da_ascii
    columns = arguments.get("columns", [])
    rows    = arguments.get("rows", [])
    twoda = TwoDAData(columns=columns, rows=rows)
    ascii_text = write_2da_ascii(twoda).decode("latin-1", errors="replace")
    return _json_content({"twoda_ascii": ascii_text, "rows": len(rows), "columns": len(columns)})


async def handle_write_tlk_patch(arguments: Dict[str, Any]) -> Any:
    from gmodular.formats.kotor_formats import TLKData, TLKEntry, TLK_FLAG_TEXT, write_tlk
    entries   = arguments.get("entries", [])
    lang_id   = int(arguments.get("language_id", 0))
    tlk = TLKData(language_id=lang_id)
    max_strref = -1
    pending = []
    for e in entries:
        strref = int(e.get("strref", 0))
        max_strref = max(max_strref, strref)
        pending.append((strref, e))
    # Pad to max_strref
    for _ in range(max_strref + 1):
        tlk._entries.append(TLKEntry())
    for strref, e in pending:
        entry = TLKEntry(
            text         = str(e.get("text", "")),
            sound_resref = str(e.get("sound_resref", "")),
            sound_length = float(e.get("sound_length", 0.0)),
            flags        = TLK_FLAG_TEXT,
        )
        tlk._entries[strref] = entry
    data = write_tlk(tlk)
    return _b64_content("tlk_b64", data)


async def handle_describe_ssf(arguments: Dict[str, Any]) -> Any:
    """Human-readable SSF description with TLK string lookups."""
    from gmodular.formats.kotor_formats import read_ssf, SSFSound, _SSF_SOUND_NAMES
    resref = arguments.get("resref", "").strip().lower()
    if not resref:
        return _json_content({"error": "resref required"})

    data = _get_resource_bytes(resref, "ssf")
    if data is None:
        return _json_content({"error": f"SSF '{resref}' not found in resource manager"})

    try:
        ssf = read_ssf(data)
    except Exception as exc:
        return _json_content({"error": str(exc)})

    # Try TLK lookup
    tlk_strings: Dict[int, str] = {}
    try:
        from gmodular.formats.archives import ResourceManager
        rm = ResourceManager.instance()
        tlk_data = rm.get_file("dialog", "tlk")
        if tlk_data:
            from gmodular.formats.kotor_formats import read_tlk
            tlk = read_tlk(tlk_data)
            for s in SSFSound:
                strref = ssf.get(s)
                if strref != -1:
                    tlk_strings[strref] = tlk.get_text(strref, f"StrRef({strref})")
    except Exception:
        pass

    sounds = []
    for s in SSFSound:
        strref = ssf.get(s)
        entry = {
            "slot":   s.name,
            "label":  _SSF_SOUND_NAMES[s],
            "strref": strref,
            "text":   tlk_strings.get(strref, "") if strref != -1 else "(none)",
        }
        sounds.append(entry)

    return _json_content({"resref": resref, "sounds": sounds})


async def handle_read_ltr(arguments: Dict[str, Any]) -> Any:
    """Parse a KotOR LTR (name-generator Markov chain) file."""
    from gmodular.formats.kotor_formats import read_ltr
    data = _resolve_bytes(arguments, "ltr")
    if not data:
        return _json_content({"error": "No LTR data — provide resref or data_b64"})
    try:
        ltr = read_ltr(data)
    except Exception as exc:
        return _json_content({"error": str(exc)})
    n = ltr.letter_count
    # Return summary: letter_count + first-letter triplets for quick inspection
    single_summary = [
        {"char_idx": i, "start": ltr.single[i*3], "middle": ltr.single[i*3+1], "end": ltr.single[i*3+2]}
        for i in range(n)
    ]
    return _json_content({
        "letter_count": n,
        "single": single_summary,
        "double_size": len(ltr.double),
        "triple_size": len(ltr.triple),
    })


async def handle_write_ltr(arguments: Dict[str, Any]) -> Any:
    """Build a KotOR .ltr binary from probability arrays."""
    from gmodular.formats.kotor_formats import LTRData, write_ltr, _LTR_LETTER_COUNT
    n = int(arguments.get("letter_count", _LTR_LETTER_COUNT))
    ltr = LTRData(letter_count=n)
    if "single" in arguments:
        ltr.single = [float(x) for x in arguments["single"]]
    if "double" in arguments:
        ltr.double = [float(x) for x in arguments["double"]]
    if "triple" in arguments:
        ltr.triple = [float(x) for x in arguments["triple"]]
    data = write_ltr(ltr)
    return _b64_content("ltr_b64", data, {"letter_count": n})


async def handle_write_ncs(arguments: Dict[str, Any]) -> Any:
    """Re-assemble a KotOR NCS binary from a flat instruction list."""
    from gmodular.formats.kotor_formats import NCSData, NCSInstruction, write_ncs
    raw_instrs = arguments.get("instructions", [])
    if not isinstance(raw_instrs, list):
        return _json_content({"error": "'instructions' must be a list"})
    ncs = NCSData()
    for idx, item in enumerate(raw_instrs):
        try:
            opcode  = int(item.get("opcode",  0))
            subtype = int(item.get("subtype", 0))
            ops_b64 = item.get("operands_b64", "")
            operands = base64.b64decode(ops_b64) if ops_b64 else b""
            ncs.instructions.append(NCSInstruction(
                offset=idx * 2, opcode=opcode, subtype=subtype, operands=operands
            ))
        except Exception as exc:
            return _json_content({"error": f"Bad instruction at index {idx}: {exc}"})
    data = write_ncs(ncs)
    return _b64_content("ncs_b64", data, {"instruction_count": len(ncs.instructions)})


async def handle_read_lyt(arguments: Dict[str, Any]) -> Any:
    """Parse a .lyt room-layout file and return structured JSON."""
    data = _resolve_bytes(arguments, "lyt")
    if data is None:
        return _json_content({"error": "No data_b64 or resref provided"})
    try:
        from gmodular.formats.lyt_vis import LayoutData
        lyt = LayoutData.from_bytes(data)
        rooms = [
            {"resref": r.resref, "x": r.x, "y": r.y, "z": r.z}
            for r in lyt.rooms
        ]
        hooks = []
        for dh in lyt.door_hooks:
            entry: Dict[str, Any] = {
                "name": dh.name, "room": dh.room,
                "x": dh.x, "y": dh.y, "z": dh.z,
            }
            if hasattr(dh, 'qx'):
                entry.update({"qx": dh.qx, "qy": dh.qy, "qz": dh.qz, "qw": dh.qw})
            hooks.append(entry)
        tracks = [str(t) for t in lyt.tracks]
        obstacles = [str(o) for o in lyt.obstacles]
        return _json_content({
            "room_count": len(rooms),
            "rooms": rooms,
            "door_hook_count": len(hooks),
            "door_hooks": hooks,
            "track_count": len(tracks),
            "tracks": tracks,
            "obstacle_count": len(obstacles),
            "obstacles": obstacles,
        })
    except Exception as exc:
        return _json_content({"error": str(exc)})


async def handle_write_lyt(arguments: Dict[str, Any]) -> Any:
    """Serialise a room layout to a KotOR .lyt text file."""
    rooms_raw = arguments.get("rooms")
    if not isinstance(rooms_raw, list):
        return _json_content({"error": "rooms must be a JSON array"})
    try:
        from gmodular.formats.lyt_vis import LayoutData, RoomPlacement, DoorHookEntry, LYTWriter
        lyt = LayoutData()
        for r in rooms_raw:
            lyt.rooms.append(RoomPlacement(
                resref=str(r.get("resref", "unknown")),
                x=float(r.get("x", 0.0)),
                y=float(r.get("y", 0.0)),
                z=float(r.get("z", 0.0)),
            ))
        for dh in (arguments.get("door_hooks") or []):
            lyt.door_hooks.append(DoorHookEntry(
                name=str(dh.get("name", "door")),
                room=str(dh.get("room", "")),
                x=float(dh.get("x", 0.0)),
                y=float(dh.get("y", 0.0)),
                z=float(dh.get("z", 0.0)),
                qx=float(dh.get("qx", 0.0)),
                qy=float(dh.get("qy", 0.0)),
                qz=float(dh.get("qz", 0.0)),
                qw=float(dh.get("qw", 1.0)),
            ))
        lyt_text = LYTWriter.to_string(lyt)
        lyt_bytes = lyt_text.encode("utf-8")
        b64 = base64.b64encode(lyt_bytes).decode()
        return _json_content({
            "room_count":      len(lyt.rooms),
            "door_hook_count": len(lyt.door_hooks),
            "lyt_text":        lyt_text,
            "data_b64":        b64,
            "size_bytes":      len(lyt_bytes),
        })
    except Exception as exc:
        return _json_content({"error": str(exc)})


async def handle_read_bwm(arguments: Dict[str, Any]) -> Any:
    """Parse a .wok/.dwk/.pwk walkmesh binary and return face data."""
    ext = arguments.get("ext", "wok").lower().lstrip(".")
    data = _resolve_bytes(arguments, ext)
    if data is None:
        return _json_content({"error": "No data_b64 or resref provided"})
    try:
        from gmodular.formats.wok_parser import parse_wok
        wok = parse_wok(data)
        faces = []
        for f in wok.faces[:100]:  # limit to 100 faces for JSON size
            face: Dict[str, Any] = {
                "v": [list(v) for v in f.vertices],
                "material": f.material,
                "walkable": f.walkable,
            }
            if hasattr(f, 'adjacency'):
                face["adjacency"] = list(f.adjacency)
            faces.append(face)
        return _json_content({
            "face_count": len(wok.faces),
            "walkable_face_count": sum(1 for f in wok.faces if f.walkable),
            "faces_shown": min(100, len(wok.faces)),
            "faces": faces,
            "has_aabb": hasattr(wok, 'aabb_root') and wok.aabb_root is not None,
        })
    except Exception as exc:
        return _json_content({"error": str(exc)})


async def handle_resource_type_lookup(arguments: Dict[str, Any]) -> Any:
    """Return resource type info by extension or type ID."""
    from gmodular.formats.archives import RES_TYPE_MAP, EXT_TO_TYPE

    # Category descriptions based on PyKotor ResourceType categorisation
    CATEGORIES: Dict[int, str] = {
        range(1, 10): "Image/Audio",      # type: ignore[dict-item]
    }
    _CAT: Dict[str, str] = {
        "bmp": "Image", "tga": "Image", "dds": "Image", "tpc": "Texture",
        "wav": "Audio", "bmu": "Audio",
        "mdl": "Model", "mdx": "Model", "wok": "Walkmesh", "dwk": "Walkmesh",
        "pwk": "Walkmesh",
        "are": "Area GFF", "git": "Area GFF", "ifo": "Module GFF",
        "gff": "Generic GFF", "dlg": "Dialog GFF", "jrl": "Journal GFF",
        "utc": "Creature Template", "utp": "Placeable Template",
        "utd": "Door Template", "ute": "Encounter Template",
        "utm": "Merchant Template", "utw": "Waypoint Template",
        "uts": "Sound Template", "utt": "Trigger Template",
        "ncs": "Compiled Script", "nss": "Script Source",
        "2da": "2D Array", "tlk": "Talk Table", "txi": "Texture Info",
        "lyt": "Room Layout", "vis": "Room Visibility", "ltr": "Letter Table",
        "lip": "Lip Sync", "ssf": "Sound Set", "bik": "Video",
        "erf": "ERF Archive", "rim": "RIM Archive", "mod": "Module Archive",
        "sav": "Save Archive",
    }

    ext = arguments.get("ext", "").lower().lstrip(".")
    type_id = arguments.get("type_id")

    results = []
    if ext:
        tid = EXT_TO_TYPE.get(ext)
        if tid is not None:
            results.append({
                "ext": ext,
                "type_id": tid,
                "category": _CAT.get(ext, "Binary"),
            })
        else:
            return _json_content({"error": f"Unknown extension: '{ext}'"})

    if type_id is not None:
        found_ext = RES_TYPE_MAP.get(int(type_id))
        if found_ext:
            results.append({
                "ext": found_ext,
                "type_id": int(type_id),
                "category": _CAT.get(found_ext, "Binary"),
            })
        else:
            return _json_content({"error": f"Unknown type_id: {type_id}"})

    if not results:
        # Return full map
        all_types = [
            {"ext": e, "type_id": i, "category": _CAT.get(e, "Binary")}
            for i, e in sorted(RES_TYPE_MAP.items())
        ]
        return _json_content({"count": len(all_types), "types": all_types})

    return _json_content({"results": results})


async def handle_read_tpc_info(arguments: Dict[str, Any]) -> Any:
    """Return metadata for a .tpc texture (header only)."""
    data = _resolve_bytes(arguments, "tpc")
    if data is None:
        return _json_content({"error": "No data_b64 or resref provided"})
    try:
        if len(data) < 128:
            return _json_content({"error": "Data too short to be a valid TPC (< 128 bytes)"})
        import struct as _struct
        size     = _struct.unpack_from("<I", data, 0)[0]
        width    = _struct.unpack_from("<H", data, 8)[0]
        height   = _struct.unpack_from("<H", data, 10)[0]
        encoding = data[12]
        mip_count = data[13]
        compressed = size != 0
        if compressed:
            fmt = "DXT1" if encoding == 2 else ("DXT5" if encoding == 4 else f"unknown(enc={encoding})")
        elif encoding == 1:
            fmt = "Greyscale"
        elif encoding == 2:
            fmt = "RGB"
        elif encoding == 4:
            fmt = "RGBA"
        else:
            fmt = f"unknown(enc={encoding})"
        # TXI data follows pixel data at the end of the file
        txi_str = ""
        try:
            from gmodular.formats.tpc_reader import read_tpc
            img = read_tpc(data)
            txi_str = getattr(img, 'txi', "") or ""
        except Exception:
            pass
        return _json_content({
            "width": width,
            "height": height,
            "format": fmt,
            "compressed": compressed,
            "mipmap_count": mip_count,
            "file_size": len(data),
            "txi": txi_str[:500],  # truncate long TXI
        })
    except Exception as exc:
        return _json_content({"error": str(exc)})


async def handle_read_pth(arguments: Dict[str, Any]) -> Any:
    """Parse a KotOR .pth (path graph) GFF file."""
    data = _resolve_bytes(arguments, "pth")
    if data is None:
        return _json_content({"error": "No data_b64 or resref provided"})
    try:
        from gmodular.formats.gff_reader import GFFReader
        from gmodular.formats.kotor_formats import PTHData, PTHPoint
        gff_reader = GFFReader.from_bytes(data)
        gff_root   = gff_reader.parse()

        pth = PTHData()
        try:
            points_list = gff_root.fields.get("Path_Points")
            if points_list is not None:
                structs = points_list.value if hasattr(points_list, "value") else []
                for struct in structs:
                    x = 0.0
                    y = 0.0
                    if hasattr(struct, "fields"):
                        xf = struct.fields.get("X")
                        yf = struct.fields.get("Y")
                        if xf is not None:
                            x = float(xf.value)
                        if yf is not None:
                            y = float(yf.value)
                    idx = pth.add_point(x, y)
                    # Connections
                    if hasattr(struct, "fields"):
                        conn_field = struct.fields.get("Conections")
                        if conn_field is not None and hasattr(conn_field, "value"):
                            for conn_s in (conn_field.value or []):
                                if hasattr(conn_s, "fields"):
                                    df = conn_s.fields.get("Destination")
                                    if df is not None:
                                        dest = int(df.value)
                                        if dest >= 0:
                                            pth.points[idx].connections.append(dest)
        except Exception as _e:
            log.warning("handle_read_pth inner parse: %s", _e)

        waypoints = [
            {
                "index":       i,
                "x":           round(pt.x, 4),
                "y":           round(pt.y, 4),
                "connections": pt.connections,
            }
            for i, pt in enumerate(pth.points)
        ]
        return _json_content({
            "waypoint_count": len(pth.points),
            "waypoints":      waypoints,
        })
    except Exception as exc:
        return _json_content({"error": str(exc)})


async def handle_write_pth(arguments: Dict[str, Any]) -> Any:
    """Serialise a KotOR path graph to a binary .pth GFF file."""
    waypoints_raw = arguments.get("waypoints")
    if not isinstance(waypoints_raw, list):
        return _json_content({"error": "waypoints must be a JSON array"})
    try:
        from gmodular.formats.kotor_formats import PTHData, write_pth_to_bytes
        pth = PTHData()
        for wp in waypoints_raw:
            x = float(wp.get("x", 0.0))
            y = float(wp.get("y", 0.0))
            pth.add_point(x, y)
        # Now add connections (after all points exist)
        for i, wp in enumerate(waypoints_raw):
            for dest in (wp.get("connections") or []):
                dest_idx = int(dest)
                if 0 <= dest_idx < len(pth.points):
                    if dest_idx not in pth.points[i].connections:
                        pth.points[i].connections.append(dest_idx)
        data = write_pth_to_bytes(pth)
        b64  = base64.b64encode(data).decode()
        return _json_content({
            "waypoint_count": len(pth.points),
            "data_b64":       b64,
            "size_bytes":     len(data),
        })
    except Exception as exc:
        return _json_content({"error": str(exc)})


async def handle_write_bwm(arguments: Dict[str, Any]) -> Any:
    """Export a KotOR walkmesh to a native BWM V1.0 binary."""
    vertices_raw = arguments.get("vertices")
    faces_raw    = arguments.get("faces")
    if not isinstance(vertices_raw, list) or not isinstance(faces_raw, list):
        return _json_content({"error": "vertices and faces must be JSON arrays"})
    try:
        from gmodular.formats.wok_parser import WalkMesh, WalkFace, WOKWriter, BWM_TYPE_WOK, BWM_TYPE_PWK_DWK
        import math as _math

        # Parse vertices
        verts = []
        for v in vertices_raw:
            if len(v) >= 3:
                verts.append((float(v[0]), float(v[1]), float(v[2])))
            elif len(v) == 2:
                verts.append((float(v[0]), float(v[1]), 0.0))
            else:
                return _json_content({"error": f"Invalid vertex {v!r}: need at least 2 coords"})

        # Parse bwm_type
        bwm_type_str = arguments.get("bwm_type", "wok").lower()
        bwm_type = BWM_TYPE_WOK if bwm_type_str == "wok" else BWM_TYPE_PWK_DWK

        # Build WalkMesh
        wm = WalkMesh()

        for fi, f in enumerate(faces_raw):
            i0 = int(f.get("v0", 0))
            i1 = int(f.get("v1", 1))
            i2 = int(f.get("v2", 2))
            if i0 >= len(verts) or i1 >= len(verts) or i2 >= len(verts):
                return _json_content({"error": f"Face {fi} references out-of-range vertex"})

            # If the caller provides an explicit 'material', use it directly.
            # If they provide 'walkable: false' with no material, use material 6 (NonWalk).
            # Default material 0 (Dirt) is walkable.
            if "material" in f:
                mat = int(f["material"])
            else:
                walkable_flag = bool(f.get("walkable", True))
                mat = 0 if walkable_flag else 6   # 0=Dirt (walk), 6=NonWalk

            v0 = verts[i0]
            v1 = verts[i1]
            v2 = verts[i2]

            # Compute face normal from cross product
            ax, ay, az = v1[0]-v0[0], v1[1]-v0[1], v1[2]-v0[2]
            bx, by, bz = v2[0]-v0[0], v2[1]-v0[1], v2[2]-v0[2]
            nx = ay*bz - az*by
            ny = az*bx - ax*bz
            nz = ax*by - ay*bx
            length = _math.sqrt(nx*nx + ny*ny + nz*nz) or 1.0
            normal = (nx/length, ny/length, nz/length)

            face = WalkFace(v0=v0, v1=v1, v2=v2, material=mat, normal=normal)
            wm.faces.append(face)

        writer = WOKWriter(wm, bwm_type=bwm_type)
        data   = writer.to_bytes()
        b64    = base64.b64encode(data).decode()
        ext    = bwm_type_str if bwm_type_str in ("dwk", "pwk") else "wok"
        walkable_count = sum(1 for face in wm.faces if face.walkable)
        return _json_content({
            "face_count":       len(faces_raw),
            "walkable_faces":   walkable_count,
            "vertex_count":     len(verts),
            "bwm_type":         bwm_type_str,
            "ext":              ext,
            "data_b64":         b64,
            "size_bytes":       len(data),
        })
    except Exception as exc:
        return _json_content({"error": str(exc)})


async def handle_write_lip(arguments: Dict[str, Any]) -> Any:
    """Serialise a KotOR lip-sync animation to binary .lip format."""
    try:
        from gmodular.formats.kotor_formats import LIPData, LIPKeyframe, LIPShape, write_lip
        duration = float(arguments.get("duration", 0.0))
        keyframes_raw = arguments.get("keyframes") or []

        # Build shape name→enum map
        _name_map = {s.name.lower(): s for s in LIPShape}
        # Also support underscore variants
        _name_map.update({s.name.lower().replace("_", ""): s for s in LIPShape})

        lip = LIPData(length=duration)
        for kf in keyframes_raw:
            t = float(kf.get("time", 0.0))
            shape_val = kf.get("shape", 0)
            if isinstance(shape_val, str):
                shape = _name_map.get(shape_val.lower().replace("_", ""), LIPShape.NEUTRAL)
            else:
                try:
                    shape = LIPShape(int(shape_val))
                except (ValueError, KeyError):
                    shape = LIPShape.NEUTRAL
            lip.add(t, shape)

        data = write_lip(lip)
        b64  = base64.b64encode(data).decode()
        return _json_content({
            "duration":       duration,
            "keyframe_count": len(lip.keyframes),
            "data_b64":       b64,
            "size_bytes":     len(data),
        })
    except Exception as exc:
        return _json_content({"error": str(exc)})


async def handle_write_vis(arguments: Dict[str, Any]) -> Any:
    """Serialise a room-visibility graph to a KotOR .vis text file."""
    try:
        from gmodular.formats.kotor_formats import VISData, write_vis
        vis_raw = arguments.get("visibility") or []

        vis = VISData()
        for entry in vis_raw:
            room = str(entry.get("room", "")).lower()
            if not room:
                continue
            vis.add_room(room)
            for seen in (entry.get("visible") or []):
                seen_lower = str(seen).lower()
                vis.add_room(seen_lower)
                vis.set_visible(room, seen_lower, visible=True)

        data = write_vis(vis)
        vis_text = data.decode("ascii", errors="replace")
        b64  = base64.b64encode(data).decode()
        rooms = vis.all_rooms()
        return _json_content({
            "room_count":  len(rooms),
            "rooms":       rooms,
            "vis_text":    vis_text,
            "data_b64":    b64,
            "size_bytes":  len(data),
        })
    except Exception as exc:
        return _json_content({"error": str(exc)})


async def handle_write_txi(arguments: Dict[str, Any]) -> Any:
    """Serialise KotOR texture extended info to a .txi text file."""
    try:
        from gmodular.formats.kotor_formats import TXIData, write_txi
        fields_raw = arguments.get("fields") or {}

        txi = TXIData()
        for k, v in fields_raw.items():
            txi.set(str(k).lower(), str(v))

        data = write_txi(txi)
        txi_text = data.decode("ascii", errors="replace")
        b64  = base64.b64encode(data).decode()
        return _json_content({
            "field_count": len(fields_raw),
            "txi_text":    txi_text,
            "data_b64":    b64,
            "size_bytes":  len(data),
        })
    except Exception as exc:
        return _json_content({"error": str(exc)})


# ── Private helpers ──────────────────────────────────────────────────────────

def _resolve_bytes(arguments: Dict[str, Any], ext: str) -> bytes | None:
    """Return bytes from base64 arg or from ResourceManager via resref."""
    b64 = arguments.get("data_b64")
    if b64:
        try:
            return base64.b64decode(b64)
        except Exception:
            pass

    resref = arguments.get("resref", "").strip().lower()
    if resref:
        return _get_resource_bytes(resref, ext)
    return None
