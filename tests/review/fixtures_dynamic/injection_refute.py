"""refute: list form, no shell interpretation (false positive)."""
import subprocess


def run(x):
    # CLEAN: list form; the argument is never interpreted by a shell.
    subprocess.run(["echo", x])
