from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import uuid4

from sqlmodel import Session, or_, select

from personify.db import session_scope
from personify.models import (
    GraphEntity,
    GraphEntityAlias,
    GraphEntityEvidence,
    GraphRelationship,
    GraphRelationshipEvidence,
)

ENTITY_TYPES = {
    "Project", "Person", "Company", "Product", "Repository", "File", "Document", "Email",
    "Conversation", "Idea", "Task", "Decision", "Tool", "Model", "API", "Dataset", "Domain",
    "Client", "Transaction", "Event", "Location", "Topic",
}
RELATIONSHIP_TYPES = {
    "RELATED_TO", "PART_OF", "USES", "CREATED_BY", "MENTIONS", "DEPENDS_ON", "BLOCKED_BY",
    "HAS_IDEA", "HAS_TASK", "HAS_DECISION", "HAS_SOURCE", "SIMILAR_TO", "OWNED_BY",
    "WORKS_WITH", "REFERENCES", "IMPLEMENTS", "AFFECTS", "REQUIRES",
}


def normalize_entity_name(value: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", " ", value.strip().lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_or_get_entity(
    s: Session, *, entity_type: str, name: str, description: str | None = None, metadata: dict | None = None,
    database_id: str | None = None, confidence: float | None = None
) -> GraphEntity:
    if entity_type not in ENTITY_TYPES:
        raise ValueError(f"unsupported entity type: {entity_type}")
    canonical_name = normalize_entity_name(name)
    entity = s.exec(
        select(GraphEntity).where(
            GraphEntity.type == entity_type,
            GraphEntity.canonical_name == canonical_name,
            GraphEntity.database_id == database_id,
        )
    ).first()
    if entity:
        return entity
    alias_hit = s.exec(
        select(GraphEntityAlias).where(GraphEntityAlias.normalized_alias == canonical_name)
    ).first()
    if alias_hit:
        aliased = s.get(GraphEntity, alias_hit.entity_id)
        if aliased and aliased.type == entity_type and aliased.database_id == database_id:
            return aliased
    entity = GraphEntity(
        id=str(uuid4()),
        database_id=database_id,
        type=entity_type,
        name=name,
        canonical_name=canonical_name,
        description=description,
        metadata_json=metadata or {},
        confidence=confidence,
    )
    s.add(entity)
    s.flush()
    return entity


def add_entity_alias(s: Session, entity_id: str, alias: str, source: str | None = None) -> GraphEntityAlias:
    normalized = normalize_entity_name(alias)
    found = s.exec(
        select(GraphEntityAlias).where(
            GraphEntityAlias.entity_id == entity_id,
            GraphEntityAlias.normalized_alias == normalized,
        )
    ).first()
    if found:
        return found
    row = GraphEntityAlias(
        id=str(uuid4()), entity_id=entity_id, alias=alias, normalized_alias=normalized, source=source
    )
    s.add(row)
    s.flush()
    return row


def create_or_get_relationship(
    s: Session, *, source_entity_id: str, target_entity_id: str, relationship_type: str,
    confidence: float | None = None, metadata: dict | None = None, database_id: str | None = None
) -> GraphRelationship:
    if relationship_type not in RELATIONSHIP_TYPES:
        raise ValueError(f"unsupported relationship type: {relationship_type}")
    rel = s.exec(
        select(GraphRelationship).where(
            GraphRelationship.source_entity_id == source_entity_id,
            GraphRelationship.target_entity_id == target_entity_id,
            GraphRelationship.relationship_type == relationship_type,
        )
    ).first()
    if rel:
        return rel
    rel = GraphRelationship(
        id=str(uuid4()),
        source_entity_id=source_entity_id,
        target_entity_id=target_entity_id,
        relationship_type=relationship_type,
        confidence=confidence,
        metadata_json=metadata or {},
        database_id=database_id,
    )
    s.add(rel)
    s.flush()
    return rel


def add_entity_evidence(s: Session, **kwargs) -> GraphEntityEvidence:
    row = GraphEntityEvidence(id=str(uuid4()), **kwargs)
    s.add(row)
    s.flush()
    return row


def add_relationship_evidence(s: Session, **kwargs) -> GraphRelationshipEvidence:
    row = GraphRelationshipEvidence(id=str(uuid4()), **kwargs)
    s.add(row)
    s.flush()
    return row


def search_entities(s: Session, q: str, entity_type: str | None = None, limit: int = 20) -> list[GraphEntity]:
    qn = normalize_entity_name(q)
    alias_subq = select(GraphEntityAlias.entity_id).where(GraphEntityAlias.normalized_alias.contains(qn))
    query = select(GraphEntity).where(
        or_(GraphEntity.name.contains(q), GraphEntity.canonical_name.contains(qn), GraphEntity.id.in_(alias_subq))
    )
    if entity_type:
        query = query.where(GraphEntity.type == entity_type)
    return list(s.exec(query.limit(limit)).all())


def get_entity_neighborhood(s: Session, entity_id: str, depth: int = 1) -> dict:
    depth = min(max(depth, 1), 2)
    node_ids = {entity_id}
    edges: list[GraphRelationship] = []
    frontier = {entity_id}
    for _ in range(depth):
        rels = list(
            s.exec(
                select(GraphRelationship).where(
                    or_(
                        GraphRelationship.source_entity_id.in_(frontier),
                        GraphRelationship.target_entity_id.in_(frontier),
                    )
                )
            ).all()
        )
        edges.extend([r for r in rels if r.id not in {e.id for e in edges}])
        frontier = set()
        for rel in rels:
            if rel.source_entity_id not in node_ids:
                frontier.add(rel.source_entity_id)
            if rel.target_entity_id not in node_ids:
                frontier.add(rel.target_entity_id)
            node_ids.add(rel.source_entity_id)
            node_ids.add(rel.target_entity_id)
    nodes = list(s.exec(select(GraphEntity).where(GraphEntity.id.in_(node_ids))).all())
    center = s.get(GraphEntity, entity_id)
    return {"center": center, "nodes": nodes, "edges": edges}


def extract_graph_candidates_from_text(**kwargs) -> dict:
    _ = kwargs
    return {"entities": [], "relationships": []}

