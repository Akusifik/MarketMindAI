"""Contracts and health metadata for live market-data providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator, Dict, Optional, Union

from orderflow import OrderBookSnapshot, Trade

LiveEvent = Union[OrderBookSnapshot, Trade]


@dataclass
class SymbolBookHealth:
    synchronized: bool = False
    generation: int = 0
    last_update_id: Optional[int] = None
    last_sequence: Optional[int] = None
    sequence_gap_count: int = 0
    last_snapshot_time: Optional[datetime] = None


@dataclass
class ConnectionHealth:
    connected: bool = False
    last_message_time: Optional[datetime] = None
    last_ping_time: Optional[datetime] = None
    last_pong_time: Optional[datetime] = None
    reconnect_count: int = 0
    symbols: Dict[str, SymbolBookHealth] = field(default_factory=dict)


class LiveMarketDataProvider(ABC):
    health: ConnectionHealth

    @abstractmethod
    async def connect(self): ...
    @abstractmethod
    async def disconnect(self): ...
    @abstractmethod
    async def subscribe_order_book(self, symbol: str): ...
    @abstractmethod
    async def subscribe_trades(self, symbol: str): ...
    @abstractmethod
    def events(self) -> AsyncIterator[LiveEvent]: ...
