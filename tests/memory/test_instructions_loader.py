from pathlib import Path

from xbot.memory.instructions.loader import InstructionLoader


def test_instruction_loader_loads_workspace_sources_in_priority_order(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("root", encoding="utf-8")
    rules_dir = tmp_path / ".claude" / "rules"
    rules_dir.mkdir(parents=True)
    (tmp_path / ".claude" / "CLAUDE.md").write_text("dotclaude", encoding="utf-8")
    (rules_dir / "alpha.md").write_text("alpha", encoding="utf-8")
    (tmp_path / "CLAUDE.local.md").write_text("local", encoding="utf-8")

    files = InstructionLoader(tmp_path).get_instruction_files()

    assert [f.path.name for f in files] == [
        "CLAUDE.md",
        "CLAUDE.md",
        "alpha.md",
        "CLAUDE.local.md",
    ]
    assert [f.content.strip() for f in files] == ["root", "dotclaude", "alpha", "local"]


def test_instruction_loader_resolves_includes_and_skips_code_blocks(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "shared.md").write_text("shared text", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text(
        "before\n@include docs/shared.md\n```md\n@include docs/shared.md\n```\nafter",
        encoding="utf-8",
    )

    files = InstructionLoader(tmp_path).get_instruction_files()

    assert [f.path.name for f in files] == ["CLAUDE.md", "shared.md"]
    assert files[1].content.strip() == "shared text"


def test_instruction_loader_filters_conditional_rules_by_target_path(tmp_path: Path) -> None:
    rules_dir = tmp_path / ".claude" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "python.md").write_text(
        "---\npaths: src/**/*.py\n---\npython rule",
        encoding="utf-8",
    )
    (rules_dir / "global.md").write_text("global rule", encoding="utf-8")

    matched = InstructionLoader(tmp_path).get_instruction_files_for_path(tmp_path / "src" / "app.py")
    unmatched = InstructionLoader(tmp_path).get_instruction_files_for_path(tmp_path / "web" / "app.ts")

    assert [f.path.name for f in matched] == ["global.md", "python.md"]
    assert [f.path.name for f in unmatched] == ["global.md"]
