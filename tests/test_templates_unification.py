from __future__ import annotations

from pathlib import Path


def test_runtime_code_no_init_templates_reference() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    offenders: list[Path] = []

    for path in (repo_root / "xbot").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "init_templates" in text:
            offenders.append(path.relative_to(repo_root))

    assert not offenders, f"Found init_templates references in runtime code: {offenders}"
