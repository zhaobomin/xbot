from xbot.channels.feishu import FeishuChannel, _extract_post_content
from xbot.channels.feishu_content import _extract_share_card_content


def test_extract_post_content_supports_post_wrapper_shape() -> None:
    payload = {
        "post": {
            "zh_cn": {
                "title": "日报",
                "content": [
                    [
                        {"tag": "text", "text": "完成"},
                        {"tag": "img", "image_key": "img_1"},
                    ]
                ],
            }
        }
    }

    text, image_keys = _extract_post_content(payload)

    assert text == "日报 完成"
    assert image_keys == ["img_1"]


def test_extract_post_content_keeps_direct_shape_behavior() -> None:
    payload = {
        "title": "Daily",
        "content": [
            [
                {"tag": "text", "text": "report"},
                {"tag": "img", "image_key": "img_a"},
                {"tag": "img", "image_key": "img_b"},
            ]
        ],
    }

    text, image_keys = _extract_post_content(payload)

    assert text == "Daily report"
    assert image_keys == ["img_a", "img_b"]


def test_register_optional_event_keeps_builder_when_method_missing() -> None:
    class Builder:
        pass

    builder = Builder()
    same = FeishuChannel._register_optional_event(builder, "missing", object())
    assert same is builder


def test_register_optional_event_calls_supported_method() -> None:
    called = []

    class Builder:
        def register_event(self, handler):
            called.append(handler)
            return self

    builder = Builder()
    handler = object()
    same = FeishuChannel._register_optional_event(builder, "register_event", handler)

    assert same is builder
    assert called == [handler]


def test_extract_interactive_content_supports_schema_2_body_elements() -> None:
    payload = {
        "schema": "2.0",
        "header": {"title": {"tag": "plain_text", "content": "任务结果"}},
        "body": {
            "elements": [
                {"tag": "markdown", "content": "**完成**\n\n详情"},
                {
                    "tag": "column_set",
                    "columns": [
                        {"elements": [{"tag": "div", "text": {"tag": "plain_text", "content": "列内容"}}]}
                    ],
                },
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "打开"},
                            "url": "https://example.com",
                        }
                    ],
                },
            ]
        },
    }

    text = _extract_share_card_content(payload, "interactive")

    assert "title: 任务结果" in text
    assert "**完成**\n\n详情" in text
    assert "列内容" in text
    assert "打开" in text
    assert "link: https://example.com" in text
