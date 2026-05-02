# Trade Analyzer

Use for completed backtest ledgers. Attribute gross edge, cost drag, and trust
quality.

## Expected Inputs

Common columns:

- `entry_time`, `exit_time`, `expiry`, `strike`, `option_type`
- `entry_price`, `exit_price`, `gross_pnl`, `charges`, `net_pnl`
- `exit_reason`, `DTE_at_entry` or equivalent metadata

Adapt to actual columns. State missing fields that limit analysis.

## Analysis Protocol

1. Integrity: duplicate `entry_time`, duplicate signal dates, DTE < 1, missing
   exits, fills outside OHLC if available.
2. PnL: trades, gross, charges, net, charges/abs(gross), win rate, avg win,
   avg loss, expectancy.
3. Attribution: year, month, weekday, DTE bucket, exit reason, side/strategy or
   strike bucket if available.
4. Costs: net without charges, break-even cost/trade, spread/slippage sensitivity.
5. Diagnosis: direction wrong means alpha issue; direction right but net losing
   means theta/IV/fills/costs; if trade count collapsed, show per-trade edge.

## Output

Use compact markdown tables for numeric sections. Start with:

```text
Verdict: usable | conditional | invalid
Reason: one sentence.
```

Flag anything that invalidates the ledger before discussing optimizations.
