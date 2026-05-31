"""Unit tests for the shared DhanHTTPClient.

Covers: rate-limiter pacing, 429 + Retry-After handling, 5xx retry,
4xx fatal raise, credential rotation, and the security-id constants.
The tests stub ``requests.Session.post`` so no network is touched.
"""

from __future__ import annotations

import threading
import time as _time
import unittest
from unittest.mock import MagicMock

from options_backtest.dhan_client import (
    DhanCredentials,
    DhanHTTPClient,
    DhanRESTError,
    DhanRateLimitError,
    _EndpointLimiter,
    get_dhan_client,
    reset_dhan_client,
)
from options_backtest.dhan_instruments import (
    NSE_INDEX_DEPTH_SYMBOLS,
    SPOT_SECURITY_IDS,
    VIX_SECURITY_ID,
    all_idx_security_ids,
    all_idx_security_ids_int,
)


def _ok_response(payload):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    return resp


def _err_response(status, *, retry_after=None, body=None):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body if body is not None else {"error": "boom"}
    resp.text = "boom"
    resp.headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
    return resp


class EndpointLimiterTests(unittest.TestCase):
    def test_acquire_enforces_minimum_interval(self):
        limiter = _EndpointLimiter(interval=0.25)
        t0 = _time.monotonic()
        limiter.acquire()
        limiter.acquire()
        elapsed = _time.monotonic() - t0
        # Second acquire must wait ~0.25s.
        self.assertGreaterEqual(elapsed, 0.20)
        self.assertLess(elapsed, 0.6)

    def test_acquire_is_thread_safe(self):
        limiter = _EndpointLimiter(interval=0.1)
        latencies = []
        lock = threading.Lock()

        def worker():
            start = _time.monotonic()
            limiter.acquire()
            with lock:
                latencies.append(_time.monotonic() - start)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        t0 = _time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        total = _time.monotonic() - t0
        # 4 calls with 0.1s spacing should serialise to ~0.3s, never less.
        self.assertGreaterEqual(total, 0.25)


class DhanHTTPClientTests(unittest.TestCase):
    def setUp(self):
        reset_dhan_client(None)
        self.creds = DhanCredentials(access_token="tok", client_id="cid")
        self.client = DhanHTTPClient(self.creds)
        self.client._session = MagicMock()

    def tearDown(self):
        reset_dhan_client(None)

    def test_fetch_expiry_list_returns_strings(self):
        self.client._session.post.return_value = _ok_response(
            {"data": ["2026-06-05", "2026-06-12"]}
        )
        result = self.client.fetch_expiry_list(13, "IDX_I")
        self.assertEqual(result, ["2026-06-05", "2026-06-12"])

    def test_fetch_expiry_list_handles_dict_wrapped_shape(self):
        self.client._session.post.return_value = _ok_response(
            {"data": {"Expirylist": ["2026-06-05"]}}
        )
        self.assertEqual(self.client.fetch_expiry_list(13, "IDX_I"), ["2026-06-05"])

    def test_fetch_option_chain_returns_data_block(self):
        body = {"data": {"last_price": 25000.0, "oc": {}}}
        self.client._session.post.return_value = _ok_response(body)
        result = self.client.fetch_option_chain(13, "IDX_I", "2026-06-05")
        self.assertEqual(result["last_price"], 25000.0)

    def test_429_then_success_retries_with_retry_after(self):
        sequence = [
            _err_response(429, retry_after=0.05),
            _ok_response({"data": []}),
        ]
        self.client._session.post.side_effect = sequence
        t0 = _time.monotonic()
        self.client.fetch_expiry_list(13, "IDX_I")
        elapsed = _time.monotonic() - t0
        self.assertGreaterEqual(elapsed, 0.04)
        self.assertEqual(self.client._session.post.call_count, 2)

    def test_5xx_then_success_retries(self):
        sequence = [
            _err_response(500),
            _err_response(503),
            _ok_response({"data": []}),
        ]
        self.client._session.post.side_effect = sequence
        self.client._session.post.reset_mock()
        # Shorten the backoff for speed: monkey-patch _BACKOFF_BASE via attribute access on the module.
        import options_backtest.dhan_client as mod
        original = mod._BACKOFF_BASE
        mod._BACKOFF_BASE = 0.01
        try:
            self.client.fetch_expiry_list(13, "IDX_I")
        finally:
            mod._BACKOFF_BASE = original
        self.assertEqual(self.client._session.post.call_count, 3)

    def test_400_raises_without_retry(self):
        self.client._session.post.return_value = _err_response(400)
        with self.assertRaises(DhanRESTError) as cm:
            self.client.fetch_expiry_list(13, "IDX_I")
        self.assertEqual(cm.exception.status_code, 400)
        self.assertEqual(self.client._session.post.call_count, 1)

    def test_persistent_429_eventually_raises_rate_limit_error(self):
        self.client._session.post.return_value = _err_response(429, retry_after=0.01)
        # max_retries=5 ⇒ should raise after 5 attempts.
        with self.assertRaises(DhanRateLimitError):
            self.client.fetch_expiry_list(13, "IDX_I")
        self.assertEqual(self.client._session.post.call_count, 5)

    def test_rotate_token_updates_session_header(self):
        # Set up a real-ish session for header assertion.
        self.client._session = MagicMock(spec=["post", "headers", "mount", "close"])
        self.client._session.headers = {}
        self.client._refresh_session_headers()
        self.assertEqual(self.client._session.headers["access-token"], "tok")
        self.client.rotate_token("new-tok")
        self.assertEqual(self.client._session.headers["access-token"], "new-tok")
        self.assertEqual(self.client._session.headers["client-id"], "cid")

    def test_endpoint_limiter_shared_across_calls(self):
        # Two consecutive fetch_option_chain calls must be at least 1 s apart
        # (per Dhan documented limit, enforced by limiter interval 1.0 s).
        self.client._session.post.return_value = _ok_response({"data": {}})
        # Use a smaller interval so the test runs in reasonable time.
        self.client._endpoints.option_chain = _EndpointLimiter(interval=0.2)
        t0 = _time.monotonic()
        self.client.fetch_option_chain(13, "IDX_I", "2026-06-05")
        self.client.fetch_option_chain(13, "IDX_I", "2026-06-05")
        elapsed = _time.monotonic() - t0
        self.assertGreaterEqual(elapsed, 0.18)


class GetDhanClientSingletonTests(unittest.TestCase):
    def setUp(self):
        reset_dhan_client(None)

    def tearDown(self):
        reset_dhan_client(None)

    def test_returns_same_instance(self):
        creds = DhanCredentials(access_token="t", client_id="c")
        a = get_dhan_client(creds)
        b = get_dhan_client(creds)
        self.assertIs(a, b)

    def test_reset_clears(self):
        creds = DhanCredentials(access_token="t", client_id="c")
        a = get_dhan_client(creds)
        reset_dhan_client(None)
        b = get_dhan_client(creds)
        self.assertIsNot(a, b)


class InstrumentConstantsTests(unittest.TestCase):
    def test_spot_dict_has_five_indices(self):
        self.assertEqual(
            set(SPOT_SECURITY_IDS),
            {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"},
        )

    def test_all_idx_security_ids_is_stable_order(self):
        ids = all_idx_security_ids()
        self.assertEqual(ids, ["13", "25", "27", "442", "51", "21"])
        self.assertEqual(all_idx_security_ids_int(), [13, 25, 27, 442, 51, 21])

    def test_nse_depth_excludes_sensex(self):
        self.assertNotIn("SENSEX", NSE_INDEX_DEPTH_SYMBOLS)
        self.assertIn("NIFTY", NSE_INDEX_DEPTH_SYMBOLS)

    def test_vix_id_is_21(self):
        self.assertEqual(VIX_SECURITY_ID, "21")


if __name__ == "__main__":
    unittest.main()
