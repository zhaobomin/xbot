from __future__ import annotations

from scripts.review.verify.confidence_updater import static_verdict, update_verdict


def test_failed_test_confirms_bug():
    assert update_verdict("failed") == ("confirmed", "medium")


def test_passed_test_refutes():
    assert update_verdict("passed") == ("refuted", "low")


def test_error_keeps_inconclusive():
    v, _c = update_verdict("error")
    assert v == "inconclusive"


def test_static_confirm_dead_code_high():
    v, note = static_verdict("dead_code", "high")
    assert v == "confirmed" and note == "static-confirmed"


def test_no_template_low_stays_inconclusive():
    assert static_verdict("naming_remnants", "low")[0] == "inconclusive"
