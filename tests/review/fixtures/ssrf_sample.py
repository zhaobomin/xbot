import httpx


def good():
    httpx.get("http://fixed-url.com")  # clean: no param in URL


def bad(user_url):
    httpx.get(f"http://api/{user_url}")  # anti: param interpolated into URL
