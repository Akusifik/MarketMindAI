"""Exchange-independent, validated market microstructure models."""

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Optional, Sequence


def normalize_timestamp(value):
    """Return a timezone-aware UTC timestamp or raise ValueError."""
    if isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if not isfinite(value):
            raise ValueError("Timestamp must be finite.")
        timestamp = datetime.fromtimestamp(value, tz=timezone.utc)
    elif isinstance(value, str):
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("Timestamp must be valid.") from error
    else:
        raise ValueError("Timestamp must be present and valid.")
    return timestamp.replace(tzinfo=timezone.utc) if timestamp.tzinfo is None else timestamp.astimezone(timezone.utc)


def _finite_number(value, name, positive=False, non_negative=False):
    try:
        numeric = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be numeric.") from error
    if not isfinite(numeric) or (positive and numeric <= 0) or (non_negative and numeric < 0):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{name} must be finite and {qualifier}.")
    return numeric


@dataclass(frozen=True)
class OrderBookLevel:
    """A price level; zero quantity is reserved for incremental deletions."""

    price: float
    quantity: float

    def __post_init__(self):
        object.__setattr__(self, "price", _finite_number(self.price, "price", positive=True))
        object.__setattr__(self, "quantity", _finite_number(self.quantity, "quantity", non_negative=True))


@dataclass(frozen=True)
class OrderBookSnapshot:
    symbol: str
    timestamp: datetime
    bids: Sequence[OrderBookLevel]
    asks: Sequence[OrderBookLevel]
    sequence: Optional[int] = None

    def __post_init__(self):
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must be a non-empty string.")
        object.__setattr__(self, "timestamp", normalize_timestamp(self.timestamp))
        bids, asks = tuple(self.bids), tuple(self.asks)
        if any(not isinstance(level, OrderBookLevel) or level.quantity <= 0 for level in bids + asks):
            raise ValueError("Snapshots require positive, normalized order-book levels.")
        if any(bids[index].price <= bids[index + 1].price for index in range(len(bids) - 1)):
            raise ValueError("Bids must be sorted descending by price.")
        if any(asks[index].price >= asks[index + 1].price for index in range(len(asks) - 1)):
            raise ValueError("Asks must be sorted ascending by price.")
        if bids and asks and bids[0].price >= asks[0].price:
            raise ValueError("Crossed order books are not supported.")
        if self.sequence is not None and (not isinstance(self.sequence, int) or self.sequence < 0):
            raise ValueError("sequence must be a non-negative integer when supplied.")
        object.__setattr__(self, "bids", bids)
        object.__setattr__(self, "asks", asks)


@dataclass(frozen=True)
class OrderBookUpdate:
    """Provider-neutral incremental order-book change."""

    symbol: str
    timestamp: datetime
    bids: Sequence[OrderBookLevel]
    asks: Sequence[OrderBookLevel]
    sequence: int
    previous_sequence: Optional[int] = None

    def __post_init__(self):
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must be a non-empty string.")
        object.__setattr__(self, "timestamp", normalize_timestamp(self.timestamp))
        bids, asks = tuple(self.bids), tuple(self.asks)
        if any(not isinstance(level, OrderBookLevel) for level in bids + asks):
            raise ValueError("Updates require normalized order-book levels.")
        if not isinstance(self.sequence, int) or self.sequence < 0:
            raise ValueError("sequence must be a non-negative integer.")
        if self.previous_sequence is not None and (
            not isinstance(self.previous_sequence, int) or self.previous_sequence < 0
        ):
            raise ValueError("previous_sequence must be a non-negative integer.")
        object.__setattr__(self, "bids", bids)
        object.__setattr__(self, "asks", asks)


@dataclass(frozen=True)
class Trade:
    """side is aggressor side: BUY lifts the ask, SELL hits the bid."""

    symbol: str
    timestamp: datetime
    price: float
    quantity: float
    side: str = "UNKNOWN"
    trade_id: Optional[str] = None

    def __post_init__(self):
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must be a non-empty string.")
        object.__setattr__(self, "timestamp", normalize_timestamp(self.timestamp))
        object.__setattr__(self, "price", _finite_number(self.price, "price", positive=True))
        object.__setattr__(self, "quantity", _finite_number(self.quantity, "quantity", positive=True))
        side = str(self.side).upper() if self.side is not None else "UNKNOWN"
        object.__setattr__(self, "side", side if side in {"BUY", "SELL"} else "UNKNOWN")
