"""MCP server tests — Phase 1.

Server boot, tool registration, allow-list contract, stdout discipline,
and an end-to-end exercise of the ``search`` tool against a real ingested
vault. Phases 3+ will add tests for the rest of the tools.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlmodel import Session, select


def _init(tmp_path: Path, monkeypatch):
    """Build a fresh sqlite-backed vault for the test."""
    db_path = tmp_path / "personify.db"
    monkeypatch.setenv("PERSONIFY_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("PERSONIFY_VAULT_DIR", str(tmp_path / "vault"))

    import personify.config as config
    import personify.db as db
    import personify.util.vault as vault

    config.settings = config.Settings()
    vault.settings = config.settings
    db.engine = db.create_engine(config.settings.db_url, echo=False, pool_pre_ping=True)
    vault.ensure_vault_layout()
    db.init_db()
    return db


# --- server boot + registration ------------------------------------------

def test_server_boots_with_tools_registered() -> None:
    from personify.mcp.server import mcp, ALLOWED_TOOL_NAMES

    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert names, "no tools registered — import-time decorator side effect missing"
    # Phase 1 only registers `search`. The allow-list will grow per phase but
    # the contract is: every registered tool MUST appear in the allow-list.
    assert names <= ALLOWED_TOOL_NAMES, (
        f"unexpected tool(s) registered: {names - ALLOWED_TOOL_NAMES}. "
        "Update ALLOWED_TOOL_NAMES if intentional; otherwise remove."
    )


def test_no_write_or_mutation_tools_registered() -> None:
    """Hard read-only contract: refuse anything whose name suggests mutation."""
    from personify.mcp.server import mcp

    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    forbidden_substrings = (
        "create",
        "delete",
        "update",
        "ingest",
        "register",
        "reset",
        "embed_",  # embed_pending etc.
        "extract",
        "write",
        "set_",
        "add_",
        "remove",
    )
    offenders = [n for n in names if any(sub in n for sub in forbidden_substrings)]
    assert not offenders, f"write-like tools registered: {offenders}"


def test_every_tool_has_a_description() -> None:
    """LLMs need the docstring; an unlabeled tool is a footgun."""
    from personify.mcp.server import mcp

    tools = asyncio.run(mcp.list_tools())
    for t in tools:
        assert t.description and t.description.strip(), f"tool {t.name!r} has empty description"


# --- search tool exercises a real vault ----------------------------------

def test_search_tool_delegates_to_text_search(tmp_path: Path, monkeypatch) -> None:
    """Tool wraps text_search verbatim — mock the service and assert wiring.

    text_search itself is Postgres-only (uses to_tsvector / plainto_tsquery),
    so testing through SQLite would exercise the wrong code path. The point
    of this test is the MCP-side contract: input parses, args forward, return
    is round-tripped to the client.
    """
    _init(tmp_path, monkeypatch)

    captured: dict[str, object] = {}

    def fake_text_search(query: str, limit: int = 25, source: str | None = None):
        captured["query"] = query
        captured["limit"] = limit
        captured["source"] = source
        return [
            {
                "id": 1,
                "source": "files",
                "account": "test",
                "kind": "doc",
                "title": "todo.txt",
                "ts": None,
                "score": 0.95,
                "snippet": "buy milk",
            }
        ]

    # Patch the symbol the tool module bound at import time.
    import personify.mcp.tools as tools_mod
    monkeypatch.setattr(tools_mod, "_text_search_service", fake_text_search)

    from personify.mcp.server import mcp

    result = asyncio.run(
        mcp.call_tool("search", {"input": {"query": "todo", "limit": 10, "source": "files"}})
    )

    # FastMCP returns (content_list, structured) across recent SDKs; older
    # ones return just the iterable. Normalize to the structured payload.
    if isinstance(result, tuple):
        _, structured = result
    else:
        structured = result
    if isinstance(structured, dict) and "result" in structured:
        structured = structured["result"]

    assert isinstance(structured, list)
    assert structured[0]["title"] == "todo.txt"
    assert captured == {"query": "todo", "limit": 10, "source": "files"}


def test_search_tool_rejects_empty_query(tmp_path: Path, monkeypatch) -> None:
    _init(tmp_path, monkeypatch)
    from mcp.server.fastmcp.exceptions import ToolError
    from personify.mcp.server import mcp

    # Pydantic's min_length=1 rejects "" before the tool body runs; the SDK
    # surfaces that as a ToolError.
    with pytest.raises((ToolError, Exception)):
        asyncio.run(mcp.call_tool("search", {"input": {"query": "", "limit": 10}}))


# --- stdout discipline ---------------------------------------------------

def test_importing_mcp_package_does_not_write_to_stdout(capsys, tmp_path, monkeypatch) -> None:
    """The MCP package and everything it imports must not print() at import.

    A single stray write here would corrupt the JSON-RPC stream on first use.
    """
    _init(tmp_path, monkeypatch)
    capsys.readouterr()  # drain any prior captures

    # Force a fresh import to exercise the side-effects.
    import importlib

    import personify.mcp
    importlib.reload(personify.mcp)
    import personify.mcp.tools
    importlib.reload(personify.mcp.tools)

    captured = capsys.readouterr()
    assert captured.out == "", (
        f"personify.mcp wrote {captured.out!r} to stdout at import time — "
        "would corrupt MCP JSON-RPC. Convert print() / Rich output to stderr "
        "logging."
    )


@pytest.mark.skipif(
    os.environ.get("CI") is None and not sys.platform.startswith("win"),
    reason="subprocess stdio test runs in CI; locally guarded to avoid hanging",
)
def test_stdio_subprocess_emits_only_jsonrpc_framing(tmp_path: Path) -> None:
    """End-to-end stdout-cleanliness check.

    Boot the server in a child process with PERSONIFY_MCP_STRICT_STDOUT=1
    and assert it doesn't immediately blow up. Full JSON-RPC handshake is
    deferred to a later phase; this one catches the obvious "module-level
    print() leaked into the handshake byte stream" regression.
    """
    env = os.environ.copy()
    env["PERSONIFY_MCP_STRICT_STDOUT"] = "1"
    env["PERSONIFY_DB_URL"] = f"sqlite:///{tmp_path / 'p.db'}"
    env["PERSONIFY_VAULT_DIR"] = str(tmp_path / "vault")
    (tmp_path / "vault").mkdir()

    proc = subprocess.Popen(
        [sys.executable, "-m", "personify.mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        # Give the server a moment to crash if it's going to. If it's still
        # alive after 1 s with no stdout junk, the boot path is clean.
        try:
            stdout, _stderr = proc.communicate(timeout=1.0)
        except subprocess.TimeoutExpired:
            proc.terminate()
            stdout, _stderr = proc.communicate(timeout=2.0)
        # Expect either nothing on stdout (server still waiting for input) or
        # only valid JSON-RPC framing (which starts with '{' or 'Content-Length').
        if stdout:
            head = stdout[:200].decode("utf-8", errors="replace").lstrip()
            assert head.startswith(("{", "Content-Length")), (
                f"non-JSONRPC bytes on stdout: {head!r}"
            )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


# --- P2: stdout guard must NOT block FastMCP runtime writes ---------------

def test_stdout_guard_is_boot_only_not_runtime(monkeypatch) -> None:
    """The guard installed under PERSONIFY_MCP_STRICT_STDOUT=1 must not still
    be wrapping sys.stdout when FastMCP's stdio transport begins writing
    JSON-RPC. Codex P2: previous version replaced stdout permanently and
    would crash on the very first response.
    """
    monkeypatch.setenv("PERSONIFY_MCP_STRICT_STDOUT", "1")

    captured_stdout_during_run: dict[str, object] = {}

    # Replace mcp.run with a stub that records what stdout looks like at the
    # moment the SDK would start writing JSON-RPC. If the guard is still
    # installed here, the contract is broken.
    import personify.mcp.server as server_mod

    real_run = server_mod.mcp.run

    def fake_run(*_a, **_kw):
        captured_stdout_during_run["stdout"] = sys.stdout
        captured_stdout_during_run["type"] = type(sys.stdout).__name__

    monkeypatch.setattr(server_mod.mcp, "run", fake_run)
    try:
        from personify.mcp.__main__ import main
        main()
    finally:
        monkeypatch.setattr(server_mod.mcp, "run", real_run)

    # The guard's class is _StdoutGuard. After main() finishes the import
    # phase, stdout must be the original — not _StdoutGuard.
    assert captured_stdout_during_run["type"] != "_StdoutGuard", (
        f"stdout was still {captured_stdout_during_run['type']} when mcp.run() "
        "started — FastMCP's JSON-RPC writes would raise. The guard must "
        "wrap imports only and restore stdout before run()."
    )
    # And a write should now succeed (no RuntimeError).
    assert hasattr(captured_stdout_during_run["stdout"], "write")


def test_boot_stdout_guard_catches_module_print(monkeypatch, capsys) -> None:
    """Sanity: the guard is still wired up to catch a module-level print at
    import time. (The previous test only verified it gets removed; this one
    verifies it still does its job during the import window.)
    """
    monkeypatch.setenv("PERSONIFY_MCP_STRICT_STDOUT", "1")
    from personify.mcp.__main__ import _boot_stdout_guard

    with _boot_stdout_guard():
        # Inside the with-block, stdout should refuse writes.
        with pytest.raises(RuntimeError, match="MCP boot phase wrote to stdout"):
            sys.stdout.write("naughty print()\n")
    # After the with-block, stdout is back to normal.
    sys.stdout.write("")  # must not raise


# --- P3: search log must redact query body --------------------------------

def _call_tool(name: str, args: dict) -> object:
    """Drive an MCP tool the way an agent would; normalize across SDK return shapes."""
    from personify.mcp.server import mcp

    result = asyncio.run(mcp.call_tool(name, args))
    if isinstance(result, tuple):
        _, structured = result
    else:
        structured = result
    if isinstance(structured, dict) and "result" in structured and len(structured) == 1:
        structured = structured["result"]
    return structured


def test_phase3_tools_cover_full_allow_list() -> None:
    """Every name in ALLOWED_TOOL_NAMES must actually be registered."""
    from personify.mcp.server import mcp, ALLOWED_TOOL_NAMES

    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    missing = ALLOWED_TOOL_NAMES - names
    assert not missing, f"allow-list names not actually registered: {missing}"
    extra = names - ALLOWED_TOOL_NAMES
    assert not extra, f"tools registered but not in allow-list: {extra}"


def test_recent_items_tool_delegates_to_service(tmp_path: Path, monkeypatch, fixtures_dir: Path) -> None:
    _init(tmp_path, monkeypatch)
    from personify.services.ingest import ingest_export
    from personify.services.register import register_export

    raw = register_export("files", fixtures_dir / "files", "test")
    ingest_export(raw.id)

    payload = _call_tool("recent_items", {"input": {"limit": 50}})
    assert payload["total"] == 2
    assert {it["source"] for it in payload["items"]} == {"files"}


def test_timeline_tool_filters_by_source(tmp_path: Path, monkeypatch, fixtures_dir: Path) -> None:
    _init(tmp_path, monkeypatch)
    from personify.services.ingest import ingest_export
    from personify.services.register import register_export

    register_export("files", fixtures_dir / "files", "test")
    raw_tw = register_export("twitter", fixtures_dir / "twitter", "personify_user")
    ingest_export(raw_tw.id)

    items = _call_tool("timeline", {"input": {"source": "twitter", "limit": 50}})
    assert isinstance(items, list)
    assert items, "twitter fixture has dated items"
    assert all(i["source"] == "twitter" for i in items)


def test_get_item_truncates_body_by_default(tmp_path: Path, monkeypatch, fixtures_dir: Path) -> None:
    db = _init(tmp_path, monkeypatch)
    from personify.services.ingest import ingest_export
    from personify.services.register import register_export
    from personify.models import Item, ItemText
    from sqlmodel import select as _select

    raw = register_export("files", fixtures_dir / "files", "test")
    ingest_export(raw.id)
    with Session(db.get_engine()) as s:
        item = s.exec(_select(Item)).first()
        text = s.exec(_select(ItemText).where(ItemText.item_id == item.id)).first()
        text.body = "y" * 8000
        s.add(text)
        s.commit()
        item_id = item.id

    truncated = _call_tool("get_item", {"input": {"item_id": item_id}})
    assert truncated["body_truncated"] is True
    assert len(truncated["body"]) == 4096

    full = _call_tool("get_item", {"input": {"item_id": item_id, "include_body": True}})
    assert full["body_truncated"] is False
    assert len(full["body"]) == 8000


def test_get_item_unknown_id_raises(tmp_path: Path, monkeypatch) -> None:
    _init(tmp_path, monkeypatch)
    from mcp.server.fastmcp.exceptions import ToolError

    with pytest.raises((ToolError, Exception)):
        _call_tool("get_item", {"input": {"item_id": 999_999}})


def test_list_sources_and_accounts_and_stats(tmp_path: Path, monkeypatch, fixtures_dir: Path) -> None:
    _init(tmp_path, monkeypatch)
    from personify.services.ingest import ingest_export
    from personify.services.register import register_export

    raw = register_export("files", fixtures_dir / "files", "test")
    ingest_export(raw.id)

    sources = _call_tool("list_sources", {})
    assert any(s["slug"] == "files" for s in sources)

    accounts = _call_tool("list_accounts", {})
    assert any(a["handle"] == "test" for a in accounts)

    payload = _call_tool("stats", {})
    assert payload["items"] >= 1
    assert "files" in payload["items_per_source"]


def test_recent_runs_returns_summaries(tmp_path: Path, monkeypatch, fixtures_dir: Path) -> None:
    _init(tmp_path, monkeypatch)
    from personify.services.ingest import ingest_export
    from personify.services.register import register_export

    raw = register_export("files", fixtures_dir / "files", "test")
    ingest_export(raw.id)
    runs = _call_tool("recent_runs", {"input": {"limit": 5}})
    assert isinstance(runs, list)
    assert runs and runs[0]["raw_export_id"] == raw.id


def test_graph_search_entities_rejects_unknown_type(tmp_path: Path, monkeypatch) -> None:
    """The schema validator pulls from the live ENTITY_TYPES registry — random
    type names must raise rather than reach the DB. Codex review request."""
    _init(tmp_path, monkeypatch)
    from mcp.server.fastmcp.exceptions import ToolError

    with pytest.raises((ToolError, Exception)):
        _call_tool(
            "graph_search_entities",
            {"input": {"query": "anything", "type": "NotAType"}},
        )


def test_graph_search_entities_finds_seeded_entity(tmp_path: Path, monkeypatch) -> None:
    db = _init(tmp_path, monkeypatch)
    from personify.services.graph import create_or_get_entity

    with Session(db.get_engine(), expire_on_commit=False) as s:
        create_or_get_entity(s, type="Project", name="Personify Vault")
        s.commit()

    hits = _call_tool(
        "graph_search_entities",
        {"input": {"query": "personify", "type": "Project"}},
    )
    assert any(h["name"] == "Personify Vault" for h in hits)


def test_get_entity_and_neighborhood_and_context(tmp_path: Path, monkeypatch) -> None:
    db = _init(tmp_path, monkeypatch)
    from personify.services.graph import create_or_get_entity, create_or_get_relationship

    with Session(db.get_engine(), expire_on_commit=False) as s:
        a = create_or_get_entity(s, type="Project", name="A")
        b = create_or_get_entity(s, type="Topic", name="B")
        create_or_get_relationship(
            s, source_entity_id=a.id, target_entity_id=b.id, relationship_type="USES"
        )
        s.commit()
        aid, bid = a.id, b.id

    full = _call_tool("get_entity", {"input": {"entity_id": aid}})
    assert full["entity"]["id"] == aid

    nbh = _call_tool("entity_neighborhood", {"input": {"entity_id": aid, "depth": 1}})
    assert nbh["center"]["id"] == aid
    assert any(n["id"] == bid for n in nbh["nodes"])

    ctx = _call_tool("entity_context", {"input": {"entity_id": aid}})
    assert ctx["entity"]["id"] == aid
    assert len(ctx["suggested_queries"]) <= 2


def test_get_entity_unknown_id_raises(tmp_path: Path, monkeypatch) -> None:
    _init(tmp_path, monkeypatch)
    from mcp.server.fastmcp.exceptions import ToolError

    with pytest.raises((ToolError, Exception)):
        _call_tool("get_entity", {"input": {"entity_id": 999_999}})


# --- Phase 5: resources + templates --------------------------------------

def _read_resource(uri: str) -> object:
    """Drive read_resource the way an MCP client would.

    FastMCP returns an iterable of ``ReadResourceContents`` whose ``content``
    is the JSON-encoded payload (or raw bytes/text). Decode the first
    element and json-parse so tests can assert on shape directly.
    """
    import json as _json
    from personify.mcp.server import mcp

    contents = list(asyncio.run(mcp.read_resource(uri)))
    assert contents, f"read_resource({uri!r}) returned no content"
    body = contents[0].content
    if isinstance(body, (bytes, bytearray)):
        body = bytes(body).decode("utf-8")
    return _json.loads(body)


def test_concrete_resources_are_listed() -> None:
    from personify.mcp.server import mcp

    listed = asyncio.run(mcp.list_resources())
    uris = {str(r.uri) for r in listed}
    expected = {"vault://stats", "vault://recent", "vault://sources"}
    assert expected <= uris, f"missing concrete resources: {expected - uris}"


def test_resource_templates_are_listed() -> None:
    from personify.mcp.server import mcp

    templates = asyncio.run(mcp.list_resource_templates())
    patterns = {t.uriTemplate for t in templates}
    expected = {
        "vault://item/{item_id}",
        "vault://entity/{entity_id}",
        "vault://export/{export_id}",
    }
    assert expected <= patterns, f"missing templates: {expected - patterns}"


def test_stats_resource_returns_collected_stats(
    tmp_path: Path, monkeypatch, fixtures_dir: Path
) -> None:
    _init(tmp_path, monkeypatch)
    from personify.services.ingest import ingest_export
    from personify.services.register import register_export

    raw = register_export("files", fixtures_dir / "files", "test")
    ingest_export(raw.id)

    payload = _read_resource("vault://stats")
    assert isinstance(payload, dict)
    assert payload["items"] >= 1
    assert "files" in payload["items_per_source"]


def test_recent_resource_returns_paginated_items(
    tmp_path: Path, monkeypatch, fixtures_dir: Path
) -> None:
    _init(tmp_path, monkeypatch)
    from personify.services.ingest import ingest_export
    from personify.services.register import register_export

    raw = register_export("files", fixtures_dir / "files", "test")
    ingest_export(raw.id)

    payload = _read_resource("vault://recent")
    assert isinstance(payload, dict)
    assert payload["limit"] == 25
    assert payload["offset"] == 0
    assert payload["total"] >= 1


def test_sources_resource_lists_active_parsers(
    tmp_path: Path, monkeypatch, fixtures_dir: Path
) -> None:
    _init(tmp_path, monkeypatch)
    from personify.services.ingest import ingest_export
    from personify.services.register import register_export

    raw = register_export("files", fixtures_dir / "files", "test")
    ingest_export(raw.id)

    payload = _read_resource("vault://sources")
    assert isinstance(payload, list)
    slugs = {s["slug"] for s in payload}
    assert "files" in slugs


def test_sources_resource_does_not_cross_assign_accounts(
    tmp_path: Path, monkeypatch, fixtures_dir: Path
) -> None:
    """Codex review: a vault with `files/test` and `twitter/me` must NOT
    return every account on every source. Each source row's `accounts` list
    must contain only handles that actually contributed items to that source.
    """
    _init(tmp_path, monkeypatch)
    from personify.services.ingest import ingest_export
    from personify.services.register import register_export

    files_raw = register_export("files", fixtures_dir / "files", "files-only-account")
    ingest_export(files_raw.id)
    twitter_raw = register_export("twitter", fixtures_dir / "twitter", "twitter-only-account")
    ingest_export(twitter_raw.id)

    payload = _read_resource("vault://sources")
    by_slug = {s["slug"]: s for s in payload}

    files_accounts = by_slug["files"]["accounts"]
    twitter_accounts = by_slug["twitter"]["accounts"]

    assert "files-only-account" in files_accounts
    assert "twitter-only-account" not in files_accounts, (
        "twitter account leaked into the files source row — accounts must "
        "be per-source, not a global list"
    )

    assert "twitter-only-account" in twitter_accounts
    assert "files-only-account" not in twitter_accounts, (
        "files account leaked into the twitter source row"
    )

    # Sources without ingested data should report an empty account list,
    # not the union of all known accounts.
    for src in payload:
        if src["slug"] not in {"files", "twitter"}:
            assert src["accounts"] == [], (
                f"unused source {src['slug']!r} carries accounts {src['accounts']!r}; "
                "expected an empty list"
            )


def test_item_template_truncates_body_by_default(
    tmp_path: Path, monkeypatch, fixtures_dir: Path
) -> None:
    """Resource template path must use the default 4096-char body cap. The
    full body is opt-in via the get_item tool with include_body=True (Codex
    P2 review)."""
    db = _init(tmp_path, monkeypatch)
    from personify.services.ingest import ingest_export
    from personify.services.register import register_export
    from personify.models import Item, ItemText

    raw = register_export("files", fixtures_dir / "files", "test")
    ingest_export(raw.id)
    with Session(db.get_engine()) as s:
        item = s.exec(select(Item)).first()
        text = s.exec(select(ItemText).where(ItemText.item_id == item.id)).first()
        text.body = "z" * 9000
        s.add(text)
        s.commit()
        item_id = item.id

    payload = _read_resource(f"vault://item/{item_id}")
    assert payload["body_truncated"] is True
    assert len(payload["body"]) == 4096
    assert payload["body_full_chars"] == 9000


def test_item_template_unknown_id_raises(tmp_path: Path, monkeypatch) -> None:
    _init(tmp_path, monkeypatch)
    with pytest.raises(Exception):
        _read_resource("vault://item/999999")


def test_entity_template_returns_full_entity(tmp_path: Path, monkeypatch) -> None:
    db = _init(tmp_path, monkeypatch)
    from personify.services.graph import (
        add_entity_alias,
        create_or_get_entity,
    )

    with Session(db.get_engine(), expire_on_commit=False) as s:
        e = create_or_get_entity(s, type="Project", name="Personify")
        add_entity_alias(s, e.id, "PV")
        s.commit()
        eid = e.id

    payload = _read_resource(f"vault://entity/{eid}")
    assert payload["entity"]["id"] == eid
    assert any(a["alias"] == "PV" for a in payload["aliases"])


def test_entity_template_unknown_id_raises(tmp_path: Path, monkeypatch) -> None:
    _init(tmp_path, monkeypatch)
    with pytest.raises(Exception):
        _read_resource("vault://entity/999999")


def test_export_template_returns_summary_with_runs(
    tmp_path: Path, monkeypatch, fixtures_dir: Path
) -> None:
    _init(tmp_path, monkeypatch)
    from personify.services.ingest import ingest_export
    from personify.services.register import register_export

    raw = register_export("files", fixtures_dir / "files", "test")
    ingest_export(raw.id)

    payload = _read_resource(f"vault://export/{raw.id}")
    assert payload["id"] == raw.id
    assert payload["source"] == "files"
    assert payload["items"] == 2
    assert payload["runs_count"] == 1
    assert payload["runs"] and payload["runs"][0]["status"] == "ok"


def test_export_template_unknown_id_raises(tmp_path: Path, monkeypatch) -> None:
    _init(tmp_path, monkeypatch)
    with pytest.raises(Exception):
        _read_resource("vault://export/999999")


# --- Phase 6: vault mcp CLI command ---------------------------------------

def test_vault_mcp_command_is_registered_and_silent_at_help(monkeypatch) -> None:
    """`vault mcp` must exist as a Typer command. Calling --help must not
    crash and (importantly for stdio transport) the entrypoint must be a
    callable that does not write to stdout itself; the actual run happens
    inside personify.mcp.__main__.main()."""
    from typer.testing import CliRunner

    from personify.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["mcp", "--help"])
    assert result.exit_code == 0, result.output
    assert "MCP" in result.output or "mcp" in result.output


def test_vault_mcp_command_delegates_to_mcp_main(monkeypatch) -> None:
    """Running `vault mcp` should call personify.mcp.__main__.main and nothing
    else from the CLI's own stdout-writing code paths."""
    called = {"count": 0}

    def fake_main() -> None:
        called["count"] += 1

    # The CLI imports lazily inside the command — patch at the import site.
    import personify.mcp.__main__ as mcp_main_mod
    monkeypatch.setattr(mcp_main_mod, "main", fake_main)

    from typer.testing import CliRunner
    from personify.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["mcp"])
    assert result.exit_code == 0, result.output
    assert called["count"] == 1


def test_recent_items_log_does_not_leak_account_handle(
    tmp_path: Path, monkeypatch, caplog, fixtures_dir: Path
) -> None:
    """Codex review: account is usually an email or handle (PII). Tool must
    fingerprint it in logs — same contract as query fingerprints in search.
    """
    _init(tmp_path, monkeypatch)
    from personify.services.ingest import ingest_export
    from personify.services.register import register_export

    raw = register_export("files", fixtures_dir / "files", "fowlerben71@gmail.com")
    ingest_export(raw.id)

    sensitive_account = "fowlerben71@gmail.com"

    with caplog.at_level("INFO", logger="personify.mcp.tools"):
        _call_tool(
            "recent_items",
            {"input": {"account": sensitive_account, "limit": 10}},
        )

    full_log = "\n".join(r.getMessage() for r in caplog.records)
    assert sensitive_account not in full_log, (
        f"recent_items leaked account handle into logs.\nLog: {full_log!r}"
    )
    # The fingerprint we DO want to see, so we can correlate.
    assert "account=fp:" in full_log


def test_search_log_does_not_leak_query_text(tmp_path: Path, monkeypatch, caplog) -> None:
    """Codex P3: the plan promises to redact query bodies from logs. Verify
    the search tool only logs metadata (length, fingerprint, limit, source)
    — never the query text itself. Personal-vault PII boundary."""
    _init(tmp_path, monkeypatch)

    def fake_text_search(query, limit=25, source=None):
        return []

    import personify.mcp.tools as tools_mod
    monkeypatch.setattr(tools_mod, "_text_search_service", fake_text_search)

    sensitive = "alice@example.com confidential password leak xyz123"

    from personify.mcp.server import mcp

    with caplog.at_level("INFO", logger="personify.mcp.tools"):
        asyncio.run(
            mcp.call_tool(
                "search",
                {"input": {"query": sensitive, "limit": 5}},
            )
        )

    full_log = "\n".join(r.getMessage() for r in caplog.records)
    # Specific fragments that would be a leak.
    for forbidden in ("alice@example.com", "confidential", "password", "xyz123"):
        assert forbidden not in full_log, (
            f"query content {forbidden!r} leaked into logs. Tool must log "
            "only length/fingerprint/limit/source."
        )
    # And the metadata we *do* want is present.
    assert f"q.len={len(sensitive)}" in full_log
    assert "q.fp=" in full_log
