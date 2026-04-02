from memory_ingest.config import Mem0Config
from memory_ingest.mem0_client import Mem0Client
from memory_ingest.models import CandidateMemory


class _Response:
    def __init__(self, status_code: int, body: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"status={self.status_code}")

    def json(self) -> dict:
        return self._body


class _HTTP:
    def __init__(self) -> None:
        self.calls = []

    def get(self, url: str, params=None, timeout=None):
        self.calls.append(("GET", url, params))
        return _Response(200, {})

    def post(self, url: str, json=None, timeout=None):
        self.calls.append(("POST", url, json))
        return _Response(200, {"results": [{"id": "memory-123"}]})

    def close(self) -> None:
        return None


def test_mem0_client_posts_memory_payload(monkeypatch) -> None:
    monkeypatch.setenv("MEM0_API_KEY", "m0-test")
    client = Mem0Client(
        Mem0Config(
            host="https://api.mem0.ai",
            api_key_env="MEM0_API_KEY",
            user_id="xbot-global",
            app_id="memory-ingest",
            timeout_seconds=30,
        )
    )
    client.http = _HTTP()
    candidate = CandidateMemory(
        memory_text="用户偏好喝乌龙茶",
        memory_type="preference",
        enable_graph=True,
        confidence=0.9,
        tags=["preference"],
        source_path="/tmp/a.md",
        source_title="A",
        source_chunk_id="chunk-1",
        fingerprint="fp-1",
        metadata={"doc_type": "md"},
    )

    remote_id = client.add_memory(candidate)

    assert remote_id == "memory-123"
    method, url, payload = client.http.calls[0]
    assert method == "POST"
    assert url == "/v1/memories/"
    assert payload["user_id"] == "xbot-global"
    assert payload["app_id"] == "memory-ingest"
    assert payload["messages"] == [{"role": "user", "content": "用户偏好喝乌龙茶"}]
    assert payload["enable_graph"] is True


def test_mem0_client_requires_api_key(monkeypatch) -> None:
    monkeypatch.delenv("MEM0_API_KEY", raising=False)
    try:
        Mem0Client(Mem0Config(host="https://api.mem0.ai", api_key_env="MEM0_API_KEY"))
    except ValueError as exc:
        assert "MEM0_API_KEY" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_mem0_client_search_returns_results_and_relations(monkeypatch) -> None:
    monkeypatch.setenv("MEM0_API_KEY", "m0-test")
    client = Mem0Client(
        Mem0Config(
            host="https://api.mem0.ai",
            api_key_env="MEM0_API_KEY",
            user_id="xbot-global",
            app_id="memory-ingest",
            timeout_seconds=30,
        )
    )

    class _SearchHTTP(_HTTP):
        def post(self, url: str, json=None):
            self.calls.append(("POST", url, json))
            return _Response(
                200,
                {
                    "results": [
                        {
                            "id": "mem-1",
                            "memory": "用户偏好 Python",
                            "score": 0.91,
                            "categories": ["preference"],
                            "metadata": {"source_path": "/tmp/a.md"},
                        }
                    ],
                    "relations": [
                        {
                            "source": "user",
                            "source_type": "person",
                            "relationship": "prefers",
                            "target": "Python",
                            "target_type": "technology",
                            "score": 0.88,
                        }
                    ],
                },
            )

    client.http = _SearchHTTP()

    result = client.search("用户偏好什么技术栈？", top_k=3, enable_graph=True)

    assert result.results[0].memory == "用户偏好 Python"
    assert result.relations[0].relationship == "prefers"
    method, url, payload = client.http.calls[0]
    assert method == "POST"
    assert url == "/v2/memories/search/"
    assert payload["enable_graph"] is True
    assert payload["top_k"] == 3


def test_mem0_client_local_add_confirms_on_timeout() -> None:
    client = Mem0Client(
        Mem0Config(
            host="http://127.0.0.1:8766",
            user_id="aliu",
            app_id="memory-ingest",
            timeout_seconds=1,
        )
    )

    class _LocalHTTP:
        def __init__(self) -> None:
            self.calls = []

        def post(self, url: str, json=None, timeout=None):
            self.calls.append(("POST", url, json, timeout))
            raise httpx.ReadTimeout("timed out")

        def get(self, url: str, params=None, timeout=None):
            self.calls.append(("GET", url, params, timeout))
            return _Response(
                200,
                {
                    "items": [
                        {
                            "id": "memory-local-1",
                            "memory": "用户偏好喝乌龙茶",
                        }
                    ]
                },
            )

        def close(self) -> None:
            return None

    import httpx
    import subprocess

    client.http = _LocalHTTP()
    real_run = subprocess.run

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="python", timeout=1)

    subprocess.run = _raise_timeout
    candidate = CandidateMemory(
        memory_text="用户偏好喝乌龙茶",
        memory_type="preference",
        enable_graph=True,
        confidence=0.9,
        tags=["preference"],
        source_path="/tmp/a.md",
        source_title="A",
        source_chunk_id="chunk-1",
        fingerprint="fp-local-1",
        metadata={"doc_type": "md"},
    )

    try:
        remote_id = client.add_memory(candidate)
    finally:
        subprocess.run = real_run

    assert remote_id == "memory-local-1"
