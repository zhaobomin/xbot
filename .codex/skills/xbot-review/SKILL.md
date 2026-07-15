---
name: xbot-review
description: Run the xbot code-review toolchain — static scanners (Python + TS + security), dynamic verification, parallel agent deep-dives. Produces a structured bug report with baseline diffing. Use when the user says "review xbot", "审查代码", "跑 bug 扫描", or wants a comprehensive code audit.
---

# xbot Code Review Toolchain

## When to use
- User says "review xbot", "审查代码", "跑 bug 扫描"
- User wants a comprehensive code audit with bug finding
- User wants to compare against a previous review baseline

## How to run
Execute: `.venv/bin/python -m scripts.review.orchestrate` from the xbot repo root.
Add `--dry-run` for preflight-only (no scanning).

## Output
- `docs/reviews/auto/<date>_review.md` — human-readable report
- `docs/reviews/auto/<date>_findings.json` — machine-readable findings
- `docs/reviews/auto/findings_baseline.json` — rolling baseline (with fixed_history + baseline_failures)

## What it checks
- Python AST scanners: async blocks, private API, fail-open, dead code, task lifecycle, mutable defaults, naming remnants, SSRF, retry jitter, codegraph reachability
- TS regex scanners: console.log, reconnect race, any type, unhandled promise, unused exports, frontend a11y
- Security/concurrency: auth bypass, SSRF, injection, secrets, async race, deadlock, event loop block
- Dynamic verification: generates regression tests for template-eligible findings, runs them, confirms/refutes
- Baseline diff: classifies findings as new/recurring/fixed/regression

## Note on --fix-confirmed
The `--fix-confirmed` flag is accepted but currently a no-op (deferred to follow-up plan). The static-confirmation rule IS implemented — dead_code and naming_remnants findings with high confidence get verdict=confirmed.
