"""Raw-export retrieval service.

Used by the MCP ``vault://export/{id}`` resource. The HTTP API still has its
own ``/api/exports`` listing route; if/when that needs the same single-item
shape it can delegate here.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import func
from sqlmodel import select

from personify.db import session_scope
from personify.models import IngestionRun, Item, RawExport
from personify.services.runs import run_summary


def get_export_summary(export_id: int) -> Optional[dict[str, Any]]:
    """One raw export with item count, run history, and pipeline-friendly fields.

    Returns ``None`` when no export exists with that id; callers translate
    that to a 404 (HTTP) or an MCP error.
    """
    with session_scope() as s:
        export = s.get(RawExport, export_id)
        if not export:
            return None
        items = int(
            s.exec(select(func.count(Item.id)).where(Item.raw_export_id == export.id)).one()
            or 0
        )
        runs = list(
            s.exec(
                select(IngestionRun)
                .where(IngestionRun.raw_export_id == export.id)
                .order_by(IngestionRun.started_at.desc())
            ).all()
        )
        return {
            "id": export.id,
            "source": export.source_slug,
            "account": export.account_handle,
            "stored_path": export.stored_path,
            "original_path": export.original_path,
            "size_bytes": export.size_bytes,
            "sha256": export.sha256,
            "received_at": export.received_at.isoformat() if export.received_at else None,
            "notes": export.notes,
            "items": items,
            "runs_count": len(runs),
            "runs": [run_summary(r) for r in runs],
        }
