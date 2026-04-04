from __future__ import annotations


def extract_memory_permissions(memory_dir: str) -> dict[str, list[str] | str]:
    return {
        "tools": ["Read", "Grep", "Glob", "Bash", "Edit", "Write"],
        "bash_mode": "read_only",
        "write_scope": [memory_dir],
    }


def auto_dream_permissions(memory_dir: str, transcript_dir: str) -> dict[str, list[str] | str]:
    return {
        "tools": ["Read", "Grep", "Glob", "Bash", "Edit", "Write"],
        "bash_mode": "read_only",
        "write_scope": [memory_dir],
        "transcript_scope": [transcript_dir],
    }
