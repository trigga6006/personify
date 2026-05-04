"""MCP resource handlers — concrete + templated.

Resources are URI-addressable read-only documents. MCP distinguishes two
kinds, and we register both:

- **Concrete resources** appear in ``list_resources()``. Discoverable, finite.
- **Resource templates** are URI patterns (``vault://item/{id}``). Discoverable
  via ``list_resource_templates()``; the client parameterizes and reads them
  on demand. Templates are the right primitive for "any item by id" — Codex
  P2 review locked this in.

Same logging discipline as :mod:`personify.mcp.tools`: stderr only, no PII
in messages, sync DB calls wrapped in :func:`asyncio.to_thread`.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlmodel import Session, select

from personify.db import get_engine
from personify.mcp.server import mcp
from personify.models import Item, Source
from personify.services.exports import get_export_summary
from personify.services.graph import get_entity_full
from personify.services.items import get_item_full, list_items
from personify.services.stats import collect_stats

log = logging.getLogger("personify.mcp.resources")

# Body truncation cap matches the documented default in docs/MCP_PLAN.md and
# the `get_item` tool. Resources are for cheap discoverability — full bodies
# go through the `get_item` tool with `include_body=True`.
_RESOURCE_BODY_TRUNCATE = 4096


# --- concrete resources ---------------------------------------------------

@mcp.resource("vault://stats", description="Vault summary: counts and per-source/per-account breakdown.")
async def stats_resource() -> dict[str, Any]:
    log.info("resource stats start")
    payload = await asyncio.to_thread(collect_stats)
    log.info("resource stats done items=%d", payload.get("items", 0))
    return payload


@mcp.resource(
    "vault://recent",
    description="Last ~25 items across all sources (most recent first).",
)
async def recent_resource() -> dict[str, Any]:
    log.info("resource recent start")
    payload = await asyncio.to_thread(list_items, limit=25, offset=0)
    log.info("resource recent done returned=%d", len(payload["items"]))
    return payload


@mcp.resource(
    "vault://sources",
    description="Source registry: every parser the vault has ingested data for.",
)
async def sources_resource() -> list[dict[str, Any]]:
    log.info("resource sources start")

    def _query() -> list[dict[str, Any]]:
        with Session(get_engine()) as s:
            sources = list(s.exec(select(Source).order_by(Source.slug)).all())
            # Per-source accounts via the items table: only the handles that
            # actually have data ingested for this source. Codex review fix:
            # the previous version pulled a global Account list and attached
            # the same list to every Source row, which leaked unrelated
            # handles across source contexts (e.g. a 'files' source row would
            # also show 'twitter' accounts).
            rows = list(
                s.exec(
                    select(Item.source_slug, Item.account_handle)
                    .distinct()
                    .order_by(Item.source_slug, Item.account_handle)
                ).all()
            )
            per_source: dict[str, list[str]] = {}
            for slug, handle in rows:
                per_source.setdefault(slug, []).append(handle)
            return [
                {
                    "slug": x.slug,
                    "label": x.label,
                    "created_at": x.created_at.isoformat() if x.created_at else None,
                    "accounts": per_source.get(x.slug, []),
                }
                for x in sources
            ]

    payload = await asyncio.to_thread(_query)
    log.info("resource sources done count=%d", len(payload))
    return payload


# --- resource templates ---------------------------------------------------

@mcp.resource(
    "vault://item/{item_id}",
    description=(
        "One item by id (truncated body, capped at 4096 chars). "
        "For the full body use the get_item tool with include_body=true."
    ),
)
async def item_resource(item_id: int) -> dict[str, Any]:
    log.info("resource item start item_id=%d", item_id)
    payload = await asyncio.to_thread(
        get_item_full, item_id, body_truncate=_RESOURCE_BODY_TRUNCATE
    )
    if payload is None:
        log.info("resource item not_found item_id=%d", item_id)
        raise ValueError(f"no item with id={item_id}")
    log.info(
        "resource item done item_id=%d truncated=%s", item_id, payload.get("body_truncated")
    )
    return payload


@mcp.resource(
    "vault://entity/{entity_id}",
    description="One graph entity by id, with aliases and item-backed evidence.",
)
async def entity_resource(entity_id: int) -> dict[str, Any]:
    log.info("resource entity start entity_id=%d", entity_id)

    def _query() -> dict[str, Any] | None:
        with Session(get_engine()) as s:
            return get_entity_full(s, entity_id)

    payload = await asyncio.to_thread(_query)
    if payload is None:
        log.info("resource entity not_found entity_id=%d", entity_id)
        raise ValueError(f"no entity with id={entity_id}")
    log.info(
        "resource entity done entity_id=%d evidence=%d",
        entity_id, len(payload.get("evidence", [])),
    )
    return payload


@mcp.resource(
    "vault://export/{export_id}",
    description="One raw export by id, with run history and item count.",
)
async def export_resource(export_id: int) -> dict[str, Any]:
    log.info("resource export start export_id=%d", export_id)
    payload = await asyncio.to_thread(get_export_summary, export_id)
    if payload is None:
        log.info("resource export not_found export_id=%d", export_id)
        raise ValueError(f"no export with id={export_id}")
    log.info(
        "resource export done export_id=%d items=%d runs=%d",
        export_id, payload.get("items", 0), payload.get("runs_count", 0),
    )
    return payload
