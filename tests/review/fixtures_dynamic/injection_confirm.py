"""confirm: string form passed to subprocess (real bug pattern)."""
import subprocess


def run(x):
    # BUG: f-string form; with shell=True this would execute metacharacters.
    # Kept without shell=True here so the fixture is safe to import/define.
    subprocess.run(f"echo {x}")
