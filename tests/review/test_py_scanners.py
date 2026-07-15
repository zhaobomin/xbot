from scripts.review.py.scan_async_blocks import scan
from scripts.review.py.scan_async_blocks import scan
from scripts.review.py.scan_private_api import scan as scan_private_api


def test_async_blocks_hits_bad_not_good():
    findings = scan("tests/review/fixtures/async_block_sample.py")
    lines = {f.line for f in findings}
    assert 7 in lines and 8 in lines  # bad() body flagged
    assert 5 not in lines              # good() not flagged


def test_async_block_detail_has_func_contract():
    findings = scan("tests/review/fixtures/async_block_sample.py")
    assert all(f.detail.startswith("func:") for f in findings)


def test_private_api_hits_waiters_not_set():
    findings = scan_private_api("tests/review/fixtures/private_api_sample.py")
    lines = {f.line for f in findings}
    assert 7 in lines              # x = event._waiters
    assert 4 not in lines          # event.set() clean


def test_private_api_detail_has_func_contract():
    findings = scan_private_api("tests/review/fixtures/private_api_sample.py")
    assert findings
    assert all(f.detail.startswith("func:") for f in findings)


def test_fail_open_hits_admit_branch_not_reject():
    findings = scan_fail_open("tests/review/fixtures/fail_open_sample.py")
    lines = {f.line for f in findings}
    assert 7 in lines              # if name not in known: (admits)
    assert 2 not in lines          # if name not in known: raise (rejects)


def test_fail_open_detail_has_func_contract():
    findings = scan_fail_open("tests/review/fixtures/fail_open_sample.py")
    assert findings
    assert all(f.detail.startswith("func:") for f in findings)


def test_dead_code_hits_unused_and_unassigned():
    findings = scan_dead_code("tests/review/fixtures/dead_code_sample.py")
    lines = {f.line for f in findings}
    assert 1 in lines              # import unused_module
    assert 11 in lines             # asyncio.ensure_future unassigned
    assert 2 not in lines          # import os is referenced


def test_dead_code_detail_has_func_contract():
    findings = scan_dead_code("tests/review/fixtures/dead_code_sample.py")
    assert findings
    assert all(f.detail.startswith("func:") for f in findings)


def test_task_lifecycle_hits_unassigned_not_assigned():
    findings = scan_task_lifecycle("tests/review/fixtures/task_lifecycle_sample.py")
    lines = {f.line for f in findings}
    assert 8 in lines              # asyncio.ensure_future (unassigned)
    assert 3 not in lines          # t = asyncio.ensure_future (assigned)


def test_task_lifecycle_detail_has_func_contract():
    findings = scan_task_lifecycle("tests/review/fixtures/task_lifecycle_sample.py")
    assert findings
    assert all(f.detail.startswith("func:") for f in findings)


def test_mutable_defaults_hits_list_and_dict_not_none():
    findings = scan_mutable_defaults("tests/review/fixtures/mutable_defaults_sample.py")
    lines = {f.line for f in findings}
    assert 5 in lines              # def bad(x=[])
    assert 9 in lines              # def also_bad(y={})
    assert 1 not in lines          # def good(x=None)


def test_mutable_defaults_detail_has_func_contract():
    findings = scan_mutable_defaults("tests/review/fixtures/mutable_defaults_sample.py")
    assert findings
    assert all(f.detail.startswith("func:") for f in findings)


def test_naming_remnants_hits_class_docstring_string():
    findings = scan_naming_remnants("tests/review/fixtures/naming_remnants_sample.py")
    lines = {f.line for f in findings}
    assert 5 in lines              # class NanobotHandler
    assert 6 in lines              # docstring "forwards to Nanobot"
    assert 9 in lines              # REPLY_TITLE = "Nanobot Reply"
    assert 1 not in lines          # class GoodHandler


def test_naming_remnants_detail_has_func_contract():
    findings = scan_naming_remnants("tests/review/fixtures/naming_remnants_sample.py")
    assert findings
    assert all(f.detail.startswith("func:") for f in findings)
def test_private_api_hits_waiters_not_set():
    findings = scan_private_api("tests/review/fixtures/private_api_sample.py")
    lines = {f.line for f in findings}
    assert 11 in lines             # x = event._waiters
    assert 6 not in lines           # event.set() clean
from scripts.review.py.scan_private_api import scan as scan_private_api
from scripts.review.py.scan_fail_open import scan as scan_fail_open
from scripts.review.py.scan_fail_open import scan as scan_fail_open
from scripts.review.py.scan_dead_code import scan as scan_dead_code
from scripts.review.py.scan_dead_code import scan as scan_dead_code
from scripts.review.py.scan_task_lifecycle import scan as scan_task_lifecycle
from scripts.review.py.scan_task_lifecycle import scan as scan_task_lifecycle
from scripts.review.py.scan_mutable_defaults import scan as scan_mutable_defaults
from scripts.review.py.scan_mutable_defaults import scan as scan_mutable_defaults
from scripts.review.py.scan_naming_remnants import scan as scan_naming_remnants
from scripts.review.py.scan_ssrf import scan as scan_ssrf
from scripts.review.py.scan_retry_jitter import scan as scan_retry_jitter
from scripts.review.py.scan_codegraph_reachability import scan as scan_codegraph_reachability


def test_ssrf_hits_bad_not_good():
    findings = scan_ssrf("tests/review/fixtures/ssrf_sample.py")
    lines = {f.line for f in findings}
    assert 9 in lines              # bad() param interpolated into URL
    assert 5 not in lines          # good() fixed URL clean


def test_ssrf_detail_has_func_contract():
    findings = scan_ssrf("tests/review/fixtures/ssrf_sample.py")
    assert findings
    assert all(f.detail.startswith("func:") for f in findings)


def test_retry_jitter_hits_bad_not_good():
    findings = scan_retry_jitter("tests/review/fixtures/retry_jitter_sample.py")
    lines = {f.line for f in findings}
    assert 10 in lines             # bad() fixed sleep in retry loop
    assert 5 not in lines          # good() jittered sleep


def test_codegraph_reachability_does_not_crash():
    findings = scan_codegraph_reachability()
    assert isinstance(findings, list)
    # Either real findings or a toolchain_error — both are valid


def test_codegraph_missing_db_returns_toolchain_error():
    findings = scan_codegraph_reachability(db_path="/nonexistent/codegraph.db")
    assert len(findings) == 1
    assert findings[0].category == "toolchain_error"
