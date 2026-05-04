from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

from sqlmodel import Session, or_, select

from personify.models import Entity, EntityAlias, EntityEvidence, Relationship, RelationshipEvidence

ENTITY_TYPES = {
    "Project", "Person", "Company", "Product", "Repository", "File", "Document", "Email",
    "Conversation", "Idea", "Task", "Decision", "Tool", "Model", "API", "Dataset", "Domain",
    "Client", "Transaction", "Event", "Location", "Topic",
}
RELATIONSHIP_TYPES = {
    "RELATED_TO", "PART_OF", "USES", "CREATED_BY", "MENTIONS", "DEPENDS_ON", "BLOCKED_BY",
    "HAS_IDEA", "HAS_TASK", "HAS_DECISION", "HAS_SOURCE", "SIMILAR_TO", "OWNED_BY", "WORKS_WITH",
    "REFERENCES", "IMPLEMENTS", "AFFECTS", "REQUIRES",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_entity_name(name: str) -> str:
    clean = re.sub(r"[\.,;:!?()\[\]{}\"']", " ", name.strip().lower())
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def add_entity_alias(s: Session, entity_id: int, alias: str, source: Optional[str] = None) -> EntityAlias:
    normalized = normalize_entity_name(alias)
    existing = s.exec(
        select(EntityAlias).where(EntityAlias.entity_id == entity_id, EntityAlias.normalized_alias == normalized)
    ).first()
    if existing:
        return existing
    row = EntityAlias(entity_id=entity_id, alias=alias, normalized_alias=normalized, source=source)
    s.add(row)
    s.flush()
    return row


def _promote_origin_if_manual(row: Any, origin: str) -> None:
    """Promote a graph row's origin from 'extractor' to 'manual' on manual upsert.

    The reverse direction is never taken: an extractor pass must not demote a
    manually-confirmed row, since manual is the stronger pin (it shields the
    row from reset-time pruning). Anything that isn't 'manual' coming in is a
    no-op so future origin values (e.g. 'imported', 'llm') don't accidentally
    overwrite a manual confirmation.
    """
    if origin == "manual" and getattr(row, "origin", None) == "extractor":
        row.origin = "manual"


def create_or_get_entity(
    s: Session,
    *,
    type: str,
    name: str,
    description: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    confidence: Optional[float] = None,
    database_id: Optional[str] = None,
    origin: str = "manual",
) -> Entity:
    """Upsert an entity by (database_id, type, canonical_name).

    `origin` is set on first insert. On a hit, an extractor-origin row is
    promoted to 'manual' when the caller is manual — otherwise the row is
    returned unchanged. Promotion is one-way: manual confirmations pin the
    row against future reset-time pruning.
    """
    if type not in ENTITY_TYPES:
        raise ValueError(f"unsupported entity type: {type}")
    canonical = normalize_entity_name(name)
    existing = s.exec(
        select(Entity).where(Entity.database_id == database_id, Entity.type == type, Entity.canonical_name == canonical)
    ).first()
    if existing:
        _promote_origin_if_manual(existing, origin)
        return existing
    alias_hit = s.exec(select(EntityAlias).where(EntityAlias.normalized_alias == canonical)).first()
    if alias_hit:
        target = s.get(Entity, alias_hit.entity_id)
        if target and target.type == type and target.database_id == database_id:
            _promote_origin_if_manual(target, origin)
            return target
    entity = Entity(
        database_id=database_id,
        type=type,
        name=name,
        canonical_name=canonical,
        description=description,
        metadata_json=metadata or {},
        confidence=confidence,
        origin=origin,
    )
    s.add(entity)
    s.flush()
    return entity


def create_or_get_relationship(
    s: Session,
    *,
    source_entity_id: int,
    target_entity_id: int,
    relationship_type: str,
    confidence: Optional[float] = None,
    metadata: Optional[dict[str, Any]] = None,
    database_id: Optional[str] = None,
    origin: str = "manual",
) -> Relationship:
    """Upsert a relationship by (source, target, type).

    `origin` is set only on first insert and is used by reset_export to decide
    whether a relationship may be pruned when its item-backed evidence is gone.
    """
    if relationship_type not in RELATIONSHIP_TYPES:
        raise ValueError(f"unsupported relationship type: {relationship_type}")
    existing = s.exec(
        select(Relationship).where(
            Relationship.source_entity_id == source_entity_id,
            Relationship.target_entity_id == target_entity_id,
            Relationship.relationship_type == relationship_type,
        )
    ).first()
    if existing:
        _promote_origin_if_manual(existing, origin)
        return existing
    row = Relationship(
        database_id=database_id,
        source_entity_id=source_entity_id,
        target_entity_id=target_entity_id,
        relationship_type=relationship_type,
        confidence=confidence,
        metadata_json=metadata or {},
        origin=origin,
    )
    s.add(row)
    s.flush()
    return row


def add_entity_evidence(s: Session, **kwargs: Any) -> EntityEvidence:
    row = EntityEvidence(**kwargs)
    s.add(row)
    s.flush()
    return row


def add_relationship_evidence(s: Session, **kwargs: Any) -> RelationshipEvidence:
    row = RelationshipEvidence(**kwargs)
    s.add(row)
    s.flush()
    return row


def search_entities(s: Session, query: str, type: Optional[str] = None, limit: int = 20) -> list[Entity]:
    q = f"%{query.lower()}%"
    stmt = select(Entity).where(
        or_(Entity.name.ilike(q), Entity.canonical_name.ilike(q), Entity.id.in_(
            select(EntityAlias.entity_id).where(EntityAlias.normalized_alias.ilike(q))
        ))
    )
    if type:
        stmt = stmt.where(Entity.type == type)
    return list(s.exec(stmt.limit(limit)).all())


def get_entity_full(s: Session, entity_id: int) -> Optional[dict[str, Any]]:
    """One entity with its aliases and item-backed evidence rows.

    Returns ``None`` when no entity exists with that id; callers translate
    that to 404 (HTTP) or an MCP error.
    """
    entity = s.get(Entity, entity_id)
    if not entity:
        return None
    aliases = list(s.exec(select(EntityAlias).where(EntityAlias.entity_id == entity_id)).all())
    evidence = list(
        s.exec(select(EntityEvidence).where(EntityEvidence.entity_id == entity_id)).all()
    )
    return {
        "entity": _entity_dict(entity),
        "aliases": [_alias_dict(a) for a in aliases],
        "evidence": [_entity_evidence_dict(e) for e in evidence],
    }


def _entity_dict(e: Entity) -> dict[str, Any]:
    return {
        "id": e.id,
        "type": e.type,
        "name": e.name,
        "canonical_name": e.canonical_name,
        "description": e.description,
        "metadata": e.metadata_json,
        "origin": e.origin,
        "confidence": float(e.confidence) if e.confidence is not None else None,
    }


def _alias_dict(a: EntityAlias) -> dict[str, Any]:
    return {
        "id": a.id,
        "alias": a.alias,
        "normalized_alias": a.normalized_alias,
        "source": a.source,
    }


def _entity_evidence_dict(e: EntityEvidence) -> dict[str, Any]:
    return {
        "id": e.id,
        "source_type": e.source_type,
        "source_id": e.source_id,
        "source_uri": e.source_uri,
        "quote": e.quote,
        "metadata": e.metadata_json,
    }


def _relationship_dict(r: Relationship) -> dict[str, Any]:
    return {
        "id": r.id,
        "source_entity_id": r.source_entity_id,
        "target_entity_id": r.target_entity_id,
        "relationship_type": r.relationship_type,
        "metadata": r.metadata_json,
        "origin": r.origin,
        "confidence": float(r.confidence) if r.confidence is not None else None,
    }


# Codex review: keep suggested queries small. Two short prompts is enough to
# nudge an agent without bloating the response or pretending we know the
# user's intent.
_MAX_SUGGESTED_QUERIES = 2


def entity_context(s: Session, entity_id: int) -> Optional[dict[str, Any]]:
    """LLM-friendly context blob: entity + summary + neighborhood + evidence.

    Use this when an agent has identified a specific entity and needs the
    grounding payload to reason about it. Combines :func:`get_entity_full`
    with a depth-1 neighborhood walk.
    """
    graph = get_entity_neighborhood(s, entity_id=entity_id, depth=1)
    center = graph["center"]
    if center is None:
        return None
    aliases = list(s.exec(select(EntityAlias).where(EntityAlias.entity_id == entity_id)).all())
    evidence = list(
        s.exec(select(EntityEvidence).where(EntityEvidence.entity_id == entity_id)).all()
    )
    related = [_entity_dict(n) for n in graph["nodes"] if n.id != center.id]
    edges = [_relationship_dict(r) for r in graph["edges"]]
    suggested = [
        f"What is related to {center.name}?",
        f"Recent decisions about {center.name}",
    ][:_MAX_SUGGESTED_QUERIES]
    return {
        "entity": _entity_dict(center),
        "summary": center.description or "",
        "aliases": [_alias_dict(a) for a in aliases],
        "related_entities": related,
        "relationships": edges,
        "evidence": [_entity_evidence_dict(e) for e in evidence],
        "suggested_queries": suggested,
    }


def get_entity_neighborhood(s: Session, entity_id: int, depth: int = 1) -> dict[str, Any]:
    """Walk the graph outward from `entity_id` up to `depth` edges.

    Codex review fix: deduplicate relationship ids across iterations. The
    boundary edge between iteration N's frontier and iteration N+1's frontier
    is matched by both queries (its target is in N's frontier, its source is
    in N+1's frontier), so without a seen-set the same row was emitted twice
    in `edges`. `seen_rel_ids` makes the walk idempotent regardless of depth.
    """
    max_depth = max(1, min(2, depth))
    visited = {entity_id}
    frontier = {entity_id}
    edges: list[Relationship] = []
    seen_rel_ids: set[int] = set()
    for _ in range(max_depth):
        next_frontier: set[int] = set()
        rels = list(
            s.exec(
                select(Relationship).where(
                    or_(
                        Relationship.source_entity_id.in_(frontier),
                        Relationship.target_entity_id.in_(frontier),
                    )
                )
            ).all()
        )
        for rel in rels:
            if rel.id in seen_rel_ids:
                continue
            seen_rel_ids.add(rel.id)
            edges.append(rel)
            if rel.source_entity_id not in visited:
                visited.add(rel.source_entity_id)
                next_frontier.add(rel.source_entity_id)
            if rel.target_entity_id not in visited:
                visited.add(rel.target_entity_id)
                next_frontier.add(rel.target_entity_id)
        frontier = next_frontier
        if not frontier:
            break
    nodes = list(s.exec(select(Entity).where(Entity.id.in_(visited))).all())
    center = s.get(Entity, entity_id)
    return {"center": center, "nodes": nodes, "edges": edges}
