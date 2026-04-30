from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from personify.config import configure_vault, db_url_for_vault, settings, vault_dir_for_name
from personify.db import init_db, reset_engine
from personify.parsers import PARSERS
from personify.services.embed import embed_pending
from personify.services.ingest import ingest_all_pending, ingest_export, ingest_source, reset_export
from personify.services.register import register_export
from personify.services.repos import (
    register_repo_intake,
    register_result_payload,
    scan_repo_intake,
    scan_row_payload,
)
from personify.services.search import text_search
from personify.services.stats import collect_stats
from personify.util.vault import ensure_vault_layout

app = typer.Typer(help="Personify — local-first personal data vault.", no_args_is_help=True)
console = Console()


@app.callback()
def main(
    vault: Optional[str] = typer.Option(
        None,
        "--vault",
        "-v",
        help="Named vault profile, e.g. personal or code-corpus.",
    ),
) -> None:
    """Run commands against a named, physically separate vault."""
    if vault:
        configure_vault(vault)
        reset_engine()


@app.command("vaults")
def vaults() -> None:
    """Show common vault profiles and their resolved DB/filesystem targets."""
    rows = [
        ("personal", db_url_for_vault("personal"), vault_dir_for_name("personal")),
        ("code-corpus", db_url_for_vault("code-corpus"), vault_dir_for_name("code-corpus")),
    ]
    table = Table(show_lines=False)
    table.add_column("vault")
    table.add_column("db")
    table.add_column("dir")
    for name, db_url, vault_dir in rows:
        marker = " *" if name == settings.vault_name else ""
        table.add_row(f"{name}{marker}", db_url, str(vault_dir))
    console.print(table)


@app.command("info")
def info() -> None:
    """Show the active vault profile."""
    console.print_json(
        data={
            "vault": settings.vault_name,
            "db_url": settings.db_url,
            "vault_dir": str(settings.vault_dir),
        }
    )


@app.command()
def init() -> None:
    """Create the vault directory layout and database schema."""
    ensure_vault_layout()
    init_db()
    console.print(f"[green]vault initialized at[/green] {settings.vault_dir.resolve()}")
    console.print(f"[green]db initialized at[/green] {settings.db_url}")


@app.command("sources")
def sources() -> None:
    """List parser sources available to add-export/ingest."""
    table = Table(show_lines=False)
    table.add_column("slug")
    table.add_column("parser")
    table.add_column("version")
    for slug, cls in sorted(PARSERS.items()):
        table.add_row(slug, f"{cls.__module__}.{cls.__name__}", cls.PARSER_VERSION)
    console.print(table)


@app.command("add-export")
def add_export(
    source: str = typer.Option(..., "--source", "-s", help=f"One of: {sorted(PARSERS)}"),
    path: Path = typer.Option(..., "--path", "-p", exists=True, readable=True),
    account: str = typer.Option(..., "--account", "-a"),
    notes: Optional[str] = typer.Option(None, "--notes"),
) -> None:
    """Register a raw export. The file is copied (never moved) into vault/raw."""
    if source not in PARSERS:
        raise typer.BadParameter(f"Unknown source. Pick one of {sorted(PARSERS)}")
    raw = register_export(source, path, account, notes=notes)
    console.print(
        f"[green]registered[/green] export id=[bold]{raw.id}[/bold] "
        f"source={raw.source_slug} sha256={raw.sha256[:12]}…"
    )


@app.command("scan-repos")
def scan_repos(
    path: Path = typer.Option(..., "--path", "-p", exists=True, file_okay=False, readable=True),
    recursive: bool = typer.Option(False, "--recursive", "-r"),
) -> None:
    """Scan an intake folder for git repos and flag duplicates."""
    rows = scan_repo_intake(path, recursive=recursive)
    console.print_json(json.dumps([scan_row_payload(r) for r in rows], default=str))


@app.command("add-repos")
def add_repos(
    path: Path = typer.Option(..., "--path", "-p", exists=True, file_okay=False, readable=True),
    account: str = typer.Option("code-corpus", "--account", "-a"),
    recursive: bool = typer.Option(False, "--recursive", "-r"),
    ingest_now: bool = typer.Option(False, "--ingest", help="Ingest each newly registered repo."),
    notes: Optional[str] = typer.Option(None, "--notes"),
) -> None:
    """Bulk-register every new git repo in an intake folder."""
    results = register_repo_intake(
        path,
        account_handle=account,
        recursive=recursive,
        ingest=ingest_now,
        notes=notes,
    )
    console.print_json(json.dumps([register_result_payload(r) for r in results], default=str))


@app.command()
def ingest(
    export_id: Optional[int] = typer.Option(None, "--export-id", "-e"),
    source: Optional[str] = typer.Option(None, "--source", "-s"),
    all_pending: bool = typer.Option(False, "--all-pending", help="Ingest exports with no ok run"),
    replace: bool = typer.Option(
        False,
        "--replace",
        help="With --export-id, delete derived rows for that export before ingesting",
    ),
) -> None:
    """Ingest one export by id, or all exports for a source."""
    if replace and export_id is None:
        raise typer.BadParameter("--replace requires --export-id.")
    if all_pending:
        if replace:
            raise typer.BadParameter("--replace cannot be used with --all-pending.")
        runs = ingest_all_pending()
        if not runs:
            console.print("[yellow]no pending exports[/yellow]")
            return
        for run in runs:
            console.print(
                f"  run id={run.id} export={run.raw_export_id} status={run.status} "
                f"seen={run.items_seen} inserted={run.items_inserted} skipped={run.items_skipped}"
            )
        return
    if export_id is None and source is None:
        raise typer.BadParameter("Pass --export-id, --source, or --all-pending.")
    if export_id is not None:
        if replace:
            deleted = reset_export(export_id)
            console.print(
                f"[yellow]reset[/yellow] export={export_id} "
                f"items={deleted['items']} runs={deleted['runs']}"
            )
        run = ingest_export(export_id)
        console.print(
            f"[green]run[/green] id={run.id} status={run.status} "
            f"seen={run.items_seen} inserted={run.items_inserted} skipped={run.items_skipped}"
        )
        return
    runs = ingest_source(source)  # type: ignore[arg-type]
    if not runs:
        console.print(f"[yellow]no exports for source[/yellow] {source}")
        return
    for run in runs:
        console.print(
            f"  run id={run.id} export={run.raw_export_id} status={run.status} "
            f"seen={run.items_seen} inserted={run.items_inserted}"
        )


@app.command("embed")
def embed(limit: int = typer.Option(500, "--limit", "-n")) -> None:
    """Compute embeddings for ingested text items without embeddings."""
    count = embed_pending(limit=limit)
    console.print(f"[green]embedded[/green] chunks={count}")


@app.command()
def search(
    query: str = typer.Argument(..., help="Free-text query"),
    source: Optional[str] = typer.Option(None, "--source", "-s"),
    limit: int = typer.Option(20, "--limit", "-n"),
) -> None:
    """Full-text search across all ingested items."""
    rows = text_search(query, limit=limit, source=source)
    if not rows:
        console.print("[yellow]no results[/yellow]")
        return
    table = Table(show_lines=False)
    table.add_column("id", justify="right")
    table.add_column("source")
    table.add_column("kind")
    table.add_column("ts")
    table.add_column("title")
    table.add_column("snippet", overflow="fold")
    for r in rows:
        table.add_row(
            str(r["id"]),
            r["source"],
            r["kind"],
            r["ts"] or "",
            (r["title"] or "")[:60],
            (r["snippet"] or "")[:120],
        )
    console.print(table)


@app.command()
def stats() -> None:
    """Show vault counts by source/account."""
    data = collect_stats()
    console.print_json(json.dumps(data, default=str))


@app.command()
def serve(
    host: Optional[str] = typer.Option(None, "--host"),
    port: Optional[int] = typer.Option(None, "--port"),
) -> None:
    """Run the FastAPI app via uvicorn (UI at /ui, API docs at /docs)."""
    import uvicorn

    h = host or settings.api_host
    p = port or settings.api_port
    base = f"http://{h}:{p}"
    console.print(f"[bold]VaultUI[/bold]      {base}/ui")
    console.print(f"[dim]API docs[/dim]    {base}/docs")
    console.print(f"[dim]Health[/dim]      {base}/health")
    console.print()
    uvicorn.run(
        "personify.api:app",
        host=h,
        port=p,
        reload=False,
    )


if __name__ == "__main__":
    app()
