import os  # noqa: F401  # keeps line numbers stable


def good():
    API_KEY = os.environ["KEY"]  # clean: read from environment
    return API_KEY


def bad():
    API_KEY = "sk-abc123def456"  # anti: hardcoded secret literal
    password = "supersecretpass"  # anti: hardcoded password
    return API_KEY, password
