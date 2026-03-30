from __future__ import annotations

import json


MSG_TYPE_MAP = {
    "image": "[image]",
    "audio": "[audio]",
    "file": "[file]",
    "sticker": "[sticker]",
}


def extract_share_card_content(content_json: dict, msg_type: str) -> str:
    parts: list[str] = []

    if msg_type == "share_chat":
        parts.append(f"[shared chat: {content_json.get('chat_id', '')}]")
    elif msg_type == "share_user":
        parts.append(f"[shared user: {content_json.get('user_id', '')}]")
    elif msg_type == "interactive":
        parts.extend(extract_interactive_content(content_json))
    elif msg_type == "share_calendar_event":
        parts.append(f"[shared calendar event: {content_json.get('event_key', '')}]")
    elif msg_type == "system":
        parts.append("[system message]")
    elif msg_type == "merge_forward":
        parts.append("[merged forward messages]")

    return "\n".join(parts) if parts else f"[{msg_type}]"


def extract_interactive_content(content: dict | str) -> list[str]:
    parts: list[str] = []

    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return [content] if content.strip() else []

    if not isinstance(content, dict):
        return parts

    if "title" in content:
        title = content["title"]
        if isinstance(title, dict):
            title_content = title.get("content", "") or title.get("text", "")
            if title_content:
                parts.append(f"title: {title_content}")
        elif isinstance(title, str):
            parts.append(f"title: {title}")

    for elements in content.get("elements", []) if isinstance(content.get("elements"), list) else []:
        for element in elements:
            parts.extend(_extract_element_content(element))

    card = content.get("card", {})
    if card:
        parts.extend(extract_interactive_content(card))

    return parts


def _extract_element_content(element: dict) -> list[str]:
    parts: list[str] = []
    if not isinstance(element, dict):
        return parts

    tag = element.get("tag", "")
    if tag in ("markdown", "lark_md"):
        content = element.get("content", "")
        if content:
            parts.append(content)
    elif tag == "div":
        text = element.get("text", {})
        if isinstance(text, dict):
            text_content = text.get("content", "") or text.get("text", "")
            if text_content:
                parts.append(text_content)
        elif isinstance(text, str):
            parts.append(text)
    elif tag == "plain_text":
        content = element.get("content", "")
        if content:
            parts.append(content)
    else:
        for child in element.get("elements", []):
            parts.extend(_extract_element_content(child))

    return parts


def extract_post_content(content_json: dict) -> str:
    root = content_json
    if isinstance(root, dict) and isinstance(root.get("post"), dict):
        root = root["post"]
    if not isinstance(root, dict):
        return ""

    block = None
    if "content" in root:
        block = root
    else:
        for key in ("zh_cn", "en_us", "ja_jp"):
            if isinstance(root.get(key), dict):
                block = root[key]
                break

    if not isinstance(block, dict) or not isinstance(block.get("content"), list):
        return ""

    texts: list[str] = []
    title = block.get("title")
    if title:
        texts.append(str(title))

    for row in block.get("content", []):
        if not isinstance(row, list):
            continue
        for el in row:
            if not isinstance(el, dict):
                continue
            tag = el.get("tag")
            if tag in ("text", "a"):
                text = el.get("text", "")
                if text:
                    texts.append(text)
            elif tag == "at":
                texts.append(f"@{el.get('user_name', 'user')}")
            elif tag == "code_block":
                code_text = el.get("text", "")
                if code_text:
                    texts.append(code_text)

    return " ".join(part for part in texts if part).strip()
