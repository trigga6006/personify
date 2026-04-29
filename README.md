# Personify — Personal Data Vault

Local-first system for ingesting exports from major services into a unified, queryable schema.

## Architecture

- **Backend**: Python + FastAPI
- **CLI**: Typer (`vault ...`)
- **Database**: Postgres + pgvector (via Docker Compose)
- **ORM**: SQLModel
- **Vault**: local filesystem (`raw/`, `staging/`, `normalized/`, `manifests/`, `logs/`)
- **Parsers**: adapter pattern, one per source

## Filesystem layout

```
vault/
  raw/         # immutable original exports (SHA256 verified)
  staging/     # extracted/working copies
  normalized/  # canonical JSON per item
  manifests/   # per-export manifests
  logs/        # ingestion logs
```

## Quickstart

```bash
# 1. start postgres + pgvector
docker compose up -d

# 2. create venv + install
python -m venv .venv
. .venv/Scripts/activate   # Windows bash
pip install -e .

# 3. init vault + db
vault init

# 4. add an export
vault add-export --source chatgpt --path ./downloads/chatgpt.zip --account me@example.com

# 5. ingest
vault ingest --source chatgpt

# 6. search
vault search "that conversation about postgres"
vault stats
```

## Supported sources

| Source     | Format                      |
|------------|-----------------------------|
| chatgpt    | OpenAI export ZIP           |
| claude     | Anthropic export ZIP        |
| gmail      | MBOX (Google Takeout)       |
| discord    | Discord data package ZIP    |
| notion     | Notion Markdown/CSV export  |
| github     | Local repo folder           |
| files      | Generic md/txt/pdf folder   |

## CLI

| Command                                                 | Purpose                          |
|---------------------------------------------------------|----------------------------------|
| `vault init`                                            | create vault dirs + db schema    |
| `vault add-export --source S --path P --account A`      | register a raw export            |
| `vault ingest --export-id ID`                           | parse one export                 |
| `vault ingest --source S`                               | parse all exports for a source   |
| `vault search "query"`                                  | full-text search                 |
| `vault stats`                                           | counts per source / account      |

## API

| Method | Path                | Purpose                       |
|--------|---------------------|-------------------------------|
| GET    | `/health`           | liveness                      |
| GET    | `/sources`          | list registered sources       |
| GET    | `/stats`            | counts                        |
| POST   | `/search`           | full-text search              |
| POST   | `/semantic-search`  | pgvector cosine search        |
| GET    | `/items/{id}`       | full item                     |
| GET    | `/timeline`         | items by time window          |

## Adding a new parser

See [docs/ADDING_A_PARSER.md](docs/ADDING_A_PARSER.md). Short version:

1. Create `personify/parsers/<source>.py` exposing a class that subclasses
   `personify.parsers.base.BaseParser`.
2. Implement `detect(path)`, `iter_items(raw_export, staging_dir)`, and
   set `SOURCE` and `PARSER_VERSION` class vars.
3. Register it in `personify/parsers/__init__.py` (`PARSERS` dict).
4. Add a fixture under `tests/fixtures/<source>/` and a test in
   `tests/test_parser_<source>.py`.

## Invariants

- Raw exports are never mutated. Every byte is hashed (SHA256) on register.
- Every item carries: `source`, `account`, `raw_export_id`, `native_id`
  (when available), `ts`, `content_hash`, `metadata` (JSONB).
- Dedup key: `(source, native_id)` when present, else `content_hash`.
- Every ingestion run records the parser version.
