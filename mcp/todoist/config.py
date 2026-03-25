"""
Configuration management for the MCP-Todoist integration.

This module handles loading and validating configuration settings,
including the Todoist API token.
"""

import os

from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load environment variables from .env file if it exists
load_dotenv()


class TodoistConfig(BaseModel):
    """Configuration for Todoist API."""

    api_token: str = Field(
        description="Todoist API token for authentication",
    )


class Config(BaseModel):
    """Main configuration for the MCP-Todoist integration."""

    todoist: TodoistConfig
    server_name: str = Field(
        default="Todoist MCP",
        description="Name of the MCP server",
    )


def load_config() -> Config:
    """
    Load configuration from environment variables.

    Returns:
        Config: Configuration object with validated settings

    Raises:
        ValueError: If required configuration values are missing
    """
    # Get Todoist API token from environment
    api_token = os.getenv("TODOIST_API_TOKEN")

    if not api_token:
        raise ValueError(
            "Todoist API token not found. Please set the TODOIST_API_TOKEN "
            "environment variable or add it to a .env file."
        )

    # Create Todoist config
    todoist_config = TodoistConfig(api_token=api_token)

    # Get server name from environment or use default
    server_name = os.getenv("MCP_SERVER_NAME", "Todoist MCP")

    # Create and return main config
    return Config(
        todoist=todoist_config,
        server_name=server_name,
    )
