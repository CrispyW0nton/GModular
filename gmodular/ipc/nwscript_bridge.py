"""
GModular — NWScript Compile/Decompile IPC Bridge (GhostScripter)
================================================================

Provides a clean Python interface to NWScript toolchain executables:
  - nwnnsscomp  (official BioWare/Odyssey compiler, Windows)
  - nwnnssdecomp / DeNCS (community decompilers)
  - PyKotor's built-in Python NssParser + NCS writer (cross-platform,
    no external binary required)

Architecture (mirrors PyKotor resource/formats/ncs/compilers.py):
──────────────────────────────────────────────────────────────────
  NWSScriptBridge          ← main façade (process management + dispatch)
    ├── _InProcessCompiler  ← pure-Python NSS→NCS via PLY yacc (optional dep)
    ├── _ExternalCompiler   ← subprocess wrapper (nwnnsscomp, nwnnssdecomp)
    └── _DecompilerFallback ← NCS disassembly when no decompiler available

MCP tools exposed:
  kotor_compile_nss   — NSS source → NCS bytes  (base64)
  kotor_decompile_ncs — NCS bytes → NSS source  (best-effort)
  kotor_nss_check     — Validate NSS syntax without compiling to NCS
  kotor_nss_format    — Format/indent NSS source code

References
----------
PyKotor  Libraries/PyKotor/src/pykotor/resource/formats/ncs/compilers.py
PyKotor  Libraries/PyKotor/src/pykotor/resource/formats/ncs/compiler/parser.py
Kotor.NET Kotor.NET.Compiler/NSSCompiler.cs
Wiki      wiki/NSS-File-Format.md
Wiki      wiki/NWNNSSCOMP-Command-Line-Reference.md
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# ── NCS magic / header constants ─────────────────────────────────────────────
NCS_MAGIC   = b"NCS V1.0"
NCS_MAGIC_BYTE = 0x42        # 'B' — fixed type byte in every NCS header
NCS_HEADER_SIZE = 13          # 8 (magic) + 1 (type) + 4 (code_size)

# ── Known compiler binary names ──────────────────────────────────────────────
_COMPILER_NAMES = [
    "nwnnsscomp", "nwnnsscomp.exe",
    "nwscript_compiler", "nwscriptcomp",
]
_DECOMPILER_NAMES = [
    "dencs", "dencs.exe", "nwnnssdecomp", "nwnnssdecomp.exe",
]


# ═══════════════════════════════════════════════════════════════════════════
#  Result dataclass
# ═══════════════════════════════════════════════════════════════════════════

class CompileResult:
    """Result of a compile or decompile operation."""
    __slots__ = ("success", "ncs_bytes", "nss_source", "errors",
                 "warnings", "method", "elapsed_ms")

    def __init__(self):
        self.success:    bool          = False
        self.ncs_bytes:  Optional[bytes] = None
        self.nss_source: Optional[str]   = None
        self.errors:     List[str]       = []
        self.warnings:   List[str]       = []
        self.method:     str             = "unknown"
        self.elapsed_ms: float           = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "success":    self.success,
            "method":     self.method,
            "errors":     self.errors,
            "warnings":   self.warnings,
            "elapsed_ms": self.elapsed_ms,
        }
        if self.ncs_bytes is not None:
            d["ncs_b64"]  = base64.b64encode(self.ncs_bytes).decode()
            d["ncs_size"] = len(self.ncs_bytes)
        if self.nss_source is not None:
            d["nss_source"]  = self.nss_source
            d["source_lines"] = self.nss_source.count("\n") + 1
        return d


# ═══════════════════════════════════════════════════════════════════════════
#  Bridge
# ═══════════════════════════════════════════════════════════════════════════

class NWSScriptBridge:
    """Compile .nss → .ncs and decompile .ncs → .nss.

    Usage::

        bridge = NWSScriptBridge(compiler_path="/path/to/nwnnsscomp",
                                 game_root="/path/to/KotOR1")
        result = bridge.compile(nss_source="void main() {}")
        if result.success:
            print(len(result.ncs_bytes), "bytes of NCS")

    All methods are synchronous; the async MCP handlers wrap them in
    ``asyncio.to_thread``.
    """

    def __init__(
        self,
        compiler_path:   Optional[str] = None,
        decompiler_path: Optional[str] = None,
        game_root:       Optional[str] = None,
        include_dirs:    Optional[List[str]] = None,
    ):
        self.compiler_path   = compiler_path   or _find_binary(_COMPILER_NAMES)
        self.decompiler_path = decompiler_path or _find_binary(_DECOMPILER_NAMES)
        self.game_root       = Path(game_root) if game_root else None
        self.include_dirs    = [Path(d) for d in (include_dirs or [])]

    # ── Compile ──────────────────────────────────────────────────────────

    def compile(self, nss_source: str, resref: str = "script") -> CompileResult:
        """Compile NWScript source to NCS bytecode.

        Tries in order:
        1. External compiler (nwnnsscomp) if available.
        2. Pure-Python PLY-based compiler (if ply is installed).
        3. Returns error.
        """
        import time
        t0 = time.monotonic()
        result = CompileResult()

        if self.compiler_path:
            result = self._compile_external(nss_source, resref)
        else:
            result = self._compile_inprocess(nss_source, resref)

        result.elapsed_ms = (time.monotonic() - t0) * 1000
        return result

    def _compile_external(self, source: str, resref: str) -> CompileResult:
        result = CompileResult()
        result.method = f"external:{Path(self.compiler_path).name}"  # type: ignore[arg-type]
        with tempfile.TemporaryDirectory() as tmp:
            nss_path = Path(tmp) / f"{resref}.nss"
            ncs_path = Path(tmp) / f"{resref}.ncs"
            nss_path.write_text(source, encoding="utf-8")

            args = [str(self.compiler_path), "-c", str(nss_path), "-o", str(ncs_path)]
            if self.game_root:
                args += ["-r", str(self.game_root)]
            for inc in self.include_dirs:
                args += ["-i", str(inc)]

            try:
                proc = subprocess.run(
                    args, capture_output=True, text=True, timeout=30
                )
                result.errors   = [l for l in proc.stderr.splitlines() if l.strip()]
                result.warnings = [l for l in proc.stdout.splitlines()
                                   if "warning" in l.lower()]
                if ncs_path.exists():
                    result.ncs_bytes = ncs_path.read_bytes()
                    result.success   = True
                else:
                    result.errors.append("Compiler produced no output file.")
            except FileNotFoundError:
                result.errors.append(f"Compiler not found: {self.compiler_path}")
            except subprocess.TimeoutExpired:
                result.errors.append("Compiler timed out (>30 s)")
            except Exception as e:
                result.errors.append(str(e))
        return result

    def _compile_inprocess(self, source: str, resref: str) -> CompileResult:
        """Attempt pure-Python compilation via PLY grammar (requires ply package)."""
        result = CompileResult()
        result.method = "inprocess:ply"
        try:
            # Try to import PyKotor's NssParser (optional heavy dep)
            from pykotor.resource.formats.ncs.compiler.parser import NssParser  # type: ignore[import]
            from pykotor.resource.formats.ncs.compiler.lexer  import NssLexer   # type: ignore[import]
            parser = NssParser(NssLexer(), [], [], [], resref)
            ncs = parser.compile(source)
            if ncs:
                from pykotor.resource.formats.ncs.io_ncs import NCSBinaryWriter  # type: ignore[import]
                result.ncs_bytes = NCSBinaryWriter.to_bytes(ncs)
                result.success   = True
            else:
                result.errors.append("PyKotor parser returned no output.")
        except ImportError:
            result.errors.append(
                "No NWScript compiler available. Install nwnnsscomp or PyKotor "
                "with PLY: pip install pykotor[compiler]"
            )
        except Exception as e:
            result.errors.append(str(e))
        return result

    # ── Decompile ─────────────────────────────────────────────────────────

    def decompile(self, ncs_bytes: bytes, resref: str = "script") -> CompileResult:
        """Decompile NCS bytecode to NWScript source (best-effort).

        Tries in order:
        1. External decompiler (dencs / nwnnssdecomp).
        2. PyKotor's decompiler (optional).
        3. Falls back to annotated disassembly.
        """
        import time
        t0 = time.monotonic()
        result = CompileResult()

        if self.decompiler_path:
            result = self._decompile_external(ncs_bytes, resref)
        else:
            result = self._decompile_inprocess(ncs_bytes, resref)

        if not result.success:
            # Always fall back to disassembly so the caller gets *something*
            dis = self._disassemble(ncs_bytes)
            result.nss_source = dis
            result.warnings.append(
                "Full decompilation unavailable — disassembly provided instead.")
            result.success  = True
            result.method  += "+disasm_fallback"

        result.elapsed_ms = (time.monotonic() - t0) * 1000
        return result

    def _decompile_external(self, ncs_bytes: bytes, resref: str) -> CompileResult:
        result = CompileResult()
        result.method = f"external:{Path(self.decompiler_path).name}"  # type: ignore[arg-type]
        with tempfile.TemporaryDirectory() as tmp:
            ncs_path = Path(tmp) / f"{resref}.ncs"
            nss_path = Path(tmp) / f"{resref}.nss"
            ncs_path.write_bytes(ncs_bytes)
            args = [str(self.decompiler_path), str(ncs_path)]
            try:
                proc = subprocess.run(
                    args, capture_output=True, text=True, timeout=30)
                if nss_path.exists():
                    result.nss_source = nss_path.read_text(encoding="utf-8",
                                                           errors="replace")
                    result.success    = True
                else:
                    result.errors.append("Decompiler produced no output file.")
            except Exception as e:
                result.errors.append(str(e))
        return result

    def _decompile_inprocess(self, ncs_bytes: bytes, resref: str) -> CompileResult:
        result = CompileResult()
        result.method = "inprocess:pykotor"
        try:
            from pykotor.resource.formats.ncs.decompiler import (  # type: ignore[import]
                NCSDecompiler,
            )
            from pykotor.resource.formats.ncs.io_ncs import NCSBinaryReader  # type: ignore[import]
            ncs  = NCSBinaryReader.from_bytes(ncs_bytes)
            dec  = NCSDecompiler(ncs)
            result.nss_source = dec.decompile()
            result.success    = True
        except ImportError:
            result.errors.append("PyKotor decompiler not available.")
        except Exception as e:
            result.errors.append(str(e))
        return result

    def _disassemble(self, ncs_bytes: bytes) -> str:
        """Return a human-readable NCS disassembly (always available)."""
        from gmodular.formats.kotor_formats import read_ncs, NCSData
        try:
            ncs: NCSData = read_ncs(ncs_bytes)
            return ncs.disassembly_text()
        except Exception as e:
            return f"// disassembly error: {e}"

    # ── Syntax check ──────────────────────────────────────────────────────

    def check_syntax(self, nss_source: str) -> Dict[str, Any]:
        """Validate NSS syntax.  Returns {valid, errors, warnings}."""
        errors: List[str] = []
        warnings: List[str] = []

        # Minimal syntactic checks we can do without a full compiler:
        # 1. Balanced braces
        depth = 0
        for i, ch in enumerate(nss_source):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth < 0:
                    errors.append(f"Unexpected '}}' (position {i})")
                    depth = 0
        if depth > 0:
            errors.append(f"Unclosed '{{' — {depth} brace(s) not closed")

        # 2. Try external compiler in --syntax-only mode
        if self.compiler_path and not errors:
            result = self.compile(nss_source, "syntax_check")
            errors   += result.errors
            warnings += result.warnings

        return {
            "valid":    len(errors) == 0,
            "errors":   errors,
            "warnings": warnings,
        }

    # ── Formatter ─────────────────────────────────────────────────────────

    @staticmethod
    def format_nss(source: str) -> str:
        """Basic auto-indenter for NWScript source.

        Applies:
        - 4-space indentation inside braces
        - Blank line before function definitions (void/int/float/string/object)
        - Normalises line endings to LF

        This is a lightweight regex-based formatter, not a full AST pretty-printer.
        """
        source = source.replace("\r\n", "\n").replace("\r", "\n")
        lines  = source.split("\n")
        out    = []
        depth  = 0
        func_re = re.compile(
            r"^\s*(void|int|float|string|object|vector|struct)\s+\w+\s*\(")

        for raw in lines:
            stripped = raw.strip()
            if not stripped:
                out.append("")
                continue
            # Closing brace(s) dedent before this line
            closing = stripped.count("}") - stripped.count("{")
            # (only dedent if line starts with })
            if stripped.startswith("}"):
                depth = max(0, depth - stripped[:len(stripped)].count("}"))
            indent = "    " * depth
            # Blank line before top-level function definitions
            if func_re.match(stripped) and depth == 0 and out and out[-1].strip():
                out.append("")
            out.append(indent + stripped)
            # Opening braces indent next line
            if stripped.endswith("{") and not stripped.startswith("}"):
                depth += 1

        return "\n".join(out) + "\n"


# ── Helper ────────────────────────────────────────────────────────────────────

def _find_binary(names: List[str]) -> Optional[str]:
    """Search PATH for the first matching binary name."""
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return None


# ── Global singleton ──────────────────────────────────────────────────────────

_bridge: Optional[NWSScriptBridge] = None


def get_nwscript_bridge(**kw) -> NWSScriptBridge:
    """Return the global NWSScriptBridge (lazy-init)."""
    global _bridge
    if _bridge is None:
        _bridge = NWSScriptBridge(**kw)
    return _bridge


# ═══════════════════════════════════════════════════════════════════════════
#  MCP tool descriptors
# ═══════════════════════════════════════════════════════════════════════════

def get_tools() -> List[Dict[str, Any]]:
    return [
        {
            "name": "kotor_compile_nss",
            "description": (
                "Compile a NWScript (.nss) source string to KotOR NCS bytecode. "
                "Returns the compiled NCS as base64-encoded bytes. "
                "Uses nwnnsscomp if available on PATH, otherwise falls back to the "
                "PyKotor pure-Python compiler (requires ply). "
                "Set compiler_path / decompiler_path / game_root to override defaults."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["nss_source"],
                "properties": {
                    "nss_source":    {"type": "string",
                                     "description": "NWScript source code"},
                    "resref":        {"type": "string",
                                     "description": "Script resref (default 'script')"},
                    "compiler_path": {"type": "string",
                                     "description": "Path to nwnnsscomp binary (optional)"},
                    "game_root":     {"type": "string",
                                     "description": "KotOR game directory (for includes)"},
                    "include_dirs":  {"type": "array", "items": {"type": "string"},
                                     "description": "Extra include directories"},
                },
            },
        },
        {
            "name": "kotor_decompile_ncs",
            "description": (
                "Decompile a KotOR NCS bytecode file back to NWScript source. "
                "Pass base64-encoded NCS bytes via ncs_b64. "
                "Uses DeNCS / nwnnssdecomp if on PATH, then PyKotor's decompiler, "
                "then falls back to annotated disassembly. "
                "Full decompilation is best-effort; complex scripts may only produce disassembly."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ncs_b64":         {"type": "string",
                                       "description": "Base64-encoded NCS bytes"},
                    "game":            {"type": "string",
                                       "description": "k1 or k2"},
                    "resref":          {"type": "string",
                                       "description": "Script resref (to load from install)"},
                    "decompiler_path": {"type": "string",
                                       "description": "Path to dencs/nwnnssdecomp (optional)"},
                },
            },
        },
        {
            "name": "kotor_nss_check",
            "description": (
                "Validate NWScript (.nss) source for syntax errors without producing NCS output. "
                "Performs brace-balance check and optionally invokes the external compiler "
                "in syntax-only mode. Returns {valid, errors, warnings}."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["nss_source"],
                "properties": {
                    "nss_source":    {"type": "string"},
                    "compiler_path": {"type": "string"},
                },
            },
        },
        {
            "name": "kotor_nss_format",
            "description": (
                "Auto-format / indent a NWScript source file. "
                "Applies 4-space indentation, normalises line endings, "
                "and inserts blank lines before top-level function definitions."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["nss_source"],
                "properties": {
                    "nss_source": {"type": "string"},
                },
            },
        },
    ]


# ═══════════════════════════════════════════════════════════════════════════
#  MCP handlers
# ═══════════════════════════════════════════════════════════════════════════

def _jc(obj: Any) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(obj, indent=2)}]}


async def handle_compile_nss(arguments: Dict[str, Any]) -> Any:
    try:
        bridge = NWSScriptBridge(
            compiler_path=arguments.get("compiler_path"),
            game_root=arguments.get("game_root"),
            include_dirs=arguments.get("include_dirs"),
        )
        result = bridge.compile(
            nss_source=arguments["nss_source"],
            resref=arguments.get("resref", "script"),
        )
        return _jc(result.to_dict())
    except Exception as e:
        return _jc({"error": str(e)})


async def handle_decompile_ncs(arguments: Dict[str, Any]) -> Any:
    try:
        bridge = NWSScriptBridge(
            decompiler_path=arguments.get("decompiler_path"),
        )
        if arguments.get("ncs_b64"):
            ncs_bytes = base64.b64decode(arguments["ncs_b64"])
        else:
            # Try to load from game install
            from gmodular.engine.resource_manager import ResourceManager
            rm = ResourceManager.instance()
            game = arguments.get("game", "k1")
            resref = arguments.get("resref", "")
            if not resref:
                return _jc({"error": "Provide ncs_b64 or resref"})
            ncs_bytes = rm.get_resource(game, resref, "ncs")
            if not ncs_bytes:
                return _jc({"error": f"Script not found: {resref}"})

        result = bridge.decompile(ncs_bytes, arguments.get("resref", "script"))
        return _jc(result.to_dict())
    except Exception as e:
        return _jc({"error": str(e)})


async def handle_nss_check(arguments: Dict[str, Any]) -> Any:
    try:
        bridge = NWSScriptBridge(
            compiler_path=arguments.get("compiler_path"),
        )
        return _jc(bridge.check_syntax(arguments["nss_source"]))
    except Exception as e:
        return _jc({"error": str(e)})


async def handle_nss_format(arguments: Dict[str, Any]) -> Any:
    try:
        formatted = NWSScriptBridge.format_nss(arguments["nss_source"])
        return _jc({"formatted": formatted,
                    "original_lines": arguments["nss_source"].count("\n") + 1,
                    "formatted_lines": formatted.count("\n") + 1})
    except Exception as e:
        return _jc({"error": str(e)})
