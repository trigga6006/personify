from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, Index, JSON, String, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from personify.config import settings


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Account(SQLModel, table=True):
    __tablename__ = "accounts"
    id: Optional[int] = Field(default=None, primary_key=True)
    handle: str = Field(index=True, description="Email, username, or arbitrary label")
    display_name: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)

    __table_args__ = (UniqueConstraint("handle", name="uq_accounts_handle"),)


class Source(SQLModel, table=True):
    __tablename__ = "sources"
    id: Optional[int] = Field(default=None, primary_key=True)
    slug: str = Field(index=True, description="chatgpt, claude, gmail, …")
    label: str
    created_at: datetime = Field(default_factory=_utcnow)

    __table_args__ = (UniqueConstraint("slug", name="uq_sources_slug"),)


class RawExport(SQLModel, table=True):
    __tablename__ = "raw_exports"
    id: Optional[int] = Field(default=None, primary_key=True)
    source_slug: str = Field(index=True)
    account_handle: str = Field(index=True)
    original_path: str = Field(sa_column=Column(Text))
    stored_path: str = Field(sa_column=Column(Text), description="Path inside vault/raw")
    size_bytes: int
    sha256: str = Field(index=True, max_length=64)
    received_at: datetime = Field(default_factory=_utcnow)
    notes: Optional[str] = Field(default=None, sa_column=Column(Text))

    __table_args__ = (
        UniqueConstraint(
            "source_slug",
            "account_handle",
            "sha256",
            name="uq_raw_exports_source_account_sha256",
        ),
    )


class IngestionRun(SQLModel, table=True):
    __tablename__ = "ingestion_runs"
    id: Optional[int] = Field(default=None, primary_key=True)
    raw_export_id: int = Field(foreign_key="raw_exports.id", index=True)
    parser_name: str
    parser_version: str
    started_at: datetime = Field(default_factory=_utcnow)
    finished_at: Optional[datetime] = None
    status: str = Field(default="running", description="running|ok|error")
    items_seen: int = 0
    items_inserted: int = 0
    items_skipped: int = 0
    error: Optional[str] = Field(default=None, sa_column=Column(Text))


class Item(SQLModel, table=True):
    __tablename__ = "items"
    id: Optional[int] = Field(default=None, primary_key=True)
    source_slug: str = Field(index=True)
    account_handle: str = Field(index=True)
    raw_export_id: int = Field(foreign_key="raw_exports.id", index=True)
    ingestion_run_id: Optional[int] = Field(default=None, foreign_key="ingestion_runs.id")
    native_id: Optional[str] = Field(default=None, index=True, max_length=512)
    kind: str = Field(index=True, description="message|email|doc|file|commit|page|…")
    title: Optional[str] = Field(default=None, sa_column=Column(Text))
    ts: Optional[datetime] = Field(default=None, index=True)
    content_hash: str = Field(index=True, max_length=64)
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column("metadata", JSON))
    created_at: datetime = Field(default_factory=_utcnow)

    __table_args__ = (
        UniqueConstraint(
            "source_slug",
            "account_handle",
            "native_id",
            name="uq_items_source_account_native",
        ),
        UniqueConstraint(
            "source_slug",
            "account_handle",
            "content_hash",
            name="uq_items_source_account_hash",
        ),
        Index("ix_items_source_ts", "source_slug", "ts"),
    )


class ItemText(SQLModel, table=True):
    __tablename__ = "item_text"
    id: Optional[int] = Field(default=None, primary_key=True)
    item_id: int = Field(foreign_key="items.id", index=True, unique=True)
    body: str = Field(sa_column=Column(Text))
    char_count: int = 0
    lang: Optional[str] = Field(default=None, max_length=8)


class ItemMedia(SQLModel, table=True):
    __tablename__ = "item_media"
    id: Optional[int] = Field(default=None, primary_key=True)
    item_id: int = Field(foreign_key="items.id", index=True)
    media_type: str = Field(description="image|audio|video|attachment|other")
    mime: Optional[str] = Field(default=None, max_length=128)
    path: str = Field(sa_column=Column(Text), description="Path inside vault/staging or normalized")
    size_bytes: Optional[int] = None
    sha256: Optional[str] = Field(default=None, max_length=64)
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column("metadata", JSON))


class Embedding(SQLModel, table=True):
    __tablename__ = "embeddings"
    id: Optional[int] = Field(default=None, primary_key=True)
    item_id: int = Field(foreign_key="items.id", index=True)
    model: str = Field(max_length=128)
    chunk_idx: int = 0
    chunk_text: str = Field(sa_column=Column(Text))
    vector: Any = Field(sa_column=Column(Vector(settings.embed_dim)))

    __table_args__ = (
        UniqueConstraint("item_id", "model", "chunk_idx", name="uq_embeddings_chunk"),
    )


class Tag(SQLModel, table=True):
    __tablename__ = "tags"
    id: Optional[int] = Field(default=None, primary_key=True)
    item_id: int = Field(foreign_key="items.id", index=True)
    key: str = Field(max_length=64, index=True)
    value: str = Field(sa_column=Column(String(256)))

    __table_args__ = (
        UniqueConstraint("item_id", "key", "value", name="uq_tags_item_kv"),
    )
