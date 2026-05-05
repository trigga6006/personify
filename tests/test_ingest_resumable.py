"""Resumability test for ``services.ingest.ingest_export``.

The previous implementation wrapped the whole iter loop in one transaction,
so a parser exception at item N rolled back items 1…N-1. With batched
commits, items committed before the crash are durable and a re-run dedups
past them. This test models that exact scenario.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pytest

from personify.parsers.base import ParsedItem, ParserBase


def _init(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "personify.db"
    monkeypatch.setenv("PERSONIFY_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("PERSONIFY_VAULT_DIR", str(tmp_path / "vault"))

    import personify.config as config
    import personify.db as db
    import personify.util.vault as vault

    config.settings = config.Settings()
    vault.settings = config.settings

    db.engine = db.create_engine(
        config.settings.db_url, echo=False, pool_pre_ping=True
    )
    vault.ensure_vault_layout()
    db.init_db()
    return db


class _FlakyParser(ParserBase):
    """Parser that yields ``total`` items but raises after ``fail_after``.

    Use ``fail_after=None`` to make it succeed cleanly. Used to simulate a
    real-world parser crash partway through a large export.
    """

    SOURCE = "files"  # reuse an existing slug so register_export accepts it
    PARSER_VERSION = "test-resume"

    fail_after: int | None = None
    total: int = 250

    def iter_items(self, raw_path: Path, staging_dir: Path) -> Iterator[ParsedItem]:
        for i in range(1, self.total + 1):
            if self.fail_after is not None and i > self.fail_after:
                raise RuntimeError(f"simulated parser crash at item {i}")
            yield ParsedItem(
                kind="file",
                title=f"item {i}",
                body=f"body {i}",
                ts=datetime.now(timezone.utc),
                native_id=f"native-{i:04d}",
            )


def _seed_raw_export(db, *, vault_dir: Path) -> int:
    """Insert a RawExport row directly. The flaky parser doesn't actually
    read disk, so any plausible stored_path is fine."""
    from personify.models import RawExport
    fake = vault_dir / "raw" / "files" / "test"
    fake.mkdir(parents=True, exist_ok=True)
    stored = fake / "fake.txt"
    stored.write_text("placeholder")

    with db.Session(db.engine, expire_on_commit=False) as s:
        raw = RawExport(
            source_slug="files",
            account_handle="test",
            original_path=str(stored),
            stored_path=str(stored),
            size_bytes=stored.stat().st_size,
            sha256="0" * 64,
        )
        s.add(raw)
        s.commit()
        return raw.id


def _count_items(db) -> int:
    from personify.models import Item
    from sqlmodel import select, func

    with db.Session(db.engine) as s:
        return int(s.exec(select(func.count(Item.id))).one() or 0)


def test_partial_failure_preserves_committed_batches(tmp_path, monkeypatch):
    """Parser raises at item 137 (mid-second-batch). With batch size 100,
    the first batch (items 1-100) is durable; the second is rolled back
    along with the crash."""
    db = _init(tmp_path, monkeypatch)

    import personify.services.ingest as ingest_mod
    from personify.services.ingest import ingest_export
    from personify.parsers import PARSERS

    raw_id = _seed_raw_export(db, vault_dir=tmp_path / "vault")

    # Swap the registered parser for our flaky one. The dict-based registry
    # is fine to monkeypatch in a test — production code reads it once per
    # ingest_export call.
    monkeypatch.setitem(PARSERS, "files", _FlakyParser)
    monkeypatch.setattr(_FlakyParser, "fail_after", 137, raising=False)
    monkeypatch.setattr(_FlakyParser, "total", 250, raising=False)
    monkeypatch.setattr(ingest_mod, "INGEST_BATCH_SIZE", 100, raising=False)

    with pytest.raises(RuntimeError, match="simulated parser crash"):
        ingest_export(raw_id)

    # First batch (1-100) should have committed. The second batch was in
    # progress when the crash hit, so items 101-137 should NOT be present.
    assert _count_items(db) == 100


def test_re_run_after_failure_dedups_and_completes(tmp_path, monkeypatch):
    """After a partial failure, a clean re-run inserts the missing items
    and reports the previously-inserted ones as ``skipped`` via the
    existing dedup path."""
    db = _init(tmp_path, monkeypatch)

    import personify.services.ingest as ingest_mod
    from personify.services.ingest import ingest_export
    from personify.parsers import PARSERS

    raw_id = _seed_raw_export(db, vault_dir=tmp_path / "vault")

    monkeypatch.setitem(PARSERS, "files", _FlakyParser)
    monkeypatch.setattr(ingest_mod, "INGEST_BATCH_SIZE", 100, raising=False)

    # First run crashes at 137 → 100 items committed.
    monkeypatch.setattr(_FlakyParser, "fail_after", 137, raising=False)
    monkeypatch.setattr(_FlakyParser, "total", 250, raising=False)
    with pytest.raises(RuntimeError):
        ingest_export(raw_id)
    assert _count_items(db) == 100

    # Second run: parser succeeds end-to-end.
    monkeypatch.setattr(_FlakyParser, "fail_after", None, raising=False)
    run = ingest_export(raw_id)

    assert _count_items(db) == 250
    assert run.status == "ok"
    assert run.items_seen == 250
    # First 100 are dedup'd by native_id; remaining 150 are fresh inserts.
    assert run.items_inserted == 150
    assert run.items_skipped == 100


def test_failed_batch_leaves_no_orphan_normalized_files(tmp_path, monkeypatch):
    """Regression: normalized JSON snapshots must be written AFTER the
    batch commits, so a rolled-back batch never leaves
    ``normalized/item_<id>.json`` files keyed to ids that didn't persist.

    With batch size 100 and a parser that crashes mid-second-batch, the
    first batch (items 1-100) commits and writes 100 snapshots. The
    second batch rolls back and must leave zero new snapshots — total
    on disk should be exactly 100.
    """
    db = _init(tmp_path, monkeypatch)

    import personify.services.ingest as ingest_mod
    from personify.services.ingest import ingest_export
    from personify.parsers import PARSERS

    raw_id = _seed_raw_export(db, vault_dir=tmp_path / "vault")

    monkeypatch.setitem(PARSERS, "files", _FlakyParser)
    monkeypatch.setattr(_FlakyParser, "fail_after", 137, raising=False)
    monkeypatch.setattr(_FlakyParser, "total", 250, raising=False)
    monkeypatch.setattr(ingest_mod, "INGEST_BATCH_SIZE", 100, raising=False)

    with pytest.raises(RuntimeError):
        ingest_export(raw_id)

    normalized_root = tmp_path / "vault" / "normalized"
    snapshot_count = sum(1 for _ in normalized_root.rglob("item_*.json"))
    assert snapshot_count == 100, (
        f"expected exactly 100 snapshots (one per committed item), got "
        f"{snapshot_count} — the in-flight batch's snapshots leaked despite "
        f"the rollback"
    )


def test_snapshot_write_failure_does_not_clobber_committed_run_counters(
    tmp_path, monkeypatch
):
    """Regression for a bug Codex caught: if ``normalized_path_for(...).
    write_text`` raises after the batch DB commit, the outer exception
    handler must NOT overwrite the just-committed run counters with the
    stale outer-scope values.

    Reproduces by forcing every snapshot write to fail. The DB rows still
    commit (2 items in the small batch), and the run must end as ``ok``
    with ``items_inserted == 2`` — not ``error`` with ``items_inserted == 0``.
    """
    db = _init(tmp_path, monkeypatch)

    import personify.services.ingest as ingest_mod
    from personify.services.ingest import ingest_export
    from personify.models import IngestionRun
    from personify.parsers import PARSERS
    from sqlmodel import select

    raw_id = _seed_raw_export(db, vault_dir=tmp_path / "vault")

    monkeypatch.setitem(PARSERS, "files", _FlakyParser)
    monkeypatch.setattr(_FlakyParser, "fail_after", None, raising=False)
    monkeypatch.setattr(_FlakyParser, "total", 2, raising=False)
    monkeypatch.setattr(ingest_mod, "INGEST_BATCH_SIZE", 100, raising=False)

    # Force the snapshot write to fail. The DB commit must already be
    # durable by this point; the question is whether the run counters
    # survive the disk-write failure.
    def _boom(self, *args, **kwargs):
        raise OSError("simulated disk full")

    monkeypatch.setattr("pathlib.Path.write_text", _boom, raising=True)

    # Ingest must complete cleanly — write_text failures are now logged,
    # not propagated.
    run = ingest_export(raw_id)

    assert run.status == "ok", f"expected ok, got {run.status} (error={run.error})"
    assert run.items_seen == 2
    assert run.items_inserted == 2
    assert _count_items(db) == 2

    # Sanity: re-fetch the run row from the DB and check the counters
    # weren't overwritten by a later step.
    with db.Session(db.engine) as s:
        row = s.exec(
            select(IngestionRun).where(IngestionRun.raw_export_id == raw_id)
        ).first()
    assert row is not None
    assert row.items_inserted == 2, (
        f"expected items_inserted=2 (DB-committed), got {row.items_inserted} — "
        f"snapshot write failure clobbered the durable counter"
    )


def test_run_row_reflects_committed_count_on_failure(tmp_path, monkeypatch):
    """The IngestionRun row's items_inserted should equal what's actually
    in the DB after a crash — not zero (old behavior) and not the
    in-memory count of attempted inserts."""
    db = _init(tmp_path, monkeypatch)

    import personify.services.ingest as ingest_mod
    from personify.services.ingest import ingest_export
    from personify.models import IngestionRun
    from personify.parsers import PARSERS
    from sqlmodel import select

    raw_id = _seed_raw_export(db, vault_dir=tmp_path / "vault")

    monkeypatch.setitem(PARSERS, "files", _FlakyParser)
    monkeypatch.setattr(_FlakyParser, "fail_after", 137, raising=False)
    monkeypatch.setattr(_FlakyParser, "total", 250, raising=False)
    monkeypatch.setattr(ingest_mod, "INGEST_BATCH_SIZE", 100, raising=False)

    with pytest.raises(RuntimeError):
        ingest_export(raw_id)

    with db.Session(db.engine) as s:
        run = s.exec(select(IngestionRun).where(IngestionRun.raw_export_id == raw_id)).first()
    assert run is not None
    assert run.status == "error"
    assert run.items_inserted == 100, (
        f"expected items_inserted=100 (one full batch committed before crash), "
        f"got {run.items_inserted}"
    )
