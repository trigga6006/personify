from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from sqlalchemy import func, or_

from personify.db import session_scope
from personify.models import (
    Embedding,
    Entity,
    EntityAlias,
    EntityEvidence,
    IngestionRun,
    Item,
    ItemMedia,
    ItemText,
    PipelineStage,
    RawExport,
    Relationship,
    RelationshipEvidence,
    Tag,
)
from personify.parsers import ParsedItem, get_parser
from personify.util.hashing import sha256_text
from personify.util.vault import (
    append_log,
    normalized_path_for,
    staging_dir_for,
)


def _existing_item(
    s: Session,
    source: str,
    account: str,
    native_id: str | None,
    content_hash: str,
) -> Item | None:
    if native_id:
        hit = s.exec(
            select(Item).where(
                Item.source_slug == source,
                Item.account_handle == account,
                Item.native_id == native_id,
            )
        ).first()
        if hit:
            return hit
    return s.exec(
        select(Item).where(
            Item.source_slug == source,
            Item.account_handle == account,
            Item.content_hash == content_hash,
        )
    ).first()


def _persist_item(
    s: Session,
    parsed: ParsedItem,
    raw_export: RawExport,
    run: IngestionRun,
) -> tuple[Item, bool, tuple[int, str] | None]:
    """Insert (or skip) one ParsedItem.

    Returns ``(item, inserted_bool, normalized_payload_or_None)``. The third
    element is the on-disk JSON snapshot the caller should write *after the
    batch commits* — see ``_flush_batch``. Writing inside the transaction
    means a rolled-back batch leaves orphaned ``normalized/item_<id>.json``
    files keyed to ids that never persisted; deferring the write until
    after commit closes that race.
    """
    body = parsed.body or ""
    content_hash = sha256_text(f"{parsed.kind}\0{parsed.title or ''}\0{body}")

    existing = _existing_item(
        s,
        raw_export.source_slug,
        raw_export.account_handle,
        parsed.native_id,
        content_hash,
    )
    if existing:
        return existing, False, None

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
        existing = _existing_item(
            s,
            raw_export.source_slug,
            raw_export.account_handle,
            parsed.native_id,
            content_hash,
        )
        return existing, False, None  # type: ignore[return-value]

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
    # Dedup (key, value) tags so a parser that yields the same pair twice
    # (e.g. a tweet that mentions the same handle multiple times) doesn't
    # break the run on the uq_tags_item_kv unique constraint.
    seen_tags: set[tuple[str, str]] = set()
    for k, v in parsed.tags or []:
        if not k or v is None:
            continue
        sv = str(v)
        if (k, sv) in seen_tags:
            continue
        seen_tags.add((k, sv))
        s.add(Tag(item_id=item.id, key=k, value=sv))

    normalized_json = json.dumps(
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
    )
    return item, True, (item.id, normalized_json)


#: Items per database transaction during ingest. Smaller = better resumability
#: (less work lost on crash) but higher per-row commit overhead. 100 keeps
#: large mbox / discord / chatgpt exports under a second of progress lost on
#: any single transaction failure, while still amortizing commit cost.
INGEST_BATCH_SIZE = 100


def ingest_export(raw_export_id: int) -> IngestionRun:
    """Stream a parser's items into the DB, committing in fixed-size batches.

    The previous implementation wrapped the entire iteration in a single
    transaction. That meant a parser crash at item 37,000 of 50,000 rolled
    back the prior 36,999 — every re-run started from zero.

    This version commits every ``INGEST_BATCH_SIZE`` items. On crash, only
    the in-flight batch is lost; everything before it is durable. Re-running
    the export then exercises the existing dedup path
    (``_existing_item`` → unique constraints on ``(source, account, native_id)``
    and ``(source, account, content_hash)``), which already classifies
    previously-inserted items as ``skipped`` and only inserts the survivors.

    Net effect: ingestion is now resumable without any schema changes.
    """
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
        stored_path = raw.stored_path

    append_log(f"ingest_{source_slug}", f"start raw_export={raw_id} run={run_id}")

    seen = inserted = skipped = 0
    batch: list[ParsedItem] = []
    error: str | None = None

    def _flush_batch(items: list[ParsedItem]) -> tuple[int, int]:
        """Persist one batch in its own transaction. Counters update on the
        run row inside the same transaction so a partial-run crash leaves
        the row reflecting actual durable progress.

        Normalized JSON snapshots are collected during the transaction and
        written to disk only AFTER the session commits successfully — so a
        rolled-back batch never leaves orphaned ``normalized/item_*.json``
        files referencing item ids that never persisted to the DB.

        Snapshot writes themselves are best-effort: once the DB commit is
        durable, a write_text failure must NOT propagate. If it did, the
        outer ``try/except`` would catch it and overwrite the just-committed
        ``items_inserted`` / ``items_skipped`` columns with the stale
        outer-scope counters (which haven't been incremented yet — that
        increment happens on the line *after* the call returns). The DB is
        the source of truth; missing snapshots are recoverable by a future
        repair/normalize pass, while a lying counter is not.
        """
        if not items:
            return 0, 0
        b_inserted = b_skipped = 0
        snapshots: list[tuple[int, str]] = []
        with session_scope() as s:
            raw_local = s.get(RawExport, raw_id)
            run_local = s.get(IngestionRun, run_id)
            for parsed in items:
                _, was_inserted, snapshot = _persist_item(s, parsed, raw_local, run_local)
                if was_inserted:
                    b_inserted += 1
                    if snapshot is not None:
                        snapshots.append(snapshot)
                else:
                    b_skipped += 1
            # Update durable run counters inside this same transaction so
            # the row stays consistent with the items committed alongside it.
            run_local.items_seen = seen
            run_local.items_inserted = inserted + b_inserted
            run_local.items_skipped = skipped + b_skipped
        # Session has committed. Snapshot writes are best-effort from here:
        # the DB is durable, so a disk failure must not overwrite the run
        # counters via the outer except handler.
        for item_id, payload in snapshots:
            try:
                normalized_path_for(item_id).write_text(payload, encoding="utf-8")
            except OSError as e:
                append_log(
                    f"ingest_{source_slug}",
                    f"snapshot_write_failed run={run_id} item={item_id} {e!r}",
                )
        return b_inserted, b_skipped

    try:
        staging = staging_dir_for(raw_id)
        for parsed in parser.iter_items(Path(stored_path), staging):
            seen += 1
            batch.append(parsed)
            if len(batch) >= INGEST_BATCH_SIZE:
                b_ins, b_skip = _flush_batch(batch)
                inserted += b_ins
                skipped += b_skip
                batch.clear()
        # Trailing partial batch.
        if batch:
            b_ins, b_skip = _flush_batch(batch)
            inserted += b_ins
            skipped += b_skip
            batch.clear()
        with session_scope() as s:
            run = s.get(IngestionRun, run_id)
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
                # Counters reflect what actually committed before the crash —
                # callers and the UI now have an accurate "inserted N of M"
                # picture, and a re-run will dedup past those N rows.
                run.items_seen = seen
                run.items_inserted = inserted
                run.items_skipped = skipped
        append_log(
            f"ingest_{source_slug}",
            f"error run={run_id} inserted_before_failure={inserted} {error}",
        )
        raise

    append_log(
        f"ingest_{source_slug}",
        f"done run={run_id} seen={seen} inserted={inserted} skipped={skipped}",
    )
    with session_scope() as s:
        return s.get(IngestionRun, run_id)


def reset_export(raw_export_id: int) -> dict[str, int]:
    """Delete persisted derived records for one raw export.

    The raw export row and vault/raw file are preserved. This is intended for
    parser upgrades where normalized item bodies should be rebuilt.

    Cleanup order is FK-safe for Postgres:
    1. PipelineStage rows (FK both raw_exports and ingestion_runs).
    2. Item-backed evidence rows (entity + relationship).
    3. Prune orphaned extractor-origin relationships and entities — those
       whose item-backed evidence is now zero. Manual-origin rows and rows
       still backed by other exports' evidence are preserved.
    4. Item-scoped derived rows (embeddings, tags, media, text).
    5. Items themselves (and their normalized JSON snapshots).
    6. IngestionRun rows last.
    """
    with session_scope() as s:
        item_ids = [
            i.id
            for i in s.exec(select(Item).where(Item.raw_export_id == raw_export_id)).all()
            if i.id is not None
        ]

        stages = list(
            s.exec(select(PipelineStage).where(PipelineStage.raw_export_id == raw_export_id)).all()
        )
        for stage in stages:
            s.delete(stage)

        entity_evidence_deleted = 0
        relationship_evidence_deleted = 0
        entities_pruned = 0
        relationships_pruned = 0
        if item_ids:
            item_id_strs = [str(i) for i in item_ids]

            # Collect candidate entity_ids and relationship_ids touched by deleted items
            # before we mutate evidence rows.
            candidate_entity_ids = {
                row.entity_id
                for row in s.exec(
                    select(EntityEvidence).where(
                        EntityEvidence.source_type == "item",
                        EntityEvidence.source_id.in_(item_id_strs),
                    )
                ).all()
            }
            candidate_rel_ids = {
                row.relationship_id
                for row in s.exec(
                    select(RelationshipEvidence).where(
                        RelationshipEvidence.source_type == "item",
                        RelationshipEvidence.source_id.in_(item_id_strs),
                    )
                ).all()
            }
            # Extractor handlers sometimes create entities only by participating
            # in a relationship (e.g. Person endpoints on gmail edges) without
            # adding direct EntityEvidence. Treat their endpoints as candidates
            # too, so reset can prune them when no item-backed evidence remains.
            if candidate_rel_ids:
                for rel in s.exec(
                    select(Relationship).where(Relationship.id.in_(candidate_rel_ids))
                ).all():
                    candidate_entity_ids.add(rel.source_entity_id)
                    candidate_entity_ids.add(rel.target_entity_id)

            # Delete the item-backed evidence rows for the items being purged.
            entity_evidence_rows = list(
                s.exec(
                    select(EntityEvidence).where(
                        EntityEvidence.source_type == "item",
                        EntityEvidence.source_id.in_(item_id_strs),
                    )
                ).all()
            )
            for row in entity_evidence_rows:
                s.delete(row)
            entity_evidence_deleted = len(entity_evidence_rows)

            rel_evidence_rows = list(
                s.exec(
                    select(RelationshipEvidence).where(
                        RelationshipEvidence.source_type == "item",
                        RelationshipEvidence.source_id.in_(item_id_strs),
                    )
                ).all()
            )
            for row in rel_evidence_rows:
                s.delete(row)
            relationship_evidence_deleted = len(rel_evidence_rows)
            s.flush()

            # Prune extractor-origin relationships with zero remaining item-backed evidence.
            # Must run before entity pruning so entity-FK constraints don't block deletes.
            for rel_id in candidate_rel_ids:
                rel = s.get(Relationship, rel_id)
                if rel is None or rel.origin != "extractor":
                    continue
                remaining = int(
                    s.exec(
                        select(func.count(RelationshipEvidence.id)).where(
                            RelationshipEvidence.relationship_id == rel_id,
                            RelationshipEvidence.source_type == "item",
                        )
                    ).one()
                    or 0
                )
                if remaining > 0:
                    continue
                # Drop any non-item evidence still pointing at this relationship.
                for ev in s.exec(
                    select(RelationshipEvidence).where(RelationshipEvidence.relationship_id == rel_id)
                ).all():
                    s.delete(ev)
                s.delete(rel)
                relationships_pruned += 1
            s.flush()

            # Prune extractor-origin entities with zero remaining item-backed evidence
            # AND no remaining relationships referencing them (manual or otherwise).
            for entity_id in candidate_entity_ids:
                entity = s.get(Entity, entity_id)
                if entity is None or entity.origin != "extractor":
                    continue
                remaining_evidence = int(
                    s.exec(
                        select(func.count(EntityEvidence.id)).where(
                            EntityEvidence.entity_id == entity_id,
                            EntityEvidence.source_type == "item",
                        )
                    ).one()
                    or 0
                )
                if remaining_evidence > 0:
                    continue
                referencing_rels = int(
                    s.exec(
                        select(func.count(Relationship.id)).where(
                            or_(
                                Relationship.source_entity_id == entity_id,
                                Relationship.target_entity_id == entity_id,
                            )
                        )
                    ).one()
                    or 0
                )
                if referencing_rels > 0:
                    # Some manual or sibling-export relationship still depends on it.
                    continue
                for alias in s.exec(
                    select(EntityAlias).where(EntityAlias.entity_id == entity_id)
                ).all():
                    s.delete(alias)
                for ev in s.exec(
                    select(EntityEvidence).where(EntityEvidence.entity_id == entity_id)
                ).all():
                    s.delete(ev)
                s.delete(entity)
                entities_pruned += 1
            s.flush()

            for model in (Embedding, Tag, ItemMedia, ItemText):
                for row in s.exec(select(model).where(model.item_id.in_(item_ids))).all():
                    s.delete(row)
            for item in s.exec(select(Item).where(Item.id.in_(item_ids))).all():
                if item.id is not None:
                    norm = normalized_path_for(item.id)
                    if norm.exists():
                        norm.unlink()
                s.delete(item)

        runs = list(
            s.exec(select(IngestionRun).where(IngestionRun.raw_export_id == raw_export_id)).all()
        )
        for run in runs:
            s.delete(run)

        return {
            "items": len(item_ids),
            "runs": len(runs),
            "pipeline_stages": len(stages),
            "entity_evidence": entity_evidence_deleted,
            "relationship_evidence": relationship_evidence_deleted,
            "entities_pruned": entities_pruned,
            "relationships_pruned": relationships_pruned,
        }


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
