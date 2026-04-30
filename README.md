# Personify - Personal Data Vault

Local-first system for ingesting exports from major services into a unified,
queryable schema.

## Architecture

- **Backend**: Python + FastAPI
- **CLI**: Typer (`vault ...`)
- **Database**: Postgres 17 + pgvector (via Docker Compose)
- **ORM**: SQLModel
- **Vault**: local filesystem (`raw/`, `staging/`, `normalized/`, `manifests/`, `logs/`)
- **Parsers**: adapter pattern, one per source

## Filesystem Layout

```text
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
. .venv/Scripts/activate
pip install -e .

# 3. init vault + db
vault init

# 4. list parser sources
vault sources

# 5. add an export
vault add-export --source chatgpt --path ./downloads/chatgpt.zip --account me@example.com

# 6. ingest
vault ingest --source chatgpt
vault ingest --all-pending

# 7. search
vault search "that conversation about postgres"
vault stats
```

## Multiple Vaults

Named vaults use separate Postgres databases and separate filesystem roots.
The default `personal` vault keeps the original database and `./vault` path:

| Vault | Database | Filesystem |
|-------|----------|------------|
| personal | `personify` | `./vault` |
| code-corpus | `personify_code_corpus` | `./vaults/code-corpus` |

Use the global `--vault` option before the command name:

```bash
vault --vault code-corpus init
vault --vault code-corpus add-export --source github --path ./repos/example --account code-corpus
vault --vault code-corpus ingest --all-pending
vault --vault code-corpus search "vector database"
vault --vault personal stats
```

`vault init` will create the target Postgres database when it does not exist,
then create the schema and vault folders for that profile.

For code-corpus intake, clone many repositories into one local folder, scan for
duplicates, bulk-register only new repos, then ingest:

```bash
vault --vault code-corpus scan-repos --path ./repo-intake
vault --vault code-corpus add-repos --path ./repo-intake --account code-corpus --ingest
```

After registration, each new repo has been copied into the vault's immutable
`raw/` storage, so the temporary intake folder can be deleted.

On this workstation, Docker Compose runs Postgres 17 + pgvector in the
`personify-db` container. Docker Desktop must be running while you query,
ingest, or serve the API. Stop the database without deleting data with:

```bash
docker compose stop
```

See [docs/WORKSTATION_SETUP.md](docs/WORKSTATION_SETUP.md) for the longer local
setup and recovery notes.

## Supported Sources

| Source  | Format                     |
|---------|----------------------------|
| chatgpt | OpenAI export ZIP          |
| claude  | Anthropic export ZIP       |
| gmail   | MBOX (Google Takeout)      |
| discord | Discord data package ZIP   |
| notion  | Notion Markdown/CSV export |
| github  | Local repo folder or GitHub `.tar.gz`/`.tgz` archive |
| files   | Generic md/txt/pdf/json/csv folder or archive |

## CLI

| Command                                            | Purpose                       |
|----------------------------------------------------|-------------------------------|
| `vault init`                                       | create vault dirs + db schema |
| `vault sources`                                    | list parser sources           |
| `vault add-export --source S --path P --account A` | register a raw export         |
| `vault ingest --export-id ID`                      | parse one export              |
| `vault ingest --export-id ID --replace`            | rebuild one export            |
| `vault ingest --source S`                          | parse all exports for a source |
| `vault ingest --all-pending`                       | parse exports without ok runs |
| `vault embed --limit N`                            | compute missing embeddings    |
| `vault search "query"`                             | full-text search              |
| `vault stats`                                      | counts per source / account   |

## API

| Method | Path               | Purpose                  |
|--------|--------------------|--------------------------|
| GET    | `/health`          | liveness                 |
| GET    | `/sources`         | list registered sources  |
| GET    | `/stats`           | counts                   |
| POST   | `/search`          | full-text search         |
| POST   | `/semantic-search` | pgvector cosine search   |
| GET    | `/items/{id}`      | full item                |
| GET    | `/timeline`        | items by time window     |

## Adding A New Parser

See [docs/ADDING_A_PARSER.md](docs/ADDING_A_PARSER.md). Short version:

1. Create `personify/parsers/<source>.py` exposing a class that subclasses
   `personify.parsers.base.BaseParser`.
2. Implement `detect(path)`, `iter_items(raw_export, staging_dir)`, and set
   `SOURCE` and `PARSER_VERSION` class vars.
3. Register it in `personify/parsers/__init__.py` (`PARSERS` dict).
4. Add a fixture under `tests/fixtures/<source>/` and a test in
   `tests/test_parser_<source>.py`.

## Invariants

- Raw exports are never mutated. Every byte is hashed (SHA256) on register.
- Every item carries: `source`, `account`, `raw_export_id`, `native_id` (when
  available), `ts`, `content_hash`, `metadata` (JSONB).
- Dedup key: `(source, account, native_id)` when present, else
  `(source, account, content_hash)`.
- Every ingestion run records the parser version.
