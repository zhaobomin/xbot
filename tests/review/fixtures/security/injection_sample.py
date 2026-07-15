import os  # noqa: F401  # keeps line numbers stable

import subprocess


def good(user_input):
    subprocess.run(["echo", user_input])  # clean: list form, no shell


def bad(user_input):
    subprocess.run(f"echo {user_input}")  # anti: f-string passed to shell
