"""Descriptive normalized trade-flow and cumulative-delta calculations."""

from dataclasses import dataclass
from typing import Optional

from orderflow.models import Trade


def _ordered(trades):
    if any(not isinstance(trade, Trade) for trade in trades):
        raise ValueError("trades must contain Trade instances.")
    return sorted(trades, key=lambda trade: trade.timestamp)


def calculate_trade_flow(trades):
    ordered = _ordered(list(trades))
    buy = sum(trade.quantity for trade in ordered if trade.side == "BUY")
    sell = sum(trade.quantity for trade in ordered if trade.side == "SELL")
    unknown = sum(trade.quantity for trade in ordered if trade.side == "UNKNOWN")
    known = buy + sell
    return {
        "buy_volume": buy, "sell_volume": sell, "unknown_volume": unknown,
        "total_volume": buy + sell + unknown, "trade_count": len(ordered),
        "buy_sell_imbalance": (buy - sell) / known if known else 0.0,
        "delta": buy - sell,
    }


@dataclass(frozen=True)
class CumulativeDeltaPoint:
    timestamp: object
    trade_id: Optional[str]
    delta: float
    cumulative_delta: float


def cumulative_delta(trades, initial=0.0):
    """Return chronologically ordered cumulative aggressor delta points."""
    running = float(initial)
    points = []
    for trade in _ordered(list(trades)):
        delta = trade.quantity if trade.side == "BUY" else -trade.quantity if trade.side == "SELL" else 0.0
        running += delta
        points.append(CumulativeDeltaPoint(trade.timestamp, trade.trade_id, delta, running))
    return points
