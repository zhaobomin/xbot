"""Tests for structured logging, CorrelationFilter, and StructuredFormatter."""

import importlib
import io
import json
import logging


def _reload_logging_module():
    import xbot.logging as xbot_logging

    return importlib.reload(xbot_logging)


# ---------------------------------------------------------------------------
# CorrelationFilter
# ---------------------------------------------------------------------------


def test_correlation_filter_injects_context_vars() -> None:
    """CorrelationFilter should populate LogRecord from ContextVar."""
    xbot_logging = _reload_logging_module()
    xbot_logging.correlation_id_var.set("test-cid")
    xbot_logging.session_key_var.set("cli:direct")

    record = logging.LogRecord(
        name="xbot.test", level=logging.INFO, pathname="", lineno=0,
        msg="hello", args=None, exc_info=None,
    )
    f = xbot_logging.CorrelationFilter()
    assert f.filter(record) is True
    assert record.correlation_id == "test-cid"  # type: ignore[attr-defined]
    assert record.session_key == "cli:direct"  # type: ignore[attr-defined]

    # Cleanup
    xbot_logging.correlation_id_var.set("")
    xbot_logging.session_key_var.set("")


def test_correlation_filter_defaults_to_empty() -> None:
    """CorrelationFilter should set empty strings when ContextVar is unset."""
    xbot_logging = _reload_logging_module()
    xbot_logging.correlation_id_var.set("")
    xbot_logging.session_key_var.set("")

    record = logging.LogRecord(
        name="xbot.test", level=logging.INFO, pathname="", lineno=0,
        msg="hello", args=None, exc_info=None,
    )
    f = xbot_logging.CorrelationFilter()
    f.filter(record)
    assert record.correlation_id == ""  # type: ignore[attr-defined]
    assert record.session_key == ""  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# StructuredFormatter
# ---------------------------------------------------------------------------


def test_structured_formatter_outputs_valid_json() -> None:
    """StructuredFormatter should produce valid single-line JSON."""
    xbot_logging = _reload_logging_module()
    fmt = xbot_logging.StructuredFormatter(datefmt="%Y-%m-%d %H:%M:%S")

    record = logging.LogRecord(
        name="xbot.test", level=logging.INFO, pathname="", lineno=0,
        msg="hello world", args=None, exc_info=None,
    )
    # Simulate CorrelationFilter
    record.correlation_id = "abc123"  # type: ignore[attr-defined]
    record.session_key = "telegram:42"  # type: ignore[attr-defined]

    output = fmt.format(record)
    parsed = json.loads(output)

    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "xbot.test"
    assert parsed["msg"] == "hello world"
    assert parsed["correlation_id"] == "abc123"
    assert parsed["session_key"] == "telegram:42"
    assert "ts" in parsed


def test_structured_formatter_omits_empty_correlation() -> None:
    """Empty correlation_id and session_key should be omitted from JSON."""
    xbot_logging = _reload_logging_module()
    fmt = xbot_logging.StructuredFormatter(datefmt="%Y-%m-%d %H:%M:%S")

    record = logging.LogRecord(
        name="xbot.test", level=logging.INFO, pathname="", lineno=0,
        msg="no context", args=None, exc_info=None,
    )
    record.correlation_id = ""  # type: ignore[attr-defined]
    record.session_key = ""  # type: ignore[attr-defined]

    parsed = json.loads(fmt.format(record))
    assert "correlation_id" not in parsed
    assert "session_key" not in parsed


def test_structured_formatter_includes_exception() -> None:
    """StructuredFormatter should include exception info when present."""
    xbot_logging = _reload_logging_module()
    fmt = xbot_logging.StructuredFormatter(datefmt="%Y-%m-%d %H:%M:%S")

    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="xbot.test", level=logging.ERROR, pathname="", lineno=0,
        msg="failure", args=None, exc_info=exc_info,
    )
    record.correlation_id = ""  # type: ignore[attr-defined]
    record.session_key = ""  # type: ignore[attr-defined]

    parsed = json.loads(fmt.format(record))
    assert "exception" in parsed
    assert "ValueError" in parsed["exception"]
    assert "boom" in parsed["exception"]


# ---------------------------------------------------------------------------
# configure_logging integration
# ---------------------------------------------------------------------------


def test_configure_logging_structured_json_output() -> None:
    """configure_logging(structured=True) should emit JSON lines."""
    xbot_logging = _reload_logging_module()
    stream = io.StringIO()
    xbot_logging.configure_logging(level=logging.INFO, stream=stream, structured=True)

    xbot_logging.correlation_id_var.set("int-test")
    xbot_logging.session_key_var.set("test:session")

    xbot_logging.get_logger("xbot.tests.json").info("json test message")

    output = stream.getvalue().strip()
    parsed = json.loads(output)
    assert parsed["msg"] == "json test message"
    assert parsed["correlation_id"] == "int-test"
    assert parsed["session_key"] == "test:session"

    xbot_logging.correlation_id_var.set("")
    xbot_logging.session_key_var.set("")


def test_configure_logging_text_includes_correlation_id() -> None:
    """Default text mode should include correlation_id in output."""
    xbot_logging = _reload_logging_module()
    stream = io.StringIO()
    xbot_logging.configure_logging(level=logging.INFO, stream=stream, structured=False)

    xbot_logging.correlation_id_var.set("txt-cid")

    xbot_logging.get_logger("xbot.tests.text").info("text test")

    output = stream.getvalue()
    assert "[txt-cid]" in output
    assert "text test" in output

    xbot_logging.correlation_id_var.set("")


def test_configure_logging_env_var_detection(monkeypatch) -> None:
    """XBOT_LOG_FORMAT=json should activate structured logging."""
    monkeypatch.setenv("XBOT_LOG_FORMAT", "json")
    xbot_logging = _reload_logging_module()
    stream = io.StringIO()
    # structured=None (default) should auto-detect from env
    xbot_logging.configure_logging(level=logging.INFO, stream=stream)

    xbot_logging.get_logger("xbot.tests.env").info("env detection")

    output = stream.getvalue().strip()
    parsed = json.loads(output)
    assert parsed["msg"] == "env detection"


def test_configure_logging_text_mode_without_correlation() -> None:
    """Text mode should handle empty correlation_id gracefully."""
    xbot_logging = _reload_logging_module()
    stream = io.StringIO()
    xbot_logging.correlation_id_var.set("")
    xbot_logging.configure_logging(level=logging.INFO, stream=stream, structured=False)

    xbot_logging.get_logger("xbot.tests.nocid").info("no cid")

    output = stream.getvalue()
    assert "no cid" in output
    # Empty cid shows as []
    assert "[]" in output
