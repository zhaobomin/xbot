#!/usr/bin/env python3
"""Test script to inspect the Todoist API SDK internals and debug issues."""

import inspect
import os
import sys

from dotenv import load_dotenv
from todoist_api_python.api import TodoistAPI


def main():
    """Run API inspection tests to debug Todoist SDK issues."""
    # Load API token from .env
    load_dotenv()
    api_token = os.getenv("TODOIST_API_TOKEN")

    if not api_token:
        print("Error: TODOIST_API_TOKEN not found in environment variables")
        sys.exit(1)

    print(f"Using API token: {api_token[:5]}...{api_token[-5:]}")

    # Initialize API client
    api = TodoistAPI(api_token)

    # Inspect add_task method
    print("\n== TodoistAPI.add_task method inspection ==")
    add_task_source = inspect.getsource(api.add_task)
    print(add_task_source)

    # Inspect get_tasks method for comparison
    print("\n== TodoistAPI.get_tasks method inspection ==")
    get_tasks_source = inspect.getsource(api.get_tasks)
    print(get_tasks_source)

    # Test base URL and endpoint construction
    print("\n== Testing URL construction ==")
    try:
        # Typically the SDK would form the URL like this
        if hasattr(api, "_url"):
            base_url = api._url
            print(f"Base URL: {base_url}")
        elif hasattr(api, "url"):
            base_url = api.url
            print(f"Base URL: {base_url}")
        else:
            print("Could not find base URL attribute in API object")
            # Try to infer it from _http attribute if available
            if hasattr(api, "_http") and hasattr(api._http, "rest_api_base_url"):
                base_url = api._http.rest_api_base_url
                print(f"Inferred Base URL: {base_url}")
            else:
                print("Could not infer base URL")
    except Exception as e:
        print(f"Error getting base URL: {str(e)}")

    print("\n== Inspecting API object attributes ==")
    print(f"API object: {api}")
    # Print all public attributes
    for attr_name in dir(api):
        if not attr_name.startswith("_"):
            try:
                attr_value = getattr(api, attr_name)
                if not callable(attr_value):
                    print(f"{attr_name}: {attr_value}")
            except Exception as e:
                print(f"Error getting {attr_name}: {str(e)}")

    # Try a simple task creation
    try:
        print("\n== Testing task creation ==")
        task = api.add_task(content="Test task from inspection script")
        print(f"Task created: {task.id} - {task.content}")
    except Exception as e:
        print(f"Error creating task: {str(e)}")
        import traceback

        print(traceback.format_exc())


if __name__ == "__main__":
    main()
