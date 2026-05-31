"""
Single shared Dhan REST client for the live paper-trading stack.

Replaces the six independent ``requests.post()`` call sites scattered across
``live_resolver.py``, ``paper_engine.py``, ``collect_order_book.py`` (via
resolvers), ``dhan_connection_check.py``, ``sample_option_chain.py``, and the
download scripts.

Design contract:

* One ``requests.Session`` per ``DhanHTTPClient`` instance so connection
  reuse, header consistency, and retry policy live in exactly one place.
* One process-global instance owned by ``get_dhan_client()`` so the rate
  limiter is shared across every consumer in the same process. Tests can
  override the global with ``reset_dhan_client(client)``.
* Per-endpoint rate limiting matching Dhan's published v2 limits:

    /optionchain                 : 1 req / 1.0 s
    /optionchain/expirylist      : 1 req / 3.0 s
    /marketfeed/ltp              : 1 req / 1.0 s

  Rate limiting is enforced by a ``threading.Lock`` + ``last_call_at``
  timestamp. Calls block (sleep) until the per-endpoint slot is free. This
  works correctly when called from either threads or asyncio workers
  scheduled via ``asyncio.to_thread`` (which is how every existing caller
  reaches us).
* 429 / 5xx retries with exponential backoff and Retry-After honouring,
  capped at ``_MAX_RETRIES`` attempts.
* Credentials live in one ``DhanCredentials`` dataclass refreshed at
  startup and on token rotation. No call site reads ``os.environ`` directly
  for Dhan auth after migration.

This client does NOT manage websockets. Live-feed and 20-depth websockets
are owned by ``paper_engine.py`` and ``collect_order_book.py`` respectively
and use the same credentials object for query-string auth.

References:
    - third_party/DhanHQ-py/src/dhanhq/dhan_http.py (session/header shape)
    - Dhan API v2 docs: https://dhanhq.co/docs/v2/
"""

from __future__ import annotations

import logging
import os
import threading
import time as _time
from dataclasses import dataclass, field
from typing import Any, Iterable

import requests

__all__ = [
    "DhanCredentials",
    "DhanHTTPClient",
    "DhanRESTError",
    "DhanRateLimitError",
    "get_dhan_client",
    "reset_dhan_client",
]

_log = logging.getLogger(__name__)

_BASE_URL = "https://api.dhan.co/v2"

# Per-endpoint minimum interval (seconds) between requests.
# Values match Dhan's documented v2 limits as of 2026-05.
_OPTION_CHAIN_INTERVAL = 1.0
_EXPIRY_LIST_INTERVAL = 3.0
_MARKETFEED_LTP_INTERVAL = 1.0
_DEFAULT_INTERVAL = 0.2  # safe default for unmetered endpoints

_DEFAULT_TIMEOUT = 15.0
_MAX_RETRIES = 5
_BACKOFF_BASE = 1.5  # seconds; doubled each attempt


class DhanRESTError(RuntimeError):
    """Raised when a Dhan REST call returns a non-recoverable error."""

    def __init__(self, message: str, *, status_code: int | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class DhanRateLimitError(DhanRESTError):
    """Raised when retries are exhausted under sustained 429s."""


@dataclass
class DhanCredentials:
    """Single source of truth for Dhan API credentials in this process."""

    access_token: str
    client_id: str

    @classmethod
    def from_env(cls) -> "DhanCredentials":
        token = os.environ.get("DHAN_ACCESS_TOKEN") or os.environ.get("DHAN_TOKEN", "")
        client_id = os.environ.get("DHAN_CLIENT_ID", "")
        if not token:
            raise RuntimeError("DHAN_ACCESS_TOKEN not set in env")
        if not client_id:
            raise RuntimeError("DHAN_CLIENT_ID not set in env")
        return cls(access_token=token, client_id=client_id)


class _EndpointLimiter:
    """Thread-safe minimum-interval limiter for a single endpoint.

    A ``threading.Lock`` plus a ``last_call_at`` monotonic timestamp gives
    sequential ordering: callers compute how long to sleep before they
    return from ``acquire()``. This avoids a token bucket because Dhan's
    limits are "one request every N seconds" (not "N requests per minute"),
    so sequential pacing is exactly the right semantics.
    """

    __slots__ = ("_interval", "_lock", "_last_call")

    def __init__(self, interval: float) -> None:
        self._interval = float(interval)
        self._lock = threading.Lock()
        self._last_call = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = _time.monotonic()
            wait = (self._last_call + self._interval) - now
            if wait > 0:
                # Release the lock while sleeping so we don't pessimise other
                # endpoints that share the same calling thread pool. We hold
                # the lock only for the bookkeeping; the sleep happens after.
                self._last_call = now + wait
                deadline = self._last_call
            else:
                self._last_call = now
                deadline = 0.0
        if deadline:
            sleep = deadline - _time.monotonic()
            if sleep > 0:
                _time.sleep(sleep)

    def interval(self) -> float:
        return self._interval


@dataclass
class _Endpoints:
    """Map of endpoint name -> (path, limiter). Stable for the client's lifetime."""

    option_chain: _EndpointLimiter = field(default_factory=lambda: _EndpointLimiter(_OPTION_CHAIN_INTERVAL))
    expiry_list: _EndpointLimiter = field(default_factory=lambda: _EndpointLimiter(_EXPIRY_LIST_INTERVAL))
    marketfeed_ltp: _EndpointLimiter = field(default_factory=lambda: _EndpointLimiter(_MARKETFEED_LTP_INTERVAL))
    default: _EndpointLimiter = field(default_factory=lambda: _EndpointLimiter(_DEFAULT_INTERVAL))


class DhanHTTPClient:
    """
    Shared REST client for Dhan v2 API.

    All call sites in this codebase MUST go through this class. Direct
    ``requests.post`` to ``api.dhan.co`` is forbidden in non-test code and
    will be flagged in review.

    Example::

        creds = DhanCredentials.from_env()
        client = DhanHTTPClient(creds)
        data = client.fetch_expiry_list(13, "IDX_I")
        chain = client.fetch_option_chain(13, "IDX_I", "2026-06-05")
    """

    def __init__(
        self,
        credentials: DhanCredentials,
        *,
        base_url: str = _BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = _MAX_RETRIES,
        pool_connections: int = 4,
        pool_maxsize: int = 8,
    ) -> None:
        self._creds = credentials
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._endpoints = _Endpoints()

        self._session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=pool_connections,
            pool_maxsize=pool_maxsize,
        )
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)
        self._refresh_session_headers()

    # ─── credential management ─────────────────────────────────────────────

    @property
    def credentials(self) -> DhanCredentials:
        return self._creds

    def rotate_token(self, new_access_token: str) -> None:
        """Swap the access token in-place. Safe to call mid-session.

        Note: this updates the REST session only. Websocket connections
        opened with the previous token continue to use it until they are
        reconnected — callers should close+reopen those sockets.
        """
        self._creds = DhanCredentials(
            access_token=new_access_token,
            client_id=self._creds.client_id,
        )
        self._refresh_session_headers()

    def _refresh_session_headers(self) -> None:
        self._session.headers.update(
            {
                "access-token": self._creds.access_token,
                "client-id": self._creds.client_id,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    # ─── endpoint methods ─────────────────────────────────────────────────

    def fetch_option_chain(
        self,
        scrip_id: int,
        segment: str,
        expiry: str,
    ) -> dict:
        """Return the parsed JSON ``data`` block from ``/optionchain``.

        Rate limit: 1 req/sec, shared across all symbols in this process.
        """
        body = {
            "UnderlyingScrip": int(scrip_id),
            "UnderlyingSeg": segment,
            "Expiry": expiry,
        }
        response = self._post("/optionchain", body, limiter=self._endpoints.option_chain)
        return response.get("data", {})

    def fetch_expiry_list(
        self,
        scrip_id: int,
        segment: str,
    ) -> list[str]:
        """Return the list of expiry date strings from ``/optionchain/expirylist``.

        Rate limit: 1 req / 3 sec.
        """
        body = {"UnderlyingScrip": int(scrip_id), "UnderlyingSeg": segment}
        response = self._post("/optionchain/expirylist", body, limiter=self._endpoints.expiry_list)
        raw = response.get("data", [])
        # v2 returns flat list; older shapes may use {"Expirylist": [...]}.
        if isinstance(raw, dict):
            raw = raw.get("Expirylist", [])
        return [str(x) for x in raw or []]

    def fetch_ltp_batch(
        self,
        segment_to_ids: dict[str, Iterable[int | str]],
    ) -> dict:
        """Return the parsed JSON body from ``/marketfeed/ltp``.

        Rate limit: 1 req/sec.
        """
        body = {seg: [int(x) for x in ids] for seg, ids in segment_to_ids.items()}
        return self._post("/marketfeed/ltp", body, limiter=self._endpoints.marketfeed_ltp)

    # ─── internals ────────────────────────────────────────────────────────

    def _post(self, path: str, body: dict, *, limiter: _EndpointLimiter) -> dict:
        url = f"{self._base_url}{path}"
        last_error: Exception | None = None
        last_was_429 = False
        for attempt in range(self._max_retries):
            limiter.acquire()
            try:
                resp = self._session.post(url, json=body, timeout=self._timeout)
            except requests.RequestException as exc:
                last_error = exc
                last_was_429 = False
                self._sleep_for_retry(attempt, exc=exc)
                continue

            if resp.status_code == 200:
                return self._parse_json(resp)

            if resp.status_code == 429:
                retry_after = self._parse_retry_after(resp)
                _log.warning(
                    "Dhan 429 on %s (attempt %d/%d); sleeping %.2fs",
                    path,
                    attempt + 1,
                    self._max_retries,
                    retry_after,
                )
                last_was_429 = True
                _time.sleep(retry_after)
                continue

            if 500 <= resp.status_code < 600:
                last_error = DhanRESTError(
                    f"Dhan 5xx on {path}: {resp.status_code}",
                    status_code=resp.status_code,
                    body=self._safe_body(resp),
                )
                last_was_429 = False
                self._sleep_for_retry(attempt)
                continue

            # 4xx other than 429 — not retryable
            raise DhanRESTError(
                f"Dhan {resp.status_code} on {path}: {self._safe_body(resp)}",
                status_code=resp.status_code,
                body=self._safe_body(resp),
            )

        # exhausted
        if last_was_429:
            raise DhanRateLimitError(
                f"Dhan rate limit not cleared after {self._max_retries} retries on {path}",
                status_code=429,
            )
        if last_error is not None:
            raise DhanRESTError(f"Dhan call failed after retries on {path}: {last_error}") from last_error
        raise DhanRESTError(f"Dhan call exhausted retries on {path}")

    @staticmethod
    def _parse_json(resp: requests.Response) -> dict:
        try:
            data = resp.json()
        except ValueError as exc:
            raise DhanRESTError(f"Dhan returned non-JSON body: {resp.text[:200]!r}") from exc
        if not isinstance(data, dict):
            raise DhanRESTError(f"Dhan returned unexpected JSON shape: {type(data).__name__}")
        return data

    @staticmethod
    def _safe_body(resp: requests.Response) -> Any:
        try:
            return resp.json()
        except ValueError:
            return resp.text[:200]

    @staticmethod
    def _parse_retry_after(resp: requests.Response) -> float:
        raw = resp.headers.get("Retry-After")
        if raw is None:
            return _BACKOFF_BASE
        try:
            return max(0.1, float(raw))
        except ValueError:
            return _BACKOFF_BASE

    def _sleep_for_retry(self, attempt: int, *, exc: Exception | None = None) -> None:
        backoff = _BACKOFF_BASE * (2 ** attempt)
        if exc is not None:
            _log.warning("Dhan REST transient error (attempt %d): %s; sleeping %.2fs", attempt + 1, exc, backoff)
        _time.sleep(backoff)

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "DhanHTTPClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


# ─── process-global singleton ─────────────────────────────────────────────

_GLOBAL_LOCK = threading.Lock()
_GLOBAL_CLIENT: DhanHTTPClient | None = None


def get_dhan_client(credentials: DhanCredentials | None = None) -> DhanHTTPClient:
    """Return the process-global ``DhanHTTPClient``, constructing it if needed.

    If ``credentials`` is provided on the first call it is used to build the
    client. Subsequent calls ignore the argument and return the existing
    instance — credentials can be rotated via ``client.rotate_token(...)``.
    """
    global _GLOBAL_CLIENT
    with _GLOBAL_LOCK:
        if _GLOBAL_CLIENT is None:
            creds = credentials or DhanCredentials.from_env()
            _GLOBAL_CLIENT = DhanHTTPClient(creds)
        return _GLOBAL_CLIENT


def reset_dhan_client(client: DhanHTTPClient | None = None) -> None:
    """Replace or clear the process-global client. Test-only helper.

    Production code should never call this.
    """
    global _GLOBAL_CLIENT
    with _GLOBAL_LOCK:
        if _GLOBAL_CLIENT is not None and _GLOBAL_CLIENT is not client:
            try:
                _GLOBAL_CLIENT.close()
            except Exception:  # noqa: BLE001 — best effort on teardown
                pass
        _GLOBAL_CLIENT = client
