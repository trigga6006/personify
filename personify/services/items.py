"""Item retrieval service.

Pure SQL/SQLModel queries with no FastAPI / MCP coupling. The HTTP routes,
CLI, and MCP tools all delegate here, so behavior stays consistent across
agent surfaces.

Body truncation matters specifically for the MCP path: an agent's context
window is finite and a single deeply-quoted email can blow through it. The
default ``body_truncate=4096`` cap is the same value documented in
``docs/MCP_PLAN.md`` resources section.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import func
from sqlmodel import select

from personify.db import session_scope
from personify.models import Item, ItemMedia, ItemText, Tag


def _item_summary(item: Item) -> dict[str, Any]:
    return {
        "id": item.id,
        "source": item.source_slug,
        "account": item.account_handle,
        "kind": item.kind,
        "title": item.title,
        "ts": item.ts.isoformat() if item.ts else None,
    }


def list_items(
    *,
    source: Optional[str] = None,
    account: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Paginated browse over the items table with optional filters.

    Stable ordering: ``ts DESC NULLS LAST, id DESC`` — keeps the same item at
    the same position across pagination calls (Codex review answer: stable
    ordering is the cursor-equivalent for now).
    """
    with session_scope() as s:
        stmt = select(Item)
        count_stmt = select(func.count(Item.id))
        if source:
            stmt = stmt.where(Item.source_slug == source)
            count_stmt = count_stmt.where(Item.source_slug == source)
        if account:
            stmt = stmt.where(Item.account_handle == account)
            count_stmt = count_stmt.where(Item.account_handle == account)
        if kind:
            stmt = stmt.where(Item.kind == kind)
            count_stmt = count_stmt.where(Item.kind == kind)
        total = int(s.exec(count_stmt).one() or 0)
        stmt = (
            stmt.order_by(Item.ts.desc().nullslast(), Item.id.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = list(s.exec(stmt).all())
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": [_item_summary(i) for i in rows],
        }


def list_timeline(
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    source: Optional[str] = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Items with non-null timestamps, optionally bounded by a date range.

    Items without timestamps are excluded. Order: ``ts DESC``.
    """
    with session_scope() as s:
        stmt = select(Item).where(Item.ts.is_not(None))
        if source:
            stmt = stmt.where(Item.source_slug == source)
        if start is not None:
            stmt = stmt.where(Item.ts >= start)
        if end is not None:
            stmt = stmt.where(Item.ts <= end)
        stmt = stmt.order_by(Item.ts.desc()).limit(limit)
        rows = list(s.exec(stmt).all())
        return [_item_summary(i) for i in rows]


def get_item_full(
    item_id: int,
    *,
    body_truncate: Optional[int] = 4096,
) -> Optional[dict[str, Any]]:
    """One item with full metadata — text body, media, tags, source data.

    Returns ``None`` if no item exists with the given id; callers translate
    that to a 404 (HTTP) or an MCP error.

    ``body_truncate``:
        - ``None`` → return the full body (used by ``get_item(include_body=True)``)
        - integer → cap the body to N chars; sets ``body_truncated=True`` on
          the response when truncation occurred. Default 4096 chars is enough
          to read a tweet, a short email, a chatgpt message — long emails or
          docs are clipped to keep agent context windows usable.
    """
    with session_scope() as s:
        item = s.get(Item, item_id)
        if not item:
            return None
        text_row = s.exec(select(ItemText).where(ItemText.item_id == item_id)).first()
        media = list(s.exec(select(ItemMedia).where(ItemMedia.item_id == item_id)).all())
        tags = list(s.exec(select(Tag).where(Tag.item_id == item_id)).all())

        body = text_row.body if text_row else None
        body_truncated = False
        body_full_chars: Optional[int] = None
        if body is not None:
            body_full_chars = len(body)
            if body_truncate is not None and body_full_chars > body_truncate:
                body = body[:body_truncate]
                body_truncated = True

        payload: dict[str, Any] = {
            "id": item.id,
            "source": item.source_slug,
            "account": item.account_handle,
            "raw_export_id": item.raw_export_id,
            "ingestion_run_id": item.ingestion_run_id,
            "native_id": item.native_id,
            "kind": item.kind,
            "title": item.title,
            "ts": item.ts.isoformat() if item.ts else None,
            "content_hash": item.content_hash,
            "metadata": item.metadata_json,
            "body": body,
            "body_truncated": body_truncated,
            "body_full_chars": body_full_chars,
            "media": [
                {
                    "id": m.id,
                    "type": m.media_type,
                    "mime": m.mime,
                    "path": m.path,
                    "sha256": m.sha256,
                    "metadata": m.metadata_json,
                }
                for m in media
            ],
            "tags": [{"key": t.key, "value": t.value} for t in tags],
        }
        return payload
