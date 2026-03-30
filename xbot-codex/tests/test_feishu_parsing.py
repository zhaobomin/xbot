from xbot_codex.channels.feishu import FeishuChannel
from xbot_codex.config import FeishuConfig


def test_feishu_extract_text_from_message_payload() -> None:
    channel = FeishuChannel(FeishuConfig(enabled=True, app_id="id", app_secret="secret", allow_from=["*"]))

    assert channel.extract_text('{"text":"hello"}', "text") == "hello"
    assert channel.extract_text("plain", "text") == "plain"


def test_feishu_extracts_post_and_interactive_content() -> None:
    channel = FeishuChannel(FeishuConfig(enabled=True, app_id="id", app_secret="secret", allow_from=["*"]))

    post_payload = (
        '{"zh_cn":{"title":"日报","content":[[{"tag":"text","text":"hello"},{"tag":"at","user_name":"bob"}]]}}'
    )
    interactive_payload = '{"title":{"content":"审批"},"elements":[[{"tag":"markdown","content":"**done**"}]]}'

    assert channel.extract_text(post_payload, "post") == "日报 hello @bob"
    assert channel.extract_text(interactive_payload, "interactive") == "title: 审批\n**done**"


def test_feishu_dedup_cache_marks_repeat_message() -> None:
    channel = FeishuChannel(FeishuConfig(enabled=True, app_id="id", app_secret="secret", allow_from=["*"]))

    assert channel.seen_message("mid-1") is False
    assert channel.seen_message("mid-1") is True
