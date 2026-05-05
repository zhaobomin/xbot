from pathlib import Path

from xbot.runtime.core.context.builder import ContextBuilder


def test_build_system_prompt_accepts_runtime_workspace_and_execution_cwd(tmp_path: Path) -> None:
    default_workspace = tmp_path / "default-workspace"
    runtime_workspace = tmp_path / "runtime-workspace"
    runtime_cwd = tmp_path / "runtime-cwd"
    default_workspace.mkdir()
    runtime_workspace.mkdir()
    runtime_cwd.mkdir()
    (runtime_workspace / "AGENTS.md").write_text("runtime-bootstrap", encoding="utf-8")

    builder = ContextBuilder(default_workspace, execution_cwd=default_workspace, use_reme=False)

    prompt = builder.build_system_prompt(workspace=runtime_workspace, execution_cwd=runtime_cwd)

    assert f"Execution CWD: {runtime_cwd.resolve()}" in prompt
    assert f"Workspace Assets Dir: {runtime_workspace.resolve()}" in prompt
    assert "runtime-bootstrap" in prompt
