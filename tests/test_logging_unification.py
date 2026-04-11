import importlib
import io
import logging
from pathlib import Path


def _reload_logging_module():
    import xbot.platform.logging.core as xbot_logging

    return importlib.reload(xbot_logging)


def test_get_logger_returns_stdlib_logger() -> None:
    xbot_logging = _reload_logging_module()
    logger = xbot_logging.get_logger("xbot.tests.stdlib")
    assert isinstance(logger, logging.Logger)


def test_configure_logging_routes_stdlib_messages_into_caplog(caplog) -> None:
    xbot_logging = _reload_logging_module()
    xbot_logging.configure_logging(level=logging.INFO)

    caplog.set_level(logging.INFO)
    xbot_logging.get_logger("xbot.tests.stdlib").info("stdlib hello")

    messages = [record.getMessage() for record in caplog.records]
    assert messages.count("stdlib hello") == 1


def test_configure_logging_preserves_traceback_from_stdlib_exception(caplog) -> None:
    xbot_logging = _reload_logging_module()
    xbot_logging.configure_logging(level=logging.ERROR)

    caplog.set_level(logging.ERROR)
    logger = xbot_logging.get_logger("xbot.tests.exceptions")
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("stdlib exploded")

    records = [record for record in caplog.records if record.getMessage() == "stdlib exploded"]
    assert len(records) == 1
    assert records[0].exc_info is not None
    assert records[0].exc_info[0] is ValueError


def test_configure_logging_uses_single_output_chain() -> None:
    xbot_logging = _reload_logging_module()
    stream = io.StringIO()
    xbot_logging.configure_logging(level=logging.INFO, stream=stream)

    xbot_logging.get_logger("xbot.tests.output").info("stdlib once")

    output = stream.getvalue()
    assert output.count("stdlib once") == 1


def test_configure_logging_does_not_mutate_root_logger_level() -> None:
    xbot_logging = _reload_logging_module()
    root = logging.getLogger()
    original_level = root.level
    root.setLevel(logging.WARNING)
    try:
        xbot_logging.configure_logging(level=logging.DEBUG)
        assert root.level == logging.WARNING
    finally:
        root.setLevel(original_level)


def test_business_modules_use_xbot_get_logger_entrypoint() -> None:
    root = Path(__file__).resolve().parents[1] / "xbot"
    offenders: list[str] = []

    for path in root.rglob("*.py"):
        if path.name == "logging.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "logging.getLogger(__name__)" in text:
            offenders.append(str(path.relative_to(root.parent)))

    assert offenders == []
