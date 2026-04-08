"""Output management module for crew task results.

Provides:
- Format handling (raw, json, markdown, structured)
- Intelligent truncation with format awareness
- LLM-based repair for malformed output
- Persistent storage with retention management
"""

from xbot.crew.output.format import (
    OutputFormat,
    OutputParser,
    ParsedOutput,
    detect_format,
    format_output,
)
from xbot.crew.output.persist import (
    OutputPersister,
    RunManifest,
    TaskOutputRecord,
    create_persister,
)
from xbot.crew.output.repair import (
    OutputRepairer,
    RepairResult,
    repair_json,
    should_attempt_repair,
)
from xbot.crew.output.truncate import (
    OutputTruncator,
    TruncationResult,
    TruncationStrategy,
    truncate_output,
)

__all__ = [
    # Format
    "OutputFormat",
    "OutputParser",
    "ParsedOutput",
    "detect_format",
    "format_output",
    # Truncate
    "OutputTruncator",
    "TruncationResult",
    "TruncationStrategy",
    "truncate_output",
    # Repair
    "OutputRepairer",
    "RepairResult",
    "repair_json",
    "should_attempt_repair",
    # Persist
    "OutputPersister",
    "RunManifest",
    "TaskOutputRecord",
    "create_persister",
]
