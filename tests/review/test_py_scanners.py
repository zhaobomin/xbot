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
def test_private_api_hits_waiters_not_set():
    findings = scan_private_api("tests/review/fixtures/private_api_sample.py")
    lines = {f.line for f in findings}
    assert 11 in lines             # x = event._waiters
    assert 6 not in lines           # event.set() clean
