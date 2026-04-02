from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import AppConfig
from .dedup import dedupe_candidates
from .extractors import CandidateExtractor
from .models import ApplySummary, CandidateMemory, ParsedDocument, ReviewPacket, ScannedFile
from .mem0_client import Mem0Client
from .parsers import parse_file
from .review_packet import build_packet_id, default_packet_path, parse_review_packet, render_review_packet, validate_review_decisions
from .scanner import scan_sources
from .state_store import StateStore


@dataclass
class RunSummary:
    scanned_files: int
    parsed_files: int
    extracted_candidates: int
    imported_candidates: int
    skipped_files: int
    mode: str


class IngestService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.state = StateStore(config.state.sqlite_path)
        self.extractor = CandidateExtractor(config.extract)
        self.client: Mem0Client | None = None

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
        self.state.close()

    def get_client(self) -> Mem0Client:
        if self.client is None:
            self.client = Mem0Client(self.config.mem0)
        return self.client

    def scan(self, *, since: timedelta | None = None, limit: int | None = None) -> list[ScannedFile]:
        return scan_sources(self.config.sources, since=since, limit=limit)

    def query(self, query: str, *, top_k: int = 5, enable_graph: bool = True):
        return self.get_client().search(query, top_k=top_k, enable_graph=enable_graph)

    def draft_packet(
        self,
        *,
        since: timedelta | None = None,
        limit: int | None = None,
        force: bool = False,
        output_path: str | None = None,
    ) -> tuple[ReviewPacket, Path]:
        scanned = self.scan(since=since, limit=limit)
        all_candidates: list[CandidateMemory] = []
        source_paths: list[str] = []
        for item in scanned:
            if not force and not self.state.should_process(item):
                continue
            parsed = parse_file(item)
            extracted = self.extractor.extract(parsed)
            candidates = dedupe_candidates(extracted.candidates)
            if not candidates:
                continue
            all_candidates.extend(candidates)
            source_paths.append(item.path)

        packet = ReviewPacket(
            packet_id=build_packet_id(source_paths or [self.config.mem0.user_id]),
            generated_at=datetime.now(timezone.utc),
            mem0_user_id=self.config.mem0.user_id,
            mem0_app_id=self.config.mem0.app_id,
            candidates=all_candidates,
        )
        target = Path(output_path).expanduser().resolve() if output_path else default_packet_path(packet)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_review_packet(packet), encoding="utf-8")
        return packet, target

    def review_packet(self, packet_path: str | Path) -> tuple[ReviewPacket, dict[str, int]]:
        packet, decisions = parse_review_packet(packet_path)
        return packet, validate_review_decisions(decisions)

    def apply_review_packet(self, packet_path: str | Path) -> ApplySummary:
        packet, decisions = parse_review_packet(packet_path)
        counts = validate_review_decisions(decisions)
        candidate_map = {candidate.fingerprint: candidate for candidate in packet.candidates}
        summary = ApplySummary(
            packet_id=packet.packet_id,
            approved=counts["approve"],
            rejected=counts["reject"],
            edited=counts["edit"],
        )
        for decision in decisions:
            if decision.status not in {"approve", "edit"}:
                continue
            original = candidate_map[decision.fingerprint]
            memory_text = (
                decision.edited_memory.strip()
                if decision.status == "edit" and decision.edited_memory
                else original.memory_text
            )
            candidate = CandidateMemory(
                memory_text=memory_text,
                memory_type=original.memory_type,
                enable_graph=original.enable_graph,
                confidence=original.confidence,
                why_it_matters=original.why_it_matters,
                tags=original.tags,
                source_path=original.source_path,
                source_title=original.source_title,
                source_chunk_id=original.source_chunk_id,
                fingerprint=original.fingerprint,
                metadata=original.metadata,
            )
            if self.state.has_fingerprint(candidate.fingerprint):
                summary.skipped_existing += 1
                continue
            remote_id = self.get_client().add_memory(candidate)
            self.state.record_import(candidate, remote_id)
            summary.imported += 1
        for candidate in packet.candidates:
            content_hash = candidate.metadata.get("content_hash", "")
            if not content_hash:
                continue
            self.state.mark_scanned(
                ScannedFile(
                    path=candidate.source_path,
                    doc_type=candidate.metadata.get("doc_type", ""),
                    modified_time=datetime.now(timezone.utc),
                    content_hash=content_hash,
                ),
                "reviewed",
            )
        return summary

    def extract_only(
        self, *, since: timedelta | None = None, limit: int | None = None, force: bool = False
    ) -> tuple[list[CandidateMemory], str]:
        scanned = self.scan(since=since, limit=limit)
        all_candidates: list[CandidateMemory] = []
        mode = "rules"
        for item in scanned:
            if not force and not self.state.should_process(item):
                continue
            parsed = parse_file(item)
            extracted = self.extractor.extract(parsed)
            mode = extracted.mode
            all_candidates.extend(extracted.candidates)
        return dedupe_candidates(all_candidates), mode

    def run(
        self,
        *,
        since: timedelta | None = None,
        limit: int | None = None,
        dry_run: bool = False,
        force: bool = False,
    ) -> RunSummary:
        scanned = self.scan(since=since, limit=limit)
        parsed_files = 0
        skipped_files = 0
        extracted_candidates = 0
        imported_candidates = 0
        mode = "rules"

        for item in scanned:
            if not force and not self.state.should_process(item):
                skipped_files += 1
                continue
            parsed: ParsedDocument = parse_file(item)
            parsed_files += 1
            extracted = self.extractor.extract(parsed)
            mode = extracted.mode
            candidates = dedupe_candidates(extracted.candidates)
            extracted_candidates += len(candidates)
            imported_this_file = 0
            for candidate in candidates:
                if self.state.has_fingerprint(candidate.fingerprint):
                    continue
                if not dry_run:
                    remote_id = self.get_client().add_memory(candidate)
                    self.state.record_import(candidate, remote_id)
                imported_candidates += 1
                imported_this_file += 1
            if not dry_run:
                self.state.mark_scanned(item, f"imported:{imported_this_file}")

        return RunSummary(
            scanned_files=len(scanned),
            parsed_files=parsed_files,
            extracted_candidates=extracted_candidates,
            imported_candidates=imported_candidates,
            skipped_files=skipped_files,
            mode=mode,
        )
