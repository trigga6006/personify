from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from personify.db import session_scope
from personify.models import IngestionRun, Item, ItemMedia, ItemText, RawExport, Tag
from personify.parsers import ParsedItem, get_parser
from personify.util.hashing import sha256_text
from personify.util.vault import (
    append_log,
    normalized_path_for,
    staging_dir_for,
)


def _existing_item(s: Session, source: str, native_id: str | None, content_hash: str) -> Item | None:
    if native_id:
        hit = s.exec(
            select(Item).where(Item.source_slug == source, Item.native_id == native_id)
        ).first()
        if hit:
            return hit
    return s.exec(
        select(Item).where(Item.source_slug == source, Item.content_hash == content_hash)
    ).first()


def _persist_item(
    s: Session,
    parsed: ParsedItem,
    raw_export: RawExport,
    run: IngestionRun,
) -> tuple[Item, bool]:
    """Insert (or skip) one ParsedItem. Returns (item, inserted_bool)."""
    body = parsed.body or ""
    content_hash = sha256_text(f"{parsed.kind}\0{parsed.title or ''}\0{body}")

    existing = _existing_item(s, raw_export.source_slug, parsed.native_id, content_hash)
    if existing:
        return existing, False

    item = Item(
        source_slug=raw_export.source_slug,
        account_handle=raw_export.account_handle,
        raw_export_id=raw_export.id,
        ingestion_run_id=run.id,
        native_id=parsed.native_id,
        kind=parsed.kind,
        title=parsed.title,
        ts=parsed.ts,
        content_hash=content_hash,
        metadata_json=parsed.metadata or {},
    )
    s.add(item)
    try:
        s.flush()
    except IntegrityError:
        s.rollback()
        existing = _existing_item(s, raw_export.source_slug, parsed.native_id, content_hash)
        return existing, False  # type: ignore[return-value]

    if body:
        s.add(ItemText(item_id=item.id, body=body, char_count=len(body)))
    for m in parsed.media or []:
        s.add(
            ItemMedia(
                item_id=item.id,
                media_type=m.get("media_type", "other"),
                mime=m.get("mime"),
                path=m.get("path", ""),
                size_bytes=m.get("size_bytes"),
                sha256=m.get("sha256"),
                metadata_json=m.get("metadata", {}) or {},
            )
        )
    for k, v in parsed.tags or []:
        if not k or v is None:
            continue
        s.add(Tag(item_id=item.id, key=k, value=str(v)))

    # Write normalized JSON snapshot.
    norm = normalized_path_for(item.id)
    norm.write_text(
        json.dumps(
            {
                "id": item.id,
                "source": raw_export.source_slug,
                "account": raw_export.account_handle,
                "raw_export_id": raw_export.id,
                "native_id": parsed.native_id,
                "kind": parsed.kind,
                "title": parsed.title,
                "ts": parsed.ts.isoformat() if parsed.ts else None,
                "content_hash": content_hash,
                "metadata": parsed.metadata or {},
                "body": body,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return item, True


def ingest_export(raw_export_id: int) -> IngestionRun:
    with session_scope() as s:
        raw = s.get(RawExport, raw_export_id)
        if not raw:
            raise ValueError(f"raw_export {raw_export_id} not found")
        parser_cls = get_parser(raw.source_slug)
        parser = parser_cls()

        run = IngestionRun(
            raw_export_id=raw.id,
            parser_name=parser.SOURCE,
            parser_version=parser.PARSER_VERSION,
        )
        s.add(run)
        s.flush()
        run_id = run.id
        raw_id = raw.id
        source_slug = raw.source_slug

    append_log(f"ingest_{source_slug}", f"start raw_export={raw_id} run={run_id}")

    seen = inserted = skipped = 0
    error: str | None = None
    try:
        with session_scope() as s:
            raw = s.get(RawExport, raw_id)
            run = s.get(IngestionRun, run_id)
            staging = staging_dir_for(raw.id)
            for parsed in parser.iter_items(Path(raw.stored_path), staging):
                seen += 1
                _, was_inserted = _persist_item(s, parsed, raw, run)
                if was_inserted:
                    inserted += 1
                else:
                    skipped += 1
                if seen % 500 == 0:
                    s.flush()
            run.items_seen = seen
            run.items_inserted = inserted
            run.items_skipped = skipped
            run.status = "ok"
            run.finished_at = datetime.now(timezone.utc)
    except Exception as e:  # noqa: BLE001
        error = repr(e)
        with session_scope() as s:
            run = s.get(IngestionRun, run_id)
            if run:
                run.status = "error"
                run.error = error
                run.finished_at = datetime.now(timezone.utc)
                run.items_seen = seen
                run.items_inserted = inserted
                run.items_skipped = skipped
        append_log(f"ingest_{source_slug}", f"error run={run_id} {error}")
        raise

    append_log(
        f"ingest_{source_slug}",
        f"done run={run_id} seen={seen} inserted={inserted} skipped={skipped}",
    )
    with session_scope() as s:
        return s.get(IngestionRun, run_id)


def ingest_source(source_slug: str) -> list[IngestionRun]:
    with session_scope() as s:
        ids = [
            r.id
            for r in s.exec(select(RawExport).where(RawExport.source_slug == source_slug)).all()
        ]
    return [ingest_export(i) for i in ids]


def ingest_all_pending() -> list[IngestionRun]:
    """Ingest any raw_export that has no successful run."""
    with session_scope() as s:
        rows = s.exec(select(RawExport)).all()
        ok_ids: set[int] = set()
        for r in s.exec(select(IngestionRun).where(IngestionRun.status == "ok")).all():
            ok_ids.add(r.raw_export_id)
        ids = [r.id for r in rows if r.id not in ok_ids]
    return [ingest_export(i) for i in ids]


def all_runs_for(raw_ids: Iterable[int]) -> list[IngestionRun]:
    with session_scope() as s:
        return list(s.exec(select(IngestionRun).where(IngestionRun.raw_export_id.in_(list(raw_ids)))).all())
