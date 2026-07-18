"""Generate a pytest regression test for a single Finding via Jinja2.

The generated test asserts the *correct* behavior for the finding's category.
Verdict inversion:
  * real bug      -> assertion fails -> generated test FAILS  -> confirmed
  * false positive-> assertion holds -> generated test PASSES -> refuted
  * missing `func:` in detail -> no test generated -> inconclusive (caller's job)
"""
from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from scripts.review.common import Finding

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_OUTPUT_DIR = Path("tests/review_temp")

# Categories backed by a Jinja2 template (dynamic-verification eligible).
TEMPLATE_CATEGORIES = {p.name.removesuffix(".py.j2") for p in _TEMPLATES_DIR.glob("*.py.j2")}

# one shared, immutable environment; templates never change at runtime
_ENV = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    autoescape=False,
)


def _file_to_module(path: str) -> str:
    """``xbot/runtime/core/service.py`` -> ``xbot.runtime.core.service``."""
    p = path.replace("\\", "/")
    if p.startswith("./"):
        p = p[2:]
    if p.endswith(".py"):
        p = p[:-3]
    return p.replace("/", ".")


def _sanitize_identifier(finding_id: str) -> str:
    """Make a finding id usable as a Python identifier suffix (``test_<id>``)."""
    s = re.sub(r"\W", "_", finding_id)
    if not s or s[0].isdigit():
        s = f"t_{s}"
    return s


def _parse_detail(detail: str) -> tuple[str | None, str | None]:
    """Extract ``func: <name>`` and optional ``args: <val>`` from the detail."""
    func_name: str | None = None
    sample_args: str | None = None
    for raw in detail.splitlines():
        line = raw.strip()
        if line.startswith("func:"):
            func_name = line[len("func:"):].strip() or None
        elif line.startswith("args:"):
            sample_args = line[len("args:"):].strip()
    return func_name, sample_args


def generate_test(finding: Finding) -> str:
    """Render a pytest test file for *finding*; return ``""`` if no ``func:``.

    The returned string is also written to ``tests/review_temp/test_<id>.py``
    by :func:`write_test`; this function stays pure for easy testing.
    """
    func_name, sample_args = _parse_detail(finding.detail)
    if not func_name:
        # caller marks the finding inconclusive
        return ""
    # ``None`` -> empty call site: ``f()`` rather than the broken ``f(None)``.
    args_render = "" if sample_args is None else sample_args
    template = _ENV.get_template(f"{finding.category}.py.j2")
    return template.render(
        module_path=_file_to_module(finding.file),
        function_name=func_name,
        sample_args=args_render,
        finding_id=_sanitize_identifier(finding.id),
    )


def write_test(finding: Finding) -> Path | None:
    """Generate and persist the test file; returns its path, or ``None`` if skipped."""
    code = generate_test(finding)
    if not code:
        return None
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = _OUTPUT_DIR / f"test_{_sanitize_identifier(finding.id)}.py"
    out.write_text(code, encoding="utf-8")
    return out
