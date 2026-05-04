# Home Workstation Setup

This repo is configured for a local-first vault on Windows with Docker Desktop
running Postgres 17 + pgvector.

## Daily Start

```powershell
docker compose up -d
.\.venv\Scripts\vault init
```

`vault init` is safe to rerun. It creates the vault folders, enables pgvector,
creates missing tables, and seeds parser sources.

## Daily Stop

```powershell
docker compose stop
```

This stops the database container but keeps the `personify_personify_pg` Docker
volume and the local `vault/` files.

## Reset Only While Empty

The command below deletes the Postgres Docker volume. Use it only before real
exports are loaded, or after making an intentional backup.

```powershell
docker compose down -v
docker compose up -d
.\.venv\Scripts\vault init
```

## First Ingest Loop

```powershell
.\.venv\Scripts\vault sources
.\.venv\Scripts\vault add-export --source files --path .\path\to\folder --account personal
.\.venv\Scripts\vault ingest --all-pending
.\.venv\Scripts\vault search "what I want to find"
.\.venv\Scripts\vault stats
```

Raw exports are copied into `vault/raw/`; normalized item snapshots are written
to `vault/normalized/`; ingest logs land in `vault/logs/`.

## API Smoke Check

```powershell
.\.venv\Scripts\vault dev
```

Then open:

- `http://localhost:18766`
- `http://localhost:18765/health`
- `http://localhost:18765/docs`

## Embeddings

Text search works with the base install. Semantic search requires the optional
embedding dependencies:

```powershell
.\.venv\Scripts\python -m pip install -e ".[embeddings]"
.\.venv\Scripts\vault embed --limit 500
```

The default model is `sentence-transformers/all-MiniLM-L6-v2`, producing
384-dimensional vectors for pgvector.
