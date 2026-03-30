from xbot_codex.session.store import SessionStore


def test_get_or_create_session_uses_channel_chat_key() -> None:
    store = SessionStore(default_workdir_root="/tmp/xbot-codex")

    session = store.get_or_create("telegram", "123")

    assert session.session_key == "telegram:123"
    assert session.channel == "telegram"
    assert session.chat_id == "123"
    assert session.codex_model is None
    assert session.process_state == "idle"


def test_reset_session_replaces_identity_and_clears_running_state() -> None:
    store = SessionStore(default_workdir_root="/tmp/xbot-codex")
    session = store.get_or_create("feishu", "chat-1")
    session.codex_session_id = "sid-1"
    session.process_state = "running"
    old_runtime_id = session.runtime_session_id

    reset = store.reset("feishu:chat-1")

    assert reset.runtime_session_id != old_runtime_id
    assert reset.codex_session_id is None
    assert reset.process_state == "idle"


def test_touch_updates_last_activity() -> None:
    store = SessionStore(default_workdir_root="/tmp/xbot-codex")
    session = store.get_or_create("telegram", "1")
    before = session.last_activity_at

    store.touch(session.session_key)

    assert session.last_activity_at >= before
