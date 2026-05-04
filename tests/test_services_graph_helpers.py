"""Unit tests for graph service helpers extracted in MCP Phase 2."""
from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, select

from personify.db import init_db


def _init(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "personify.db"
    monkeypatch.setenv("PERSONIFY_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("PERSONIFY_VAULT_DIR", str(tmp_path / "vault"))

    import personify.config as config
    import personify.db as db
    import personify.util.vault as vault

    config.settings = config.Settings()
    vault.settings = config.settings
    db.engine = db.create_engine(config.settings.db_url, echo=False, pool_pre_ping=True)
    vault.ensure_vault_layout()
    init_db()
    return db


def test_get_entity_full_returns_none_for_missing(tmp_path, monkeypatch):
    db = _init(tmp_path, monkeypatch)
    from personify.services.graph import get_entity_full

    with Session(db.get_engine()) as s:
        assert get_entity_full(s, 999_999) is None


def test_get_entity_full_returns_canonical_shape(tmp_path, monkeypatch):
    db = _init(tmp_path, monkeypatch)
    from personify.services.graph import (
        add_entity_alias,
        add_entity_evidence,
        create_or_get_entity,
        get_entity_full,
    )

    with Session(db.get_engine(), expire_on_commit=False) as s:
        e = create_or_get_entity(s, type="Project", name="Personify Vault")
        add_entity_alias(s, e.id, "PVault")
        add_entity_evidence(
            s,
            entity_id=e.id,
            source_type="item",
            source_id="42",
            quote="Personify Vault is a personal data store",
        )
        s.commit()
        eid = e.id

    with Session(db.get_engine()) as s:
        payload = get_entity_full(s, eid)

    assert payload is not None
    assert payload["entity"]["id"] == eid
    assert payload["entity"]["origin"] == "manual"
    assert any(a["alias"] == "PVault" for a in payload["aliases"])
    assert len(payload["evidence"]) == 1
    assert payload["evidence"][0]["source_type"] == "item"


def test_entity_context_includes_neighborhood_and_capped_suggestions(tmp_path, monkeypatch):
    db = _init(tmp_path, monkeypatch)
    from personify.services.graph import (
        create_or_get_entity,
        create_or_get_relationship,
        entity_context,
    )

    with Session(db.get_engine(), expire_on_commit=False) as s:
        a = create_or_get_entity(s, type="Project", name="Personify")
        b = create_or_get_entity(s, type="Topic", name="Knowledge Graph")
        c = create_or_get_entity(s, type="Tool", name="pgvector")
        create_or_get_relationship(
            s, source_entity_id=a.id, target_entity_id=b.id, relationship_type="USES"
        )
        create_or_get_relationship(
            s, source_entity_id=a.id, target_entity_id=c.id, relationship_type="USES"
        )
        s.commit()
        aid = a.id

    with Session(db.get_engine()) as s:
        ctx = entity_context(s, aid)

    assert ctx is not None
    assert ctx["entity"]["id"] == aid
    related_names = {r["name"] for r in ctx["related_entities"]}
    assert "Knowledge Graph" in related_names
    assert "pgvector" in related_names
    assert all(r.get("origin") in {"manual", "extractor"} for r in ctx["relationships"])
    # Codex review: keep suggested queries small.
    assert len(ctx["suggested_queries"]) <= 2


def test_entity_context_returns_none_for_missing(tmp_path, monkeypatch):
    db = _init(tmp_path, monkeypatch)
    from personify.services.graph import entity_context

    with Session(db.get_engine()) as s:
        assert entity_context(s, 999_999) is None


def test_neighborhood_depth2_does_not_duplicate_edges(tmp_path, monkeypatch):
    """Codex review: a depth-2 walk crossing a boundary edge was emitting that
    edge twice (matched by both the source-side and target-side queries).
    Each Relationship.id must appear at most once in the returned ``edges``.
    """
    db = _init(tmp_path, monkeypatch)
    from personify.services.graph import (
        create_or_get_entity,
        create_or_get_relationship,
        get_entity_neighborhood,
    )

    with Session(db.get_engine(), expire_on_commit=False) as s:
        a = create_or_get_entity(s, type="Project", name="A")
        b = create_or_get_entity(s, type="Topic", name="B")
        c = create_or_get_entity(s, type="Tool", name="C")
        # Linear chain A → B → C. Depth-2 from A traverses both edges; the
        # A-B edge is the boundary that previously got double-counted.
        create_or_get_relationship(
            s, source_entity_id=a.id, target_entity_id=b.id, relationship_type="USES"
        )
        create_or_get_relationship(
            s, source_entity_id=b.id, target_entity_id=c.id, relationship_type="USES"
        )
        s.commit()
        aid = a.id

    with Session(db.get_engine()) as s:
        graph = get_entity_neighborhood(s, aid, depth=2)

    edge_ids = [e.id for e in graph["edges"]]
    assert len(edge_ids) == len(set(edge_ids)), (
        f"depth-2 walk emitted duplicate edge ids: {edge_ids}"
    )
    assert len(edge_ids) == 2  # exactly the two relationships in the chain


def test_neighborhood_depth2_with_back_edge_no_duplicates(tmp_path, monkeypatch):
    """Triangular structure where a back-edge could re-enter the visited set.
    Validates dedup holds even when the BFS frontier shrinks before expanding.
    """
    db = _init(tmp_path, monkeypatch)
    from personify.services.graph import (
        create_or_get_entity,
        create_or_get_relationship,
        get_entity_neighborhood,
    )

    with Session(db.get_engine(), expire_on_commit=False) as s:
        a = create_or_get_entity(s, type="Project", name="A")
        b = create_or_get_entity(s, type="Topic", name="B")
        c = create_or_get_entity(s, type="Tool", name="C")
        create_or_get_relationship(
            s, source_entity_id=a.id, target_entity_id=b.id, relationship_type="USES"
        )
        create_or_get_relationship(
            s, source_entity_id=a.id, target_entity_id=c.id, relationship_type="USES"
        )
        # Triangle closer — without dedup this would be added in iteration 2
        # because both endpoints are already visited.
        create_or_get_relationship(
            s, source_entity_id=b.id, target_entity_id=c.id, relationship_type="RELATED_TO"
        )
        s.commit()
        aid = a.id

    with Session(db.get_engine()) as s:
        graph = get_entity_neighborhood(s, aid, depth=2)

    edge_ids = [e.id for e in graph["edges"]]
    assert len(edge_ids) == len(set(edge_ids))
    assert len(edge_ids) == 3
