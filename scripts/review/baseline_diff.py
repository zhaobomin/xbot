from __future__ import annotations

from datetime import date

from scripts.review.common import Finding

TTL_ROUNDS = 4


def apply_diff(
    current: list[Finding], baseline: dict
) -> tuple[list[Finding], list[dict]]:
    """Compare current findings against a stored baseline.

    baseline shape:
        {"findings": [<Finding dicts>], "fixed_history": [{"sig_key":..., "fixed_at":...}]}

    Returns (diffed_findings, new_fixed_history). Each current Finding gets a
    diff_status of "new", "recurring", or "regression". Findings that were
    present in the baseline but absent now are appended to fixed_history.

    Classification by sig_key (stable across runs; line numbers and ids shift):
      - in baseline fixed_history            -> "regression"
      - in baseline findings (and not fixed)  -> "recurring"
      - otherwise                             -> "new"

    fixed_history TTL: entries are kept for the last TTL_ROUNDS distinct
    fixed_at dates; anything older is dropped. A fixed_history entry that
    reappears in current is classified as a regression and removed from
    fixed_history (it is no longer "fixed").
    """
    baseline_findings = baseline.get("findings") or []
    fixed_history = baseline.get("fixed_history") or []

    current_keys = {f.sig_key for f in current}
    baseline_keys = {e["sig_key"] for e in baseline_findings if "sig_key" in e}
    fh_keys = {e["sig_key"] for e in fixed_history if "sig_key" in e}

    # Regression (reappeared after being fixed) takes precedence over recurring.
    diffed: list[Finding] = []
    for f in current:
        if f.sig_key in fh_keys:
            f.diff_status = "regression"
        elif f.sig_key in baseline_keys:
            f.diff_status = "recurring"
        else:
            f.diff_status = "new"
        diffed.append(f)

    today = date.today().isoformat()

    # Carry forward fixed_history entries that did NOT reappear (still fixed).
    kept_fh = [{**e} for e in fixed_history if e.get("sig_key") not in current_keys]
    kept_fh_keys = {e["sig_key"] for e in kept_fh}

    # Newly fixed: present in baseline findings but absent from current.
    for key in baseline_keys - current_keys:
        if key in kept_fh_keys:
            continue
        kept_fh.append({"sig_key": key, "fixed_at": today})
        kept_fh_keys.add(key)

    # TTL: retain only entries within the last TTL_ROUNDS distinct dates.
    distinct_dates = sorted({e.get("fixed_at", "") for e in kept_fh}, reverse=True)
    keep_dates = set(distinct_dates[:TTL_ROUNDS])
    new_fixed_history = [e for e in kept_fh if e.get("fixed_at", "") in keep_dates]

    return diffed, new_fixed_history
