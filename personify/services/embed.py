from __future__ import annotations

from functools import lru_cache
from typing import Any, Sequence

from sqlalchemy import func
from sqlmodel import select

from personify.config import settings
from personify.db import session_scope
from personify.models import Embedding, Item, ItemText


def _pick_device() -> tuple[str, str]:
    """Return (device_str, friendly_label) preferring CUDA → DirectML → CPU."""
    try:
        import torch
    except ImportError:
        return "cpu", "CPU (torch not installed)"
    if torch.cuda.is_available():
        try:
            name = torch.cuda.get_device_name(0)
        except Exception:  # noqa: BLE001
            name = "CUDA"
        return "cuda", f"CUDA · {name}"
    # DirectML (AMD/Intel on Windows) — only used if torch-directml is installed.
    try:
        import torch_directml  # type: ignore

        device = torch_directml.device()
        try:
            name = torch_directml.device_name(0)
        except Exception:  # noqa: BLE001
            name = "DirectML"
        return device, f"DirectML · {name}"
    except ImportError:
        pass
    return "cpu", "CPU"


@lru_cache(maxsize=1)
def _model():
    """Load the sentence-transformers model lazily.

    Embeddings are optional. If sentence-transformers isn't installed, callers
    should catch ImportError and degrade to text search.
    """
    from sentence_transformers import SentenceTransformer  # type: ignore

    device, _ = _pick_device()
    return SentenceTransformer(settings.embed_model, device=device)


def get_device_info() -> dict[str, Any]:
    """Cheap lookup for the embed dashboard — never loads the model."""
    try:
        import torch  # noqa: F401
    except ImportError:
        return {
            "device": "cpu",
            "label": "CPU",
            "torch": None,
            "available": False,
            "note": "Install with `.\\.venv\\Scripts\\pip install -e \".[embeddings]\"` to enable embeddings.",
        }
    import torch as _torch

    device, label = _pick_device()
    return {
        "device": device if isinstance(device, str) else str(device),
        "label": label,
        "torch": _torch.__version__,
        "available": True,
    }


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


def embed_stats() -> dict[str, Any]:
    """Counts of items eligible for embedding, items already embedded, etc."""
    with session_scope() as s:
        items_with_text = int(
            s.exec(
                select(func.count(Item.id)).join(ItemText, ItemText.item_id == Item.id)
            ).one()
            or 0
        )
        items_embedded = int(
            s.exec(
                select(func.count(func.distinct(Embedding.item_id)))
            ).one()
            or 0
        )
        total_chunks = int(
            s.exec(select(func.count(Embedding.id))).one() or 0
        )
    return {
        "model": settings.embed_model,
        "embed_dim": settings.embed_dim,
        "items_with_text": items_with_text,
        "items_embedded": items_embedded,
        "items_pending": max(0, items_with_text - items_embedded),
        "total_chunks": total_chunks,
        "device": get_device_info(),
    }


def _embed_rows(s, rows: list) -> int:
    """Embed (item, text) rows that don't yet have embeddings. Returns chunk count."""
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
    inserted = 0
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


def embed_pending(limit: int = 500) -> int:
    """Compute embeddings for items that don't yet have any. Returns count."""
    with session_scope() as s:
        stmt = (
            select(Item, ItemText)
            .join(ItemText, ItemText.item_id == Item.id)
            .outerjoin(Embedding, Embedding.item_id == Item.id)
            .where(Embedding.id.is_(None))
            .limit(limit)
        )
        rows = s.exec(stmt).all()
        return _embed_rows(s, list(rows))


def embed_export(raw_export_id: int) -> int:
    """Embed any unembedded text items belonging to one raw export.

    Used by the pipeline so an export's downstream stage only touches its own
    items rather than every pending item in the vault.
    """
    with session_scope() as s:
        stmt = (
            select(Item, ItemText)
            .join(ItemText, ItemText.item_id == Item.id)
            .outerjoin(Embedding, Embedding.item_id == Item.id)
            .where(Item.raw_export_id == raw_export_id)
            .where(Embedding.id.is_(None))
        )
        rows = s.exec(stmt).all()
        return _embed_rows(s, list(rows))
