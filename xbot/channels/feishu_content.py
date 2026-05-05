"""Feishu content extraction utilities.

This module provides utilities for extracting text content from various
Feishu message types (share cards, interactive cards, posts, etc.).
"""

from __future__ import annotations

import json

# Message type display mapping
MSG_TYPE_MAP = {
    "image": "[image]",
    "audio": "[audio]",
    "file": "[file]",
    "sticker": "[sticker]",
}


def _text_value(value: object) -> str:
    if isinstance(value, dict):
        text = value.get("content", "") or value.get("text", "")
        return str(text) if text else ""
    if isinstance(value, str):
        return value
    return ""


def _extract_share_card_content(content_json: dict, msg_type: str) -> str:
    """Extract text representation from share cards and interactive messages.

    Args:
        content_json: The message content JSON
        msg_type: The message type (share_chat, share_user, interactive, etc.)

    Returns:
        Human-readable text representation of the content
    """
    parts = []

    if msg_type == "share_chat":
        parts.append(f"[shared chat: {content_json.get('chat_id', '')}]")
    elif msg_type == "share_user":
        parts.append(f"[shared user: {content_json.get('user_id', '')}]")
    elif msg_type == "interactive":
        parts.extend(_extract_interactive_content(content_json))
    elif msg_type == "share_calendar_event":
        parts.append(f"[shared calendar event: {content_json.get('event_key', '')}]")
    elif msg_type == "system":
        parts.append("[system message]")
    elif msg_type == "merge_forward":
        parts.append("[merged forward messages]")

    return "\n".join(parts) if parts else f"[{msg_type}]"


def _extract_interactive_content(content: dict) -> list[str]:
    """Recursively extract text and links from interactive card content.

    Args:
        content: The interactive card content (dict or JSON string)

    Returns:
        List of extracted text segments
    """
    parts = []

    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return [content] if content.strip() else []

    if not isinstance(content, dict):
        return parts

    if "title" in content:
        title_content = _text_value(content["title"])
        if title_content:
            parts.append(f"title: {title_content}")

    header = content.get("header", {})
    if isinstance(header, dict):
        header_text = _text_value(header.get("title", {}))
        if header_text:
            parts.append(f"title: {header_text}")

    body = content.get("body", {})
    if isinstance(body, dict):
        body_elements = body.get("elements", [])
        if isinstance(body_elements, list):
            for element in body_elements:
                parts.extend(_extract_element_content(element))

    for element in content.get("elements", []) if isinstance(content.get("elements"), list) else []:
        parts.extend(_extract_element_content(element))

    card = content.get("card", {})
    if card:
        parts.extend(_extract_interactive_content(card))

    return parts


def _extract_element_content(element: dict) -> list[str]:
    """Extract content from a single card element.

    Handles various element types: markdown, div, link, button, image, etc.

    Args:
        element: The card element dict

    Returns:
        List of extracted text segments from this element
    """
    parts = []

    if not isinstance(element, dict):
        return parts

    tag = element.get("tag", "")

    if tag in ("markdown", "lark_md"):
        content = element.get("content", "")
        if content:
            parts.append(content)

    elif tag == "div":
        text_content = _text_value(element.get("text", {}))
        if text_content:
            parts.append(text_content)
        for field in element.get("fields", []):
            if isinstance(field, dict):
                c = _text_value(field.get("text", {}))
                if c:
                    parts.append(c)

    elif tag == "a":
        href = element.get("href", "")
        text = element.get("text", "")
        if href:
            parts.append(f"link: {href}")
        if text:
            parts.append(text)

    elif tag == "button":
        c = _text_value(element.get("text", {}))
        if c:
            parts.append(c)
        url = element.get("url", "") or element.get("multi_url", {}).get("url", "")
        if url:
            parts.append(f"link: {url}")

    elif tag == "img":
        alt = element.get("alt", {})
        parts.append(alt.get("content", "[image]") if isinstance(alt, dict) else "[image]")

    elif tag == "note":
        for ne in element.get("elements", []):
            parts.extend(_extract_element_content(ne))

    elif tag == "column_set":
        for col in element.get("columns", []):
            if isinstance(col, dict):
                for ce in col.get("elements", []):
                    parts.extend(_extract_element_content(ce))

    elif tag in ("action", "actions"):
        for action in element.get("actions", []):
            parts.extend(_extract_element_content(action))

    elif tag == "plain_text":
        content = element.get("content", "")
        if content:
            parts.append(content)

    else:
        for ne in element.get("elements", []):
            parts.extend(_extract_element_content(ne))
        for action in element.get("actions", []):
            parts.extend(_extract_element_content(action))

    return parts


def _extract_post_content(content_json: dict) -> tuple[str, list[str]]:
    """Extract text and image keys from Feishu post (rich text) message.

    Handles three payload shapes:
    - Direct:    {"title": "...", "content": [[...]]}
    - Localized: {"zh_cn": {"title": "...", "content": [...]}}
    - Wrapped:   {"post": {"zh_cn": {"title": "...", "content": [...]}}}

    Args:
        content_json: The post message content JSON

    Returns:
        Tuple of (text, list of image keys)
    """

    def _parse_block(block: dict) -> tuple[str | None, list[str]]:
        if not isinstance(block, dict) or not isinstance(block.get("content"), list):
            return None, []
        texts, images = [], []
        if title := block.get("title"):
            texts.append(title)
        for row in block["content"]:
            if not isinstance(row, list):
                continue
            for el in row:
                if not isinstance(el, dict):
                    continue
                tag = el.get("tag")
                if tag in ("text", "a"):
                    texts.append(el.get("text", ""))
                elif tag == "at":
                    texts.append(f"@{el.get('user_name', 'user')}")
                elif tag == "code_block":
                    lang = el.get("language", "")
                    code_text = el.get("text", "")
                    texts.append(f"\n```{lang}\n{code_text}\n```\n")
                elif tag == "img" and (key := el.get("image_key")):
                    images.append(key)
        return (" ".join(texts).strip() or None), images

    # Unwrap optional {"post": ...} envelope
    root = content_json
    if isinstance(root, dict) and isinstance(root.get("post"), dict):
        root = root["post"]
    if not isinstance(root, dict):
        return "", []

    # Direct format
    if "content" in root:
        text, imgs = _parse_block(root)
        if text or imgs:
            return text or "", imgs

    # Localized: prefer known locales, then fall back to any dict child
    for key in ("zh_cn", "en_us", "ja_jp"):
        if key in root:
            text, imgs = _parse_block(root[key])
            if text or imgs:
                return text or "", imgs
    for val in root.values():
        if isinstance(val, dict):
            text, imgs = _parse_block(val)
            if text or imgs:
                return text or "", imgs

    return "", []


def _extract_post_mention_ids(content_json: dict) -> list[str]:
    """Extract mentioned user identifiers from a Feishu post message."""

    def _parse_block(block: dict) -> list[str]:
        if not isinstance(block, dict) or not isinstance(block.get("content"), list):
            return []
        ids: list[str] = []
        for row in block["content"]:
            if not isinstance(row, list):
                continue
            for el in row:
                if not isinstance(el, dict) or el.get("tag") != "at":
                    continue
                for key in ("open_id", "user_id", "union_id"):
                    value = el.get(key)
                    if isinstance(value, str) and value:
                        ids.append(value)
        return ids

    root = content_json
    if isinstance(root, dict) and isinstance(root.get("post"), dict):
        root = root["post"]
    if not isinstance(root, dict):
        return []

    if "content" in root:
        ids = _parse_block(root)
        if ids:
            return ids

    for key in ("zh_cn", "en_us", "ja_jp"):
        if key in root:
            ids = _parse_block(root[key])
            if ids:
                return ids
    for val in root.values():
        if isinstance(val, dict):
            ids = _parse_block(val)
            if ids:
                return ids

    return []


def _extract_post_text(content_json: dict) -> str:
    """Extract plain text from Feishu post (rich text) message content.

    Legacy wrapper for _extract_post_content, returns only text.

    Args:
        content_json: The post message content JSON

    Returns:
        Extracted plain text
    """
    text, _ = _extract_post_content(content_json)
    return text
