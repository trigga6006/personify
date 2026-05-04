"""Tests for ``services.media.resolve_media`` — end-to-end path resolution
plus the path-traversal guard that's the actual security boundary.

The ItemMedia.path column is parser-controlled and the endpoint streams the
file off disk, so a buggy or hostile parser must NOT be able to point a
media row at ``/etc/passwd``. ``resolve_media`` is the single chokepoint
that enforces "this path resolves underneath the active vault dir."
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from personify.services.media import (
    MediaNotFound,
    MediaUnavailable,
    resolve_media,
)


def _init(tmp_path: Path, monkeypatch):
    """Mirror of the bootstrap used in test_services_items_runs.py — SQLite
    DB + tmp vault dir wired into the live ``settings`` singleton."""
    db_path = tmp_path / "personify.db"
    monkeypatch.setenv("PERSONIFY_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("PERSONIFY_VAULT_DIR", str(tmp_path / "vault"))

    import personify.config as config
    import personify.db as db
    import personify.util.vault as vault

    config.settings = config.Settings()
    vault.settings = config.settings
    # Re-wire downstream module-level references that captured the old
    # settings at import time.
    import personify.services.media as media_mod
    media_mod.settings = config.settings

    db.engine = db.create_engine(
        config.settings.db_url, echo=False, pool_pre_ping=True
    )
    vault.ensure_vault_layout()
    db.init_db()
    return db


def _seed_item_with_media(db, *, vault_dir: Path, media_path: Path | str) -> tuple[int, int]:
    """Insert one Item + one ItemMedia pointing at ``media_path``. Returns
    (item_id, media_id)."""
    from personify.models import Item, ItemMedia, RawExport

    with db.Session(db.engine, expire_on_commit=False) as s:
        raw = RawExport(
            source_slug="files",
            account_handle="test",
            original_path="/tmp/fake",
            stored_path=str(vault_dir / "raw" / "files" / "test"),
            size_bytes=1,
            sha256="0" * 64,
        )
        s.add(raw)
        s.flush()

        item = Item(
            source_slug="files",
            account_handle="test",
            raw_export_id=raw.id,
            kind="file",
            content_hash="1" * 64,
            ts=datetime.now(timezone.utc),
        )
        s.add(item)
        s.flush()

        m = ItemMedia(
            item_id=item.id,
            media_type="attachment",
            mime="text/plain",
            path=str(media_path),
            size_bytes=11,
            sha256="2" * 64,
        )
        s.add(m)
        s.commit()
        return item.id, m.id


def test_resolve_media_returns_absolute_path_and_metadata(tmp_path, monkeypatch):
    db = _init(tmp_path, monkeypatch)
    vault_dir = tmp_path / "vault"
    media_file = vault_dir / "staging" / "export_1" / "hello.txt"
    media_file.parent.mkdir(parents=True, exist_ok=True)
    media_file.write_text("hello world")

    item_id, media_id = _seed_item_with_media(
        db, vault_dir=vault_dir, media_path=media_file
    )

    resolved = resolve_media(item_id, media_id)
    assert resolved.absolute_path == media_file.resolve()
    assert resolved.mime == "text/plain"
    assert resolved.media_type == "attachment"
    assert resolved.suggested_filename.endswith("hello.txt")
    assert str(media_id) in resolved.suggested_filename


def test_resolve_media_accepts_path_relative_to_vault_dir(tmp_path, monkeypatch):
    """Parsers can store paths relative to the vault root; the resolver
    joins them onto ``settings.vault_dir`` before validating."""
    db = _init(tmp_path, monkeypatch)
    vault_dir = tmp_path / "vault"
    media_file = vault_dir / "staging" / "rel.txt"
    media_file.parent.mkdir(parents=True, exist_ok=True)
    media_file.write_text("relative payload")

    item_id, media_id = _seed_item_with_media(
        db, vault_dir=vault_dir, media_path="staging/rel.txt"
    )

    resolved = resolve_media(item_id, media_id)
    assert resolved.absolute_path == media_file.resolve()


def test_resolve_media_rejects_path_traversal(tmp_path, monkeypatch):
    """Even if a malicious row points at /etc/passwd or ../../somewhere,
    the resolver must refuse — that's the security boundary."""
    db = _init(tmp_path, monkeypatch)
    vault_dir = tmp_path / "vault"

    # Plant a real file outside the vault so the file-existence check
    # doesn't mask the path-traversal rejection.
    outside = tmp_path / "outside.txt"
    outside.write_text("you should never see this")

    item_id, media_id = _seed_item_with_media(
        db, vault_dir=vault_dir, media_path=str(outside)
    )

    with pytest.raises(MediaUnavailable, match="outside the vault root"):
        resolve_media(item_id, media_id)


def test_resolve_media_rejects_unknown_id(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    with pytest.raises(MediaNotFound):
        resolve_media(item_id=999, media_id=999)


def test_resolve_media_rejects_missing_file(tmp_path, monkeypatch):
    """Row exists, file doesn't — caller deserves a clear error so the
    HTTP layer can map it to 410 Gone instead of 500."""
    db = _init(tmp_path, monkeypatch)
    vault_dir = tmp_path / "vault"
    inside_path = vault_dir / "staging" / "ghost.txt"
    inside_path.parent.mkdir(parents=True, exist_ok=True)
    # Note: NOT writing the file.

    item_id, media_id = _seed_item_with_media(
        db, vault_dir=vault_dir, media_path=str(inside_path)
    )

    with pytest.raises(MediaUnavailable, match="missing on disk"):
        resolve_media(item_id, media_id)
