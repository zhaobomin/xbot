from __future__ import annotations

import tomllib
from pathlib import Path


def test_bridge_wheel_force_include_is_file_scoped() -> None:
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    force_include = config["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]

    assert "bridge" not in force_include
    assert force_include == {
        "bridge/package.json": "xbot/bridge/package.json",
        "bridge/package-lock.json": "xbot/bridge/package-lock.json",
        "bridge/tsconfig.json": "xbot/bridge/tsconfig.json",
        "bridge/src": "xbot/bridge/src",
    }


def test_webui_distribution_is_versioned_for_clean_source_builds() -> None:
    frontend_dist = Path("xbot/interfaces/webui/frontend/dist")
    dev_dist = Path("xbot/interfaces/webui/frontend/dev-dist")

    assert (frontend_dist / "index.html").is_file()
    assert not dev_dist.exists() or not any(dev_dist.rglob("*"))
