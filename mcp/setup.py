#!/usr/bin/env python3
"""Setup script for mcp-todoist package."""

from setuptools import find_packages, setup

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="mcp-todoist",
    use_scm_version=True,
    author="",
    author_email="",
    description="Model Context Protocol (MCP) server for Todoist integration",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/stevengonsalvez/mcp-todoist",
    packages=find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.10",
    install_requires=[
        "todoist-api-python",
        "mcp-server>=0.1.4",
        "pydantic",
        "python-dotenv",
        "aiohttp",
    ],
    extras_require={
        "dev": [
            "black",
            "flake8",
            "isort",
            "pytest",
            "pytest-asyncio",
            "pre-commit",
        ],
    },
    entry_points={
        "console_scripts": [
            "mcp-todoist=main:main",
        ],
    },
)
