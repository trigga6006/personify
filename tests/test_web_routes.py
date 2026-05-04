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

