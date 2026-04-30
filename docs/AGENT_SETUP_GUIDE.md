# Agent Setup Guide

This guide is for an AI agent or technical helper setting up Personify from a
fresh clone on a user's workstation. It is intentionally operational and
step-by-step. Follow it before asking the user to run manual commands, except
where account login, export confirmation, or OS prompts require the user.

Personify is a local-first personal data vault. It ingests user exports and code
repositories into Postgres 17 + pgvector plus a local filesystem vault, then
exposes CLI, API, and UI workflows.

## Safety Rules

- Treat exports as sensitive personal data.
- Do not upload exports or vault contents to any external service.
- Do not delete source export files, cloned repos, Docker volumes, or vault
  folders unless the user explicitly confirms.
- Raw exports are copied into vault storage. After a successful bulk repo
  registration, the user may delete their temporary intake folder.
- Keep personal data and code corpus data in separate vaults.
- Prefer the UI/API/CLI over direct DB edits.
- If a command fails, inspect the error and fix the setup; do not reset data.

## Intended Vault Layout

There are two default vault profiles:

| Vault | Purpose | Postgres DB | Filesystem |
|-------|---------|-------------|------------|
| `personal` | AI chats, social media, email, files, personal service exports | `personify` | `./vault` |
| `code-corpus` | cloned repos and code datasets | `personify_code_corpus` | `./vaults/code-corpus` |

These are separate databases inside one Docker Postgres container. Docker
Desktop will show one container, usually `personify-db`, but Postgres contains
multiple databases.

## Prerequisites

Check for these before setup:

```powershell
python --version
git --version
docker --version
docker compose version
```

Expected:

- Python 3.11 or newer.
- Git installed and available on PATH.
- Docker Desktop installed, running, and using Linux containers.
- Enough disk space for raw exports, extracted staging data, normalized data,
  embeddings, and Docker's Postgres volume.

Large archives can expand dramatically. For a multi-GB export or many repos,
verify free disk space first.

## Fresh Clone Bootstrap

From the cloned repo root:

```powershell
cd C:\path\to\personify
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

If semantic embeddings are desired, install the optional embedding extra:

```powershell
.\.venv\Scripts\python -m pip install -e ".[embeddings]"
```

The embedding extra may download a local `sentence-transformers` model the first
time embeddings are run.

## Environment File

Create `.env` if it does not exist:

```powershell
Copy-Item .env.example .env
```

Default values are usually fine:

```text
PERSONIFY_DB_URL=postgresql+psycopg://personify:personify@localhost:5544/personify
PERSONIFY_VAULT_DIR=./vault
PERSONIFY_VAULT_NAME=personal
PERSONIFY_VAULTS_DIR=./vaults
PERSONIFY_API_HOST=127.0.0.1
PERSONIFY_API_PORT=8765
PERSONIFY_EMBED_MODEL=sentence-transformers/all-MiniLM-L6-v2
PERSONIFY_EMBED_DIM=384
```

Use named vaults with the CLI global option instead of editing `.env` for normal
work:

```powershell
.\.venv\Scripts\vault --vault code-corpus stats
```

## Start Docker/Postgres

Start Postgres 17 + pgvector:

```powershell
docker compose up -d
```

Verify the container:

```powershell
docker ps --filter "name=personify-db"
docker exec personify-db psql -U personify -d postgres -c "SELECT version();"
docker exec personify-db psql -U personify -d postgres -c "SELECT extname FROM pg_available_extensions WHERE extname = 'vector';"
```

Initialize the personal vault:

```powershell
.\.venv\Scripts\vault init
```

Initialize the code corpus vault:

```powershell
.\.venv\Scripts\vault --vault code-corpus init
```

Confirm both vaults:

```powershell
.\.venv\Scripts\vault info
.\.venv\Scripts\vault --vault code-corpus info
.\.venv\Scripts\vault vaults
```

Confirm both Postgres databases exist:

```powershell
docker exec personify-db psql -U personify -d postgres -c "SELECT datname FROM pg_database WHERE datname LIKE 'personify%' ORDER BY datname;"
```

Expected:

```text
personify
personify_code_corpus
```

## Run Verification

Before ingesting real data:

```powershell
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\vault sources
.\.venv\Scripts\vault stats
.\.venv\Scripts\vault --vault code-corpus stats
```

An empty code-corpus vault should show zero items, zero exports, zero runs, and
a seeded source list.

## Supported Data Sources

Current source slugs:

| Source | Expected input |
|--------|----------------|
| `chatgpt` | OpenAI export archive containing `conversations.json` |
| `claude` | Anthropic export archive containing `conversations.json` |
| `gmail` | MBOX file from Google Takeout |
| `discord` | Discord data package archive |
| `notion` | Notion Markdown/CSV export |
| `github` | Local git repo folder, GitHub archive, or bulk repo intake |
| `files` | Generic text, Markdown, JSON, CSV, PDF folder or archive |

Archives supported by the shared extraction path include `.zip`, `.tar`,
`.tar.gz`, `.tgz`, and plain single-file `.gz`.

## Personal Vault Workflow

Use the `personal` vault for anything tied to the user's personal accounts:
AI chat exports, social media exports, email, notes, documents, Discord, and
similar data.

### Download User Exports

The user usually must log in and request/download exports manually. An agent may
guide them to the right export pages, but should not ask for passwords.

Common export locations:

- OpenAI / ChatGPT: Settings -> Data Controls -> Export data.
- Anthropic / Claude: Account settings -> Privacy or data export.
- Google Takeout: select Gmail or other products, export as MBOX/ZIP.
- Discord: User Settings -> Privacy & Safety -> Request all of my data.
- Notion: Workspace settings -> Export content.

After an export is downloaded, keep it in Downloads or a temporary folder. The
vault will copy it into immutable raw storage.

### Register And Ingest A Personal Export

Examples:

```powershell
.\.venv\Scripts\vault add-export --source chatgpt --path "C:\Users\you\Downloads\chatgpt-export.zip" --account "you@example.com"
.\.venv\Scripts\vault add-export --source claude --path "C:\Users\you\Downloads\claude-export.zip" --account "you@example.com"
.\.venv\Scripts\vault add-export --source gmail --path "C:\Users\you\Downloads\Mail.mbox" --account "you@example.com"
.\.venv\Scripts\vault ingest --all-pending
```

Check results:

```powershell
.\.venv\Scripts\vault stats
.\.venv\Scripts\vault search "something you remember discussing"
```

If a parser is improved and one export needs to be rebuilt:

```powershell
.\.venv\Scripts\vault ingest --export-id 2 --replace
```

## Code-Corpus Workflow

Use `code-corpus` for cloned repositories, public source corpora, and future
model-training data. Do not mix this with personal exports.

### Create A Temporary Repo Intake Folder

The intake folder can live anywhere and can have any name. It is temporary.

Recommended example:

```powershell
mkdir C:\Users\you\Documents\repo-intake
cd C:\Users\you\Documents\repo-intake
```

Clone many repos into that one folder:

```powershell
git clone https://github.com/owner/repo-a.git
git clone https://github.com/owner/repo-b.git
git clone https://github.com/owner/repo-c.git
```

The folder should look like:

```text
repo-intake/
  repo-a/.git/
  repo-b/.git/
  repo-c/.git/
```

### Scan For Duplicates

From the Personify repo:

```powershell
cd C:\path\to\personify
.\.venv\Scripts\vault --vault code-corpus scan-repos --path "C:\Users\you\Documents\repo-intake"
```

The scan returns JSON rows. Each row includes:

- `path`
- `repo.key`, usually `owner/repo`
- `repo.remote_url`
- `repo.head_sha`
- `duplicate`
- `existing_export_id`

Duplicate detection is based on git remote identity, not just folder name. If a
repo is already registered in the active vault, it is flagged before copying.

### Bulk Register And Ingest Repos

Register only new repos and ingest immediately:

```powershell
.\.venv\Scripts\vault --vault code-corpus add-repos --path "C:\Users\you\Documents\repo-intake" --account code-corpus --ingest
```

For nested repo folders, use:

```powershell
.\.venv\Scripts\vault --vault code-corpus add-repos --path "C:\Users\you\Documents\repo-intake" --account code-corpus --recursive --ingest
```

After this succeeds, each new repo has been copied into:

```text
vaults/code-corpus/raw/github/code-corpus/
```

The user may then delete the temporary intake folder:

```powershell
Remove-Item -Recurse -Force "C:\Users\you\Documents\repo-intake"
```

Only run that deletion when the user explicitly confirms.

### Code-Corpus Checks

```powershell
.\.venv\Scripts\vault --vault code-corpus stats
.\.venv\Scripts\vault --vault code-corpus search "repository name or commit message"
```

Note: the current GitHub parser indexes commit history and a tracked file
manifest for real cloned repos. It may not yet be a full source-file content
index for every language. If the user's goal is code search or training data,
the next parser upgrade should ingest source file contents with language,
path, size, license, and dependency metadata.

## Embeddings And Semantic Search

Full-text search works after ingest. pgvector semantic search requires
embeddings.

Install embedding dependencies:

```powershell
.\.venv\Scripts\python -m pip install -e ".[embeddings]"
```

Embed personal vault items:

```powershell
.\.venv\Scripts\vault embed --limit 10000
```

Embed code-corpus vault items:

```powershell
.\.venv\Scripts\vault --vault code-corpus embed --limit 10000
```

Repeat until no more chunks are added, or use the UI embedding dashboard if
available.

Default model:

```text
sentence-transformers/all-MiniLM-L6-v2
```

Default dimension:

```text
384
```

Embeddings are stored in each vault's own Postgres database. Resetting/replacing
an export deletes derived embeddings for that export so they can be rebuilt.

## Start The UI

Run:

```powershell
.\.venv\Scripts\vault serve
```

Open:

```text
http://127.0.0.1:8765/ui
```

Useful endpoints:

```text
http://127.0.0.1:8765/health
http://127.0.0.1:8765/docs
```

The UI can switch vaults through the backend:

```http
GET /api/vaults
POST /api/vaults/{name}/activate
```

After activating a vault, the UI should hard reload or refetch all state because
the server process points at a different database.

Repo intake endpoints for the UI:

```http
POST /api/repos/scan
POST /api/repos/register
```

Scan payload:

```json
{
  "path": "C:\\Users\\you\\Documents\\repo-intake",
  "recursive": false
}
```

Register payload:

```json
{
  "path": "C:\\Users\\you\\Documents\\repo-intake",
  "account": "code-corpus",
  "recursive": false,
  "ingest": true
}
```

## Daily Use

Start:

```powershell
docker compose up -d
.\.venv\Scripts\vault init
.\.venv\Scripts\vault --vault code-corpus init
.\.venv\Scripts\vault serve
```

Stop:

```powershell
docker compose stop
```

Stopping Docker keeps the database volume. It does not delete data.

## Backup Notes

Personify data lives in two places:

- Docker volume for Postgres databases.
- Local vault filesystem folders (`vault/`, `vaults/`).

Back up both. A database backup alone is not enough because raw exports,
staging files, manifests, logs, and normalized snapshots live on disk.

Simple Postgres backup examples:

```powershell
docker exec personify-db pg_dump -U personify -d personify > personify_personal.sql
docker exec personify-db pg_dump -U personify -d personify_code_corpus > personify_code_corpus.sql
```

Also archive/copy:

```text
vault/
vaults/
```

## Safe Cleanup

Safe:

```powershell
docker compose stop
```

Dangerous, deletes Postgres data:

```powershell
docker compose down -v
```

Only run destructive cleanup after an explicit user request and after verifying
backups.

## Troubleshooting

### Docker Is Not Running

Symptoms:

- connection refused
- cannot connect to Postgres
- `vault stats` fails

Fix:

```powershell
docker compose up -d
docker ps --filter "name=personify-db"
```

### The UI Shows One Docker Database

Docker Desktop shows one container. That is expected. Check databases inside
Postgres:

```powershell
docker exec personify-db psql -U personify -d postgres -c "SELECT datname FROM pg_database WHERE datname LIKE 'personify%' ORDER BY datname;"
```

### Code-Corpus Looks Missing

Check:

```powershell
.\.venv\Scripts\vault --vault code-corpus info
.\.venv\Scripts\vault --vault code-corpus stats
```

If needed:

```powershell
.\.venv\Scripts\vault --vault code-corpus init
```

### Duplicate Repo Not Flagged

Duplicate detection uses the git origin remote identity. Inspect the repo:

```powershell
git -C "C:\path\to\repo" remote get-url origin
```

If two clones have different remotes for the same logical repo, normalize the
remote or improve `personify.services.repos.normalize_repo_key`.

### Embeddings Fail

Install embedding dependencies:

```powershell
.\.venv\Scripts\python -m pip install -e ".[embeddings]"
```

Then retry:

```powershell
.\.venv\Scripts\vault embed --limit 500
```

### Need To See Parser Sources

```powershell
.\.venv\Scripts\vault sources
```

## Agent Completion Checklist

Before telling the user setup is done:

- Docker container is running.
- `vault init` succeeds for `personal`.
- `vault --vault code-corpus init` succeeds.
- `vault stats` works.
- `vault --vault code-corpus stats` works.
- `pytest` passes.
- `ruff check .` passes.
- UI starts at `http://127.0.0.1:8765/ui`.
- User knows where to place downloaded exports.
- User knows where to clone code-corpus repos.
- User knows temporary intake folders can be deleted only after successful
  registration/ingest.
