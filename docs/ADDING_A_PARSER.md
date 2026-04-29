# Adding a new parser

Each adapter parses one source format and yields `ParsedItem` instances. The
ingestion pipeline owns hashing, dedup, persistence, normalized JSON output,
and embeddings — adapters only need to read raw bytes and produce records.

## 1. Create the module

`personify/parsers/<source>.py`:

```python
from pathlib import Path
from typing import Iterator

from personify.parsers.base import ParsedItem, ParserBase


class MyServiceParser(ParserBase):
    SOURCE = "myservice"          # CLI/API slug — keep it short, lowercase, no spaces
    PARSER_VERSION = "0.1.0"      # bump on any output-shape change

    @classmethod
    def detect(cls, path: Path) -> bool:
        # Cheap check that returns True if `path` looks like this format.
        return path.is_file() and path.suffix.lower() == ".myservice"

    def iter_items(self, raw_path: Path, staging_dir: Path) -> Iterator[ParsedItem]:
        # MUST NOT mutate raw_path. Use staging_dir for any extraction or
        # working files. Yield one ParsedItem per logical record.
        ...
```

### `ParsedItem` fields

| Field        | Notes                                                                    |
|--------------|--------------------------------------------------------------------------|
| `kind`       | Required. e.g. `message`, `email`, `doc`, `commit`, `page`, `db_row`.    |
| `title`      | Short label. Optional but encouraged.                                    |
| `body`       | Plain-text body — what FTS and embeddings will index.                    |
| `ts`         | `datetime` (UTC preferred). Drives the timeline and time filters.        |
| `native_id`  | Stable upstream ID if available. Enables idempotent re-ingestion.        |
| `metadata`   | Free-form JSON. Don't put bulk text here — that goes in `body`.          |
| `media`      | List of `{media_type, mime, path, size_bytes, sha256, metadata}` dicts.  |
| `tags`       | List of `(key, value)` tuples. Stored in the `tags` table.               |

### Invariants the pipeline enforces

- The raw export is hashed once on registration; the file in `vault/raw/`
  is never modified. Don't open it in write mode.
- Dedup key is `(source, native_id)` if `native_id` is set, else
  `(source, sha256(kind + title + body))`. If you can't supply a
  `native_id`, the body must be deterministic (don't include "now()" etc.).
- The pipeline writes a normalized JSON snapshot for every inserted item to
  `vault/normalized/<bucket>/item_<id>.json`. You don't write it.

## 2. Register it

In `personify/parsers/__init__.py`:

```python
from personify.parsers.myservice import MyServiceParser

PARSERS = {
    ...,
    MyServiceParser.SOURCE: MyServiceParser,
}
```

That's all the wiring — the CLI (`vault add-export --source myservice ...`)
and the API (`/sources`) pick it up automatically.

## 3. Add a fixture and test

- Drop a tiny fake export under `tests/fixtures/<source>/`.
- Create `tests/test_parser_<source>.py`:

```python
def test_myservice_parses(fixtures_dir, staging):
    raw = fixtures_dir / "myservice" / "sample.myservice"
    items = list(MyServiceParser().iter_items(raw, staging))
    assert len(items) == 3
    assert items[0].kind == "thing"
```

Keep fixtures small and committed — they exercise `detect()` and
`iter_items()` without needing a database.

## 4. Bump `PARSER_VERSION` when output changes

Every `IngestionRun` records `parser_name` + `parser_version`. If you change
how `body`, `metadata`, or `native_id` are produced, bump the version so old
runs are distinguishable from new ones.

## Tips

- Use generators, not lists — exports can be large.
- Decode bytes with `errors="replace"` so a single bad byte doesn't kill a run.
- For ZIP-shaped exports, use `personify.parsers._zip.extract_zip(...)` and
  extract into `staging_dir` (the pipeline allocates a per-export folder).
- If the source has multiple "kinds" (e.g. messages + manifests), use
  distinct `kind` values so consumers can filter.
- Don't embed in the parser. Embeddings are computed by
  `personify.services.embed.embed_pending()` after ingestion.
