from __future__ import annotations

from fastapi.testclient import TestClient


def test_ui_redirects_to_svelte_app_when_built() -> None:
    from personify.api import app
    from personify.web.routes import STATIC_DIR

    assert (STATIC_DIR / "app" / "index.html").exists()

    client = TestClient(app, follow_redirects=False)
    resp = client.get("/ui")

    assert resp.status_code == 307
    assert resp.headers["location"] == "/app/"


def test_vault_info_redacts_db_password() -> None:
    """The /api/vaults payload (and the helper that powers it) must not
    leak the DB password — the value lands in browser devtools and any
    future MCP transcript that snapshots a response."""
    from personify.services import vaults as vaults_mod

    info = vaults_mod._vault_info("personal")
    assert "personify:personify@" not in info["db_url"], info["db_url"]
    # Show that the credential field was actually scrubbed, not just absent
    # (e.g. for a SQLite URL the password segment doesn't exist at all).
    raw_url = vaults_mod._redact_db_url(
        "postgresql+psycopg://personify:secret@localhost:5544/personify"
    )
    assert "secret" not in raw_url
    assert "***" in raw_url


def test_health_reports_degraded_on_init_failure(monkeypatch) -> None:
    """If init_db raised at startup, /health must report status=degraded
    with the captured error rather than silently claiming ok."""
    import personify.api as api_mod

    # Simulate the startup-failed state without re-running the lifespan.
    monkeypatch.setattr(api_mod, "_init_error", "RuntimeError('boom')")
    client = TestClient(api_mod.app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert "boom" in body["error"]


def test_mcp_runner_start_stop_status_cycle() -> None:
    """The /api/mcp/{status,start,stop} routes must round-trip cleanly:
    starts in stopped state, start flips enabled+sets started_at, stop
    flips back. Counter behavior is verified separately on the gated
    ASGI wrapper."""
    from personify.api import app
    from personify.services import mcp_runner

    # Reset module state so we don't depend on test ordering.
    mcp_runner.stop()

    client = TestClient(app)

    initial = client.get("/api/mcp/status").json()
    assert initial["enabled"] is False
    assert initial["request_count"] == 0
    assert initial["endpoint"] == "/mcp"

    started = client.post("/api/mcp/start").json()
    assert started["enabled"] is True
    assert started["started_at"] is not None
    assert started["uptime_seconds"] is not None

    stopped = client.post("/api/mcp/stop").json()
    assert stopped["enabled"] is False
    # After stop, started_at is preserved but uptime_seconds returns null
    # (it's only computed while enabled).
    assert stopped["uptime_seconds"] is None


def test_mcp_endpoint_is_503_when_stopped_and_counted_when_started() -> None:
    """The /mcp mount must be gated. When stopped, every HTTP request
    short-circuits with 503 and a JSON detail. When started, requests
    pass to the inner FastMCP app and increment request_count."""
    from personify.api import app
    from personify.services import mcp_runner

    mcp_runner.stop()
    client = TestClient(app)

    gated = client.get("/mcp/")
    assert gated.status_code == 503
    assert "stopped" in gated.json()["detail"].lower()

    mcp_runner.start()
    # FastMCP returns 404/406 for a plain GET (it expects a proper
    # MCP handshake), but the gate has already counted the request.
    client.get("/mcp/")
    status = client.get("/api/mcp/status").json()
    assert status["enabled"] is True
    assert status["request_count"] >= 1
    assert status["last_request_at"] is not None

    mcp_runner.stop()


def test_mcp_start_resets_counters() -> None:
    """Each start fresh-zeros the request counter so 'uptime' lines up
    with 'requests during this run' — that's what the UI's stats panel
    promises."""
    from personify.api import app
    from personify.services import mcp_runner

    mcp_runner.start()
    client = TestClient(app)
    client.get("/mcp/")
    client.get("/mcp/")
    assert client.get("/api/mcp/status").json()["request_count"] >= 2

    # Re-start: counters zero out.
    fresh = client.post("/api/mcp/start").json()
    assert fresh["enabled"] is True
    assert fresh["request_count"] == 0
    assert fresh["session_count"] == 0
    mcp_runner.stop()

