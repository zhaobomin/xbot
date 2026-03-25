#!/usr/bin/env python3
"""Test script to diagnose Todoist API SDK issues."""

import os
import sys

from dotenv import load_dotenv
from todoist_api_python.api import TodoistAPI


def main():
    """Run API diagnostic tests to verify Todoist SDK functionality."""
    # Load API token from .env
    load_dotenv()
    api_token = os.getenv("TODOIST_API_TOKEN")

    if not api_token:
        print("Error: TODOIST_API_TOKEN not found in environment variables")
        sys.exit(1)

    print(f"Using API token: {api_token[:5]}...{api_token[-5:]}")

    # Initialize API client
    api = TodoistAPI(api_token)

    try:
        # Test GET request - List tasks
        print("\nTesting GET /tasks...")
        tasks = list(api.get_tasks())
        print(f"Success! Retrieved {len(tasks)} tasks")
        if tasks:
            print(f"First task: {tasks[0].content}")
    except Exception as e:
        print(f"Error listing tasks: {str(e)}")

    try:
        # Test POST request - Create task
        print("\nTesting POST /tasks (add_task)...")
        task_data = {"content": "Test task from SDK"}
        print(f"Task data: {task_data}")

        task = api.add_task(**task_data)
        print(f"Success! Created task: {task.id} - {task.content}")
    except Exception as e:
        print(f"Error creating task: {str(e)}")
        import traceback

        print(traceback.format_exc())


if __name__ == "__main__":
    main()
