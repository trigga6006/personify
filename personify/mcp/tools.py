"""MCP tool implementations.

Each tool is a thin wrapper over an existing service-layer function. No
business logic lives here — the goal is that the CLI, HTTP API, and MCP
agent surface all behave identically because they share the same callees.

Logging discipline:

- **stdout is reserved for JSON-RPC framing.** This module uses
  :mod:`logging`, routed to stderr by :mod:`personify.mcp.__main__`. Any
  service-layer code path reachable from a tool must follow the same rule.
- **Never log query bodies.** A personal vault is a privacy surface; log
  metadata (tool, limit, source, duration, length) plus a short truncated
  SHA-256 prefix for correlation, never the query text. Codex review P3.

Async/sync bridge: the service layer uses synchronous SQLModel sessions, so
every tool wraps the call in :func:`asyncio.to_thread` to avoid blocking the
event loop FastMCP runs on.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from functools import wraps
from typing import Any, Awaitable, Callable, Optional, TypeVar

from sqlmodel import Session

from personify.db import get_engine
from personify.mcp.schemas import (
    EntityIdInput,
    GetItemInput,
    GraphSearchEntitiesInput,
    NeighborhoodInput,
    RecentItemsInput,
    RecentRunsInput,
    SearchInput,
    SemanticSearchInput,
    TimelineInput,
)
from personify.mcp.server import mcp
from personify.services.graph import (
    entity_context as _entity_context_service,
    get_entity_full as _get_entity_full_service,
    get_entity_neighborhood,
    search_entities,
)
from personify.services.items import (
    get_item_full as _get_item_full_service,
    list_items as _list_items_service,
    list_timeline as _list_timeline_service,
)
from personify.services.runs import list_recent_runs as _list_recent_runs_service
from personify.services.search import semantic_search as _semantic_search_service
from personify.services.search import text_search as _text_search_service
from personify.services.stats import collect_stats

log = logging.getLogger("personify.mcp.tools")

T = TypeVar("T")


def _query_fingerprint(text_value: str) -> str:
    """8-char sha256 prefix — correlates a query across stages without leaking it."""
    return hashlib.sha256(text_value.encode("utf-8")).hexdigest()[:8]


def _redact_filter(value: Optional[str]) -> str:
    """Render a free-form filter value safely in logs.

    ``source`` and ``kind`` are closed-vocabulary slugs ("twitter", "tweet"),
    not PII. ``account`` is whatever the user used to identify a data source
    — usually an email or a handle, which IS PII. Use this for any filter
    field that could carry user identity. Returns the literal string ``None``
    when unset, an 8-char fingerprint otherwise.

    Codex review: ``recent_items`` was logging ``account`` raw.
    """
    if value is None:
        return "None"
    return f"fp:{_query_fingerprint(value)}"


def _timed(name: str) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Wrap an async tool body with start/done/error logging at INFO level.

    Privacy contract: only the kwargs the decorator emits are logged. Tools
    must NOT pass query strings or other PII through ``meta=...``; pass
    fingerprints, lengths, and counts.
    """

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            started = time.monotonic()
            log.info("%s start", name)
            try:
                result = await fn(*args, **kwargs)
            except Exception:
                log.exception("%s error", name)
                raise
            ms = (time.monotonic() - started) * 1000.0
            count = len(result) if isinstance(result, (list, tuple)) else None
            if count is not None:
                log.info("%s done items=%d ms=%.1f", name, count, ms)
            else:
                log.info("%s done ms=%.1f", name, ms)
            return result

        return wrapper

    return decorator


# --- search & retrieval ---------------------------------------------------

@mcp.tool()
async def search(input: SearchInput) -> list[dict[str, Any]]:
    """Full-text search across all ingested items.

    Returns the most relevant items as ``SearchHit`` records (id, source,
    kind, ts, title, snippet). Use this for keyword and phrase queries; for
    semantic similarity use ``semantic_search``.
    """
    started = time.monotonic()
    fp = _query_fingerprint(input.query)
    log.info(
        "search start q.len=%d q.fp=%s limit=%d source=%r",
        len(input.query),
        fp,
        input.limit,
        input.source,
    )
    try:
        results = await asyncio.to_thread(
            _text_search_service, input.query, limit=input.limit, source=input.source
        )
    except Exception:
        log.exception("search error q.fp=%s", fp)
        raise
    log.info(
        "search done q.fp=%s hits=%d ms=%.1f",
        fp, len(results), (time.monotonic() - started) * 1000.0,
    )
    return results


@mcp.tool()
async def semantic_search(input: SemanticSearchInput) -> list[dict[str, Any]]:
    """Vector similarity search across embedded item bodies.

    Requires the optional embeddings backend; raises with an install hint if
    sentence-transformers / pgvector aren't available. Use this for "find
    items semantically similar to this idea" rather than keyword matching.
    """
    started = time.monotonic()
    fp = _query_fingerprint(input.query)
    log.info(
        "semantic_search start q.len=%d q.fp=%s limit=%d source=%r",
        len(input.query), fp, input.limit, input.source,
    )
    try:
        results = await asyncio.to_thread(
            _semantic_search_service, input.query, limit=input.limit, source=input.source
        )
    except ImportError as e:
        log.error("semantic_search backend missing q.fp=%s err=%s", fp, e)
        raise
    except Exception:
        log.exception("semantic_search error q.fp=%s", fp)
        raise
    log.info(
        "semantic_search done q.fp=%s hits=%d ms=%.1f",
        fp, len(results), (time.monotonic() - started) * 1000.0,
    )
    return results


@mcp.tool()
@_timed("timeline")
async def timeline(input: TimelineInput) -> list[dict[str, Any]]:
    """Items with timestamps in a date range, most recent first.

    Items without timestamps are excluded. Bound the query with ``start`` /
    ``end`` to keep payloads small; without bounds you get the most recent
    ``limit`` items across the whole vault.
    """
    return await asyncio.to_thread(
        _list_timeline_service,
        start=input.start,
        end=input.end,
        source=input.source,
        limit=input.limit,
    )


@mcp.tool()
async def get_item(input: GetItemInput) -> dict[str, Any]:
    """Fetch one item by id with metadata, media, tags, and (optionally) body.

    The body is **truncated to 4096 chars by default** — set ``include_body=True``
    only when you need the full text. Truncation status is reported in the
    response (``body_truncated``, ``body_full_chars``).
    """
    started = time.monotonic()
    truncate = None if input.include_body else 4096
    log.info(
        "get_item start item_id=%d include_body=%s",
        input.item_id, input.include_body,
    )
    payload = await asyncio.to_thread(
        _get_item_full_service, input.item_id, body_truncate=truncate
    )
    if payload is None:
        log.info("get_item not_found item_id=%d", input.item_id)
        raise ValueError(f"no item with id={input.item_id}")
    log.info(
        "get_item done item_id=%d truncated=%s ms=%.1f",
        input.item_id, payload.get("body_truncated"),
        (time.monotonic() - started) * 1000.0,
    )
    return payload


@mcp.tool()
async def recent_items(input: RecentItemsInput) -> dict[str, Any]:
    """Paginated browse over the items table with optional source/account/kind filters.

    Stable ordering (ts DESC NULLS LAST, id DESC) means re-paginating returns
    each item at the same offset until new data lands.
    """
    started = time.monotonic()
    # Privacy: source and kind are closed-vocabulary slugs and safe to log,
    # but `account` is usually an email or a handle the user controls and
    # must be treated as PII. Fingerprint it — the prefix is enough to
    # correlate "start" → "done" → "error" entries for the same query.
    log.info(
        "recent_items start source=%r account=%s kind=%r limit=%d offset=%d",
        input.source, _redact_filter(input.account), input.kind, input.limit, input.offset,
    )
    payload = await asyncio.to_thread(
        _list_items_service,
        source=input.source,
        account=input.account,
        kind=input.kind,
        limit=input.limit,
        offset=input.offset,
    )
    log.info(
        "recent_items done returned=%d total=%d ms=%.1f",
        len(payload["items"]), payload["total"],
        (time.monotonic() - started) * 1000.0,
    )
    return payload


@mcp.tool()
@_timed("recent_runs")
async def recent_runs(input: RecentRunsInput) -> list[dict[str, Any]]:
    """Most recent ingestion runs with status, parser, and item counts."""
    return await asyncio.to_thread(_list_recent_runs_service, limit=input.limit)


# --- vault metadata -------------------------------------------------------

@mcp.tool()
@_timed("list_sources")
async def list_sources() -> list[dict[str, Any]]:
    """Active source registry — every parser slug the vault knows about."""
    from personify.models import Source
    from sqlmodel import select

    def _query() -> list[dict[str, Any]]:
        with Session(get_engine()) as s:
            return [
                {
                    "slug": x.slug,
                    "label": x.label,
                    "created_at": x.created_at.isoformat() if x.created_at else None,
                }
                for x in s.exec(select(Source).order_by(Source.slug)).all()
            ]

    return await asyncio.to_thread(_query)


@mcp.tool()
@_timed("list_accounts")
async def list_accounts() -> list[dict[str, Any]]:
    """Accounts that have data ingested into the vault."""
    from personify.models import Account
    from sqlmodel import select

    def _query() -> list[dict[str, Any]]:
        with Session(get_engine()) as s:
            return [
                {"handle": a.handle, "display_name": a.display_name}
                for a in s.exec(select(Account).order_by(Account.handle)).all()
            ]

    return await asyncio.to_thread(_query)


@mcp.tool()
async def stats() -> dict[str, Any]:
    """Vault summary: total counts of items / exports / runs and per-source / per-account breakdowns."""
    started = time.monotonic()
    log.info("stats start")
    payload = await asyncio.to_thread(collect_stats)
    log.info(
        "stats done items=%d sources=%d ms=%.1f",
        payload.get("items", 0),
        len(payload.get("items_per_source", {})),
        (time.monotonic() - started) * 1000.0,
    )
    return payload


# --- graph ---------------------------------------------------------------

@mcp.tool()
async def graph_search_entities(input: GraphSearchEntitiesInput) -> list[dict[str, Any]]:
    """Find entities in the knowledge graph by name or alias substring.

    Restrict by ``type`` (Person, Project, Company, …) to scope the result
    set; unknown types raise — the schema validates against the live registry.
    """
    started = time.monotonic()
    fp = _query_fingerprint(input.query)
    log.info(
        "graph_search_entities start q.len=%d q.fp=%s type=%r limit=%d",
        len(input.query), fp, input.type, input.limit,
    )

    def _query() -> list[dict[str, Any]]:
        with Session(get_engine()) as s:
            entities = search_entities(s, query=input.query, type=input.type, limit=input.limit)
            return [
                {
                    "id": e.id,
                    "type": e.type,
                    "name": e.name,
                    "canonical_name": e.canonical_name,
                    "origin": e.origin,
                }
                for e in entities
            ]

    results = await asyncio.to_thread(_query)
    log.info(
        "graph_search_entities done q.fp=%s hits=%d ms=%.1f",
        fp, len(results), (time.monotonic() - started) * 1000.0,
    )
    return results


@mcp.tool()
async def get_entity(input: EntityIdInput) -> dict[str, Any]:
    """Fetch one graph entity with its aliases and item-backed evidence."""
    started = time.monotonic()
    log.info("get_entity start entity_id=%d", input.entity_id)

    def _query() -> dict[str, Any] | None:
        with Session(get_engine()) as s:
            return _get_entity_full_service(s, input.entity_id)

    payload = await asyncio.to_thread(_query)
    if payload is None:
        log.info("get_entity not_found entity_id=%d", input.entity_id)
        raise ValueError(f"no entity with id={input.entity_id}")
    log.info(
        "get_entity done entity_id=%d evidence=%d ms=%.1f",
        input.entity_id, len(payload.get("evidence", [])),
        (time.monotonic() - started) * 1000.0,
    )
    return payload


@mcp.tool()
async def entity_neighborhood(input: NeighborhoodInput) -> dict[str, Any]:
    """Walk the graph outward from an entity and return its neighborhood.

    Depth is capped at 2 to keep payloads bounded. Returns ``center``,
    ``nodes`` (the related entities), and ``edges`` (the relationships).
    """
    started = time.monotonic()
    log.info(
        "entity_neighborhood start entity_id=%d depth=%d",
        input.entity_id, input.depth,
    )

    def _query() -> dict[str, Any]:
        with Session(get_engine()) as s:
            graph = get_entity_neighborhood(s, entity_id=input.entity_id, depth=input.depth)
            # Convert ORM rows to plain dicts so the JSON serializer doesn't
            # have to know about SQLModel internals.
            from personify.services.graph import _entity_dict, _relationship_dict
            return {
                "center": _entity_dict(graph["center"]) if graph["center"] else None,
                "nodes": [_entity_dict(n) for n in graph["nodes"]],
                "edges": [_relationship_dict(r) for r in graph["edges"]],
            }

    payload = await asyncio.to_thread(_query)
    log.info(
        "entity_neighborhood done entity_id=%d nodes=%d edges=%d ms=%.1f",
        input.entity_id, len(payload["nodes"]), len(payload["edges"]),
        (time.monotonic() - started) * 1000.0,
    )
    return payload


@mcp.tool()
async def entity_context(input: EntityIdInput) -> dict[str, Any]:
    """LLM-friendly grounding payload: entity + neighborhood + evidence.

    Use this when the agent has identified a specific entity and needs the
    full context to reason about it. Includes a small set of suggested
    follow-up queries the agent can run.
    """
    started = time.monotonic()
    log.info("entity_context start entity_id=%d", input.entity_id)

    def _query() -> dict[str, Any] | None:
        with Session(get_engine()) as s:
            return _entity_context_service(s, input.entity_id)

    payload = await asyncio.to_thread(_query)
    if payload is None:
        log.info("entity_context not_found entity_id=%d", input.entity_id)
        raise ValueError(f"no entity with id={input.entity_id}")
    log.info(
        "entity_context done entity_id=%d related=%d ms=%.1f",
        input.entity_id, len(payload.get("related_entities", [])),
        (time.monotonic() - started) * 1000.0,
    )
    return payload
