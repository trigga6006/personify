# WORK_LOG — feat/vault-completeness

Branch: `feat/vault-completeness` (off `main`)
Author: working session 2026-05-04
Reviewer: Codex

This branch addresses three of the four gaps surfaced in the recent audit:

1. **Media retrieval** — endpoints + CLI to fetch the binary attachments the
   vault already ingests but never serves back.
2. **Vault export / restore** — full bundle export and restore so users can
   back up or migrate a vault between machines.
3. **Resumable / incremental ingestion** — replace the single-transaction
   ingest loop with batched commits, so a crash at item N preserves items
   1…N-1 instead of rolling back the entire run.

The fourth audit item (secret redaction) is intentionally deferred — the user
plans to explore an OpenAI privacy-focused local model for that work.

---

## Architectural notes for the reviewer

### Why each change lives where it does

- **Media retrieval — `personify/services/media.py` (new)** rather than inline
  in `api.py`: keeps the path-validation logic reusable from CLI and MCP, and
  matches the existing pattern (`services/items.py`, `services/search.py`).

- **Resumability — refactor of `services/ingest.py::ingest_export` only.** The
  schema already has `(source_slug, account_handle, native_id)` and
  `(source_slug, account_handle, content_hash)` UNIQUE constraints, plus an
  `_existing_item` check inside `_persist_item`. Dedup-on-replay therefore
  comes for free; the only structural problem is that the original loop
  wrapped the entire iteration in one transaction, so a crash at item N
  rolled back items 1…N-1. Fix: commit in batches of 100. No schema change.

- **Export / restore — `personify/services/backup.py` (new) + new CLI
  commands.** Uses `pg_dump` / `psql` when available (correct handling of
  sequences, pgvector columns, indexes); falls back to a SQLAlchemy-driven
  per-table JSON dump if the postgres tools aren't on PATH. Restore is
  refused unless the target vault is empty (no DB rows + no `vault_dir`),
  to prevent accidental data loss.

### Things this branch deliberately does NOT do

- No new schema columns. The resumability fix uses what's already there.
- No changes to parsers. They keep their existing contract.
- No changes to the UI — those should land on a separate branch so this PR
  can be reviewed as a backend-only change.
- No secret redaction (deferred per user direction).

### Migration impact

- Zero. No schema changes. Existing vaults, runs, items, and media rows are
  untouched. The new endpoints/commands operate on existing data.

---

## Files changed (running list — fill in as work progresses)

### Item 1: Media retrieval

- `personify/services/media.py` *(new)* — service-layer module that resolves
  an `ItemMedia.path` against the active vault directory, rejects path
  traversal attempts, and returns `(absolute_path, mime, size)` tuples for
  the HTTP and CLI surfaces to consume.
- `personify/api.py` — add `GET /items/{item_id}/media/{media_id}` route
  that streams the file via `FileResponse`. Supports `?download=1` to send
  `Content-Disposition: attachment`.
- `personify/cli.py` — add `vault media <media_id>` command that writes the
  file to stdout or `--out <path>`.
- `tests/test_media_retrieval.py` *(new)* — round-trip + path-traversal
  rejection tests using SQLite + a tmp vault dir.

### Item 4: Resumable ingestion

- `personify/services/ingest.py::ingest_export` — refactor the inner loop
  to commit in batches of `INGEST_BATCH_SIZE = 100`. On exception, the
  `IngestionRun` row's `items_inserted` reflects the count actually
  committed (no longer zeroed out).
- `tests/test_ingest_resumable.py` *(new)* — synthesizes a parser that
  raises mid-iteration; asserts the partial run is recoverable on re-run
  with no double-inserts.

### Item 2: Vault export / restore

- `personify/services/backup.py` *(new)* — `export_vault(out_path)` and
  `restore_vault(bundle_path, into_vault)` service functions. Uses
  `pg_dump` / `psql` when on PATH, else a SQLAlchemy-based per-table JSON
  dump.
- `personify/cli.py` — `vault export <out>` and `vault restore <bundle>
  --into <vault-name>` commands.
- `tests/test_backup_roundtrip.py` *(new)* — round-trip with the JSON
  fallback path on SQLite.

---

## Verification checklist (for Codex)

- [x] **`pytest` (excluding `tests/test_mcp.py`) passes — 90/90.** The 31
      failures in `test_mcp.py` are pre-existing in the local environment
      because the `mcp` package wasn't installed; they reproduce on `main`
      and aren't caused by this branch.
- [x] **New tests added: 14 total**, all passing:
      - `tests/test_media_retrieval.py` — 5 tests (round-trip, relative
        path resolution, traversal rejection, missing-file rejection,
        unknown-id rejection).
      - `tests/test_ingest_resumable.py` — 3 tests (partial-failure
        preserves committed batches, re-run after failure dedups,
        run-row reflects committed count on failure).
      - `tests/test_backup_roundtrip.py` — 6 tests (bundle written with
        manifest, missing-extension normalization, full round-trip
        preserves items + media file, refuses to overwrite populated
        target, refuses unknown bundle format version, refuses tar-slip).
- [x] `personify/api.py` imports cleanly; new `FileResponse` import
      registers a single new route (`GET /items/{id}/media/{media_id}`).
- [x] **No new schema columns** in `personify/models.py`. The
      resumability fix uses the existing `(source, account, native_id)`
      and `(source, account, content_hash)` UNIQUE constraints.
- [x] `init_db()` is unchanged — existing vaults migrate to this branch
      by pulling code only.
- [x] No CLI regression — three new commands (`vault export`,
      `vault restore`, `vault media`) added; existing commands untouched.
- [x] Existing parsers untouched (`personify/parsers/` not in diff).
- [x] All new endpoints / commands / service functions have docstrings
      explaining *why* the code exists, not just what it does.

## Open questions / follow-ups

- **OpenAI privacy model integration** for redaction (item 3) — handed off
  to user.
- **Cross-source identity merging** (audit item 5) — out of scope for this
  branch; pairs naturally with the planned Obsidian visualization layer.
- **Media dedup across exports** (audit item 10) — out of scope; would
  require the same SHA index treatment that `RawExport` already has.
