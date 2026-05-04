from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from typing import Optional
from urllib.error import URLError
from urllib.request import urlopen

import typer
from rich.console import Console
from rich.table import Table

from personify.config import configure_vault, db_url_for_vault, settings, vault_dir_for_name
from personify.db import init_db, reset_engine
from personify.parsers import PARSERS
from personify.services.embed import embed_pending
from personify.services.ingest import ingest_all_pending, ingest_export, ingest_source, reset_export
from personify.services.pipeline import latest_pipeline_summary, run_pipeline
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


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _stop_processes(procs: list[tuple[str, subprocess.Popen]]) -> None:
    for _name, proc in procs:
        if proc.poll() is None:
            proc.terminate()
    for name, proc in procs:
        if proc.poll() is not None:
            continue
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            console.print(f"[yellow]forcing {name} to stop[/yellow]")
            proc.kill()
            proc.wait(timeout=5)


def _is_port_open(host: str, port: int) -> bool:
    """Return true if a local TCP port is reachable on IPv4 or IPv6 localhost."""
    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1", "::"}:
        candidates = ("127.0.0.1", "::1", "localhost")
    else:
        candidates = (host,)

    for candidate in candidates:
        try:
            addresses = socket.getaddrinfo(candidate, port, type=socket.SOCK_STREAM)
        except socket.gaierror:
            continue
        for family, socktype, proto, _canonname, sockaddr in addresses:
            with socket.socket(family, socktype, proto) as sock:
                sock.settimeout(0.2)
                if sock.connect_ex(sockaddr) == 0:
                    return True
    return False


def _display_host(host: str) -> str:
    return "localhost" if host in {"127.0.0.1", "0.0.0.0"} else host


def _health_url(host: str, port: int) -> str:
    return f"http://{_display_host(host)}:{port}/health"


def _backend_health_ok(host: str, port: int) -> bool:
    try:
        with urlopen(_health_url(host, port), timeout=1) as res:
            return 200 <= res.status < 300
    except (OSError, URLError):
        return False


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
    with_embeddings: bool = typer.Option(
        False,
        "--with-embeddings",
        help="After ingest, embed any text items belonging to this export.",
    ),
    with_graph: bool = typer.Option(
        False,
        "--with-graph",
        help="After ingest, run heuristic graph extraction over this export's items.",
    ),
) -> None:
    """Ingest one export by id, or all exports for a source.

    Embeddings and graph extraction are optional follow-on stages and only
    apply when a single --export-id is given.
    """
    if replace and export_id is None:
        raise typer.BadParameter("--replace requires --export-id.")
    if (with_embeddings or with_graph) and export_id is None:
        raise typer.BadParameter(
            "--with-embeddings/--with-graph require --export-id."
        )
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
        if with_embeddings or with_graph:
            result = run_pipeline(
                export_id,
                with_embeddings=with_embeddings,
                with_graph=with_graph,
            )
            for stage in result.stages:
                console.print(
                    f"  stage=[bold]{stage.stage}[/bold] status={stage.status} "
                    f"items_processed={stage.items_processed}"
                    + (f" error={stage.error}" if stage.error else "")
                )
            return
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


@app.command("pipeline-status")
def pipeline_status(
    export_id: int = typer.Option(..., "--export-id", "-e"),
) -> None:
    """Show the latest pipeline stage statuses for one export."""
    console.print_json(json.dumps(latest_pipeline_summary(export_id), default=str))


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


@app.command("dev")
def dev(
    host: Optional[str] = typer.Option(None, "--host", help="FastAPI host."),
    port: int = typer.Option(18765, "--port", help="FastAPI port."),
    frontend_port: int = typer.Option(18766, "--frontend-port", help="Vite dev server port."),
) -> None:
    """Start Docker/Postgres, FastAPI, and the Vite frontend for local development.

    MCP stays separate: use `vault mcp` as the second command/Claude Desktop
    entrypoint so stdio remains reserved for JSON-RPC framing.
    """
    root = _repo_root()
    frontend_dir = root / "frontend"
    if not frontend_dir.is_dir():
        raise typer.BadParameter(f"frontend directory not found: {frontend_dir}")

    h = host or settings.api_host
    p = port
    display_host = _display_host(h)

    console.print("[bold]Docker[/bold]      docker compose up -d")
    subprocess.run(["docker", "compose", "up", "-d"], cwd=root, check=True)

    pnpm = shutil.which("pnpm")
    if pnpm is None:
        raise typer.BadParameter("pnpm not found on PATH; install pnpm or run Vite manually.")

    backend_running = _is_port_open(h, p) and _backend_health_ok(h, p)
    frontend_running = _is_port_open("127.0.0.1", frontend_port) or _is_port_open(
        "localhost", frontend_port
    )

    backend_cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "personify.api:app",
        "--host",
        h,
        "--port",
        str(p),
        "--reload",
    ]
    frontend_cmd = [pnpm, "exec", "vite", "--host", "localhost", "--port", str(frontend_port)]
    frontend_env = os.environ.copy()
    frontend_env["PERSONIFY_DEV_API_ORIGIN"] = f"http://{display_host}:{p}"
    frontend_env["PERSONIFY_DEV_FRONTEND_PORT"] = str(frontend_port)

    console.print(
        f"[bold]FastAPI[/bold]     http://{display_host}:{p}"
        + (" [dim](already running)[/dim]" if backend_running else "")
    )
    console.print(
        f"[bold]Vite[/bold]        http://localhost:{frontend_port}"
        + (" [dim](already running)[/dim]" if frontend_running else "")
    )
    console.print("[dim]MCP stays separate: run `vault mcp` or let Claude Desktop launch it.[/dim]")
    console.print()

    procs: list[tuple[str, subprocess.Popen]] = []
    try:
        if not backend_running:
            procs.append(("fastapi", subprocess.Popen(backend_cmd, cwd=root)))
        if not frontend_running:
            procs.append(("vite", subprocess.Popen(frontend_cmd, cwd=frontend_dir, env=frontend_env)))
        last_backend_check = 0.0
        while True:
            for name, proc in procs:
                code = proc.poll()
                if code is not None:
                    console.print(f"[yellow]{name} exited with code {code}[/yellow]")
                    raise typer.Exit(code)
            now = time.monotonic()
            if now - last_backend_check > 2:
                last_backend_check = now
                if not _backend_health_ok(h, p):
                    if _is_port_open(h, p):
                        console.print(
                            f"[red]FastAPI health check failed at {_health_url(h, p)}[/red]"
                        )
                        raise typer.Exit(1)
                    console.print("[yellow]FastAPI stopped; restarting it[/yellow]")
                    proc = subprocess.Popen(backend_cmd, cwd=root)
                    procs.append(("fastapi", proc))
            time.sleep(0.5)
    except KeyboardInterrupt:
        console.print("\n[yellow]stopping dev servers[/yellow]")
    finally:
        _stop_processes(procs)


@app.command("export")
def export(
    out: Path = typer.Option(..., "--out", "-o", help="Output path (.tar.gz)"),
) -> None:
    """Write a portable backup of the active vault to a single tarball.

    The bundle contains the database (one JSON file per table) plus the
    raw, staging, normalized, and manifest directories. Embeddings are
    intentionally excluded — re-run ``vault embed`` after restoring.
    """
    from personify.services.backup import export_vault, BackupError

    try:
        result = export_vault(out)
    except BackupError as e:
        console.print(f"[red]export failed:[/red] {e}")
        raise typer.Exit(1)

    console.print(
        f"[green]wrote[/green] {result.bundle_path}\n"
        f"  tables={len(result.tables_written)} rows={result.rows_written} "
        f"files={result.files_written} bytes={result.bytes_written}"
    )


@app.command("restore")
def restore(
    bundle: Path = typer.Argument(..., exists=True, readable=True, help="Bundle path"),
    into: str = typer.Option(
        ...,
        "--into",
        "-i",
        help="Target vault name. Must be unused (refuses to overwrite).",
    ),
) -> None:
    """Restore a bundle into a NEW vault profile.

    Refuses if the target vault directory already exists or its database
    already has items, so a typo can never overwrite the active vault.
    """
    from personify.services.backup import restore_vault, BackupError

    try:
        result = restore_vault(bundle, into)
    except BackupError as e:
        console.print(f"[red]restore failed:[/red] {e}")
        raise typer.Exit(1)

    console.print(
        f"[green]restored[/green] vault={result.vault_name}\n"
        f"  tables={len(result.tables_restored)} rows={result.rows_restored} "
        f"files={result.files_restored}"
    )
    console.print(
        "[dim]Tip: re-run `vault --vault "
        f"{result.vault_name} embed` if you had embeddings.[/dim]"
    )


@app.command("media")
def media(
    media_id: int = typer.Argument(..., help="ItemMedia row id"),
    item_id: int = typer.Option(..., "--item-id", "-i", help="Owning item id"),
    out: Optional[Path] = typer.Option(
        None,
        "--out",
        "-o",
        help="Output path. Default: ./<suggested_filename> in cwd.",
    ),
) -> None:
    """Dump one ingested attachment to disk.

    Mirrors the ``GET /items/{id}/media/{mid}`` HTTP route — same
    path-traversal guard via ``services.media.resolve_media``.
    """
    from personify.services.media import (
        MediaNotFound,
        MediaUnavailable,
        resolve_media,
    )

    try:
        resolved = resolve_media(item_id, media_id)
    except MediaNotFound as e:
        console.print(f"[red]not found:[/red] {e}")
        raise typer.Exit(1)
    except MediaUnavailable as e:
        console.print(f"[red]unavailable:[/red] {e}")
        raise typer.Exit(1)

    dest = out if out is not None else Path.cwd() / resolved.suggested_filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(resolved.absolute_path, dest)
    console.print(
        f"[green]wrote[/green] {dest} "
        f"({resolved.size_bytes or 'unknown'} bytes, {resolved.mime or 'unknown mime'})"
    )


@app.command("mcp")
def mcp_serve() -> None:
    """Run the MCP server over stdio (for Claude Desktop / agents).

    The vault is selected via the global ``--vault`` flag (e.g.
    ``vault --vault code-corpus mcp``) or the ``PERSONIFY_VAULT_NAME`` env var.

    CRITICAL: this command MUST NOT print anything to stdout — the MCP stdio
    transport uses stdout for JSON-RPC framing. The entrypoint configures
    logging to stderr before any service module loads. Don't add `console.print`
    or any other stdout writer here.
    """
    from personify.mcp.__main__ import main as mcp_main

    mcp_main()


if __name__ == "__main__":
    app()
