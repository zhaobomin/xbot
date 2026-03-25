"""
Resource implementation for Todoist data.

This module defines the MCP resources for accessing and displaying
Todoist data such as tasks, projects, and labels.
"""

import json
from typing import Optional, Tuple

from mcp.server.fastmcp import Context
from todoist_api_python.api import TodoistAPI


class TodoistResources:
    """Implements Todoist data access as MCP resources."""

    def __init__(self, api_token: str):
        """
        Initialize TodoistResources with Todoist API client.

        Args:
            api_token: Todoist API token for authentication
        """
        self.api = TodoistAPI(api_token)

    async def get_tasks_resource(
        self,
        project_id: Optional[str] = None,
        section_id: Optional[str] = None,
        label: Optional[str] = None,
        ctx: Optional[Context] = None,
    ) -> Tuple[str, str]:
        """
        Get tasks as a resource.

        Args:
            project_id: Filter tasks by project ID (optional)
            section_id: Filter tasks by section ID (optional)
            label: Filter tasks by label name (optional)
            ctx: MCP context (optional)

        Returns:
            Tuple of (data, mime_type)
        """
        # Log action if context is provided
        if ctx:
            ctx.info("Accessing Todoist tasks resource")

        try:
            # Prepare filter parameters
            kwargs = {}
            if project_id:
                kwargs["project_id"] = project_id
            if section_id:
                kwargs["section_id"] = section_id
            if label:
                kwargs["label"] = label

            # Get tasks
            tasks_iterator = self.api.get_tasks(**kwargs)
            tasks_list = list(tasks_iterator)

            # Convert tasks to dictionaries
            tasks_data = []
            for task in tasks_list:
                # Safely build task dictionary by checking each attribute
                task_dict = {}
                for attr in [
                    "id",
                    "content",
                    "description",
                    "url",
                    "created_at",
                    "priority",
                    "project_id",
                    "section_id",
                    "parent_id",
                ]:
                    if hasattr(task, attr):
                        task_dict[attr] = getattr(task, attr)

                # Handle labels
                if hasattr(task, "labels"):
                    task_dict["labels"] = task.labels
                elif hasattr(task, "label_ids"):
                    task_dict["label_ids"] = task.label_ids

                # Handle due date safely
                if task.due:
                    try:
                        due_dict = {}
                        for attr in [
                            "date",
                            "string",
                            "is_recurring",
                            "datetime",
                            "timezone",
                        ]:
                            if hasattr(task.due, attr):
                                due_dict[attr] = getattr(task.due, attr)
                        task_dict["due"] = due_dict
                    except Exception:
                        task_dict["due"] = None
                else:
                    task_dict["due"] = None

                tasks_data.append(task_dict)

            # Format as a readable markdown table
            if tasks_data:
                markdown = "# Todoist Tasks\n\n"
                markdown += "| ID | Task | Due | Priority |\n"
                markdown += "|:---|:-----|:----|:--------|\n"

                for task in tasks_data:
                    due_str = "None"
                    if task["due"]:
                        due_date = task["due"].get("date", "")
                        due_str = due_date

                    priority_map = {1: "Normal", 2: "Medium", 3: "High", 4: "Urgent"}
                    priority_str = priority_map.get(task["priority"], "Normal")

                    task_id = task["id"]
                    task_content = task["content"]
                    markdown += (
                        f"| {task_id} | {task_content} | {due_str} | {priority_str} |\n"
                    )

                # Add JSON data at the end for reference
                markdown += (
                    "\n\n<details>\n<summary>Raw Data (Click to expand)</summary>\n\n"
                )
                markdown += "```json\n"
                markdown += json.dumps(tasks_data, indent=2)
                markdown += "\n```\n</details>\n"

                return markdown, "text/markdown"
            else:
                return "No tasks found.", "text/plain"
        except Exception as e:
            # Log error if context is provided
            if ctx:
                ctx.error(f"Failed to access Todoist tasks resource: {str(e)}")
            return f"Error accessing Todoist tasks: {str(e)}", "text/plain"

    async def get_projects_resource(
        self,
        ctx: Optional[Context] = None,
    ) -> Tuple[str, str]:
        """
        Get projects as a resource.

        Args:
            ctx: MCP context (optional)

        Returns:
            Tuple of (data, mime_type)
        """
        # Log action if context is provided
        if ctx:
            ctx.info("Accessing Todoist projects resource")

        try:
            # Get all projects
            projects = self.api.get_projects()

            # Convert projects to dictionaries
            projects_data = [
                {
                    "id": project.id,
                    "name": project.name,
                    "color": project.color,
                    "is_favorite": project.is_favorite,
                    "is_inbox_project": project.is_inbox_project,
                    "order": project.order,
                    "parent_id": project.parent_id,
                    "url": project.url,
                }
                for project in projects
            ]

            # Format as a readable markdown table
            if projects_data:
                markdown = "# Todoist Projects\n\n"
                markdown += "| ID | Project Name | Is Favorite | Is Inbox |\n"
                markdown += "|:---|:------------|:------------|:--------|\n"

                for project in projects_data:
                    favorite = "★" if project["is_favorite"] else ""
                    inbox = "✓" if project["is_inbox_project"] else ""

                    project_id = project["id"]
                    project_name = project["name"]
                    markdown += (
                        f"| {project_id} | {project_name} | {favorite} | {inbox} |\n"
                    )

                # Add JSON data at the end for reference
                markdown += (
                    "\n\n<details>\n<summary>Raw Data (Click to expand)</summary>\n\n"
                )
                markdown += "```json\n"
                markdown += json.dumps(projects_data, indent=2)
                markdown += "\n```\n</details>\n"

                return markdown, "text/markdown"
            else:
                return "No projects found.", "text/plain"
        except Exception as e:
            # Log error if context is provided
            if ctx:
                ctx.error(f"Failed to access Todoist projects resource: {str(e)}")
            return f"Error accessing Todoist projects: {str(e)}", "text/plain"

    async def get_sections_resource(
        self,
        project_id: str,
        ctx: Optional[Context] = None,
    ) -> Tuple[str, str]:
        """
        Get sections for a project as a resource.

        Args:
            project_id: ID of the project to get sections for
            ctx: MCP context (optional)

        Returns:
            Tuple of (data, mime_type)
        """
        # Log action if context is provided
        if ctx:
            ctx.info(f"Accessing Todoist sections resource for project {project_id}")

        try:
            # Get sections for the project
            sections = self.api.get_sections(project_id=project_id)

            # Convert sections to dictionaries
            sections_data = [
                {
                    "id": section.id,
                    "name": section.name,
                    "order": section.order,
                    "project_id": section.project_id,
                }
                for section in sections
            ]

            # Format as a readable markdown table
            if sections_data:
                markdown = f"# Sections for Project {project_id}\n\n"
                markdown += "| ID | Section Name | Order |\n"
                markdown += "|:---|:------------|:-----|\n"

                for section in sections_data:
                    section_id = section["id"]
                    section_name = section["name"]
                    section_order = section["order"]
                    markdown += f"| {section_id} | {section_name} | {section_order} |\n"

                # Add JSON data at the end for reference
                markdown += (
                    "\n\n<details>\n<summary>Raw Data (Click to expand)</summary>\n\n"
                )
                markdown += "```json\n"
                markdown += json.dumps(sections_data, indent=2)
                markdown += "\n```\n</details>\n"

                return markdown, "text/markdown"
            else:
                return f"No sections found for project {project_id}.", "text/plain"
        except Exception as e:
            # Log error if context is provided
            if ctx:
                ctx.error(f"Failed to access Todoist sections resource: {str(e)}")
            return f"Error accessing Todoist sections: {str(e)}", "text/plain"

    async def get_labels_resource(
        self,
        ctx: Optional[Context] = None,
    ) -> Tuple[str, str]:
        """
        Get all labels as a resource.

        Args:
            ctx: MCP context (optional)

        Returns:
            Tuple of (data, mime_type)
        """
        # Log action if context is provided
        if ctx:
            ctx.info("Accessing Todoist labels resource")

        try:
            # Get all labels
            labels = self.api.get_labels()

            # Convert labels to dictionaries
            labels_data = [
                {
                    "id": label.id,
                    "name": label.name,
                    "color": label.color,
                    "order": label.order,
                    "is_favorite": label.is_favorite,
                }
                for label in labels
            ]

            # Format as a readable markdown table
            if labels_data:
                markdown = "# Todoist Labels\n\n"
                markdown += "| ID | Label Name | Color | Is Favorite |\n"
                markdown += "|:---|:-----------|:------|:-----------|\n"

                for label in labels_data:
                    favorite = "★" if label["is_favorite"] else ""

                    label_id = label["id"]
                    label_name = label["name"]
                    label_color = label["color"]
                    markdown += (
                        f"| {label_id} | {label_name} | {label_color} | {favorite} |\n"
                    )

                # Add JSON data at the end for reference
                markdown += (
                    "\n\n<details>\n<summary>Raw Data (Click to expand)</summary>\n\n"
                )
                markdown += "```json\n"
                markdown += json.dumps(labels_data, indent=2)
                markdown += "\n```\n</details>\n"

                return markdown, "text/markdown"
            else:
                return "No labels found.", "text/plain"
        except Exception as e:
            # Log error if context is provided
            if ctx:
                ctx.error(f"Failed to access Todoist labels resource: {str(e)}")
            return f"Error accessing Todoist labels: {str(e)}", "text/plain"
