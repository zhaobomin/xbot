from __future__ import annotations

from datetime import datetime, timezone
from difflib import SequenceMatcher
import os

import httpx

from .config import Mem0Config
from .models import CandidateMemory, MemoryQueryResponse, MemoryRelation, MemorySearchResult


class Mem0Client:
    def __init__(self, config: Mem0Config) -> None:
        self.config = config
        self.is_local = self._is_local_host(config.host)
        headers = None
        if self.is_local:
            self.http = httpx.Client(base_url=config.host.rstrip("/"), timeout=config.timeout_seconds)
        else:
            api_key_name = config.api_key_env or "MEM0_API_KEY"
            api_key = os.getenv(api_key_name, "").strip()
            if not api_key:
                raise ValueError(
                    f"Missing mem0 API key. Set environment variable {api_key_name}."
                )
            headers = {"Authorization": f"Token {api_key}"}
            self.http = httpx.Client(
                base_url=config.host.rstrip("/"),
                headers=headers,
                timeout=config.timeout_seconds,
            )

    def healthcheck(self) -> None:
        if self.is_local:
            response = self.http.get("/api/v1/config/")
        else:
            response = self.http.get("/v1/ping/", params=self._params())
        response.raise_for_status()

    def add_memory(self, candidate: CandidateMemory) -> str | None:
        if self.is_local:
            return self._add_memory_local(candidate)
        payload = {
            "messages": [{"role": "user", "content": candidate.memory_text}],
            "user_id": self.config.user_id,
            "app_id": self.config.app_id,
            "enable_graph": candidate.enable_graph,
            "metadata": {
                "source_path": candidate.source_path,
                "title": candidate.source_title,
                "fingerprint": candidate.fingerprint,
                "memory_type": candidate.memory_type,
                "tags": candidate.tags,
                "imported_at": datetime.now(timezone.utc).isoformat(),
                **candidate.metadata,
            },
            "async_mode": False,
            "output_format": "v1.1",
        }
        payload.update(self._params())
        response = self.http.post("/v1/memories/", json=payload)
        response.raise_for_status()
        body = response.json()
        if isinstance(body, dict):
            if "results" in body and body["results"]:
                return str(body["results"][0].get("id"))
            if "id" in body:
                return str(body["id"])
        return None

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        enable_graph: bool = True,
    ) -> MemoryQueryResponse:
        if self.is_local:
            return self._search_local(query, top_k=top_k, enable_graph=enable_graph)
        payload = {
            "query": query,
            "filters": {"AND": [{"user_id": self.config.user_id}, {"app_id": self.config.app_id}]},
            "top_k": top_k,
            "enable_graph": enable_graph,
        }
        payload.update(self._params())
        response = self.http.post("/v2/memories/search/", json=payload)
        response.raise_for_status()
        decoded = response.json()
        body = decoded if isinstance(decoded, dict) else {}
        results = [
            MemorySearchResult(
                id=item.get("id"),
                memory=item.get("memory", ""),
                score=item.get("score"),
                categories=list(item.get("categories") or []),
                metadata=item.get("metadata"),
                raw=item,
            )
            for item in body.get("results", []) or []
        ]
        relations = [
            MemoryRelation(
                source=item.get("source", ""),
                source_type=item.get("source_type"),
                relationship=item.get("relationship", ""),
                target=item.get("target", ""),
                target_type=item.get("target_type"),
                score=item.get("score"),
            )
            for item in body.get("relations", []) or []
        ]
        return MemoryQueryResponse(
            query=query,
            user_id=self.config.user_id,
            app_id=self.config.app_id,
            enable_graph=enable_graph,
            results=results,
            relations=relations,
            raw=body,
        )

    def close(self) -> None:
        self.http.close()

    def _params(self) -> dict[str, str]:
        params: dict[str, str] = {}
        if self.config.org_id:
            params["org_id"] = self.config.org_id
        if self.config.project_id:
            params["project_id"] = self.config.project_id
        return params

    @staticmethod
    def _is_local_host(host: str) -> bool:
        normalized = host.rstrip("/")
        return normalized.startswith("http://127.0.0.1") or normalized.startswith("http://localhost")

    def _add_memory_local(self, candidate: CandidateMemory) -> str | None:
        metadata = {
            "source_path": candidate.source_path,
            "title": candidate.source_title,
            "fingerprint": candidate.fingerprint,
            "memory_type": candidate.memory_type,
            "tags": candidate.tags,
            "imported_at": datetime.now(timezone.utc).isoformat(),
            **candidate.metadata,
        }
        try:
            response = self.http.post(
                "/api/v1/memories/",
                json={
                    "user_id": self.config.user_id,
                    "text": candidate.memory_text,
                    "metadata": metadata,
                    "infer": False,
                    "app": self.config.app_id,
                },
                timeout=max(self.config.timeout_seconds, 180),
            )
            response.raise_for_status()
            body = response.json() if getattr(response, "content", True) else {}
            if isinstance(body, dict):
                raw_id = body.get("id")
                if raw_id is not None:
                    return str(raw_id)
        except httpx.ReadTimeout:
            pass
        return self._find_local_memory_id(candidate.memory_text)

    def _find_local_memory_id(self, memory_text: str) -> str | None:
        response = self.http.get(
            "/api/v1/memories/",
            params={"user_id": self.config.user_id},
            timeout=max(self.config.timeout_seconds, 180),
        )
        response.raise_for_status()
        body = response.json() if getattr(response, "content", True) else {}
        items = []
        if isinstance(body, dict):
            items = list(body.get("items") or body.get("results") or body.get("memories") or [])
        elif isinstance(body, list):
            items = body
        for item in items:
            if item.get("memory") == memory_text or item.get("text") == memory_text:
                raw_id = item.get("id")
                return str(raw_id) if raw_id is not None else None
        return None

    def _search_local(self, query: str, *, top_k: int, enable_graph: bool) -> MemoryQueryResponse:
        response = self.http.get(
            "/api/v1/memories/",
            params={
                "user_id": self.config.user_id,
                "page": 1,
                "size": max(top_k * 10, 100),
            },
        )
        response.raise_for_status()
        body = response.json() if getattr(response, "content", True) else {}
        items = list(body.get("items") or []) if isinstance(body, dict) else []
        scored_items = [
            (self._local_text_score(query, item.get("content") or item.get("memory", "")), item)
            for item in items
        ]
        ranked_items = [item for score, item in sorted(scored_items, key=lambda pair: pair[0], reverse=True) if score > 0][:top_k]
        results = [
            MemorySearchResult(
                id=item.get("id"),
                memory=item.get("content") or item.get("memory", ""),
                score=self._local_text_score(query, item.get("content") or item.get("memory", "")),
                categories=list(item.get("categories") or []),
                metadata=item.get("metadata_") or item.get("metadata"),
                raw=item,
            )
            for item in ranked_items
        ]
        return MemoryQueryResponse(
            query=query,
            user_id=self.config.user_id,
            app_id=self.config.app_id,
            enable_graph=False,
            results=results,
            relations=[],
            raw=body,
        )

    @staticmethod
    def _local_text_score(query: str, content: str) -> float:
        query_norm = "".join(query.lower().split())
        content_norm = "".join(content.lower().split())
        if not query_norm or not content_norm:
            return 0.0
        if query_norm in content_norm:
            return 1.0
        query_chars = set(query_norm)
        content_chars = set(content_norm)
        overlap = len(query_chars & content_chars) / max(len(query_chars), 1)
        ratio = SequenceMatcher(None, query_norm, content_norm).ratio()
        return max(overlap, ratio)
