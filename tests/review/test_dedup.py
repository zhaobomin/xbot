from scripts.review.py.runner import run


def test_runner_runs_all_scanners_and_dedups():
    findings = run("tests/review/fixtures/")
    assert isinstance(findings, list)
    assert len(findings) > 0  # fixtures have known anti-patterns
    # Verify no duplicate (file, line, category) tuples
    keys = [(f.file, f.line, f.category) for f in findings]
    assert len(keys) == len(set(keys)), "duplicates found"
