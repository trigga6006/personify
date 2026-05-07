# Google Takeout Ingestion Plan

This document scopes first-class Google Takeout support for Personify. It is
written as an implementation guide for agents working phase by phase.

## Goal

Support Google Takeout as a first-class source so a user can register a whole
Takeout archive, extracted Takeout folder, or folder of Takeout archive parts
and ingest every Google product Personify understands.

The system must preserve the raw export, ingest supported products, skip
unsupported products safely, and produce clear product-level diagnostics.

## Current State

Personify currently has a narrow `gmail` parser for MBOX files and a generic
`files` parser for text-ish files and PDFs. It does not have a whole-account
Google Takeout parser.

Relevant code:

- Parser registry: `personify/parsers/__init__.py`
- Gmail parser: `personify/parsers/gmail.py`
- Generic files parser: `personify/parsers/files.py`
- Archive helpers: `personify/parsers/_zip.py`
- Registration: `personify/services/register.py`
- Ingest pipeline and source-scoped dedup: `personify/services/ingest.py`
- Raw/staging/normalized vault helpers: `personify/util/vault.py`

Archive extraction already supports `.zip`, `.tar`, `.tgz`, `.tar.gz`, and
single-file `.gz`. What is missing is multi-file batch normalization: a folder
of Takeout parts should become one logical staging root.

## Non-Goals For The First Milestone

- Do not attempt to parse every Google product immediately.
- Do not add a large normalized schema for Google-specific products yet.
- Do not point `ItemMedia.path` at staging files.
- Do not silently deduplicate across existing `gmail`, `files`, or future
  source slugs.
- Do not commit real Takeout fixtures. Use synthetic fixtures only.

## Architectural Decisions

### One Source Slug

Add one source slug:

```text
google_takeout
```

Do not create per-product `Source` rows such as `google_mail` or
`google_drive`. Product identity should live on each item:

- `metadata["product"]`, for example `"Mail"` or `"YouTube and YouTube Music"`
- tag `("product", "<product name>")`

This keeps `RawExport` simple and makes dedup behavior explicit.

### Router Parser And Product Handlers

`GoogleTakeoutParser` should be a container/router parser. It owns:

- input normalization
- Takeout root discovery
- product folder detection
- product report creation
- handler error isolation

Each product handler owns one Google product format.

Suggested layout:

```text
personify/parsers/google_takeout.py
personify/parsers/google/
  __init__.py
  root.py
  report.py
  handlers/
    __init__.py
    base.py
    mail.py
    calendar.py
    contacts.py
    youtube.py
    keep.py
    drive.py
```

Suggested handler interface:

```python
from pathlib import Path
from typing import Iterator, Protocol

from personify.parsers.base import ParsedItem


class GoogleProductHandler(Protocol):
    product: str

    def detect(self, takeout_root: Path) -> bool:
        ...

    def iter_items(self, takeout_root: Path, staging_dir: Path) -> Iterator[ParsedItem]:
        ...
```

Handlers should add `metadata["product"]` and `("product", product)` on every
yielded item. A small helper can enforce this so handlers do not forget.

### Handler Errors Should Not Fail The Whole Run

The current parser contract lets exceptions bubble up, which marks the whole
ingestion run as `error`. The Takeout router should invert that behavior at the
product-handler boundary:

- Catch exceptions raised by one handler.
- Record `handler_error` in the product report.
- Continue with the next product.

Exceptions in archive normalization or root discovery can still fail the whole
run because no meaningful product-level ingest is possible.

### Product Report Storage

Do not overwrite `vault/manifests/export_<id>.json`; that path is already owned
by registration.

Preferred design:

1. Add `metadata_json` to `IngestionRun` and store the product report there.
2. Also write a human-readable mirror:

```text
vault/manifests/takeout_<raw_export_id>_products.json
```

If adding `IngestionRun.metadata_json` is too much for the first pass, start
with the manifest mirror only, but keep the report shape compatible with a
future DB column.

Report statuses should be enumerated:

- `ingested`
- `handler_not_implemented`
- `handler_disabled`
- `no_data`
- `handler_error`

Suggested shape:

```json
{
  "source": "google_takeout",
  "raw_export_id": 123,
  "products_detected": ["Mail", "Calendar", "Google Photos"],
  "products": [
    {
      "product": "Mail",
      "status": "ingested",
      "items_seen": 42,
      "warnings": []
    },
    {
      "product": "Google Photos",
      "status": "handler_not_implemented",
      "items_seen": 0,
      "warnings": []
    }
  ],
  "warnings": []
}
```

### Gmail Reuse

Do not instantiate `GmailParser` from inside the Takeout router. Refactor
`personify/parsers/gmail.py` to expose a pure helper, for example:

```python
def iter_mbox_messages(mbox_path: Path) -> Iterator[ParsedItem]:
    ...
```

Then `GmailParser` and the Takeout Mail handler can both use that helper.

### Dedup Policy

Existing dedup is source-scoped. A previous `gmail` import and a later
`google_takeout` import containing the same Mail data will duplicate emails
because the source slug differs.

First implementation decision:

- Do not silently widen dedup across sources.
- Detect likely overlap where practical and warn in the product report.
- Document this behavior.

Cross-source dedup can be revisited later with explicit UX and tests.

### Permanent Media Storage

Staging is working storage and should not be referenced by durable media rows.
Before Photos or binary-heavy Drive support, add permanent media storage:

```text
vault/media/<source>/<account>/<sha-prefix>__<name>
```

Add helper functions near `personify/util/vault.py` to hash/copy or hardlink
media into this durable location. `ItemMedia.path` should point there, not into
`vault/staging/export_<id>/`.

### Streaming For Huge Product Files

Some Takeout files can be gigabyte-scale, especially Location History or
Timeline exports. Product handlers for large JSON must use streaming parsing,
for example `ijson`, and must not use `json.load()` on unbounded files.

Bake this into handler design early so high-volume products do not need a
painful retrofit later.

## Phase 1: Router Scaffolding

Deliverable: `google_takeout` exists as a source and can inspect a synthetic
Takeout export without product handlers crashing the run.

Tasks:

1. Add `GoogleTakeoutParser` with `SOURCE = "google_takeout"`.
2. Register it in `personify/parsers/__init__.py`.
3. Add Takeout input normalization:
   - single archive
   - extracted folder
   - folder containing multiple Takeout archive parts
4. Add root detection for common shapes:
   - `Takeout/`
   - `Google Takeout/`
   - product folders directly at root
5. Add handler registry and base protocol.
6. Add product report builder with enumerated statuses.
7. Catch handler-level exceptions and continue.
8. Add synthetic fixture builder or hand-built tiny fixtures.

Tests:

- Parser detects a single Takeout archive.
- Parser detects an extracted Takeout folder.
- Parser detects a folder of archive parts.
- Unknown product folders are reported as `handler_not_implemented`.
- A handler exception records `handler_error` and another handler still runs.

## Phase 2: Safe First Product Handlers

Deliverable: a practical MVP that covers common, structured, low-risk products.

Recommended order:

1. Mail
2. Calendar
3. Contacts
4. YouTube activity
5. Keep
6. Drive text/PDF only

### Mail

Input:

- `Mail/*.mbox`

Implementation:

- Refactor Gmail parser to expose `iter_mbox_messages`.
- Mail handler locates MBOX files anywhere under the Mail product folder.
- Add `metadata.product = "Mail"` and product tag.

Kinds:

- `email`

Tests:

- Existing Gmail parser tests still pass.
- Takeout Mail handler parses the same synthetic MBOX.

### Calendar

Input:

- `.ics`

Implementation:

- Parse events into stable `calendar_event` items.
- Use event UID as `native_id` where available.
- Include attendees, organizer, location, start/end, recurrence metadata.

Kinds:

- `calendar_event`

Tests:

- Single event.
- Recurring event metadata preserved.
- Missing UID falls back deterministically.

### Contacts

Input:

- `.vcf`
- CSV if present in Takeout fixture variants

Implementation:

- Parse contacts into one item per contact.
- Use email or vCard UID as `native_id` where available.
- Keep phone numbers, emails, organizations, birthday, URLs in metadata.

Kinds:

- `contact`

Tests:

- vCard with multiple emails/phones.
- CSV contact export.

### YouTube Activity

Input:

- YouTube Takeout CSV/JSON/HTML activity files, depending on observed fixture
  format.

Implementation:

- Start with watch history, search history, playlists, subscriptions, and
  comments where the format is simple.
- Prefer stable URL or upstream ID as `native_id`.
- Avoid parsing uploaded video binaries in this phase.

Kinds:

- `youtube_watch`
- `youtube_search`
- `youtube_comment`
- `youtube_playlist`
- `youtube_subscription`

Tests:

- Watch history row.
- Search history row.
- Playlist/subscription row.

### Keep

Input:

- Keep notes, usually HTML/JSON plus attachments depending on export shape.

Implementation:

- Parse note title/body/list items/labels/timestamps.
- Attachments should be metadata-only until permanent media storage exists.

Kinds:

- `note`

Tests:

- Text note.
- Checklist note.
- Labeled note.

### Drive Text/PDF Only

Input:

- `.txt`, `.md`, `.csv`, `.json`, `.html`, `.pdf`
- Possibly exported Google Docs formats if selected by user

Implementation:

- Limit initial support to text-ish documents and PDFs.
- Skip `.gdoc`, `.gsheet`, `.gslides` pointer files unless they contain useful
  exported metadata.
- Do not store large raw uploads as media until durable media storage exists.

Kinds:

- `document`

Tests:

- Text document.
- PDF document.
- Unsupported binary skipped with a report warning.

## Phase 3: UI And Docs

Deliverable: users and agents can understand what happened after ingest.

Tasks:

1. Add Google Takeout source branding in the frontend.
2. Update Add Export helper text to say accepted inputs:
   - single Takeout archive
   - extracted Takeout folder
   - folder of Takeout archive parts
3. Surface product-level report on export or run detail views.
4. Display skipped products and handler errors clearly.
5. Update docs:
   - `README.md`
   - `docs/AGENT_SETUP_GUIDE.md`
   - `docs/ADDING_A_PARSER.md` or a linked section for router parsers

Support matrix should distinguish:

- Supported
- Partial
- Preserved but skipped
- Not implemented

## Phase 4: Durable Media Foundation

Deliverable: handlers can create durable media references safely.

Tasks:

1. Add `vault/media/` directory to vault layout.
2. Add helper to copy or hardlink media into:

   ```text
   vault/media/<source>/<account>/<sha-prefix>__<name>
   ```

3. Store hash, size, MIME, original relative path, and product metadata.
4. Ensure `ItemMedia.path` never points at staging.
5. Add cleanup/rebuild behavior for `reset_export` if media files are derived
   from one export only.

Tests:

- Media copied to durable storage.
- Media path survives staging cleanup.
- Re-ingest does not duplicate identical media unnecessarily.
- `reset_export` behavior is explicit and tested.

## Phase 5: Heavy Product Handlers

Deliverable: high-volume and binary-heavy products are supported safely.

### Google Photos

Implementation:

- Merge media files with JSON sidecars.
- Create media items with timestamp, album, description, location metadata.
- Store image/video references through durable media storage.
- Do not OCR or embed images initially unless a separate feature enables it.

Risks:

- Huge exports.
- Duplicate sidecars.
- Timestamp inconsistencies.

### Location History / Timeline

Implementation:

- Use streaming JSON parsing.
- Prefer summarized `location_visit` or `activity_segment` items over raw
  coordinate-per-row ingest by default.
- Preserve raw point counts in metadata/report.

Risks:

- `Records.json` can exceed 1 GB.
- Item explosion if raw points become individual items.

### Chrome

Implementation:

- Bookmarks.
- Browser history if present.
- Reading list where available.

Kinds:

- `browser_bookmark`
- `browser_history`

### Fit

Implementation:

- Activity summaries and workouts first.
- Avoid deep health metric modeling until query needs are clearer.

Kinds:

- `fitness_activity`
- `fitness_metric`

### Expanded Drive

Implementation:

- Add `.docx`, `.xlsx`, `.pptx`, HTML export variants, and durable media for
  raw uploads.
- Keep pointer-file behavior explicit.

## Data Model Notes

The current `ParsedItem` model is flexible enough for the MVP:

- `kind`
- `title`
- `body`
- `ts`
- `native_id`
- `metadata`
- `media`
- `tags`

Avoid schema expansion until repeated product-specific query needs justify it.

Possible later additions:

- `IngestionRun.metadata_json`
- `raw_export_parts`
- `parser_warnings`
- product report DB table
- geospatial support for location queries

## Fixture Strategy

Use synthetic fixtures only.

Recommended approach:

- Add a small fixture builder script under `tests/fixtures/google_takeout/`.
- Generate tiny Takeout archives from deterministic sample files.
- Include a product folder that has no handler to test skipped reporting.
- Include a broken handler fixture or monkeypatch test to verify fault
  isolation.

Do not commit real Google Takeout archives, even small ones. They are personal
data and create binary churn.

## Duplicate And Overlap Warnings

Initial behavior should warn, not silently deduplicate across sources.

Examples:

- If `google_takeout` contains Mail and the same account already has `gmail`
  exports, report a likely overlap warning.
- If Drive text files look similar to a previous `files` import, report a
  generic possible overlap warning only if detection is cheap and reliable.

Future cross-source dedup should be designed deliberately with UI support.

## Acceptance Criteria For MVP

The first production-worthy milestone is complete when:

- `vault sources` lists `google_takeout`.
- A single synthetic Takeout archive can be registered and ingested.
- A folder of Takeout archive parts can be registered and ingested.
- Mail, Calendar, Contacts, YouTube activity, Keep, and Drive text/PDF produce
  `ParsedItem`s.
- Unknown products are preserved and reported as skipped.
- One handler failure does not fail the whole run.
- Product reports are visible through a manifest and preferably on the
  ingestion run.
- Existing `gmail` parser behavior remains unchanged.
- Docs explain supported and skipped Google products.

## Suggested Agent Work Sequence

1. Implement Phase 1 scaffolding and tests.
2. Refactor Gmail MBOX helper and implement Mail handler.
3. Add Calendar and Contacts handlers.
4. Add YouTube activity and Keep handlers.
5. Add Drive text/PDF handler.
6. Add UI/docs support matrix.
7. Add durable media foundation.
8. Add Photos, Location History, Chrome, Fit, and expanded Drive.

