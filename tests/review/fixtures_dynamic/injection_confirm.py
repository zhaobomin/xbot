"""confirm: string form passed to subprocess (real bug pattern)."""
import subprocess


def run(x):
    # BUG: shell=True with unescaped f-string interpolation -> command injection.
    # Safe payload (echo), but the metacharacter is executed by the shell.
    return subprocess.run(f"echo {x}", shell=True, capture_output=True, text=True)
