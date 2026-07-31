"""Bybit V5 public WebSocket adapter with per-symbol synchronization."""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from itertools import count

from exchanges.live.base import ConnectionHealth, LiveMarketDataProvider, SymbolBookHealth
from orderflow import OrderBookLevel, OrderBookSnapshot, OrderBookState, Trade

logger = logging.getLogger(__name__)


def _timestamp(milliseconds):
    return datetime.fromtimestamp(float(milliseconds) / 1000, tz=timezone.utc)


@dataclass(frozen=True)
class BybitBookMessage:
    kind: str
    symbol: str
    timestamp: datetime
    bids: tuple
    asks: tuple
    update_id: int
    cross_sequence: int


class SubscriptionState(Enum):
    UNSUBSCRIBED = "unsubscribed"
    SUBSCRIBING = "subscribing"
    SUBSCRIBED = "subscribed"
    RESYNCING = "resyncing"


class BybitParser:
    @staticmethod
    def parse(payload):
        if not isinstance(payload, dict):
            raise ValueError("Payload must be an object.")
        topic, data = payload.get("topic", ""), payload.get("data")
        if not topic:
            if payload.get("op") or payload.get("success") is not None:
                return []
            raise ValueError("Malformed Bybit message.")
        if topic.startswith("orderbook."):
            if not isinstance(data, dict) or payload.get("type") not in {"snapshot", "delta"}:
                raise ValueError("Malformed order-book message.")
            return [BybitBookMessage(
                payload["type"], data["s"], _timestamp(payload["ts"]),
                tuple(OrderBookLevel(*level) for level in data.get("b", ())),
                tuple(OrderBookLevel(*level) for level in data.get("a", ())),
                int(data["u"]), int(data["seq"]),
            )]
        if topic.startswith("publicTrade."):
            if not isinstance(data, list):
                raise ValueError("Malformed trade message.")
            return [Trade(item["s"], _timestamp(item["T"]), item["p"], item["v"], item["S"], str(item["i"]) if item.get("i") is not None else None) for item in data]
        raise ValueError("Unsupported Bybit topic.")


class BybitBookSynchronizer:
    """Apply Bybit V5 rules without assuming update IDs are consecutive.

    A snapshot always replaces state. Update ID ``u`` and cross-sequence ``seq``
    must both advance for deltas. Equal pairs are duplicates; regressions or
    contradictory movement are unsafe. Non-consecutive monotonic IDs are valid:
    Bybit exposes no previous-update pointer, so they cannot prove that every
    delta arrived. When transport continuity is uncertain, reconnect/resync is
    the only safe recovery. ``u == 1`` is documented as a restart snapshot; a
    delta carrying it is rejected pending a real snapshot.
    """
    def __init__(self, symbol, health):
        self.symbol, self.health, self.state = symbol, health, None

    def invalidate(self, *, gap=False):
        self.state = None
        self.health.synchronized = False
        self.health.generation += 1
        self.health.last_update_id = self.health.last_sequence = None
        if gap:
            self.health.sequence_gap_count += 1

    def apply(self, message, generation):
        if generation != self.health.generation or message.symbol != self.symbol:
            return None, "old_generation"
        if message.kind == "snapshot":
            snapshot = OrderBookSnapshot(message.symbol, message.timestamp, message.bids, message.asks, message.update_id)
            self.state = OrderBookState.from_snapshot(snapshot)
            self.health.synchronized = True
            self.health.last_update_id = message.update_id
            self.health.last_sequence = message.cross_sequence
            self.health.last_snapshot_time = message.timestamp
            return snapshot, "snapshot"
        if not self.health.synchronized:
            return None, "unsynchronized"
        if message.update_id == 1:
            self.invalidate(gap=True)
            return None, "restart"
        last_u, last_seq = self.health.last_update_id, self.health.last_sequence
        if message.update_id == last_u and message.cross_sequence == last_seq:
            return None, "duplicate"
        if message.update_id <= last_u and message.cross_sequence <= last_seq:
            return None, "stale"
        if message.update_id <= last_u or message.cross_sequence <= last_seq:
            self.invalidate(gap=True)
            return None, "gap"
        snapshot = self.state.apply_updates(message.bids, message.asks, timestamp=message.timestamp, sequence=message.update_id)
        self.health.last_update_id, self.health.last_sequence = message.update_id, message.cross_sequence
        return snapshot, "update"


class BybitWebSocketProvider(LiveMarketDataProvider):
    def __init__(self, url, *, depth=50, message_timeout=45.0, heartbeat_interval=20.0,
                 ack_timeout=5.0, websocket_factory=None):
        self.url, self.depth = url, depth
        self.message_timeout, self.heartbeat_interval, self.ack_timeout = message_timeout, heartbeat_interval, ack_timeout
        self.websocket_factory = websocket_factory
        self.health = ConnectionHealth()
        self._socket = None
        self._queue = asyncio.Queue()
        self._books, self._book_symbols, self._trade_symbols = {}, set(), set()
        self._ready_books = set()
        self._pending, self._request_ids = {}, count(1)
        self._subscription_states = {}
        self._subscription_lock = asyncio.Lock()
        self._reader_task = self._heartbeat_task = None
        self._resync_tasks = set()
        self._session = 0
        self._fatal_error = None
        self._closed = asyncio.Event(); self._closed.set()
        self._disconnect_lock = asyncio.Lock()
        self._closing = True

    def _ensure_book(self, symbol):
        health = self.health.symbols.setdefault(symbol, SymbolBookHealth())
        return self._books.setdefault(symbol, BybitBookSynchronizer(symbol, health))

    async def connect(self):
        if self.health.connected:
            return
        if self.websocket_factory is None:
            from websockets.asyncio.client import connect
            self.websocket_factory = connect
        self._socket = await self.websocket_factory(self.url)
        self._session += 1
        self._closing = False
        self._fatal_error = None
        self._subscription_states.clear()
        self._ready_books.clear()
        self.health.connected = True
        self._closed.clear()
        self._reader_task = asyncio.create_task(self._reader_loop(self._session), name="bybit-reader")
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(self._session), name="bybit-heartbeat")
        self._reader_task.add_done_callback(lambda task, active_session=self._session: self._connection_task_done(task, active_session))
        self._heartbeat_task.add_done_callback(lambda task, active_session=self._session: self._connection_task_done(task, active_session))

    def _connection_task_done(self, task, session):
        if session == self._session and not task.cancelled():
            self._closed.set()

    async def disconnect(self):
        async with self._disconnect_lock:
            self._closing = True
            self.health.connected = False
            current = asyncio.current_task()
            tasks = [task for task in (self._reader_task, self._heartbeat_task, *self._resync_tasks) if task and task is not current]
            for task in tasks: task.cancel()
            if tasks: await asyncio.gather(*tasks, return_exceptions=True)
            self._reader_task = self._heartbeat_task = None
            self._resync_tasks.clear()
            for future, _, _, _ in self._pending.values():
                if not future.done(): future.cancel()
            self._pending.clear()
            self._ready_books.clear()
            self._subscription_states.clear()
            socket, self._socket = self._socket, None
            if socket is not None: await socket.close()
            for book in self._books.values(): book.invalidate()
            self._closed.set()

    async def _request(self, op, topics=None, *, session=None):
        session = self._session if session is None else session
        if self._closing or not self.health.connected or session != self._session:
            raise ConnectionError("Connection session is no longer active")
        req_id = str(next(self._request_ids))
        future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = (future, op, tuple(topics or ()), session)
        payload = {"req_id": req_id, "op": op}
        if topics is not None: payload["args"] = topics
        await self._socket.send(json.dumps(payload))
        try:
            response = await asyncio.wait_for(future, self.ack_timeout)
        finally:
            pending = self._pending.get(req_id)
            if pending and pending[3] == session:
                self._pending.pop(req_id, None)
        if session != self._session or self._closing:
            raise ConnectionError("Acknowledgement belongs to an old session")
        if response.get("success") is not True or response.get("op") != op:
            raise ConnectionError(f"Bybit {op} failed: {response.get('ret_msg', '')}")

    async def subscribe_order_book(self, symbol):
        self._book_symbols.add(symbol); self._ensure_book(symbol)
        if self.health.connected: await self._subscribe_topics([f"orderbook.{self.depth}.{symbol}"])

    async def subscribe_trades(self, symbol):
        self._trade_symbols.add(symbol)
        if self.health.connected: await self._subscribe_topics([f"publicTrade.{symbol}"])

    async def resubscribe(self):
        topics = [f"orderbook.{self.depth}.{s}" for s in sorted(self._book_symbols)] + [f"publicTrade.{s}" for s in sorted(self._trade_symbols)]
        if topics: await self._subscribe_topics(topics)

    async def _subscribe_topics(self, topics):
        async with self._subscription_lock:
            session = self._session
            needed = [topic for topic in topics if self._subscription_states.get(topic, SubscriptionState.UNSUBSCRIBED) is SubscriptionState.UNSUBSCRIBED]
            if not needed:
                return
            for topic in needed: self._subscription_states[topic] = SubscriptionState.SUBSCRIBING
            try:
                await self._request("subscribe", needed, session=session)
            except BaseException:
                if session == self._session:
                    for topic in needed: self._subscription_states[topic] = SubscriptionState.UNSUBSCRIBED
                raise
            if session != self._session or self._closing:
                raise ConnectionError("Subscription completed on an old session")
            for topic in needed:
                self._subscription_states[topic] = SubscriptionState.SUBSCRIBED
                if topic.startswith("orderbook."): self._ready_books.add(topic.rsplit(".", 1)[-1])

    async def _resync(self, symbol, session, already_invalid=False):
        book = self._ensure_book(symbol)
        if not already_invalid: book.invalidate()
        self._ready_books.discard(symbol)
        topic = f"orderbook.{self.depth}.{symbol}"
        async with self._subscription_lock:
            if self._closing or session != self._session: return
            self._subscription_states[topic] = SubscriptionState.RESYNCING
            await self._request("unsubscribe", [topic], session=session)
            if self._closing or session != self._session: return
            await self._request("subscribe", [topic], session=session)
            if self._closing or session != self._session: return
            self._subscription_states[topic] = SubscriptionState.SUBSCRIBED
            self._ready_books.add(symbol)

    async def _reader_loop(self, session):
        try:
            while self.health.connected and session == self._session:
                raw = await asyncio.wait_for(self._socket.recv(), self.message_timeout)
                now = datetime.now(timezone.utc); self.health.last_message_time = now
                try: payload = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    logger.warning("Malformed Bybit market-data message"); continue
                req_id = payload.get("req_id") if isinstance(payload, dict) else None
                pending = self._pending.get(req_id)
                if pending and pending[3] == session:
                    future, operation, topics, _ = pending
                    if payload.get("op") == "ping" and payload.get("success") is True:
                        self.health.last_pong_time = now
                    if not future.done(): future.set_result(payload)
                    continue
                if isinstance(payload, dict) and payload.get("op") == "ping" and payload.get("success") is True:
                    self.health.last_pong_time = now; continue
                try: events = BybitParser.parse(payload)
                except (ValueError, TypeError, KeyError, OverflowError):
                    logger.warning("Malformed Bybit market-data message"); continue
                for event in events:
                    if isinstance(event, BybitBookMessage):
                        if event.symbol not in self._ready_books:
                            continue
                        book = self._ensure_book(event.symbol)
                        generation = book.health.generation
                        snapshot, status = book.apply(event, generation)
                        if status in {"gap", "restart"}:
                            if not self._closing and session == self._session:
                                task = asyncio.create_task(self._resync(event.symbol, session, already_invalid=True), name=f"bybit-resync-{event.symbol}")
                                self._resync_tasks.add(task)
                                task.add_done_callback(lambda done, active_session=session: self._resync_done(done, active_session))
                        elif snapshot is not None:
                            await self._queue.put((session, event.symbol, generation, snapshot))
                    else:
                        await self._queue.put((session, None, None, event))
        finally:
            if session == self._session: self._closed.set()

    async def _heartbeat_loop(self, session):
        while self.health.connected and session == self._session:
            await asyncio.sleep(self.heartbeat_interval)
            self.health.last_ping_time = datetime.now(timezone.utc)
            await self._request("ping", session=session)

    def _resync_done(self, task, session):
        self._resync_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.warning("Bybit order-book resync failed: %s", task.exception())
            self._fail_session(task.exception(), session)

    def _fail_session(self, error, session):
        if session != self._session or self._closing:
            return
        self._fatal_error = error
        self.health.connected = False
        self._ready_books.clear()
        for book in self._books.values(): book.invalidate()
        self._closed.set()

    async def wait_closed(self):
        await self._closed.wait()
        if self._fatal_error is not None:
            raise ConnectionError("Bybit connection session failed") from self._fatal_error
        for task in (self._reader_task, self._heartbeat_task):
            if task and task.done() and not task.cancelled():
                error = task.exception()
                if error: raise error

    async def next_event(self):
        while True:
            session, symbol, generation, event = await self._queue.get()
            if session != self._session: continue
            if symbol is None: return event
            health = self.health.symbols.get(symbol)
            if health and health.synchronized and health.generation == generation: return event

    async def events(self):
        while True: yield await self.next_event()
