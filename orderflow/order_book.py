"""Deterministic snapshot metrics and mutable incremental book foundation."""

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

from orderflow.models import OrderBookLevel, OrderBookSnapshot, normalize_timestamp


def calculate_order_book_metrics(snapshot, top_n=5):
    if not isinstance(snapshot, OrderBookSnapshot):
        raise ValueError("snapshot must be an OrderBookSnapshot.")
    if not isinstance(top_n, int) or top_n < 1:
        raise ValueError("top_n must be a positive integer.")
    best_bid = snapshot.bids[0].price if snapshot.bids else None
    best_ask = snapshot.asks[0].price if snapshot.asks else None
    bid_depth = sum(level.quantity for level in snapshot.bids)
    ask_depth = sum(level.quantity for level in snapshot.asks)
    top_bid = sum(level.quantity for level in snapshot.bids[:top_n])
    top_ask = sum(level.quantity for level in snapshot.asks[:top_n])
    total_depth = bid_depth + ask_depth
    return {
        "best_bid": best_bid, "best_ask": best_ask,
        "spread": best_ask - best_bid if best_bid is not None and best_ask is not None else None,
        "mid_price": (best_ask + best_bid) / 2 if best_bid is not None and best_ask is not None else None,
        "total_bid_depth": bid_depth, "total_ask_depth": ask_depth,
        "top_n_bid_depth": top_bid, "top_n_ask_depth": top_ask,
        "imbalance": (bid_depth - ask_depth) / total_depth if total_depth else 0.0,
    }


@dataclass
class OrderBookState:
    """In-memory state for adapters that already provide synchronized updates."""

    symbol: str
    bids: dict
    asks: dict
    timestamp: datetime
    sequence: Optional[int] = None

    @classmethod
    def from_snapshot(cls, snapshot):
        if not isinstance(snapshot, OrderBookSnapshot):
            raise ValueError("snapshot must be an OrderBookSnapshot.")
        return cls(snapshot.symbol, {level.price: level.quantity for level in snapshot.bids}, {level.price: level.quantity for level in snapshot.asks}, snapshot.timestamp, snapshot.sequence)

    def snapshot(self):
        return OrderBookSnapshot(
            self.symbol, self.timestamp,
            tuple(OrderBookLevel(price, quantity) for price, quantity in sorted(self.bids.items(), reverse=True)),
            tuple(OrderBookLevel(price, quantity) for price, quantity in sorted(self.asks.items())),
            self.sequence,
        )

    def apply_updates(self, bids: Iterable[OrderBookLevel] = (), asks: Iterable[OrderBookLevel] = (), *, timestamp, sequence=None):
        """Atomically apply updates; zero quantity deletes a level.

        Sequence values must strictly increase when both the prior and update
        values are supplied. Exchange-specific gap recovery remains adapter work.
        """
        timestamp = normalize_timestamp(timestamp)
        if self.sequence is not None and (not isinstance(sequence, int) or sequence <= self.sequence):
            raise ValueError("Update sequence must strictly increase.")
        if sequence is not None and (not isinstance(sequence, int) or sequence < 0):
            raise ValueError("sequence must be a non-negative integer.")
        new_bids, new_asks = self.bids.copy(), self.asks.copy()
        for updates, book in ((bids, new_bids), (asks, new_asks)):
            for level in updates:
                if not isinstance(level, OrderBookLevel):
                    raise ValueError("Updates must contain OrderBookLevel instances.")
                if level.quantity == 0:
                    book.pop(level.price, None)
                else:
                    book[level.price] = level.quantity
        candidate = OrderBookSnapshot(
            self.symbol, timestamp,
            tuple(OrderBookLevel(price, quantity) for price, quantity in sorted(new_bids.items(), reverse=True)),
            tuple(OrderBookLevel(price, quantity) for price, quantity in sorted(new_asks.items())),
            sequence if sequence is not None else self.sequence,
        )
        self.bids = {level.price: level.quantity for level in candidate.bids}
        self.asks = {level.price: level.quantity for level in candidate.asks}
        self.timestamp, self.sequence = candidate.timestamp, candidate.sequence
        return candidate
