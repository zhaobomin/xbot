import requests


async def get_tasks(api_token):
    # Bug: sync Todoist-style SDK call blocks the event loop.
    r = requests.get("https://api.todoist.com/api/v1/tasks", headers={"Authorization": api_token})
    return r.json()
