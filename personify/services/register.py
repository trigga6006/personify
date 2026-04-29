from __future__ import annotations

from pathlib import Path
from typing import Optional

from sqlmodel import select

from personify.db import session_scope
from personify.models import Account, RawExport, Source
from personify.parsers import PARSERS
from personify.util.vault import ensure_vault_layout, store_raw_export, write_manifest


def register_export(
    source_slug: str,
    path: Path,
    account_handle: str,
    notes: Optional[str] = None,
) -> RawExport:
    """Copy an export into vault/raw and create a RawExport row.

    Idempotent: if a RawExport with the same SHA256 already exists, returns it.
    """
    if source_slug not in PARSERS:
        raise ValueError(f"Unknown source '{source_slug}'. Known: {sorted(PARSERS)}")

    ensure_vault_layout()
    info = store_raw_export(path, source_slug, account_handle)

    with session_scope() as s:
        if not s.exec(select(Source).where(Source.slug == source_slug)).first():
            s.add(Source(slug=source_slug, label=source_slug.title()))
        if not s.exec(select(Account).where(Account.handle == account_handle)).first():
            s.add(Account(handle=account_handle))
        s.flush()

        existing = s.exec(select(RawExport).where(RawExport.sha256 == info["sha256"])).first()
        if existing:
            return existing

        export = RawExport(
            source_slug=source_slug,
            account_handle=account_handle,
            original_path=info["original_path"],
            stored_path=info["stored_path"],
            size_bytes=info["size_bytes"],
            sha256=info["sha256"],
            notes=notes,
        )
        s.add(export)
        s.flush()
        write_manifest(
            export.id,
            {
                "raw_export_id": export.id,
                "source": source_slug,
                "account": account_handle,
                "sha256": info["sha256"],
                "size_bytes": info["size_bytes"],
                "original_path": info["original_path"],
                "stored_path": info["stored_path"],
            },
        )
        s.refresh(export)
        return export
