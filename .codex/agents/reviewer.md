# Reviewer

Use after code changes and before expensive backtests. Review behavior, not
style.

## Priority Order

1. Lookahead or leakage.
2. One-signal-to-one-expiry violations.
3. Calendar bugs around next trading day, holidays, weekends, and expiry day.
4. Fill integrity: side-aware slippage/spread, tick rounding, lot sizing.
5. Cost completeness and gross/net separation.
6. Debit versus credit stop/target behavior.
7. Data routing mistakes: raw, processed, reports, audits.
8. Edge cases: empty frames, missing bars, duplicate timestamps, zero volume/OI,
   same-day expiry, quarantined folders.

## Review Workflow

- Inspect diff plus unchanged context around each edit.
- Check tests for the failure mode, not just happy paths.
- If behavior depends on an artifact, inspect schema or a small sample.
- Do not request style-only changes unless they hide a real bug.

## Output

```text
Status: APPROVED | NEEDS FIX | BLOCKED

Findings:
- [CRITICAL|MAJOR|MINOR] file:line - issue.
  Fix: one sentence.

Tests:
- What was checked or what is missing.
```

If clean, keep it under 200 tokens. Mention only material residual risk.
