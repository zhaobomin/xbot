from scripts.review.ts.scan_console_log import scan as scan_console_log
from scripts.review.ts.scan_reconnect_race import scan as scan_reconnect_race


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
