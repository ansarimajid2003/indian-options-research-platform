# NIFTY 50 3 PM Alpha Study

Dataset: `cleaned_archive\NIFTY 50_15minute.csv`

Period studied: `2015-01-09` to `2026-04-07`

Observations used: `2,769` trading days with a 15:00 candle and a next trading day.

## Executive Read

The 3 PM candle is useful, but mainly as a reference level and context filter. The raw next-day gap-up tendency is strong at `67.24%`, while gap-downs are `32.61%`. This bias appears across bullish and bearish 3 PM candles, so candle color alone should not be treated as the full signal.

The stronger behavior is after the next open: when price gaps away from the prior 3 PM close, whether it revisits that level is a major decision point. The readable touch-level report has the key table.

## Baseline

- Overall next-day gap-up rate: `67.24%`
- Overall next-day open-to-close up rate: `46.8%`
- Average next-day open-to-close return: `-0.069%`

## 3 PM Candle Color

| 3 PM candle | N | Gap up | Gap down | Next day up | Avg O-C | Touches 3 PM close |
| --- | --- | --- | --- | --- | --- | --- |
| bullish | 1420 | 67.04% | 32.68% | 45.56% | -0.079% | 67.11% |
| bearish | 1347 | 67.48% | 32.52% | 48.18% | -0.058% | 60.73% |
| doji | 2 | 50.00% | 50.00% | 0.00% | -1.157% | 100.00% |

## Gap Size Buckets

| Gap bucket | N | Next day up | Avg O-C | Touches 3 PM close | Close above 3 PM close |
| --- | --- | --- | --- | --- | --- |
| gap down big <= -0.50% | 214 | 54.67% | 0.082% | 30.37% | 14.02% |
| gap up tiny 0% to 0.10% | 293 | 47.10% | -0.044% | 82.25% | 49.15% |
| gap down tiny 0% to -0.10% | 246 | 49.19% | -0.060% | 86.18% | 44.72% |
| gap up big >= 0.50% | 421 | 52.26% | -0.071% | 38.24% | 80.05% |
| gap down mid -0.50% to -0.10% | 443 | 44.92% | -0.092% | 65.01% | 31.83% |
| gap up mid 0.10% to 0.50% | 1148 | 43.47% | -0.096% | 69.95% | 61.41% |
| flat | 4 | 50.00% | -0.263% | 75.00% | 50.00% |

## Highest-Signal Candidate Buckets

These are sorted by absolute average next-day open-to-close return, with at least 100 observations.

| Family | Pattern | Gap bucket | N | Next day up | Avg O-C | Touches 3 PM close |
| --- | --- | --- | --- | --- | --- | --- |
| 3 PM color + gap bucket | bullish | gap up big >= 0.50% | 187 | 50.27% | -0.141% | 38.50% |
| 3 PM + 3:15 combo + gap bucket | bullish then bearish | gap up mid 0.10% to 0.50% | 360 | 37.78% | -0.138% | 78.33% |
| 3 PM + 3:15 combo + gap bucket | bearish then bearish | gap up mid 0.10% to 0.50% | 262 | 44.27% | -0.138% | 76.34% |
| 3 PM + 3:15 combo + gap bucket | bearish then bullish | gap down mid -0.50% to -0.10% | 119 | 45.38% | -0.135% | 72.27% |
| 3 PM + 3:15 combo + gap bucket | bearish then bullish | gap up big >= 0.50% | 121 | 52.07% | -0.125% | 35.54% |
| 3 PM color + gap bucket | bullish | gap up mid 0.10% to 0.50% | 610 | 40.66% | -0.121% | 72.13% |
| 3 PM + 3:15 combo + gap bucket | bearish then bearish | gap up big >= 0.50% | 111 | 55.86% | 0.118% | 40.54% |
| 3 PM color + gap bucket | bearish | gap down mid -0.50% to -0.10% | 217 | 44.24% | -0.110% | 63.59% |
| 3 PM + 3:15 combo + gap bucket | bullish then bearish | gap down mid -0.50% to -0.10% | 121 | 42.98% | -0.099% | 59.50% |
| 3 PM + 3:15 combo + gap bucket | bullish then bullish | gap up mid 0.10% to 0.50% | 249 | 44.98% | -0.090% | 63.05% |
| 3 PM color + gap bucket | bullish | gap down mid -0.50% to -0.10% | 225 | 45.78% | -0.072% | 66.22% |
| 3 PM color + gap bucket | bearish | gap up mid 0.10% to 0.50% | 538 | 46.65% | -0.067% | 67.47% |

## Files

- Raw daily observations: `../nifty_3pm_pattern_daily_observations.csv`
- Raw summary table: `../nifty_3pm_pattern_summary.csv`
- Charts: `charts/nifty_3pm_charts.html`
