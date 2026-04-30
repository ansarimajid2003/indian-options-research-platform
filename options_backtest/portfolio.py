from __future__ import annotations

from .schemas import Fill, Side, Trade


def signed_cashflow(fill: Fill) -> float:
    value = fill.gross_value
    return value if fill.side == Side.SELL else -value


def finalize_trade(trade: Trade) -> Trade:
    entry_cash = sum(signed_cashflow(fill) for fill in trade.entry_fills)
    exit_cash = sum(signed_cashflow(fill) for fill in trade.exit_fills)
    charges = sum(fill.charges for fill in trade.entry_fills + trade.exit_fills)
    trade.gross_pnl = round(entry_cash + exit_cash, 2)
    trade.charges = round(charges, 2)
    trade.net_pnl = round(trade.gross_pnl - trade.charges, 2)
    return trade
