from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session, select

from personify.db import init_db
from personify.models import Entity, PipelineStage, Relationship


def _init(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "personify.db"
    monkeypatch.setenv("PERSONIFY_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("PERSONIFY_VAULT_DIR", str(tmp_path / "vault"))

    import personify.config as config
    import personify.db as db
    import personify.services.register as register
    import personify.util.vault as vault

    config.settings = config.Settings()
    vault.settings = config.settings
    db.engine = db.create_engine(config.settings.db_url, echo=False, pool_pre_ping=True)
    register.ensure_vault_layout()
    init_db()
    return db


def test_pipeline_ingest_only_records_one_stage(tmp_path: Path, monkeypatch, fixtures_dir: Path) -> None:
    db = _init(tmp_path, monkeypatch)
    from personify.services.pipeline import (
        STAGE_INGEST,
        STATUS_DONE,
        run_pipeline,
    )
    from personify.services.register import register_export

    raw = register_export("files", fixtures_dir / "files", "test")
    result = run_pipeline(raw.id)

    assert [s.stage for s in result.stages] == [STAGE_INGEST]
    assert result.stages[0].status == STATUS_DONE
    assert result.stages[0].items_processed == 2

    with Session(db.get_engine(), expire_on_commit=False) as s:
        rows = list(s.exec(select(PipelineStage).where(PipelineStage.raw_export_id == raw.id)).all())
    assert len(rows) == 1
    assert rows[0].stage == STAGE_INGEST
    assert rows[0].status == STATUS_DONE
    assert rows[0].started_at is not None and rows[0].finished_at is not None
    assert rows[0].ingestion_run_id == result.ingestion_run_id


def test_pipeline_with_graph_creates_entities_and_evidence(
    tmp_path: Path, monkeypatch, fixtures_dir: Path
) -> None:
    db = _init(tmp_path, monkeypatch)
    from personify.services.pipeline import (
        STAGE_GRAPH,
        STAGE_INGEST,
        STATUS_DONE,
        run_pipeline,
    )
    from personify.services.register import register_export

    raw = register_export("files", fixtures_dir / "files", "test")
    result = run_pipeline(raw.id, with_graph=True)

    stage_names = [s.stage for s in result.stages]
    assert stage_names == [STAGE_INGEST, STAGE_GRAPH]
    assert all(s.status == STATUS_DONE for s in result.stages)
    assert result.stage(STAGE_GRAPH).items_processed >= 1

    with Session(db.get_engine(), expire_on_commit=False) as s:
        files_entities = list(s.exec(select(Entity).where(Entity.type == "File")).all())
    # files fixture has note.md and todo.txt
    assert len(files_entities) == 2


def test_failed_ingest_run_reports_zero_committed_items(
    tmp_path: Path, monkeypatch
) -> None:
    """A run that errors mid-loop must report items_inserted=0, not the
    in-Python attempted count, because the failed transaction rolled back."""
    db = _init(tmp_path, monkeypatch)
    monkeypatch.setenv("PERSONIFY_VAULT_DIR", str(tmp_path / "vault"))
    import personify.config as config
    import personify.util.vault as vault
    config.settings = config.Settings()
    vault.settings = config.settings
    db.engine = db.create_engine(config.settings.db_url, echo=False, pool_pre_ping=True)
    vault.ensure_vault_layout()

    from personify.parsers import PARSERS
    from personify.parsers.base import ParsedItem, ParserBase
    from personify.models import IngestionRun, Item

    class FlakyParser(ParserBase):
        SOURCE = "flaky_test"
        PARSER_VERSION = "0.0.1"

        @classmethod
        def detect(cls, path):
            return True

        def iter_items(self, raw_path, staging_dir):
            # Yield two items, then crash.
            yield ParsedItem(kind="message", title="a", body="a", native_id="a")
            yield ParsedItem(kind="message", title="b", body="b", native_id="b")
            raise RuntimeError("simulated mid-stream failure")

    PARSERS[FlakyParser.SOURCE] = FlakyParser
    try:
        from personify.services.ingest import ingest_export
        from personify.services.register import register_export

        sample = tmp_path / "f.txt"
        sample.write_text("placeholder", encoding="utf-8")
        raw = register_export("flaky_test", sample, "test")
        with pytest.raises(RuntimeError):
            ingest_export(raw.id)

        with Session(db.get_engine(), expire_on_commit=False) as s:
            run = s.exec(
                select(IngestionRun).where(IngestionRun.raw_export_id == raw.id)
            ).first()
            committed = list(s.exec(select(Item).where(Item.raw_export_id == raw.id)).all())

        assert run is not None
        assert run.status == "error"
        assert run.items_seen == 2  # both items were attempted
        assert run.items_inserted == 0  # but none were committed (rolled back)
        assert run.items_skipped == 0
        assert committed == []
    finally:
        PARSERS.pop(FlakyParser.SOURCE, None)


def test_persist_item_tolerates_parser_duplicate_tags(
    tmp_path: Path, monkeypatch
) -> None:
    """Persist layer must dedup (key, value) tags so a parser that yields
    duplicates (e.g. a tweet mentioning the same handle twice) can't blow up
    the whole ingest run on uq_tags_item_kv."""
    db = _init(tmp_path, monkeypatch)
    monkeypatch.setenv("PERSONIFY_VAULT_DIR", str(tmp_path / "vault"))
    import personify.config as config
    import personify.util.vault as vault
    config.settings = config.Settings()
    vault.settings = config.settings
    db.engine = db.create_engine(config.settings.db_url, echo=False, pool_pre_ping=True)
    vault.ensure_vault_layout()

    # Build a parser that deliberately emits duplicate tags. The persister
    # must accept this without raising IntegrityError.
    from personify.parsers import PARSERS
    from personify.parsers.base import ParsedItem, ParserBase

    class DupTagParser(ParserBase):
        SOURCE = "duptag_test"
        PARSER_VERSION = "0.0.1"

        @classmethod
        def detect(cls, path):
            return True

        def iter_items(self, raw_path, staging_dir):
            yield ParsedItem(
                kind="message",
                title="dup",
                body="hi",
                native_id="dup-1",
                tags=[("mention", "amritwt"), ("mention", "amritwt"), ("hashtag", "ai")],
            )

    PARSERS[DupTagParser.SOURCE] = DupTagParser
    try:
        from personify.models import Tag
        from personify.services.ingest import ingest_export
        from personify.services.register import register_export

        # Need a real file on disk to register; any file works since DupTagParser ignores it.
        sample = tmp_path / "sample.txt"
        sample.write_text("placeholder", encoding="utf-8")
        raw = register_export("duptag_test", sample, "test")
        run = ingest_export(raw.id)
        assert run.status == "ok"
        assert run.items_inserted == 1

        with Session(db.get_engine(), expire_on_commit=False) as s:
            tags = list(s.exec(select(Tag).where(Tag.item_id.is_not(None))).all())
        # Two ("mention","amritwt") entries collapse to one; ("hashtag","ai") stays.
        pairs = sorted((t.key, t.value) for t in tags)
        assert pairs == [("hashtag", "ai"), ("mention", "amritwt")]
    finally:
        PARSERS.pop(DupTagParser.SOURCE, None)


def test_pipeline_twitter_dm_only_extractor_state_is_prunable_by_reset(
    tmp_path: Path, monkeypatch
) -> None:
    """A DM-only archive must not leave an unprunable extractor-origin author.

    The DM branch creates the archive owner's Person entity but no document
    and no relationship. Without item-backed evidence on the Person itself,
    reset_export() can never trace it back through evidence rows or via
    relationship-endpoint expansion, so the entity would survive resets
    forever as stale graph state.
    """
    db = _init(tmp_path, monkeypatch)
    monkeypatch.setenv("PERSONIFY_VAULT_DIR", str(tmp_path / "vault"))
    import personify.config as config
    import personify.util.vault as vault
    config.settings = config.Settings()
    vault.settings = config.settings
    db.engine = db.create_engine(config.settings.db_url, echo=False, pool_pre_ping=True)
    vault.ensure_vault_layout()

    # Build a DM-only Twitter archive on disk.
    dm_archive = tmp_path / "dm_archive"
    (dm_archive / "data").mkdir(parents=True)
    (dm_archive / "data" / "account.js").write_text(
        'window.YTD.account.part0 = [\n'
        '  {"account": {"username": "lonely_dm_user", "accountId": "999"}}\n'
        ']\n',
        encoding="utf-8",
    )
    (dm_archive / "data" / "direct-messages.js").write_text(
        'window.YTD.direct_messages.part0 = [\n'
        '  {"dmConversation": {"conversationId": "999-888", "messages": ['
        '    {"messageCreate": {"id": "555", "text": "hey", "senderId": "999",'
        '     "recipientId": "888", "createdAt": "2024-04-06T15:30:00.000Z"}}'
        '  ]}}\n'
        ']\n',
        encoding="utf-8",
    )

    from personify.services.ingest import reset_export
    from personify.services.pipeline import run_pipeline
    from personify.services.register import register_export

    raw = register_export("twitter", dm_archive, "lonely_dm_user")
    run_pipeline(raw.id, with_graph=True)

    # Author entity should exist after ingest+graph.
    with Session(db.get_engine(), expire_on_commit=False) as s:
        people = list(s.exec(select(Entity).where(Entity.type == "Person")).all())
    assert len(people) == 1
    assert (people[0].metadata_json or {}).get("twitter_handle") == "lonely_dm_user"

    # Reset must prune that author entity — it has no item-backed evidence
    # to keep it alive after the underlying DM item is removed.
    deleted = reset_export(raw.id)
    assert deleted["entities_pruned"] == 1

    with Session(db.get_engine(), expire_on_commit=False) as s:
        survivors = list(s.exec(select(Entity)).all())
    assert survivors == []


def test_pipeline_twitter_graph_creates_people_and_mentions(
    tmp_path: Path, monkeypatch, fixtures_dir: Path
) -> None:
    """Twitter ingest + graph should produce Person entities for the author and
    every mention, plus MENTIONS edges from author → mentioned handles."""
    db = _init(tmp_path, monkeypatch)
    from personify.services.pipeline import STAGE_GRAPH, run_pipeline
    from personify.services.register import register_export

    raw = register_export("twitter", fixtures_dir / "twitter", "personify_user")
    result = run_pipeline(raw.id, with_graph=True)

    graph_stage = result.stage(STAGE_GRAPH)
    assert graph_stage is not None
    assert graph_stage.metadata["entities_created"] >= 4  # author + 2 mentions + ≥1 tweet doc

    with Session(db.get_engine(), expire_on_commit=False) as s:
        people = list(s.exec(select(Entity).where(Entity.type == "Person")).all())
        rels = list(s.exec(select(Relationship)).all())

    handles = {(p.metadata_json or {}).get("twitter_handle") for p in people}
    assert {"personify_user", "anthropicai", "codex"} <= handles

    mentions = [r for r in rels if r.relationship_type == "MENTIONS"]
    assert len(mentions) >= 2  # reply → @anthropicai, @codex (retweet adds anthropicai again, dedup'd)


def test_pipeline_gmail_with_graph_links_people_to_email(
    tmp_path: Path, monkeypatch, fixtures_dir: Path
) -> None:
    db = _init(tmp_path, monkeypatch)
    from personify.services.pipeline import STAGE_GRAPH, run_pipeline
    from personify.services.register import register_export

    raw = register_export("gmail", fixtures_dir / "gmail", "test")
    result = run_pipeline(raw.id, with_graph=True)

    graph_stage = result.stage(STAGE_GRAPH)
    assert graph_stage is not None
    assert graph_stage.metadata["entities_created"] >= 4  # 2 emails + at least 2 people

    with Session(db.get_engine(), expire_on_commit=False) as s:
        people = list(s.exec(select(Entity).where(Entity.type == "Person")).all())
        emails = list(s.exec(select(Entity).where(Entity.type == "Email")).all())
        rels = list(s.exec(select(Relationship)).all())

    person_emails = {(p.metadata_json or {}).get("email") for p in people}
    assert person_emails >= {"alice@example.com", "bob@example.com", "me@example.com"}
    assert len(emails) == 2
    # Each email should have a CREATED_BY edge from a sender Person.
    created_by = [r for r in rels if r.relationship_type == "CREATED_BY"]
    assert len(created_by) == 2


def test_pipeline_reset_cleans_stage_rows(tmp_path: Path, monkeypatch, fixtures_dir: Path) -> None:
    db = _init(tmp_path, monkeypatch)
    from personify.models import EntityEvidence
    from personify.services.ingest import reset_export
    from personify.services.pipeline import run_pipeline
    from personify.services.register import register_export

    raw = register_export("files", fixtures_dir / "files", "test")
    run_pipeline(raw.id, with_graph=True)

    with Session(db.get_engine(), expire_on_commit=False) as s:
        before = list(s.exec(select(PipelineStage).where(PipelineStage.raw_export_id == raw.id)).all())
        evidence_before = list(s.exec(select(EntityEvidence)).all())
    assert len(before) == 2
    assert len(evidence_before) >= 1

    deleted = reset_export(raw.id)
    assert deleted["pipeline_stages"] == 2
    assert deleted["entity_evidence"] == len(evidence_before)

    with Session(db.get_engine(), expire_on_commit=False) as s:
        after = list(s.exec(select(PipelineStage).where(PipelineStage.raw_export_id == raw.id)).all())
        evidence_after = list(s.exec(select(EntityEvidence)).all())
    assert after == []
    assert evidence_after == []


def test_pipeline_reset_after_run_then_reingest_does_not_duplicate_evidence(
    tmp_path: Path, monkeypatch, fixtures_dir: Path
) -> None:
    """Re-ingesting after reset must not leave stale evidence pointing at dead item ids."""
    db = _init(tmp_path, monkeypatch)
    from personify.models import EntityEvidence
    from personify.services.ingest import reset_export
    from personify.services.pipeline import run_pipeline
    from personify.services.register import register_export

    raw = register_export("files", fixtures_dir / "files", "test")
    run_pipeline(raw.id, with_graph=True)
    reset_export(raw.id)
    run_pipeline(raw.id, with_graph=True)

    with Session(db.get_engine(), expire_on_commit=False) as s:
        evidence = list(s.exec(select(EntityEvidence)).all())
        from personify.models import Item
        live_items = {str(i.id) for i in s.exec(select(Item)).all()}

    # Every evidence row must point at a live item id, not a stale one.
    for row in evidence:
        if row.source_type == "item":
            assert row.source_id in live_items, f"orphan evidence for source_id={row.source_id}"


def test_pipeline_records_skipped_downstream_on_ingest_failure(
    tmp_path: Path, monkeypatch, fixtures_dir: Path
) -> None:
    _init(tmp_path, monkeypatch)
    from personify.services import pipeline as pipeline_mod
    from personify.services.pipeline import (
        STAGE_EMBED,
        STAGE_GRAPH,
        STAGE_INGEST,
        STATUS_ERROR,
        STATUS_SKIPPED,
        run_pipeline,
    )
    from personify.services.register import register_export

    raw = register_export("files", fixtures_dir / "files", "test")

    def boom(_raw_export_id):
        raise RuntimeError("ingest exploded")

    monkeypatch.setattr(pipeline_mod, "ingest_export", boom)

    result = run_pipeline(raw.id, with_embeddings=True, with_graph=True)

    statuses = {s.stage: s.status for s in result.stages}
    assert statuses[STAGE_INGEST] == STATUS_ERROR
    assert statuses[STAGE_EMBED] == STATUS_SKIPPED
    assert statuses[STAGE_GRAPH] == STATUS_SKIPPED
    # Error message recorded on the ingest stage row.
    ingest_stage = result.stage(STAGE_INGEST)
    assert ingest_stage.error is not None
    assert "ingest exploded" in ingest_stage.error


def test_pipeline_links_failed_stage_to_ingestion_run(
    tmp_path: Path, monkeypatch, fixtures_dir: Path
) -> None:
    """When ingest_export raises mid-run, the failed PipelineStage should still
    reference the IngestionRun that ingest_export persisted with status=error."""
    db = _init(tmp_path, monkeypatch)
    from personify.parsers.files import FilesParser
    from personify.services.pipeline import (
        STAGE_INGEST,
        STATUS_ERROR,
        run_pipeline,
    )
    from personify.services.register import register_export

    raw = register_export("files", fixtures_dir / "files", "test")

    def angry_iter(self, raw_path, staging_dir):
        raise RuntimeError("parser failure mid-stream")
        yield  # pragma: no cover - keeps the function a generator

    monkeypatch.setattr(FilesParser, "iter_items", angry_iter)

    result = run_pipeline(raw.id)
    ingest_stage = result.stage(STAGE_INGEST)
    assert ingest_stage.status == STATUS_ERROR
    assert result.ingestion_run_id is not None

    with Session(db.get_engine(), expire_on_commit=False) as s:
        from personify.models import IngestionRun
        stage_row = list(
            s.exec(select(PipelineStage).where(PipelineStage.raw_export_id == raw.id)).all()
        )[0]
        run = s.get(IngestionRun, stage_row.ingestion_run_id)
    assert stage_row.ingestion_run_id == result.ingestion_run_id
    assert run is not None
    assert run.status == "error"


def test_reset_prunes_extractor_origin_orphans(
    tmp_path: Path, monkeypatch, fixtures_dir: Path
) -> None:
    """After reset, extractor-origin entities/relationships with no remaining
    item-backed evidence are pruned, so the graph reflects current ingest state."""
    db = _init(tmp_path, monkeypatch)
    from personify.services.ingest import reset_export
    from personify.services.pipeline import run_pipeline
    from personify.services.register import register_export

    raw = register_export("gmail", fixtures_dir / "gmail", "test")
    run_pipeline(raw.id, with_graph=True)

    with Session(db.get_engine(), expire_on_commit=False) as s:
        before_entities = list(s.exec(select(Entity).where(Entity.origin == "extractor")).all())
        before_rels = list(s.exec(select(Relationship).where(Relationship.origin == "extractor")).all())
    assert len(before_entities) > 0
    assert len(before_rels) > 0

    deleted = reset_export(raw.id)
    assert deleted["entities_pruned"] == len(before_entities)
    assert deleted["relationships_pruned"] == len(before_rels)

    with Session(db.get_engine(), expire_on_commit=False) as s:
        after_entities = list(s.exec(select(Entity)).all())
        after_rels = list(s.exec(select(Relationship)).all())
    assert after_entities == []
    assert after_rels == []


def test_reset_preserves_manual_entity_even_when_evidence_drops_to_zero(
    tmp_path: Path, monkeypatch, fixtures_dir: Path
) -> None:
    """Entities created via the API (origin=manual) must survive reset, even if
    extractor evidence pointing at them is purged."""
    db = _init(tmp_path, monkeypatch)
    from personify.services.graph import (
        add_entity_evidence,
        create_or_get_entity,
    )
    from personify.services.ingest import reset_export
    from personify.services.pipeline import run_pipeline
    from personify.services.register import register_export

    raw = register_export("files", fixtures_dir / "files", "test")
    run_pipeline(raw.id)  # ingest only — no extractor entities created yet

    # User manually creates an entity, then attaches evidence pointing at one of
    # the export's items. Reset must remove the evidence but keep the entity.
    with Session(db.get_engine(), expire_on_commit=False) as s:
        manual = create_or_get_entity(s, type="Project", name="My Manual Project")
        manual_id = manual.id
        from personify.models import Item
        first_item = s.exec(select(Item).where(Item.raw_export_id == raw.id)).first()
        add_entity_evidence(
            s,
            entity_id=manual_id,
            source_type="item",
            source_id=str(first_item.id),
            quote="manual link to an ingested item",
        )
        s.commit()

    deleted = reset_export(raw.id)
    assert deleted["entities_pruned"] == 0  # manual entity must not be pruned

    with Session(db.get_engine(), expire_on_commit=False) as s:
        survivor = s.get(Entity, manual_id)
    assert survivor is not None
    assert survivor.origin == "manual"


def test_reset_preserves_extractor_entity_referenced_by_manual_relationship(
    tmp_path: Path, monkeypatch, fixtures_dir: Path
) -> None:
    """If a manual relationship references an extractor-created entity, the
    entity stays even when its own evidence is gone."""
    db = _init(tmp_path, monkeypatch)
    from personify.services.graph import (
        create_or_get_entity,
        create_or_get_relationship,
    )
    from personify.services.ingest import reset_export
    from personify.services.pipeline import run_pipeline
    from personify.services.register import register_export

    raw = register_export("files", fixtures_dir / "files", "test")
    run_pipeline(raw.id, with_graph=True)

    # Pick an extractor-origin File entity and manually link it to a brand-new
    # manual Project entity. The manual relationship pins the File entity.
    with Session(db.get_engine(), expire_on_commit=False) as s:
        file_entity = s.exec(
            select(Entity).where(Entity.origin == "extractor", Entity.type == "File")
        ).first()
        assert file_entity is not None
        pinned_id = file_entity.id

        project = create_or_get_entity(s, type="Project", name="Pinning Project")
        create_or_get_relationship(
            s,
            source_entity_id=project.id,
            target_entity_id=pinned_id,
            relationship_type="USES",
        )
        s.commit()

    reset_export(raw.id)

    with Session(db.get_engine(), expire_on_commit=False) as s:
        pinned = s.get(Entity, pinned_id)
        rels = list(s.exec(select(Relationship)).all())
    assert pinned is not None  # extractor entity preserved because manual rel references it
    assert any(r.relationship_type == "USES" and r.origin == "manual" for r in rels)


def test_pipeline_unknown_export_raises(tmp_path: Path, monkeypatch) -> None:
    _init(tmp_path, monkeypatch)
    from personify.services.pipeline import run_pipeline

    with pytest.raises(ValueError):
        run_pipeline(999)


def test_pipeline_api_runs_with_toggles(tmp_path: Path, monkeypatch, fixtures_dir: Path) -> None:
    _init(tmp_path, monkeypatch)
    from fastapi.testclient import TestClient

    from personify.api import app
    from personify.services.register import register_export

    raw = register_export("files", fixtures_dir / "files", "test")

    client = TestClient(app)
    resp = client.post(
        "/api/pipeline",
        json={"export_id": raw.id, "with_graph": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    stages = body["pipeline"]["stages"]
    assert [s["stage"] for s in stages] == ["ingest", "graph"]
    assert all(s["status"] == "done" for s in stages)

    status_resp = client.get(f"/api/exports/{raw.id}/pipeline")
    assert status_resp.status_code == 200
    payload = status_resp.json()
    assert set(payload["stages"].keys()) == {"ingest", "graph"}
    assert payload["stages"]["graph"]["status"] == "done"


def test_ingest_api_rejects_toggle_without_export_id(tmp_path: Path, monkeypatch) -> None:
    _init(tmp_path, monkeypatch)
    from fastapi.testclient import TestClient

    from personify.api import app

    client = TestClient(app)
    resp = client.post("/api/ingest", json={"all_pending": True, "with_graph": True})
    assert resp.status_code == 400


def test_exports_api_includes_pipeline_stage_summary(
    tmp_path: Path, monkeypatch, fixtures_dir: Path
) -> None:
    """The Exports table relies on /api/exports embedding the latest stage rows."""
    _init(tmp_path, monkeypatch)
    from fastapi.testclient import TestClient

    from personify.api import app
    from personify.services.pipeline import run_pipeline
    from personify.services.register import register_export

    raw = register_export("files", fixtures_dir / "files", "test")
    run_pipeline(raw.id, with_graph=True)

    client = TestClient(app)
    resp = client.get("/api/exports")
    assert resp.status_code == 200
    rows = resp.json()
    row = next(r for r in rows if r["id"] == raw.id)
    stages = row["pipeline_stages"]
    assert set(stages.keys()) == {"ingest", "graph"}
    assert stages["ingest"]["status"] == "done"
    assert stages["graph"]["status"] == "done"
    # Each stage exposes the durable bookkeeping fields the UI renders.
    for s in stages.values():
        assert "items_processed" in s
        assert "started_at" in s
        assert "finished_at" in s
