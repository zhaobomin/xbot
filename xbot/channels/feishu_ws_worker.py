"""Feishu WebSocket worker process.

Runs the lark-oapi WebSocket client in an isolated process so its module-level
event loop state does not interfere with the main xbot runtime.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any


def _getattr_chain(obj: Any, *attrs: str) -> Any:
    value = obj
    for attr in attrs:
        value = getattr(value, attr, None)
        if value is None:
            return None
    return value


def _normalize_message_event(data: Any) -> dict[str, Any]:
    message = _getattr_chain(data, "event", "message")
    sender = _getattr_chain(data, "event", "sender")
    mentions = []
    for mention in getattr(message, "mentions", None) or []:
        mention_id = getattr(mention, "id", None)
        mentions.append(
            {
                "id": {
                    "open_id": getattr(mention_id, "open_id", None),
                    "user_id": getattr(mention_id, "user_id", None),
                }
            }
        )

    return {
        "event": {
            "message": {
                "message_id": getattr(message, "message_id", None),
                "chat_id": getattr(message, "chat_id", None),
                "chat_type": getattr(message, "chat_type", None),
                "message_type": getattr(message, "message_type", None),
                "content": getattr(message, "content", None),
                "parent_id": getattr(message, "parent_id", None),
                "root_id": getattr(message, "root_id", None),
                "mentions": mentions,
            },
            "sender": {
                "sender_type": getattr(sender, "sender_type", None),
                "sender_id": {
                    "open_id": _getattr_chain(sender, "sender_id", "open_id"),
                } if getattr(sender, "sender_id", None) is not None else None,
            },
        }
    }


def run_feishu_ws_worker(
    config: dict[str, Any],
    event_queue: Any,
    stop_event: Any,
    reconnect_delay: int,
    max_reconnect_delay: int,
) -> None:
    import lark_oapi as lark
    import lark_oapi.ws.client as lark_ws_client

    def _enqueue_message(data: Any) -> None:
        try:
            event_queue.put_nowait({"type": "message", "payload": _normalize_message_event(data)})
        except Exception as exc:
            event_queue.put_nowait({"type": "error", "error": f"enqueue message failed: {exc}"})

    def _noop(_data: Any) -> None:
        return None

    builder = lark.EventDispatcherHandler.builder(
        config.get("encrypt_key") or "",
        config.get("verification_token") or "",
    ).register_p2_im_message_receive_v1(_enqueue_message)

    for method_name in (
        "register_p2_im_message_reaction_created_v1",
        "register_p2_im_message_message_read_v1",
        "register_p2_im_chat_access_event_bot_p2p_chat_entered_v1",
    ):
        method = getattr(builder, method_name, None)
        if callable(method):
            builder = method(_noop)

    event_handler = builder.build()
    ws_client = lark.ws.Client(
        config["app_id"],
        config["app_secret"],
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    lark_ws_client.loop = loop

    def _stop_watcher() -> None:
        stop_event.wait()
        if loop.is_closed():
            return
        loop.call_soon_threadsafe(loop.stop)

    watcher = threading.Thread(target=_stop_watcher, daemon=True, name="feishu-ws-stop")
    watcher.start()

    current_delay = reconnect_delay
    try:
        while not stop_event.is_set():
            try:
                ws_client.start()
                current_delay = reconnect_delay
            except Exception as exc:
                event_queue.put_nowait({"type": "error", "error": str(exc)})
            if stop_event.wait(timeout=current_delay):
                break
            current_delay = min(current_delay * 2, max_reconnect_delay)
    finally:
        if not loop.is_closed():
            loop.close()
