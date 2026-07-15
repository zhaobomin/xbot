from scripts.review.security.scan_async_race import scan as scan_async_race
from scripts.review.security.scan_auth_bypass import scan as scan_auth_bypass
from scripts.review.security.scan_deadlock import scan as scan_deadlock
from scripts.review.security.scan_event_loop_block import scan as scan_event_loop_block
from scripts.review.security.scan_injection import scan as scan_injection
from scripts.review.security.scan_secrets import scan as scan_secrets
from scripts.review.security.scan_ssrf import scan as scan_ssrf


def test_auth_bypass_hits_bad_not_good():
    findings = scan_auth_bypass("tests/review/fixtures/security/auth_bypass_sample.py")
    lines = {f.line for f in findings}
    assert 11 in lines          # @app.get("/admin") with no auth dependency
    assert 16 not in lines      # @app.get(..., dependencies=[Depends(verify)]) clean


def test_auth_bypass_detail_has_func_contract():
    findings = scan_auth_bypass("tests/review/fixtures/security/auth_bypass_sample.py")
    assert findings
    assert all(f.detail.startswith("func:") for f in findings)
    assert all(f.category == "auth_bypass" for f in findings)


def test_ssrf_hits_bad_not_good():
    findings = scan_ssrf("tests/review/fixtures/security/ssrf_sample.py")
    lines = {f.line for f in findings}
    assert 11 in lines          # httpx.get(user_url) user-controlled URL
    assert 7 not in lines       # httpx.get(fixed URL) clean


def test_ssrf_detail_has_func_contract():
    findings = scan_ssrf("tests/review/fixtures/security/ssrf_sample.py")
    assert findings
    assert all(f.detail.startswith("func:") for f in findings)
    assert all(f.category == "ssrf" for f in findings)


def test_injection_hits_bad_not_good():
    findings = scan_injection("tests/review/fixtures/security/injection_sample.py")
    lines = {f.line for f in findings}
    assert 11 in lines         # subprocess.run(f"echo {user_input}") shell string
    assert 7 not in lines      # subprocess.run(["echo", user_input]) list form clean


def test_injection_detail_has_func_contract():
    findings = scan_injection("tests/review/fixtures/security/injection_sample.py")
    assert findings
    assert all(f.detail.startswith("func:") for f in findings)
    assert all(f.category == "injection" for f in findings)


def test_secrets_hits_bad_not_good():
    findings = scan_secrets("tests/review/fixtures/security/secrets_sample.py")
    lines = {f.line for f in findings}
    assert 10 in lines          # API_KEY = "sk-abc123def456"
    assert 11 in lines          # password = "supersecretpass"
    assert 6 not in lines       # os.environ["KEY"] clean


def test_secrets_high_confidence_no_func_contract():
    findings = scan_secrets("tests/review/fixtures/security/secrets_sample.py")
    assert findings
    assert all(f.category == "secrets" for f in findings)
    assert all(f.confidence == "high" for f in findings)


def test_async_race_hits_bad_not_good():
    findings = scan_async_race("tests/review/fixtures/security/async_race_sample.py")
    lines = {f.line for f in findings}
    assert 12 in lines         # _cache["k"] = "v" without a lock
    assert 8 not in lines      # _cache["k"] = "v" under asyncio.Lock() clean


def test_async_race_category_no_func_contract():
    findings = scan_async_race("tests/review/fixtures/security/async_race_sample.py")
    assert findings
    assert all(f.category == "async_race" for f in findings)


def test_deadlock_hits_bad_not_good():
    findings = scan_deadlock("tests/review/fixtures/security/deadlock_sample.py")
    lines = {f.line for f in findings}
    assert 10 in lines         # reversed lock order (lock_b, lock_a)
    assert 5 not in lines      # consistent order (lock_a, lock_b) clean


def test_deadlock_category_no_func_contract():
    findings = scan_deadlock("tests/review/fixtures/security/deadlock_sample.py")
    assert findings
    assert all(f.category == "deadlock" for f in findings)


def test_event_loop_block_hits_bad_not_good():
    findings = scan_event_loop_block(
        "tests/review/fixtures/security/event_loop_block_sample.py"
    )
    lines = {f.line for f in findings}
    assert 12 in lines         # requests.get in async function
    assert 13 in lines         # time.sleep in async function
    assert 8 not in lines      # await httpx.get clean


def test_event_loop_block_detail_has_func_contract():
    findings = scan_event_loop_block(
        "tests/review/fixtures/security/event_loop_block_sample.py"
    )
    assert findings
    assert all(f.detail.startswith("func:") for f in findings)
    assert all(f.category == "async_block" for f in findings)
