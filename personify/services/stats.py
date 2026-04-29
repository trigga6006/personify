from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlmodel import select

from personify.db import session_scope
from personify.models import Account, IngestionRun, Item, RawExport, Source


def collect_stats() -> dict[str, Any]:
    with session_scope() as s:
        sources = [
            {"slug": x.slug, "label": x.label} for x in s.exec(select(Source)).all()
        ]
        accounts = [
            {"handle": a.handle, "display_name": a.display_name}
            for a in s.exec(select(Account)).all()
        ]
        per_source = {
            row[0]: int(row[1])
            for row in s.exec(
                select(Item.source_slug, func.count(Item.id)).group_by(Item.source_slug)
            ).all()
        }
        per_account = {
            row[0]: int(row[1])
            for row in s.exec(
                select(Item.account_handle, func.count(Item.id)).group_by(Item.account_handle)
            ).all()
        }
        total_items = int(s.exec(select(func.count(Item.id))).one() or 0)
        total_exports = int(s.exec(select(func.count(RawExport.id))).one() or 0)
        total_runs = int(s.exec(select(func.count(IngestionRun.id))).one() or 0)
        return {
            "items": total_items,
            "exports": total_exports,
            "runs": total_runs,
            "items_per_source": per_source,
            "items_per_account": per_account,
            "sources": sources,
            "accounts": accounts,
        }
