"""Tests for Phase 4: secret pattern scanner and operations integration."""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

from xbot.memory.memdir.secrets import scan_for_secrets
from xbot.memory.memdir.store import MemoryDirStore
from xbot.memory.workers.operations import apply_memory_operations


def test_detect_aws_access_key() -> None:
    content = "config: AKIAIOSFODNN7EXAMPLE"
    assert "AWS Access Key" in scan_for_secrets(content)


def test_detect_ssh_private_key() -> None:
    content = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAK..."
    assert "SSH Private Key" in scan_for_secrets(content)


def test_detect_openssh_private_key() -> None:
    content = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC..."
    assert "SSH Private Key" in scan_for_secrets(content)


def test_detect_github_token() -> None:
    content = "token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmn"
    assert "GitHub Token" in scan_for_secrets(content)


def test_detect_slack_token() -> None:
    content = "SLACK_TOKEN=xoxb-1234567890-abcdefghij"
    assert "Slack Token" in scan_for_secrets(content)


def test_detect_jwt_token() -> None:
    content = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    assert "JWT Token" in scan_for_secrets(content)


def test_detect_generic_api_key() -> None:
    content = 'api_key = "sk_live_abcdefghijklmnopqrst"'
    assert "Generic API Key" in scan_for_secrets(content)


def test_detect_generic_password() -> None:
    content = "password=SuperSecret123!"
    assert "Generic Password" in scan_for_secrets(content)


def test_clean_content_returns_empty() -> None:
    content = "This is a normal memory about Python coding best practices."
    assert scan_for_secrets(content) == []


def test_multiple_secrets_detected() -> None:
    content = (
        "AWS key: AKIAIOSFODNN7EXAMPLE\n"
        "password=mysecretpass123\n"
    )
    detected = scan_for_secrets(content)
    assert "AWS Access Key" in detected
    assert "Generic Password" in detected


def test_operations_logs_warning_on_create_with_secrets(
    tmp_path: Path, caplog: logging.LogRecord
) -> None:
    """apply_memory_operations should log warning when secret detected in create."""
    store = MemoryDirStore(tmp_path)
    ops = [
        {
            "action": "create",
            "memory_type": "project",
            "title": "Config",
            "description": "Configuration notes",
            "content": "AWS key: AKIAIOSFODNN7EXAMPLE",
        }
    ]

    with caplog.at_level(logging.WARNING):
        apply_memory_operations(store, ops)

    assert any("secrets" in r.message.lower() for r in caplog.records)
    # File should still be created (warn-only policy)
    headers = store.scan_headers()
    assert len(headers) == 1


def test_operations_logs_warning_on_update_with_secrets(
    tmp_path: Path, caplog: logging.LogRecord
) -> None:
    """apply_memory_operations should log warning when secret detected in update."""
    store = MemoryDirStore(tmp_path)
    path = store.create_memory(
        memory_type="project",
        title="Config",
        description="Configuration notes",
        body="Clean content",
    )

    ops = [
        {
            "action": "update",
            "path": str(path),
            "content": "password=SuperSecret123!",
        }
    ]

    with caplog.at_level(logging.WARNING):
        apply_memory_operations(store, ops)

    assert any("secrets" in r.message.lower() for r in caplog.records)


def test_operations_no_warning_for_clean_content(
    tmp_path: Path, caplog: logging.LogRecord
) -> None:
    """No warning should be logged for clean content."""
    store = MemoryDirStore(tmp_path)
    ops = [
        {
            "action": "create",
            "memory_type": "project",
            "title": "Clean",
            "description": "Clean notes",
            "content": "This is clean content about Python coding.",
        }
    ]

    with caplog.at_level(logging.WARNING):
        apply_memory_operations(store, ops)

    assert not any("secrets" in r.message.lower() for r in caplog.records)
