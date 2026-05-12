from __future__ import annotations

import unittest
import shutil
import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

from options_backtest.dashboard_bridge import DashboardBridge
import options_backtest.dashboard_bridge as dashboard_bridge
from scripts.live.api.routes.backtests import router as backtests_router
from scripts.live.api.routes.historical import router as historical_router
from scripts.save_backtest import write_canonical_run


class FakeDashboardBridge:
    def historical_spot(self, symbol: str, start: date, end: date, tf: int) -> pd.DataFrame:
        df = pd.DataFrame([{
            "ts": "2026-05-11T09:15:00+05:30",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 10,
        }])
        df.attrs["source"] = "fake_spot.csv"
        df.attrs["warnings"] = ["fake_warning"]
        return df

    def historical_vix(self, start: date, end: date, tf: int) -> pd.DataFrame:
        return self.historical_spot("VIX", start, end, tf)

    def historical_options_metadata(self, symbol: str) -> dict:
        return {
            "symbol": symbol,
            "date_min": "2021-01-01",
            "date_max": "2026-05-11",
            "expiry_types": ["week", "month"],
            "atm_offsets": ["ATMm1", "ATM", "ATMp1"],
            "opt_types": ["call", "put"],
            "source": "fake_options",
        }

    def historical_options(
        self,
        symbol: str,
        expiry: str,
        strike: str,
        opt_type: str,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        df = self.historical_spot(symbol, start, end, 1)
        df["oi"] = 100
        df["iv_clean"] = 12.5
        return df

    def historical_bhavcopy(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        df = self.historical_spot(symbol, start, end, 1)
        df["settle_price"] = 100.4
        return df

    def backtest_list(self, sort_by: str = "date", sort_dir: str = "desc") -> list[dict]:
        return [self.backtest_summary("canon")]

    def backtest_summary(self, backtest_id: str) -> dict:
        if backtest_id == "missing":
            return {}
        return {
            "id": backtest_id,
            "name": "Canonical Smoke",
            "strategy": "iron-condor",
            "symbol": "NIFTY",
            "start_date": "2026-05-01",
            "end_date": "2026-05-11",
            "trades": 2,
            "net_pnl": 1250.0,
            "sharpe": 2.1,
            "source_kind": "canonical",
            "has_ledger": True,
            "has_equity": True,
            "has_decisions": False,
        }

    def backtest_equity_curve(self, backtest_id: str) -> pd.DataFrame:
        return pd.DataFrame([
            {"date": "2026-05-01", "equity": 1_000_000.0, "normalised": 1.0},
            {"date": "2026-05-02", "equity": 1_001_250.0, "normalised": 1.00125},
        ])

    def backtest_drawdown(self, backtest_id: str) -> pd.DataFrame:
        return pd.DataFrame([{"date": "2026-05-02", "drawdown_pct": -0.1}])

    def backtest_monthly_returns(self, backtest_id: str) -> pd.DataFrame:
        return pd.DataFrame([{"year": 2026, "month": 5, "net_pnl": 1250.0, "return_pct": 0.125}])

    def backtest_ledger(self, backtest_id: str) -> pd.DataFrame:
        return pd.DataFrame([
            {
                "entry_date": "2026-05-01",
                "exit_date": "2026-05-01",
                "symbol": "NIFTY",
                "expiry": "2026-05-07",
                "dte": 6,
                "vix": 14.2,
                "entry_premium": 1000.0,
                "gross_pnl": 800.0,
                "net_pnl": 750.0,
                "exit_reason": "time_exit",
            },
            {
                "entry_date": "2026-05-02",
                "exit_date": "2026-05-02",
                "symbol": "SENSEX",
                "expiry": "2026-05-07",
                "dte": 5,
                "vix": 15.0,
                "entry_premium": 900.0,
                "gross_pnl": 550.0,
                "net_pnl": 500.0,
                "exit_reason": "target",
            },
        ])

    def backtest_events(self, backtest_id: str) -> list[dict]:
        return [{"ts": "2026-05-01", "symbol": "NIFTY", "event_type": "entry", "label": "ENTRY"}]

    def backtest_decisions(self, backtest_id: str) -> pd.DataFrame:
        out = pd.DataFrame(columns=["ts", "symbol", "decision", "reason", "vix", "dte", "expiry", "eligible", "selected"])
        out.attrs["has_decisions"] = False
        return out


def _client() -> TestClient:
    app = FastAPI()
    app.state.bridge = FakeDashboardBridge()
    app.include_router(historical_router)
    app.include_router(backtests_router)
    return TestClient(app)


class DashboardV2RouteTests(unittest.TestCase):
    def test_historical_contract_includes_v2_metadata(self) -> None:
        resp = _client().get("/api/historical/spot/NIFTY?start=2026-05-01&end=2026-05-11&tf=5")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["symbol"], "NIFTY")
        self.assertEqual(body["row_count"], 1)
        self.assertEqual(body["source"], "fake_spot.csv")
        self.assertEqual(body["warnings"], ["fake_warning"])
        self.assertIn("stats", body)
        self.assertIn("generated_at", body)

    def test_unknown_historical_symbol_is_http_400(self) -> None:
        resp = _client().get("/api/historical/spot/UNKNOWN?start=2026-05-01&end=2026-05-11")
        self.assertEqual(resp.status_code, 400)

    def test_backtest_endpoints_expose_summary_and_drilldowns(self) -> None:
        client = _client()
        listing = client.get("/api/backtests").json()["backtests"]
        self.assertEqual(listing[0]["id"], "canon")
        self.assertFalse(listing[0]["legacy"])

        equity = client.get("/api/backtests/canon/equity-curve").json()
        self.assertEqual(equity["row_count"], 2)
        self.assertEqual(equity["stats"]["normalised_last"], 1.00125)

        ledger = client.get("/api/backtests/canon/ledger?symbol=NIFTY&size=1").json()
        self.assertEqual(ledger["total"], 1)
        self.assertEqual(ledger["rows"][0]["symbol"], "NIFTY")

        self.assertEqual(client.get("/api/backtests/canon/drawdown").json()[0]["drawdown_pct"], -0.1)
        self.assertEqual(client.get("/api/backtests/canon/monthly").json()[0]["month"], 5)
        self.assertEqual(client.get("/api/backtests/canon/decisions").json()["warnings"], ["no_decision_log"])

    def test_missing_backtest_summary_is_404(self) -> None:
        self.assertEqual(_client().get("/api/backtests/missing").status_code, 404)


class SaveBacktestTests(unittest.TestCase):
    def test_write_canonical_run_writes_dashboard_artifacts(self) -> None:
        root = Path.cwd() / "tmp_dashboard_v2_unit"
        if root.exists():
            shutil.rmtree(root)
        root.mkdir()
        try:
            ledger = pd.DataFrame([{"entry_date": "2026-05-01", "net_pnl": 100.0}])
            decisions = pd.DataFrame([{"ts": "2026-05-01T09:20:00", "decision": "enter"}])
            paths = write_canonical_run(
                root,
                "unit_run",
                {"id": "unit_run", "strategy": "iron-condor", "trades": 1, "net_pnl": 100.0},
                ledger,
                {"id": "unit_run", "engine": "test"},
                decisions,
            )
            self.assertTrue(paths["summary"].exists())
            self.assertTrue(paths["ledger"].exists())
            self.assertTrue(paths["manifest"].exists())
            self.assertTrue(paths["decisions"].exists())
            with self.assertRaises(FileExistsError):
                write_canonical_run(root, "unit_run", {}, ledger, {})
        finally:
            shutil.rmtree(root)


class DashboardBridgeBacktestTests(unittest.TestCase):
    def test_backtest_list_writes_persistent_index_with_groups_and_sort(self) -> None:
        root = Path.cwd() / "tmp_dashboard_v2_index"
        if root.exists():
            shutil.rmtree(root)
        runs = root / "dashboard_runs"
        index_root = root / "dashboard_index"
        index_path = index_root / "backtest_index.json"
        runs.mkdir(parents=True)
        try:
            (runs / "20260501_short_strangle_summary.json").write_text(
                json.dumps({
                    "id": "20260501_short_strangle",
                    "name": "20260501 short strangle",
                    "strategy": "short-strangle",
                    "net_pnl": 100.0,
                    "sharpe": 1.2,
                    "trades": 2,
                }),
                encoding="utf-8",
            )
            (runs / "20260502_ic_wing6_summary.json").write_text(
                json.dumps({
                    "id": "20260502_ic_wing6",
                    "name": "20260502 ic wing6",
                    "strategy": "iron-condor",
                    "net_pnl": 250.0,
                    "sharpe": 2.4,
                    "trades": 3,
                }),
                encoding="utf-8",
            )

            with (
                patch.object(dashboard_bridge, "_BACKTEST_ROOT", runs),
                patch.object(dashboard_bridge, "_LEGACY_BACKTEST_ROOTS", []),
                patch.object(dashboard_bridge, "_BACKTEST_INDEX_ROOT", index_root),
                patch.object(dashboard_bridge, "_BACKTEST_INDEX_PATH", index_path),
            ):
                bridge = DashboardBridge(root / "live")
                rows = bridge.backtest_list("net_pnl", "desc")
                self.assertTrue(index_path.exists())
                self.assertEqual([r["id"] for r in rows], ["20260502_ic_wing6", "20260501_short_strangle"])
                self.assertEqual(rows[0]["strategy_family"], "iron_condor")
                self.assertIn("/", rows[0]["group_path"])

                second_bridge = DashboardBridge(root / "live")
                cached = second_bridge.backtest_list("sharpe", "desc")
                self.assertEqual(cached[0]["id"], "20260502_ic_wing6")
        finally:
            shutil.rmtree(root)

    def test_equity_curve_aggregates_duplicate_exit_dates_for_charting(self) -> None:
        root = Path.cwd() / "tmp_dashboard_v2_bridge"
        if root.exists():
            shutil.rmtree(root)
        runs = root / "dashboard_runs"
        runs.mkdir(parents=True)
        try:
            (runs / "dupe_summary.json").write_text(
                json.dumps({"id": "dupe", "name": "Duplicate Dates", "has_ledger": True}),
                encoding="utf-8",
            )
            pd.DataFrame([
                {"exit_date": "2026-05-01", "entry_date": "2026-05-01", "net_pnl": 100.0},
                {"exit_date": "2026-05-01", "entry_date": "2026-05-01", "net_pnl": -25.0},
                {"exit_date": "2026-05-02", "entry_date": "2026-05-02", "net_pnl": 50.0},
            ]).to_csv(runs / "dupe_ledger.csv", index=False)

            with patch.object(dashboard_bridge, "_BACKTEST_ROOT", runs):
                bridge = DashboardBridge(root / "live")
                equity = bridge.backtest_equity_curve("dupe")

            self.assertEqual(len(equity), 2)
            self.assertEqual(equity.iloc[0]["equity"], 1_000_075.0)
            self.assertFalse(equity["date"].duplicated().any())
        finally:
            shutil.rmtree(root)


if __name__ == "__main__":
    unittest.main()
