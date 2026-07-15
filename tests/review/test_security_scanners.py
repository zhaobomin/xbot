from scripts.review.security.scan_auth_bypass import scan as scan_auth_bypass
from scripts.review.security.scan_injection import scan as scan_injection
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
