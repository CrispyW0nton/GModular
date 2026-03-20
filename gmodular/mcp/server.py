"""GModular MCP Server — Model Context Protocol server for KotOR modding.

Exposes GModular's full suite of KotOR tools to any MCP-compatible client
(Claude Desktop, Cursor, VS Code, etc.) without requiring pykotor.

Transport modes:
    stdio   — default; used by Claude Desktop / Cursor integration
    sse     — Server-Sent Events over HTTP (requires: pip install aiohttp)
    http    — Simple JSON-over-HTTP (no extra deps)

Usage:
    python -m gmodular.mcp.server               # stdio (default)
    python -m gmodular.mcp.server --mode http   # HTTP on 127.0.0.1:6480
    python -m gmodular.mcp.server --mode sse    # SSE on 127.0.0.1:6480

Environment variables for automatic installation detection:
    K1_PATH  /  KOTOR_PATH   — KotOR 1 installation directory
    K2_PATH  /  TSL_PATH     — KotOR 2 / TSL installation directory

MCP Resources (kotor:// URI scheme):
    kotor://k1/resource/{resref}.{ext}       — Resolve K1 resource
    kotor://k2/resource/{resref}.{ext}       — Resolve K2 resource
    kotor://k1/2da/{table}                   — 2DA table as JSON
    kotor://k1/tlk/{strref}                  — TLK string by number
    kotor://k1/walkmesh-diagram/{resref}.wok — Walkmesh validation diagram
    kotor://docs/capabilities                — Tool index + agent onboarding
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

_SERVER_NAME = "GModularMCP"
_SERVER_VERSION = "1.0.0"


# ═══════════════════════════════════════════════════════════════════════════
#  Core request/response logic (transport-independent)
# ═══════════════════════════════════════════════════════════════════════════

async def _handle_request(method: str, params: Dict[str, Any]) -> Any:
    """Route a JSON-RPC method to the appropriate handler."""
    from gmodular.mcp.tools import get_all_tools, handle_tool

    if method == "initialize":
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
                "resources": {},          # kotor:// URI scheme
            },
            "serverInfo": {"name": _SERVER_NAME, "version": _SERVER_VERSION},
        }

    if method == "tools/list":
        tools = get_all_tools()
        return {"tools": tools}

    if method == "tools/call":
        name: str = params.get("name", "")
        arguments: Dict[str, Any] = params.get("arguments", {})
        try:
            result = await handle_tool(name, arguments)
        except ValueError as exc:
            result = {"content": [{"type": "text", "text": json.dumps({"error": str(exc)})}]}
        except Exception as exc:
            log.exception("Tool %s raised unexpected error", name)
            result = {"content": [{"type": "text", "text": json.dumps({"error": str(exc)})}]}
        # Normalise to plain dict if the result is an mcp.types object
        if hasattr(result, "content"):
            content = result.content
            return {
                "content": [
                    {"type": getattr(c, "type", "text"), "text": getattr(c, "text", str(c))}
                    for c in content
                ]
            }
        return result

    # MCP Resources — kotor:// URI scheme
    if method == "resources/list":
        from gmodular.mcp.mcp_resources import list_resources
        resources = await list_resources()
        return {"resources": resources}

    if method == "resources/read":
        uri: str = params.get("uri", "")
        from gmodular.mcp.mcp_resources import read_resource
        try:
            content = await read_resource(uri)
        except ValueError as exc:
            return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": f"Error: {exc}"}]}
        return {"contents": [content]}

    # Notifications / unknown methods — return None (no response)
    return None


# ═══════════════════════════════════════════════════════════════════════════
#  stdio transport
# ═══════════════════════════════════════════════════════════════════════════

async def _run_stdio() -> None:
    """JSON-RPC 2.0 over stdin/stdout (used by Claude Desktop)."""
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    loop = asyncio.get_event_loop()
    await loop.connect_read_pipe(lambda: protocol, sys.stdin.buffer)

    write_transport, _ = await loop.connect_write_pipe(
        asyncio.BaseProtocol, sys.stdout.buffer
    )

    async def _write(obj: Any) -> None:
        data = json.dumps(obj, ensure_ascii=False) + "\n"
        write_transport.write(data.encode())

    log.info("GModularMCP stdio server started")

    while True:
        try:
            line = await reader.readline()
        except Exception:
            break
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        req_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params") or {}

        response = await _handle_request(method, params)
        if response is not None and req_id is not None:
            await _write({"jsonrpc": "2.0", "id": req_id, "result": response})


# ═══════════════════════════════════════════════════════════════════════════
#  HTTP transport (plain JSON-over-HTTP, no extra deps)
# ═══════════════════════════════════════════════════════════════════════════

async def _run_http(host: str = "127.0.0.1", port: int = 6480) -> None:
    """Minimal HTTP/JSON transport using only stdlib asyncio."""
    from aiohttp import web  # type: ignore

    async def _handle(request: web.Request) -> web.Response:
        body = await request.json()
        req_id = body.get("id")
        method = body.get("method", "")
        params = body.get("params") or {}
        response = await _handle_request(method, params)
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
        if response is not None:
            payload["result"] = response
        return web.json_response(payload)

    app = web.Application()
    app.router.add_post("/mcp", _handle)
    app.router.add_get("/mcp", lambda r: web.json_response({"server": _SERVER_NAME, "version": _SERVER_VERSION}))

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("GModularMCP HTTP server on http://%s:%d/mcp", host, port)
    print(f"GModularMCP HTTP server started: http://{host}:{port}/mcp", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


# ═══════════════════════════════════════════════════════════════════════════
#  Fallback HTTP transport (stdlib only — no aiohttp)
# ═══════════════════════════════════════════════════════════════════════════

async def _run_http_stdlib(host: str = "127.0.0.1", port: int = 6480) -> None:
    """Pure-stdlib asyncio HTTP server (no aiohttp needed)."""

    async def _client_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            # Read request line + headers
            header_lines: List[bytes] = []
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                header_lines.append(line)

            content_length = 0
            for hl in header_lines:
                if hl.lower().startswith(b"content-length:"):
                    content_length = int(hl.split(b":", 1)[1].strip())

            body_bytes = await reader.read(content_length) if content_length else b""

            req_line = header_lines[0].decode() if header_lines else ""
            method_http = req_line.split()[0] if req_line else "GET"

            if method_http == "POST" and body_bytes:
                request_json = json.loads(body_bytes)
                req_id = request_json.get("id")
                rpc_method = request_json.get("method", "")
                params = request_json.get("params") or {}
                rpc_response = await _handle_request(rpc_method, params)
                payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
                if rpc_response is not None:
                    payload["result"] = rpc_response
            else:
                payload = {"server": _SERVER_NAME, "version": _SERVER_VERSION}

            body_out = json.dumps(payload, ensure_ascii=False).encode()
            response = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Access-Control-Allow-Origin: *\r\n"
                + f"Content-Length: {len(body_out)}\r\n".encode()
                + b"\r\n"
                + body_out
            )
            writer.write(response)
            await writer.drain()
        except Exception as exc:
            log.debug("HTTP handler error: %s", exc)
        finally:
            writer.close()

    server = await asyncio.start_server(_client_handler, host, port)
    log.info("GModularMCP HTTP server (stdlib) on http://%s:%d/", host, port)
    print(f"GModularMCP server started: http://{host}:{port}/", flush=True)
    async with server:
        await server.serve_forever()


# ═══════════════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════════════

def main(argv: Optional[List[str]] = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="gmodular-mcp",
        description="GModular MCP server — KotOR modding tools for AI agents",
    )
    parser.add_argument(
        "--mode",
        choices=["stdio", "http", "sse"],
        default="stdio",
        help="Transport (stdio|http|sse); default: stdio",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host for HTTP/SSE (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=6480, help="Bind port for HTTP/SSE (default: 6480)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    if args.mode == "stdio":
        asyncio.run(_run_stdio())
    elif args.mode in ("http", "sse"):
        try:
            asyncio.run(_run_http(host=args.host, port=args.port))
        except ImportError:
            asyncio.run(_run_http_stdlib(host=args.host, port=args.port))
    else:
        raise SystemExit(f"Unknown mode: {args.mode}")


if __name__ == "__main__":
    main()
