"""LLM-based output repair for malformed content.

When output doesn't match expected format (e.g., invalid JSON),
use LLM to repair/extract the intended structure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from xbot.agent.crew.output.format import OutputFormat, ParsedOutput


@dataclass
class RepairResult:
    """Result of repair attempt."""

    success: bool
    repaired_content: str
    parsed: ParsedOutput | None = None
    error: str | None = None
    attempts: int = 0


class OutputRepairer:
    """Repairs malformed output using LLM assistance."""

    # Maximum repair attempts
    MAX_ATTEMPTS = 2

    def __init__(
        self,
        llm_call: Callable[[str], str] | None = None,
    ):
        """Initialize the repairer.

        Args:
            llm_call: Function to call LLM with a prompt, returns response.
                      If None, repair will fail gracefully.
        """
        self.llm_call = llm_call

    def repair(
        self,
        content: str,
        target_format: OutputFormat,
        schema: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> RepairResult:
        """Attempt to repair malformed output.

        Args:
            content: The malformed output content
            target_format: Expected output format
            schema: JSON schema for validation (if JSON format)
            error_message: Original error message from parsing

        Returns:
            RepairResult with repaired content if successful
        """
        if not self.llm_call:
            return RepairResult(
                success=False,
                repaired_content=content,
                error="No LLM call function provided for repair",
            )

        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            prompt = self._build_repair_prompt(
                content, target_format, schema, error_message, attempt
            )

            try:
                repaired = self.llm_call(prompt)
                parsed = self._validate_repair(repaired, target_format, schema)

                if parsed.valid:
                    return RepairResult(
                        success=True,
                        repaired_content=repaired,
                        parsed=parsed,
                        attempts=attempt,
                    )

                error_message = parsed.error

            except Exception as e:
                error_message = str(e)

        return RepairResult(
            success=False,
            repaired_content=content,
            error=f"Failed to repair after {self.MAX_ATTEMPTS} attempts: {error_message}",
            attempts=self.MAX_ATTEMPTS,
        )

    def _build_repair_prompt(
        self,
        content: str,
        target_format: OutputFormat,
        schema: dict | None,
        error_message: str | None,
        attempt: int,
    ) -> str:
        """Build LLM prompt for repair."""
        if target_format == OutputFormat.JSON:
            return self._build_json_repair_prompt(content, schema, error_message, attempt)
        elif target_format == OutputFormat.MARKDOWN:
            return self._build_markdown_repair_prompt(content, error_message, attempt)
        else:
            return self._build_generic_repair_prompt(content, target_format, error_message)

    def _build_json_repair_prompt(
        self,
        content: str,
        schema: dict | None,
        error_message: str | None,
        attempt: int,
    ) -> str:
        """Build prompt for JSON repair."""
        schema_desc = ""
        if schema:
            import json
            schema_desc = f"\n\nExpected JSON Schema:\n```json\n{json.dumps(schema, indent=2)}\n```"

        error_desc = f"\n\nError encountered: {error_message}" if error_message else ""

        return f"""Your task is to repair or extract valid JSON from the following output.

The output was supposed to be valid JSON but parsing failed.{error_desc}{schema_desc}

Original output:
```
{content}
```

Requirements:
1. Output ONLY valid JSON that matches the expected structure
2. Preserve all important information from the original output
3. If you cannot determine a value, use null
4. Do not include any explanation or markdown formatting
5. Output the JSON directly

Repaired JSON:"""

    def _build_markdown_repair_prompt(
        self,
        content: str,
        error_message: str | None,
        attempt: int,
    ) -> str:
        """Build prompt for Markdown repair."""
        return f"""Your task is to repair the following Markdown content.

The content has structural issues that need to be fixed.

Original content:
```
{content}
```

Requirements:
1. Fix any broken markdown syntax (unclosed code blocks, malformed tables, etc.)
2. Ensure all headers are properly formatted
3. Keep all original content
4. Output only the repaired markdown

Repaired Markdown:"""

    def _build_generic_repair_prompt(
        self,
        content: str,
        target_format: OutputFormat,
        error_message: str | None,
    ) -> str:
        """Build generic repair prompt."""
        return f"""The following content needs to be formatted as {target_format.value}.

Original content:
```
{content}
```

Error: {error_message}

Please reformat the content correctly. Output only the corrected content:"""

    def _validate_repair(
        self,
        repaired: str,
        target_format: OutputFormat,
        schema: dict | None,
    ) -> ParsedOutput:
        """Validate the repaired content."""
        from xbot.agent.crew.output.format import OutputParser

        parser = OutputParser()
        return parser.parse(repaired, target_format, schema)


def repair_json(
    content: str,
    schema: dict[str, Any] | None = None,
    llm_call: Callable[[str], str] | None = None,
) -> RepairResult:
    """Convenience function to repair JSON output.

    Args:
        content: Malformed JSON content
        schema: Optional JSON schema for validation
        llm_call: Function to call LLM

    Returns:
        RepairResult with repaired JSON if successful
    """
    repairer = OutputRepairer(llm_call=llm_call)
    return repairer.repair(content, OutputFormat.JSON, schema)


def should_attempt_repair(parsed: ParsedOutput) -> bool:
    """Determine if repair should be attempted.

    Args:
        parsed: The parsed output result

    Returns:
        True if repair should be attempted
    """
    # Don't repair if already valid
    if parsed.valid:
        return False

    # Don't repair raw format
    if parsed.format == OutputFormat.RAW:
        return False

    # Attempt repair for JSON and structured formats
    return parsed.format in (OutputFormat.JSON, OutputFormat.STRUCTURED)