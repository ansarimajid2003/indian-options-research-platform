# NIFTY Expiry Calendar Rules

This engine uses explicit NIFTY expiry selection instead of assuming every
weekly contract expires on Thursday.

## Weekly NIFTY

- Through `2025-08-28`, weekly NIFTY contracts use Thursday expiry.
- From contracts expiring on or after `2025-09-01`, weekly NIFTY contracts use
  Tuesday expiry. The first Tuesday weekly expiry is `2025-09-02`.
- If the scheduled expiry is a trading holiday, expiry is moved to the previous
  trading session when a trading calendar is supplied.

Sources:

- NSE circular `NSE/FAOP/68747`, dated `2025-06-25`
  (`https://nsearchives.nseindia.com/content/circulars/FAOP68747.pdf`): revised
  NIFTY weekly expiry from Thursday of the week to Tuesday of the week, with
  existing contracts expiring on or before `2025-08-31` unchanged.
- NSE NIFTY 50 F&O contract page
  (`https://www.nseindia.com/products-services/equity-derivatives-nifty50`):
  current weekly options expire every Tuesday of the expiry week, or the previous
  trading day if Tuesday is a holiday.

## Monthly NIFTY

- Through August 2025, monthly NIFTY contracts use the last Thursday of the
  expiry month.
- From September 2025 onward, monthly NIFTY contracts use the last Tuesday of
  the expiry month.

## Backtest Policy

Intraday strategies may use `DTE=0` only when the strategy intentionally enters
and exits before the same contract expires.

Strategies with `next_day_exit=True` must use `min_dte=1`, so an entry on expiry
day selects the next available weekly/monthly expiry. If the loaded vendor data
does not contain that next-expiry contract at entry time, the trade is skipped
rather than filled from the expiring contract.

In Dhan rolling-option data, the raw rows do not contain an explicit expiry
field. `options_backtest.dhan_loader` infers the front-contract expiry for each
timestamp using `options_backtest.calendar`, then filters the resolver to the
selected contract expiry before entry, stop/target, and exit fills.
