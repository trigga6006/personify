"""Heuristic graph extraction over already-ingested items.

This is the *deterministic* first pass: it walks `Item` rows belonging to one
raw export and emits Entity + Relationship + Evidence rows using cheap rules
keyed off the source slug and item metadata. No LLM is invoked here.

Intent: cover the obvious structural entities (conversations, repositories,
people you emailed) so the graph has real content the moment ingestion finishes.
A future LLM-based extractor can run as a separate stage and merge into the
same entities (alias matching already handles dedup).
"""
from __future__ import annotations

from email.utils import getaddresses, parseaddr
from typing import Any, Optional

from sqlmodel import Session, select

from personify.db import session_scope
from personify.models import Item, ItemText
from personify.services.graph import (
    add_entity_evidence,
    add_relationship_evidence,
    create_or_get_entity,
    create_or_get_relationship,
)


def _trim(text: str, limit: int = 280) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _person_name_from_email(addr: str) -> str:
    name, email = parseaddr(addr)
    return name.strip() or email.strip() or addr.strip()


def _emails_from_header(header: str) -> list[tuple[str, str]]:
    """Parse a comma-separated address header into (display_name, email) pairs."""
    out: list[tuple[str, str]] = []
    for name, email in getaddresses([header or ""]):
        email = (email or "").strip()
        if not email or "@" not in email:
            continue
        out.append((name.strip() or email, email.lower()))
    return out


def _evidence_kwargs(item: Item, body: Optional[str]) -> dict[str, Any]:
    return {
        "source_type": "item",
        "source_id": str(item.id) if item.id is not None else None,
        "source_uri": f"item://{item.source_slug}/{item.id}",
        "quote": _trim(body or item.title or ""),
    }


def _process_chatgpt_or_claude(s: Session, item: Item, body: Optional[str]) -> dict[str, int]:
    """ChatGPT/Claude messages → Conversation entity, message linked as evidence."""
    meta = item.metadata_json or {}
    conv_title = meta.get("conversation_title") or item.title or "(untitled conversation)"
    conv_id = meta.get("conversation_id")
    if not conv_title:
        return {"entities_created": 0, "relationships_created": 0, "evidence_added": 0}

    entity = create_or_get_entity(
        s,
        type="Conversation",
        name=str(conv_title),
        metadata={"source": item.source_slug, "conversation_id": conv_id},
        origin="extractor",
    )
    add_entity_evidence(s, entity_id=entity.id, **_evidence_kwargs(item, body))
    return {"entities_created": 1, "relationships_created": 0, "evidence_added": 1}


def _process_gmail(s: Session, item: Item, body: Optional[str]) -> dict[str, int]:
    """Email → Email entity, plus Person entities for from/to with WORKS_WITH links."""
    meta = item.metadata_json or {}
    subject = item.title or "(no subject)"

    email_entity = create_or_get_entity(
        s,
        type="Email",
        name=str(subject),
        metadata={"native_id": item.native_id, "thread": meta.get("thread")},
        origin="extractor",
    )
    evidence_count = 1
    add_entity_evidence(s, entity_id=email_entity.id, **_evidence_kwargs(item, body))

    sender_pairs = _emails_from_header(meta.get("from", ""))
    recipient_pairs = _emails_from_header(meta.get("to", ""))
    sender_entities: list[int] = []
    for display, addr in sender_pairs:
        person = create_or_get_entity(
            s,
            type="Person",
            name=display,
            metadata={"email": addr},
            origin="extractor",
        )
        sender_entities.append(person.id)
        rel = create_or_get_relationship(
            s,
            source_entity_id=person.id,
            target_entity_id=email_entity.id,
            relationship_type="CREATED_BY",
            origin="extractor",
        )
        add_relationship_evidence(s, relationship_id=rel.id, **_evidence_kwargs(item, body))
        evidence_count += 1

    relationships_created = len(sender_entities)
    for display, addr in recipient_pairs:
        person = create_or_get_entity(
            s,
            type="Person",
            name=display,
            metadata={"email": addr},
            origin="extractor",
        )
        rel = create_or_get_relationship(
            s,
            source_entity_id=email_entity.id,
            target_entity_id=person.id,
            relationship_type="MENTIONS",
            origin="extractor",
        )
        add_relationship_evidence(s, relationship_id=rel.id, **_evidence_kwargs(item, body))
        evidence_count += 1
        relationships_created += 1
        # Link senders to recipients as WORKS_WITH so the neighborhood is useful.
        for sender_id in sender_entities:
            if sender_id == person.id:
                continue
            works = create_or_get_relationship(
                s,
                source_entity_id=sender_id,
                target_entity_id=person.id,
                relationship_type="WORKS_WITH",
                origin="extractor",
            )
            add_relationship_evidence(s, relationship_id=works.id, **_evidence_kwargs(item, body))
            evidence_count += 1
            relationships_created += 1

    entities_created = 1 + len(sender_pairs) + len(recipient_pairs)
    return {
        "entities_created": entities_created,
        "relationships_created": relationships_created,
        "evidence_added": evidence_count,
    }


def _process_github(s: Session, item: Item, body: Optional[str]) -> dict[str, int]:
    """GitHub items → Repository entity, optional Person via 'author'/'author_email'."""
    meta = item.metadata_json or {}
    repo_name = meta.get("repo")
    if not repo_name:
        # Commit native_id has shape "<repo>@<sha>".
        if item.native_id and "@" in item.native_id:
            repo_name = item.native_id.split("@", 1)[0]

    entities_created = 0
    relationships_created = 0
    evidence_added = 0

    repo_entity = None
    if repo_name:
        repo_entity = create_or_get_entity(
            s,
            type="Repository",
            name=str(repo_name),
            metadata={"source": item.source_slug},
            origin="extractor",
        )
        add_entity_evidence(s, entity_id=repo_entity.id, **_evidence_kwargs(item, body))
        entities_created += 1
        evidence_added += 1

    # Person author: prefer login, fall back to commit author email.
    author_login = meta.get("author")
    author_email = meta.get("author_email")
    author_name = meta.get("author_name") or author_login or author_email
    if author_name:
        person = create_or_get_entity(
            s,
            type="Person",
            name=str(author_name),
            metadata={
                "github_login": author_login,
                "email": author_email,
            },
            origin="extractor",
        )
        entities_created += 1
        if repo_entity is not None:
            rel = create_or_get_relationship(
                s,
                source_entity_id=person.id,
                target_entity_id=repo_entity.id,
                relationship_type="OWNED_BY",
                origin="extractor",
            )
            add_relationship_evidence(s, relationship_id=rel.id, **_evidence_kwargs(item, body))
            evidence_added += 1
            relationships_created += 1

    return {
        "entities_created": entities_created,
        "relationships_created": relationships_created,
        "evidence_added": evidence_added,
    }


def _process_notion(s: Session, item: Item, body: Optional[str]) -> dict[str, int]:
    if not item.title:
        return {"entities_created": 0, "relationships_created": 0, "evidence_added": 0}
    entity = create_or_get_entity(
        s,
        type="Document",
        name=str(item.title),
        metadata={"source": "notion"},
        origin="extractor",
    )
    add_entity_evidence(s, entity_id=entity.id, **_evidence_kwargs(item, body))
    return {"entities_created": 1, "relationships_created": 0, "evidence_added": 1}


def _process_discord(s: Session, item: Item, body: Optional[str]) -> dict[str, int]:
    meta = item.metadata_json or {}
    channel = meta.get("channel") or item.title
    if not channel:
        return {"entities_created": 0, "relationships_created": 0, "evidence_added": 0}
    entity = create_or_get_entity(
        s,
        type="Conversation",
        name=str(channel),
        metadata={"source": "discord"},
        origin="extractor",
    )
    add_entity_evidence(s, entity_id=entity.id, **_evidence_kwargs(item, body))
    return {"entities_created": 1, "relationships_created": 0, "evidence_added": 1}


def _process_twitter(s: Session, item: Item, body: Optional[str]) -> dict[str, int]:
    """Twitter/X items → Person (author + mentions) and a Document for the tweet/DM/like."""
    meta = item.metadata_json or {}
    screen_name = meta.get("screen_name")
    mentions = meta.get("mentions") or []
    if not isinstance(mentions, list):
        mentions = []

    entities_created = 0
    relationships_created = 0
    evidence_added = 0

    author_entity = None
    if screen_name:
        author_entity = create_or_get_entity(
            s,
            type="Person",
            name=f"@{screen_name}",
            metadata={"twitter_handle": screen_name, "source": "twitter"},
            origin="extractor",
        )
        entities_created += 1
        # Always attribute the author to the source item so reset_export can
        # prune them when the underlying items go away. Without this row, a
        # DM-only archive would leave the author entity unreachable from
        # reset's evidence-keyed candidate set.
        add_entity_evidence(s, entity_id=author_entity.id, **_evidence_kwargs(item, body))
        evidence_added += 1

    if item.kind == "dm":
        # Skip DM document entity — the conversation is already private and the
        # mention extraction below is what makes the graph useful. The author
        # entity above carries the item-backed evidence so it remains prunable.
        pass
    else:
        title = item.title or f"tweet {item.native_id or ''}".strip()
        tweet_entity = create_or_get_entity(
            s,
            type="Document",
            name=title,
            metadata={"source": "twitter", "kind": item.kind, "native_id": item.native_id},
            origin="extractor",
        )
        add_entity_evidence(s, entity_id=tweet_entity.id, **_evidence_kwargs(item, body))
        entities_created += 1
        evidence_added += 1
        if author_entity is not None:
            rel = create_or_get_relationship(
                s,
                source_entity_id=author_entity.id,
                target_entity_id=tweet_entity.id,
                relationship_type="CREATED_BY",
                origin="extractor",
            )
            add_relationship_evidence(s, relationship_id=rel.id, **_evidence_kwargs(item, body))
            evidence_added += 1
            relationships_created += 1

    for mention in mentions:
        if not mention or not isinstance(mention, str):
            continue
        person = create_or_get_entity(
            s,
            type="Person",
            name=f"@{mention}",
            metadata={"twitter_handle": mention, "source": "twitter"},
            origin="extractor",
        )
        entities_created += 1
        if author_entity is not None and author_entity.id != person.id:
            rel = create_or_get_relationship(
                s,
                source_entity_id=author_entity.id,
                target_entity_id=person.id,
                relationship_type="MENTIONS",
                origin="extractor",
            )
            add_relationship_evidence(s, relationship_id=rel.id, **_evidence_kwargs(item, body))
            evidence_added += 1
            relationships_created += 1

    return {
        "entities_created": entities_created,
        "relationships_created": relationships_created,
        "evidence_added": evidence_added,
    }


def _process_files(s: Session, item: Item, body: Optional[str]) -> dict[str, int]:
    if not item.title:
        return {"entities_created": 0, "relationships_created": 0, "evidence_added": 0}
    entity = create_or_get_entity(
        s,
        type="File",
        name=str(item.title),
        metadata={"source": "files", "kind": item.kind},
        origin="extractor",
    )
    add_entity_evidence(s, entity_id=entity.id, **_evidence_kwargs(item, body))
    return {"entities_created": 1, "relationships_created": 0, "evidence_added": 1}


_SOURCE_HANDLERS = {
    "chatgpt": _process_chatgpt_or_claude,
    "claude": _process_chatgpt_or_claude,
    "gmail": _process_gmail,
    "github": _process_github,
    "notion": _process_notion,
    "discord": _process_discord,
    "files": _process_files,
    "twitter": _process_twitter,
}


def extract_graph_for_export(raw_export_id: int) -> dict[str, int]:
    """Walk every item belonging to a raw export and emit graph rows.

    Returns a counters dict suitable for surfacing in PipelineStage.metadata.
    Ignores items from sources that don't have a registered handler so we
    never silently invent entities for sources we haven't reasoned about.
    """
    counters = {
        "entities_created": 0,
        "relationships_created": 0,
        "evidence_added": 0,
        "items_processed": 0,
        "items_skipped": 0,
    }
    with session_scope() as s:
        items = list(
            s.exec(select(Item).where(Item.raw_export_id == raw_export_id)).all()
        )
        if not items:
            return counters
        text_rows = {
            t.item_id: t
            for t in s.exec(
                select(ItemText).where(ItemText.item_id.in_([i.id for i in items if i.id]))
            ).all()
        }
        for item in items:
            handler = _SOURCE_HANDLERS.get(item.source_slug)
            if handler is None:
                counters["items_skipped"] += 1
                continue
            body = text_rows.get(item.id).body if text_rows.get(item.id) else None
            stats = handler(s, item, body)
            counters["entities_created"] += stats.get("entities_created", 0)
            counters["relationships_created"] += stats.get("relationships_created", 0)
            counters["evidence_added"] += stats.get("evidence_added", 0)
            counters["items_processed"] += 1
    return counters
