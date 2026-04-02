from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class DocumentSection(BaseModel):
    heading: str | None = None
    text: str


class ParsedDocument(BaseModel):
    source_path: str
    doc_type: str
    title: str
    sections: list[DocumentSection]
    modified_time: datetime
    content_hash: str


class CandidateMemory(BaseModel):
    memory_text: str
    memory_type: str
    enable_graph: bool = False
    confidence: float
    why_it_matters: str = ""
    tags: list[str] = Field(default_factory=list)
    source_path: str
    source_title: str
    source_chunk_id: str
    fingerprint: str
    metadata: dict[str, str] = Field(default_factory=dict)


class ScannedFile(BaseModel):
    path: str
    doc_type: str
    modified_time: datetime
    content_hash: str


class MemoryRelation(BaseModel):
    source: str
    source_type: str | None = None
    relationship: str
    target: str
    target_type: str | None = None
    score: float | None = None


class MemorySearchResult(BaseModel):
    id: str | None = None
    memory: str
    score: float | None = None
    categories: list[str] = Field(default_factory=list)
    metadata: dict | None = None
    raw: dict = Field(default_factory=dict)


class MemoryQueryResponse(BaseModel):
    query: str
    user_id: str
    app_id: str
    enable_graph: bool = True
    results: list[MemorySearchResult] = Field(default_factory=list)
    relations: list[MemoryRelation] = Field(default_factory=list)
    raw: dict = Field(default_factory=dict)


class ReviewDecision(BaseModel):
    fingerprint: str
    status: str
    edited_memory: str | None = None


class ReviewPacket(BaseModel):
    packet_id: str
    generated_at: datetime
    mem0_user_id: str
    mem0_app_id: str
    candidates: list[CandidateMemory] = Field(default_factory=list)


class ApplySummary(BaseModel):
    packet_id: str
    approved: int = 0
    rejected: int = 0
    edited: int = 0
    imported: int = 0
    skipped_existing: int = 0
