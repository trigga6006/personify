"""Vault export / restore — full bundle backup.

Why this exists
---------------
A personal data vault that lives on a single machine has no recovery story
without an explicit backup format. This module produces a portable tarball
that captures both halves of vault state:

1. **Database rows** — every table the application owns, dumped row-by-row
   to per-table JSON files. The JSON-per-table format was chosen over
   ``pg_dump`` for v1 because (a) it works on SQLite for tests, (b) it
   doesn't require ``pg_dump`` / ``psql`` on the user's PATH, and
   (c) it's human-inspectable, which matters when the data being backed
   up is the user's own personal archive.
2. **Filesystem state** — ``vault/raw``, ``vault/staging``,
   ``vault/normalized``, ``vault/manifests``. These hold the bytes that
   ``ItemMedia.path`` rows reference, the original raw exports, and the
   normalized JSON snapshots, so dumping the DB without them would lose
   half the vault.

Embeddings are intentionally skipped. They're (a) large, (b) trivially
regeneratable via ``vault embed``, and (c) tied to the embedding model
version — restoring stale vectors against a different model would silently
return the wrong nearest-neighbors. The restore flow tells the user to
re-run ``vault embed`` if they had embeddings.

Restore safety
--------------
Restore refuses to write into a target vault that already exists on disk
or already has rows in its database. The user must explicitly pass
``--into <new-vault-name>`` and that name must be unallocated, so a
mistyped path can never silently overwrite the active vault.

Format
------
The bundle is a gzipped tar archive containing:

::

    manifest.json
    db/<tablename>.json    (one file per table, JSON array of row dicts)
    files/raw/...
    files/staging/...
    files/normalized/...
    files/manifests/...
"""
from __future__ import annotations

import json
import shutil
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Table, inspect
from sqlmodel import SQLModel

from personify import __version__
from personify.config import (
    configure_vault,
    normalize_vault_name,
    settings,
    vault_dir_for_name,
)
from personify.db import get_engine, init_db, reset_engine
from personify.util.vault import ensure_vault_layout

#: Bundle format version. Bump when the on-disk schema or layout changes
#: in a way that older Personify versions won't recognize.
BUNDLE_FORMAT_VERSION = 1

#: Tables we deliberately don't round-trip. Embeddings are skipped because
#: they're regenerable and cheap to recompute, and because round-tripping
#: pgvector columns through a portable JSON dump is fragile.
SKIPPED_TABLES: frozenset[str] = frozenset({"embeddings"})

#: Tables that ``init_db()`` auto-seeds (the parser registry lands in
#: ``sources``), so a freshly-initialized empty vault has rows here. The
#: emptiness gate ignores these — finding rows in ``sources`` does not
#: mean the user has data worth refusing to overwrite.
AUTO_SEEDED_TABLES: frozenset[str] = frozenset({"sources"})

#: Subdirectories under ``vault/`` that we ship in the bundle. ``logs/`` is
#: deliberately excluded — they're append-only, contain timestamps, and
#: aren't part of the vault's *data*.
BUNDLED_VAULT_SUBDIRS: tuple[str, ...] = (
    "raw",
    "staging",
    "normalized",
    "manifests",
)


class BackupError(Exception):
    """Raised when export or restore can't proceed safely."""


@dataclass
class ExportResult:
    bundle_path: Path
    tables_written: list[str] = field(default_factory=list)
    rows_written: int = 0
    files_written: int = 0
    bytes_written: int = 0


@dataclass
class RestoreResult:
    vault_name: str
    tables_restored: list[str] = field(default_factory=list)
    rows_restored: int = 0
    files_restored: int = 0


# --------------------------------------------------------------------------- #
# Serialization helpers                                                       #
# --------------------------------------------------------------------------- #

def _json_default(value: Any) -> Any:
    """JSON encoder fallback for column types that aren't natively
    serializable. Datetime → ISO string is the only common case once the
    Vector column is excluded."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"unserializable column value of type {type(value).__name__}")


def _row_to_dict(row: Any, table: Table) -> dict[str, Any]:
    """Convert a SQLAlchemy Row / mapping into a plain dict keyed by column
    name, in the table's own column order. Stable column order means the
    JSON dump is diff-friendly."""
    mapping = row._mapping if hasattr(row, "_mapping") else row
    return {col.name: mapping[col.name] for col in table.columns}


def _coerce_for_insert(table: Table, payload: dict[str, Any]) -> dict[str, Any]:
    """Reverse of ``_json_default`` — turn ISO strings back into datetimes
    for columns that the schema declared as DateTime."""
    out: dict[str, Any] = {}
    for col in table.columns:
        if col.name not in payload:
            continue
        v = payload[col.name]
        # Best-effort datetime parse. The columns we care about (created_at,
        # ts, started_at, etc.) all use ISO 8601 via _json_default.
        col_type = str(col.type).lower()
        if v is not None and isinstance(v, str) and "datetime" in col_type:
            try:
                out[col.name] = datetime.fromisoformat(v)
                continue
            except ValueError:
                pass
        out[col.name] = v
    return out


# --------------------------------------------------------------------------- #
# Export                                                                      #
# --------------------------------------------------------------------------- #

def _dump_tables_to_json(out_dir: Path) -> tuple[list[str], int]:
    """Dump every non-skipped table in the live metadata to one JSON file.
    Returns ``(table_names_written, total_rows)``. Tables are dumped in
    metadata order to preserve a deterministic restore sequence (which
    incidentally respects FK ordering for the current schema)."""
    # Force model registration so SQLModel.metadata is populated.
    from personify import models  # noqa: F401

    out_dir.mkdir(parents=True, exist_ok=True)
    tables_written: list[str] = []
    total_rows = 0

    engine = get_engine()
    with engine.connect() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if table.name in SKIPPED_TABLES:
                continue
            stmt = table.select()
            rows = [_row_to_dict(r, table) for r in conn.execute(stmt)]
            (out_dir / f"{table.name}.json").write_text(
                json.dumps(rows, indent=2, default=_json_default),
                encoding="utf-8",
            )
            tables_written.append(table.name)
            total_rows += len(rows)
    return tables_written, total_rows


def _copy_vault_files(target_root: Path) -> tuple[int, int]:
    """Copy bundled vault subdirs into ``target_root/files``. Returns
    ``(files_copied, bytes_copied)``."""
    files_copied = 0
    bytes_copied = 0
    files_root = target_root / "files"
    for sub in BUNDLED_VAULT_SUBDIRS:
        src = settings.vault_dir / sub
        if not src.exists():
            continue
        dest = files_root / sub
        for src_path in src.rglob("*"):
            if src_path.is_dir():
                continue
            rel = src_path.relative_to(src)
            dest_path = dest / rel
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dest_path)
            files_copied += 1
            try:
                bytes_copied += src_path.stat().st_size
            except OSError:
                pass
    return files_copied, bytes_copied


def export_vault(out_path: Path) -> ExportResult:
    """Write a portable bundle of the active vault to ``out_path``.

    The output is a ``.tar.gz`` regardless of the suffix the caller passed —
    the suffix is normalized so users running ``vault export backup``
    don't end up with an extension-less file.
    """
    out_path = Path(out_path)
    if out_path.is_dir():
        raise BackupError(f"out_path {out_path} is a directory; pass a file path")
    if not str(out_path).endswith((".tar.gz", ".tgz")):
        out_path = out_path.with_name(out_path.name + ".tar.gz")

    with tempfile.TemporaryDirectory(prefix="personify-export-") as tmp:
        staging = Path(tmp)
        tables, rows = _dump_tables_to_json(staging / "db")
        files_count, bytes_count = _copy_vault_files(staging)

        manifest = {
            "personify_version": __version__,
            "bundle_format_version": BUNDLE_FORMAT_VERSION,
            "vault_name": settings.vault_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "tables": tables,
            "rows": rows,
            "skipped_tables": sorted(SKIPPED_TABLES),
            "bundled_vault_subdirs": list(BUNDLED_VAULT_SUBDIRS),
            "files": files_count,
            "files_bytes": bytes_count,
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(out_path, "w:gz") as tar:
            for entry in sorted(staging.rglob("*")):
                if entry.is_file():
                    tar.add(entry, arcname=str(entry.relative_to(staging)))

    return ExportResult(
        bundle_path=out_path,
        tables_written=tables,
        rows_written=rows,
        files_written=files_count,
        bytes_written=bytes_count,
    )


# --------------------------------------------------------------------------- #
# Restore                                                                     #
# --------------------------------------------------------------------------- #

def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract a tarball into ``dest`` while rejecting any member whose
    resolved path escapes the destination — defends against zip-slip /
    tar-slip in untrusted bundles."""
    dest_resolved = dest.resolve()
    for member in tar.getmembers():
        member_path = (dest / member.name).resolve()
        try:
            member_path.relative_to(dest_resolved)
        except ValueError as e:
            raise BackupError(
                f"refusing to extract {member.name!r}: escapes destination"
            ) from e
        if member.issym() or member.islnk():
            # Symlinks in a backup tarball are surprising and risky — refuse.
            raise BackupError(
                f"refusing to extract symlink/hardlink {member.name!r}"
            )
    # ``filter='data'`` (Python 3.12+) rejects setuid/setgid bits, absolute
    # paths, and other tarball gotchas in addition to the path check above.
    # Older Pythons silently ignore the kwarg; we re-implement the path
    # guard ourselves above so we're safe either way.
    try:
        tar.extractall(dest, filter="data")
    except TypeError:
        tar.extractall(dest)  # noqa: S202 — guarded above


def _first_populated_table(engine) -> str | None:
    """Return the first non-skipped, non-auto-seeded table that has any rows
    on ``engine``, or ``None`` if every such table is empty.

    ``_insert_table_rows`` deletes EVERY non-skipped table during restore, so
    the emptiness gate must look at every table we'd otherwise clobber — not
    just ``items``. A target with registered exports/accounts/runs but zero
    items would otherwise pass the old check and get silently wiped.
    """
    from personify import models  # noqa: F401 — register metadata

    insp = inspect(engine)
    existing = set(insp.get_table_names())
    with engine.connect() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if table.name in SKIPPED_TABLES or table.name in AUTO_SEEDED_TABLES:
                continue
            if table.name not in existing:
                continue
            row = conn.execute(table.select().limit(1)).first()
            if row is not None:
                return table.name
    return None


def _target_is_empty(name: str) -> tuple[bool, str]:
    """True if ``name`` is unallocated. Returns ``(ok, reason)`` so callers
    can surface a clear error to the user.

    Two checks: the vault directory must not exist with content, and every
    non-skipped, non-auto-seeded table in the target database must be empty.
    Restore deletes every non-skipped table before re-inserting, so the
    emptiness check has to span the same set — checking only ``items``
    would let a half-set-up vault (accounts/exports/runs but no items yet)
    silently get clobbered.

    When the caller is restoring over the *currently active* vault we use
    the live engine (no switching). For a different vault name we briefly
    point ``settings`` at it, do a read-only introspection, then restore —
    failure to introspect a nonexistent target is fine and is treated as
    "empty".
    """
    target_dir = vault_dir_for_name(name)
    if target_dir.exists() and any(target_dir.rglob("*")):
        return False, f"vault directory already exists with content: {target_dir}"

    # Case 1: restoring over the active vault — query the live engine.
    if normalize_vault_name(name) == normalize_vault_name(settings.vault_name):
        engine = get_engine()
        try:
            populated_table = _first_populated_table(engine)
        except Exception as e:  # noqa: BLE001 — surface a clean error
            return False, f"could not introspect active db: {e}"
        if populated_table is not None:
            return False, f"vault {name!r} already has rows in {populated_table}"
        return True, ""

    # Case 2: restoring into a different vault profile — switch briefly
    # and introspect. Inability to connect (e.g. the target DB doesn't
    # exist yet) is not a "populated" signal; treat it as empty.
    saved_db_url = settings.db_url
    saved_vault_name = settings.vault_name
    saved_vault_dir = settings.vault_dir
    populated = False
    reason = ""
    try:
        configure_vault(name)
        reset_engine()
        engine = get_engine()
        try:
            populated_table = _first_populated_table(engine)
            if populated_table is not None:
                populated = True
                reason = f"vault {name!r} already has rows in {populated_table}"
        except Exception:
            # Target DB unreachable — treat as empty. The subsequent
            # restore will create it.
            pass
    finally:
        settings.db_url = saved_db_url
        settings.vault_name = saved_vault_name
        settings.vault_dir = saved_vault_dir
        reset_engine()
    return (not populated), reason


def _load_table_rows(staging: Path) -> dict[str, list[dict[str, Any]]]:
    db_dir = staging / "db"
    if not db_dir.is_dir():
        raise BackupError(f"bundle is missing db/ directory at {db_dir}")
    out: dict[str, list[dict[str, Any]]] = {}
    for json_path in sorted(db_dir.glob("*.json")):
        out[json_path.stem] = json.loads(json_path.read_text(encoding="utf-8"))
    return out


def _insert_table_rows(rows_per_table: dict[str, list[dict[str, Any]]]) -> int:
    """Insert restored rows into the target DB. Tables are inserted in
    SQLModel's metadata sorted_tables order so foreign keys resolve
    correctly without us having to encode FK relationships explicitly.

    ``init_db()`` auto-seeds the ``sources`` table with the parser
    registry, so we DELETE before INSERT to avoid colliding on the
    bundle's source ids. The DELETE is FK-safe because we go in reverse
    metadata order; INSERT then runs in forward order.
    """
    from personify import models  # noqa: F401 — register metadata

    total = 0
    engine = get_engine()
    with engine.begin() as conn:
        # Pre-clear in reverse FK order so any auto-seeded rows
        # (notably ``sources``) are gone before we re-insert from the
        # bundle. Skip tables we don't restore at all.
        for table in reversed(SQLModel.metadata.sorted_tables):
            if table.name in SKIPPED_TABLES:
                continue
            conn.execute(table.delete())
        for table in SQLModel.metadata.sorted_tables:
            if table.name in SKIPPED_TABLES:
                continue
            payload = rows_per_table.get(table.name)
            if not payload:
                continue
            coerced = [_coerce_for_insert(table, row) for row in payload]
            if coerced:
                conn.execute(table.insert(), coerced)
                total += len(coerced)
    return total


def _restore_files(staging: Path, vault_dir: Path) -> int:
    """Copy bundled ``files/<sub>/...`` content back into the target
    vault directory. Returns the count of regular files copied."""
    files_root = staging / "files"
    if not files_root.is_dir():
        return 0
    count = 0
    for src_path in files_root.rglob("*"):
        if src_path.is_dir():
            continue
        rel = src_path.relative_to(files_root)
        dest = vault_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dest)
        count += 1
    return count


def restore_vault(bundle_path: Path, into_vault: str) -> RestoreResult:
    """Restore a bundle produced by :func:`export_vault` into a NEW vault.

    Refuses to overwrite an existing vault — the caller must pass an
    ``into_vault`` name whose vault directory doesn't exist and whose
    database has no items.
    """
    bundle_path = Path(bundle_path)
    if not bundle_path.is_file():
        raise BackupError(f"bundle not found: {bundle_path}")

    vault_name = normalize_vault_name(into_vault)

    ok, reason = _target_is_empty(vault_name)
    if not ok:
        raise BackupError(
            f"refusing to restore into {vault_name!r} — {reason}. "
            "Pass --into <new-vault-name> with an unused name."
        )

    with tempfile.TemporaryDirectory(prefix="personify-restore-") as tmp:
        staging = Path(tmp)
        with tarfile.open(bundle_path, "r:gz") as tar:
            _safe_extract(tar, staging)

        manifest_path = staging / "manifest.json"
        if not manifest_path.is_file():
            raise BackupError("bundle is missing manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        bundle_v = manifest.get("bundle_format_version")
        if bundle_v != BUNDLE_FORMAT_VERSION:
            raise BackupError(
                f"bundle format version {bundle_v} not supported by this "
                f"build (expects {BUNDLE_FORMAT_VERSION})"
            )

        rows_per_table = _load_table_rows(staging)

        # Switch the process to the target vault and create schema.
        configure_vault(vault_name)
        reset_engine()
        ensure_vault_layout()
        init_db()

        total_rows = _insert_table_rows(rows_per_table)
        files_count = _restore_files(staging, settings.vault_dir)

    return RestoreResult(
        vault_name=vault_name,
        tables_restored=manifest.get("tables", []),
        rows_restored=total_rows,
        files_restored=files_count,
    )
