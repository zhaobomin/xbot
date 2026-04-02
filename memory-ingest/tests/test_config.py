from pathlib import Path

from memory_ingest.config import load_config


def test_load_config_applies_source_overrides(tmp_path: Path) -> None:
    cfg_path = tmp_path / "memory-ingest.toml"
    cfg_path.write_text(
        """
[sources]
directories = ["/tmp/original"]
include_globs = ["**/*.md"]
exclude_globs = []

[mem0]
host = "https://api.mem0.ai"
api_key_env = "MEM0_API_KEY"
user_id = "xbot-global"
app_id = "memory-ingest"
timeout_seconds = 30

[extract]
model = "kimi-k2.5"
provider = "openai_compatible"
api_base = "https://example.com/v1"
api_key_env = "TEST_API_KEY"
max_chunk_chars = 4000
min_confidence = 0.72

[dedup]
enabled = true
fingerprint_similarity = 0.9

[state]
sqlite_path = "~/.memory-ingest/state.db"

[logging]
level = "INFO"
log_path = "~/.memory-ingest/memory-ingest.log"
""",
        encoding="utf-8",
    )

    config = load_config(cfg_path, source_overrides=[str(tmp_path)])

    assert config.sources.directories == [str(tmp_path.resolve())]


def test_load_config_accepts_legacy_openmemory_section(tmp_path: Path) -> None:
    cfg_path = tmp_path / "memory-ingest.toml"
    cfg_path.write_text(
        """
[sources]
directories = ["/tmp/original"]
include_globs = ["**/*.md"]
exclude_globs = []

[openmemory]
base_url = "http://127.0.0.1:8766"
user_id = "xbot-global"
app_id = "memory-ingest"
timeout_seconds = 30

[extract]
model = "kimi-k2.5"
provider = "openai_compatible"
api_base = "https://example.com/v1"
api_key_env = "TEST_API_KEY"
max_chunk_chars = 4000
min_confidence = 0.72

[dedup]
enabled = true
fingerprint_similarity = 0.9

[state]
sqlite_path = "~/.memory-ingest/state.db"

[logging]
level = "INFO"
log_path = "~/.memory-ingest/memory-ingest.log"
""",
        encoding="utf-8",
    )

    config = load_config(cfg_path)

    assert config.mem0.host == "http://127.0.0.1:8766"
    assert config.mem0.user_id == "xbot-global"
