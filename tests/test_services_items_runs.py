"""Unit tests for the extracted item / run services.

These services are now the single source of truth for the HTTP routes and
the MCP tools — testing them in isolation catches divergence at the layer
where it matters, before either surface gets exercised.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, select


def _init(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "personify.db"
    monkeypatch.setenv("PERSONIFY_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("PERSONIFY_VAULT_DIR", str(tmp_path / "vault"))

    import personify.config as config
    import personify.db as db
    import personify.util.vault as vault

    config.settings = config.Settings()
    vault.settings = config.settings
    db.engine = db.create_engine(config.settings.db_url, echo=False, pool_pre_ping=True)
    vault.ensure_vault_layout()
    db.init_db()
    return db


# --- services/items.py ----------------------------------------------------

def test_list_items_paginates_and_filters(tmp_path, monkeypatch, fixtures_dir):
    _init(tmp_path, monkeypatch)
    from personify.services.ingest import ingest_export
    from personify.services.items import list_items
    from personify.services.register import register_export

    raw = register_export("files", fixtures_dir / "files", "test")
    ingest_export(raw.id)

    page = list_items(limit=10)
    assert page["total"] == 2
    assert page["limit"] == 10
    assert page["offset"] == 0
    assert {it["source"] for it in page["items"]} == {"files"}

    filtered = list_items(source="chatgpt", limit=10)
    assert filtered["total"] == 0
    assert filtered["items"] == []

    page2 = list_items(limit=1, offset=1)
    assert len(page2["items"]) == 1
    assert page2["offset"] == 1


def test_list_items_ordering_is_stable(tmp_path, monkeypatch, fixtures_dir):
    """ts DESC NULLS LAST, id DESC — same item at the same offset every call."""
    _init(tmp_path, monkeypatch)
    from personify.services.ingest import ingest_export
    from personify.services.items import list_items
    from personify.services.register import register_export

    raw = register_export("files", fixtures_dir / "files", "test")
    ingest_export(raw.id)

    a = [it["id"] for it in list_items(limit=10)["items"]]
    b = [it["id"] for it in list_items(limit=10)["items"]]
    assert a == b


def test_list_timeline_filters_by_date_and_excludes_undated(tmp_path, monkeypatch, fixtures_dir):
    _init(tmp_path, monkeypatch)
    from personify.services.ingest import ingest_export
    from personify.services.items import list_timeline
    from personify.services.register import register_export

    raw = register_export("twitter", fixtures_dir / "twitter", "personify_user")
    ingest_export(raw.id)

    # Twitter fixture has 2024-04-{03,04,05} timestamps + an undated like.
    early = list_timeline(end=datetime(2024, 4, 4, tzinfo=timezone.utc))
    assert all(it["ts"] is not None for it in early)
    early_ts = [it["ts"][:10] for it in early]
    assert "2024-04-03" in early_ts
    assert "2024-04-05" not in early_ts

    full = list_timeline(limit=200)
    # Likes have ts=None and must NOT appear in the timeline.
    assert all(it["ts"] is not None for it in full)


def test_get_item_full_returns_none_for_missing(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    from personify.services.items import get_item_full

    assert get_item_full(999_999) is None


def test_get_item_full_truncates_body_by_default(tmp_path, monkeypatch, fixtures_dir):
    db = _init(tmp_path, monkeypatch)
    from personify.services.ingest import ingest_export
    from personify.services.items import get_item_full
    from personify.services.register import register_export
    from personify.models import Item, ItemText

    raw = register_export("files", fixtures_dir / "files", "test")
    ingest_export(raw.id)

    # Force a long body so truncation is observable.
    with Session(db.get_engine()) as s:
        item = s.exec(select(Item).where(Item.raw_export_id == raw.id)).first()
        text = s.exec(select(ItemText).where(ItemText.item_id == item.id)).first()
        text.body = "x" * 10_000
        s.add(text)
        s.commit()
        item_id = item.id

    truncated = get_item_full(item_id, body_truncate=4096)
    assert truncated is not None
    assert len(truncated["body"]) == 4096
    assert truncated["body_truncated"] is True
    assert truncated["body_full_chars"] == 10_000

    full = get_item_full(item_id, body_truncate=None)
    assert len(full["body"]) == 10_000
    assert full["body_truncated"] is False


# --- services/runs.py -----------------------------------------------------

def test_list_recent_runs_orders_most_recent_first(tmp_path, monkeypatch, fixtures_dir):
    _init(tmp_path, monkeypatch)
    from personify.services.ingest import ingest_export
    from personify.services.register import register_export
    from personify.services.runs import list_recent_runs

    raw_a = register_export("files", fixtures_dir / "files", "test-a")
    ingest_export(raw_a.id)
    raw_b = register_export("twitter", fixtures_dir / "twitter", "personify_user")
    ingest_export(raw_b.id)

    runs = list_recent_runs(limit=5)
    assert len(runs) == 2
    # Most recent run is twitter (registered last).
    assert runs[0]["raw_export_id"] == raw_b.id
    # Each summary has the canonical shape.
    for r in runs:
        for key in ("id", "status", "parser", "items_seen", "started_at"):
            assert key in r
