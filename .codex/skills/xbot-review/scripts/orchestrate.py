#!/usr/bin/env python3
"""xbot-review skill entry point. Delegates to scripts.review.orchestrate."""
import subprocess
import sys


def main():
    subprocess.run([sys.executable, "-m", "scripts.review.orchestrate", *sys.argv[1:]])


if __name__ == "__main__":
    main()
