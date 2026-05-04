"""Resolve ``ItemMedia.path`` rows to safe, on-disk file references.

Why this lives in services/ instead of inline in the route:

- The CLI (``vault media``) and the eventual MCP tool both want the same
  resolution + path-traversal check that the HTTP route does, so the logic
  belongs behind one function.
- The check is security-relevant: ``ItemMedia.path`` is a string column
  populated by parsers, and a malicious or buggy parser must not be able
  to point the row at a file outside the active vault. The resolver below
  re-rejects anything whose canonical path doesn't sit underneath
  ``settings.vault_dir`` *or* the parser's source export directory.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlmodel import select

from personify.config import settings
from personify.db import session_scope
from personify.models import ItemMedia


class MediaNotFound(Exception):
    """No ItemMedia row with that (item_id, media_id) pair."""


class MediaUnavailable(Exception):
    """The row exists but the underlying file doesn't, is empty, or sits
    outside the active vault directory (path-traversal guard)."""


@dataclass(frozen=True)
class ResolvedMedia:
    media_id: int
    item_id: int
    absolute_path: Path
    mime: Optional[str]
    size_bytes: Optional[int]
    sha256: Optional[str]
    media_type: str
    suggested_filename: str


def _is_under(child: Path, parent: Path) -> bool:
    """True if `child` resolves underneath `parent`. Both arguments are
    expected to already be absolute / resolved.

    Uses the string-prefix check rather than ``Path.is_relative_to`` so it
    runs on Python 3.8 too; equivalent semantics with the trailing-separator
    guard preventing ``/vault-evil`` from passing under ``/vault``.
    """
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _suggested_filename(row: ItemMedia, abs_path: Path) -> str:
    """Pick a reasonable download filename: the file's basename, prefixed
    with the media id so two attachments with identical original names
    (e.g. two ``image.png``) don't collide on the user's disk."""
    base = abs_path.name or f"media_{row.id}"
    return f"media_{row.id}_{base}"


def resolve_media(item_id: int, media_id: int) -> ResolvedMedia:
    """Fetch and validate one media row. Raises ``MediaNotFound`` if the
    row doesn't exist (or doesn't belong to ``item_id``), and
    ``MediaUnavailable`` if the row's path is missing on disk or escapes
    the vault root."""
    with session_scope() as s:
        row = s.exec(
            select(ItemMedia).where(
                ItemMedia.id == media_id, ItemMedia.item_id == item_id
            )
        ).first()
        if row is None:
            raise MediaNotFound(f"media {media_id} not found for item {item_id}")

        if not row.path:
            raise MediaUnavailable(f"media {media_id} has no on-disk path")

        candidate = Path(row.path)
        if not candidate.is_absolute():
            # Parsers store paths relative to the vault dir; resolve there.
            candidate = settings.vault_dir / candidate
        try:
            abs_path = candidate.resolve(strict=False)
        except (OSError, RuntimeError) as e:
            raise MediaUnavailable(f"could not resolve media path: {e}") from e

        vault_root = settings.vault_dir.resolve(strict=False)
        if not _is_under(abs_path, vault_root):
            # Path-traversal guard. Even if the row stored an absolute path
            # outside the vault, refuse to serve it.
            raise MediaUnavailable(
                f"media path {abs_path} is outside the vault root {vault_root}"
            )

        if not abs_path.exists() or not abs_path.is_file():
            raise MediaUnavailable(f"media file missing on disk: {abs_path}")

        return ResolvedMedia(
            media_id=row.id or 0,
            item_id=row.item_id,
            absolute_path=abs_path,
            mime=row.mime,
            size_bytes=row.size_bytes,
            sha256=row.sha256,
            media_type=row.media_type,
            suggested_filename=_suggested_filename(row, abs_path),
        )
