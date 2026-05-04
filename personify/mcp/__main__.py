"""stdio entry point for the MCP server.

Boot order matters:
  1. Configure logging to stderr.
  2. Optionally install a *boot-only* stdout guard that raises on any write —
     catches stray ``print()`` calls in module-level code and import-time
     side effects.
  3. Import the server (and transitively tools, services, the DB layer).
  4. **Restore stdout before** ``mcp.run()`` because FastMCP's stdio transport
     itself writes JSON-RPC framing to stdout. Leaving the guard in place at
     runtime would crash the very first response.

Run with::

    python -m personify.mcp
    PERSONIFY_VAULT_NAME=code-corpus python -m personify.mcp
    PERSONIFY_MCP_STRICT_STDOUT=1 python -m personify.mcp   # CI / tests
"""
from __future__ import annotations

import contextlib
import logging
import os
import sys
from typing import IO, Any, Iterator


def _configure_logging() -> None:
    """Route every log to stderr. Stdout belongs to the JSON-RPC framer."""
    logging.basicConfig(
        level=os.environ.get("PERSONIFY_MCP_LOG_LEVEL", "INFO"),
        stream=sys.stderr,
        format="%(asctime)s mcp %(levelname)s %(name)s %(message)s",
    )


class _StdoutGuard:
    """File-like proxy that raises on any write — used during boot only.

    Catches ``print()`` and Rich-on-stdout side effects from module-level code
    in ``personify.mcp`` and anything it imports. **Must be removed before**
    FastMCP starts the stdio transport (the transport legitimately writes
    JSON-RPC framing to stdout). See :func:`_boot_stdout_guard`.
    """

    def __init__(self, real: IO[Any]) -> None:
        self._real = real

    def write(self, data: object) -> int:
        raise RuntimeError(
            "MCP boot phase wrote to stdout — logs must go to stderr. "
            "Caller should use logging.getLogger(...) and stream=sys.stderr."
        )

    def flush(self) -> None:
        return None


@contextlib.contextmanager
def _boot_stdout_guard() -> Iterator[None]:
    """Install the stdout guard for the duration of the with-block, then restore.

    The guard is opt-in via ``PERSONIFY_MCP_STRICT_STDOUT=1`` so day-to-day
    runs don't pay any cost; CI and the test suite turn it on to surface
    regressions where a service-layer change starts printing.
    """
    if os.environ.get("PERSONIFY_MCP_STRICT_STDOUT") != "1":
        yield
        return
    original = sys.stdout
    sys.stdout = _StdoutGuard(original)  # type: ignore[assignment]
    try:
        yield
    finally:
        sys.stdout = original


def main() -> None:
    _configure_logging()
    # Strict mode wraps imports only — restore real stdout before the SDK
    # starts the stdio transport (it writes JSON-RPC framing to stdout).
    with _boot_stdout_guard():
        from personify.mcp.server import mcp  # noqa: F401 — side-effect import

    # Re-import to grab the module-level binding now that stdout is real.
    from personify.mcp.server import mcp as server

    server.run()  # FastMCP defaults to stdio


if __name__ == "__main__":
    main()
