from scripts.review.ts.build_tsc import scan as build_tsc_scan
from scripts.review.ts.lint_eslint import scan as lint_eslint_scan
from scripts.review.ts.scan_any_type import scan as scan_any_type
from scripts.review.ts.scan_console_log import scan as scan_console_log
from scripts.review.ts.scan_frontend_a11y import scan as scan_frontend_a11y
from scripts.review.ts.scan_reconnect_race import scan as scan_reconnect_race
from scripts.review.ts.scan_unhandled_promise import scan as scan_unhandled_promise
from scripts.review.ts.scan_unused_exports import scan as scan_unused_exports


def test_console_log_hits_bad_not_good():
    findings = scan_console_log("tests/review/fixtures/ts/console_log_sample.ts")
    lines = {f.line for f in findings}
    assert 6 in lines              # console.log("x")
    assert 2 not in lines          # logger.info("x") clean


def test_console_log_detail_has_func_contract():
    findings = scan_console_log("tests/review/fixtures/ts/console_log_sample.ts")
    assert findings
    assert all(f.detail.startswith("func:") for f in findings)


def test_reconnect_race_hits_bad_not_good():
    findings = scan_reconnect_race("tests/review/fixtures/ts/reconnect_race_sample.ts")
    lines = {f.line for f in findings}
    assert 7 in lines              # setTimeout(reconnect, 1000) without clear
    assert 3 not in lines          # clearTimeout before setTimeout (clean)


def test_reconnect_race_detail_has_func_contract():
    findings = scan_reconnect_race("tests/review/fixtures/ts/reconnect_race_sample.ts")
    assert findings
    assert all(f.detail.startswith("func:") for f in findings)


def test_any_type_hits_bad_not_good():
    findings = scan_any_type("tests/review/fixtures/ts/any_type_sample.ts")
    lines = {f.line for f in findings}
    assert 7 in lines              # let x: any
    assert 2 not in lines          # let x: string clean


def test_any_type_detail_has_func_contract():
    findings = scan_any_type("tests/review/fixtures/ts/any_type_sample.ts")
    assert findings
    assert all(f.detail.startswith("func:") for f in findings)


def test_unhandled_promise_hits_bad_not_good():
    findings = scan_unhandled_promise("tests/review/fixtures/ts/unhandled_promise_sample.ts")
    lines = {f.line for f in findings}
    assert 6 in lines              # fetch().then() without .catch
    assert 2 not in lines          # fetch().then().catch() clean


def test_unhandled_promise_detail_has_func_contract():
    findings = scan_unhandled_promise("tests/review/fixtures/ts/unhandled_promise_sample.ts")
    assert findings
    assert all(f.detail.startswith("func:") for f in findings)


def test_unused_exports_hits_unused_not_used():
    findings = scan_unused_exports("tests/review/fixtures/ts/unused_exports")
    lines = {f.line for f in findings if f.file.endswith("mod.ts")}
    assert 1 in lines              # export const unused (never imported)
    assert 2 not in lines          # export const used (imported by consumer.ts)


def test_unused_exports_detail_has_func_contract():
    findings = scan_unused_exports("tests/review/fixtures/ts/unused_exports")
    assert findings
    assert all(f.detail.startswith("func:") for f in findings)


def test_frontend_a11y_hits_bad_not_good():
    findings = scan_frontend_a11y("tests/review/fixtures/ts/frontend_a11y_sample.tsx")
    lines = {f.line for f in findings}
    assert 6 in lines              # <img src="x" /> without alt
    assert 2 not in lines          # <img src="x" alt="desc" /> clean


def test_frontend_a11y_detail_has_func_contract():
    findings = scan_frontend_a11y("tests/review/fixtures/ts/frontend_a11y_sample.tsx")
    assert findings
    assert all(f.detail.startswith("func:") for f in findings)


def test_build_tsc_parses_type_error():
    findings = build_tsc_scan("tests/review/fixtures/ts/tsc_error_sample.ts")
    lines = {f.line for f in findings}
    assert 1 in lines              # const x: number = "bad" -> TS2322
    assert all(f.category == "toolchain_error" for f in findings)


def test_lint_eslint_emits_toolchain_error_and_does_not_crash():
    findings = lint_eslint_scan("bridge")
    assert len(findings) == 1
    f = findings[0]
    assert f.category == "toolchain_error"
    assert f.detail.startswith("eslint broken:")
