from __future__ import annotations

import re

from xbot.memory.models import MAX_RELEVANT_MEMORIES, MemoryHeader

# Punctuation ranges to strip from CJK text before bigram extraction
_CJK_PUNCT = re.compile(r"[\u3000-\u303f\uff00-\uffef\u2000-\u206f\u00a0]")


def _extract_cjk_bigrams(text: str) -> set[str]:
    """Extract character bigrams from non-ASCII (CJK) sequences."""
    bigrams: set[str] = set()
    for seq in re.findall(r"[^\x00-\x7f]+", text):
        cleaned = _CJK_PUNCT.sub("", seq)
        for i in range(len(cleaned) - 1):
            bigrams.add(cleaned[i : i + 2])
    return bigrams


def select_relevant_memories(query: str, headers: list[MemoryHeader]) -> list[MemoryHeader]:
    if not query or not query.strip():
        return []
    # ASCII word tokens (min length 3)
    ascii_terms = {token.lower() for token in re.findall(r"[a-zA-Z0-9_-]+", query) if len(token) > 2}
    # CJK character bigrams for Chinese/Japanese/Korean matching
    cjk_terms = _extract_cjk_bigrams(query)
    # Require at least 2 ASCII terms or any CJK bigrams to avoid over-broad matching
    if not cjk_terms and len(ascii_terms) <= 1:
        return []
    if not ascii_terms and not cjk_terms:
        return []
    scored: list[tuple[int, MemoryHeader]] = []
    for header in headers:
        haystack = f"{header.name or ''} {header.filename} {header.description or ''}".lower()
        score = sum(1 for term in ascii_terms if term in haystack)
        score += sum(1 for bigram in cjk_terms if bigram in haystack)
        if score > 0:
            scored.append((score, header))
    scored.sort(key=lambda item: (item[0], item[1].mtime_ms), reverse=True)
    return [header for _, header in scored[:MAX_RELEVANT_MEMORIES]]
