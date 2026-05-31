"""
Single source of truth for Dhan instrument metadata used by the live stack.

Before this module these dicts were duplicated in three call sites of
``paper_engine.py`` (lines 694-700, 712, 728) plus a separate symbol list
in ``collect_order_book.py``. Inconsistency between copies silently
disabled symbols.

Membership of ``NSE_INDEX_DEPTH_SYMBOLS`` reflects which symbols are
served on Dhan's NSE_FNO 20-depth websocket. SENSEX option depth is on
``BSE_FNO`` and is not currently subscribed by the depth collector — the
paper engine relies on top-of-book values from the option chain REST
payload for SENSEX fills.

Update this file whenever Dhan adds or removes an index. Do not duplicate
the dicts elsewhere; import from here.
"""

from __future__ import annotations

# ─── Exchange segments (Dhan v2) ──────────────────────────────────────────
SEG_IDX = "IDX_I"
SEG_NSE_FNO = "NSE_FNO"
SEG_BSE_FNO = "BSE_FNO"

# ─── Spot / Index security ids ────────────────────────────────────────────
SPOT_SECURITY_IDS: dict[str, str] = {
    "NIFTY": "13",
    "BANKNIFTY": "25",
    "FINNIFTY": "27",
    "MIDCPNIFTY": "442",
    "SENSEX": "51",
}

# India VIX, served on IDX_I.
VIX_SECURITY_ID: str = "21"

# Underlying scrip ids for option-chain REST calls (UnderlyingScrip param).
# Currently identical to the spot security_id by symbol but kept separate
# so future divergence (e.g., BSE-listed scripid) doesn't fan out.
UNDERLYING_SCRIP_IDS: dict[str, int] = {
    "NIFTY": 13,
    "BANKNIFTY": 25,
    "FINNIFTY": 27,
    "MIDCPNIFTY": 442,
    "SENSEX": 51,
}

# Underlying segment per symbol for option-chain REST (UnderlyingSeg param).
UNDERLYING_SEGMENTS: dict[str, str] = {
    "NIFTY": SEG_IDX,
    "BANKNIFTY": SEG_IDX,
    "FINNIFTY": SEG_IDX,
    "MIDCPNIFTY": SEG_IDX,
    "SENSEX": SEG_IDX,
}

# Symbols whose option depth is served on the NSE_FNO 20-depth feed.
# SENSEX is excluded because its option depth is on BSE_FNO and the
# current collector only handles NSE_FNO.
NSE_INDEX_DEPTH_SYMBOLS: tuple[str, ...] = (
    "NIFTY",
    "BANKNIFTY",
    "FINNIFTY",
    "MIDCPNIFTY",
)


def all_idx_security_ids(*, include_vix: bool = True) -> list[str]:
    """Return spot index ids plus (optionally) VIX in a stable order.

    Stable order matters because subscription payloads should be
    deterministic across reconnects for log diffing.
    """
    ids = [SPOT_SECURITY_IDS[k] for k in (
        "NIFTY",
        "BANKNIFTY",
        "FINNIFTY",
        "MIDCPNIFTY",
        "SENSEX",
    )]
    if include_vix:
        ids.append(VIX_SECURITY_ID)
    return ids


def all_idx_security_ids_int(*, include_vix: bool = True) -> list[int]:
    """Same as ``all_idx_security_ids`` but as ``int`` (for REST payloads)."""
    return [int(x) for x in all_idx_security_ids(include_vix=include_vix)]
