from sqlmodel import Session, SQLModel, create_engine

from personify.models import GraphEntityAlias
from personify.services.graph import (
    add_entity_alias,
    add_entity_evidence,
    create_or_get_entity,
    create_or_get_relationship,
    get_entity_neighborhood,
    search_entities,
)


def _session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_entity_upsert_and_alias_search() -> None:
    with _session() as s:
        e1 = create_or_get_entity(s, entity_type="Project", name="OmniVox")
        e2 = create_or_get_entity(s, entity_type="Project", name="OmniVox")
        assert e1.id == e2.id
        add_entity_alias(s, e1.id, "Omni Vox")
        hits = search_entities(s, "Omni Vox")
        assert [h.id for h in hits] == [e1.id]
        from sqlmodel import select

        aliases = s.exec(select(GraphEntityAlias)).all()
        assert len(aliases) == 1


def test_relationship_upsert_and_neighborhood_depth_2() -> None:
    with _session() as s:
        pdv = create_or_get_entity(s, entity_type="Project", name="Personal Data Vault")
        kg = create_or_get_entity(s, entity_type="Topic", name="Knowledge Graph")
        pgv = create_or_get_entity(s, entity_type="Tool", name="pgvector")
        ee = create_or_get_entity(s, entity_type="Topic", name="Entity Extraction")
        create_or_get_relationship(
            s, source_entity_id=pdv.id, target_entity_id=kg.id, relationship_type="USES"
        )
        create_or_get_relationship(
            s, source_entity_id=pdv.id, target_entity_id=pgv.id, relationship_type="USES"
        )
        rel = create_or_get_relationship(
            s, source_entity_id=kg.id, target_entity_id=ee.id, relationship_type="RELATED_TO"
        )
        rel2 = create_or_get_relationship(
            s, source_entity_id=kg.id, target_entity_id=ee.id, relationship_type="RELATED_TO"
        )
        assert rel.id == rel2.id
        neighborhood = get_entity_neighborhood(s, pdv.id, depth=2)
        node_names = {n.name for n in neighborhood["nodes"]}
        assert {"Personal Data Vault", "Knowledge Graph", "pgvector", "Entity Extraction"} <= node_names


def test_entity_evidence_creation() -> None:
    with _session() as s:
        entity = create_or_get_entity(s, entity_type="Project", name="Omni Impact")
        evidence = add_entity_evidence(
            s,
            entity_id=entity.id,
            source_type="document",
            source_uri="file://spec.md",
            quote="Omni Impact project notes",
            metadata_json={},
        )
        assert evidence.entity_id == entity.id
