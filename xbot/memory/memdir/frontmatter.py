from __future__ import annotations


def parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    """Parse YAML-style frontmatter from a memory document.

    Returns (metadata_dict, body_text). If the document has no valid
    frontmatter block, returns an empty dict and the original text.
    """
    if not raw.startswith("---\n"):
        return {}, raw
    parts = raw.split("---\n", 2)
    if len(parts) < 3:
        return {}, raw
    _, block, body = parts
    data: dict[str, str] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()
    return data, body.strip()
