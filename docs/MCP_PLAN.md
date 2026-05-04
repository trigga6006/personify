# MCP server — implementation plan

Status: **draft for review**
Owner: Claude (build), Codex (review)

## Goal

Expose the personal vault as a first-class data surface for any MCP-compatible
agent (Claude Desktop, Claude Code, Cursor, etc.). The agent should be able to
discover the vault's tools automatically rather than learning the FastAPI REST
API by hand.

This is the highest-leverage missing piece in the stack — it's the difference
between "the user can ask agents to query this" and "agents can query this on
their own."

## Non-goals (v1)

- **No write tools.** Ingest, embed, graph extraction stay CLI/UI-driven. An
  agent should not be able to mutate the vault. Read-only is the safety floor
  for v1.
- **No multi-vault routing inside one server.** One server = one vault. To
  query a different vault, start a different server.
- **No MCP "prompts" feature.** Tools and resources are sufficient.
- **No auth on stdio.** stdio implies local trust. HTTP transport gets a bearer
  token.

## Architecture

```
personify/
  mcp/
    __init__.py
    server.py          # MCP Server instance, tool/resource registry, lifespan hook
    schemas.py         # Pydantic input/output models for every tool
    tools.py           # Tool implementations — thin wrappers over services
    resources.py       # Resource handlers (vault://item/{id}, vault://entity/{id}, …)
    transports.py      # stdio entrypoint, optional HTTP/SSE setup
    __main__.py        # `python -m personify.mcp` → stdio server
```

Why a sibling module of `personify.api`, not under it: the FastAPI app and the
MCP server are two different *protocols*; they share the **service layer**
(`personify.services.*`, `personify.db`, `personify.models`) but should not
share routing or transport concerns. Mounting an HTTP MCP transport on the
existing FastAPI app is fine — it'll live in `transports.py` as a sub-app.

### Service-layer reuse

Every tool delegates to existing service functions. **No new business logic
in the MCP layer.** This guarantees CLI/UI/MCP behave identically:

| Tool                   | Delegates to                                              |
|------------------------|-----------------------------------------------------------|
| `search`               | `services.search.text_search` *(already extracted)*       |
| `semantic_search`      | `services.search.semantic_search` *(already extracted)*   |
| `timeline`             | `services.items.list_timeline` *(extract first — P2)*     |
| `get_item`             | `services.items.get_item_full` *(extract first — P2)*     |
| `recent_items`         | `services.items.list_items` *(extract first — P2)*        |
| `recent_runs`          | `services.runs.list_recent_runs` *(extract first — P2)*   |
| `list_sources`         | direct query on `Source`                                  |
| `list_accounts`        | direct query on `Account`                                 |
| `stats`                | `services.stats.collect_stats` *(already extracted)*      |
| `graph_search_entities`| `services.graph.search_entities` *(already extracted)*    |
| `get_entity`           | `services.graph.get_entity_full` *(extract first — P2)*   |
| `entity_neighborhood`  | `services.graph.get_entity_neighborhood` *(extracted)*    |
| `entity_context`       | `services.graph.entity_context` *(extract first — P2)*    |

**Service extraction is a hard prerequisite** (Codex P2): every "extract first"
function above must land before the MCP tool that depends on it. The shape of
each service function is dictated by what *both* `web/routes.py` and the MCP
tool need, so they share one implementation from day one rather than the MCP
behavior drifting from the HTTP behavior over time.

After extraction, the corresponding HTTP route in `web/routes.py` is also
updated to delegate to the new service function in the same PR. Net effect:
zero observable change to the HTTP API, plus a clean call site for the MCP
tool to reuse.

### Transport: stdio + HTTP

The official Python MCP SDK (`mcp`) supports both. v1 ships **stdio** because
that's what Claude Desktop uses. v1.1 mounts the same FastMCP instance on
FastAPI via the SDK's streamable-HTTP transport for remote agent access.

**Build with `FastMCP`, not the low-level `Server`** (Codex review). FastMCP
infers tool input schemas from Python type hints + Pydantic models and uses
docstrings as the LLM-facing description. Less plumbing, fewer divergence
points between the schema we write and the schema the agent sees.

```python
# server.py — one FastMCP instance, transport chosen at runtime.
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "personify",
    instructions=(
        "Read-only access to the user's Personify vault: ingested items "
        "across sources, semantic + full-text search, knowledge graph."
    ),
)
# tool/resource registrations live in tools.py / resources.py and import
# this `mcp` to attach themselves via @mcp.tool() / @mcp.resource(...).
```

```python
# __main__.py — stdio entry. NOTHING may be printed to stdout from this
# point on; stdio uses stdout for JSON-RPC framing.
from personify.mcp.server import mcp

if __name__ == "__main__":
    mcp.run()  # FastMCP defaults to stdio
```

```python
# transports.py — optional HTTP mount on FastAPI (v1.1)
from personify.mcp.server import mcp

def mount_mcp(app):
    # bearer-token middleware here, then mount the streamable-http app.
    app.mount("/mcp", mcp.streamable_http_app())
```

### Stdout discipline (P1, Codex review)

**The MCP stdio transport uses stdout for JSON-RPC framing. Anything else on
stdout corrupts the protocol and the client refuses to connect.** This is a
hard requirement, not a preference.

Concrete rules for the `personify.mcp` package and anything it imports:

- All logging via `logging` configured to write to **stderr or a file**, never
  stdout. Set this up explicitly in `__main__.py` before importing the rest of
  the module.
- **No `print()`** in `personify.mcp` or in any service-layer code path
  reachable from a tool. Existing `print()` calls in `personify/services/*`
  must be audited and converted to `logging` before phase 3 lands. (Codex:
  please flag any new `print()` in PRs touching the MCP path.)
- **No `rich.console.Console()` writing to stdout** in those code paths. Rich
  may still be used for the CLI / FastAPI startup banner — separate processes.
- **Disable uvicorn's stdout banner** if/when an HTTP MCP transport is mounted
  inside `mcp.run()`'s lifecycle.
- Every entrypoint installs a `sys.stdout` guard in dev/test that raises if
  anything other than the MCP framer writes to it, so we catch regressions in
  CI rather than at "Claude Desktop won't connect" time.

```python
# __main__.py setup — minimal pattern
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s mcp %(levelname)s %(name)s %(message)s",
)
# Optional defensive guard — only enabled when PERSONIFY_MCP_STRICT_STDOUT=1
if os.environ.get("PERSONIFY_MCP_STRICT_STDOUT") == "1":
    sys.stdout = _StdoutGuard(sys.stdout)  # raises on .write()
```

### Vault selection

The MCP server inherits the vault config from the same `PERSONIFY_*` env vars
the CLI/API use. To run against a non-default vault:

```bash
PERSONIFY_VAULT_NAME=code-corpus python -m personify.mcp
```

Claude Desktop config example:

```json
{
  "mcpServers": {
    "personify-personal": {
      "command": "C:\\Users\\fowle\\Documents\\dev\\personify\\.venv\\Scripts\\python.exe",
      "args": ["-m", "personify.mcp"]
    },
    "personify-code-corpus": {
      "command": "C:\\Users\\fowle\\Documents\\dev\\personify\\.venv\\Scripts\\python.exe",
      "args": ["-m", "personify.mcp"],
      "env": { "PERSONIFY_VAULT_NAME": "code-corpus" }
    }
  }
}
```

One server per vault is simpler to reason about than a `vault` parameter on
every tool, and matches how Claude Desktop renders distinct MCP connections in
its UI.

## Tools (v1)

Every tool gets:
- A Pydantic input model in `schemas.py`
- A docstring that becomes the LLM-facing tool description
- Structured JSON-serializable output
- Errors caught and returned as MCP errors with a useful message

### Search & retrieval

```python
class SearchInput(BaseModel):
    query: str = Field(..., min_length=1, description="Free-text query")
    limit: int = Field(25, ge=1, le=100)
    source: Optional[str] = Field(None, description="Restrict to one source slug, e.g. 'twitter'")

@server.tool()
async def search(input: SearchInput) -> list[dict]:
    """Full-text search across all ingested items. Returns title/snippet/source/ts/id."""
    return text_search(input.query, limit=input.limit, source=input.source)
```

Same shape for `semantic_search` (vector). Wrap the `ImportError` from the
embed backend so the tool returns an MCP error rather than crashing the server.

```python
class TimelineInput(BaseModel):
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    source: Optional[str] = None
    limit: int = Field(200, ge=1, le=1000)

@server.tool()
async def timeline(input: TimelineInput) -> list[dict]: ...
```

```python
class GetItemInput(BaseModel):
    item_id: int = Field(..., description="Numeric Item.id")

@server.tool()
async def get_item(input: GetItemInput) -> dict:
    """Fetch one item by id including body text, media, tags, source metadata."""
```

```python
class RecentItemsInput(BaseModel):
    source: Optional[str] = None
    account: Optional[str] = None
    kind: Optional[str] = None
    limit: int = Field(50, ge=1, le=500)
    offset: int = Field(0, ge=0)

@server.tool()
async def recent_items(input: RecentItemsInput) -> dict: ...
```

### Graph

```python
class EntitySearchInput(BaseModel):
    query: str = Field(..., min_length=1)
    type: Optional[str] = Field(None, description="Restrict to a specific Entity.type (e.g. Person)")
    limit: int = Field(20, ge=1, le=100)

@server.tool()
async def graph_search_entities(input: EntitySearchInput) -> list[dict]: ...

class EntityIdInput(BaseModel):
    entity_id: int

@server.tool()
async def get_entity(input: EntityIdInput) -> dict: ...

class NeighborhoodInput(BaseModel):
    entity_id: int
    depth: int = Field(1, ge=1, le=2)

@server.tool()
async def entity_neighborhood(input: NeighborhoodInput) -> dict: ...

@server.tool()
async def entity_context(input: EntityIdInput) -> dict:
    """Return entity + summary + aliases + neighborhood + evidence — LLM-grounding payload."""
```

### Vault metadata

```python
@server.tool()
async def list_sources() -> list[dict]: ...
@server.tool()
async def list_accounts() -> list[dict]: ...
@server.tool()
async def stats() -> dict: ...
@server.tool()
async def recent_runs(limit: int = 10) -> list[dict]: ...
```

### Total: ~13 tools.

## Resources

Resources are URI-addressable read-only documents. MCP distinguishes two kinds
and Codex (P2) flagged that the plan was conflating them:

- **Concrete resources** appear in `list_resources()`. Discoverable, finite.
- **Resource templates** are URI patterns (`vault://item/{id}`). Discoverable
  via `list_resource_templates()`; the client parameterizes and reads them on
  demand. Templates are the right primitive for "any item by id."

### Concrete resources (small, curated)

These appear in `list_resources()`:

| URI                          | Returns                                                |
|------------------------------|--------------------------------------------------------|
| `vault://stats`              | `stats()` payload                                      |
| `vault://recent`             | last ~25 items across all sources                      |
| `vault://sources`            | source registry summary                                |

### Resource templates (parameterized)

These appear in `list_resource_templates()`:

| URI template                 | Returns                                                |
|------------------------------|--------------------------------------------------------|
| `vault://item/{id}`          | Item summary (title, source, ts, **truncated body**)   |
| `vault://entity/{id}`        | Same shape as `get_entity`                             |
| `vault://export/{id}`        | RawExport summary (id, source, item count, runs)       |

**Body truncation on `vault://item/{id}`** (Codex review answer): default
truncate to 4 KB of body text. The full untruncated body is available via
the `get_item` tool with an explicit `include_body=true` parameter. This
keeps a Gmail with 10 MB of nested quotes from blowing the agent's context
when the agent's just looking for the title and date.

The MCP SDK's FastMCP exposes both as decorators:

```python
@mcp.resource("vault://stats")
def stats_resource() -> dict: ...

@mcp.resource("vault://item/{item_id}")
def item_resource(item_id: int) -> dict:
    return get_item_full(item_id, body_truncate=4096)
```

## Error handling

- **DB errors**: caught at the tool boundary, returned as MCP errors with a
  short reason. The full traceback goes to the server log, never to the agent.
- **Validation errors**: Pydantic raises before the tool body runs; these
  become MCP errors automatically.
- **Empty results**: return `[]` or `{}`; never an error.
- **Embedding backend missing**: `semantic_search` returns an MCP error
  containing the install hint, mirroring the API's 501 response.

Trust boundary note: **no agent input is concatenated into SQL.** Every query
parameter goes through SQLModel/SQLAlchemy bindings. The graph services already
validate `entity_type`/`relationship_type` against a closed set before insert
or query.

## CLI integration

Add a `vault mcp` Typer command:

```python
@app.command("mcp")
def mcp(
    vault: Optional[str] = typer.Option(None, "--vault", "-v"),
) -> None:
    """Run the MCP server over stdio."""
    if vault:
        configure_vault(vault)
        reset_engine()
    from personify.mcp.__main__ import main
    asyncio.run(main())
```

The Claude Desktop config can call either `python -m personify.mcp` or
`vault mcp` — same outcome.

## Tests

`tests/test_mcp.py`:

1. **Server boots** — instantiating `Server` with all tools/resources doesn't
   raise; tool descriptions are non-empty; tool input schemas validate.
2. **Per-tool integration tests** — for each tool, set up a sqlite vault with
   a fixture export, ingest it, then call the tool *directly through the
   registered handler* (not stdio). Assert structured shape + key fields.
3. **End-to-end stdio test** — launch the server as a subprocess, write an
   MCP `initialize` + `list_tools` + `call_tool(search)` over stdio, assert
   the response. Slow-ish, marked `@pytest.mark.slow` and excluded from the
   default `pytest` run.
4. **Schema validation** — bad input rejected with a useful message.
5. **Read-only contract** — assert there are no write/destructive tools
   registered. (Loop through `server.list_tools()` and check names against an
   allow-list constant.)

## Phasing

Each phase ends with passing tests and the server runnable. **Reordered per
Codex P2: service-layer extraction comes first, before tools that depend on
it, so HTTP and MCP share one implementation from day one.**

1. **Skeleton + 1 tool** (~30 min)
   - `personify/mcp/{__init__,server,schemas,tools,__main__}.py`
   - Add `mcp` to `pyproject.toml` deps + lock with the project lockfile
   - FastMCP server with stderr logging + stdout guard
   - Tool: `search` only (already-extracted service, no refactor needed)
   - Test: server boots + search returns results + stdout-clean assertion
2. **Service-layer extraction** (~45 min) — **moved up from phase 6**
   - `services/items.py`: `list_items`, `list_timeline`, `get_item_full`
   - `services/runs.py`: `list_recent_runs`
   - `services/graph.py`: extend with `get_entity_full`, `entity_context`
   - Each extraction lands with: HTTP route delegated, no observable change,
     unit test on the new service function.
3. **Read-only retrieval tools** (~45 min)
   - `semantic_search`, `timeline`, `get_item`, `recent_items`
   - `list_sources`, `list_accounts`, `stats`, `recent_runs`
   - Each tool delegates to the service functions extracted in phase 2.
   - Per-tool unit tests.
4. **Graph tools** (~30 min)
   - `graph_search_entities`, `get_entity`, `entity_neighborhood`, `entity_context`
   - Per-tool tests.
5. **Resources + templates** (~30 min)
   - Concrete: `vault://stats`, `vault://recent`, `vault://sources`
   - Templates: `vault://item/{id}` (truncated body), `vault://entity/{id}`,
     `vault://export/{id}`
6. **CLI integration** (~10 min)
   - `vault mcp` Typer command (writes nothing to stdout in this code path)
7. **Documentation** (~30 min)
   - `docs/MCP_GUIDE.md` — Claude Desktop config, example queries, env vars,
     vault selection
   - README section pointing at the guide
8. **HTTP transport (v1.1, deferred)** (~1 hr, separate PR)
   - Streamable-HTTP transport mounted on FastAPI
   - Bearer-token middleware
   - Test: HTTP `initialize` round-trip

Total v1 (stdio only, phases 1-7): ~3-4 hrs of focused work.

## Resolved decisions (Codex review)

1. ~~`semantic_search` vs `search` shape~~ → **separate tools**. Clearer LLM
   affordances. Input shape is identical so it's not duplication.
2. **Pagination on `recent_items` / `timeline`** — offset/limit is fine for
   v1, but cursor-based would be friendlier if vaults grow. Start with
   offset/limit, revisit when a vault hits >100k items.
3. **Should `entity_context` include suggested follow-up queries?** The HTTP
   API already does (`suggested_queries`). Including them in MCP gives the
   agent more to chew on. Default: yes, ship as-is.
4. **Resource cache headers / size limits.** A `vault://item/{id}` for a
   long email could be megabytes. Truncate body to N chars in resource view,
   keep the full body available via `get_item`? Or no truncation, trust the
   agent's context window?
5. **HTTP transport in v1 or defer?** Defer — stdio covers Claude Desktop
   which is the primary integration. HTTP is a clean separate PR.
6. **MCP SDK version pin?** `mcp>=1.2,<2` — track the SDK closely, the API
   is still evolving.

## Risks / things to watch

- **SDK churn**: `mcp` Python SDK is young, signatures may change. Pin
  conservatively, run `mcp` install in CI, lock with `uv` or `pip-compile`.
- **Async ↔ sync DB**: SQLModel sessions are sync. Tools are async per MCP
  SDK. `await asyncio.to_thread(...)` around DB calls, or use a sync MCP
  handler wrapper if the SDK exposes one. **Codex: please flag if you see a
  blocking DB call inside an async tool body.**
- **Vault switching at runtime**: not supported. Restart the server with
  different env vars. Worth a clear error if a tool somehow gets called
  before `init_db()` ran.
- **Graph entity/relationship type whitelists**: tools that accept an
  `entity.type` parameter must validate against `ENTITY_TYPES` before
  hitting the DB; otherwise an agent can probe the schema with garbage.
- **Logging**: keep tool invocations logged (vault dir + tool name +
  duration) for debugging, but **logs go to stderr or a file, never stdout**
  (see Stdout discipline above). Redact query bodies — PII risk on a personal
  vault.
- **SDK pinning**: pin `mcp` in `pyproject.toml` *and* via the project
  lockfile (Codex review). Loose `mcp>=1.2,<2` alone is not enough — minor
  releases of the SDK have changed signatures. Use the lockfile as the
  reproducibility floor; bump deliberately.

## Acceptance criteria for v1

- `python -m personify.mcp` starts an MCP server over stdio.
- **Stdout stays protocol-clean**: a CI test pipes the server's stdout through
  a JSON-RPC framer and asserts every byte parses; any stray write fails the
  build. (Codex P1.)
- Claude Desktop with the example config sees ~13 tools, 3 concrete resources,
  and 3 resource templates.
- An agent can:
  - Search the vault by full text and semantically.
  - Pull a timeline of items in a date range.
  - Fetch a specific item, entity, or export (truncated by default; full body
    available via `get_item(include_body=true)`).
  - Walk an entity's neighborhood for graph-grounded context.
  - Read summary stats.
- All MCP tools have unit + integration tests.
- No write/mutation tools exist (test enforces against an allow-list constant).
- Existing backend suite still passes.
- `vault mcp` CLI command works.
- HTTP and MCP routes for items/timeline/runs/entity-context delegate to one
  shared service function each (no duplicated query bodies).

## What this unlocks

Once v1 ships, an agent connected to your vault can answer questions like:

- "Summarize my recent thinking about the personal vault project — pull from
  my chatgpt and claude conversations from the last two weeks."
- "Who do I email most about Personify? Build a graph neighborhood from
  whoever the central Person entity is."
- "Find tweets I liked that reference knowledge graphs."
- "List the people in my graph that show up in both gmail and twitter."

Without a single hand-crafted query.
