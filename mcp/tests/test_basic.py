"""Basic tests for mcp-todoist."""

import os
from unittest import mock

import pytest
from todoist_api_python.models import Task


def test_import():
    """Test that we can import the main module."""
    import main

    assert main is not None


@pytest.fixture
def mock_env_token():
    """Mock environment variables for testing."""
    with mock.patch.dict(os.environ, {"TODOIST_API_TOKEN": "fake_test_token"}):
        yield


@pytest.fixture
def mock_task():
    """Create a mock Task object."""
    mock_task = mock.MagicMock(spec=Task)
    mock_task.id = "123456789"
    mock_task.content = "Test task"
    mock_task.description = "Test description"
    mock_task.completed = False
    mock_task.labels = ["test-label"]
    mock_task.priority = 1
    mock_task.project_id = "project123"
    return mock_task


@pytest.mark.asyncio
async def test_get_tasks(mock_env_token, mock_task):
    """Test getting tasks from Todoist."""
    # Import the module we'll patch
    from todoist_tools import TodoistTools

    # Create a mock API instance
    mock_api = mock.MagicMock()
    mock_api.get_tasks.return_value = [mock_task]

    # Patch the TodoistAPI class where it's imported in TodoistTools
    with mock.patch("todoist_tools.TodoistAPI") as mock_todoist_api_class:
        # Configure the mock class to return our mock API instance
        mock_todoist_api_class.return_value = mock_api

        # Now when TodoistTools creates a TodoistAPI, it will get our mock
        todoist_tools = TodoistTools("fake_test_token")

        # Call the method we're testing
        tasks = await todoist_tools.get_tasks()

        # Verify the mock was used correctly
        mock_api.get_tasks.assert_called_once_with()

        # Check that we got the expected task data
        assert len(tasks) == 1
        assert tasks[0]["id"] == "123456789"
        assert tasks[0]["content"] == "Test task"
        assert "test-label" in tasks[0]["labels"]
