from __future__ import annotations

from scripts.review.common import Finding
from scripts.review.verify.gen_regression import generate_test


def make_finding(
    category: str,
    module_path: str,
    function_name: str,
    *,
    detail: str | None = None,
    finding_id: str = "t1",
) -> Finding:
    if detail is None:
        detail = f"func: {function_name}"
    file_path = module_path.replace(".", "/") + ".py"
    return Finding(
        id=finding_id,
        sig_key=f"{category}:{function_name}",
        severity="P2",
        file=file_path,
        line=1,
        category=category,
        title="test",
        detail=detail,
        suggestion="",
        confidence="low",
        scanner="test",
    )


# --- spec-given: flagship async_block contract --------------------------------


def test_async_block_confirm_generates_failing_test():
    finding = make_finding(
        "async_block",
        "tests.review.fixtures_dynamic.async_block_confirm",
        "blocks_forever",
        finding_id="ab_conf",
    )
    test_code = generate_test(finding)
    assert "wait_for" in test_code and "pytest.fail" in test_code
    assert "asyncio.TimeoutError" in test_code
    # Must NOT use pytest.raises(TimeoutError) -- that inverts the verdict.
    assert "pytest.raises(asyncio.TimeoutError)" not in test_code


def test_generate_test_skips_missing_func():
    finding = make_finding("async_block", "x.y", "f", detail="no func prefix")
    assert generate_test(finding) == ""  # or None


# --- per-category confirm: generated source points at the buggy fixture -------



def test_fail_open_confirm_generates_permission_check():
    f = make_finding(
        "fail_open",
        "tests.review.fixtures_dynamic.fail_open_confirm",
        "check",
        finding_id="fo_conf",
    )
    code = generate_test(f)
    assert "pytest.raises(PermissionError)" in code
    assert "illegal_input" in code
    assert "tests.review.fixtures_dynamic.fail_open_confirm" in code
    assert "def test_fo_conf" in code


def test_ssrf_confirm_generates_intranet_guard():
    f = make_finding(
        "ssrf",
        "tests.review.fixtures_dynamic.ssrf_confirm",
        "fetch",
        finding_id="sf_conf",
    )
    code = generate_test(f)
    assert "pytest.raises((ValueError, ConnectionError))" in code
    assert "169.254.169.254" in code
    assert "tests.review.fixtures_dynamic.ssrf_confirm" in code
    assert "def test_sf_conf" in code


def test_task_lifecycle_confirm_generates_gc_check():
    f = make_finding(
        "task_lifecycle",
        "tests.review.fixtures_dynamic.task_lifecycle_confirm",
        "spawn",
        finding_id="tl_conf",
    )
    code = generate_test(f)
    assert "gc.collect" in code
    assert "@pytest.mark.asyncio" in code
    assert "tests.review.fixtures_dynamic.task_lifecycle_confirm" in code
    assert "def test_tl_conf" in code


def test_injection_confirm_generates_metachar_check():
    f = make_finding(
        "injection",
        "tests.review.fixtures_dynamic.injection_confirm",
        "run",
        finding_id="ij_conf",
    )
    code = generate_test(f)
    assert "subprocess.SubprocessError" in code
    assert "; rm -rf /" in code
    assert "tests.review.fixtures_dynamic.injection_confirm" in code
    assert "def test_ij_conf" in code


def test_auth_bypass_confirm_generates_auth_check():
    f = make_finding(
        "auth_bypass",
        "tests.review.fixtures_dynamic.auth_bypass_confirm",
        "admin",
        finding_id="au_conf",
    )
    code = generate_test(f)
    assert "pytest.raises((PermissionError, Exception))" in code
    assert "tests.review.fixtures_dynamic.auth_bypass_confirm" in code
    assert "def test_au_conf" in code


# --- per-category refute: generated source points at the clean fixture --------



def test_async_block_refute_points_at_clean_fixture():
    f = make_finding(
        "async_block",
        "tests.review.fixtures_dynamic.async_block_refute",
        "yields_quickly",
        finding_id="ab_ref",
    )
    code = generate_test(f)
    assert "tests.review.fixtures_dynamic.async_block_refute" in code
    assert "yields_quickly" in code
    assert "wait_for" in code and "pytest.fail" in code


def test_fail_open_refute_points_at_clean_fixture():
    f = make_finding(
        "fail_open",
        "tests.review.fixtures_dynamic.fail_open_refute",
        "check",
        finding_id="fo_ref",
    )
    code = generate_test(f)
    assert "tests.review.fixtures_dynamic.fail_open_refute" in code
    assert "pytest.raises(PermissionError)" in code


def test_ssrf_refute_points_at_clean_fixture():
    f = make_finding(
        "ssrf",
        "tests.review.fixtures_dynamic.ssrf_refute",
        "fetch",
        finding_id="sf_ref",
    )
    code = generate_test(f)
    assert "tests.review.fixtures_dynamic.ssrf_refute" in code
    assert "169.254.169.254" in code


def test_task_lifecycle_refute_points_at_clean_fixture():
    f = make_finding(
        "task_lifecycle",
        "tests.review.fixtures_dynamic.task_lifecycle_refute",
        "spawn",
        finding_id="tl_ref",
    )
    code = generate_test(f)
    assert "tests.review.fixtures_dynamic.task_lifecycle_refute" in code
    assert "gc.collect" in code


def test_injection_refute_points_at_clean_fixture():
    f = make_finding(
        "injection",
        "tests.review.fixtures_dynamic.injection_refute",
        "run",
        finding_id="ij_ref",
    )
    code = generate_test(f)
    assert "tests.review.fixtures_dynamic.injection_refute" in code
    assert "subprocess.SubprocessError" in code


def test_auth_bypass_refute_points_at_clean_fixture():
    f = make_finding(
        "auth_bypass",
        "tests.review.fixtures_dynamic.auth_bypass_refute",
        "admin",
        finding_id="au_ref",
    )
    code = generate_test(f)
    assert "tests.review.fixtures_dynamic.auth_bypass_refute" in code
    assert "pytest.raises((PermissionError, Exception))" in code


# --- arg + id rendering contracts ---------------------------------------------



def test_args_rendered_into_call():
    f = make_finding(
        "async_block",
        "m.f",
        "go",
        detail="func: go\nargs: 42",
        finding_id="ar1",
    )
    code = generate_test(f)
    assert "go(42)" in code


def test_no_args_renders_empty_call():
    f = make_finding("async_block", "m.f", "go", finding_id="ar2")
    code = generate_test(f)
    assert "go()" in code
    assert "go(None)" not in code


def test_finding_id_sanitized_to_valid_identifier():
    f = make_finding("ssrf", "m.f", "fetch", finding_id="ssrf:abcd1234")
    code = generate_test(f)
    assert "def test_ssrf_abcd1234" in code
    # the raw colon-bearing id must not leak into the function name
    assert "def test_ssrf:abcd1234" not in code
