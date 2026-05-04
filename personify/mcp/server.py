"""FastMCP instance + tool registration.

Tools live in :mod:`personify.mcp.tools` and resources in
:mod:`personify.mcp.resources`. Importing this module triggers their
registration via decorator side-effects.

The server is read-only: write/mutation tools are forbidden. The
``ALLOWED_TOOL_NAMES`` constant pins the public surface so a regression test
can assert nothing destructive ever sneaks in.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "personify",
    instructions=(
        "Read-only access to the user's Personify personal data vault. "
        "Tools cover full-text and semantic search across ingested items, "
        "knowledge-graph traversal (entities, relationships, evidence), and "
        "vault metadata (sources, accounts, stats). No write or mutation "
        "tools exist; all data was ingested by the vault owner ahead of time."
    ),
)

# Importing tools / resources triggers the @mcp.tool() and @mcp.resource()
# decorator side-effects that register them with the FastMCP server.
from personify.mcp import tools as _tools  # noqa: E402, F401
from personify.mcp import resources as _resources  # noqa: E402, F401

# Allow-list pinned so the read-only contract is testable. Any new tool must
# be added here explicitly; the test suite asserts the registered tool set
# never exceeds this list.
ALLOWED_TOOL_NAMES: frozenset[str] = frozenset(
    {
        # Search & retrieval
        "search",
        "semantic_search",
        "timeline",
        "get_item",
        "recent_items",
        "recent_runs",
        # Vault metadata
        "list_sources",
        "list_accounts",
        "stats",
        # Graph
        "graph_search_entities",
        "get_entity",
        "entity_neighborhood",
        "entity_context",
    }
)
