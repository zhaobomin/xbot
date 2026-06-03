from __future__ import annotations

from xbot.platform.utils import helpers


class _FakeEncoding:
    def encode(self, text: str) -> list[str]:
        return text.split()


def test_estimate_prompt_tokens_reuses_tiktoken_encoding(monkeypatch) -> None:
    calls = 0

    def _get_encoding(name: str) -> _FakeEncoding:
        nonlocal calls
        assert name == "cl100k_base"
        calls += 1
        return _FakeEncoding()

    monkeypatch.setattr(helpers, "_TIKTOKEN_ENCODING", None)
    monkeypatch.setattr(helpers.tiktoken, "get_encoding", _get_encoding)

    messages = [{"role": "user", "content": "hello world"}]

    assert helpers.estimate_prompt_tokens(messages) == 2
    assert helpers.estimate_prompt_tokens(messages) == 2
    assert calls == 1
