from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from personify.db import init_db
from personify.models import EntityAlias
from personify.services.graph import (
    add_entity_evidence,
    create_or_get_entity,
    create_or_get_relationship,
    get_entity_neighborhood,
    search_entities,
)


def _init(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "personify.db"
    monkeypatch.setenv("PERSONIFY_DB_URL", f"sqlite:///{db_path}")

    import personify.config as config
    import personify.db as db

    config.settings = config.Settings()
    db.engine = db.create_engine(config.settings.db_url, echo=False, pool_pre_ping=True)
    init_db()
    return db


def test_entity_upsert_and_alias_matching(tmp_path: Path, monkeypatch) -> None:
    db = _init(tmp_path, monkeypatch)
    with Session(db.get_engine(), expire_on_commit=False) as s:
        a = create_or_get_entity(s, type="Project", name="OmniVox")
        b = create_or_get_entity(s, type="Project", name="OmniVox")
        assert a.id == b.id

        entity = create_or_get_entity(s, type="Project", name="Omni Impact AI Visibility Platform")
        s.add(EntityAlias(entity_id=entity.id, alias="OmniImpact", normalized_alias="omniimpact"))
        s.commit()

        hits = search_entities(s, "OmniImpact")
        assert any(x.id == entity.id for x in hits)


def test_relationship_upsert_evidence_and_neighborhood(tmp_path: Path, monkeypatch) -> None:
    db = _init(tmp_path, monkeypatch)
    with Session(db.get_engine(), expire_on_commit=False) as s:
        pdv = create_or_get_entity(s, type="Project", name="Personal Data Vault")
        kg = create_or_get_entity(s, type="Topic", name="Knowledge Graph")
        pgv = create_or_get_entity(s, type="Tool", name="pgvector")
        ex = create_or_get_entity(s, type="Topic", name="Entity Extraction")

        r1 = create_or_get_relationship(s, source_entity_id=pdv.id, target_entity_id=kg.id, relationship_type="USES")
        r1b = create_or_get_relationship(s, source_entity_id=pdv.id, target_entity_id=kg.id, relationship_type="USES")
        assert r1.id == r1b.id
        create_or_get_relationship(s, source_entity_id=pdv.id, target_entity_id=pgv.id, relationship_type="USES")
        create_or_get_relationship(s, source_entity_id=kg.id, target_entity_id=ex.id, relationship_type="RELATED_TO")

        add_entity_evidence(s, entity_id=pdv.id, source_type="document", quote="Personal Data Vault USES Knowledge Graph")
        s.commit()

        graph = get_entity_neighborhood(s, pdv.id, depth=2)
        node_ids = {n.id for n in graph["nodes"]}
        assert pdv.id in node_ids
        assert kg.id in node_ids
        assert pgv.id in node_ids
        assert ex.id in node_ids

        rel_types = {r.relationship_type for r in graph["edges"]}
        assert "USES" in rel_types
        assert "RELATED_TO" in rel_types


def test_manual_upsert_promotes_extractor_origin(tmp_path: Path, monkeypatch) -> None:
    """Manual confirmations must pin an extractor-origin row against pruning."""
    db = _init(tmp_path, monkeypatch)
    from personify.models import Entity
    from personify.services.graph import create_or_get_entity

    with Session(db.get_engine(), expire_on_commit=False) as s:
        a = create_or_get_entity(s, type="Project", name="OmniVox", origin="extractor")
        b = create_or_get_entity(s, type="Project", name="OmniVox")  # default origin=manual
        assert a.id == b.id
        assert b.origin == "manual"  # promoted

        # Extractor running again must not demote the manual confirmation back.
        c = create_or_get_entity(s, type="Project", name="OmniVox", origin="extractor")
        assert c.origin == "manual"
        s.commit()

    with Session(db.get_engine(), expire_on_commit=False) as s:
        persisted = s.get(Entity, a.id)
    assert persisted.origin == "manual"


def test_manual_relationship_upsert_promotes_extractor_origin(tmp_path: Path, monkeypatch) -> None:
    db = _init(tmp_path, monkeypatch)
    from personify.models import Relationship
    from personify.services.graph import create_or_get_entity, create_or_get_relationship

    with Session(db.get_engine(), expire_on_commit=False) as s:
        src = create_or_get_entity(s, type="Project", name="A", origin="extractor")
        dst = create_or_get_entity(s, type="Topic", name="B", origin="extractor")
        rel_extractor = create_or_get_relationship(
            s,
            source_entity_id=src.id,
            target_entity_id=dst.id,
            relationship_type="USES",
            origin="extractor",
        )
        assert rel_extractor.origin == "extractor"
        rel_manual = create_or_get_relationship(
            s,
            source_entity_id=src.id,
            target_entity_id=dst.id,
            relationship_type="USES",
        )
        assert rel_manual.id == rel_extractor.id
        assert rel_manual.origin == "manual"
        s.commit()

    with Session(db.get_engine(), expire_on_commit=False) as s:
        persisted = s.get(Relationship, rel_extractor.id)
    assert persisted.origin == "manual"


def test_promoted_entity_survives_reset(tmp_path: Path, monkeypatch, fixtures_dir) -> None:
    """An extractor entity that the user later confirmed manually must outlive reset."""
    db = _init(tmp_path, monkeypatch)
    monkeypatch.setenv("PERSONIFY_VAULT_DIR", str(tmp_path / "vault"))

    import personify.config as config
    import personify.util.vault as vault

    config.settings = config.Settings()
    vault.settings = config.settings
    db.engine = db.create_engine(config.settings.db_url, echo=False, pool_pre_ping=True)
    vault.ensure_vault_layout()

    from personify.models import Entity
    from personify.services.graph import create_or_get_entity
    from personify.services.ingest import reset_export
    from personify.services.pipeline import run_pipeline
    from personify.services.register import register_export

    raw = register_export("files", fixtures_dir / "files", "test")
    run_pipeline(raw.id, with_graph=True)

    # Pick the first extractor File entity; user manually re-asserts it.
    with Session(db.get_engine(), expire_on_commit=False) as s:
        ext = s.exec(select(Entity).where(Entity.origin == "extractor", Entity.type == "File")).first()
        assert ext is not None
        ext_id = ext.id
        ext_name = ext.name
        confirmed = create_or_get_entity(s, type="File", name=ext_name)
        assert confirmed.id == ext_id
        s.commit()

    reset_export(raw.id)

    with Session(db.get_engine(), expire_on_commit=False) as s:
        survivor = s.get(Entity, ext_id)
    assert survivor is not None
    assert survivor.origin == "manual"


def test_graph_api_persists_created_entities_and_relationships(tmp_path: Path, monkeypatch) -> None:
    _init(tmp_path, monkeypatch)

    from personify.api import app

    client = TestClient(app)
    project_resp = client.post(
        "/graph/entities",
        json={"type": "Project", "name": "OmniVox", "aliases": ["OV"]},
    )
    assert project_resp.status_code == 200
    project_id = project_resp.json()["id"]

    topic_resp = client.post("/graph/entities", json={"type": "Topic", "name": "Knowledge Graph"})
    assert topic_resp.status_code == 200
    topic_id = topic_resp.json()["id"]

    assert client.get(f"/graph/entities/{project_id}").status_code == 200
    search_resp = client.get("/graph/entities/search", params={"q": "OV"})
    assert search_resp.status_code == 200
    assert any(entity["id"] == project_id for entity in search_resp.json())

    relationship_resp = client.post(
        "/graph/relationships",
        json={
            "source_entity_id": project_id,
            "target_entity_id": topic_id,
            "relationship_type": "USES",
        },
    )
    assert relationship_resp.status_code == 200

    context_resp = client.get(f"/graph/entities/{project_id}/context")
    assert context_resp.status_code == 200
    assert context_resp.json()["entity"]["id"] == project_id
