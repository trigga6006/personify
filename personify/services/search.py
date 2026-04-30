from __future__ import annotations

from typing import Any

from sqlalchemy import func, text
from sqlmodel import select

from personify.db import session_scope
from personify.models import Item, ItemText


def text_search(query: str, limit: int = 25, source: str | None = None) -> list[dict[str, Any]]:
    """Postgres full-text search via plainto_tsquery on item_text.body."""
    if not query.strip():
        return []
    with session_scope() as s:
        ts = func.plainto_tsquery("english", query)
        rank = func.ts_rank_cd(func.to_tsvector("english", ItemText.body), ts).label("rank")
        stmt = (
            select(Item, ItemText, rank)
            .join(ItemText, ItemText.item_id == Item.id)
            .where(func.to_tsvector("english", ItemText.body).op("@@")(ts))
            .order_by(rank.desc())
            .limit(limit)
        )
        if source:
            stmt = stmt.where(Item.source_slug == source)
        rows = s.exec(stmt).all()
        out = []
        for item, txt, score in rows:
            snippet = (txt.body or "")[:280]
            out.append(
                {
                    "id": item.id,
                    "source": item.source_slug,
                    "account": item.account_handle,
                    "kind": item.kind,
                    "title": item.title,
                    "ts": item.ts.isoformat() if item.ts else None,
                    "score": float(score) if score is not None else None,
                    "snippet": snippet,
                }
            )
        return out


def semantic_search(query: str, limit: int = 25, source: str | None = None) -> list[dict[str, Any]]:
    """pgvector cosine similarity. Requires embeddings + sentence-transformers."""
    from personify.services.embed import embed_texts

    qvec = embed_texts([query])[0]
    with session_scope() as s:
        # Use raw SQL for the <=> operator with parameter binding.
        sql = """
            SELECT e.item_id,
                   e.chunk_idx,
                   e.chunk_text,
                   (e.vector <=> CAST(:qvec AS vector)) AS distance
            FROM embeddings e
            JOIN items i ON i.id = e.item_id
            {where}
            ORDER BY e.vector <=> CAST(:qvec AS vector) ASC
            LIMIT :lim
        """
        where = "WHERE i.source_slug = :src" if source else ""
        params: dict[str, Any] = {"qvec": qvec, "lim": limit}
        if source:
            params["src"] = source
        rows = s.exec(text(sql.format(where=where)).bindparams(**params)).all()  # type: ignore[arg-type]
        out: list[dict[str, Any]] = []
        for item_id, chunk_idx, chunk_text, distance in rows:
            item = s.get(Item, item_id)
            if not item:
                continue
            out.append(
                {
                    "id": item.id,
                    "source": item.source_slug,
                    "account": item.account_handle,
                    "kind": item.kind,
                    "title": item.title,
                    "ts": item.ts.isoformat() if item.ts else None,
                    "distance": float(distance),
                    "chunk_idx": int(chunk_idx),
                    "snippet": (chunk_text or "")[:280],
                }
            )
        return out
