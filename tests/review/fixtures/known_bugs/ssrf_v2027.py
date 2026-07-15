import httpx


def search_searxng(base_url, query):
    # Pre-SSRF-guard: config-controlled base_url interpolated into fetch
    # URL with no resolved-IP validation.
    r = httpx.get(f"http://{base_url}/search", params={"q": query})
    return r.json()
