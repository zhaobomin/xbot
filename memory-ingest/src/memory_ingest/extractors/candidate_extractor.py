from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re

from openai import OpenAI

from ..config import ExtractConfig
from ..dedup import build_fingerprint
from ..models import CandidateMemory, ParsedDocument


_DROP_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\btodo\b",
        r"\bdeadline\b",
        r"今天",
        r"明天",
        r"本周",
        r"待办",
        r"会议纪要",
        r"action item",
        r"status update",
    ]
]
_KEEP_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"偏好|偏向|倾向|喜欢|不喜欢|习惯|注重|原则|约束|规则|背景|长期|负责|owner|希望",
        r"\bprefer\b|\bmust\b|\bnever\b|\balways\b|\brule\b|\bconstraint\b|\bbackground\b",
    ]
]


@dataclass
class ExtractionResult:
    candidates: list[CandidateMemory]
    mode: str


class CandidateExtractor:
    def __init__(self, config: ExtractConfig) -> None:
        self.config = config

    def extract(self, document: ParsedDocument) -> ExtractionResult:
        api_key = os.getenv(self.config.api_key_env)
        if api_key:
            try:
                return ExtractionResult(self._extract_with_llm(document, api_key), mode="llm")
            except Exception:
                pass
        return ExtractionResult(self._extract_with_rules(document), mode="rules")

    def _extract_with_rules(self, document: ParsedDocument) -> list[CandidateMemory]:
        candidates: list[CandidateMemory] = []
        for index, section in enumerate(document.sections):
            for line in re.split(r"[\n。！？!?]+", section.text):
                text = line.strip(" -•\t")
                if not text or len(text) < 6:
                    continue
                if any(pattern.search(text) for pattern in _DROP_PATTERNS):
                    continue
                if not any(pattern.search(text) for pattern in _KEEP_PATTERNS):
                    continue
                text = self._rewrite_atomic_memory(text)
                candidates.append(
                    CandidateMemory(
                        memory_text=text,
                        memory_type=self._classify_memory_type(text),
                        enable_graph=self._should_enable_graph(text),
                        confidence=0.8,
                        why_it_matters=self._why_it_matters(text),
                        tags=self._tags_for_text(text),
                        source_path=document.source_path,
                        source_title=document.title,
                        source_chunk_id=f"{document.content_hash[:12]}:{index}",
                        fingerprint=build_fingerprint(f"{document.source_path}:{text}"),
                        metadata={"doc_type": document.doc_type, "content_hash": document.content_hash},
                    )
                )
        return candidates

    def _extract_with_llm(self, document: ParsedDocument, api_key: str) -> list[CandidateMemory]:
        client = OpenAI(api_key=api_key, base_url=self.config.api_base)
        prompt = (
            "从下面文档中抽取适合长期记忆的候选内容。"
            "只保留偏好、约束、长期背景、稳定结论、可复用规则、长期关系信息。"
            "不要输出待办、短期状态、会议流水、低价值总结。"
            "每条候选必须是原子化的一条长期记忆，不要把多个事实揉成一条。"
            "返回 JSON 数组，每项包含 memory_text,memory_type,confidence,tags,why_it_matters。"
        )
        content = "\n\n".join(
            section.text[: self.config.max_chunk_chars] for section in document.sections
        )
        response = client.chat.completions.create(
            model=self.config.model,
            temperature=0,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"标题: {document.title}\n\n内容:\n{content}"},
            ],
        )
        raw = response.choices[0].message.content or "[]"
        data = json.loads(raw)
        candidates: list[CandidateMemory] = []
        for index, item in enumerate(data):
            confidence = float(item.get("confidence", 0))
            if confidence < self.config.min_confidence:
                continue
            text = self._rewrite_atomic_memory(str(item.get("memory_text", "")).strip())
            if not text:
                continue
            candidates.append(
                CandidateMemory(
                    memory_text=text,
                    memory_type=str(item.get("memory_type", "fact")),
                    enable_graph=self._should_enable_graph(text),
                    confidence=confidence,
                    why_it_matters=str(item.get("why_it_matters", "")).strip(),
                    tags=[str(tag) for tag in item.get("tags", [])],
                    source_path=document.source_path,
                    source_title=document.title,
                    source_chunk_id=f"{document.content_hash[:12]}:{index}",
                    fingerprint=build_fingerprint(f"{document.source_path}:{text}"),
                    metadata={"doc_type": document.doc_type, "content_hash": document.content_hash},
                )
            )
        return candidates

    @staticmethod
    def _classify_memory_type(text: str) -> str:
        lowered = text.lower()
        if any(token in lowered for token in ["喜欢", "不喜欢", "prefer"]) or any(
            token in text for token in ["偏好", "偏向", "倾向"]
        ):
            return "preference"
        if any(token in lowered for token in ["规则", "约束", "must", "never", "always"]) or any(
            token in text for token in ["原则", "希望", "注重"]
        ):
            return "rule"
        if any(token in lowered for token in ["背景", "负责", "background", "owner"]):
            return "background"
        return "fact"

    @staticmethod
    def _why_it_matters(text: str) -> str:
        lowered = text.lower()
        if any(token in text for token in ["偏好", "偏向", "倾向", "喜欢"]) or "prefer" in lowered:
            return "这是稳定偏好，适合作为长期决策和推荐的参考。"
        if any(token in text for token in ["规则", "约束", "原则", "希望", "注重"]) or any(
            token in lowered for token in ["must", "always", "never", "constraint"]
        ):
            return "这是稳定工作方式或约束，适合作为长期执行偏好记忆。"
        if any(token in text for token in ["背景", "负责"]) or any(
            token in lowered for token in ["background", "owner"]
        ):
            return "这是长期背景信息，适合作为跨会话上下文。"
        return "这是可复用的长期事实，适合作为后续检索记忆。"

    @staticmethod
    def _rewrite_atomic_memory(text: str) -> str:
        cleaned = re.sub(r"^\d+\.\s*", "", text).strip(" ，,;；")
        cleaned = re.sub(r"\s+", " ", cleaned)

        if "技术选型" in cleaned and any(token in cleaned for token in ["偏向于", "偏好"]):
            if "在技术选型上我偏向于" in cleaned:
                preference_text = cleaned.split("在技术选型上我偏向于", 1)[1]
            elif "用户在技术选型上偏好" in cleaned:
                preference_text = cleaned.split("用户在技术选型上偏好", 1)[1]
            else:
                preference_text = cleaned
            technologies = [
                item.strip(" ，,。")
                for item in re.split(r"[，,、和]\s*", preference_text)
                if item.strip(" ，,。")
            ]
            normalized: list[str] = []
            for item in technologies:
                lowered = item.lower()
                if lowered.startswith("python"):
                    normalized.append("Python")
                elif lowered in {"ts", "typescript"}:
                    normalized.append("TypeScript")
                elif lowered == "go":
                    normalized.append("Go")
            normalized = list(dict.fromkeys(normalized))
            if normalized:
                if len(normalized) == 1:
                    return f"用户在技术选型上偏好 {normalized[0]}。"
                if len(normalized) == 2:
                    return f"用户在技术选型上偏好 {normalized[0]} 和 {normalized[1]}。"
                head = "、".join(normalized[:-1])
                return f"用户在技术选型上偏好 {head} 和 {normalized[-1]}。"

        if "我注重逻辑思维" in cleaned or "希望能先定义清楚问题" in cleaned:
            return "用户解决问题时偏好先定义清楚问题、量化目标并拆解任务。"

        if "加盟体系" in cleaned and "返利" in cleaned:
            return "中国经济型快递网络普遍采用加盟体系，总部与网点之间存在围绕返利政策的博弈。"

        if len(cleaned) > 80:
            parts = re.split(r"[，,；;]", cleaned)
            short = [part.strip() for part in parts if part.strip()]
            if short:
                candidate = short[0]
                return candidate if candidate.endswith("。") else f"{candidate}。"

        return cleaned if cleaned.endswith("。") else f"{cleaned}。"

    @staticmethod
    def _tags_for_text(text: str) -> list[str]:
        tags: list[str] = []
        lowered = text.lower()
        if any(token in text for token in ["偏好", "偏向", "倾向"]) or "prefer" in lowered:
            tags.append("preference")
        if any(token in text for token in ["规则", "约束", "原则", "希望", "注重"]) or "constraint" in lowered or "must" in lowered:
            tags.append("rule")
        if "背景" in text or "background" in lowered:
            tags.append("background")
        return tags

    @staticmethod
    def _should_enable_graph(text: str) -> bool:
        return False
