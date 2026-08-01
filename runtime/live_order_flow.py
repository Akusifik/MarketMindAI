"""Reusable live order-flow collection and analysis runtime."""

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping, Optional

from analysis.order_flow_analysis import analyze_order_flow
from config import (
    BYBIT_WS_URL,
    LIVE_MESSAGE_TIMEOUT,
    LIVE_ORDER_BOOK_DEPTH,
    LIVE_RECONNECT_INITIAL_DELAY,
    LIVE_RECONNECT_MAX_DELAY,
)
from exchanges.live import BybitWebSocketProvider, LiveMarketDataService
from orderflow import OrderBookSnapshot, Trade

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LatestOrderFlowAnalysis:
    symbol: str
    calculated_at: datetime
    generation: int
    analysis: Mapping[str, Any]
    snapshot_count: int
    trade_count: int
    window_seconds: float


@dataclass(frozen=True)
class SymbolOrderFlowHealth:
    synchronized: bool
    generation: int
    gaps: int
    latest_analysis_time: Optional[datetime]
    snapshot_history_size: int
    recent_trade_count: int
    ready: bool
    analysis_in_flight: bool
    last_analysis_error: Optional[str]


@dataclass(frozen=True)
class LiveOrderFlowHealth:
    running: bool
    connected: bool
    reconnect_count: int
    symbols: Mapping[str, SymbolOrderFlowHealth]
    failure: Optional[str]


class _SymbolState:
    def __init__(self, snapshot_limit, trade_limit):
        self.snapshots = deque(maxlen=snapshot_limit)
        self.trades = deque(maxlen=trade_limit)
        self.generation = None
        self.latest = None
        self.ready = asyncio.Event()
        self.analysis_in_flight = False
        self.last_analysis_error = None
        self.trade_watermark = None

    def reset(self, generation=None):
        self.snapshots.clear()
        self.trades.clear()
        self.generation = generation
        self.latest = None
        self.ready.clear()
        self.analysis_in_flight = False
        self.last_analysis_error = None
        self.trade_watermark = None


class LiveOrderFlowService:
    """Collect and periodically analyze trusted data for independent symbols."""

    def __init__(
        self,
        symbols=("BTCUSDT",),
        *,
        provider=None,
        market_data_service=None,
        analysis_interval=2.0,
        trade_window=60.0,
        snapshot_history=200,
        max_trades=50_000,
        analyzer=analyze_order_flow,
        url=BYBIT_WS_URL,
    ):
        normalized = tuple(dict.fromkeys(str(symbol).upper() for symbol in symbols))
        if not normalized or any(not symbol for symbol in normalized):
            raise ValueError("At least one non-empty symbol is required")
        if analysis_interval <= 0 or trade_window <= 0:
            raise ValueError("analysis_interval and trade_window must be positive")
        if snapshot_history < 1 or max_trades < 1:
            raise ValueError("snapshot_history and max_trades must be positive")
        self.symbols = normalized
        self.analysis_interval = analysis_interval
        self.trade_window = trade_window
        self.analyzer = analyzer
        self.provider = provider or BybitWebSocketProvider(
            url, depth=LIVE_ORDER_BOOK_DEPTH, message_timeout=LIVE_MESSAGE_TIMEOUT
        )
        self.market_data_service = market_data_service or LiveMarketDataService(
            self.provider,
            initial_delay=LIVE_RECONNECT_INITIAL_DELAY,
            max_delay=LIVE_RECONNECT_MAX_DELAY,
        )
        self._states = {
            symbol: _SymbolState(snapshot_history, max_trades) for symbol in normalized
        }
        self._lifecycle_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._tasks = set()
        self._infrastructure_tasks = set()
        self._analysis_tasks = set()
        self._supervisor_task = None
        self._running = False
        self._failure = None
        self.analysis_count = 0

    @property
    def running(self):
        return self._running

    async def start(self):
        """Start subscriptions and background work; duplicate calls are harmless."""
        async with self._lifecycle_lock:
            if self._running:
                return
            self._running = True
            self._failure = None
            self._stop_event.clear()
            try:
                for symbol in self.symbols:
                    await self.provider.subscribe_order_book(symbol)
                    await self.provider.subscribe_trades(symbol)
                self._infrastructure_tasks = {
                    asyncio.create_task(self.market_data_service.start(), name="live-order-flow-market-data"),
                    asyncio.create_task(self._consume(), name="live-order-flow-consumer"),
                }
                self._analysis_tasks = {
                    asyncio.create_task(
                        self._symbol_analysis_loop(symbol),
                        name=f"live-order-flow-analysis-{symbol}",
                    )
                    for symbol in self.symbols
                }
                self._supervisor_task = asyncio.create_task(
                    self._supervise_infrastructure(), name="live-order-flow-supervisor"
                )
                self._tasks = self._infrastructure_tasks | self._analysis_tasks | {self._supervisor_task}
            except BaseException:
                self._running = False
                self._stop_event.set()
                await self.market_data_service.stop()
                raise

    async def stop(self):
        """Stop cleanly. Repeated and concurrent calls are safe."""
        async with self._lifecycle_lock:
            if not self._running and not self._tasks:
                return
            self._running = False
            self._stop_event.set()
            tasks, self._tasks = tuple(self._tasks), set()
            try:
                await self.market_data_service.stop()
            finally:
                # Analysis jobs are allowed to finish against their private input
                # copies. The post-worker trust check prevents shutdown publishing.
                cancellable = self._infrastructure_tasks | ({self._supervisor_task} if self._supervisor_task else set())
                for task in cancellable:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                self._infrastructure_tasks.clear()
                self._analysis_tasks.clear()
                self._supervisor_task = None

    async def wait_until_ready(self, symbol, timeout=None):
        state = self._state(symbol)
        if timeout is None:
            await state.ready.wait()
        else:
            await asyncio.wait_for(state.ready.wait(), timeout)
        return self.get_latest(symbol)

    def get_latest(self, symbol):
        self._sync_trust(str(symbol).upper())
        return self._state(symbol).latest

    def get_health(self, symbol=None):
        if symbol is not None:
            normalized = str(symbol).upper()
            self._sync_trust(normalized)
            return self._symbol_health(normalized)
        values = {}
        for name in self.symbols:
            self._sync_trust(name)
            values[name] = self._symbol_health(name)
        provider_health = self.provider.health
        return LiveOrderFlowHealth(
            running=self._running,
            connected=provider_health.connected,
            reconnect_count=provider_health.reconnect_count,
            symbols=MappingProxyType(values),
            failure=self._failure,
        )

    def record_event(self, event):
        """Record one provider event if its symbol is currently trusted."""
        symbol = getattr(event, "symbol", "").upper()
        if symbol not in self._states or not self._sync_trust(symbol):
            return False
        state = self._states[symbol]
        health = self.provider.health.symbols.get(symbol)
        if not health or not health.synchronized or health.generation != state.generation:
            self._sync_trust(symbol)
            return False
        if isinstance(event, OrderBookSnapshot):
            if state.snapshots and (
                event.timestamp <= state.snapshots[-1].timestamp
                or (event.sequence is not None and state.snapshots[-1].sequence is not None
                    and event.sequence <= state.snapshots[-1].sequence)
            ):
                return False
            state.snapshots.append(event)
            self._prune_trades(state, event.timestamp)
            return True
        if isinstance(event, Trade):
            self._record_trade(state, event)
            return True
        return False

    def analyze_symbol(self, symbol):
        """Run one synchronous cycle for compatibility and deterministic tests.

        Production scheduling uses ``_analyze_symbol_async`` so analysis never
        executes on the event loop.
        """
        symbol = str(symbol).upper()
        inputs = self._analysis_inputs(symbol, use_wall_clock=False)
        if inputs is None:
            return None
        generation, current, trades, history, snapshot_count = inputs
        payload = self.analyzer(current, list(trades), list(history))
        if not self._generation_is_current(symbol, generation, require_running=False):
            return None
        return self._publish(symbol, generation, payload, snapshot_count, len(trades))

    async def _analyze_symbol_async(self, symbol):
        state = self._states[symbol]
        if state.analysis_in_flight or not self._running:
            return None
        inputs = self._analysis_inputs(symbol, use_wall_clock=True)
        if inputs is None:
            return None
        generation, current, trades, history, snapshot_count = inputs
        state.analysis_in_flight = True
        try:
            payload = await asyncio.to_thread(self.analyzer, current, trades, history)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            state.last_analysis_error = f"{type(error).__name__}: {error}"
            logger.exception("Live order-flow analysis failed for %s", symbol)
            return None
        finally:
            state.analysis_in_flight = False
        if not self._generation_is_current(symbol, generation, require_running=True):
            return None
        state.last_analysis_error = None
        return self._publish(symbol, generation, payload, snapshot_count, len(trades))

    def _publish(self, symbol, generation, payload, snapshot_count, trade_count):
        state = self._states[symbol]
        latest = LatestOrderFlowAnalysis(
            symbol=symbol,
            calculated_at=datetime.now(timezone.utc),
            generation=generation,
            analysis=payload,
            snapshot_count=snapshot_count,
            trade_count=trade_count,
            window_seconds=self.trade_window,
        )
        state.latest = latest
        state.ready.set()
        self.analysis_count += 1
        return latest

    def _analysis_inputs(self, symbol, *, use_wall_clock):
        if not self._sync_trust(symbol):
            return None
        state = self._states[symbol]
        if use_wall_clock:
            self._prune_trades(state, datetime.now(timezone.utc))
        if not state.snapshots:
            return None
        current = state.snapshots[-1]
        if not use_wall_clock:
            self._prune_trades(state, current.timestamp)
        # Models are frozen; tuples detach the worker's containers from live deques.
        snapshots = tuple(state.snapshots)
        trades = tuple(sorted(
            (trade for trade in state.trades if trade.timestamp <= current.timestamp),
            key=lambda trade: trade.timestamp,
        ))
        return state.generation, current, trades, snapshots[:-1], len(snapshots)

    def _generation_is_current(self, symbol, generation, *, require_running):
        health = self.provider.health.symbols.get(symbol)
        return bool(
            (self._running or not require_running)
            and self.provider.health.connected
            and health
            and health.synchronized
            and health.generation == generation
            and self._states[symbol].generation == generation
        )

    def _state(self, symbol):
        normalized = str(symbol).upper()
        if normalized not in self._states:
            raise KeyError(f"Symbol is not configured: {normalized}")
        return self._states[normalized]

    def _sync_trust(self, symbol):
        state = self._state(symbol)
        health = self.provider.health.symbols.get(symbol)
        trusted = bool(self.provider.health.connected and health and health.synchronized)
        if not trusted:
            if state.generation is not None or state.snapshots or state.trades or state.latest:
                state.reset()
            return False
        if state.generation != health.generation:
            state.reset(health.generation)
        return True

    def _prune_trades(self, state, reference_time):
        reference_time = reference_time.astimezone(timezone.utc)
        if state.trade_watermark is None or reference_time > state.trade_watermark:
            state.trade_watermark = reference_time
        cutoff = state.trade_watermark.timestamp() - self.trade_window
        retained = sorted(
            (trade for trade in state.trades if trade.timestamp.timestamp() >= cutoff),
            key=lambda trade: trade.timestamp,
        )
        state.trades.clear()
        state.trades.extend(retained)

    def _record_trade(self, state, trade):
        if state.trade_watermark is None or trade.timestamp > state.trade_watermark:
            state.trade_watermark = trade.timestamp
        cutoff = state.trade_watermark.timestamp() - self.trade_window
        retained = sorted(
            (item for item in (*state.trades, trade) if item.timestamp.timestamp() >= cutoff),
            key=lambda item: item.timestamp,
        )
        state.trades.clear()
        state.trades.extend(retained[-state.trades.maxlen:])

    def _symbol_health(self, symbol):
        state = self._states[symbol]
        if self._running:
            self._prune_trades(state, datetime.now(timezone.utc))
        health = self.provider.health.symbols.get(symbol)
        return SymbolOrderFlowHealth(
            synchronized=bool(health and health.synchronized),
            generation=health.generation if health else 0,
            gaps=health.sequence_gap_count if health else 0,
            latest_analysis_time=state.latest.calculated_at if state.latest else None,
            snapshot_history_size=len(state.snapshots),
            recent_trade_count=len(state.trades),
            ready=state.ready.is_set(),
            analysis_in_flight=state.analysis_in_flight,
            last_analysis_error=state.last_analysis_error,
        )

    async def _consume(self):
        async for event in self.provider.events():
            self.record_event(event)

    async def _symbol_analysis_loop(self, symbol):
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), self.analysis_interval)
                break
            except asyncio.TimeoutError:
                pass
            await self._analyze_symbol_async(symbol)

    async def _supervise_infrastructure(self):
        done, _ = await asyncio.wait(
            self._infrastructure_tasks, return_when=asyncio.FIRST_COMPLETED
        )
        if not self._running:
            return
        task = next(iter(done))
        if task.cancelled():
            error = RuntimeError(f"{task.get_name()} was cancelled unexpectedly")
        else:
            error = task.exception() or RuntimeError(f"{task.get_name()} stopped unexpectedly")
        await self._fail_runtime(error)

    async def _fail_runtime(self, error):
        self._failure = f"{type(error).__name__}: {error}"
        logger.error("Live order-flow runtime failed: %s", self._failure)
        self._running = False
        self._stop_event.set()
        for state in self._states.values():
            state.reset()
        try:
            await self.market_data_service.stop()
        finally:
            current = asyncio.current_task()
            for task in self._infrastructure_tasks | self._analysis_tasks:
                if task is not current and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in self._infrastructure_tasks | self._analysis_tasks if task is not current),
                return_exceptions=True,
            )
