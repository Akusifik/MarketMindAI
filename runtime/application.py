"""Application-level lifecycle orchestration for MarketMind AI."""

import asyncio
import logging
import signal
import threading
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Optional

import config as default_config
from runtime.live_order_flow import LiveOrderFlowService, SymbolOrderFlowHealth

logger = logging.getLogger(__name__)
MAX_LIVE_ORDER_FLOW_SYMBOLS = 5


class ApplicationMode(str, Enum):
    ONE_SHOT = "ONE_SHOT"
    LONG_RUNNING = "LONG_RUNNING"


@dataclass(frozen=True)
class ApplicationHealth:
    running: bool
    live_order_flow_enabled: bool
    live_order_flow_running: bool
    live_provider_connected: bool
    live_symbols: Mapping[str, SymbolOrderFlowHealth]
    live_runtime_failure: Optional[str]


class MarketMindApp:
    """Own application services while keeping candle and live state separate."""

    def __init__(
        self, *, symbol, timeframe, candle_limit,
        mode=ApplicationMode.ONE_SHOT,
        live_order_flow_enabled=True,
        live_order_flow_symbols=("BTCUSDT",),
        live_analysis_interval=2.0,
        live_trade_window=60.0,
        live_snapshot_history=200,
        live_ready_timeout=5.0,
        live_first_analysis_timeout=5.0,
        candle_operation_timeout=60.0,
        live_summary_interval=10.0,
        market_factory=None,
        live_service_factory=LiveOrderFlowService,
        live_service=None,
        output=print,
    ):
        live_symbols = self._normalize_live_symbols(live_order_flow_symbols)
        if live_order_flow_enabled and not live_symbols:
            raise ValueError("At least one live order-flow symbol is required when enabled")
        if live_order_flow_enabled and len(live_symbols) > MAX_LIVE_ORDER_FLOW_SYMBOLS:
            raise ValueError(
                f"Live order flow v1 supports at most {MAX_LIVE_ORDER_FLOW_SYMBOLS} symbols"
            )
        self.symbol = symbol
        self.timeframe = timeframe
        self.candle_limit = candle_limit
        self.mode = self._normalize_mode(mode)
        for name, value in (
            ("live_ready_timeout", live_ready_timeout),
            ("live_first_analysis_timeout", live_first_analysis_timeout),
            ("candle_operation_timeout", candle_operation_timeout),
            ("live_summary_interval", live_summary_interval),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        self.live_ready_timeout = float(live_ready_timeout)
        self.live_first_analysis_timeout = float(live_first_analysis_timeout)
        self.candle_operation_timeout = float(candle_operation_timeout)
        self.live_summary_interval = float(live_summary_interval)
        self.live_order_flow_enabled = bool(live_order_flow_enabled)
        self.live_order_flow_symbols = live_symbols
        self.market_factory = market_factory
        self.output = output
        self._lifecycle_lock = asyncio.Lock()
        self._shutdown_event = asyncio.Event()
        self._running = False
        self._live_started = False
        self._live_start_failure = None
        self._live_service = None
        if self.live_order_flow_enabled:
            self._live_service = live_service or live_service_factory(
                live_symbols,
                analysis_interval=live_analysis_interval,
                trade_window=live_trade_window,
                snapshot_history=live_snapshot_history,
            )

    @classmethod
    def from_config(cls, settings=default_config, **overrides):
        values = {
            "symbol": settings.SYMBOL,
            "timeframe": settings.TIMEFRAME,
            "candle_limit": settings.CANDLE_LIMIT,
            "mode": settings.APPLICATION_MODE,
            "live_order_flow_enabled": settings.LIVE_ORDER_FLOW_ENABLED,
            "live_order_flow_symbols": settings.LIVE_ORDER_FLOW_SYMBOLS,
            "live_analysis_interval": settings.LIVE_ORDER_FLOW_ANALYSIS_INTERVAL,
            "live_trade_window": settings.LIVE_ORDER_FLOW_TRADE_WINDOW,
            "live_snapshot_history": settings.LIVE_ORDER_FLOW_SNAPSHOT_HISTORY,
            "live_ready_timeout": settings.LIVE_READY_TIMEOUT_SECONDS,
            "live_first_analysis_timeout": settings.LIVE_FIRST_ANALYSIS_TIMEOUT_SECONDS,
            "candle_operation_timeout": settings.CANDLE_OPERATION_TIMEOUT_SECONDS,
            "live_summary_interval": settings.LIVE_SUMMARY_INTERVAL_SECONDS,
        }
        values.update(overrides)
        return cls(**values)

    @staticmethod
    def _normalize_mode(mode):
        if isinstance(mode, ApplicationMode):
            return mode
        value = str(mode).strip().upper().replace("-", "_")
        aliases = {"LIVE": "LONG_RUNNING", "ONESHOT": "ONE_SHOT"}
        try:
            return ApplicationMode(aliases.get(value, value))
        except ValueError as error:
            raise ValueError("mode must be ONE_SHOT or LONG_RUNNING") from error

    @staticmethod
    def _normalize_live_symbols(symbols):
        if isinstance(symbols, str):
            raise ValueError("live_order_flow_symbols must be a collection, not a string")
        return tuple(dict.fromkeys(
            str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()
        ))

    @property
    def running(self):
        return self._running

    async def start(self):
        """Start optional services once; live failure does not abort the app."""
        async with self._lifecycle_lock:
            if self._running:
                return
            self._running = True
            self._shutdown_event.clear()
            self._live_start_failure = None
            if self._live_service is not None:
                try:
                    await self._live_service.start()
                    self._live_started = True
                except Exception as error:
                    self._live_start_failure = f"{type(error).__name__}: {error}"
                    logger.exception("Live order-flow startup failed; candle analysis will continue")

    async def stop(self):
        """Stop all application services. Repeated calls are harmless."""
        async with self._lifecycle_lock:
            if not self._running and not self._live_started:
                return
            self._running = False
            self._shutdown_event.set()
            if self._live_service is not None and self._live_started:
                self._live_started = False
                try:
                    await self._live_service.stop()
                except Exception:
                    logger.exception("Live order-flow shutdown failed")

    async def run_candle_analysis(self):
        """Run the existing synchronous candle pipeline without blocking asyncio."""
        loop = asyncio.get_running_loop()
        completed = loop.create_future()

        def deliver(outcome):
            if completed.done():
                return
            succeeded, value = outcome
            if succeeded:
                completed.set_result(value)
            else:
                completed.set_exception(value)

        def execute():
            try:
                outcome = (True, self._run_candle_analysis_sync())
            except BaseException as error:
                outcome = (False, error)
            try:
                loop.call_soon_threadsafe(deliver, outcome)
            except RuntimeError:
                # The event loop may already be closed after process shutdown.
                pass

        threading.Thread(
            target=execute, name="marketmind-candle-analysis", daemon=True
        ).start()
        return await asyncio.wait_for(completed, timeout=self.candle_operation_timeout)

    def _run_candle_analysis_sync(self):
        market_factory = self.market_factory
        if market_factory is None:
            from core.market import Market
            market_factory = Market
        market = market_factory(self.symbol, self.timeframe, self.candle_limit)
        market.load_data()
        market.analyze()
        return market.report()

    async def run(self):
        """Run candle analysis, then follow the configured application mode."""
        await self.start()
        try:
            try:
                report = await self.run_candle_analysis()
            except Exception as error:
                self.output(f"CANDLE ANALYSIS FAILED: {type(error).__name__}: {error}")
                logger.exception("Candle analysis failed")
                raise
            self.output(report)
            if self.mode is ApplicationMode.ONE_SHOT:
                await self._finish_one_shot_live_output()
            else:
                await self._run_long_running()
            return report
        finally:
            await self.stop()

    def request_shutdown(self):
        """Request a graceful stop from signals, a CLI, or future GUI code."""
        self._shutdown_event.set()

    async def _finish_one_shot_live_output(self):
        if not self.live_order_flow_enabled:
            return
        started = asyncio.get_running_loop().time()
        summary = self._format_live_summary()
        if summary:
            self.output(summary)
            return
        ready = await self._wait_for_live_condition(
            lambda: self._any_symbol_synchronized(), self.live_ready_timeout
        )
        analyzed = bool(self._format_live_summary())
        if ready and not analyzed:
            analyzed = await self._wait_for_live_condition(
                lambda: bool(self._format_live_summary()),
                self.live_first_analysis_timeout,
            )
        summary = self._format_live_summary()
        if analyzed and summary:
            self.output(summary)
            return
        failure = self.get_health().live_runtime_failure
        if failure:
            self.output(f"LIVE ORDER FLOW: unavailable ({failure})")
        else:
            elapsed = asyncio.get_running_loop().time() - started
            self.output(f"LIVE ORDER FLOW: not ready within {elapsed:.1f}s grace period")

    async def _run_long_running(self):
        summary = self._format_live_summary()
        if self.live_order_flow_enabled:
            self.output(summary or "LIVE ORDER FLOW: waiting for first analysis")
        else:
            self.output("LIVE ORDER FLOW: disabled")
        last_summary = summary
        while not self._shutdown_event.is_set():
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(), timeout=self.live_summary_interval
                )
            except asyncio.TimeoutError:
                summary = self._format_live_summary()
                if summary:
                    self.output(summary)
                    last_summary = summary
                elif not summary:
                    failure = self.get_health().live_runtime_failure
                    if failure:
                        text = f"LIVE ORDER FLOW: unavailable ({failure})"
                        if text != last_summary:
                            self.output(text)
                            last_summary = text

    async def _wait_for_live_condition(self, predicate, timeout):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while self._running and not self._shutdown_event.is_set():
            if predicate():
                return True
            if self.get_health().live_runtime_failure:
                return False
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(), timeout=min(0.05, remaining)
                )
            except asyncio.TimeoutError:
                pass
        return False

    def _any_symbol_synchronized(self):
        health = self.get_health()
        return any(item.synchronized for item in health.live_symbols.values())

    def get_live_order_flow(self, symbol):
        if self._live_service is None or self._live_start_failure is not None:
            return None
        try:
            return self._live_service.get_latest(str(symbol).upper())
        except KeyError:
            return None

    def get_health(self):
        live_health = None
        failure = None
        if self._live_service is not None:
            try:
                live_health = self._live_service.get_health()
            except Exception as error:
                logger.exception("Unable to read live order-flow health")
                failure = f"{type(error).__name__}: {error}"
            else:
                failure = live_health.failure
        failure = self._live_start_failure or failure
        return ApplicationHealth(
            running=self._running,
            live_order_flow_enabled=self.live_order_flow_enabled,
            live_order_flow_running=bool(live_health and live_health.running),
            live_provider_connected=bool(live_health and live_health.connected),
            live_symbols=(live_health.symbols if live_health else MappingProxyType({})),
            live_runtime_failure=failure,
        )

    def _format_live_summary(self):
        lines = []
        for symbol in self.live_order_flow_symbols:
            latest = self.get_live_order_flow(symbol)
            if latest is None:
                continue
            payload = latest.analysis
            lines.append(
                f"{symbol}: {payload.get('bias', 'UNKNOWN')} "
                f"strength={payload.get('strength', 0):g} generation={latest.generation}"
            )
        return "LIVE ORDER FLOW\n" + "\n".join(lines) if lines else ""


async def run_application(*, settings=default_config, **overrides):
    """Build and run the configured application."""
    app = MarketMindApp.from_config(settings, **overrides)
    loop = asyncio.get_running_loop()
    application_task = asyncio.create_task(app.run(), name="marketmind-application")
    signal_shutdown = False

    def request_signal_shutdown():
        nonlocal signal_shutdown
        signal_shutdown = True
        app.request_shutdown()
        application_task.cancel()

    signal_installed = False
    try:
        loop.add_signal_handler(signal.SIGTERM, request_signal_shutdown)
        signal_installed = True
    except (NotImplementedError, RuntimeError):
        pass
    try:
        try:
            return await application_task
        except asyncio.CancelledError:
            if signal_shutdown:
                return None
            raise
    finally:
        if not application_task.done():
            application_task.cancel()
            await asyncio.gather(application_task, return_exceptions=True)
        if signal_installed:
            loop.remove_signal_handler(signal.SIGTERM)
