from scripts.review.py.scan_async_blocks import scan


def test_async_blocks_hits_bad_not_good():
    findings = scan("tests/review/fixtures/async_block_sample.py")
    lines = {f.line for f in findings}
    assert 7 in lines and 8 in lines  # bad() body flagged
    assert 5 not in lines              # good() not flagged


def test_async_block_detail_has_func_contract():
    findings = scan("tests/review/fixtures/async_block_sample.py")
    assert all(f.detail.startswith("func:") for f in findings)
