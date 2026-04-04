from pathlib import Path

from xbot.memory.integration.api import read_workspace_memory_snapshot


def test_read_workspace_memory_snapshot_returns_index_and_topics(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "MEMORY.md").write_text("- [Rule](feedback/rule.md) — Prefer rg", encoding="utf-8")
    feedback_dir = memory_dir / "feedback"
    feedback_dir.mkdir()
    (feedback_dir / "rule.md").write_text(
        "---\nname: Rule\ndescription: Prefer rg\ntype: feedback\nupdated_at: 2026-04-04T00:00:00\n---\n\nUse rg.",
        encoding="utf-8",
    )

    snapshot = read_workspace_memory_snapshot(tmp_path)

    assert "Prefer rg" in snapshot["memory_index"]
    assert len(snapshot["topics"]) == 1
    assert snapshot["topics"][0]["path"] == "feedback/rule.md"


def test_memory_skill_docs_reference_memdir_topics_not_history() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    skill_doc = (repo_root / "xbot" / "skills" / "memory" / "SKILL.md").read_text(encoding="utf-8")
    init_skill_doc = (repo_root / "xbot" / "init_templates" / "skills" / "memory" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    for content in (skill_doc, init_skill_doc):
        assert "memory/HISTORY.md" not in content
        assert "memory/<type>/*.md" in content
        assert "MEMORY.md is an index" in content
