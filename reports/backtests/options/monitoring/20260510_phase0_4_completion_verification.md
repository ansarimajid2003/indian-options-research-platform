# Phase 0-4 Completion Verification - Live Paper Trading

**Date:** 2026-05-10  
**Scope:** `docs/design/live_paper_trading_plan.md`, laptop checkout, `zimaos` server checkout, and DhanHQ API/SDK structure.

## Verdict

Phase 0-2 are partially/mostly complete on the server, with important caveats. Phase 3-4 are **not complete end-to-end** because the implementation exists only on the laptop and has live-path blockers. The server is not deployed with Phase 3 or Phase 4 files.

## Laptop Verification

PASS:
- New local files exist for Phase 3/4:
  - `options_backtest/depth_cache.py`
  - `options_backtest/live_resolver.py`
  - `scripts/live/collect_order_book.py`
  - `options_backtest/paper_engine.py`
  - `scripts/live/run_paper_trading.py`
  - `scripts/live/paper_json_to_ledger.py`
  - `scripts/live/health_monitor.py`
  - `scripts/live/renew_token.py`
  - `scripts/live/sample_option_chain.py`
- `python -m py_compile` passed for the new live modules.
- `python -m unittest discover -s tests -v` passed: 54 tests OK.
- `scripts/live/collect_order_book.py --dry-run` ran and wrote a dry-run gap sentinel.

FAIL / BLOCKERS:
- `options_backtest/paper_engine.py` live feed URL formatting is wrong:
  - `_FEED_URL` uses `{client_id}`.
  - `_feed_loop()` calls `_FEED_URL.format(token=..., clientId=...)`.
  - This raises `KeyError: 'client_id'` before websocket connection.
- `scripts/live/run_paper_trading.py --dry-run` exits on non-trading days before dry-run validation, so it cannot perform the planned Saturday/non-trading dry-run.
- `scripts/live/run_paper_trading.py` uses `asyncio.gather(collect_order_book(...), engine.run())`; the collector has no normal stop path after EOD, so the orchestrator can hang after the engine finishes.
- `scripts/live/collect_order_book.py` claims ATM-range selection, but `_build_subscription_universe()` appends every option-chain security id. This can blow through the intended configured major-index depth budget.
- `scripts/live/dhan_connection_check.py` tests 20-depth using `ExchangeSegment="IDX_I"` and `SecurityId=13`. Dhan full-depth is only for NSE equity and derivatives; this confirms socket auth/open only, not a valid option-depth subscription.
- `paper_json_to_ledger.py` only prints a DataFrame and does not generate the planned markdown/json summary through `reports.summary()` / `reports.daily_pnl()`.

## Server Verification - `zimaos`

PASS:
- Repo path: `/DATA/live-paper/indian-markets`
- Server HEAD: `a378b18 Fix connection check: Dhan REST endpoint moved to /v2/, response has no status field`
- `data/live` resolves to `/media/WD-Storage/indian-markets-live`.
- Storage:
  - `/DATA`: 222 GB total, 173 GB free.
  - `/media/WD-Storage`: 466 GB total, 216 GB free.
- Python:
  - system Python 3.12.12
  - venv Python 3.12.12
- Dhan redacted connection check passed:
  - token valid for 0.9 days
  - expiry list returned 18 expiries
  - live feed websocket connected
  - depth websocket connected

FAIL / BLOCKERS:
- Server clock sync check returned `NTPSynchronized=no`.
- Server checkout only has:
  - `configs/live/wing6_4x1_all_vix_filtered.json`
  - `scripts/live/dhan_connection_check.py`
- Server is missing all Phase 3/4 implementation files:
  - `options_backtest/depth_cache.py`
  - `options_backtest/live_resolver.py`
  - `scripts/live/collect_order_book.py`
  - `options_backtest/paper_engine.py`
  - `scripts/live/run_paper_trading.py`
  - `scripts/live/paper_json_to_ledger.py`
  - `scripts/live/health_monitor.py`
  - `scripts/live/renew_token.py`
  - `scripts/live/sample_option_chain.py`

## Dhan API / SDK Structure Verification

Official Dhan docs and current live response shape agree on the current option-chain shape:
- `POST /v2/optionchain/expirylist` returns `data` as a flat list of expiry date strings.
- `POST /v2/optionchain` currently returns:
  - top keys: `data`, `status`
  - `data` type: dict
  - `data` keys: `last_price`, `oc`
  - `oc` type: dict keyed by strike string, e.g. `17850.000000`
  - each strike has `ce` and `pe`
  - option fields include `security_id`, `top_bid_price`, `top_ask_price`, `top_bid_quantity`, `top_ask_quantity`, `oi`, `volume`, `implied_volatility`, `greeks`

This does **not** match the local resolver/sample-script primary parser, which expects a flat list with `CallOption` / `PutOption` and PascalCase fields. That parser must be fixed before Phase 3 can be marked complete.

Official full-depth structure:
- 20-depth endpoint: `wss://depth-api-feed.dhan.co/twentydepth?...`
- 200-depth endpoint: `wss://full-depth-api.dhan.co/twohundreddepth?...`
- Full market depth is only enabled for NSE equity and derivatives.
- 20-depth allows up to 50 instruments per connection.
- Request code is `23`.
- 20-depth response header is 12 bytes: int16 message length, uint8 feed code, uint8 exchange segment, int32 security id, uint32 message sequence.
- Bid feed code is `41`; ask feed code is `51`.
- Each depth level is 16 bytes: float64 price, uint32 quantity, uint32 order count.

The local parser in `collect_order_book.py` uses the correct binary struct for 20-depth (`<hBBiI` and `<dII`), but the top module comment is stale and describes the old incorrect format.

## Phase-by-Phase Status

| Phase | Laptop | Server | Verdict |
|---|---|---|---|
| Phase 0 - Server preparation | n/a | Mostly pass, but clock sync is currently `no`; static IP still manual | Partial |
| Phase 1 - Credentials/external services | n/a | Dhan check passes; server env works; heartbeat/Sentry not fully re-tested in this pass | Partial |
| Phase 2 - Locked profile + smoke test | Profile and connection script exist | Profile and connection script exist; Dhan check passes | Mostly pass, but 20-depth test is too weak |
| Phase 3 - Depth cache/live resolver/collector | Files exist and compile | Files missing | Not complete |
| Phase 4 - Paper engine/runner/ledger | Files exist and compile, but live blockers remain | Files missing | Not complete |

## Required Fixes Before Live Paper Session

1. Fix Dhan option-chain parsing to support current `data.oc.{strike}.ce/pe` shape.
2. Fix `paper_engine.py` URL formatting: pass `client_id=...`.
3. Change 20-depth smoke test to subscribe to a valid `NSE_FNO` option `SecurityId`, not `IDX_I` NIFTY spot.
4. Restrict depth subscriptions to the intended Wing-6 universe instead of all strikes.
5. Add a clean shutdown path so `run_paper_trading.py` stops collectors after EOD.
6. Let `--dry-run` run on non-trading days.
7. Implement `paper_json_to_ledger.py` report generation through `reports.summary()` and `reports.daily_pnl()`.
8. Deploy Phase 3/4 files to `zimaos` only after local fixes pass smoke tests.
9. Fix server clock sync before market-hours collection.

## Fix Pass - 2026-05-10

Status after remediation:

- Fixed `LiveDhanContractResolver` to parse current Dhan `data.oc.{strike}.ce/pe` responses and legacy flat `CallOption` / `PutOption` responses.
- Fixed `sample_option_chain.py` for current lower-case Dhan fields.
- Fixed `paper_engine.py` live-feed URL formatting (`client_id`) and limited live-feed option subscriptions to the configured ATM +/- 20 chain window.
- Fixed `collect_order_book.py` to parse stacked Dhan depth packets and to subscribe only the configured major-index ATM +/- 20 NSE option universe.
- Fixed `dhan_connection_check.py` so the 20-depth smoke test discovers a real NIFTY option `security_id` and subscribes with `ExchangeSegment="NSE_FNO"`.
- Fixed `run_paper_trading.py --dry-run` to run on non-trading days.
- Fixed `run_paper_trading.py` collector lifecycle so the order-book collector is cancelled cleanly after the paper engine finishes.
- Implemented `paper_json_to_ledger.py` report generation through `reports.daily_pnl()`, `reports.equity_curve()`, and `reports.summary()`.
- Added `tests/test_live_paper.py` covering current Dhan option-chain shape, stacked depth packets, URL formatting, and paper report outputs.
- Made token-renewal imports lazy and installed `pyotp==2.9.0` in the zimaos venv for actual Dhan token renewal.
- Enabled/check clock sync on zimaos; `NTPSynchronized=yes` after remediation.

Verification evidence:

- Laptop: `python -m py_compile ...` passed for all live-paper modules.
- Laptop: `python -m unittest discover -s tests -v` -> 58 tests OK.
- Laptop: `scripts/live/run_paper_trading.py --profile wing6_4x1_all_vix_filtered --dry-run` completed on Sunday 2026-05-10.
- zimaos: deployed Phase 3/4 files to `/DATA/live-paper/indian-markets`.
- zimaos: `python -m py_compile ...` passed for all deployed live-paper modules.
- zimaos: `python -m unittest tests.test_live_paper -v` -> 4 tests OK.
- zimaos: `python -m unittest discover -s tests -v` -> 58 tests OK, 22 skipped because Shoonya raw data is absent on server.
- zimaos: updated `dhan_connection_check.py` -> ALL PASS (4/4), including real NSE_FNO option depth subscription.
- zimaos: `sample_option_chain.py --symbol NIFTY --strikes 1` parsed live Dhan response successfully; spot 24176.15, ATM 24200.0.
- zimaos: `collect_order_book.py --dry-run` wrote a dry-run gap sentinel.
- zimaos: `run_paper_trading.py --profile wing6_4x1_all_vix_filtered --dry-run` completed.
- zimaos: `renew_token.py --check` showed the token valid for 20h 24m at verification time.

Updated phase verdict:

| Phase | Laptop | Server | Verdict |
|---|---|---|---|
| Phase 0 - Server preparation | n/a | Storage/path/Python OK; clock sync now `yes`; static IP still manual ISP item | Mostly complete |
| Phase 1 - Credentials/external services | n/a | Dhan and token-check pass; pyotp installed; Healthchecks/Sentry/Telegram full alarm drills not re-run in this pass | Partial |
| Phase 2 - Locked profile + smoke test | Profile and connection script fixed | Profile and fixed connection script deployed; Dhan check passes | Complete for connectivity |
| Phase 3 - Depth cache/live resolver/collector | Code fixed; tests pass | Code deployed; tests pass; collector dry-run pass | Code complete, market-hours packet flow still needs live-session validation |
| Phase 4 - Paper engine/runner/ledger | Code fixed; dry-run pass; tests pass | Code deployed; dry-run pass; tests pass | Code complete, first market-hours session still pending |

Remaining before first live paper session:

- Run market-hours session to verify actual tick delivery, quote freshness, and 20-depth packet flow.
- Run simulated failure drills for preflight gates and crash recovery.
- Make the Healthchecks/Sentry/Telegram alarm drills pass end-to-end.
- Confirm ACT/static egress IP before relying on Dhan IP whitelist.
