from pathlib import Path
import pytest
from tests.test_webui_adapter import _build_client
from xbot.interfaces.gateway import auth


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "JWT_SECRET_FILE", tmp_path / "jwt")
    monkeypatch.setattr(auth, "PASSWORD_FILE", tmp_path / "password")
    client, services = _build_client(tmp_path)
    token = client.app.state.auth.issue_token({"id": "admin", "username": "admin", "role": "admin"})
    client.headers["Authorization"] = "Bearer " + token
    yield client, services, token
    client.close()


def test_revoke_requires_revision_and_rejects_stale_index(gateway):
    client, services, _ = gateway
    key = "web:admin:review"
    session = services.conversation_store.get_or_create(key)
    for i in range(6):
        session.add_message("user" if i % 2 == 0 else "assistant", str(i))
    services.conversation_store.save(session)
    endpoint = f"/api/sessions/{key}/messages"
    history = client.get(endpoint)
    revision = history.headers.get("etag")
    assert revision, "History must identify the snapshot being deleted from"
    assert client.delete(endpoint + "/1").status_code == 428
    assert client.delete(endpoint + "/1", headers={"If-Match": revision}).status_code == 200
    assert client.delete(endpoint + "/2", headers={"If-Match": revision}).status_code == 409
    assert [m["content"] for m in client.get(endpoint).json()] == ["0", "2", "3", "4", "5"]


def test_revoke_detects_another_store_writer(gateway):
    from xbot.runtime.session.conversation_store import ConversationStore

    client, services, _ = gateway
    key = "web:admin:review"
    session = services.conversation_store.get_or_create(key)
    session.add_message("user", "first")
    services.conversation_store.save(session)
    endpoint = f"/api/sessions/{key}/messages"
    revision = client.get(endpoint).headers.get("etag")
    assert revision
    other = ConversationStore(services.config.workspace_path)
    changed = other.get_or_create(key)
    changed.add_message("assistant", "second")
    other.save(changed)
    assert client.delete(endpoint + "/0", headers={"If-Match": revision}).status_code == 409
    assert len(other._load(key).messages) == 2


def test_upload_is_forwarded_as_real_image_to_runtime(gateway):
    import base64
    from xbot.runtime.core.service import AgentService

    client, services, token = gateway
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
    )
    response = client.post("/api/config/s3/upload", files={"file": ("tiny.png", png, "image/png")})
    assert response.status_code == 200
    attachment = response.json()
    with client.websocket_connect(f"/ws/chat?token={token}&session=web:admin:review") as ws:
        assert ws.receive_json()["type"] == "session_info"
        ws.send_json(
            {"type": "message", "content": "describe", "attachment_ids": [attachment["id"]]}
        )
        while ws.receive_json()["type"] != "done":
            pass
    media = services.agent.calls[-1]["media"]
    assert len(media) == 1 and Path(media[0]).read_bytes() == png
    prompt = AgentService._build_query_prompt("describe", media)
    assert any(item.get("type") == "image" for item in prompt)


def test_json_attachment_round_trip_preserves_content(gateway):
    client, services, _ = gateway
    content = b'{"answer":42}'
    response = client.post(
        "/api/config/s3/upload", files={"file": ("data.json", content, "application/json")}
    )
    assert response.status_code == 200
    assert client.get(response.json()["url"]).content == content


@pytest.mark.asyncio
async def test_history_edit_drains_archival_before_store_delete(gateway, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    client, services, _ = gateway
    key = "web:admin:edit"
    session = services.conversation_store.get_or_create(key)
    session.add_message("user", "old")
    services.conversation_store.save(session)
    prepare = AsyncMock()
    services.agent = SimpleNamespace(prepare_history_edit=prepare)
    revision = client.get(f"/api/sessions/{key}/messages").headers["etag"]
    assert (
        client.delete(f"/api/sessions/{key}/messages/0", headers={"If-Match": revision}).status_code
        == 200
    )
    prepare.assert_awaited_once_with(key)


def test_conditional_delete_cannot_resurrect_deleted_session(tmp_path):
    from xbot.runtime.session.conversation_store import ConversationStore

    first, second = ConversationStore(tmp_path), ConversationStore(tmp_path)
    session = first.get_or_create("web:admin:deleted")
    session.add_message("user", "a")
    session.add_message("assistant", "b")
    first.save(session)
    revision = first.message_revision(session.messages)
    second.delete(session.key)
    with pytest.raises(ValueError):
        first.delete_message(session, 0, expected_revision=revision)
    assert second.get(session.key) is None


def test_attachment_upload_and_preview_boundaries(gateway):
    client, _, _ = gateway
    endpoint = "/api/config/s3/upload"
    assert (
        client.post(
            endpoint, headers={"Authorization": ""}, files={"file": ("a.txt", b"a")}
        ).status_code
        == 401
    )
    assert (
        client.post(endpoint, files={"file": ("a.txt", b"x" * (20 * 1024 * 1024 + 1))}).status_code
        == 413
    )
    assert client.post(endpoint, files={"file": ("a.png", b"not an image")}).status_code == 400
    uploaded = client.post(endpoint, files={"file": ("a.txt", b"original")}).json()
    assert client.get(uploaded["url"] + "bad").status_code == 404
    assert client.get(uploaded["url"]).content == b"original"


def test_attachment_reference_owner_and_retention(tmp_path):
    import time
    from xbot.interfaces.gateway.attachments import AttachmentStore

    store = AttachmentStore(tmp_path / "uploads", "secret")
    metadata = store.save("alice", "../../notes.json", b'{"safe":true}')
    with pytest.raises(ValueError, match="another user"):
        store.resolve([metadata["id"]], "bob")
    with pytest.raises(ValueError, match="maximum 8"):
        store.resolve([metadata["id"]] * 9, "alice")
    with pytest.raises(ValueError):
        store.resolve(["../../private"], "alice")
    metadata["created_at"] = time.time() - 90000
    store._write(metadata)
    retained = store.resolve([metadata["id"]], "alice", reference=True)[0]
    orphan = store.save("alice", "old.txt", b"orphan")
    orphan["created_at"] = time.time() - 90000
    store._write(orphan)
    store.cleanup()
    assert Path(retained).read_bytes() == b'{"safe":true}'
    with pytest.raises(ValueError):
        store.get(orphan["id"])


def test_cleanup_cannot_delete_an_attachment_while_it_is_being_referenced(tmp_path, monkeypatch):
    import time
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from xbot.interfaces.gateway.attachments import AttachmentStore

    store = AttachmentStore(tmp_path / "uploads", "secret")
    metadata = store.save("alice", "notes.txt", b"keep")
    metadata["created_at"] = time.time() - 90000
    store._write(metadata)
    loaded, cleanup_started = threading.Event(), threading.Event()
    original = store.get

    def paused_get(identifier):
        result = original(identifier)
        loaded.set()
        assert cleanup_started.wait(2)
        return result

    monkeypatch.setattr(store, "get", paused_get)

    def cleanup():
        assert loaded.wait(2)
        cleanup_started.set()
        store.cleanup()

    with ThreadPoolExecutor(2) as pool:
        resolve = pool.submit(store.resolve, [metadata["id"]], "alice", reference=True)
        cleaned = pool.submit(cleanup)
        paths = resolve.result(timeout=3)
        cleaned.result(timeout=3)
    assert Path(paths[0]).read_bytes() == b"keep"


def test_s3_mirror_uses_real_client_serialization(tmp_path, monkeypatch):
    import boto3
    from botocore.stub import Stubber
    from xbot.interfaces.gateway.attachments import mirror_to_s3

    client = boto3.client(
        "s3", aws_access_key_id="test", aws_secret_access_key="test", region_name="us-east-1"
    )
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: client)
    with Stubber(client) as stub:
        stub.add_response(
            "put_object",
            {},
            {"Bucket": "uploads", "Key": "xbot-attachments/id.txt", "Body": b"content"},
        )
        mirror_to_s3(
            {
                "enabled": True,
                "bucket": "uploads",
                "access_key_id": "test",
                "secret_access_key": "test",
            },
            {"filename": "id.txt"},
            b"content",
        )
        stub.assert_no_pending_responses()


def test_delete_session_rejects_an_active_turn_before_reset(gateway):
    import asyncio
    import threading
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    client, services, token = gateway
    started = threading.Event()

    async def running(**kwargs):
        started.set()
        await asyncio.Event().wait()

    reset = AsyncMock()
    services.agent = SimpleNamespace(process_managed_direct=running, reset_session=reset)
    key = "web:admin:active-delete"
    session = services.conversation_store.get_or_create(key)
    session.add_message("user", "keep")
    services.conversation_store.save(session)
    with client.websocket_connect(f"/ws/chat?token={token}&session={key}") as ws:
        ws.receive_json()
        ws.send_json({"type": "message", "content": "work"})
        assert started.wait(2)
        assert client.delete(f"/api/sessions/{key}").status_code == 409
        reset.assert_not_awaited()
        assert services.conversation_store.get(key) is not None
