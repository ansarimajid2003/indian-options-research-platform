# Codex Project Guide: Indian Markets

This is a NIFTY 50 options research and backtest workspace. Keep raw data
untouched, prefer deterministic scripts, and treat profitable backtests as
untrusted until leakage, calendar, expiry, fill, and cost checks pass.

## Current Map

- Backtest engine: `options_backtest/`
- Operational scripts: `scripts/`
- Human docs: `docs/`
- Raw data: `data/raw/`
- Processed data: `data/processed/`
- Data inventory: `data/manifests/data_inventory_manifest.csv`
- Backtest outputs: `reports/backtests/options/`
- Research outputs: `reports/research/`
- Structural audits: `reports/data_quality/`

## Always-On Rules

- Do not edit raw datasets in place. Regenerate processed outputs from raw
  inputs.
- Test data stays locked unless the user explicitly unlocks it.
- One signal date maps to one expiry. Use the nearest weekly expiry with
  DTE >= 1 unless a task specifies another explicit rule.
- `next_day_exit` means the next trading session from spot data.
- Option fills must be conservative: buys pay slippage/spread, sells give it up,
  prices are tick-rounded, and costs remain separate from gross PnL.
- Shoonya and Dhan minute data lack true bid/ask. Treat spread assumptions as
  model risk, not live execution proof.
- Prefer focused `unittest` and `python -m py_compile` checks. Do not assume
  `pytest` or `pyarrow` are installed.
- Use PowerShell-native commands in examples and verification.

## Codex Playbooks

Load the smallest matching playbook from `.codex/agents/`:

- `researcher.md`: KB/web evidence before strategy or market-structure claims.
- `implementer.md`: scoped Python changes to engine, data, or strategy code.
- `reviewer.md`: post-change code review for correctness regressions.
- `bias-auditor.md`: trust gate before using any backtest result as evidence.
- `trade-analyzer.md`: ledger attribution and cost/gross/edge decomposition.

Keep outputs short and evidence-first. Persist useful findings in repo docs when
the user asks to write them down.
