"""Pydantic input/output models for MCP tools.

FastMCP infers schemas from type hints, but we centralize the shapes here so
tool signatures stay terse and the LLM-facing schemas are reviewable in one
place. Each model's docstring becomes part of the tool description.

Closed-set validation (Codex review): tools that accept ``entity_type`` or
``relationship_type`` go through the live registries from
:mod:`personify.services.graph` so an agent can't probe the schema with
junk values.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class SearchInput(BaseModel):
    """Free-text search input."""

    query: str = Field(..., min_length=1, description="Free-text query string.")
    limit: int = Field(25, ge=1, le=100, description="Max results to return.")
    source: Optional[str] = Field(
        None,
        description=(
            "Restrict results to one source slug (e.g. 'twitter', 'gmail', "
            "'chatgpt'). Omit to search across all sources."
        ),
    )


class SemanticSearchInput(SearchInput):
    """Semantic similarity search input — same shape as full-text search."""


class TimelineInput(BaseModel):
    """Time-windowed item lookup."""

    start: Optional[datetime] = Field(
        None, description="Inclusive lower bound on item timestamp (ISO-8601)."
    )
    end: Optional[datetime] = Field(
        None, description="Inclusive upper bound on item timestamp (ISO-8601)."
    )
    source: Optional[str] = Field(
        None, description="Restrict to one source slug; omit for all sources."
    )
    limit: int = Field(200, ge=1, le=1000, description="Max items to return.")


class GetItemInput(BaseModel):
    """Item retrieval — full body is opt-in to keep agent context lean."""

    item_id: int = Field(..., description="Numeric Item.id.")
    include_body: bool = Field(
        False,
        description=(
            "When False (default), the body is truncated to 4096 chars and "
            "the response indicates whether truncation happened. Set True to "
            "fetch the full body — only do this when you actually need it."
        ),
    )


class RecentItemsInput(BaseModel):
    """Paginated browse over the items table with optional filters."""

    source: Optional[str] = Field(None)
    account: Optional[str] = Field(None)
    kind: Optional[str] = Field(None, description="Restrict to one item kind, e.g. 'tweet'.")
    limit: int = Field(50, ge=1, le=500)
    offset: int = Field(0, ge=0)


class RecentRunsInput(BaseModel):
    """Most-recent-first list of ingestion runs."""

    limit: int = Field(10, ge=1, le=100)


class GraphSearchEntitiesInput(BaseModel):
    """Find graph entities by name or alias substring."""

    query: str = Field(..., min_length=1)
    type: Optional[str] = Field(
        None,
        description=(
            "Restrict to a specific Entity.type. Must be one of the registered "
            "entity types (Project, Person, Company, etc.); junk values raise."
        ),
    )
    limit: int = Field(20, ge=1, le=100)

    @field_validator("type")
    @classmethod
    def _validate_type(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        # Defer the import to validation time so we always reflect the live
        # registry — a parser PR adding new entity types takes effect without
        # touching this file.
        from personify.services.graph import ENTITY_TYPES

        if v not in ENTITY_TYPES:
            raise ValueError(
                f"unknown entity type {v!r}. Allowed: {sorted(ENTITY_TYPES)!r}"
            )
        return v


class EntityIdInput(BaseModel):
    entity_id: int = Field(..., description="Numeric Entity.id.")


class NeighborhoodInput(BaseModel):
    entity_id: int
    depth: int = Field(
        1, ge=1, le=2, description="Edge-traversal depth — capped at 2 to keep payload bounded."
    )
