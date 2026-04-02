from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re

from .models import CandidateMemory, ReviewDecision, ReviewPacket


_SECTION_RE = re.compile(
    r"## Candidate \d+\n(?P<body>.*?)(?=\n## Candidate \d+\n|\Z)",
    re.DOTALL,
)


def default_packet_path(packet: ReviewPacket) -> Path:
    root = (Path.cwd() / ".memory-ingest" / "packets").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{packet.packet_id}.md"


def build_packet_id(source_paths: list[str]) -> str:
    digest = hashlib.sha256("\n".join(sorted(source_paths)).encode("utf-8")).hexdigest()[:10]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"packet-{timestamp}-{digest}"


def render_review_packet(packet: ReviewPacket) -> str:
    lines = [
        "# Memory Review Packet",
        "",
        f"Packet ID: {packet.packet_id}",
        f"Generated At: {packet.generated_at.astimezone(timezone.utc).isoformat()}",
        f"Mem0 User ID: {packet.mem0_user_id}",
        f"Mem0 App ID: {packet.mem0_app_id}",
        "",
    ]
    for index, candidate in enumerate(packet.candidates, start=1):
        tags = ", ".join(candidate.tags) if candidate.tags else "-"
        lines.extend(
            [
                f"## Candidate {index}",
                f"Fingerprint: `{candidate.fingerprint}`",
                f"Source Path: `{candidate.source_path}`",
                f"Source Chunk ID: `{candidate.source_chunk_id}`",
                f"Doc Type: `{candidate.metadata.get('doc_type', '-')}`",
                f"Content Hash: `{candidate.metadata.get('content_hash', '-')}`",
                f"Type: `{candidate.memory_type}`",
                f"Confidence: `{candidate.confidence:.2f}`",
                f"Tags: `{tags}`",
                "",
                "### Source Title",
                candidate.source_title,
                "",
                "### Proposed Memory",
                candidate.memory_text,
                "",
                "### Why It Matters",
                candidate.why_it_matters or "-",
                "",
                "### Review",
                "Status: pending",
                "Edited Memory:",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def parse_review_packet(path: str | Path) -> tuple[ReviewPacket, list[ReviewDecision]]:
    packet_path = Path(path).expanduser().resolve()
    text = packet_path.read_text(encoding="utf-8")
    packet = ReviewPacket(
        packet_id=_extract_header(text, "Packet ID"),
        generated_at=datetime.fromisoformat(_extract_header(text, "Generated At")),
        mem0_user_id=_extract_header(text, "Mem0 User ID"),
        mem0_app_id=_extract_header(text, "Mem0 App ID"),
        candidates=[],
    )
    decisions: list[ReviewDecision] = []
    for body in _SECTION_RE.findall(text):
        fingerprint = _extract_field(body, "Fingerprint").strip("`")
        source_path = _extract_field(body, "Source Path").strip("`")
        source_chunk_id = _extract_field(body, "Source Chunk ID").strip("`")
        doc_type = _extract_field(body, "Doc Type").strip("`")
        content_hash = _extract_field(body, "Content Hash").strip("`")
        memory_type = _extract_field(body, "Type").strip("`")
        confidence = float(_extract_field(body, "Confidence").strip("`"))
        tags_raw = _extract_field(body, "Tags").strip("`")
        tags = [] if tags_raw == "-" else [item.strip() for item in tags_raw.split(",") if item.strip()]
        source_title = _extract_section(body, "Source Title", "Proposed Memory").strip()
        proposed_memory = _extract_section(body, "Proposed Memory", "Why It Matters").strip()
        why_it_matters = _extract_section(body, "Why It Matters", "Review").strip()
        review_block = _extract_section(body, "Review", None).strip()
        status = _extract_inline_from_block(review_block, "Status").lower()
        edited_memory = _extract_edited_memory(review_block)
        packet.candidates.append(
            CandidateMemory(
                memory_text=proposed_memory,
                memory_type=memory_type,
                enable_graph=False,
                confidence=confidence,
                why_it_matters="" if why_it_matters == "-" else why_it_matters,
                tags=tags,
                source_path=source_path,
                source_title=source_title,
                source_chunk_id=source_chunk_id,
                fingerprint=fingerprint,
                metadata={
                    "doc_type": "" if doc_type == "-" else doc_type,
                    "content_hash": "" if content_hash == "-" else content_hash,
                },
            )
        )
        decisions.append(
            ReviewDecision(
                fingerprint=fingerprint,
                status=status,
                edited_memory=edited_memory,
            )
        )
    return packet, decisions


def validate_review_decisions(decisions: list[ReviewDecision]) -> dict[str, int]:
    counts = {"approve": 0, "reject": 0, "edit": 0, "pending": 0}
    for decision in decisions:
        if decision.status not in counts:
            raise ValueError(
                f"Unsupported review status '{decision.status}' for fingerprint {decision.fingerprint}"
            )
        if decision.status == "edit" and not (decision.edited_memory or "").strip():
            raise ValueError(f"Edited Memory is required for fingerprint {decision.fingerprint}")
        counts[decision.status] += 1
    return counts


def _extract_header(text: str, name: str) -> str:
    match = re.search(rf"^{re.escape(name)}:\s*(.+)$", text, re.MULTILINE)
    if not match:
        raise ValueError(f"Missing header {name}")
    return match.group(1).strip()


def _extract_field(text: str, name: str) -> str:
    match = re.search(rf"^{re.escape(name)}:\s*(.+)$", text, re.MULTILINE)
    if not match:
        raise ValueError(f"Missing field {name}")
    return match.group(1).strip()


def _extract_optional_field(text: str, name: str) -> str | None:
    match = re.search(rf"^{re.escape(name)}:\s*(.+)$", text, re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip()


def _extract_section(text: str, title: str, next_title: str | None) -> str:
    if next_title:
        pattern = rf"### {re.escape(title)}\n(?P<body>.*?)(?=\n### {re.escape(next_title)}\n)"
    else:
        pattern = rf"### {re.escape(title)}\n(?P<body>.*)$"
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        raise ValueError(f"Missing section {title}")
    return match.group("body").strip()


def _extract_inline_from_block(block: str, name: str) -> str:
    match = re.search(rf"^{re.escape(name)}:\s*(.*)$", block, re.MULTILINE)
    if not match:
        raise ValueError(f"Missing review field {name}")
    return match.group(1).strip()


def _extract_edited_memory(block: str) -> str | None:
    match = re.search(r"^Edited Memory:[ \t]*(.*)$", block, re.MULTILINE)
    if not match:
        raise ValueError("Missing review field Edited Memory")
    inline = match.group(1).strip()
    if inline:
        return inline
    tail = block[match.end() :].strip()
    return tail or None
