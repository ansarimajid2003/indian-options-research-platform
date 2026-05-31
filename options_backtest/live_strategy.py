"""
Live trading strategy interface.

Phase D3 of the May 2026 refactor extracts the Wing-6 Iron Condor
strategy parameters out of ``paper_engine.py`` (which had hard-coded
short/long offsets, lot sizes, VIX gates, and DTE filters scattered
across ~5 different methods) into a single dataclass plus a small
``LiveStrategy`` interface.

The engine *coordinates* (time loop, feed handler, position manager,
event log, EOD reporting); the strategy *decides* what to enter/exit
based on the profile. New strategies plug in by implementing
``LiveStrategy``.

For backwards compatibility the engine still owns the actual entry /
exit mechanics — this commit moves *parameters* and per-symbol
decisions into the strategy class. A later commit can move the entire
``_enter_all_symbols`` flow into ``strategy.enter()`` once the
interface has stabilised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .schemas import OptionType


@dataclass(frozen=True)
class Leg:
    """One leg of a multi-leg position blueprint.

    ``offset_steps`` is in strike-step units (each symbol's strike-step
    comes from ``options_backtest.calendar.get_instrument_spec``). A
    negative offset is below the ATM, positive is above. ``side`` is
    SELL or BUY (string for JSON-friendly logging).
    """

    role: str
    offset_steps: int
    option_type: OptionType
    side: str  # "SELL" or "BUY"


@dataclass(frozen=True)
class SymbolPolicy:
    """Per-symbol gating + sizing for one ``LiveStrategy`` instance.

    The defaults reflect the Wing-6 4x1 profile:
      * short legs at +/- 2 steps
      * long  legs at +/- 8 steps
      * min_dte 1 (always skip same-day expiry — different risk profile)
      * lots default 1
    """

    symbol: str
    lots: int = 1
    short_offset_steps: int = 2
    long_offset_steps: int = 8
    min_dte: int = 1
    max_dte: int | None = None
    vix_threshold: float | None = None
    vix_bucket: str | None = None
    require_positive_credit: bool = True

    def legs(self) -> tuple[Leg, ...]:
        """Return the four iron-condor legs for this symbol.

        Short call + long call wing above the ATM, short put + long put
        wing below. Order matters for downstream loggers that key on
        ``leg_role``.
        """
        return (
            Leg("short_call", +self.short_offset_steps, OptionType.CALL, "SELL"),
            Leg("long_call",  +self.long_offset_steps,  OptionType.CALL, "BUY"),
            Leg("short_put",  -self.short_offset_steps, OptionType.PUT,  "SELL"),
            Leg("long_put",   -self.long_offset_steps,  OptionType.PUT,  "BUY"),
        )


class LiveStrategy(Protocol):
    """Interface implemented by every live strategy.

    Methods return *decisions*, not side effects. The engine performs
    the entry, exit, and persistence — keeping the strategy pure makes
    unit testing trivial and dry-runs free of write hazards.
    """

    name: str

    def symbol_policy(self, symbol: str) -> SymbolPolicy | None:
        """Return the policy for ``symbol`` or ``None`` to skip it."""
        ...

    def should_trade_symbol(self, symbol: str, *, vix: float | None, dte: int) -> tuple[bool, str]:
        """Return ``(True, "")`` if the engine should attempt entry on
        ``symbol`` given the current VIX and DTE; otherwise
        ``(False, reason)`` for signal-log attribution.
        """
        ...


# ─── Wing-6 Iron Condor implementation ────────────────────────────────────


@dataclass
class Wing6IronCondorStrategy:
    """Wing-6 4x1 (NIFTY:FINNIFTY:MIDCPNIFTY:SENSEX) configurable IC.

    Parameters come from the live profile JSON's ``symbols`` block.
    """

    name: str = "wing6_iron_condor"
    policies: dict[str, SymbolPolicy] = field(default_factory=dict)

    @classmethod
    def from_profile(cls, profile: dict) -> "Wing6IronCondorStrategy":
        """Build a strategy from a live profile dict.

        Recognised per-symbol fields:

            trade                   bool   include in trading
            lots                    int    contracts per leg (default 1)
            short_offset_steps      int    (default 2)
            long_offset_steps       int    (default 8)
            min_dte                 int    (default 1)
            max_dte                 int|null
            vix_threshold           float|null
            vix_bucket              str|null
            require_positive_credit bool   (default True)
        """
        policies: dict[str, SymbolPolicy] = {}
        for symbol, sym_cfg in (profile.get("symbols") or {}).items():
            if not sym_cfg.get("trade", False):
                continue
            policy = SymbolPolicy(
                symbol=symbol,
                lots=int(sym_cfg.get("lots", 1)),
                short_offset_steps=int(sym_cfg.get("short_offset_steps", 2)),
                long_offset_steps=int(sym_cfg.get("long_offset_steps", 8)),
                min_dte=int(sym_cfg.get("min_dte", 1)),
                max_dte=(int(sym_cfg["max_dte"]) if sym_cfg.get("max_dte") is not None else None),
                vix_threshold=(float(sym_cfg["vix_threshold"]) if sym_cfg.get("vix_threshold") is not None else None),
                vix_bucket=sym_cfg.get("vix_bucket"),
                require_positive_credit=bool(sym_cfg.get("require_positive_credit", True)),
            )
            policies[symbol] = policy
        return cls(policies=policies)

    # ─── LiveStrategy protocol ────────────────────────────────────────────

    def symbol_policy(self, symbol: str) -> SymbolPolicy | None:
        return self.policies.get(symbol)

    def should_trade_symbol(self, symbol: str, *, vix: float | None, dte: int) -> tuple[bool, str]:
        policy = self.policies.get(symbol)
        if policy is None:
            return False, "not_in_profile"
        if dte < policy.min_dte:
            return False, "dte_below_min"
        if policy.max_dte is not None and dte > policy.max_dte:
            return False, "dte_above_max"
        if policy.vix_threshold is not None:
            if vix is None:
                return False, "vix_stale"
            if vix < policy.vix_threshold:
                return False, "vix_below_threshold"
        return True, ""

    # Convenience for the engine — returns the canonical leg list for a symbol.
    def legs_for(self, symbol: str) -> tuple[Leg, ...]:
        policy = self.policies.get(symbol)
        if policy is None:
            return ()
        return policy.legs()
