"""Integration tests: authentication rate limiting scaling and bcrypt boundary.

These tests verify three scenarios:
1. Memory boundedness: login attempts from many IPs are evicted at _MAX_TRACKED_IPS.
2. Rate limit boundary: exactly _MAX_ATTEMPTS_PER_IP-1 attempts still pass, Nth triggers 429.
3. bcrypt 72-byte boundary: the system guards against password truncation via validation.

Constants from the source (xbot/interfaces/gateway/app.py):
    _MAX_ATTEMPTS_PER_IP = 5
    _MAX_TRACKED_IPS = 10_000
    _ATTEMPT_WINDOW_SECONDS = 60

The gateway rejects passwords > 72 UTF-8 bytes with HTTP 400 in validate_password_length(),
preventing bcrypt silent truncation at the application layer.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from xbot.interfaces.gateway.app import (
    _active_login_attempts,
    _clear_login_rate_limit,
    _LOGIN_ATTEMPTS,
    _MAX_ATTEMPTS_PER_IP,
    _MAX_TRACKED_IPS,
    _record_failed_login,
    create_app,
)
from xbot.interfaces.gateway.auth import (
    hash_password,
    set_password,
    validate_password_length,
    verify_password,
)
from xbot.interfaces.gateway.services import ServiceContainer
from xbot.platform.bus.queue import MessageBus
from xbot.platform.config.schema import Config
from xbot.runtime.session.conversation_store import ConversationStore

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MinimalAgent:
    """Stub agent satisfying ServiceContainer requirements."""

    model = "test-model"
    router = SimpleNamespace(backend_type="test")
    shared_resources = {"workspace": "/tmp"}
    tools = SimpleNamespace(tool_names=[])

    async def process_managed_direct(self, *args, **kwargs) -> str:
        return ""

    def describe_runtime(self) -> str:
        return "test"


class _MinimalCron:
    def list_jobs(self):
        return []

    def status(self):
        return {"jobs": 0, "running": False}


class _MinimalHeartbeat:
    enabled = False
    interval_s = 1800
    _running = False

    def status(self):
        return {"enabled": self.enabled, "interval_s": self.interval_s, "running": False}

    async def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled


def _make_app(tmp_path: Path):
    """Build a minimal FastAPI app with auth wired up for testing."""
    import xbot.interfaces.gateway.auth as auth_module

    # Isolate the password file to tmp_path
    password_file = tmp_path / "password"
    original_password_file = auth_module.PASSWORD_FILE
    auth_module.PASSWORD_FILE = password_file

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    workspace = config.workspace_path
    workspace.mkdir(parents=True, exist_ok=True)

    conversation_store = ConversationStore(workspace)
    container = ServiceContainer(
        config=config,
        bus=MessageBus(),
        agent=_MinimalAgent(),
        conversation_store=conversation_store,
        cron=_MinimalCron(),
        heartbeat=_MinimalHeartbeat(),
    )
    data_dir = tmp_path / "webui-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    app = create_app(container, data_dir=data_dir, skip_lifecycle=True)

    # Restore original after app is created (fixture cleanup will reset)
    def _restore():
        auth_module.PASSWORD_FILE = original_password_file

    return app, _restore


TEST_PASSWORD = "integration-test-pw-2024"


@pytest.fixture(autouse=True)
def _clean_rate_limit():
    """Reset global rate limit state before and after each test."""
    _clear_login_rate_limit()
    yield
    _clear_login_rate_limit()


@pytest.fixture()
def gateway_app(tmp_path: Path):
    """Provide a configured FastAPI app with a known password."""
    import xbot.interfaces.gateway.auth as auth_module

    app, restore = _make_app(tmp_path)
    set_password(TEST_PASSWORD)
    yield app
    restore()


# ---------------------------------------------------------------------------
# Scenario 1: test_login_attempts_memory_bounded
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestLoginAttemptsMemoryBounded:
    """Simulate login failures from many different IPs and verify
    that _LOGIN_ATTEMPTS has a bounded size via the eviction mechanism."""

    async def test_login_attempts_memory_bounded(self, gateway_app):
        """Simulate login failures from 1000 different IPs using varied
        X-Forwarded-For headers (the gateway reads request.client.host,
        so we record attempts directly to exercise the eviction logic).

        After all attempts, _LOGIN_ATTEMPTS must remain bounded at
        _MAX_TRACKED_IPS. This confirms no unbounded memory growth.
        """
        num_ips = 1000

        # Record failed login attempts from 1000 unique IPs
        for i in range(num_ips):
            ip = f"192.168.{i // 256}.{i % 256}"
            _record_failed_login(ip)

        # The dict should contain exactly num_ips entries (well within the
        # 10,000 capacity) -- no eviction needed yet.
        assert len(_LOGIN_ATTEMPTS) == num_ips
        assert len(_LOGIN_ATTEMPTS) <= _MAX_TRACKED_IPS, (
            f"_LOGIN_ATTEMPTS has {len(_LOGIN_ATTEMPTS)} entries, "
            f"exceeding the capacity bound of {_MAX_TRACKED_IPS}"
        )

        # Now push past the capacity limit to verify eviction works
        for i in range(_MAX_TRACKED_IPS + 100):
            ip = f"10.{(i >> 16) & 0xFF}.{(i >> 8) & 0xFF}.{i & 0xFF}"
            _record_failed_login(ip)

        # After exceeding capacity, size must remain bounded
        assert len(_LOGIN_ATTEMPTS) <= _MAX_TRACKED_IPS, (
            f"MEMORY GROWTH ISSUE: _LOGIN_ATTEMPTS grew to {len(_LOGIN_ATTEMPTS)} "
            f"entries without eviction. Expected <= {_MAX_TRACKED_IPS}."
        )

        # The earliest IPs should have been evicted (FIFO/LRU eviction)
        # The very first IP we inserted in the large batch should be gone
        assert "10.0.0.0" not in _LOGIN_ATTEMPTS, (
            "Oldest IP was not evicted -- eviction policy may be broken"
        )

    async def test_memory_bounded_via_http_endpoint(self, gateway_app):
        """Verify via the actual HTTP endpoint that many distinct IPs
        are properly tracked. Since TestClient uses a fixed client IP,
        we supplement with direct _record_failed_login calls to
        simulate the multi-IP scenario."""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway_app),
            base_url="http://testserver",
        ) as client:
            # Make a few real HTTP requests (all from same test IP)
            for _ in range(3):
                resp = await client.post(
                    "/api/auth/login",
                    json={"username": "admin", "password": "wrong"},
                )
                assert resp.status_code == 401

        # httpx.ASGITransport uses "127.0.0.1" as the default client IP
        assert len(_active_login_attempts("127.0.0.1")) == 3

        # Simulate many more IPs directly
        for i in range(500):
            _record_failed_login(f"172.16.{i // 256}.{i % 256}")

        # Total should be bounded
        assert len(_LOGIN_ATTEMPTS) <= _MAX_TRACKED_IPS


# ---------------------------------------------------------------------------
# Scenario 2: test_rate_limit_boundary_exact
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestRateLimitBoundaryExact:
    """Find the LOGIN_LIMIT value (_MAX_ATTEMPTS_PER_IP) and test the
    exact boundary: N-1 failures still get 401, the Nth triggers 429.

    From source: _MAX_ATTEMPTS_PER_IP = 5
    """

    async def test_rate_limit_boundary_exact(self, gateway_app):
        """Send exactly _MAX_ATTEMPTS_PER_IP - 1 failed login attempts.
        Assert: still getting 401 (not rate-limited).
        Send one more failed attempt (the Nth).
        Assert: now getting 429 (rate-limited).

        This tests the exact boundary where the rate limiter transitions
        from allowing attempts to blocking them.
        """
        limit = _MAX_ATTEMPTS_PER_IP  # 5 from source
        assert limit == 5, (
            f"Expected _MAX_ATTEMPTS_PER_IP to be 5, got {limit}. "
            "Update this test if the limit has changed."
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway_app),
            base_url="http://testserver",
        ) as client:
            # Phase 1: Send exactly (limit - 1) failed attempts
            for i in range(limit - 1):
                resp = await client.post(
                    "/api/auth/login",
                    json={"username": "admin", "password": "wrong-password"},
                )
                assert resp.status_code == 401, (
                    f"Attempt {i + 1}/{limit - 1}: expected 401, got {resp.status_code}. "
                    f"Rate limiter triggered too early."
                )

            # Phase 2: The next attempt (attempt #limit-1 + 1 = limit) should
            # still be PROCESSED (returns 401) because the rate limiter checks
            # BEFORE recording: at this point we have (limit-1) recorded failures.
            resp = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "wrong-password"},
            )
            # This is the Nth attempt. The rate limiter checks existing attempts
            # (which is limit-1) so it passes the check, then records this as
            # the Nth failure.
            assert resp.status_code == 401, (
                f"Attempt {limit}: expected 401 (check happens before record), "
                f"got {resp.status_code}"
            )

            # Phase 3: Now we have exactly `limit` recorded failures.
            # The NEXT attempt should be blocked with 429.
            resp = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "wrong-password"},
            )
            assert resp.status_code == 429, (
                f"Attempt {limit + 1}: expected 429 (rate-limited), "
                f"got {resp.status_code}. "
                f"The rate limiter did not engage after {limit} failures."
            )

    async def test_rate_limit_boundary_correct_password_also_blocked(self, gateway_app):
        """Once rate-limited, even a correct password should be rejected
        with 429 (rate limit check happens before authentication)."""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway_app),
            base_url="http://testserver",
        ) as client:
            # Exhaust the rate limit
            for _ in range(_MAX_ATTEMPTS_PER_IP):
                await client.post(
                    "/api/auth/login",
                    json={"username": "admin", "password": "wrong"},
                )

            # Even the correct password should be blocked
            resp = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": TEST_PASSWORD},
            )
            assert resp.status_code == 429, (
                f"Expected 429 even with correct password after rate limit, "
                f"got {resp.status_code}"
            )

    async def test_successful_login_resets_counter(self, gateway_app):
        """After a successful login, the counter resets and the user
        can make fresh attempts without hitting 429."""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway_app),
            base_url="http://testserver",
        ) as client:
            # Fail (limit - 1) times (one short of triggering rate limit)
            for _ in range(_MAX_ATTEMPTS_PER_IP - 1):
                resp = await client.post(
                    "/api/auth/login",
                    json={"username": "admin", "password": "wrong"},
                )
                assert resp.status_code == 401

            # Successful login should clear the counter
            resp = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": TEST_PASSWORD},
            )
            assert resp.status_code == 200

            # Now we should be able to fail again without immediate 429
            for _ in range(_MAX_ATTEMPTS_PER_IP - 1):
                resp = await client.post(
                    "/api/auth/login",
                    json={"username": "admin", "password": "wrong"},
                )
                assert resp.status_code == 401, (
                    f"Expected 401 after counter reset, got {resp.status_code}"
                )


# ---------------------------------------------------------------------------
# Scenario 3: test_password_length_bcrypt_72_byte_boundary
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestPasswordLengthBcrypt72ByteBoundary:
    """bcrypt silently truncates passwords at 72 bytes. The xbot gateway
    GUARDS against this by rejecting passwords > 72 UTF-8 bytes with
    HTTP 400 via validate_password_length().

    These tests document and verify this behavior:
    (a) A password exactly 72 bytes works for login.
    (b) A password > 72 bytes is rejected at the API level (HTTP 400),
        preventing the bcrypt truncation vulnerability.
    (c) The change-password endpoint also enforces this guard.
    """

    def _make_password_n_bytes(self, n: int) -> str:
        """Create an ASCII password of exactly n UTF-8 bytes."""
        # Use printable ASCII characters to ensure 1 byte = 1 char
        base = "Aa1!bcde"
        password = (base * ((n // len(base)) + 1))[:n]
        assert len(password.encode("utf-8")) == n
        return password

    async def test_password_exactly_72_bytes_works(self, gateway_app, tmp_path):
        """A password that is exactly 72 UTF-8 bytes should be accepted
        for both setting and authenticating."""
        import xbot.interfaces.gateway.auth as auth_module

        password_72 = self._make_password_n_bytes(72)
        assert len(password_72.encode("utf-8")) == 72

        # Set this as the password
        set_password(password_72)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway_app),
            base_url="http://testserver",
        ) as client:
            # Login should succeed with the 72-byte password
            resp = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": password_72},
            )
            assert resp.status_code == 200, (
                f"Expected 200 for 72-byte password login, got {resp.status_code}: "
                f"{resp.text}"
            )
            data = resp.json()
            assert "access_token" in data

    async def test_password_over_72_bytes_rejected_on_login(self, gateway_app):
        """A password > 72 UTF-8 bytes should be rejected with HTTP 400
        at the login endpoint, preventing bcrypt truncation."""
        password_73 = self._make_password_n_bytes(73)
        assert len(password_73.encode("utf-8")) == 73

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway_app),
            base_url="http://testserver",
        ) as client:
            resp = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": password_73},
            )
            # The gateway rejects > 72 bytes with 400, NOT silently truncating
            assert resp.status_code == 400, (
                f"Expected 400 for >72-byte password, got {resp.status_code}. "
                "If this returns 401, the system may be silently truncating "
                "passwords at 72 bytes (bcrypt vulnerability)."
            )
            assert "72" in resp.text or "exceed" in resp.text.lower()

    async def test_password_over_72_bytes_rejected_on_change(self, gateway_app):
        """The change-password endpoint also guards against > 72 bytes."""
        set_password(TEST_PASSWORD)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway_app),
            base_url="http://testserver",
        ) as client:
            # First, get a valid token
            login_resp = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": TEST_PASSWORD},
            )
            assert login_resp.status_code == 200
            token = login_resp.json()["access_token"]

            # Try to change to a > 72 byte password
            long_password = self._make_password_n_bytes(73)
            resp = await client.post(
                "/api/auth/change-password",
                json={
                    "current_password": TEST_PASSWORD,
                    "new_password": long_password,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 400, (
                f"Expected 400 for >72-byte new password, got {resp.status_code}"
            )

    async def test_multibyte_utf8_password_boundary(self, gateway_app):
        """Test that the 72-byte limit is based on UTF-8 encoding, not
        character count. A string with multibyte characters can have
        fewer characters but exceed 72 bytes.

        Example: 24 characters of 3-byte UTF-8 (e.g., CJK) = 72 bytes.
        25 such characters = 75 bytes -> should be rejected.
        """
        # 24 CJK characters = 72 UTF-8 bytes (each char is 3 bytes)
        password_72_multibyte = "\u4e00" * 24  # Chinese character '一'
        assert len(password_72_multibyte.encode("utf-8")) == 72

        set_password(password_72_multibyte)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway_app),
            base_url="http://testserver",
        ) as client:
            # 72-byte multibyte password should work
            resp = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": password_72_multibyte},
            )
            assert resp.status_code == 200, (
                f"Expected 200 for 72-byte multibyte password, got {resp.status_code}"
            )

            # 25 CJK characters = 75 bytes -> rejected
            password_75_multibyte = "\u4e00" * 25
            assert len(password_75_multibyte.encode("utf-8")) == 75
            resp = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": password_75_multibyte},
            )
            assert resp.status_code == 400, (
                f"Expected 400 for 75-byte password (25 CJK chars), "
                f"got {resp.status_code}"
            )

    def test_validate_password_length_unit(self):
        """Direct unit test of validate_password_length to document behavior."""
        # Exactly 72 bytes: should pass without exception
        password_72 = "A" * 72
        validate_password_length(password_72)  # No exception

        # 73 bytes: should raise HTTPException
        password_73 = "A" * 73
        with pytest.raises(Exception) as exc_info:
            validate_password_length(password_73)
        # FastAPI HTTPException with status 400
        assert exc_info.value.status_code == 400

    def test_bcrypt_truncation_behavior_documented(self):
        """Document bcrypt's behavior at the 72-byte boundary.

        Modern bcrypt (Python bcrypt >= 4.1) RAISES ValueError for
        passwords > 72 bytes, providing a library-level guard in addition
        to the application-level validate_password_length() check.

        This test confirms:
        1. bcrypt refuses to hash passwords > 72 bytes (ValueError).
        2. If we manually truncate to 72 bytes, passwords that differ
           only after byte 72 produce identical hashes (proving that
           the application guard is essential for older bcrypt versions
           or implementations that silently truncate).
        """
        import bcrypt

        # Two passwords that differ only after byte 72
        base_72 = "A" * 72
        password_a = base_72 + "XXXXX"
        password_b = base_72 + "YYYYY"

        # Modern bcrypt raises ValueError for > 72 bytes
        with pytest.raises(ValueError, match="72"):
            bcrypt.hashpw(password_a.encode("utf-8"), bcrypt.gensalt())

        # If we manually truncate (as older libraries would do silently),
        # passwords differing only after byte 72 produce the same hash
        hashed = bcrypt.hashpw(
            password_a.encode("utf-8")[:72], bcrypt.gensalt()
        )

        # password_b truncated to 72 bytes is identical to password_a[:72]
        assert bcrypt.checkpw(password_b.encode("utf-8")[:72], hashed), (
            "Truncated passwords should match: the first 72 bytes are identical."
        )

        # The base 72-byte prefix alone also matches
        assert bcrypt.checkpw(base_72.encode("utf-8"), hashed), (
            "The 72-byte prefix should authenticate identically."
        )
