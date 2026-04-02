from __future__ import annotations

import hashlib
import re

from .models import CandidateMemory


_SPACE_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    return _SPACE_RE.sub(" ", text.strip().lower())


def build_fingerprint(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def dedupe_candidates(candidates: list[CandidateMemory]) -> list[CandidateMemory]:
    unique: list[CandidateMemory] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.fingerprint in seen:
            continue
        seen.add(candidate.fingerprint)
        unique.append(candidate)
    return unique
