# Bias Auditor

Use before trusting any backtest, summary table, or claimed edge. Decide whether
the result is usable evidence.

## Verdict Scale

- `PASS`: no known correctness blocker; sample/context fit the claim.
- `CONDITIONAL`: usable but limited by sample size, data, cost/spread
  assumptions, or missing cross-checks.
- `FAIL`: leakage, duplicate trade inflation, wrong calendar, missing costs,
  impossible fills, or another issue that can materially flip the conclusion.

## Mandatory Checks

- Signal timing: every feature is known before the entry decision.
- Entry/exit bars: no fill uses a future close/open by accident.
- Expiry mapping: one signal date produces one trade/expiry unless explicitly
  testing an expiry portfolio.
- DTE: no same-day expiry entry unless the strategy explicitly allows it.
- Calendar: next-day exits use the next actual trading session.
- Costs: brokerage, STT, exchange, GST, stamp duty or configured proxy are
  present and separate.
- Spread/slippage: applied on entry and exit, against the trader.
- Fill feasibility: fills are inside OHLC or explicitly pessimistic.
- Data quality: duplicate timestamps, gaps, zero volume/OI traded bars, missing
  option bars, quarantined folders.
- Statistics: N >= 30 for subgroup claims; show expectancy, not just win rate;
  check subperiod stability when possible.
- Dataset boundary: Shoonya/Dhan minute OHLCV lacks true bid/ask, so live PnL
  claims require a spread proxy or independent bid/ask source.

## Output

```text
VERDICT: PASS | CONDITIONAL | FAIL - one sentence.

Bugs or blockers:
1. file:line or artifact - issue - severity.

Minimum fixes before trusting:
- Required fix or "none".

Evidence still useful:
- What can safely be learned despite the limitations.
```

Keep it short. Never hide a trust blocker to save space.
