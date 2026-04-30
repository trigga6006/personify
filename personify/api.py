from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from personify import __version__
from personify.db import get_session
from personify.models import Item, ItemMedia, ItemText, Source, Tag
from personify.services.search import semantic_search, text_search
from personify.services.stats import collect_stats
from personify.web.routes import STATIC_DIR, router as web_router

app = FastAPI(title="Personify Vault API", version=__version__)
app.include_router(web_router)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = 25
    source: Optional[str] = None


class SemanticSearchRequest(SearchRequest):
    pass


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
def get_item(item_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    item = s.get(Item, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="not found")
    text_row = s.exec(select(ItemText).where(ItemText.item_id == item_id)).first()
    media = list(s.exec(select(ItemMedia).where(ItemMedia.item_id == item_id)).all())
    tags = list(s.exec(select(Tag).where(Tag.item_id == item_id)).all())
    return {
        "id": item.id,
        "source": item.source_slug,
        "account": item.account_handle,
        "raw_export_id": item.raw_export_id,
        "ingestion_run_id": item.ingestion_run_id,
        "native_id": item.native_id,
        "kind": item.kind,
        "title": item.title,
        "ts": item.ts.isoformat() if item.ts else None,
        "content_hash": item.content_hash,
        "metadata": item.metadata_json,
        "body": text_row.body if text_row else None,
        "media": [
            {
                "id": m.id,
                "type": m.media_type,
                "mime": m.mime,
                "path": m.path,
                "sha256": m.sha256,
                "metadata": m.metadata_json,
            }
            for m in media
        ],
        "tags": [{"key": t.key, "value": t.value} for t in tags],
    }


@app.get("/timeline")
def timeline(
    start: Optional[datetime] = Query(default=None),
    end: Optional[datetime] = Query(default=None),
    source: Optional[str] = None,
    limit: int = 200,
    s: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    stmt = select(Item).where(Item.ts.is_not(None))
    if source:
        stmt = stmt.where(Item.source_slug == source)
    if start:
        stmt = stmt.where(Item.ts >= start)
    if end:
        stmt = stmt.where(Item.ts <= end)
    stmt = stmt.order_by(Item.ts.desc()).limit(limit)
    rows = s.exec(stmt).all()
    return [
        {
            "id": i.id,
            "source": i.source_slug,
            "account": i.account_handle,
            "kind": i.kind,
            "title": i.title,
            "ts": i.ts.isoformat() if i.ts else None,
        }
        for i in rows
    ]
