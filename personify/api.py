from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from personify import __version__
from personify.db import get_session, init_db
from personify.models import (
    Entity,
    EntityAlias,
    EntityEvidence,
    Item,
    ItemMedia,
    ItemText,
    RelationshipEvidence,
    Source,
    Tag,
)
from personify.services.graph import (
    add_entity_alias,
    create_or_get_entity,
    create_or_get_relationship,
    entity_context as _entity_context_service,
    get_entity_full as _get_entity_full_service,
    get_entity_neighborhood,
    search_entities,
)
from personify.services.items import (
    get_item_full as _get_item_full_service,
    list_timeline as _list_timeline_service,
)
from personify.services.search import semantic_search, text_search
from personify.services.stats import collect_stats
from personify.web.routes import STATIC_DIR, router as web_router

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Apply create_all + idempotent column migrations on every server start.

    Schema additions (new tables, new columns) take effect on next server
    restart without the user re-running `vault init`. Failures are swallowed
    so the process still boots — they'll surface when a request hits the DB.
    """
    try:
        init_db()
    except Exception:  # noqa: BLE001
        pass
    yield


app = FastAPI(title="Personify Vault API", version=__version__, lifespan=_lifespan)
app.include_router(web_router)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Mount the new Svelte/TS frontend at /app if it has been built. The build
# step writes to personify/web/static/app/ via vite.config.ts. Mount is
# conditional so a fresh checkout (no build yet) still boots — the legacy
# /ui keeps working until /app reaches feature parity.
_APP_DIR = STATIC_DIR / "app"
if _APP_DIR.is_dir() and (_APP_DIR / "index.html").exists():
    app.mount("/app", StaticFiles(directory=str(_APP_DIR), html=True), name="app")


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = 25
    source: Optional[str] = None


class SemanticSearchRequest(SearchRequest):
    pass


class CreateEntityRequest(BaseModel):
    type: str
    name: str
    description: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    confidence: Optional[float] = None
    database_id: Optional[str] = None


class CreateRelationshipRequest(BaseModel):
    source_entity_id: int
    target_entity_id: int
    relationship_type: str
    confidence: Optional[float] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    database_id: Optional[str] = None


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": __version__}


@app.get("/sources")
def list_sources(s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return [
        {"slug": x.slug, "label": x.label, "created_at": x.created_at.isoformat()}
        for x in s.exec(select(Source)).all()
    ]


@app.get("/stats")
def stats() -> dict[str, Any]:
    return collect_stats()


@app.post("/search")
def search(req: SearchRequest) -> list[dict[str, Any]]:
    return text_search(req.query, limit=req.limit, source=req.source)


@app.post("/semantic-search")
def semantic(req: SemanticSearchRequest) -> list[dict[str, Any]]:
    try:
        return semantic_search(req.query, limit=req.limit, source=req.source)
    except ImportError as e:
        raise HTTPException(
            status_code=501,
            detail=f"Embedding backend unavailable: {e}. Install personify[embeddings].",
        )


@app.get("/items/{item_id}")
def get_item(item_id: int) -> dict[str, Any]:
    # HTTP API returns the full body (no truncation); the MCP `get_item` tool
    # is the path that truncates by default.
    payload = _get_item_full_service(item_id, body_truncate=None)
    if payload is None:
        raise HTTPException(status_code=404, detail="not found")
    return payload


@app.get("/timeline")
def timeline(
    start: Optional[datetime] = Query(default=None),
    end: Optional[datetime] = Query(default=None),
    source: Optional[str] = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    return _list_timeline_service(start=start, end=end, source=source, limit=limit)


@app.get("/graph/entities/search")
def graph_search_entities(
    q: str,
    type: Optional[str] = None,
    limit: int = 20,
    s: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    entities = search_entities(s, query=q, type=type, limit=limit)
    return [{"id": e.id, "type": e.type, "name": e.name, "canonical_name": e.canonical_name} for e in entities]


@app.post("/graph/entities")
def graph_create_entity(req: CreateEntityRequest, s: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        entity = create_or_get_entity(
            s,
            type=req.type,
            name=req.name,
            description=req.description,
            metadata=req.metadata,
            confidence=req.confidence,
            database_id=req.database_id,
        )
        for alias in req.aliases:
            add_entity_alias(s, entity.id, alias)
        s.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"id": entity.id, "type": entity.type, "name": entity.name, "canonical_name": entity.canonical_name}


@app.get("/graph/entities/{entity_id}")
def graph_get_entity(entity_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    payload = _get_entity_full_service(s, entity_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="not found")
    return payload


@app.get("/graph/entities/{entity_id}/neighborhood")
def graph_neighborhood(entity_id: int, depth: int = 1, s: Session = Depends(get_session)) -> dict[str, Any]:
    return get_entity_neighborhood(s, entity_id=entity_id, depth=depth)


@app.get("/graph/entities/{entity_id}/context")
def graph_context(entity_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    payload = _entity_context_service(s, entity_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="not found")
    return payload


@app.post("/graph/relationships")
def graph_create_relationship(req: CreateRelationshipRequest, s: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        rel = create_or_get_relationship(
            s,
            source_entity_id=req.source_entity_id,
            target_entity_id=req.target_entity_id,
            relationship_type=req.relationship_type,
            confidence=req.confidence,
            metadata=req.metadata,
            database_id=req.database_id,
        )
        s.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    evidence = list(s.exec(select(RelationshipEvidence).where(RelationshipEvidence.relationship_id == rel.id)).all())
    return {"id": rel.id, "relationship_type": rel.relationship_type, "evidence_count": len(evidence)}
