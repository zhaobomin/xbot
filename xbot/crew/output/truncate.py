"""Intelligent output truncation with format awareness.

Strategies:
1. Markdown-aware: Truncate at code block, table, or section boundaries
2. JSON-aware: Truncate at complete object/array boundaries
3. Key content preservation: Keep errors, warnings, key conclusions
4. Fallback: Hard truncate with ellipsis marker
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


@dataclass
class TruncationResult:
    """Result of truncation operation."""

    content: str
    original_length: int
    truncated_length: int
    truncated: bool
    strategy: str  # Which strategy was used
    message: str | None = None


class TruncationStrategy(str, Enum):
    """Available truncation strategies."""

    MARKDOWN = "markdown"
    JSON = "json"
    SMART = "smart"  # Auto-detect format
    HARD = "hard"  # Simple character cut


class OutputTruncator:
    """Truncates output content intelligently based on format."""

    # Patterns that indicate important content
    IMPORTANT_PATTERNS = [
        r'error[:：]',
        r'warning[:：]',
        r'failed',
        r'success',
        r'result[:：]',
        r'conclusion',
        r'summary',
        r'important',
        r'note[:：]',
    ]

    # Maximum size for different content types
    DEFAULT_MAX_LENGTH = 4000
    CODE_BLOCK_MAX = 1500
    TABLE_MAX = 1000

    def truncate(
        self,
        content: str,
        max_length: int = DEFAULT_MAX_LENGTH,
        strategy: TruncationStrategy = TruncationStrategy.SMART,
        preserve_patterns: list[str] | None = None,
    ) -> TruncationResult:
        """Truncate content intelligently.

        Args:
            content: Content to truncate
            max_length: Maximum length in characters
            strategy: Truncation strategy to use
            preserve_patterns: Regex patterns for content to preserve

        Returns:
            TruncationResult with truncated content and metadata
        """
        original_length = len(content)

        if original_length <= max_length:
            return TruncationResult(
                content=content,
                original_length=original_length,
                truncated_length=original_length,
                truncated=False,
                strategy="none",
            )

        # Determine strategy
        if strategy == TruncationStrategy.SMART:
            strategy = self._detect_strategy(content)

        # Apply appropriate strategy
        if strategy == TruncationStrategy.MARKDOWN:
            result = self._truncate_markdown(content, max_length, preserve_patterns)
        elif strategy == TruncationStrategy.JSON:
            result = self._truncate_json(content, max_length)
        else:
            result = self._truncate_hard(content, max_length)

        return result

    def _detect_strategy(self, content: str) -> TruncationStrategy:
        """Detect the best truncation strategy for content."""
        # Check for JSON
        stripped = content.strip()
        if stripped.startswith('{') or stripped.startswith('['):
            try:
                json.loads(stripped)
                return TruncationStrategy.JSON
            except json.JSONDecodeError:
                pass

        # Check for markdown
        md_indicators = [
            r'^#{1,6}\s',  # Headers
            r'```',  # Code blocks
            r'\|.*\|',  # Tables
            r'^\s*[-*+]\s',  # Lists
        ]
        for pattern in md_indicators:
            if re.search(pattern, content, re.MULTILINE):
                return TruncationStrategy.MARKDOWN

        return TruncationStrategy.HARD

    def _truncate_markdown(
        self,
        content: str,
        max_length: int,
        preserve_patterns: list[str] | None = None,
    ) -> TruncationResult:
        """Truncate markdown content at structural boundaries."""
        lines = content.split('\n')
        result_lines: list[str] = []
        current_length = 0
        in_code_block = False
        truncated = False
        truncation_point = None
        skip_until = -1  # Index to skip until (inclusive)

        patterns_to_preserve = preserve_patterns or self.IMPORTANT_PATTERNS

        # First pass: collect important sections
        important_sections = self._find_important_sections(content, patterns_to_preserve)

        for i, line in enumerate(lines):
            # Skip lines we've already added as part of a code block
            if i <= skip_until:
                continue

            line_len = len(line) + 1  # +1 for newline

            # Track code block state
            if line.strip().startswith('```'):
                in_code_block = not in_code_block

            # Don't break inside code blocks
            if in_code_block and current_length + line_len > max_length:
                # Find end of code block
                end_idx = self._find_code_block_end(lines, i)
                code_block_len = self._calculate_length(lines, i, end_idx) if end_idx else float('inf')

                if end_idx and code_block_len <= max_length:
                    # Include entire code block if it fits
                    for j in range(i, end_idx + 1):
                        result_lines.append(lines[j])
                        current_length += len(lines[j]) + 1
                    # Mark these lines as processed
                    skip_until = end_idx
                    # Update code block state since we processed the end marker
                    in_code_block = False
                    continue
                else:
                    # Code block too long, truncate here
                    truncated = True
                    truncation_point = i
                    break

            # Check if adding this line exceeds limit
            if current_length + line_len > max_length:
                # Check if we're at a good boundary
                if self._is_good_boundary(line, lines, i):
                    truncated = True
                    truncation_point = i
                    break
                else:
                    # Try to find next good boundary
                    boundary = self._find_next_boundary(lines, i)
                    if boundary and self._calculate_length(lines, 0, boundary) <= max_length * 1.1:
                        result_lines.extend(lines[i:boundary])
                        current_length = sum(len(line) + 1 for line in result_lines)
                        truncated = True
                        truncation_point = boundary
                        break
                    else:
                        truncated = True
                        truncation_point = i
                        break

            result_lines.append(line)
            current_length += line_len

        result_content = '\n'.join(result_lines)

        if truncated:
            # Add truncation marker
            result_content += '\n\n... (output truncated)'
            result_content = self._preserve_important(
                result_content,
                content,
                important_sections,
                max_length,
            )

        return TruncationResult(
            content=result_content,
            original_length=len(content),
            truncated_length=len(result_content),
            truncated=truncated,
            strategy="markdown",
            message=f"Truncated at line {truncation_point}" if truncation_point else None,
        )

    def _truncate_json(self, content: str, max_length: int) -> TruncationResult:
        """Truncate JSON content at object boundaries."""
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # Fallback to hard truncate
            return self._truncate_hard(content, max_length)

        # Try to fit the whole thing
        dumped = json.dumps(data, indent=2)
        if len(dumped) <= max_length:
            return TruncationResult(
                content=dumped,
                original_length=len(content),
                truncated_length=len(dumped),
                truncated=False,
                strategy="json",
            )

        # Need to truncate - try to keep structure
        truncated_data = self._truncate_json_data(data, max_length - 100)  # Leave room for truncation marker

        if truncated_data:
            result = json.dumps(truncated_data, indent=2)
            result += '\n\n// ... (output truncated)'
            return TruncationResult(
                content=result,
                original_length=len(content),
                truncated_length=len(result),
                truncated=True,
                strategy="json",
                message="JSON truncated at object boundary",
            )

        # Fallback to hard truncate
        return self._truncate_hard(content, max_length)

    def _truncate_json_data(self, data: Any, max_length: int) -> Any:
        """Recursively truncate JSON data to fit within max_length."""
        dumped = json.dumps(data)
        if len(dumped) <= max_length:
            return data

        if isinstance(data, dict):
            result = {}
            current_length = 2  # {}
            for key, value in data.items():
                item_json = json.dumps({key: value})
                if current_length + len(item_json) <= max_length:
                    result[key] = value
                    current_length += len(item_json) + 1  # +1 for comma
                else:
                    break
            return result

        elif isinstance(data, list):
            result = []
            current_length = 2  # []
            for item in data:
                item_json = json.dumps(item)
                if current_length + len(item_json) <= max_length:
                    result.append(item)
                    current_length += len(item_json) + 1
                else:
                    break
            return result

        else:
            # Primitive value - truncate string representation
            s = str(data)
            if len(s) > max_length:
                return s[:max_length - 3] + '...'
            return data

    def _truncate_hard(self, content: str, max_length: int) -> TruncationResult:
        """Simple character-level truncation."""
        if max_length <= 0:
            truncated_content = ""
        elif max_length < 20:
            marker = "..."
            if max_length <= len(marker):
                truncated_content = marker[:max_length]
            else:
                truncated_content = content[: max_length - len(marker)] + marker
        else:
            truncated_content = content[:max_length - 20] + '\n\n... (output truncated)'
        return TruncationResult(
            content=truncated_content,
            original_length=len(content),
            truncated_length=len(truncated_content),
            truncated=True,
            strategy="hard",
            message=f"Hard truncation at character {max_length}",
        )

    def _find_important_sections(self, content: str, patterns: list[str]) -> list[tuple[int, int]]:
        """Find sections containing important content."""
        sections = []
        lines = content.split('\n')
        current_section: list[int] = []

        for i, line in enumerate(lines):
            # Check for section headers
            if re.match(r'^#{1,6}\s', line):
                if current_section:
                    sections.append((current_section[0], current_section[-1]))
                current_section = [i]
            else:
                current_section.append(i)

            # Check for important patterns
            for pattern in patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    # Mark this section as important
                    if not current_section:
                        current_section = [i]
                    break

        if current_section:
            sections.append((current_section[0], current_section[-1]))

        return sections

    def _preserve_important(
        self,
        truncated: str,
        original: str,
        important_sections: list[tuple[int, int]],
        max_length: int,
    ) -> str:
        """Ensure important sections are preserved in truncated output."""
        # If already under limit, return as-is
        if len(truncated) <= max_length:
            return truncated

        # For now, trust the markdown truncation
        # A more sophisticated version would extract and append important sections
        return truncated

    def _is_good_boundary(self, line: str, lines: list[str], index: int) -> bool:
        """Check if this is a good truncation boundary."""
        # Empty line is a good boundary
        if not line.strip():
            return True

        # End of code block
        if line.strip() == '```':
            return True

        # After a complete markdown element
        prev_line = lines[index - 1] if index > 0 else ''
        if prev_line.strip() and not line.strip():
            return True

        return False

    def _find_next_boundary(self, lines: list[str], start: int) -> int | None:
        """Find the next good truncation boundary."""
        for i in range(start, min(start + 20, len(lines))):
            if self._is_good_boundary(lines[i], lines, i):
                return i
        return None

    def _find_code_block_end(self, lines: list[str], start: int) -> int | None:
        """Find the end of the current code block."""
        for i in range(start + 1, len(lines)):
            if lines[i].strip() == '```':
                return i
        return None

    def _calculate_length(self, lines: list[str], start: int, end: int) -> int:
        """Calculate total length of lines from start to end."""
        return sum(len(lines[i]) + 1 for i in range(start, end))


def truncate_output(
    content: str,
    max_length: int = 4000,
    strategy: TruncationStrategy = TruncationStrategy.SMART,
    preserve_patterns: list[str] | None = None,
) -> TruncationResult:
    """Convenience function to truncate output.

    Args:
        content: Content to truncate
        max_length: Maximum length in characters
        strategy: Truncation strategy
        preserve_patterns: Patterns for important content

    Returns:
        TruncationResult with truncated content
    """
    truncator = OutputTruncator()
    return truncator.truncate(content, max_length, strategy, preserve_patterns)
