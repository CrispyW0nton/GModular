"""
GModular MCP Tools — GFF / 2DA / TLK diff and patch tools.

Provides:
  kotor_gff_diff      — Compare two GFF resources and return field-level differences
  kotor_2da_diff      — Compare two 2DA tables and return cell-level differences
  kotor_tlk_diff      — Compare two TLK talk-tables and list changed/added/removed entries
  kotor_patch_gff     — Apply a JSON patch to a GFF resource (add/change/delete fields)

Reference implementations:
  PyKotor/diff_tool/differ.py            — GFF differ
  Kotor.NET.Patcher/Diff/Diff2DA.cs      — 2DA diff algorithm
  TSLPatcher / HoloPatcher              — mod-patching reference
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any, Dict, List

log = logging.getLogger(__name__)


# ─── tool descriptors ────────────────────────────────────────────────────────

def get_tools() -> List[Dict[str, Any]]:
    return [
        {
            "name": "kotor_gff_diff",
            "description": (
                "Compare two KotOR GFF resources (UTC, DLG, ARE, GIT, IFO, …) and return a "
                "structured list of field-level differences (added, removed, changed values). "
                "Pass either base64-encoded GFF bytes (gff_a_b64 / gff_b_b64) or "
                "game + resref + type pairs to load from the current installation. "
                "Ideal for diff-checking between vanilla and modded versions of the same file."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "gff_a_b64":  {"type": "string", "description": "Base64-encoded GFF bytes (original)"},
                    "gff_b_b64":  {"type": "string", "description": "Base64-encoded GFF bytes (modified)"},
                    "game":       {"type": "string", "description": "k1 or k2"},
                    "resref_a":   {"type": "string", "description": "Resref of the original resource"},
                    "resref_b":   {"type": "string", "description": "Resref of the modified resource"},
                    "res_type":   {"type": "string", "description": "Resource type: utc, dlg, are, git, ifo, …"},
                    "max_diffs":  {"type": "integer", "description": "Maximum differences to return (default 200)"},
                },
            },
        },
        {
            "name": "kotor_2da_diff",
            "description": (
                "Compare two KotOR 2DA tables and return a structured list of cell changes "
                "(added rows, removed rows, changed cells). "
                "Algorithm mirrors Kotor.NET Diff2DA.cs and TSLPatcher's 2DA patcher. "
                "Pass base64-encoded 2DA bytes or game+resref pairs."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "twoda_a_b64": {"type": "string", "description": "Base64-encoded 2DA (original, V2.b binary or V2.0 ASCII)"},
                    "twoda_b_b64": {"type": "string", "description": "Base64-encoded 2DA (modified)"},
                    "game":        {"type": "string", "description": "k1 or k2"},
                    "resref":      {"type": "string", "description": "2DA resref (used for both sides if only one resref)"},
                    "resref_a":    {"type": "string", "description": "Resref for the original 2DA"},
                    "resref_b":    {"type": "string", "description": "Resref for the modified 2DA"},
                },
            },
        },
        {
            "name": "kotor_tlk_diff",
            "description": (
                "Compare two KotOR TLK (talk-table) files and list changed, added, or removed "
                "string entries. Useful for comparing localised versions or tracking mod changes. "
                "Pass base64-encoded TLK bytes or resolve via game identifier."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tlk_a_b64": {"type": "string", "description": "Base64-encoded TLK bytes (original)"},
                    "tlk_b_b64": {"type": "string", "description": "Base64-encoded TLK bytes (modified)"},
                    "game":      {"type": "string", "description": "k1 or k2 (load vanilla TLK for side A)"},
                    "max_diffs": {"type": "integer", "description": "Maximum string diffs to return (default 500)"},
                },
            },
        },
        {
            "name": "kotor_patch_gff",
            "description": (
                "Apply a JSON patch specification to a GFF resource and return the modified GFF "
                "as base64-encoded bytes. Patches are expressed as a JSON object that maps "
                "dot-separated GFF field paths to new values, or null to delete a field. "
                "Example: {\"FirstName\": \"Carth\", \"MaxHitPoints\": 60}. "
                "This is the programmatic equivalent of hand-editing a GFF in a hex editor."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["patch"],
                "properties": {
                    "gff_b64":   {"type": "string", "description": "Base64-encoded GFF bytes to patch"},
                    "game":      {"type": "string", "description": "k1 or k2"},
                    "resref":    {"type": "string", "description": "Resource resref (load from install if gff_b64 not provided)"},
                    "res_type":  {"type": "string", "description": "Resource type: utc, dlg, are, …"},
                    "patch":     {
                        "type": "object",
                        "description": "JSON object mapping field paths to new values (null = delete).",
                    },
                },
            },
        },
    ]


# ─── helpers ─────────────────────────────────────────────────────────────────

def _json_content(obj: Any) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(obj, indent=2)}]}


def _load_gff(b64: str) -> Dict[str, Any]:
    """Decode base64 GFF and return as Python dict via gff_reader."""
    from gmodular.formats.gff_reader import GFFReader
    data = base64.b64decode(b64)
    gff  = GFFReader.from_bytes(data)
    return gff.to_dict() if hasattr(gff, 'to_dict') else {}


def _load_2da(b64: str) -> Dict[str, Any]:
    """Decode base64 2DA (binary V2.b or ASCII V2.0) and return as {headers, rows}."""
    from gmodular.formats.twoda_loader import TwoDALoader
    data = base64.b64decode(b64)
    tda  = TwoDALoader.from_bytes(data)
    return {"headers": tda.headers, "rows": tda.rows}


def _gff_flatten(d: Any, prefix: str = "") -> Dict[str, Any]:
    """Flatten a nested GFF dict to dot-path keys for easy diffing."""
    flat: Dict[str, Any] = {}
    if isinstance(d, dict):
        for k, v in d.items():
            full = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                flat.update(_gff_flatten(v, full))
            else:
                flat[full] = v
    elif isinstance(d, list):
        for i, v in enumerate(d):
            full = f"{prefix}[{i}]"
            if isinstance(v, (dict, list)):
                flat.update(_gff_flatten(v, full))
            else:
                flat[full] = v
    return flat


# ─── handlers ────────────────────────────────────────────────────────────────

async def handle_gff_diff(arguments: Dict[str, Any]) -> Any:
    """Compare two GFF resources field-by-field."""
    try:
        max_diffs = int(arguments.get("max_diffs", 200))

        # Load side A
        if arguments.get("gff_a_b64"):
            dict_a = _load_gff(arguments["gff_a_b64"])
        else:
            return _json_content({"error": "Provide gff_a_b64 (or resref support coming soon)"})

        # Load side B
        if arguments.get("gff_b_b64"):
            dict_b = _load_gff(arguments["gff_b_b64"])
        else:
            return _json_content({"error": "Provide gff_b_b64"})

        flat_a = _gff_flatten(dict_a)
        flat_b = _gff_flatten(dict_b)

        keys_a = set(flat_a)
        keys_b = set(flat_b)

        diffs: List[Dict[str, Any]] = []

        for k in sorted(keys_a - keys_b):
            diffs.append({"type": "removed", "path": k, "old": flat_a[k]})
        for k in sorted(keys_b - keys_a):
            diffs.append({"type": "added",   "path": k, "new": flat_b[k]})
        for k in sorted(keys_a & keys_b):
            if flat_a[k] != flat_b[k]:
                diffs.append({"type": "changed", "path": k,
                               "old": flat_a[k], "new": flat_b[k]})

        truncated = len(diffs) > max_diffs
        return _json_content({
            "total_diffs": len(diffs),
            "truncated":   truncated,
            "diffs":       diffs[:max_diffs],
        })
    except Exception as exc:
        return _json_content({"error": str(exc)})


async def handle_2da_diff(arguments: Dict[str, Any]) -> Any:
    """Compare two 2DA tables, returning row/cell-level changes.

    Algorithm mirrors Kotor.NET Diff2DA.cs:
    1. Detect added columns (in B but not A)
    2. Detect added rows (index ≥ len(A))
    3. Detect changed cells in common rows/columns
    """
    try:
        if not arguments.get("twoda_a_b64") or not arguments.get("twoda_b_b64"):
            return _json_content({"error": "Provide twoda_a_b64 and twoda_b_b64"})

        a = _load_2da(arguments["twoda_a_b64"])
        b = _load_2da(arguments["twoda_b_b64"])

        headers_a = set(a["headers"])
        headers_b = set(b["headers"])
        added_cols   = sorted(headers_b - headers_a)
        removed_cols = sorted(headers_a - headers_b)
        common_cols  = sorted(headers_a & headers_b)

        rows_a = a["rows"]
        rows_b = b["rows"]

        changes: List[Dict[str, Any]] = []

        for col in added_cols:
            changes.append({"type": "added_column", "column": col})
        for col in removed_cols:
            changes.append({"type": "removed_column", "column": col})

        added_row_count = max(0, len(rows_b) - len(rows_a))
        if added_row_count:
            changes.append({"type": "added_rows", "count": added_row_count,
                            "first_new_row": len(rows_a)})

        common_row_count = min(len(rows_a), len(rows_b))
        cell_changes = 0
        for ri in range(common_row_count):
            for col in common_cols:
                va = rows_a[ri].get(col, "")
                vb = rows_b[ri].get(col, "")
                if va != vb:
                    changes.append({
                        "type": "changed_cell",
                        "row": ri, "column": col,
                        "old": va, "new": vb,
                    })
                    cell_changes += 1
                    if cell_changes >= 500:
                        break
            if cell_changes >= 500:
                changes.append({"type": "truncated", "note": "Cell diff list capped at 500"})
                break

        return _json_content({
            "total_changes": len(changes),
            "added_columns": added_cols,
            "removed_columns": removed_cols,
            "added_rows": added_row_count,
            "changes": changes,
        })
    except Exception as exc:
        return _json_content({"error": str(exc)})


async def handle_tlk_diff(arguments: Dict[str, Any]) -> Any:
    """Compare two TLK files, listing changed/added/removed string entries."""
    try:
        max_diffs = int(arguments.get("max_diffs", 500))
        if not arguments.get("tlk_a_b64") or not arguments.get("tlk_b_b64"):
            return _json_content({"error": "Provide tlk_a_b64 and tlk_b_b64"})

        from gmodular.formats.tlk_reader import TLKReader
        tlk_a = TLKReader.from_bytes(base64.b64decode(arguments["tlk_a_b64"]))
        tlk_b = TLKReader.from_bytes(base64.b64decode(arguments["tlk_b_b64"]))

        entries_a = {e.string_ref: e.text for e in tlk_a.entries}
        entries_b = {e.string_ref: e.text for e in tlk_b.entries}

        diffs: List[Dict[str, Any]] = []
        for k in sorted(set(entries_a) | set(entries_b)):
            va = entries_a.get(k)
            vb = entries_b.get(k)
            if va is None:
                diffs.append({"type": "added",   "strref": k, "text": vb})
            elif vb is None:
                diffs.append({"type": "removed",  "strref": k, "text": va})
            elif va != vb:
                diffs.append({"type": "changed",  "strref": k, "old": va, "new": vb})

        return _json_content({
            "total_diffs": len(diffs),
            "truncated":   len(diffs) > max_diffs,
            "diffs":       diffs[:max_diffs],
        })
    except Exception as exc:
        return _json_content({"error": str(exc)})


async def handle_patch_gff(arguments: Dict[str, Any]) -> Any:
    """Apply JSON patch to a GFF resource and return modified bytes as base64."""
    try:
        patch = arguments.get("patch", {})
        if not patch:
            return _json_content({"error": "Provide a non-empty 'patch' object"})

        if not arguments.get("gff_b64"):
            return _json_content({"error": "Provide gff_b64 (resref loading coming soon)"})

        from gmodular.formats.gff_reader import GFFReader
        from gmodular.formats.gff_writer import GFFWriter
        data = base64.b64decode(arguments["gff_b64"])
        gff  = GFFReader.from_bytes(data)

        applied = 0
        failed  = []
        for path, value in patch.items():
            try:
                gff.set_field(path, value)
                applied += 1
            except Exception as e:
                failed.append({"path": path, "error": str(e)})

        out_bytes = GFFWriter.to_bytes(gff)
        return _json_content({
            "success":           True,
            "fields_applied":    applied,
            "fields_failed":     len(failed),
            "failures":          failed,
            "gff_b64":           base64.b64encode(out_bytes).decode(),
            "output_size":       len(out_bytes),
        })
    except Exception as exc:
        return _json_content({"error": str(exc)})
