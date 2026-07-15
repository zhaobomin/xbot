"""confirm: user-controlled URL passed straight to httpx, no guard (real bug)."""
import httpx


def fetch(u):
    # BUG: no validation; the raw URL reaches the network client.
    return httpx.get(u)
