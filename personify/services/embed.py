from __future__ import annotations

from functools import lru_cache
from typing import Sequence

from sqlmodel import select

from personify.config import settings
from personify.db import session_scope
from personify.models import Embedding, Item, ItemText


@lru_cache(maxsize=1)
def _model():
    """Load the sentence-transformers model lazily.

    Embeddings are optional. If sentence-transformers isn't installed, callers
    should catch ImportError and degrade to text search.
    """
    from sentence_transformers import SentenceTransformer  # type: ignore

    return SentenceTransformer(settings.embed_model)


def embed_texts(texts: Sequence[str]) -> list[list[float]]:
    m = _model()
    vecs = m.encode(list(texts), normalize_embeddings=True, show_progress_bar=False)
    return [list(map(float, v)) for v in vecs]


def _chunk(text: str, max_chars: int = 1500) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    out: list[str] = []
    for i in range(0, len(text), max_chars):
        out.append(text[i : i + max_chars])
    return out


def embed_pending(limit: int = 500) -> int:
    """Compute embeddings for items that don't yet have any. Returns count."""
    inserted = 0
    with session_scope() as s:
        stmt = (
            select(Item, ItemText)
            .join(ItemText, ItemText.item_id == Item.id)
            .outerjoin(Embedding, Embedding.item_id == Item.id)
            .where(Embedding.id.is_(None))
            .limit(limit)
        )
        rows = s.exec(stmt).all()
        if not rows:
            return 0
        chunks_per_item: list[tuple[int, list[str]]] = []
        all_chunks: list[str] = []
        for item, text in rows:
            cs = _chunk(text.body)
            chunks_per_item.append((item.id, cs))
            all_chunks.extend(cs)
        if not all_chunks:
            return 0
        vecs = embed_texts(all_chunks)
        cursor = 0
        for item_id, cs in chunks_per_item:
            for idx, c in enumerate(cs):
                v = vecs[cursor]
                cursor += 1
                s.add(
                    Embedding(
                        item_id=item_id,
                        model=settings.embed_model,
                        chunk_idx=idx,
                        chunk_text=c,
                        vector=v,
                    )
                )
                inserted += 1
    return inserted
