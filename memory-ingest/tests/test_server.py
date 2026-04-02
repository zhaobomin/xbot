from pathlib import Path

from fastapi.testclient import TestClient

from memory_ingest.config import load_config
from memory_ingest.server import create_app


def _write_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "memory-ingest.toml"
    cfg.write_text(
        f"""
[sources]
directories = ["{tmp_path.as_posix()}"]
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
api_key_env = "MISSING_KEY"
max_chunk_chars = 4000
min_confidence = 0.72

[dedup]
enabled = true
fingerprint_similarity = 0.9

[state]
sqlite_path = "{(tmp_path / 'state.db').as_posix()}"

[logging]
level = "INFO"
log_path = "{(tmp_path / 'memory-ingest.log').as_posix()}"
""",
        encoding="utf-8",
    )
    return cfg


def test_server_query_endpoint_returns_json(tmp_path: Path) -> None:
    cfg = load_config(_write_config(tmp_path))
    app = create_app(cfg)

    class _FakeService:
        def query(self, query: str, *, top_k: int = 5, enable_graph: bool = True):
            return {
                "query": query,
                "top_k": top_k,
                "enable_graph": enable_graph,
                "results": [{"memory": "用户偏好 Python"}],
                "relations": [{"source": "user", "relationship": "prefers", "target": "Python"}],
            }

        def close(self) -> None:
            return None

    with TestClient(app) as client:
        client.app.state.ingest_service = _FakeService()
        response = client.post("/query", json={"query": "技术偏好", "top_k": 3, "graph": True})

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "技术偏好"
    assert body["results"][0]["memory"] == "用户偏好 Python"
