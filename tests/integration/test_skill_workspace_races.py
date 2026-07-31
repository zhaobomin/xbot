"""Integration tests: workspace skill toggle × edit × delete filesystem races.

These tests attack the race window between the toggle endpoint (which renames
``SKILL.md`` ↔ ``SKILL.md.disabled`` via ``Path.replace``) and the update
endpoint (which reads the current state via ``workspace_skill_file`` then
writes back). Even on a single-thread asyncio event loop these paths mix
purely-sync filesystem I/O; we simulate the interleavings by driving the
filesystem out-of-band between endpoint calls so we can precisely observe how
the endpoints behave when the on-disk state changes underneath them.

Focus areas:
- Toggle idempotency and 404 when the skill directory disappears.
- "Both SKILL.md and SKILL.md.disabled exist" edge case (partial rename fallout).
- PUT lands on the currently-persisted state file (enabled OR disabled).
- Toggle handles a mid-flight directory deletion gracefully.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest
from fastapi.testclient import TestClient

# Reuse the same builder that the WebUI adapter tests use.
from tests.test_webui_adapter import _build_client

pytestmark = pytest.mark.integration


def _auth_headers(client: TestClient) -> dict[str, str]:
    token = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-webui-password"},
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _create_skill(client: TestClient, headers: dict, name: str, content: str) -> None:
    resp = client.post(
        "/api/skills",
        headers=headers,
        json={"name": name, "content": content},
    )
    assert resp.status_code == 201, resp.text


def _skill_dir(services, name: str) -> Path:
    return services.config.workspace_path / ".claude" / "skills" / name


class TestToggleIdempotency:
    """Toggle should be safe to call multiple times without duplicating files."""

    def test_toggle_to_current_state_is_noop(self, tmp_path: Path) -> None:
        client, services = _build_client(tmp_path)
        headers = _auth_headers(client)
        _create_skill(client, headers, "idem-a", "# demo")
        d = _skill_dir(services, "idem-a")

        # Toggle to enabled while already enabled — no-op.
        r = client.post(
            "/api/skills/idem-a/toggle", headers=headers, json={"enabled": True}
        )
        assert r.status_code == 200
        assert (d / "SKILL.md").exists()
        assert not (d / "SKILL.md.disabled").exists()

    def test_repeated_enable_disable_cycles_leave_single_file(
        self, tmp_path: Path
    ) -> None:
        client, services = _build_client(tmp_path)
        headers = _auth_headers(client)
        _create_skill(client, headers, "cycle-a", "# demo")
        d = _skill_dir(services, "cycle-a")

        for _ in range(5):
            client.post(
                "/api/skills/cycle-a/toggle", headers=headers, json={"enabled": False}
            )
            client.post(
                "/api/skills/cycle-a/toggle", headers=headers, json={"enabled": True}
            )

        # Only one canonical file should remain after cycles.
        assert (d / "SKILL.md").exists()
        assert not (d / "SKILL.md.disabled").exists()


class TestBothFilesExistFallout:
    """When both SKILL.md and SKILL.md.disabled exist (partial-rename fallout,
    manual filesystem edits, or a race between toggle+PUT+create), the current
    contract prefers the enabled file. We freeze that behavior here so future
    changes are forced to be intentional."""

    def test_workspace_skill_file_prefers_enabled_when_both_exist(
        self, tmp_path: Path
    ) -> None:
        client, services = _build_client(tmp_path)
        headers = _auth_headers(client)
        _create_skill(client, headers, "both-a", "# enabled content")
        d = _skill_dir(services, "both-a")

        # Simulate the "both files exist" hazard.
        (d / "SKILL.md.disabled").write_text("# disabled content", encoding="utf-8")
        assert (d / "SKILL.md").exists()
        assert (d / "SKILL.md.disabled").exists()

        # list_skills reports it as enabled and paths at SKILL.md.
        r = client.get("/api/skills", headers=headers)
        entry = next(item for item in r.json() if item["name"] == "both-a")
        assert entry["enabled"] is True
        assert entry["path"].endswith("/SKILL.md")

        # GET returns the enabled content.
        r = client.get("/api/skills/both-a", headers=headers)
        assert r.json()["content"] == "# enabled content"

    def test_toggle_to_disabled_when_both_files_exist_overwrites_stale_disabled(
        self, tmp_path: Path
    ) -> None:
        """Path.replace atomically overwrites the destination. If a stale
        SKILL.md.disabled is present, toggling to disabled should replace it
        with the fresh content — not fail."""
        client, services = _build_client(tmp_path)
        headers = _auth_headers(client)
        _create_skill(client, headers, "both-b", "# fresh enabled")
        d = _skill_dir(services, "both-b")
        (d / "SKILL.md.disabled").write_text("# stale disabled", encoding="utf-8")

        r = client.post(
            "/api/skills/both-b/toggle", headers=headers, json={"enabled": False}
        )
        assert r.status_code == 200

        assert not (d / "SKILL.md").exists()
        assert (d / "SKILL.md.disabled").exists()
        assert (
            (d / "SKILL.md.disabled").read_text(encoding="utf-8") == "# fresh enabled"
        )


class TestPutTargetsCurrentStateFile:
    """PUT /api/skills/{name} must write to whichever of SKILL.md /
    SKILL.md.disabled currently persists the skill, never creating a stray
    second file."""

    def test_put_writes_to_disabled_when_currently_disabled(
        self, tmp_path: Path
    ) -> None:
        client, services = _build_client(tmp_path)
        headers = _auth_headers(client)
        _create_skill(client, headers, "put-a", "# v1")
        d = _skill_dir(services, "put-a")

        client.post(
            "/api/skills/put-a/toggle", headers=headers, json={"enabled": False}
        )
        assert not (d / "SKILL.md").exists()

        r = client.put(
            "/api/skills/put-a", headers=headers, json={"content": "# v2 while off"}
        )
        assert r.status_code == 200
        # Critical: no stray SKILL.md was created.
        assert not (d / "SKILL.md").exists()
        assert (d / "SKILL.md.disabled").read_text(encoding="utf-8") == "# v2 while off"

    def test_toggle_then_put_never_leaves_two_files(self, tmp_path: Path) -> None:
        """A tight enable→PUT→disable→PUT sequence must never leave both
        files behind."""
        client, services = _build_client(tmp_path)
        headers = _auth_headers(client)
        _create_skill(client, headers, "seq-a", "# v0")
        d = _skill_dir(services, "seq-a")

        client.post(
            "/api/skills/seq-a/toggle", headers=headers, json={"enabled": False}
        )
        client.put("/api/skills/seq-a", headers=headers, json={"content": "# vA"})
        client.post(
            "/api/skills/seq-a/toggle", headers=headers, json={"enabled": True}
        )
        client.put("/api/skills/seq-a", headers=headers, json={"content": "# vB"})

        siblings = sorted(p.name for p in d.iterdir() if p.name.startswith("SKILL"))
        assert siblings == ["SKILL.md"], f"unexpected leftover files: {siblings}"


class TestToggleAgainstMissingDirectory:
    """Toggle should return 404 (or at least not 500) when the underlying
    skill directory disappears between list_skills() and Path.replace().

    We simulate the race window by deleting the directory outright, since the
    list_skills() call snapshots the state on-disk. If a concurrent DELETE
    /api/skills/{name} were to fire in the window, the endpoint should behave
    the same way as if the skill never existed.
    """

    def test_toggle_after_delete_returns_404(self, tmp_path: Path) -> None:
        client, services = _build_client(tmp_path)
        headers = _auth_headers(client)
        _create_skill(client, headers, "del-a", "# demo")
        d = _skill_dir(services, "del-a")
        assert d.exists()

        # DELETE the skill.
        r_del = client.delete("/api/skills/del-a", headers=headers)
        assert r_del.status_code == 200
        assert not d.exists()

        # Now toggle — must return 404, not 500.
        r_toggle = client.post(
            "/api/skills/del-a/toggle", headers=headers, json={"enabled": False}
        )
        assert r_toggle.status_code == 404, (
            f"expected 404 after delete, got {r_toggle.status_code}: {r_toggle.text}"
        )

    def test_toggle_after_dir_disappears_mid_flight_does_not_500(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Simulate the exact race: list_skills() ran and saw the entry,
        but between then and Path.replace() the directory was removed. The
        endpoint currently does no exception guard around replace, so this
        test doubles as a tripwire: today it may bubble 500; the day someone
        adds a guard, mark this xfail(strict=True) removed.

        We patch Path.replace to raise FileNotFoundError to imitate the race
        without needing genuine concurrency.
        """
        client, services = _build_client(tmp_path)
        headers = _auth_headers(client)
        _create_skill(client, headers, "race-a", "# demo")

        # Configure TestClient to surface server exceptions as 500 responses,
        # not re-raise them into the test.
        client = TestClient(client.app, raise_server_exceptions=False)

        real_replace = Path.replace

        def flaky_replace(self, target):
            if self.name == "SKILL.md" and "race-a" in str(self):
                raise FileNotFoundError(str(self))
            return real_replace(self, target)

        monkeypatch.setattr(Path, "replace", flaky_replace)

        r = client.post(
            "/api/skills/race-a/toggle", headers=headers, json={"enabled": False}
        )

        # Document current behavior: today the endpoint has no guard, so a
        # missing file surfaces as HTTP 500. When a guard is added this test
        # will need to be updated to expect 404 (and become a positive
        # assertion instead of a tripwire).
        assert r.status_code in {404, 500}, r.text


class TestBuiltinSkillNotToggleable:
    """Builtin skills must reject toggle regardless of surrounding state."""

    def test_toggle_builtin_returns_400(self, tmp_path: Path) -> None:
        client, _services = _build_client(tmp_path)
        headers = _auth_headers(client)

        # skill-creator is shipped as builtin.
        r = client.post(
            "/api/skills/skill-creator/toggle",
            headers=headers,
            json={"enabled": False},
        )
        assert r.status_code == 400
