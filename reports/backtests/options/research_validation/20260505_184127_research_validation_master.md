# Research Paper Validation — 20260505_184127

Recreates Indian_Index_Options_Strategy.txt for all four indices.
Window: `2022-02-01` to `2026-04-30`

## Strategy A — Short Strangle (DTE 0-3, intraday, VIX 13-22)

| Symbol | Trades | Net PnL | Sharpe | t-stat | Win% | Max DD% | PF |
|---|---:|---:|---:|---:|---:|---:|---:|
| NIFTY | 477 | +INR 89,826 | 0.803 | 1.744 | 58.5% | -3.32% | 1.241 |
| BANKNIFTY | 335 | +INR 70,730 | 0.716 | 1.508 | 57.3% | -2.39% | 1.235 |
| FINNIFTY | 193 | +INR 29,288 | 0.445 | 0.972 | 49.2% | -2.39% | 1.202 |
| MIDCPNIFTY | 113 | +INR 82,759 | 1.582 | 2.521 | 57.5% | -1.28% | 1.867 |

**Portfolio** — Trades: 1,118, Net PnL: +INR 272,603, Sharpe: 1.156, Max DD: -5.81%

## Strategy B — Iron Condor (monthly, 18-24 DTE, VIX 15-25)

| Symbol | Trades | Net PnL | Sharpe | t-stat | Win% | Max DD% | PF |
|---|---:|---:|---:|---:|---:|---:|---:|
| NIFTY | 3 | +INR 4,784 | 0.761 | 1.369 | 100.0% | +0.00% | - |
| BANKNIFTY | 2 | +INR 2,083 | 0.894 | 1.131 | 100.0% | +0.00% | - |
| FINNIFTY | 0 | +INR 0 | - | - | 0.0% | +0.00% | - |
| MIDCPNIFTY | 0 | +INR 0 | - | - | 0.0% | +0.00% | - |

**Portfolio** — Trades: 5, Net PnL: +INR 6,867, Sharpe: 0.967, Max DD: +0.00%

## Strategy C — Short Straddle (DTE 0-7, Mon SL 15% / Other SL 70%, VIX 10-30)

| Symbol | Trades | Net PnL | Sharpe | t-stat | Win% | Max DD% | PF |
|---|---:|---:|---:|---:|---:|---:|---:|
| NIFTY | 284 | INR -54,540 | -0.491 | -1.172 | 57.0% | -6.64% | 0.832 |
| BANKNIFTY | 281 | +INR 111,393 | 0.994 | 2.212 | 65.5% | -3.27% | 1.393 |
| FINNIFTY | 236 | +INR 88,443 | 0.833 | 1.890 | 61.4% | -3.82% | 1.366 |
| MIDCPNIFTY | 166 | +INR 164,871 | 1.839 | 3.191 | 70.5% | -2.82% | 2.052 |

**Portfolio** — Trades: 967, Net PnL: +INR 310,167, Sharpe: 0.897, Max DD: -10.59%

## Notes

- Strategy A VIX gate uses the paper's stated 'OPTIMAL VIX RANGE 13-22'. The paper's size-reduction bands (10-13 and 22-30) are not modelled — engine runs fixed 1 lot.
- Strategy B uses ATM±6/±10 offsets for all indices. For NIFTY (50pt step) this equals the paper's 300/500 pt example. BANKNIFTY (100pt step) → 600/1000 pt; MIDCPNIFTY (25pt step) → 150/250 pt.
- Strategy B adjustment rules (roll when spot within 100 pt of short strike) are not implemented.
- Strategy C cooling-off rule (skip next day after double-leg SL) is not implemented.
- 2026 STT rates (0.15%) apply — paper assumed 0.10%. ~15-20% higher cost drag on low-premium legs.
- All stop-loss fills execute at the open of the bar after the trigger bar, per engine convention.
