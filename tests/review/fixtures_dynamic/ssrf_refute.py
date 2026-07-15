"""refute: URL is validated before the request (false positive)."""
import httpx


def _is_safe(u: str) -> None:
    # Guard: reject link-local / cloud metadata endpoints before the request.
    if "169.254." in u or "metadata" in u:
        raise ValueError("intranet URL blocked")


def fetch(u):
    # CLEAN: validate first, then make the request.
    _is_safe(u)
    return httpx.get(u)
