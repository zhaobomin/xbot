from scripts.review.security.scan_auth_bypass import scan as scan_auth_bypass


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
