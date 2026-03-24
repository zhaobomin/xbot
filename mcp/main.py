"""
MCP-Todoist Integration.

This is the main entry point for the MCP server that integrates with Todoist.
It sets up the server and registers the tools and resources.
"""

from typing import Any, Dict, List, Optional, Tuple

from mcp.server.fastmcp import Context, FastMCP

from config import load_config
from todoist_resources import TodoistResources
from todoist_tools import TodoistTools


def create_server() -> FastMCP:
    """
    Create and configure the MCP server.

    Returns:
        FastMCP: Configured MCP server
    """
    # Load configuration
    config = load_config()

    # Create MCP server
    server = FastMCP(
        config.server_name,
        # List dependencies for installation without version constraints
        dependencies=[
            "todoist-api-python",
            "pydantic",
            "python-dotenv",
        ],
    )

    # Initialize Todoist clients
    todoist_tools = TodoistTools(config.todoist.api_token)
    todoist_resources = TodoistResources(config.todoist.api_token)

    # Register Todoist tools

    @server.tool()
    async def create_task(
        content: str,
        description: Optional[str] = None,
        due_string: Optional[str] = None,
        due_date: Optional[str] = None,
        due_datetime: Optional[str] = None,
        due_lang: Optional[str] = None,
        priority: Optional[int] = None,
        project_id: Optional[str] = None,
        section_id: Optional[str] = None,
        labels: Optional[List[str]] = None,
        parent_id: Optional[str] = None,
        assignee_id: Optional[str] = None,
        day_order: Optional[int] = None,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """
        Create a new task in Todoist.

        Args:
            content: The content/title of the task
            description: Detailed description of the task (optional)
            due_string: Natural language due date like 'tomorrow', 'next Monday'
                (optional)
            due_date: Due date in YYYY-MM-DD format (optional)
            due_datetime: Due date with time in RFC3339 format (optional)
            due_lang: Language for parsing due_string, e.g., 'en', 'fr' (optional)
            priority: Task priority from 1 (normal) to 4 (urgent) (optional)
            project_id: ID of the project to add the task to (optional)
            section_id: ID of the section to add the task to (optional)
            labels: List of label names to apply to the task (optional)
            parent_id: ID of the parent task for subtasks (optional)
            assignee_id: User ID to whom the task is assigned (optional)
            day_order: Task order in Today or Next 7 days view (optional)
            ctx: MCP context (injected automatically)

        Returns:
            Dictionary containing task data
        """
        return await todoist_tools.create_task(
            content=content,
            description=description,
            due_string=due_string,
            due_date=due_date,
            due_datetime=due_datetime,
            due_lang=due_lang,
            priority=priority,
            project_id=project_id,
            section_id=section_id,
            labels=labels,
            parent_id=parent_id,
            assignee_id=assignee_id,
            day_order=day_order,
            ctx=ctx,
        )

    @server.tool()
    async def get_tasks(
        project_id: Optional[str] = None,
        section_id: Optional[str] = None,
        label: Optional[str] = None,
        filter_query: Optional[str] = None,
        ctx: Context = None,
    ) -> List[Dict[str, Any]]:
        """
        Get tasks from Todoist based on filters.

        Args:
            project_id: Filter tasks by project ID (optional)
            section_id: Filter tasks by section ID (optional)
            label: Filter tasks by label name (optional)
            filter_query: Filter tasks using Todoist's filter language (optional)
            ctx: MCP context (injected automatically)

        Returns:
            List of task dictionaries
        """
        return await todoist_tools.get_tasks(
            project_id=project_id,
            section_id=section_id,
            label=label,
            filter_query=filter_query,
            ctx=ctx,
        )

    @server.tool()
    async def get_task(
        task_id: str,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """
        Get a specific task by ID.

        Args:
            task_id: ID of the task to retrieve
            ctx: MCP context (injected automatically)

        Returns:
            Task dictionary
        """
        return await todoist_tools.get_task(
            task_id=task_id,
            ctx=ctx,
        )

    @server.tool()
    async def update_task(
        task_id: str,
        content: Optional[str] = None,
        description: Optional[str] = None,
        due_string: Optional[str] = None,
        due_date: Optional[str] = None,
        due_datetime: Optional[str] = None,
        due_lang: Optional[str] = None,
        priority: Optional[int] = None,
        labels: Optional[List[str]] = None,
        assignee_id: Optional[str] = None,
        day_order: Optional[int] = None,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """
        Update an existing task.

        Args:
            task_id: ID of the task to update
            content: New task content/title (optional)
            description: New task description (optional)
            due_string: New due date in natural language (optional)
            due_date: New due date in YYYY-MM-DD format (optional)
            due_datetime: New due date with time in RFC3339 format (optional)
            due_lang: Language for parsing due_string, e.g., 'en', 'fr' (optional)
            priority: New priority level from 1 (normal) to 4 (urgent) (optional)
            labels: List of label names to apply to the task (optional)
            assignee_id: User ID to whom the task is assigned (optional)
            day_order: Task order in Today or Next 7 days view (optional)
            ctx: MCP context (injected automatically)

        Returns:
            Updated task dictionary
        """
        return await todoist_tools.update_task(
            task_id=task_id,
            content=content,
            description=description,
            due_string=due_string,
            due_date=due_date,
            due_datetime=due_datetime,
            due_lang=due_lang,
            priority=priority,
            labels=labels,
            assignee_id=assignee_id,
            day_order=day_order,
            ctx=ctx,
        )

    @server.tool()
    async def complete_task(
        task_id: str,
        ctx: Context = None,
    ) -> Dict[str, str]:
        """
        Complete a task.

        Args:
            task_id: ID of the task to complete
            ctx: MCP context (injected automatically)

        Returns:
            Dictionary with status information
        """
        return await todoist_tools.complete_task(
            task_id=task_id,
            ctx=ctx,
        )

    @server.tool()
    async def delete_task(
        task_id: str,
        ctx: Context = None,
    ) -> Dict[str, str]:
        """
        Delete a task.

        Args:
            task_id: ID of the task to delete
            ctx: MCP context (injected automatically)

        Returns:
            Dictionary with status information
        """
        return await todoist_tools.delete_task(
            task_id=task_id,
            ctx=ctx,
        )

    @server.tool()
    async def get_projects(
        ctx: Context = None,
    ) -> List[Dict[str, Any]]:
        """
        Get all projects.

        Args:
            ctx: MCP context (injected automatically)

        Returns:
            List of project dictionaries
        """
        return await todoist_tools.get_projects(
            ctx=ctx,
        )

    # Add new tools here

    @server.tool()
    async def uncomplete_task(
        task_id: str,
        ctx: Context = None,
    ) -> Dict[str, str]:
        """
        Uncomplete a task.

        Args:
            task_id: ID of the task to uncomplete
            ctx: MCP context (injected automatically)

        Returns:
            Dictionary with status information
        """
        return await todoist_tools.uncomplete_task(
            task_id=task_id,
            ctx=ctx,
        )

    @server.tool()
    async def add_project(
        name: str,
        parent_id: Optional[str] = None,
        color: Optional[str] = None,
        is_favorite: Optional[bool] = None,
        view_style: Optional[str] = None,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """
        Create a new project.

        Args:
            name: Name of the project
            parent_id: ID of the parent project for nested projects (optional)
            color: Color for the project (optional)
            is_favorite: Whether the project is a favorite (optional)
            view_style: Style of the project view (optional)
            ctx: MCP context (injected automatically)

        Returns:
            Project dictionary
        """
        return await todoist_tools.add_project(
            name=name,
            parent_id=parent_id,
            color=color,
            is_favorite=is_favorite,
            view_style=view_style,
            ctx=ctx,
        )

    @server.tool()
    async def get_project(
        project_id: str,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """
        Get a specific project by ID.

        Args:
            project_id: ID of the project to retrieve
            ctx: MCP context (injected automatically)

        Returns:
            Project dictionary
        """
        return await todoist_tools.get_project(
            project_id=project_id,
            ctx=ctx,
        )

    @server.tool()
    async def update_project(
        project_id: str,
        name: Optional[str] = None,
        color: Optional[str] = None,
        is_favorite: Optional[bool] = None,
        view_style: Optional[str] = None,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """
        Update an existing project.

        Args:
            project_id: ID of the project to update
            name: New name for the project (optional)
            color: New color for the project (optional)
            is_favorite: Whether the project is a favorite (optional)
            view_style: New style for the project view (optional)
            ctx: MCP context (injected automatically)

        Returns:
            Updated project dictionary
        """
        return await todoist_tools.update_project(
            project_id=project_id,
            name=name,
            color=color,
            is_favorite=is_favorite,
            view_style=view_style,
            ctx=ctx,
        )

    @server.tool()
    async def delete_project(
        project_id: str,
        ctx: Context = None,
    ) -> Dict[str, str]:
        """
        Delete a project.

        Args:
            project_id: ID of the project to delete
            ctx: MCP context (injected automatically)

        Returns:
            Dictionary with status information
        """
        return await todoist_tools.delete_project(
            project_id=project_id,
            ctx=ctx,
        )

    @server.tool()
    async def archive_project(
        project_id: str,
        ctx: Context = None,
    ) -> Dict[str, str]:
        """
        Archive a project.

        Args:
            project_id: ID of the project to archive
            ctx: MCP context (injected automatically)

        Returns:
            Dictionary with status information
        """
        return await todoist_tools.archive_project(
            project_id=project_id,
            ctx=ctx,
        )

    @server.tool()
    async def unarchive_project(
        project_id: str,
        ctx: Context = None,
    ) -> Dict[str, str]:
        """
        Unarchive a project.

        Args:
            project_id: ID of the project to unarchive
            ctx: MCP context (injected automatically)

        Returns:
            Dictionary with status information
        """
        return await todoist_tools.unarchive_project(
            project_id=project_id,
            ctx=ctx,
        )

    @server.tool()
    async def get_sections(
        project_id: Optional[str] = None,
        ctx: Context = None,
    ) -> List[Dict[str, Any]]:
        """
        Get sections.

        Args:
            project_id: Filter by project ID (optional)
            ctx: MCP context (injected automatically)

        Returns:
            List of section dictionaries
        """
        return await todoist_tools.get_sections(
            project_id=project_id,
            ctx=ctx,
        )

    @server.tool()
    async def get_section(
        section_id: str,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """
        Get a specific section by ID.

        Args:
            section_id: ID of the section to retrieve
            ctx: MCP context (injected automatically)

        Returns:
            Section dictionary
        """
        return await todoist_tools.get_section(
            section_id=section_id,
            ctx=ctx,
        )

    @server.tool()
    async def add_section(
        name: str,
        project_id: str,
        order: Optional[int] = None,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """
        Create a new section.

        Args:
            name: Name of the section
            project_id: ID of the project to add the section to
            order: Order of the section within the project (optional)
            ctx: MCP context (injected automatically)

        Returns:
            Section dictionary
        """
        return await todoist_tools.add_section(
            name=name,
            project_id=project_id,
            order=order,
            ctx=ctx,
        )

    @server.tool()
    async def update_section(
        section_id: str,
        name: str,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """
        Update an existing section.

        Args:
            section_id: ID of the section to update
            name: New name for the section
            ctx: MCP context (injected automatically)

        Returns:
            Updated section dictionary
        """
        return await todoist_tools.update_section(
            section_id=section_id,
            name=name,
            ctx=ctx,
        )

    @server.tool()
    async def delete_section(
        section_id: str,
        ctx: Context = None,
    ) -> Dict[str, str]:
        """
        Delete a section.

        Args:
            section_id: ID of the section to delete
            ctx: MCP context (injected automatically)

        Returns:
            Dictionary with status information
        """
        return await todoist_tools.delete_section(
            section_id=section_id,
            ctx=ctx,
        )

    @server.tool()
    async def get_labels(
        ctx: Context = None,
    ) -> List[Dict[str, Any]]:
        """
        Get all labels.

        Args:
            ctx: MCP context (injected automatically)

        Returns:
            List of label dictionaries
        """
        return await todoist_tools.get_labels(
            ctx=ctx,
        )

    @server.tool()
    async def get_label(
        label_id: str,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """
        Get a specific label by ID.

        Args:
            label_id: ID of the label to retrieve
            ctx: MCP context (injected automatically)

        Returns:
            Label dictionary
        """
        return await todoist_tools.get_label(
            label_id=label_id,
            ctx=ctx,
        )

    @server.tool()
    async def add_label(
        name: str,
        color: Optional[str] = None,
        favorite: Optional[bool] = None,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """
        Create a new label.

        Args:
            name: Name of the label
            color: Color for the label (optional)
            favorite: Whether the label is a favorite (optional)
            ctx: MCP context (injected automatically)

        Returns:
            Label dictionary
        """
        return await todoist_tools.add_label(
            name=name,
            color=color,
            favorite=favorite,
            ctx=ctx,
        )

    @server.tool()
    async def update_label(
        label_id: str,
        name: Optional[str] = None,
        color: Optional[str] = None,
        favorite: Optional[bool] = None,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """
        Update an existing label.

        Args:
            label_id: ID of the label to update
            name: New name for the label (optional)
            color: New color for the label (optional)
            favorite: Whether the label is a favorite (optional)
            ctx: MCP context (injected automatically)

        Returns:
            Updated label dictionary
        """
        return await todoist_tools.update_label(
            label_id=label_id,
            name=name,
            color=color,
            favorite=favorite,
            ctx=ctx,
        )

    @server.tool()
    async def delete_label(
        label_id: str,
        ctx: Context = None,
    ) -> Dict[str, str]:
        """
        Delete a label.

        Args:
            label_id: ID of the label to delete
            ctx: MCP context (injected automatically)

        Returns:
            Dictionary with status information
        """
        return await todoist_tools.delete_label(
            label_id=label_id,
            ctx=ctx,
        )

    @server.tool()
    async def get_comments(
        task_id: Optional[str] = None,
        project_id: Optional[str] = None,
        ctx: Context = None,
    ) -> List[Dict[str, Any]]:
        """
        Get comments for a task or project.

        Args:
            task_id: ID of the task to get comments for (optional)
            project_id: ID of the project to get comments for (optional)
            ctx: MCP context (injected automatically)

        Returns:
            List of comment dictionaries
        """
        return await todoist_tools.get_comments(
            task_id=task_id,
            project_id=project_id,
            ctx=ctx,
        )

    @server.tool()
    async def get_comment(
        comment_id: str,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """
        Get a specific comment by ID.

        Args:
            comment_id: ID of the comment to retrieve
            ctx: MCP context (injected automatically)

        Returns:
            Comment dictionary
        """
        return await todoist_tools.get_comment(
            comment_id=comment_id,
            ctx=ctx,
        )

    @server.tool()
    async def add_comment(
        content: str,
        task_id: Optional[str] = None,
        project_id: Optional[str] = None,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """
        Add a comment to a task or project.

        Args:
            content: Content of the comment
            task_id: ID of the task to add comment to (optional)
            project_id: ID of the project to add comment to (optional)
            ctx: MCP context (injected automatically)

        Returns:
            Comment dictionary
        """
        return await todoist_tools.add_comment(
            content=content,
            task_id=task_id,
            project_id=project_id,
            ctx=ctx,
        )

    @server.tool()
    async def update_comment(
        comment_id: str,
        content: str,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """
        Update an existing comment.

        Args:
            comment_id: ID of the comment to update
            content: New content for the comment
            ctx: MCP context (injected automatically)

        Returns:
            Updated comment dictionary
        """
        return await todoist_tools.update_comment(
            comment_id=comment_id,
            content=content,
            ctx=ctx,
        )

    @server.tool()
    async def delete_comment(
        comment_id: str,
        ctx: Context = None,
    ) -> Dict[str, str]:
        """
        Delete a comment.

        Args:
            comment_id: ID of the comment to delete
            ctx: MCP context (injected automatically)

        Returns:
            Dictionary with status information
        """
        return await todoist_tools.delete_comment(
            comment_id=comment_id,
            ctx=ctx,
        )

    @server.tool()
    async def get_collaborators(
        project_id: str,
        ctx: Context = None,
    ) -> List[Dict[str, Any]]:
        """
        Get collaborators for a project.

        Args:
            project_id: ID of the project to get collaborators for
            ctx: MCP context (injected automatically)

        Returns:
            List of collaborator dictionaries
        """
        return await todoist_tools.get_collaborators(
            project_id=project_id,
            ctx=ctx,
        )

    # Register Todoist resources

    @server.resource("todoist://tasks")
    async def tasks_resource() -> Tuple[str, str]:
        """
        Get all tasks as a resource.

        Returns:
            Tuple of (data, mime_type)
        """
        return await todoist_resources.get_tasks_resource()

    @server.resource("todoist://tasks/project/{project_id}")
    async def project_tasks_resource(project_id: str) -> Tuple[str, str]:
        """
        Get tasks for a specific project as a resource.

        Args:
            project_id: ID of the project to get tasks for

        Returns:
            Tuple of (data, mime_type)
        """
        return await todoist_resources.get_tasks_resource(
            project_id=project_id,
        )

    @server.resource("todoist://tasks/section/{section_id}")
    async def section_tasks_resource(section_id: str) -> Tuple[str, str]:
        """
        Get tasks for a specific section as a resource.

        Args:
            section_id: ID of the section to get tasks for

        Returns:
            Tuple of (data, mime_type)
        """
        return await todoist_resources.get_tasks_resource(
            section_id=section_id,
        )

    @server.resource("todoist://tasks/label/{label}")
    async def label_tasks_resource(label: str) -> Tuple[str, str]:
        """
        Get tasks with a specific label as a resource.

        Args:
            label: Label name to get tasks for

        Returns:
            Tuple of (data, mime_type)
        """
        return await todoist_resources.get_tasks_resource(
            label=label,
        )

    @server.resource("todoist://projects")
    async def projects_resource() -> Tuple[str, str]:
        """
        Get all projects as a resource.

        Returns:
            Tuple of (data, mime_type)
        """
        return await todoist_resources.get_projects_resource()

    @server.resource("todoist://sections/{project_id}")
    async def sections_resource(project_id: str) -> Tuple[str, str]:
        """
        Get sections for a project as a resource.

        Args:
            project_id: ID of the project to get sections for

        Returns:
            Tuple of (data, mime_type)
        """
        return await todoist_resources.get_sections_resource(
            project_id=project_id,
        )

    @server.resource("todoist://labels")
    async def labels_resource() -> Tuple[str, str]:
        """
        Get all labels as a resource.

        Returns:
            Tuple of (data, mime_type)
        """
        return await todoist_resources.get_labels_resource()

    # Add some helpful prompts

    @server.prompt()
    def create_task_prompt(content: str, due_date: Optional[str] = None) -> str:
        """
        Prompt to create a new task.

        Args:
            content: Task content/title
            due_date: Optional due date in natural language
        """
        prompt = f"Please create a new task titled '{content}'"
        if due_date:
            prompt += f" due {due_date}"
        prompt += "."
        return prompt

    @server.prompt()
    def show_tasks_prompt() -> str:
        """Prompt to show all tasks."""
        return "Please show me my current tasks in Todoist."

    @server.prompt()
    def complete_task_prompt(task_name: str) -> str:
        """
        Prompt to complete a task by name.

        Args:
            task_name: Name of the task to complete
        """
        return f"Please mark the task '{task_name}' as complete."

    return server


def main():
    """Start the MCP-Todoist server."""
    try:
        # Run the server
        server.run()
    except Exception as e:
        print(f"Error starting MCP server: {str(e)}")
        import traceback

        traceback.print_exc()
        return 1
    return 0


# Create the server instance at module level
server = create_server()

if __name__ == "__main__":
    exit(main())
