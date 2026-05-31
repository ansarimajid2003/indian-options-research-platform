"""Transport-layer tests for the live Dhan plumbing.

Covers:
- depth-feed disconnect parser (collect_order_book._disconnect_code)
- live-feed disconnect parser (paper_engine._parse_disconnect_code)
- fatal disconnect codes (805/806/807/808/809) match the SDK constants
- restart-grace calculation (run_paper_trading._compute_restart_grace_seconds)

These tests are protocol-level: they assert byte offsets and code values
against the official DhanHQ-py SDK vendored under third_party/.
"""

from __future__ import annotations

import json
import os
import struct
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from options_backtest.paper_engine import (
    _FATAL_DISCONNECT_CODES as PAPER_FATAL_CODES,
    _parse_disconnect_code,
)
from scripts.live.collect_order_book import (
    _DISCONNECT_CODE,
    _DISCONNECT_REASONS,
    _FATAL_DISCONNECT_CODES,
    _HEADER_FMT,
    _UNSUB_CODE,
    _disconnect_code,
)
from scripts.live.run_paper_trading import (
    _RESTART_GRACE_SECONDS,
    _compute_restart_grace_seconds,
)


def _depth_disconnect_packet(code: int) -> bytes:
    """Construct a Dhan 20-depth disconnect packet matching SDK layout.

    Header: <hBBiI> (12 bytes) — msg_len, feed_code, exch_seg,
    security_id, reserved_or_disconnect_code.
    """
    return struct.pack(_HEADER_FMT, 12, _DISCONNECT_CODE, 2, 0, code)


def _live_disconnect_packet(code: int) -> bytes:
    """Construct a Dhan live-feed disconnect packet.

    First byte is the packet type (50 = disconnect); code is a uint16 at
    byte offset 8. We pad to 10 bytes to match the SDK's struct.unpack
    pattern ``<BHBIH`` from data[0:10].
    """
    # B(1):type=50  H(2):msg_len  B(1):exch_seg  I(4):security_id  H(2):code
    return struct.pack("<BHBIH", 50, 10, 0, 0, code)


class DepthDisconnectParserTests(unittest.TestCase):
    def test_805_is_decoded(self):
        pkt = _depth_disconnect_packet(805)
        self.assertEqual(_disconnect_code(pkt), 805)

    def test_807_is_decoded(self):
        # 807 == access token expired
        self.assertEqual(_disconnect_code(_depth_disconnect_packet(807)), 807)

    def test_non_disconnect_returns_none(self):
        # Build a synthetic bid (code 41) packet header — must not be
        # mistaken for a disconnect.
        bid = struct.pack(_HEADER_FMT, 332, 41, 2, 9999, 0)
        self.assertIsNone(_disconnect_code(bid))

    def test_short_packet_returns_none(self):
        self.assertIsNone(_disconnect_code(b"\x00\x01\x02"))

    def test_fatal_codes_match_sdk(self):
        # From third_party/DhanHQ-py/src/dhanhq/fulldepth.py:358-376
        sdk_codes = {805, 806, 807, 808, 809}
        self.assertEqual(_FATAL_DISCONNECT_CODES, sdk_codes)

    def test_disconnect_reasons_have_strings_for_all_codes(self):
        for code in _FATAL_DISCONNECT_CODES:
            self.assertIn(code, _DISCONNECT_REASONS)
            self.assertTrue(_DISCONNECT_REASONS[code])

    def test_unsubscribe_code_is_12(self):
        # Required by SDK marketfeed.py:186-194 / fulldepth.py:132-137.
        self.assertEqual(_UNSUB_CODE, 12)


class LiveFeedDisconnectParserTests(unittest.TestCase):
    def test_805_is_decoded(self):
        pkt = _live_disconnect_packet(805)
        self.assertEqual(_parse_disconnect_code(pkt), 805)

    def test_fatal_set_matches_sdk(self):
        # SDK marketfeed.py:493-505 documents 805..809 as fatal.
        self.assertEqual(PAPER_FATAL_CODES, {805, 806, 807, 808, 809})

    def test_non_disconnect_returns_none(self):
        # First byte != 50 → not a disconnect.
        self.assertIsNone(_parse_disconnect_code(b"\x08" + b"\x00" * 20))


class RestartGraceTests(unittest.TestCase):
    def test_no_previous_file_returns_zero(self):
        with TemporaryDirectory() as td:
            self.assertEqual(
                _compute_restart_grace_seconds(Path(td), date(2026, 5, 31)),
                0.0,
            )

    def test_previous_session_different_date_returns_zero(self):
        with TemporaryDirectory() as td:
            live_root = Path(td)
            self._write_health(live_root, "2026-05-30", os.getpid() + 1, datetime.now())
            self.assertEqual(
                _compute_restart_grace_seconds(live_root, date(2026, 5, 31)),
                0.0,
            )

    def test_completed_phase_returns_zero(self):
        with TemporaryDirectory() as td:
            live_root = Path(td)
            self._write_health(
                live_root,
                "2026-05-31",
                os.getpid() + 1,
                datetime.now(),
                phase="complete",
            )
            self.assertEqual(
                _compute_restart_grace_seconds(live_root, date(2026, 5, 31)),
                0.0,
            )

    def test_recent_other_pid_returns_remaining_grace(self):
        # Other PID wrote 20s ago → 40s remaining of the 60s grace.
        with TemporaryDirectory() as td:
            live_root = Path(td)
            written = datetime(2026, 5, 31, 9, 30, 0)
            self._write_health(live_root, "2026-05-31", 99999, written)
            now = written + timedelta(seconds=20)
            remaining = _compute_restart_grace_seconds(
                live_root, date(2026, 5, 31), now=now,
            )
            self.assertAlmostEqual(remaining, 40.0, delta=0.5)

    def test_same_pid_returns_zero(self):
        with TemporaryDirectory() as td:
            live_root = Path(td)
            written = datetime(2026, 5, 31, 9, 30, 0)
            self._write_health(live_root, "2026-05-31", os.getpid(), written)
            now = written + timedelta(seconds=5)
            self.assertEqual(
                _compute_restart_grace_seconds(
                    live_root, date(2026, 5, 31), now=now,
                ),
                0.0,
            )

    def test_old_other_pid_returns_zero(self):
        # 5 minutes old → no grace needed.
        with TemporaryDirectory() as td:
            live_root = Path(td)
            written = datetime(2026, 5, 31, 9, 30, 0)
            self._write_health(live_root, "2026-05-31", 99999, written)
            now = written + timedelta(seconds=_RESTART_GRACE_SECONDS + 5)
            self.assertEqual(
                _compute_restart_grace_seconds(
                    live_root, date(2026, 5, 31), now=now,
                ),
                0.0,
            )

    @staticmethod
    def _write_health(
        live_root: Path,
        session_date: str,
        pid: int,
        written_at: datetime,
        *,
        phase: str = "running",
    ) -> None:
        snapshot_dir = live_root / "snapshots"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        path = snapshot_dir / "latest_process_health.json"
        path.write_text(
            json.dumps(
                {
                    "session_date": session_date,
                    "pid": pid,
                    "phase": phase,
                    "written_at": written_at.isoformat(),
                }
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
