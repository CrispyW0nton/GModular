"""
GModular MCP Tools — NWScript / NCS script tools.

Provides:
  kotor_disasm_script     — Disassemble a compiled NCS binary into readable opcodes
  kotor_compile_script    — Compile NWScript (.nss) source via nwnnsscomp or built-in stub
  kotor_decompile_script  — Decompile NCS binary back to NWScript source via DeNCS / xoreos-tools
  kotor_ncs_info          — Return opcode statistics and call graph hints for an NCS file

Reference implementations:
  PyKotor/resource/formats/ncs/io_ncs.py       — NCS binary reader
  PyKotor/resource/formats/ncs/decompiler.py   — NCS decompiler (DeNCS wrapper)
  PyKotor/resource/formats/ncs/compilers.py    — NWN compiler wrappers
  Kotor.NET/Formats/KotorNCS/NCS.cs            — C# opcode reference
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import shutil
import subprocess
import tempfile
import os
from pathlib import Path
from typing import Any, Dict, List

log = logging.getLogger(__name__)

# ─── tool descriptors ────────────────────────────────────────────────────────

def get_tools() -> List[Dict[str, Any]]:
    return [
        {
            "name": "kotor_disasm_script",
            "description": (
                "Disassemble a compiled KotOR NCS binary into a human-readable opcode listing. "
                "Pass base64-encoded NCS bytes in 'data_b64', or provide game+resref to load "
                "from the current installation. Returns offset, opcode, type, and operands for "
                "every instruction. Useful for understanding script logic without source code."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "data_b64":  {"type": "string", "description": "Base64-encoded .ncs binary"},
                    "game":      {"type": "string", "description": "k1 or k2"},
                    "resref":    {"type": "string", "description": "Script resref (no extension)"},
                    "format":    {
                        "type": "string",
                        "enum": ["text", "json"],
                        "default": "text",
                        "description": "Output format: text listing or JSON array of instructions",
                    },
                },
            },
        },
        {
            "name": "kotor_compile_script",
            "description": (
                "Compile a NWScript (.nss) source file to NCS bytecode. "
                "Requires nwnnsscomp.exe / nwnnsscomp on PATH, or the path can be supplied. "
                "Pass 'source' as the script text or 'source_b64' as base64-encoded text. "
                "Returns the compiled NCS bytes as base64 on success, or the compiler error log."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "source":       {"type": "string", "description": "NWScript source text"},
                    "source_b64":   {"type": "string", "description": "Base64-encoded NWScript source"},
                    "compiler_path":{"type": "string", "description": "Path to nwnnsscomp binary (optional)"},
                    "game":         {"type": "string", "description": "k1 or k2 (affects nwscript.nss includes)"},
                },
            },
        },
        {
            "name": "kotor_decompile_script",
            "description": (
                "Decompile a compiled NCS binary back to NWScript source code. "
                "Attempts DeNCS decompilation first, then falls back to annotated disassembly. "
                "Pass 'data_b64' as base64-encoded NCS bytes, or game+resref to load from install."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "data_b64":  {"type": "string", "description": "Base64-encoded .ncs binary"},
                    "game":      {"type": "string", "description": "k1 or k2"},
                    "resref":    {"type": "string", "description": "Script resref (no extension)"},
                    "tool_path": {"type": "string", "description": "Path to DeNCS/xoreos-tools binary (optional)"},
                },
            },
        },
        {
            "name": "kotor_ncs_info",
            "description": (
                "Analyse a compiled NCS binary and return statistics: instruction count, "
                "opcode histogram, all ACTION (engine-call) ids, all jump targets, "
                "and a list of all string constants in the script. "
                "Useful for a quick overview before full decompilation."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "data_b64": {"type": "string", "description": "Base64-encoded .ncs binary"},
                    "game":     {"type": "string", "description": "k1 or k2"},
                    "resref":   {"type": "string", "description": "Script resref (no extension)"},
                },
            },
        },
    ]


# ─── helpers ─────────────────────────────────────────────────────────────────

def _get_ncs_data(arguments: Dict[str, Any]) -> bytes:
    """Resolve NCS bytes from data_b64, or from installation via game+resref."""
    if "data_b64" in arguments and arguments["data_b64"]:
        return base64.b64decode(arguments["data_b64"])

    game   = arguments.get("game", "k1")
    resref = arguments.get("resref", "")
    if not resref:
        raise ValueError("Provide either data_b64 or both game and resref")

    try:
        from gmodular.mcp.state import get_installation
        inst = get_installation(game)
        if inst is None:
            raise ValueError(f"No installation loaded for game '{game}'")
        rm = inst.resource_manager()
        data = rm.get(resref, "ncs")
        if data is None:
            raise ValueError(f"Script '{resref}.ncs' not found in installation '{game}'")
        return data
    except ImportError:
        raise ValueError("Installation manager not available; pass data_b64 instead")


def _json_content(obj: Any) -> Dict[str, Any]:
    """Wrap a result in MCP content envelope."""
    return {"content": [{"type": "text", "text": json.dumps(obj, indent=2)}]}


# ─── handlers ────────────────────────────────────────────────────────────────

async def handle_disasm_script(arguments: Dict[str, Any]) -> Any:
    """Disassemble NCS binary → opcode listing."""
    try:
        from gmodular.formats.kotor_formats import read_ncs, NCSOpcode
        data = _get_ncs_data(arguments)
        ncs  = read_ncs(data)
        fmt  = arguments.get("format", "text")

        if fmt == "json":
            instructions = []
            for instr in ncs.instructions:
                try:
                    op_name = NCSOpcode(instr.opcode).name
                except ValueError:
                    op_name = f"0x{instr.opcode:02X}"
                instructions.append({
                    "offset":   instr.offset,
                    "opcode":   op_name,
                    "subtype":  instr.subtype,
                    "operands": instr.operands.hex() if instr.operands else "",
                })
            return _json_content({
                "instruction_count": len(instructions),
                "code_size":         ncs.code_size,
                "instructions":      instructions,
            })
        else:
            text = ncs.disassembly_text() or "(empty script)"
            return _json_content({
                "instruction_count": len(ncs),
                "code_size":         ncs.code_size,
                "disassembly":       text,
            })
    except Exception as exc:
        return _json_content({"error": str(exc)})


async def handle_compile_script(arguments: Dict[str, Any]) -> Any:
    """Compile NWScript source → NCS bytes via nwnnsscomp."""
    try:
        # Get source text
        if "source_b64" in arguments and arguments["source_b64"]:
            source = base64.b64decode(arguments["source_b64"]).decode("utf-8", errors="replace")
        elif "source" in arguments and arguments["source"]:
            source = arguments["source"]
        else:
            return _json_content({"error": "Provide source or source_b64"})

        compiler = arguments.get("compiler_path", "") or shutil.which("nwnnsscomp") or ""
        if not compiler:
            return _json_content({
                "error": (
                    "nwnnsscomp compiler not found on PATH and no compiler_path provided. "
                    "Install nwnnsscomp (https://github.com/nwneetools/nwnsc) and add it to PATH, "
                    "or pass compiler_path to this tool."
                )
            })

        game = arguments.get("game", "k1")
        with tempfile.TemporaryDirectory() as tmpdir:
            src_path = os.path.join(tmpdir, "script.nss")
            ncs_path = os.path.join(tmpdir, "script.ncs")
            with open(src_path, "w", encoding="utf-8") as f:
                f.write(source)

            args = [compiler, "-o", ncs_path, src_path]
            result = subprocess.run(
                args, capture_output=True, text=True, timeout=30,
                cwd=tmpdir,
            )
            if result.returncode != 0 or not os.path.exists(ncs_path):
                return _json_content({
                    "error":  "Compilation failed",
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                })

            with open(ncs_path, "rb") as f:
                ncs_bytes = f.read()

        return _json_content({
            "success":      True,
            "ncs_b64":      base64.b64encode(ncs_bytes).decode(),
            "ncs_size":     len(ncs_bytes),
            "compiler":     compiler,
            "stdout":       result.stdout,
        })
    except subprocess.TimeoutExpired:
        return _json_content({"error": "Compiler timed out after 30 seconds"})
    except Exception as exc:
        return _json_content({"error": str(exc)})


async def handle_decompile_script(arguments: Dict[str, Any]) -> Any:
    """Decompile NCS bytes → NWScript source (DeNCS or annotated disasm fallback)."""
    try:
        data = _get_ncs_data(arguments)

        # Try DeNCS / xoreos-tools decompiler
        tool_path = arguments.get("tool_path", "")
        for candidate in (tool_path, "dencs", "xoreos-tools", "nwnnsscomp"):
            if not candidate:
                continue
            resolved = shutil.which(candidate) or (candidate if os.path.isfile(candidate) else None)
            if resolved:
                try:
                    with tempfile.TemporaryDirectory() as tmpdir:
                        ncs_path = os.path.join(tmpdir, "script.ncs")
                        nss_path = os.path.join(tmpdir, "script.nss")
                        with open(ncs_path, "wb") as f:
                            f.write(data)
                        # DeNCS: dencs script.ncs script.nss
                        result = subprocess.run(
                            [resolved, ncs_path, nss_path],
                            capture_output=True, text=True, timeout=15,
                        )
                        if os.path.exists(nss_path):
                            with open(nss_path, "r", encoding="utf-8", errors="replace") as f:
                                nss_source = f.read()
                            return _json_content({
                                "source":      nss_source,
                                "decompiler":  resolved,
                                "via":         "DeNCS",
                            })
                except Exception:
                    pass

        # Fallback: annotated disassembly
        from gmodular.formats.kotor_formats import read_ncs, NCSOpcode
        ncs = read_ncs(data)
        disasm = ncs.disassembly_text()
        return _json_content({
            "note":          "No decompiler found — returning annotated disassembly",
            "disassembly":   disasm,
            "instruction_count": len(ncs),
            "hint": (
                "Install DeNCS (https://github.com/KOTOR-Modding-Discord/DeNCS) "
                "or nwnnsscomp and pass tool_path to get actual NWScript source."
            ),
        })
    except Exception as exc:
        return _json_content({"error": str(exc)})


async def handle_ncs_info(arguments: Dict[str, Any]) -> Any:
    """Return opcode statistics and metadata for an NCS binary."""
    try:
        from gmodular.formats.kotor_formats import read_ncs, NCSOpcode
        from collections import Counter
        data  = _get_ncs_data(arguments)
        ncs   = read_ncs(data)

        opcode_counts: Counter = Counter()
        action_ids:    List[int]   = []
        jump_targets:  List[int]   = []
        string_consts: List[str]   = []

        for instr in ncs.instructions:
            try:
                name = NCSOpcode(instr.opcode).name
            except ValueError:
                name = f"0x{instr.opcode:02X}"
            opcode_counts[name] += 1

            if instr.opcode == NCSOpcode.ACTION and len(instr.operands) >= 2:
                import struct
                rid = struct.unpack_from(">H", instr.operands, 0)[0]
                action_ids.append(rid)

            if instr.opcode in (NCSOpcode.JMP, NCSOpcode.JSR,
                                NCSOpcode.JZ, NCSOpcode.JNZ):
                if len(instr.operands) >= 4:
                    import struct
                    target = struct.unpack_from(">i", instr.operands, 0)[0]
                    jump_targets.append(instr.offset + target)

            if instr.opcode == NCSOpcode.CONST and instr.subtype == 0x05:
                try:
                    s = instr.operands[2:].decode("utf-8", errors="replace") if len(instr.operands) > 2 else ""
                    if s:
                        string_consts.append(s)
                except Exception:
                    pass

        return _json_content({
            "instruction_count": len(ncs),
            "code_size":         ncs.code_size,
            "opcode_histogram":  dict(opcode_counts.most_common()),
            "unique_action_ids": sorted(set(action_ids)),
            "jump_targets":      sorted(set(jump_targets)),
            "string_constants":  string_consts[:50],  # cap at 50
        })
    except Exception as exc:
        return _json_content({"error": str(exc)})
