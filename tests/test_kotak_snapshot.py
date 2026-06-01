import json
import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.live.snapshot_kotak_limits import (
    fetch_and_write_snapshot,
    naked_leg_margin,
    normalize_limits,
)

_SAMPLE_LIMITS = {
    "Net": "962345.50",
    "MarginUsed": "37654.50",
    "Collateral": "0",
    "CollateralValue": "0",
    "FoUnRlsMtomPrsnt": "-1200.0",
    "FoRlsMtomPrsnt": "2500.0",
    "stCode": 200,
    "stat": "Ok",
}


class NormalizeLimitsTests(unittest.TestCase):
    def test_maps_real_fields(self):
        snap = normalize_limits(_SAMPLE_LIMITS)
        self.assertAlmostEqual(snap["broker_net"], 962345.50)
        self.assertAlmostEqual(snap["broker_margin_used"], 37654.50)
        self.assertAlmostEqual(snap["broker_collateral"], 0.0)
        self.assertAlmostEqual(snap["broker_fo_unrealized"], -1200.0)
        self.assertAlmostEqual(snap["broker_fo_realized"], 2500.0)
        self.assertEqual(snap["stat"], "Ok")
        self.assertEqual(snap["stCode"], 200)
        self.assertIn("captured_at", snap)

    def test_injected_now_is_deterministic(self):
        fixed = datetime(2026, 6, 2, 9, 15, 0)
        snap = normalize_limits(_SAMPLE_LIMITS, now=fixed)
        self.assertEqual(snap["captured_at"], "2026-06-02T09:15:00")

    def test_missing_keys_become_none(self):
        snap = normalize_limits({"Net": "100.0"})
        self.assertAlmostEqual(snap["broker_net"], 100.0)
        self.assertIsNone(snap["broker_margin_used"])
        self.assertIsNone(snap["broker_fo_unrealized"])


class FetchAndWriteTests(unittest.TestCase):
    def test_writes_valid_snapshot_file(self):
        client = Mock()
        client.limits.return_value = _SAMPLE_LIMITS
        with TemporaryDirectory() as tmp:
            out = fetch_and_write_snapshot(client, Path(tmp), date(2026, 6, 2))
            self.assertEqual(out.name, "kotak_limits_20260602.json")
            client.limits.assert_called_once_with(segment="ALL", exchange="ALL", product="ALL")
            data = json.loads(out.read_text())
            self.assertAlmostEqual(data["broker_margin_used"], 37654.50)
            self.assertAlmostEqual(data["broker_net"], 962345.50)

    def test_error_response_raises(self):
        client = Mock()
        client.limits.return_value = {"Error Message": "Complete the 2fa process..."}
        with TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                fetch_and_write_snapshot(client, Path(tmp), date(2026, 6, 2))


class NakedLegMarginTests(unittest.TestCase):
    def test_returns_ord_mrgn_float(self):
        client = Mock()
        client.margin_required.return_value = {"data": {"ordMrgn": "15000.0", "stat": "Ok"}}
        val = naked_leg_margin(
            client,
            exchange_segment="nse_fo",
            price=60.0,
            order_type="L",
            product="NRML",
            quantity=65,
            instrument_token=12345,
            transaction_type="S",
        )
        self.assertEqual(val, 15000.0)
        self.assertIsInstance(val, float)


if __name__ == "__main__":
    unittest.main()
