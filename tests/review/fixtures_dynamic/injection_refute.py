"""refute: list form, no shell interpretation (false positive)."""
import subprocess


def run(x):
    # CLEAN: list form; the argument is never interpreted by a shell.
    return subprocess.run(["echo", x], capture_output=True, text=True)
