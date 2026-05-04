"""Ingestion run retrieval service.

Used by the HTTP API (`/api/runs`) and the MCP `recent_runs` tool. Owns the
single canonical "run summary" shape so both surfaces stay in sync.
"""
from __future__ import annotations

from typing import Any

from sqlmodel import select

from personify.db import session_scope
from personify.models import IngestionRun


def run_summary(run: IngestionRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "raw_export_id": run.raw_export_id,
        "status": run.status,
        "parser": run.parser_name,
        "parser_version": run.parser_version,
        "items_seen": run.items_seen,
        "items_inserted": run.items_inserted,
        "items_skipped": run.items_skipped,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "error": run.error,
    }


def list_recent_runs(*, limit: int = 25) -> list[dict[str, Any]]:
    """Last N ingestion runs, most recent first."""
    with session_scope() as s:
        rows = list(
            s.exec(select(IngestionRun).order_by(IngestionRun.started_at.desc()).limit(limit)).all()
        )
    return [run_summary(r) for r in rows]
