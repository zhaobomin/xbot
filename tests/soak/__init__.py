"""Soak / long-duration load tests.

These tests run high-volume, extended-iteration workloads against key
subsystems to surface leaks, unbounded growth, and slow degradations that
short-lived unit / integration tests cannot detect.

Design principles:
- Deterministic iteration counts (not wall-clock time) so they remain
  CI-friendly. Each test caps at ~30s on a modern laptop.
- Explicit resource baselines: capture the size of internal tracking
  dictionaries before the loop, run N cycles, then assert the size returns
  to (or near) the baseline.
- Adversarial patterns: interleave supersede / cancel / timeout paths to
  stress the same cleanup logic that the earlier bug review touched.
- Reporting: on failure, dump the offending dict / set sizes so the
  regression is diagnosable from the test log alone.
"""
