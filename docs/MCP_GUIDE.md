# MCP server guide

The Personify MCP server exposes the vault as a first-class data surface
for any [Model Context Protocol](https://modelcontextprotocol.io)-compatible
agent: Claude Desktop, Claude Code, Cursor, MCP Inspector, etc. The agent
discovers the vault's tools and resources automatically — no hand-crafted
queries, no REST schema to learn.

This guide covers v1 of the server. The implementation plan is in
[`MCP_PLAN.md`](MCP_PLAN.md).

## What it exposes

**13 read-only tools** — search, retrieval, graph traversal, vault metadata.
**3 concrete resources** + **3 resource templates** — addressable read-only
documents the agent can pull on demand.

| Category | Tools |
|---|---|
| Search | `search`, `semantic_search` |
| Retrieval | `timeline`, `get_item`, `recent_items`, `recent_runs` |
| Vault metadata | `list_sources`, `list_accounts`, `stats` |
| Graph | `graph_search_entities`, `get_entity`, `entity_neighborhood`, `entity_context` |

| Concrete resource | Returns |
|---|---|
| `vault://stats` | counts + per-source / per-account breakdown |
| `vault://recent` | last 25 items across all sources |
| `vault://sources` | source registry with the accounts that contributed items to each |

| Resource template | Returns |
|---|---|
| `vault://item/{item_id}` | one item (body truncated to 4 KB) |
| `vault://entity/{entity_id}` | one graph entity + aliases + evidence |
| `vault://export/{export_id}` | one raw export with run history |

## Hard rules

- **Read-only.** No tool or resource mutates anything. Ingest, embedding
  computation, graph extraction stay CLI/UI-driven.
- **One vault per server.** To query a different vault, start a different
  server (env var or `--vault` flag).
- **Stdout is reserved for JSON-RPC framing.** Any `print()` or Rich-on-stdout
  in code reachable from a tool corrupts the protocol. The server logs to
  stderr; the boot path has a guard that raises if anything else writes.
- **No PII in logs.** Free-form filter values that could carry user identity
  (queries, account handles) are logged as 8-char SHA-256 fingerprints,
  never raw.

## Quickstart

### 1. Install

The server ships with the main package — no extra install. Just make sure
the editable install picked up the `mcp` dependency:

```bash
pip install -e .
```

### 2. Sanity check

```bash
vault mcp --help
```

Should print the help text. The actual server (`vault mcp` with no args)
runs over stdio and waits for a client; you won't see anything happen until
an agent connects, which is correct.

### 3. Wire up Claude Desktop

Edit your Claude Desktop config (`%APPDATA%\Claude\claude_desktop_config.json`
on Windows; `~/Library/Application Support/Claude/claude_desktop_config.json`
on macOS):

```json
{
  "mcpServers": {
    "personify": {
      "command": "C:\\Users\\you\\Documents\\dev\\personify\\.venv\\Scripts\\python.exe",
      "args": ["-m", "personify.mcp"]
    }
  }
}
```

For multiple vaults, register each as a separate server entry — Claude
Desktop displays them as distinct connections in its UI:

```json
{
  "mcpServers": {
    "personify-personal": {
      "command": "C:\\Users\\you\\Documents\\dev\\personify\\.venv\\Scripts\\python.exe",
      "args": ["-m", "personify.mcp"]
    },
    "personify-code-corpus": {
      "command": "C:\\Users\\you\\Documents\\dev\\personify\\.venv\\Scripts\\python.exe",
      "args": ["-m", "personify.mcp"],
      "env": { "PERSONIFY_VAULT_NAME": "code-corpus" }
    }
  }
}
```

You can use `vault mcp` instead of `python -m personify.mcp` if you'd
rather invoke the CLI entrypoint:

```json
"command": "C:\\Users\\you\\Documents\\dev\\personify\\.venv\\Scripts\\vault.exe",
"args": ["mcp"]
```

Or with a non-default vault from the CLI:

```json
"args": ["--vault", "code-corpus", "mcp"]
```

Restart Claude Desktop after editing the config.

### 4. Use it

In a Claude Desktop conversation, you should see Personify under the
🔧 tools menu. Try asking things like:

- *"Summarize what I was thinking about Personify two weeks ago — pull from
  my chatgpt and claude conversations."*
- *"Who do I email most about this project? Build a graph neighborhood
  from whichever Person entity is most central."*
- *"Find tweets I liked that reference knowledge graphs."*
- *"What sources have data in this vault, and how many items each?"*

Claude figures out which tools to call. You don't have to know the
endpoint names.

## Vault selection

The server inherits its vault config from the same env vars the CLI uses:

- `PERSONIFY_VAULT_NAME` — vault profile name (default: `personal`)
- `PERSONIFY_DB_URL` — direct DB URL override
- `PERSONIFY_VAULT_DIR` — direct filesystem root override

The CLI flag `vault --vault <name> mcp` is equivalent to setting
`PERSONIFY_VAULT_NAME=<name>`.

## Tool reference

Every tool's full signature is visible to the agent at runtime via MCP's
schema introspection. Below is a reference of what each one does and when
to use it.

### Search

#### `search(query, limit=25, source=None) → list[hit]`
Postgres full-text search across `item_text.body`. Returns the most
relevant items as `{id, source, kind, ts, title, snippet, score}`. Best
for keyword and phrase queries.

#### `semantic_search(query, limit=25, source=None) → list[hit]`
pgvector cosine similarity search across embedded chunks. Best for
"find items semantically similar to this idea" rather than exact matches.
Requires the optional embeddings backend (`pip install -e .[embeddings]`).

### Retrieval

#### `timeline(start=None, end=None, source=None, limit=200) → list[item]`
Items with non-null timestamps in a date range, most recent first. Items
without timestamps are excluded. Bound the query with `start`/`end` to
keep payloads small.

#### `get_item(item_id, include_body=False) → item`
Fetch one item by id with metadata, media, tags, and (optionally) body.
**Body is truncated to 4096 chars by default** — set `include_body=True`
only when you need the full text. The response indicates truncation:

```json
{
  "id": 42,
  "title": "...",
  "body": "...",
  "body_truncated": true,
  "body_full_chars": 17204,
  ...
}
```

#### `recent_items(source=None, account=None, kind=None, limit=50, offset=0) → page`
Paginated browse over items with optional filters. Stable ordering
(`ts DESC NULLS LAST, id DESC`) means re-paginating returns each item at
the same offset until new data lands.

#### `recent_runs(limit=10) → list[run_summary]`
Most recent ingestion runs with status, parser, and item counts.

### Vault metadata

#### `list_sources() → list[source]`
Active source registry — every parser slug the vault knows about, with
display labels.

#### `list_accounts() → list[account]`
All accounts that have data ingested.

#### `stats() → vault_summary`
Item / export / run totals plus per-source and per-account breakdowns.

### Graph

#### `graph_search_entities(query, type=None, limit=20) → list[entity_summary]`
Find entities in the knowledge graph by name or alias substring. The
`type` parameter is validated against the live entity-type registry —
unknown types raise. Allowed types include `Project`, `Person`, `Company`,
`Topic`, `Tool`, `Document`, etc.

#### `get_entity(entity_id) → entity_full`
One graph entity with its aliases and item-backed evidence. Use this when
the agent has identified a specific entity and needs the structured
metadata (origin, confidence, source items it was derived from).

#### `entity_neighborhood(entity_id, depth=1) → graph_subgraph`
Walk the graph outward from an entity. Depth capped at 2 to keep payloads
bounded. Returns `{center, nodes, edges}`. Useful for "show me everything
connected to X."

#### `entity_context(entity_id) → context_payload`
LLM-friendly grounding payload combining entity, neighborhood, evidence,
and a small set of suggested follow-up queries. The "give the agent what
it needs to reason about this entity" tool.

## Resource reference

Resources are URI-addressable read-only documents. Concrete resources are
discoverable via `list_resources()`; templates via `list_resource_templates()`.

### Concrete

- `vault://stats` — same payload as `stats` tool, suitable as a one-shot
  context primer.
- `vault://recent` — last 25 items, useful as "what's new in this vault."
- `vault://sources` — source registry. `accounts` per source is the list
  of handles that actually contributed items to *that source*, not a
  global list.

### Templates

- `vault://item/{item_id}` — one item, body truncated to 4 KB. For the
  full body use the `get_item` tool with `include_body=True`.
- `vault://entity/{entity_id}` — same shape as the `get_entity` tool.
- `vault://export/{export_id}` — one raw export with item count and full
  run history.

## Privacy & logging

The server's logs are written to stderr (the agent never sees them). They
are designed to stay useful for debugging without leaking vault content:

- **Query bodies** never appear in logs. Free-text queries (`search`,
  `semantic_search`, `graph_search_entities`) are represented by `q.fp` —
  an 8-char SHA-256 fingerprint of the query plus its length, the limit,
  and (closed-set) source slug.
- **Account filters** in `recent_items` are fingerprinted the same way
  (`account=fp:a3f1b8c4`) — account handles are usually emails and
  treated as PII.
- **Source slugs** and **kinds** are closed-vocabulary and logged raw —
  they're filter primitives, not user content.
- **Errors** use `log.exception(...)` with the same fingerprint. Stack
  traces don't carry the original query because the tool body never
  passes it through.

If you need to correlate a "start" log line with a later "done" or "error",
match them by fingerprint and timestamp.

## Troubleshooting

### Claude Desktop doesn't list Personify tools

Check that the `command` path points at the right Python interpreter and
that the package is installed in that environment. Run the same command
from a terminal:

```bash
"C:\Users\you\Documents\dev\personify\.venv\Scripts\python.exe" -m personify.mcp
```

If it crashes immediately, the error message goes to stderr — Claude
Desktop captures stderr separately from the JSON-RPC stream. On Windows,
look in `%APPDATA%\Claude\logs\mcp*.log`.

### "Unable to parse JSON-RPC" or the server connects but never responds

This is the symptom of stray stdout writes corrupting the protocol. Run
the server with the strict guard enabled.

PowerShell:

```powershell
$env:PERSONIFY_MCP_STRICT_STDOUT = "1"; vault mcp
```

cmd.exe:

```cmd
set PERSONIFY_MCP_STRICT_STDOUT=1 && vault mcp
```

bash / zsh:

```bash
PERSONIFY_MCP_STRICT_STDOUT=1 vault mcp
```

The guard raises on any boot-time stdout write so you can see exactly
which import is the offender. The MCP transport's own JSON-RPC writes
happen *after* the guard window, so legitimate responses still go through.

### `semantic_search` errors

The optional embeddings backend isn't installed. Either install it:

```bash
pip install -e .[embeddings]
```

…or have the agent fall back to `search` (full-text). The error message
includes the install hint.

### Agent gets back a too-long item body and runs out of context

By default `vault://item/{id}` and `get_item(...)` truncate bodies to
4 KB. Make sure the agent isn't passing `include_body=True` unnecessarily.

### Graph type validation rejects my filter

`graph_search_entities` validates `type` against the live registry.
The error message lists the allowed types. The registry is in
`personify/services/graph.py:ENTITY_TYPES`.

## Security model

- **No auth on stdio.** stdio is local-trust by definition — only processes
  on the same machine can connect.
- **No write tools.** The allow-list constant
  (`personify.mcp.server.ALLOWED_TOOL_NAMES`) pins exactly 13 tools, and
  a CI test asserts the registered set never exceeds it. Adding a new
  tool means explicitly adding it to the allow-list, which forces a
  conscious decision.
- **Closed-set validators** on graph type parameters prevent agents from
  probing schema with garbage values.
- **Body truncation** caps the worst case for context-window damage from
  any one item.

A future v1.1 will add an HTTP/SSE transport for remote agent access.
That transport WILL require a bearer token and is gated behind a separate
config — stdio remains the default and the simplest mode.

## Development

### Running tests

```bash
.venv\Scripts\python -m pytest tests/test_mcp.py -q
```

The MCP test suite covers tool registration, the read-only allow-list
contract, schema validation, FastMCP roundtrips, resource templates,
truncation, and the privacy contract (no PII in logs).

### Adding a new tool

1. Add a Pydantic input model to `personify/mcp/schemas.py`.
2. Add a handler decorated with `@mcp.tool()` to `personify/mcp/tools.py`.
3. Service-layer reuse: the handler should be a thin wrapper over a
   function in `personify/services/`. If the logic doesn't exist yet,
   extract it from `web/routes.py` *first* (so HTTP and MCP delegate to
   the same code).
4. Add the tool's name to `ALLOWED_TOOL_NAMES` in
   `personify/mcp/server.py`.
5. Add a unit test that drives the tool via `mcp.call_tool(...)`, plus a
   privacy test if the tool takes any user-controlled string.

### Adding a new resource

1. Add the handler decorated with `@mcp.resource(uri, description=...)`
   to `personify/mcp/resources.py`. URIs with `{name}` placeholders are
   automatically registered as templates.
2. Add a test that drives it via `mcp.read_resource(uri)` (use the
   `_read_resource` helper in `tests/test_mcp.py`).

### Stdout discipline

If your change touches `personify/mcp/`, `personify/services/`, or any
code reachable from a tool body:

- No `print()` calls.
- No `rich.console.Console()` writing to stdout. Rich is fine in CLI
  (separate process); not in the MCP path.
- Use `logging.getLogger("personify.mcp.<module>")` — the entrypoint
  configures the handler to stderr.

The CI suite includes `test_importing_mcp_package_does_not_write_to_stdout`
which catches stray writes at import time.
