# Prior 3 PM Close Touch-Level Report

This report asks: after the next-day gap, does NIFTY trade back to the prior day's 3 PM candle close?

Interpretation:

- `touched = True` means any next-day 15-minute candle traded through the prior 3 PM close.
- `next day up` is next day close versus next day open.
- `after touch up` is close versus the first candle close that touched the level.

## Touch Versus No Touch

| 3 PM candle | Gap | Touched | N | Next day up | Next day down | Avg O-C | Close above level |
| --- | --- | --- | --- | --- | --- | --- | --- |
| bullish | flat | no | 1 | 0.00% | 100.00% | -0.842% | 0.00% |
| bullish | flat | yes | 3 | 66.67% | 33.33% | -0.070% | 66.67% |
| bearish | gap down | no | 186 | 21.51% | 78.49% | -0.510% | 1.61% |
| bearish | gap down | yes | 252 | 65.48% | 34.52% | 0.253% | 51.19% |
| bullish | gap down | no | 152 | 26.32% | 73.68% | -0.414% | 3.95% |
| bullish | gap down | yes | 312 | 61.54% | 38.46% | 0.183% | 45.83% |
| doji | gap down | yes | 1 | 0.00% | 100.00% | -0.477% | 0.00% |
| bearish | gap up | no | 343 | 76.09% | 23.91% | 0.378% | 96.50% |
| bearish | gap up | yes | 566 | 32.33% | 67.67% | -0.311% | 48.59% |
| bullish | gap up | no | 314 | 76.11% | 23.89% | 0.309% | 96.18% |
| bullish | gap up | yes | 638 | 27.27% | 72.73% | -0.316% | 43.57% |
| doji | gap up | yes | 1 | 0.00% | 100.00% | -1.839% | 0.00% |

## First Touch Timing

| Gap | First touch period | N | Next day up | Next day down | Avg O-C | After touch up |
| --- | --- | --- | --- | --- | --- | --- |
| flat | opening 5 bars | 3 | 66.67% | 33.33% | -0.070% | 66.67% |
| gap down | afternoon | 47 | 80.85% | 19.15% | 0.507% | 46.81% |
| gap down | closing hour | 38 | 92.11% | 7.89% | 0.460% | 42.11% |
| gap down | midday | 38 | 78.95% | 21.05% | 0.787% | 57.89% |
| gap down | opening 5 bars | 442 | 57.47% | 42.53% | 0.111% | 46.15% |
| gap up | afternoon | 101 | 15.84% | 84.16% | -0.574% | 48.51% |
| gap up | closing hour | 58 | 8.62% | 91.38% | -0.558% | 55.17% |
| gap up | midday | 138 | 28.99% | 71.01% | -0.357% | 57.25% |
| gap up | opening 5 bars | 908 | 32.60% | 67.40% | -0.265% | 45.59% |

## Practical Read

The prior 3 PM close behaves like a decision level. Gap-ups that do not revisit it tend to continue upward intraday. Gap-ups that revisit it tend to fade. Gap-downs show the mirror image: no revisit tends to stay weak, while a revisit often means recovery.
