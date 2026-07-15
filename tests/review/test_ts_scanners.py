from scripts.review.ts.scan_any_type import scan as scan_any_type
from scripts.review.ts.scan_console_log import scan as scan_console_log
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
