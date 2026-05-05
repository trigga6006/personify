"""HTTP MCP runner — gating wrapper + status state.

The vault exposes its read-only MCP tools over two transports:

* **stdio** — what Claude Desktop spawns on demand via
  ``python -m personify.mcp``. Out-of-process, lifecycle controlled by the
  client. Not visible to this app.
* **streamable HTTP** — mounted in-process at ``/mcp`` so the FastAPI
  app can offer a "start MCP" button, an uptime read-out, and a request
  counter to the UI. Same FastMCP instance, same tool surface; only the
  transport differs.

This module owns the toggle + counters for the HTTP transport. The
session manager itself is started once in :func:`personify.api._lifespan`
and runs for the life of the process — flipping ``enabled`` on/off is
just whether requests are allowed through, not whether the server is
"up". That separation keeps start/stop cheap and avoids the cold-start
latency we'd pay if the session manager were torn down each time.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable


@dataclass
class _MCPState:
    enabled: bool = False
    started_at: datetime | None = None
    request_count: int = 0
    last_request_at: datetime | None = None
    last_error: str | None = None
    #: Counts the unique client sessions seen since this enable cycle began,
    #: derived from the ``Mcp-Session-Id`` request header. A session value of
    #: ``None`` (initial connection before the server assigns an id) is
    #: tracked once per enable cycle so the count reflects new clients
    #: rather than "every request from one client".
    seen_sessions: set[str | None] = field(default_factory=set)


_state = _MCPState()
_lock = threading.Lock()


def status() -> dict[str, Any]:
    """Read-only snapshot of the gate state, safe to return from an API.

    ``uptime_seconds`` is computed at read time so the UI can poll without
    needing its own clock skew compensation.
    """
    with _lock:
        now = datetime.now(timezone.utc)
        uptime = (
            (now - _state.started_at).total_seconds()
            if _state.enabled and _state.started_at
            else None
        )
        return {
            "enabled": _state.enabled,
            "started_at": _state.started_at.isoformat() if _state.started_at else None,
            "uptime_seconds": uptime,
            "request_count": _state.request_count,
            "last_request_at": (
                _state.last_request_at.isoformat() if _state.last_request_at else None
            ),
            "session_count": len(_state.seen_sessions),
            "last_error": _state.last_error,
            "endpoint": "/mcp",
        }


def start() -> dict[str, Any]:
    """Open the gate. Resets counters because the user expects "uptime"
    to mean since-this-start, not lifetime-of-process."""
    with _lock:
        _state.enabled = True
        _state.started_at = datetime.now(timezone.utc)
        _state.request_count = 0
        _state.last_request_at = None
        _state.last_error = None
        _state.seen_sessions.clear()
    return status()


def stop() -> dict[str, Any]:
    """Close the gate. State is preserved so the UI can still report the
    final request count immediately after stop."""
    with _lock:
        _state.enabled = False
    return status()


def _record_error(detail: str) -> None:
    with _lock:
        _state.last_error = detail


# --------------------------------------------------------------------------- #
# ASGI gating wrapper                                                         #
# --------------------------------------------------------------------------- #

ASGIScope = dict[str, Any]
ASGIReceive = Callable[[], Awaitable[dict[str, Any]]]
ASGISend = Callable[[dict[str, Any]], Awaitable[None]]


def _session_id_from_headers(scope: ASGIScope) -> str | None:
    """Extract ``Mcp-Session-Id`` from raw ASGI headers (lowercase per
    ASGI spec), or ``None`` if absent."""
    for name, value in scope.get("headers") or []:
        if name == b"mcp-session-id":
            try:
                return value.decode("latin-1")
            except Exception:  # noqa: BLE001
                return None
    return None


async def _send_503(send: ASGISend) -> None:
    """Reject a request when the gate is closed. JSON body so the UI
    or an MCP client gets a structured reason rather than a blank 503."""
    body = json.dumps(
        {"detail": "MCP HTTP server is stopped. Start it from the UI or via /api/mcp/start."}
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 503,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class GatedMCPApp:
    """ASGI wrapper around FastMCP's ``streamable_http_app``.

    Two responsibilities:

    1. **Gate** — when ``_state.enabled`` is False, every HTTP request is
       short-circuited with a 503 carrying a human-readable reason. The
       inner MCP app never sees the request, so the session manager
       can keep running cheaply between toggles.
    2. **Count** — every passed-through request bumps ``request_count``
       and updates ``last_request_at``. Distinct client sessions are
       tracked via the ``Mcp-Session-Id`` header so the UI can
       distinguish "10 calls from one client" from "10 separate clients".

    Non-HTTP scopes (lifespan, websocket) pass through unconditionally
    so the parent FastAPI app can still drive the inner Starlette
    lifespan if it ever needs to.
    """

    def __init__(self, inner: Callable[[ASGIScope, ASGIReceive, ASGISend], Awaitable[None]]) -> None:
        self._inner = inner

    async def __call__(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        if scope.get("type") != "http":
            await self._inner(scope, receive, send)
            return

        with _lock:
            if not _state.enabled:
                gated = True
            else:
                gated = False
                _state.request_count += 1
                _state.last_request_at = datetime.now(timezone.utc)
                _state.seen_sessions.add(_session_id_from_headers(scope))

        if gated:
            await _send_503(send)
            return

        try:
            await self._inner(scope, receive, send)
        except Exception as e:  # noqa: BLE001 — record but re-raise
            _record_error(repr(e))
            raise
