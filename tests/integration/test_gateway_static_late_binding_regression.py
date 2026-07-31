"""Integration tests: static file route late-binding regression guard.

This is a tripwire test ensuring that the v2.1.1 fix for the closure
late-binding bug in static file routes remains working. Each file in
the frontend directory should be served by its own dedicated route that
returns that specific file's content (not the last file in the iteration).

The bug was: in a loop registering static file routes, a closure captured
the loop variable by reference (late binding), causing ALL routes to serve
the LAST file's content. The fix uses a factory function (_make_static_handler)
to capture the path by value.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from xbot.interfaces.gateway.app import create_app
from xbot.interfaces.gateway.services import ServiceContainer
from xbot.platform.bus.queue import MessageBus
from xbot.platform.config.schema import Config

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Static file definitions used across the parametrized test
# ---------------------------------------------------------------------------

STATIC_FILES: dict[str, bytes] = {
    "app.js": b"JS_CONTENT_APP",
    "style.css": b"CSS_CONTENT_STYLE",
    "icon.svg": b"SVG_ICON_CONTENT",
    "manifest.json": b'{"name":"MANIFEST_JSON_CONTENT"}',
    "robots.txt": b"ROBOTS_TXT_CONTENT",
}


@pytest.fixture()
def frontend_dist(tmp_path: Path) -> Path:
    """Create a temporary frontend/dist directory with distinct static files."""
    dist_dir = tmp_path / "frontend" / "dist"
    dist_dir.mkdir(parents=True)

    for filename, content in STATIC_FILES.items():
        (dist_dir / filename).write_bytes(content)

    # index.html is excluded from static route registration
    (dist_dir / "index.html").write_text(
        "<html><body>INDEX</body></html>", encoding="utf-8"
    )
    return dist_dir


@pytest.fixture()
def gateway_client(tmp_path: Path, frontend_dist: Path) -> TestClient:
    """Build a TestClient for the gateway app configured with the temp frontend dir."""
    config = Config()
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    config.agents.defaults.workspace = str(workspace_dir)

    container = ServiceContainer(
        config=config,
        bus=MessageBus(),
        agent=None,
        conversation_store=None,
        cron=None,
        heartbeat=None,
    )

    app = create_app(
        container,
        data_dir=tmp_path / "data",
        frontend_dir=frontend_dist,
        skip_lifecycle=True,
    )
    return TestClient(app)


# ---------------------------------------------------------------------------
# Regression test: each static file route serves its own content
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename",
    list(STATIC_FILES.keys()),
    ids=list(STATIC_FILES.keys()),
)
def test_all_static_files_serve_own_content(
    gateway_client: TestClient,
    filename: str,
) -> None:
    """Regression guard for v2.1.1 late-binding bug fix.

    If the closure late-binding bug returns, ALL routes would serve the
    content of the last file registered in the iteration loop. This test
    ensures each route correctly serves its own file's content.
    """
    expected_content = STATIC_FILES[filename]
    response = gateway_client.get(f"/{filename}")

    assert response.status_code == 200, (
        f"GET /{filename} returned HTTP {response.status_code}, expected 200"
    )
    assert response.content == expected_content, (
        f"GET /{filename} served wrong content — late-binding bug likely regressed. "
        f"Expected {expected_content!r}, got {response.content!r}"
    )
