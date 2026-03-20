"""GModular MCP — response formatting helpers.

Mirrors KotorMCP/utils/formatting.py.  Produces JSON CallToolResult objects
with automatic truncation when the payload exceeds MAX_RESPONSE_CHARS.
"""
from __future__ import annotations

import json
from typing import Any

MAX_RESPONSE_CHARS = 25_000
CONTINUATION_HINT = (
    "Response exceeded limit; use offset/limit or filters to request a smaller result."
)


def _build_tool_result(text: str) -> dict:
    """Return a dict that matches mcp.types.CallToolResult structure
    (works whether or not the 'mcp' package is installed)."""
    try:
        from mcp import types as mcp_types  # optional dependency
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=text)]
        )
    except ImportError:
        return {"content": [{"type": "text", "text": text}]}


def json_content(payload: Any, max_chars: int = MAX_RESPONSE_CHARS) -> Any:
    """Serialise *payload* to JSON and return an MCP CallToolResult.

    Truncates with a hint object when the serialised length exceeds *max_chars*.
    """
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if len(text) <= max_chars:
        return _build_tool_result(text)

    wrapper: dict[str, Any] = {
        "truncated": True,
        "continuation_hint": CONTINUATION_HINT,
        "truncated_preview": text[: max_chars - 200],
    }
    out = json.dumps(wrapper, ensure_ascii=False, indent=2)
    if len(out) > max_chars:
        out = out[: max_chars - 80] + "\n  ... (output truncated)"
    return _build_tool_result(out)


def error_content(message: str) -> Any:
    """Return an MCP CallToolResult wrapping an error message."""
    payload = {"error": message}
    return _build_tool_result(json.dumps(payload, ensure_ascii=False, indent=2))
