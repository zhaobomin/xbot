"""Comprehensive tests for xbot.platform.security.network — SSRF protection.

Covers:
  - _is_private() — IPv4 private ranges, IPv6 private, IPv4-mapped IPv6, public IPs
  - validate_url_target() — scheme, domain, hostname, DNS, private-IP blocking
  - async_validate_url_target() — async mirror of above
  - async_validate_and_pin_url() — pinned-host dict, failure paths, multi-IP
  - validate_resolved_url() — direct IP, hostname resolution, redirect blocking
  - async_validate_resolved_url() — async mirror of above
  - contains_internal_url() — regex extraction + validation
  - async_contains_internal_url() — async mirror of above

ALL DNS resolution is mocked (socket.getaddrinfo and loop.getaddrinfo).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from unittest.mock import AsyncMock, patch

import pytest

from xbot.platform.security.network import (
    _is_private,
    async_contains_internal_url,
    async_validate_and_pin_url,
    async_validate_resolved_url,
    async_validate_url_target,
    contains_internal_url,
    validate_resolved_url,
    validate_url_target,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_IPV4 = ipaddress.ip_address
_IPV6 = ipaddress.ip_address


def _make_addrinfo(ip_str: str, family: int = socket.AF_INET):
    """Build a minimal getaddrinfo-style tuple for one address."""
    return (family, socket.SOCK_STREAM, 6, "", (ip_str, 0))


def _mock_getaddrinfo(*ips: str):
    """Return a mock for socket.getaddrinfo that yields the given IPs."""
    infos = [_make_addrinfo(ip) for ip in ips]
    return infos


def _mock_async_getaddrinfo(*ips: str):
    """Return an AsyncMock for loop.getaddrinfo that yields the given IPs."""
    infos = [_make_addrinfo(ip) for ip in ips]
    mock = AsyncMock(return_value=infos)
    return mock


# ===========================================================================
# _is_private — IPv4
# ===========================================================================


class TestIsPrivateIPv4:
    """_is_private for IPv4 addresses."""

    # 10.0.0.0/8
    def test_10_network(self):
        assert _is_private(_IPV4("10.0.0.0"))
        assert _is_private(_IPV4("10.0.0.1"))
        assert _is_private(_IPV4("10.255.255.255"))

    # 172.16.0.0/12
    def test_172_16_network(self):
        assert _is_private(_IPV4("172.16.0.0"))
        assert _is_private(_IPV4("172.16.0.1"))
        assert _is_private(_IPV4("172.31.255.255"))

    def test_172_just_outside_range(self):
        assert not _is_private(_IPV4("172.15.255.255"))
        assert not _is_private(_IPV4("172.32.0.0"))

    # 192.168.0.0/16
    def test_192_168_network(self):
        assert _is_private(_IPV4("192.168.0.0"))
        assert _is_private(_IPV4("192.168.1.1"))
        assert _is_private(_IPV4("192.168.255.255"))

    # 127.0.0.0/8 — loopback
    def test_127_loopback(self):
        assert _is_private(_IPV4("127.0.0.1"))
        assert _is_private(_IPV4("127.0.0.2"))
        assert _is_private(_IPV4("127.255.255.255"))

    # 169.254.0.0/16 — link-local / cloud metadata
    def test_169_254_link_local(self):
        assert _is_private(_IPV4("169.254.0.0"))
        assert _is_private(_IPV4("169.254.169.254"))  # AWS metadata
        assert _is_private(_IPV4("169.254.255.255"))

    # 100.64.0.0/10 — carrier-grade NAT
    def test_100_64_cgnat(self):
        assert _is_private(_IPV4("100.64.0.0"))
        assert _is_private(_IPV4("100.64.0.1"))
        assert _is_private(_IPV4("100.127.255.255"))

    def test_100_64_just_outside_range(self):
        assert not _is_private(_IPV4("100.63.255.255"))
        assert not _is_private(_IPV4("100.128.0.0"))

    # 0.0.0.0/8
    def test_0_network(self):
        assert _is_private(_IPV4("0.0.0.0"))
        assert _is_private(_IPV4("0.0.0.1"))
        assert _is_private(_IPV4("0.255.255.255"))

    # Public IPs — must NOT be private
    def test_public_ips(self):
        assert not _is_private(_IPV4("8.8.8.8"))
        assert not _is_private(_IPV4("1.1.1.1"))
        assert not _is_private(_IPV4("93.184.216.34"))
        assert not _is_private(_IPV4("203.0.113.1"))
        assert not _is_private(_IPV4("198.51.100.1"))
        assert not _is_private(_IPV4("172.15.0.1"))
        assert not _is_private(_IPV4("192.167.255.255"))


# ===========================================================================
# _is_private — IPv6
# ===========================================================================


class TestIsPrivateIPv6:
    """_is_private for IPv6 addresses."""

    def test_loopback(self):
        assert _is_private(_IPV6("::1"))

    def test_unique_local_fc00(self):
        assert _is_private(_IPV6("fc00::"))
        assert _is_private(_IPV6("fc00::1"))
        assert _is_private(_IPV6("fd00::1"))
        assert _is_private(_IPV6("fdff:ffff:ffff:ffff:ffff:ffff:ffff:ffff"))

    def test_link_local_fe80(self):
        assert _is_private(_IPV6("fe80::"))
        assert _is_private(_IPV6("fe80::1"))
        assert _is_private(_IPV6("febf::1"))

    def test_public_ipv6(self):
        assert not _is_private(_IPV6("2001:4860:4860::8888"))
        assert not _is_private(_IPV6("2606:4700:4700::1111"))
        assert not _is_private(_IPV6("::2"))  # not in any blocked net


# ===========================================================================
# _is_private — IPv4-mapped IPv6
# ===========================================================================


class TestIsPrivateIPv4MappedIPv6:
    """IPv4-mapped IPv6 addresses should be checked against IPv4 ranges."""

    def test_mapped_loopback(self):
        # ::ffff:127.0.0.1
        addr = _IPV6("::ffff:127.0.0.1")
        assert addr.ipv4_mapped is not None
        assert _is_private(addr)

    def test_mapped_private_10(self):
        addr = _IPV6("::ffff:10.0.0.1")
        assert addr.ipv4_mapped is not None
        assert _is_private(addr)

    def test_mapped_private_192_168(self):
        addr = _IPV6("::ffff:192.168.1.1")
        assert addr.ipv4_mapped is not None
        assert _is_private(addr)

    def test_mapped_private_172_16(self):
        addr = _IPV6("::ffff:172.16.0.1")
        assert addr.ipv4_mapped is not None
        assert _is_private(addr)

    def test_mapped_link_local(self):
        addr = _IPV6("::ffff:169.254.169.254")
        assert addr.ipv4_mapped is not None
        assert _is_private(addr)

    def test_mapped_public(self):
        addr = _IPV6("::ffff:8.8.8.8")
        assert addr.ipv4_mapped is not None
        assert not _is_private(addr)

    def test_mapped_zero_network(self):
        addr = _IPV6("::ffff:0.0.0.0")
        assert addr.ipv4_mapped is not None
        assert _is_private(addr)


# ===========================================================================
# validate_url_target — sync
# ===========================================================================


class TestValidateUrlTarget:
    """Sync SSRF validation for URLs."""

    # --- valid public URL ---
    @patch("xbot.platform.security.network.socket.getaddrinfo")
    def test_valid_public_url(self, mock_gai):
        mock_gai.return_value = _mock_getaddrinfo("93.184.216.34")
        ok, err = validate_url_target("https://example.com/path")
        assert ok is True
        assert err == ""

    @patch("xbot.platform.security.network.socket.getaddrinfo")
    def test_valid_public_url_http(self, mock_gai):
        mock_gai.return_value = _mock_getaddrinfo("1.1.1.1")
        ok, err = validate_url_target("http://cloudflare.com")
        assert ok is True
        assert err == ""

    # --- scheme rejection ---
    @pytest.mark.parametrize("url", [
        "ftp://files.example.com/data",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "gopher://evil.com/",
        "data:text/html,<h1>hi</h1>",
    ])
    def test_blocked_schemes(self, url):
        ok, err = validate_url_target(url)
        assert ok is False
        assert "http/https" in err

    def test_empty_scheme(self):
        ok, err = validate_url_target("//example.com/path")
        assert ok is False
        assert "http/https" in err

    # --- missing domain ---
    def test_missing_domain(self):
        ok, err = validate_url_target("http:///path-only")
        assert ok is False
        assert "Missing" in err

    # --- missing hostname ---
    def test_missing_hostname_empty_netloc(self):
        ok, err = validate_url_target("http://")
        assert ok is False

    # --- DNS resolution failure ---
    @patch("xbot.platform.security.network.socket.getaddrinfo",
           side_effect=socket.gaierror("name resolution failed"))
    def test_dns_failure(self, mock_gai):
        ok, err = validate_url_target("https://nonexistent.invalid")
        assert ok is False
        assert "Cannot resolve" in err

    # --- private IP blocking ---
    @patch("xbot.platform.security.network.socket.getaddrinfo")
    def test_blocks_localhost(self, mock_gai):
        mock_gai.return_value = _mock_getaddrinfo("127.0.0.1")
        ok, err = validate_url_target("http://localhost/secret")
        assert ok is False
        assert "Blocked" in err
        assert "127.0.0.1" in err

    @patch("xbot.platform.security.network.socket.getaddrinfo")
    def test_blocks_10_network(self, mock_gai):
        mock_gai.return_value = _mock_getaddrinfo("10.0.0.1")
        ok, err = validate_url_target("http://internal.corp/api")
        assert ok is False
        assert "Blocked" in err

    @patch("xbot.platform.security.network.socket.getaddrinfo")
    def test_blocks_192_168(self, mock_gai):
        mock_gai.return_value = _mock_getaddrinfo("192.168.1.1")
        ok, err = validate_url_target("http://router.local/")
        assert ok is False
        assert "Blocked" in err

    @patch("xbot.platform.security.network.socket.getaddrinfo")
    def test_blocks_169_254(self, mock_gai):
        mock_gai.return_value = _mock_getaddrinfo("169.254.169.254")
        ok, err = validate_url_target("http://metadata.google.internal/")
        assert ok is False
        assert "Blocked" in err

    @patch("xbot.platform.security.network.socket.getaddrinfo")
    def test_blocks_ipv6_loopback(self, mock_gai):
        mock_gai.return_value = [_make_addrinfo("::1", socket.AF_INET6)]
        ok, err = validate_url_target("http://[::1]/admin")
        assert ok is False
        assert "Blocked" in err

    @patch("xbot.platform.security.network.socket.getaddrinfo")
    def test_blocks_ipv4_mapped_ipv6(self, mock_gai):
        mock_gai.return_value = [_make_addrinfo("::ffff:127.0.0.1", socket.AF_INET6)]
        ok, err = validate_url_target("http://[::ffff:127.0.0.1]/")
        assert ok is False
        assert "Blocked" in err

    # --- multiple resolved IPs, one private ---
    @patch("xbot.platform.security.network.socket.getaddrinfo")
    def test_blocks_when_any_resolved_ip_is_private(self, mock_gai):
        mock_gai.return_value = _mock_getaddrinfo("93.184.216.34", "10.0.0.1")
        ok, err = validate_url_target("https://dual.example.com/")
        assert ok is False
        assert "Blocked" in err

    # --- multiple resolved IPs, all public ---
    @patch("xbot.platform.security.network.socket.getaddrinfo")
    def test_passes_when_all_resolved_ips_public(self, mock_gai):
        mock_gai.return_value = _mock_getaddrinfo("93.184.216.34", "1.1.1.1")
        ok, err = validate_url_target("https://multi.example.com/")
        assert ok is True
        assert err == ""


# ===========================================================================
# async_validate_url_target
# ===========================================================================


class TestAsyncValidateUrlTarget:
    """Async SSRF validation for URLs."""

    @patch("xbot.platform.security.network.asyncio.get_running_loop")
    async def test_valid_public_url(self, mock_loop):
        mock_loop.return_value.getaddrinfo = _mock_async_getaddrinfo("93.184.216.34")
        ok, err = await async_validate_url_target("https://example.com/path")
        assert ok is True
        assert err == ""

    @pytest.mark.parametrize("url", [
        "ftp://files.example.com/data",
        "file:///etc/passwd",
        "javascript:alert(1)",
    ])
    async def test_blocked_schemes(self, url):
        ok, err = await async_validate_url_target(url)
        assert ok is False
        assert "http/https" in err

    async def test_empty_scheme(self):
        ok, err = await async_validate_url_target("//example.com/path")
        assert ok is False
        assert "http/https" in err

    async def test_missing_domain(self):
        ok, err = await async_validate_url_target("http:///path-only")
        assert ok is False

    @patch("xbot.platform.security.network.asyncio.get_running_loop")
    async def test_dns_failure(self, mock_loop):
        mock_gai = AsyncMock(side_effect=socket.gaierror("name resolution failed"))
        mock_loop.return_value.getaddrinfo = mock_gai
        ok, err = await async_validate_url_target("https://nonexistent.invalid")
        assert ok is False
        assert "Cannot resolve" in err

    @patch("xbot.platform.security.network.asyncio.get_running_loop")
    async def test_blocks_localhost(self, mock_loop):
        mock_loop.return_value.getaddrinfo = _mock_async_getaddrinfo("127.0.0.1")
        ok, err = await async_validate_url_target("http://localhost/secret")
        assert ok is False
        assert "Blocked" in err

    @patch("xbot.platform.security.network.asyncio.get_running_loop")
    async def test_blocks_cloud_metadata(self, mock_loop):
        mock_loop.return_value.getaddrinfo = _mock_async_getaddrinfo("169.254.169.254")
        ok, err = await async_validate_url_target("http://169.254.169.254/latest/meta-data/")
        assert ok is False
        assert "Blocked" in err

    @patch("xbot.platform.security.network.asyncio.get_running_loop")
    async def test_blocks_ipv4_mapped_ipv6(self, mock_loop):
        mock_loop.return_value.getaddrinfo = AsyncMock(
            return_value=[_make_addrinfo("::ffff:10.0.0.1", socket.AF_INET6)]
        )
        ok, err = await async_validate_url_target("http://evil.example/")
        assert ok is False
        assert "Blocked" in err

    @patch("xbot.platform.security.network.asyncio.get_running_loop")
    async def test_passes_all_public(self, mock_loop):
        mock_loop.return_value.getaddrinfo = _mock_async_getaddrinfo("8.8.8.8", "1.1.1.1")
        ok, err = await async_validate_url_target("https://dns.google/")
        assert ok is True


# ===========================================================================
# async_validate_and_pin_url
# ===========================================================================


class TestAsyncValidateAndPinUrl:
    """Async validation + host pinning."""

    @patch("xbot.platform.security.network.asyncio.get_running_loop")
    async def test_success_returns_pinned_host(self, mock_loop):
        mock_loop.return_value.getaddrinfo = _mock_async_getaddrinfo("93.184.216.34")
        ok, err, pinned = await async_validate_and_pin_url("https://example.com/api")
        assert ok is True
        assert err == ""
        assert pinned == {"example.com": "93.184.216.34"}

    @patch("xbot.platform.security.network.asyncio.get_running_loop")
    async def test_success_multiple_ips_pins_first(self, mock_loop):
        mock_loop.return_value.getaddrinfo = _mock_async_getaddrinfo("93.184.216.34", "1.1.1.1")
        ok, err, pinned = await async_validate_and_pin_url("https://example.com/")
        assert ok is True
        assert pinned == {"example.com": "93.184.216.34"}

    async def test_bad_scheme(self):
        ok, err, pinned = await async_validate_and_pin_url("ftp://example.com/")
        assert ok is False
        assert pinned == {}

    async def test_missing_domain(self):
        ok, err, pinned = await async_validate_and_pin_url("http:///path")
        assert ok is False
        assert pinned == {}

    @patch("xbot.platform.security.network.asyncio.get_running_loop")
    async def test_dns_failure(self, mock_loop):
        mock_loop.return_value.getaddrinfo = AsyncMock(
            side_effect=socket.gaierror("no such host")
        )
        ok, err, pinned = await async_validate_and_pin_url("https://noexist.invalid/")
        assert ok is False
        assert "Cannot resolve" in err
        assert pinned == {}

    @patch("xbot.platform.security.network.asyncio.get_running_loop")
    async def test_generic_exception_returns_failure(self, mock_loop):
        mock_loop.return_value.getaddrinfo = AsyncMock(
            side_effect=OSError("network down")
        )
        ok, err, pinned = await async_validate_and_pin_url("https://broken.example/")
        assert ok is False
        assert pinned == {}

    @patch("xbot.platform.security.network.asyncio.get_running_loop")
    async def test_empty_resolved_list(self, mock_loop):
        """DNS returns no results — should fail."""
        mock_loop.return_value.getaddrinfo = AsyncMock(return_value=[])
        ok, err, pinned = await async_validate_and_pin_url("https://empty.example/")
        assert ok is False
        assert "Cannot resolve" in err
        assert pinned == {}

    @patch("xbot.platform.security.network.asyncio.get_running_loop")
    async def test_private_ip_blocks(self, mock_loop):
        mock_loop.return_value.getaddrinfo = _mock_async_getaddrinfo("10.0.0.1")
        ok, err, pinned = await async_validate_and_pin_url("http://internal.corp/")
        assert ok is False
        assert "Blocked" in err
        assert pinned == {}

    @patch("xbot.platform.security.network.asyncio.get_running_loop")
    async def test_missing_hostname(self, mock_loop):
        ok, err, pinned = await async_validate_and_pin_url("http://")
        assert ok is False
        assert pinned == {}

    @patch("xbot.platform.security.network.asyncio.get_running_loop")
    async def test_ipv6_public(self, mock_loop):
        mock_loop.return_value.getaddrinfo = AsyncMock(
            return_value=[_make_addrinfo("2001:4860:4860::8888", socket.AF_INET6)]
        )
        ok, err, pinned = await async_validate_and_pin_url("https://dns.google/")
        assert ok is True
        assert pinned == {"dns.google": "2001:4860:4860::8888"}


# ===========================================================================
# validate_resolved_url — sync
# ===========================================================================


class TestValidateResolvedUrl:
    """Validate URL after redirect — checks direct IP or resolves hostname."""

    # --- direct IP: public ---
    def test_direct_public_ip(self):
        ok, err = validate_resolved_url("http://93.184.216.34/page")
        assert ok is True
        assert err == ""

    # --- direct IP: private ---
    def test_direct_private_ip_127(self):
        ok, err = validate_resolved_url("http://127.0.0.1/admin")
        assert ok is False
        assert "private address" in err

    def test_direct_private_ip_10(self):
        ok, err = validate_resolved_url("http://10.0.0.1/secret")
        assert ok is False
        assert "private address" in err

    def test_direct_private_ip_169_254(self):
        ok, err = validate_resolved_url("http://169.254.169.254/latest")
        assert ok is False
        assert "private address" in err

    def test_direct_private_ip_0(self):
        ok, err = validate_resolved_url("http://0.0.0.0/")
        assert ok is False
        assert "private address" in err

    # --- direct IPv6 ---
    def test_direct_ipv6_loopback(self):
        ok, err = validate_resolved_url("http://[::1]/admin")
        assert ok is False
        assert "private address" in err

    def test_direct_ipv6_public(self):
        ok, err = validate_resolved_url("http://[2001:4860:4860::8888]/")
        assert ok is True
        assert err == ""

    # --- hostname resolution ---
    @patch("xbot.platform.security.network.socket.getaddrinfo")
    def test_hostname_resolves_public(self, mock_gai):
        mock_gai.return_value = _mock_getaddrinfo("93.184.216.34")
        ok, err = validate_resolved_url("https://example.com/page")
        assert ok is True

    @patch("xbot.platform.security.network.socket.getaddrinfo")
    def test_hostname_resolves_private(self, mock_gai):
        mock_gai.return_value = _mock_getaddrinfo("192.168.1.1")
        ok, err = validate_resolved_url("https://internal.corp/page")
        assert ok is False
        assert "resolves to private address" in err

    @patch("xbot.platform.security.network.socket.getaddrinfo",
           side_effect=socket.gaierror("no such host"))
    def test_hostname_dns_failure(self, mock_gai):
        ok, err = validate_resolved_url("https://noexist.invalid/page")
        assert ok is False
        assert "cannot be resolved" in err

    @patch("xbot.platform.security.network.socket.getaddrinfo")
    def test_hostname_empty_resolved_list(self, mock_gai):
        mock_gai.return_value = []
        ok, err = validate_resolved_url("https://empty.example/page")
        assert ok is False
        assert "cannot be resolved" in err

    # --- scheme ---
    def test_bad_scheme(self):
        ok, err = validate_resolved_url("ftp://example.com/file")
        assert ok is False
        assert "http/https" in err

    # --- missing hostname ---
    def test_missing_hostname(self):
        ok, err = validate_resolved_url("http://")
        assert ok is False

    # --- multiple resolved, one private ---
    @patch("xbot.platform.security.network.socket.getaddrinfo")
    def test_blocks_when_any_resolved_is_private(self, mock_gai):
        mock_gai.return_value = _mock_getaddrinfo("1.1.1.1", "10.0.0.1")
        ok, err = validate_resolved_url("https://mixed.example/")
        assert ok is False
        assert "private address" in err


# ===========================================================================
# async_validate_resolved_url
# ===========================================================================


class TestAsyncValidateResolvedUrl:
    """Async version of validate_resolved_url."""

    async def test_direct_public_ip(self):
        ok, err = await async_validate_resolved_url("http://93.184.216.34/page")
        assert ok is True

    async def test_direct_private_ip(self):
        ok, err = await async_validate_resolved_url("http://127.0.0.1/admin")
        assert ok is False
        assert "private address" in err

    async def test_direct_ipv6_private(self):
        ok, err = await async_validate_resolved_url("http://[::1]/")
        assert ok is False
        assert "private address" in err

    async def test_direct_ipv6_public(self):
        ok, err = await async_validate_resolved_url("http://[2606:4700:4700::1111]/")
        assert ok is True

    @patch("xbot.platform.security.network.asyncio.get_running_loop")
    async def test_hostname_resolves_public(self, mock_loop):
        mock_loop.return_value.getaddrinfo = _mock_async_getaddrinfo("93.184.216.34")
        ok, err = await async_validate_resolved_url("https://example.com/page")
        assert ok is True

    @patch("xbot.platform.security.network.asyncio.get_running_loop")
    async def test_hostname_resolves_private(self, mock_loop):
        mock_loop.return_value.getaddrinfo = _mock_async_getaddrinfo("172.16.0.1")
        ok, err = await async_validate_resolved_url("https://internal.corp/")
        assert ok is False
        assert "private address" in err

    @patch("xbot.platform.security.network.asyncio.get_running_loop")
    async def test_dns_failure(self, mock_loop):
        mock_loop.return_value.getaddrinfo = AsyncMock(
            side_effect=socket.gaierror("no such host")
        )
        ok, err = await async_validate_resolved_url("https://noexist.invalid/")
        assert ok is False
        assert "cannot be resolved" in err

    @patch("xbot.platform.security.network.asyncio.get_running_loop")
    async def test_generic_exception(self, mock_loop):
        mock_loop.return_value.getaddrinfo = AsyncMock(
            side_effect=OSError("network error")
        )
        ok, err = await async_validate_resolved_url("https://broken.example/")
        assert ok is False
        assert "cannot be resolved" in err

    @patch("xbot.platform.security.network.asyncio.get_running_loop")
    async def test_empty_resolved_list(self, mock_loop):
        mock_loop.return_value.getaddrinfo = AsyncMock(return_value=[])
        ok, err = await async_validate_resolved_url("https://empty.example/")
        assert ok is False
        assert "cannot be resolved" in err

    async def test_bad_scheme(self):
        ok, err = await async_validate_resolved_url("file:///etc/shadow")
        assert ok is False
        assert "http/https" in err

    async def test_missing_hostname(self):
        ok, err = await async_validate_resolved_url("http://")
        assert ok is False

    @patch("xbot.platform.security.network.asyncio.get_running_loop")
    async def test_multiple_resolved_one_private(self, mock_loop):
        mock_loop.return_value.getaddrinfo = _mock_async_getaddrinfo("8.8.8.8", "10.0.0.1")
        ok, err = await async_validate_resolved_url("https://dual.example/")
        assert ok is False
        assert "private address" in err


# ===========================================================================
# contains_internal_url — sync
# ===========================================================================


class TestContainsInternalUrl:
    """Regex-based detection of internal URLs in arbitrary strings."""

    @patch("xbot.platform.security.network.socket.getaddrinfo")
    def test_string_with_internal_url(self, mock_gai):
        mock_gai.return_value = _mock_getaddrinfo("127.0.0.1")
        assert contains_internal_url("fetch http://localhost/admin") is True

    @patch("xbot.platform.security.network.socket.getaddrinfo")
    def test_string_with_only_public_urls(self, mock_gai):
        mock_gai.return_value = _mock_getaddrinfo("93.184.216.34")
        assert contains_internal_url("visit https://example.com/page") is False

    def test_empty_string(self):
        assert contains_internal_url("") is False

    def test_no_urls_in_string(self):
        assert contains_internal_url("just plain text, no URLs here") is False

    @patch("xbot.platform.security.network.socket.getaddrinfo")
    def test_multiple_urls_one_internal(self, mock_gai):
        """First URL public, second URL private → returns True."""
        def side_effect(hostname, *args, **kwargs):
            if hostname == "example.com":
                return _mock_getaddrinfo("93.184.216.34")
            elif hostname == "internal.corp":
                return _mock_getaddrinfo("10.0.0.1")
            raise socket.gaierror("unknown")

        mock_gai.side_effect = side_effect
        result = contains_internal_url(
            "visit https://example.com and http://internal.corp/api"
        )
        assert result is True

    @patch("xbot.platform.security.network.socket.getaddrinfo")
    def test_multiple_urls_all_public(self, mock_gai):
        mock_gai.return_value = _mock_getaddrinfo("93.184.216.34")
        result = contains_internal_url(
            "visit https://example.com and http://example.org/api"
        )
        assert result is False

    @patch("xbot.platform.security.network.socket.getaddrinfo")
    def test_url_with_cloud_metadata(self, mock_gai):
        mock_gai.return_value = _mock_getaddrinfo("169.254.169.254")
        assert contains_internal_url(
            "curl http://169.254.169.254/latest/meta-data/"
        ) is True

    @patch("xbot.platform.security.network.socket.getaddrinfo")
    def test_ignores_non_http_schemes(self, mock_gai):
        """file:// won't match the regex (only http/https), so no internal URL."""
        assert contains_internal_url("use file:///etc/passwd") is False
        mock_gai.assert_not_called()


# ===========================================================================
# async_contains_internal_url
# ===========================================================================


class TestAsyncContainsInternalUrl:
    """Async version of internal-URL detection."""

    @patch("xbot.platform.security.network.asyncio.get_running_loop")
    async def test_string_with_internal_url(self, mock_loop):
        mock_loop.return_value.getaddrinfo = _mock_async_getaddrinfo("127.0.0.1")
        result = await async_contains_internal_url("fetch http://localhost/admin")
        assert result is True

    @patch("xbot.platform.security.network.asyncio.get_running_loop")
    async def test_string_with_only_public_urls(self, mock_loop):
        mock_loop.return_value.getaddrinfo = _mock_async_getaddrinfo("93.184.216.34")
        result = await async_contains_internal_url("visit https://example.com/")
        assert result is False

    async def test_empty_string(self):
        result = await async_contains_internal_url("")
        assert result is False

    async def test_no_urls_in_string(self):
        result = await async_contains_internal_url("just plain text")
        assert result is False

    @patch("xbot.platform.security.network.asyncio.get_running_loop")
    async def test_multiple_urls_one_internal(self, mock_loop):
        call_count = 0

        async def fake_getaddrinfo(hostname, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if hostname == "example.com":
                return [_make_addrinfo("93.184.216.34")]
            elif hostname == "internal.corp":
                return [_make_addrinfo("10.0.0.1")]
            raise socket.gaierror("unknown")

        mock_loop.return_value.getaddrinfo = fake_getaddrinfo
        result = await async_contains_internal_url(
            "visit https://example.com and http://internal.corp/api"
        )
        assert result is True

    @patch("xbot.platform.security.network.asyncio.get_running_loop")
    async def test_multiple_urls_all_public(self, mock_loop):
        mock_loop.return_value.getaddrinfo = _mock_async_getaddrinfo("93.184.216.34")
        result = await async_contains_internal_url(
            "visit https://example.com and http://example.org/api"
        )
        assert result is False


# ===========================================================================
# Edge cases
# ===========================================================================


class TestEdgeCases:
    """Boundary / edge cases that don't fit neatly elsewhere."""

    @patch("xbot.platform.security.network.socket.getaddrinfo")
    def test_resolve_skips_unparseable_addrinfo(self, mock_gai):
        """If getaddrinfo returns entries with unparseable addresses, they are skipped."""
        # Normal entry + entry with garbage address string
        infos = [
            _make_addrinfo("93.184.216.34"),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("not-an-ip", 0)),
        ]
        mock_gai.return_value = infos
        ok, err = validate_url_target("https://example.com/")
        assert ok is True  # the public IP passes; the garbage is skipped

    @patch("xbot.platform.security.network.asyncio.get_running_loop")
    async def test_async_resolve_skips_unparseable_addrinfo(self, mock_loop):
        infos = [
            _make_addrinfo("93.184.216.34"),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("not-an-ip", 0)),
        ]
        mock_loop.return_value.getaddrinfo = AsyncMock(return_value=infos)
        ok, err = await async_validate_url_target("https://example.com/")
        assert ok is True

    def test_0_0_0_0_is_private(self):
        assert _is_private(_IPV4("0.0.0.0"))

    def test_0_0_0_0_via_resolved_url(self):
        ok, err = validate_resolved_url("http://0.0.0.0/")
        assert ok is False
        assert "private address" in err

    @patch("xbot.platform.security.network.socket.getaddrinfo")
    def test_cgnat_100_64_blocked(self, mock_gai):
        mock_gai.return_value = _mock_getaddrinfo("100.64.0.1")
        ok, err = validate_url_target("http://cgnat.host/")
        assert ok is False
        assert "Blocked" in err

    def test_contains_internal_url_with_quoted_url(self):
        """URL inside quotes should still be detected."""
        with patch("xbot.platform.security.network.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = _mock_getaddrinfo("127.0.0.1")
            assert contains_internal_url('fetch "http://localhost/secret"') is True

    def test_url_regex_does_not_match_partial(self):
        """String that looks URL-ish but doesn't start with http(s)://."""
        assert contains_internal_url("the host is 10.0.0.1 but no scheme") is False

    @patch("xbot.platform.security.network.socket.getaddrinfo")
    def test_url_with_port(self, mock_gai):
        mock_gai.return_value = _mock_getaddrinfo("93.184.216.34")
        ok, err = validate_url_target("https://example.com:8443/api")
        assert ok is True

    @patch("xbot.platform.security.network.socket.getaddrinfo")
    def test_url_with_auth(self, mock_gai):
        mock_gai.return_value = _mock_getaddrinfo("93.184.216.34")
        ok, err = validate_url_target("https://user:pass@example.com/api")
        assert ok is True

    @patch("xbot.platform.security.network.socket.getaddrinfo")
    def test_blocks_100_127_boundary(self, mock_gai):
        """100.127.255.255 is the last address in 100.64.0.0/10."""
        mock_gai.return_value = _mock_getaddrinfo("100.127.255.255")
        ok, err = validate_url_target("http://cgnat-edge.host/")
        assert ok is False

    @patch("xbot.platform.security.network.socket.getaddrinfo")
    def test_passes_100_128(self, mock_gai):
        """100.128.0.0 is just outside 100.64.0.0/10 — public."""
        mock_gai.return_value = _mock_getaddrinfo("100.128.0.0")
        ok, err = validate_url_target("http://just-outside.host/")
        assert ok is True
