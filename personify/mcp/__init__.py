"""Model Context Protocol server for Personify.

Exposes the vault as a first-class data surface for any MCP-compatible agent
(Claude Desktop, Claude Code, Cursor, …). Read-only by design: tools cover
search, retrieval, graph traversal, and stats — no ingest, no mutation.

Run with:

    python -m personify.mcp                    # default vault, stdio
    PERSONIFY_VAULT_NAME=foo python -m personify.mcp

CRITICAL: stdio transport uses stdout for JSON-RPC framing. Anything written
to stdout from this package or anything it imports will corrupt the protocol.
All logging must go to stderr or a file. See `personify/mcp/__main__.py` for
the boot-time setup.
"""
from __future__ import annotations

# Re-export the FastMCP instance so tests / other entry points can import it
# without going through __main__ (which has side effects on stdout/logging).
from personify.mcp.server import mcp

__all__ = ["mcp"]
