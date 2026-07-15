"""Apply dynamic and static verdicts to findings.

Dynamic verdicts come from the regression-test runner (pytest pass/fail/error).
Static verdicts apply to no-template categories (dead_code, naming_remnants, ...):
the scanner's default "high" confidence is trusted without a generated test.
"""
from __future__ import annotations

from dataclasses import replace

from scripts.review.common import Finding
from scripts.review.verify.gen_regression import TEMPLATE_CATEGORIES

_CONF_RANK = {"high": 3, "medium": 2, "low": 1}


def _rank(confidence: str) -> int:
    return _CONF_RANK.get(confidence, 0)


def update_verdict(test_status: str) -> tuple[str, str]:
    """Map a pytest test status to a (verdict, confidence) pair.

    * failed  -> ("confirmed", "medium")  # assertion failed -> real bug
    * passed  -> ("refuted", "low")       # assertion held -> false positive
    * error   -> ("inconclusive", "")     # import/syntax error; caller keeps confidence
    """
    if test_status == "failed":
        return ("confirmed", "medium")
    if test_status == "passed":
        return ("refuted", "low")
    return ("inconclusive", "")


def static_verdict(category: str, confidence: str) -> tuple[str, str]:
    """No-template category: confirm when default-high, else leave inconclusive.

    Template-eligible categories are handled dynamically and get no static override.
    """
    if category in TEMPLATE_CATEGORIES:
        return ("inconclusive", "")
    if confidence == "high":
        return ("confirmed", "static-confirmed")
    return ("inconclusive", "")


def update(findings: list[Finding], verify_results: dict) -> list[Finding]:
    """Apply dynamic verdicts (from verify_results) and static rules.

    ``verify_results`` maps a finding's ``id`` to a pytest status string
    ("failed" / "passed" / "error"). Template categories without an entry keep
    their existing verdict; no-template categories get a static verdict.
    """
    out: list[Finding] = []
    for f in findings:
        if f.category in TEMPLATE_CATEGORIES:
            status = verify_results.get(f.id)
            if status is None:
                # No dynamic result (e.g. no `func:` to build a test); keep as-is.
                out.append(f)
                continue
            verdict, conf = update_verdict(status)
            if verdict == "confirmed":
                # confidence is "at least medium": bump up, never down.
                new_conf = conf if _rank(conf) >= _rank(f.confidence) else f.confidence
                out.append(
                    replace(f, verdict=verdict, confidence=new_conf, verify_note="dynamic-confirmed")
                )
            elif verdict == "refuted":
                out.append(replace(f, verdict=verdict, confidence="low", verify_note="dynamic-refuted"))
            else:  # inconclusive: keep original confidence, note the failure.
                out.append(
                    replace(f, verdict=verdict, verify_note=f"verification failed: {status}")
                )
        else:
            verdict, note = static_verdict(f.category, f.confidence)
            if verdict == "inconclusive" and not note:
                out.append(f)
            else:
                out.append(replace(f, verdict=verdict, verify_note=note))
    return out
