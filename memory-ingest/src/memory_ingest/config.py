from __future__ import annotations

from pathlib import Path
import os
import tomllib

from pydantic import BaseModel, Field, field_validator


def _expand_path(value: str) -> str:
    return str(Path(os.path.expandvars(os.path.expanduser(value))).resolve())


def _expand_path_no_resolve(value: str) -> str:
    return str(Path(os.path.expandvars(os.path.expanduser(value))).absolute())


class SourcesConfig(BaseModel):
    directories: list[str]
    include_globs: list[str] = Field(default_factory=list)
    exclude_globs: list[str] = Field(default_factory=list)

    @field_validator("directories", mode="before")
    @classmethod
    def _normalize_dirs(cls, value: list[str]) -> list[str]:
        return [_expand_path(v) for v in value]


class Mem0Config(BaseModel):
    host: str = "http://127.0.0.1:8766"
    api_key_env: str | None = None
    user_id: str = "xbot-global"
    app_id: str = "memory-ingest"
    org_id: str | None = None
    project_id: str | None = None
    timeout_seconds: int = 30
    local_python: str = "~/.xbot/workspace/openmemory/openmemory/api/.venv/bin/python"
    local_source: str = "~/.xbot/workspace/openmemory"

    @field_validator("local_python", mode="before")
    @classmethod
    def _normalize_local_python(cls, value: str) -> str:
        return _expand_path_no_resolve(value)

    @field_validator("local_source", mode="before")
    @classmethod
    def _normalize_local_source(cls, value: str) -> str:
        return _expand_path(value)


class ExtractConfig(BaseModel):
    model: str
    provider: str = "openai_compatible"
    api_base: str
    api_key_env: str = "MEMORY_INGEST_API_KEY"
    max_chunk_chars: int = 4000
    min_confidence: float = 0.72


class DedupConfig(BaseModel):
    enabled: bool = True
    fingerprint_similarity: float = 0.9


class StateConfig(BaseModel):
    sqlite_path: str

    @field_validator("sqlite_path", mode="before")
    @classmethod
    def _normalize_path(cls, value: str) -> str:
        return _expand_path(value)


class LoggingConfig(BaseModel):
    level: str = "INFO"
    log_path: str

    @field_validator("log_path", mode="before")
    @classmethod
    def _normalize_path(cls, value: str) -> str:
        return _expand_path(value)


class AppConfig(BaseModel):
    sources: SourcesConfig
    mem0: Mem0Config
    extract: ExtractConfig
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    state: StateConfig
    logging: LoggingConfig


def load_config(path: str | Path, *, source_overrides: list[str] | None = None) -> AppConfig:
    config_path = Path(path).expanduser().resolve()
    with config_path.open("rb") as fh:
        raw = tomllib.load(fh)
    if "mem0" not in raw and "openmemory" in raw:
        legacy = dict(raw.pop("openmemory"))
        raw["mem0"] = {
            "host": legacy.get("base_url", "https://api.mem0.ai"),
            "api_key_env": legacy.get("api_key_env", "MEM0_API_KEY"),
            "user_id": legacy.get("user_id", "xbot-global"),
            "app_id": legacy.get("app_id", "memory-ingest"),
            "org_id": legacy.get("org_id"),
            "project_id": legacy.get("project_id"),
            "timeout_seconds": legacy.get("timeout_seconds", 30),
        }
    if source_overrides:
        raw.setdefault("sources", {})
        raw["sources"]["directories"] = [_expand_path(v) for v in source_overrides]
    return AppConfig.model_validate(raw)
