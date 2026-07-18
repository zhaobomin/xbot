import os  # noqa: F401  # keeps line numbers stable

import httpx


def good():
    httpx.get("http://allowlisted.internal/api")  # clean: fixed allowlisted URL


def bad(user_url):
    httpx.get(user_url)  # anti: user-controlled param used as request URL
