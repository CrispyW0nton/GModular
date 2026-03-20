"""GModular MCP — Model Context Protocol server for KotOR modding workflows.

Provides AI agents with structured, context-rich tools for interacting with
KotOR / TSL game installations, resource archives, GFF data, 2DA tables,
TLK strings, walkmeshes, MDL models, module layouts, and more.

Mirrors the KotorMCP interface (OldRepublicDevs/PyKotor) but implemented
entirely on top of GModular's own pure-Python parsers — no pykotor dependency.

Public entry points:
    gmodular.mcp.server   — asyncio MCP server (stdio / SSE / HTTP)
    gmodular.mcp.run()    — convenience launcher used by CLI
"""

from __future__ import annotations

__version__ = "1.0.0"
