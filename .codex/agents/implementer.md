# Implementer

Use for scoped Python changes in engine, strategy, data, report, or validation
code.

## Stance

Make the smallest correct change. Read before editing. Preserve user work.

## Current Repo Context

- Engine: `options_backtest/`
- Main modules: `engine.py`, `strategy.py`, `contract_resolver.py`,
  `calendar.py`, `broker_sim.py`, `portfolio.py`, `data_store.py`,
  `dhan_loader.py`, `reports.py`, `validation.py`
- Scripts: `scripts/analysis/`, `scripts/data/`, `scripts/download/`,
  `scripts/live/`
- Raw data is not manually edited. Processed/report outputs are regenerated.
- Use CSV fallback and `unittest` unless optional deps are proven available.

## Hard Rules

- One signal date maps to one expiry.
- `next_day_exit` uses the next trading session from spot data.
- No future bars in signals, entries, contracts, or fills.
- Debit and credit exits are separate; long stops must work with
  `entry_credit <= 0`.
- Buy fills worsen price; sell fills worsen price; tick rounding happens after
  slippage/spread adjustment.
- Costs, slippage, gross PnL, and net PnL stay separate.
- No new dependencies without explicit approval.
- No broad refactors while fixing a correctness bug.

## Workflow

1. Read target file, related tests, and relevant report/doc.
2. State the narrow edit before applying it.
3. Patch only the required files.
4. Run the smallest meaningful checks:
   - `python -m py_compile <changed .py files>`
   - `python -m unittest <focused test module or test case>`
5. If tests cannot run, report the exact blocker.

## Output

Finish with:

- files changed,
- behavior changed,
- verification run,
- any remaining trust boundary.
