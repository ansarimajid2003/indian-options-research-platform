from __future__ import annotations

import unittest
import shutil
from datetime import date
from pathlib import Path

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

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

    def backtest_list(self) -> list[dict]:
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


if __name__ == "__main__":
    unittest.main()
