"""Release version consistency tests."""

import tomllib
from pathlib import Path

import xbot


def test_runtime_version_matches_package_version() -> None:
    """Runtime surfaces must report the package version declared for release."""
    project_root = Path(__file__).resolve().parents[1]
    with (project_root / "pyproject.toml").open("rb") as file:
        package_version = tomllib.load(file)["project"]["version"]

    assert xbot.__version__ == package_version
