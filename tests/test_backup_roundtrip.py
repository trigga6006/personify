"""Round-trip tests for ``services.backup`` — export a populated vault,
restore into a fresh vault, verify the data matches.

Uses SQLite + a tmp vault dir, which exercises the JSON-per-table dump
path. The pg_dump path isn't tested here (would require a live Postgres).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from personify.services.backup import (
    BackupError,
    export_vault,
    restore_vault,
)


def _init(tmp_path: Path, monkeypatch, *, vault_name: str = "personal"):
    """Bootstrap an isolated SQLite vault for one test, returning the
    ``personify.db`` module so callers can spin up sessions.

    ``chdir(tmp_path)`` is critical: ``vault_dir_for_name("personal")``
    returns the relative path ``./vault``, and SQLite URLs with bare
    database names also resolve relative to cwd. Pinning cwd to tmp_path
    keeps every implicit path inside the test's sandbox.
    """
    monkeypatch.chdir(tmp_path)
    db_dir = tmp_path / f"db_{vault_name}"
    db_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PERSONIFY_DB_URL", f"sqlite:///{db_dir / 'p.db'}")
    monkeypatch.setenv("PERSONIFY_VAULT_DIR", str(tmp_path / f"vault_{vault_name}"))
    monkeypatch.setenv("PERSONIFY_VAULT_NAME", vault_name)
    monkeypatch.setenv("PERSONIFY_VAULTS_DIR", str(tmp_path / "vaults"))

    import personify.config as config
    import personify.db as db
    import personify.util.vault as vault
    import personify.services.backup as backup_mod

    config.settings = config.Settings()
    vault.settings = config.settings
    backup_mod.settings = config.settings

    db.engine = db.create_engine(
        config.settings.db_url, echo=False, pool_pre_ping=True
    )
    vault.ensure_vault_layout()
    db.init_db()
    return db


def _seed_one_item_with_media(db, *, vault_dir: Path) -> tuple[int, int, str]:
    """Insert RawExport + Item + ItemText + ItemMedia, with the media file
    actually present on disk under ``vault_dir/staging``. Returns
    ``(item_id, media_id, media_body_text)``."""
    from personify.models import Item, ItemMedia, ItemText, RawExport

    media_payload = "the cat sat on the mat"
    media_file = vault_dir / "staging" / "export_1" / "note.txt"
    media_file.parent.mkdir(parents=True, exist_ok=True)
    media_file.write_text(media_payload)

    with db.Session(db.engine, expire_on_commit=False) as s:
        raw = RawExport(
            source_slug="files",
            account_handle="test",
            original_path="/tmp/orig",
            stored_path=str(media_file),
            size_bytes=media_file.stat().st_size,
            sha256="a" * 64,
        )
        s.add(raw)
        s.flush()

        item = Item(
            source_slug="files",
            account_handle="test",
            raw_export_id=raw.id,
            kind="file",
            title="cat note",
            ts=datetime.now(timezone.utc),
            content_hash="b" * 64,
            metadata_json={"hint": "for round-trip"},
        )
        s.add(item)
        s.flush()
        s.add(ItemText(item_id=item.id, body=media_payload, char_count=len(media_payload)))
        m = ItemMedia(
            item_id=item.id,
            media_type="attachment",
            mime="text/plain",
            path=str(media_file),
            size_bytes=media_file.stat().st_size,
            sha256="c" * 64,
        )
        s.add(m)
        s.commit()
        return item.id, m.id, media_payload


def test_export_writes_bundle_with_manifest_and_tables(tmp_path, monkeypatch):
    db = _init(tmp_path, monkeypatch)
    _seed_one_item_with_media(db, vault_dir=tmp_path / "vault_personal")

    out = tmp_path / "backup.tar.gz"
    result = export_vault(out)

    assert result.bundle_path == out
    assert out.is_file()
    assert "items" in result.tables_written
    assert "item_media" in result.tables_written
    assert "embeddings" not in result.tables_written  # explicitly skipped
    assert result.rows_written > 0
    assert result.files_written >= 1  # the seeded media file


def test_export_normalizes_missing_extension(tmp_path, monkeypatch):
    """Calling ``vault export backup`` without an extension should still
    produce a tarball — we append .tar.gz."""
    db = _init(tmp_path, monkeypatch)
    _seed_one_item_with_media(db, vault_dir=tmp_path / "vault_personal")

    out = tmp_path / "backup"  # no extension
    result = export_vault(out)
    assert result.bundle_path.suffixes[-2:] == [".tar", ".gz"]
    assert result.bundle_path.is_file()


def test_restore_round_trip_preserves_items_and_media(tmp_path, monkeypatch):
    """End-to-end: export a populated vault, restore into a fresh vault
    name, confirm rows + media file came back."""
    db = _init(tmp_path, monkeypatch, vault_name="personal")
    item_id, media_id, body = _seed_one_item_with_media(
        db, vault_dir=tmp_path / "vault_personal"
    )

    out = tmp_path / "backup.tar.gz"
    export_vault(out)

    # Restore into a brand-new vault profile. ``configure_vault`` inside
    # restore_vault flips the active settings; we don't undo that here
    # because the assertions below intentionally read from the restored
    # target.
    result = restore_vault(out, into_vault="restored-copy")
    assert result.vault_name == "restored-copy"
    assert result.rows_restored > 0
    assert result.files_restored >= 1

    # Read the restored DB. The active engine is now pointed at the
    # restored vault by the restore call.
    from personify.models import Item, ItemMedia
    from sqlmodel import select

    with db.Session(db.engine) as s:
        items = list(s.exec(select(Item)).all())
        media_rows = list(s.exec(select(ItemMedia)).all())
    assert len(items) == 1
    assert items[0].title == "cat note"
    assert len(media_rows) == 1

    # The media file should have been copied into the restored vault dir.
    # Bundled paths preserve vault/<sub>/... structure under the new vault
    # root, so look up the file by name inside the new vault dir.
    import personify.config as config

    new_vault_dir = config.settings.vault_dir
    candidates = list(new_vault_dir.rglob("note.txt"))
    assert candidates, f"expected note.txt under {new_vault_dir}"
    assert candidates[0].read_text() == body


def test_restore_refuses_to_overwrite_existing_vault(tmp_path, monkeypatch):
    """Safety: if the target vault has any data, restore aborts."""
    db = _init(tmp_path, monkeypatch, vault_name="personal")
    _seed_one_item_with_media(db, vault_dir=tmp_path / "vault_personal")

    out = tmp_path / "backup.tar.gz"
    export_vault(out)

    # Try to restore back into the same active vault, which already has
    # the seeded data. Should refuse.
    with pytest.raises(BackupError, match="already has rows"):
        restore_vault(out, into_vault="personal")


def test_restore_refuses_target_with_only_raw_exports(tmp_path, monkeypatch):
    """Regression: a target that has registered raw_exports/accounts but no
    items must still refuse — restore deletes every non-skipped table, so
    the emptiness gate has to look at every table it would clobber, not
    just ``items``."""
    from personify.models import Account, RawExport

    db = _init(tmp_path, monkeypatch, vault_name="personal")
    _seed_one_item_with_media(db, vault_dir=tmp_path / "vault_personal")

    out = tmp_path / "backup.tar.gz"
    export_vault(out)

    # Stand up a target vault that has accounts and raw_exports rows but
    # zero items — the case the old `items`-only gate let through.
    db2 = _init(tmp_path, monkeypatch, vault_name="half-set-up")
    with db2.Session(db2.engine, expire_on_commit=False) as s:
        s.add(Account(handle="someone", display_name="Some One"))
        s.add(
            RawExport(
                source_slug="files",
                account_handle="someone",
                original_path="/tmp/x",
                stored_path="/tmp/x",
                size_bytes=0,
                sha256="d" * 64,
            )
        )
        s.commit()

    with pytest.raises(BackupError, match="already has rows"):
        restore_vault(out, into_vault="half-set-up")


def test_restore_allows_freshly_initialized_target(tmp_path, monkeypatch):
    """A vault that's only been ``init_db()``-seeded (sources table populated
    by the parser registry, every other table empty) must NOT trip the
    emptiness gate — that's the normal "create then restore into" path."""
    db = _init(tmp_path, monkeypatch, vault_name="personal")
    _seed_one_item_with_media(db, vault_dir=tmp_path / "vault_personal")

    out = tmp_path / "backup.tar.gz"
    export_vault(out)

    # Initialize the target — this populates `sources` via init_db's
    # parser-registry seed. Other tables remain empty.
    _init(tmp_path, monkeypatch, vault_name="fresh-target")

    # Drop the directory so the disk-side guard doesn't fire; we want to
    # exercise just the DB emptiness check.
    import shutil

    shutil.rmtree(tmp_path / "vault_fresh-target", ignore_errors=True)

    result = restore_vault(out, into_vault="fresh-target")
    assert result.vault_name == "fresh-target"
    assert result.rows_restored > 0


def test_restore_refuses_unknown_bundle_format_version(tmp_path, monkeypatch):
    """If the manifest reports a bundle_format_version this build doesn't
    understand, restore aborts before touching the target."""
    import json
    import tarfile

    _init(tmp_path, monkeypatch)

    # Hand-craft a bogus bundle.
    work = tmp_path / "fake-bundle"
    work.mkdir()
    (work / "db").mkdir()
    (work / "manifest.json").write_text(
        json.dumps(
            {
                "personify_version": "0.0.0",
                "bundle_format_version": 999,  # nonsense value
                "vault_name": "x",
                "created_at": "2026-01-01T00:00:00",
                "tables": [],
                "rows": 0,
            }
        )
    )
    fake = tmp_path / "fake.tar.gz"
    with tarfile.open(fake, "w:gz") as tar:
        for entry in work.rglob("*"):
            if entry.is_file():
                tar.add(entry, arcname=str(entry.relative_to(work)))

    with pytest.raises(BackupError, match="bundle format version"):
        restore_vault(fake, into_vault="should-not-exist")


def test_restore_refuses_tar_slip(tmp_path, monkeypatch):
    """Bundle members whose paths escape the destination must be refused.

    Constructs a tar entry with name='../../evil' and confirms restore
    aborts before extracting anything.
    """
    import tarfile
    import io

    _init(tmp_path, monkeypatch)

    fake = tmp_path / "evil.tar.gz"
    with tarfile.open(fake, "w:gz") as tar:
        info = tarfile.TarInfo(name="../../evil")
        data = b"pwned"
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    with pytest.raises(BackupError, match="escapes destination"):
        restore_vault(fake, into_vault="should-not-exist")
