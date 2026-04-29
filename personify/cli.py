from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from personify.config import settings
from personify.db import init_db
from personify.parsers import PARSERS
from personify.services.ingest import ingest_export, ingest_source
from personify.services.register import register_export
from personify.services.search import text_search
from personify.services.stats import collect_stats
from personify.util.vault import ensure_vault_layout

app = typer.Typer(help="Personify — local-first personal data vault.", no_args_is_help=True)
console = Console()


@app.command()
def init() -> None:
    """Create the vault directory layout and database schema."""
    ensure_vault_layout()
    init_db()
    console.print(f"[green]vault initialized at[/green] {settings.vault_dir.resolve()}")
    console.print(f"[green]db initialized at[/green] {settings.db_url}")


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


@app.command()
def ingest(
    export_id: Optional[int] = typer.Option(None, "--export-id", "-e"),
    source: Optional[str] = typer.Option(None, "--source", "-s"),
) -> None:
    """Ingest one export by id, or all exports for a source."""
    if export_id is None and source is None:
        raise typer.BadParameter("Pass --export-id or --source.")
    if export_id is not None:
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
    """Run the FastAPI app via uvicorn."""
    import uvicorn

    uvicorn.run(
        "personify.api:app",
        host=host or settings.api_host,
        port=port or settings.api_port,
        reload=False,
    )


if __name__ == "__main__":
    app()
