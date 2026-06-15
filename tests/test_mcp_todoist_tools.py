from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from unittest import mock

import pytest


def _load_todoist_tools_module(monkeypatch):
    fake_mcp = types.ModuleType("mcp")
    fake_server = types.ModuleType("mcp.server")
    fake_fastmcp = types.ModuleType("mcp.server.fastmcp")
    fake_fastmcp.Context = object
    fake_todoist_pkg = types.ModuleType("todoist_api_python")
    fake_api_module = types.ModuleType("todoist_api_python.api")

    class _PlaceholderTodoistAPI:
        def __init__(self, _token):
            pass

    fake_api_module.TodoistAPI = _PlaceholderTodoistAPI

    monkeypatch.setitem(sys.modules, "mcp", fake_mcp)
    monkeypatch.setitem(sys.modules, "mcp.server", fake_server)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fake_fastmcp)
    monkeypatch.setitem(sys.modules, "todoist_api_python", fake_todoist_pkg)
    monkeypatch.setitem(sys.modules, "todoist_api_python.api", fake_api_module)

    path = Path("mcp/todoist_tools.py").resolve()
    spec = importlib.util.spec_from_file_location("todoist_tools_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_create_task_uses_stored_token_not_sdk_private_attr(monkeypatch):
    module = _load_todoist_tools_module(monkeypatch)
    mock_api = mock.MagicMock()
    del mock_api._token
    fresh_api = mock.MagicMock()
    task = types.SimpleNamespace(
        id="123456789",
        content="Test task",
        description="",
        completed=False,
        labels=[],
        priority=1,
        project_id=None,
        section_id=None,
        parent_id=None,
        url="https://todoist.com/showTask?id=123456789",
        due=None,
        assignee_id=None,
    )
    fresh_api.add_task.return_value = task

    with mock.patch.object(module, "TodoistAPI", side_effect=[mock_api, fresh_api]) as api_class:
        todoist_tools = module.TodoistTools("fake_test_token")
        result = await todoist_tools.create_task("Test task")

    assert result["id"] == "123456789"
    api_class.assert_any_call("fake_test_token")


@pytest.mark.asyncio
async def test_get_tasks_uses_running_loop(monkeypatch):
    module = _load_todoist_tools_module(monkeypatch)
    fake_loop = mock.MagicMock()
    future = asyncio.Future()
    future.set_result([[types.SimpleNamespace(
        id="1",
        content="Task",
        description="",
        completed=False,
        labels=[],
        priority=1,
        project_id=None,
        section_id=None,
        parent_id=None,
        url="https://todoist.com/showTask?id=1",
        due=None,
        assignee_id=None,
    )]])
    fake_loop.run_in_executor.return_value = future
    tools = module.TodoistTools("token")
    tools.api.get_tasks = mock.MagicMock(return_value=[])

    with mock.patch.object(module.asyncio, "get_running_loop", return_value=fake_loop) as running_loop, \
         mock.patch.object(module.asyncio, "get_event_loop", side_effect=AssertionError("deprecated")):
        result = await tools.get_tasks()

    running_loop.assert_called_once()
    assert result[0]["id"] == "1"


def test_label_to_dict_uses_is_favorite(monkeypatch):
    module = _load_todoist_tools_module(monkeypatch)
    label = types.SimpleNamespace(
        id="label-1",
        name="Important",
        color="red",
        order=1,
        is_favorite=True,
    )
    tools = module.TodoistTools("token")

    result = tools._label_to_dict(label)

    assert result["favorite"] is True


def test_project_to_dict_handles_missing_optional_attrs(monkeypatch):
    module = _load_todoist_tools_module(monkeypatch)
    project = types.SimpleNamespace(id="project-1", name="Inbox")
    tools = module.TodoistTools("token")

    result = tools._project_to_dict(project)

    assert result["id"] == "project-1"
    assert result["name"] == "Inbox"
    assert result["color"] is None
    assert result["is_favorite"] is False


@pytest.mark.asyncio
async def test_sync_todoist_operations_run_in_executor(monkeypatch):
    module = _load_todoist_tools_module(monkeypatch)
    tools = module.TodoistTools("token")
    calls: list[str] = []

    async def fake_run_sdk(fn, *args, **kwargs):
        calls.append(fn.__name__)
        return fn(*args, **kwargs)

    monkeypatch.setattr(tools, "_run_sdk", fake_run_sdk)
    def close_task(_task_id):
        return True

    def get_projects():
        return [[types.SimpleNamespace(id="p1", name="Inbox")]]

    def add_project(**_kwargs):
        return types.SimpleNamespace(id="p2", name="New")

    tools.api.close_task = close_task
    tools.api.get_projects = get_projects
    tools.api.add_project = add_project

    await tools.complete_task("task-1")
    projects = await tools.get_projects()
    created = await tools.add_project("New")

    assert calls == ["close_task", "get_projects", "add_project"]
    assert projects[0]["id"] == "p1"
    assert created["id"] == "p2"
