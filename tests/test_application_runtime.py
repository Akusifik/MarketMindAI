import asyncio
import inspect
import time
import unittest
from datetime import datetime, timezone
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch

from exchanges.live.base import ConnectionHealth, SymbolBookHealth
from orderflow import OrderBookLevel, OrderBookSnapshot
from runtime.application import ApplicationMode, MarketMindApp, run_application
from runtime.live_order_flow import (
    LatestOrderFlowAnalysis,
    LiveOrderFlowHealth,
    SymbolOrderFlowHealth,
    LiveOrderFlowService,
)


def symbol_health(*, synchronized=True, analysis_time=None):
    return SymbolOrderFlowHealth(
        synchronized=synchronized,
        generation=1,
        gaps=0,
        latest_analysis_time=analysis_time,
        snapshot_history_size=1,
        recent_trade_count=2,
        ready=analysis_time is not None,
        analysis_in_flight=False,
        last_analysis_error=None,
    )


class FakeLiveService:
    def __init__(self, symbols=("BTCUSDT",), *, start_error=None, latest=None, synchronized=True):
        self.symbols = tuple(symbols)
        self.start_error = start_error
        self.latest = latest or {}
        self.start_calls = 0
        self.stop_calls = 0
        self.running = False
        self.connected = False
        self.failure = None
        self.synchronized = synchronized
        self.background = None

    async def start(self):
        self.start_calls += 1
        if self.start_error:
            raise self.start_error
        self.running = True
        self.connected = True
        self.background = asyncio.create_task(asyncio.Event().wait(), name="fake-live-service")

    async def stop(self):
        self.stop_calls += 1
        self.running = False
        self.connected = False
        if self.background:
            self.background.cancel()
            await asyncio.gather(self.background, return_exceptions=True)
            self.background = None

    def get_latest(self, symbol):
        if symbol not in self.symbols:
            raise KeyError(symbol)
        return self.latest.get(symbol)

    def get_health(self):
        now = datetime.now(timezone.utc)
        return LiveOrderFlowHealth(
            running=self.running,
            connected=self.connected,
            reconnect_count=0,
            symbols=MappingProxyType({
                symbol: symbol_health(
                    synchronized=self.connected and self.synchronized,
                    analysis_time=now if symbol in self.latest else None,
                )
                for symbol in self.symbols
            }),
            failure=self.failure,
        )


class FakeMarket:
    calls = []

    def __init__(self, symbol, timeframe, limit):
        self.values = (symbol, timeframe, limit)
        self.calls.append(["create", self.values])

    def load_data(self):
        self.calls.append(["load", self.values])

    def analyze(self):
        self.calls.append(["analyze", self.values])

    def report(self):
        return "CANDLE REPORT"


class ApplicationRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeMarket.calls = []

    def app(self, *, enabled=True, service=None, symbols=("BTCUSDT",), output=None, **options):
        return MarketMindApp(
            symbol="BTC/USDT", timeframe="1d", candle_limit=200,
            live_order_flow_enabled=enabled,
            live_order_flow_symbols=symbols,
            market_factory=FakeMarket,
            live_service=service,
            output=output or (lambda *_: None),
            live_ready_timeout=options.pop("live_ready_timeout", .03),
            live_first_analysis_timeout=options.pop("live_first_analysis_timeout", .03),
            live_summary_interval=options.pop("live_summary_interval", .02),
            candle_operation_timeout=options.pop("candle_operation_timeout", 1),
            **options,
        )

    async def test_enabled_app_starts_service_once_and_stops_cleanly(self):
        service = FakeLiveService()
        app = self.app(service=service)
        before = set(asyncio.all_tasks())
        await asyncio.gather(app.start(), app.start())
        self.assertEqual(service.start_calls, 1)
        await asyncio.gather(app.stop(), app.stop())
        await asyncio.sleep(0)
        self.assertEqual(service.stop_calls, 1)
        self.assertEqual([task for task in asyncio.all_tasks() - before if not task.done()], [])

    async def test_disabled_app_runs_candle_pipeline_without_live_service(self):
        created = []
        app = MarketMindApp(
            symbol="BTC/USDT", timeframe="1d", candle_limit=200,
            live_order_flow_enabled=False,
            live_order_flow_symbols=(),
            market_factory=FakeMarket,
            live_service_factory=lambda *args, **kwargs: created.append((args, kwargs)),
            output=lambda *_: None,
        )
        self.assertEqual(await app.run(), "CANDLE REPORT")
        self.assertEqual(created, [])
        self.assertFalse(app.get_health().live_order_flow_enabled)

    async def test_configured_symbols_and_runtime_options_are_forwarded(self):
        captured = {}

        def factory(symbols, **options):
            captured.update(symbols=symbols, **options)
            return FakeLiveService(symbols)

        app = MarketMindApp(
            symbol="BTC/USDT", timeframe="1d", candle_limit=200,
            live_order_flow_symbols=("btcusdt", "ETHUSDT"),
            live_analysis_interval=3,
            live_trade_window=90,
            live_snapshot_history=50,
            market_factory=FakeMarket,
            live_service_factory=factory,
            output=lambda *_: None,
        )
        self.assertEqual(captured, {
            "symbols": ("BTCUSDT", "ETHUSDT"),
            "analysis_interval": 3,
            "trade_window": 90,
            "snapshot_history": 50,
        })
        await app.stop()

    async def test_v1_symbol_limit_is_enforced(self):
        with self.assertRaises(ValueError):
            self.app(symbols=("A", "B", "C", "D", "E", "F"))

    async def test_live_start_failure_does_not_kill_candle_pipeline(self):
        service = FakeLiveService(start_error=ConnectionError("unavailable"))
        app = self.app(service=service)
        self.assertEqual(await app.run(), "CANDLE REPORT")
        self.assertIn("unavailable", app.get_health().live_runtime_failure)
        self.assertEqual([call[0] for call in FakeMarket.calls], ["create", "load", "analyze"])

    async def test_terminal_live_failure_does_not_stop_candle_pipeline(self):
        service = FakeLiveService()
        app = self.app(service=service)
        await app.start()
        service.running = False
        service.connected = False
        service.failure = "ConnectionError: terminal live failure"
        self.assertEqual(await app.run_candle_analysis(), "CANDLE REPORT")
        self.assertTrue(app.running)
        self.assertIn("terminal live failure", app.get_health().live_runtime_failure)
        await app.stop()

    async def test_structured_latest_and_safe_missing_access(self):
        latest = LatestOrderFlowAnalysis(
            symbol="BTCUSDT", calculated_at=datetime.now(timezone.utc), generation=2,
            analysis={"bias": "BULLISH", "strength": 35.0},
            snapshot_count=4, trade_count=8, window_seconds=60,
        )
        service = FakeLiveService(latest={"BTCUSDT": latest})
        app = self.app(service=service)
        self.assertIs(app.get_live_order_flow("btcusdt"), latest)
        self.assertIsNone(app.get_live_order_flow("ETHUSDT"))

    async def test_health_reuses_live_provider_and_symbol_state(self):
        service = FakeLiveService(("BTCUSDT",))
        service.running = True
        service.connected = True
        app = self.app(service=service)
        await app.start()
        health = app.get_health()
        self.assertTrue(health.running)
        self.assertTrue(health.live_order_flow_running)
        self.assertTrue(health.live_provider_connected)
        self.assertTrue(health.live_symbols["BTCUSDT"].synchronized)
        await app.stop()

    async def test_cancellation_runs_idempotent_shutdown(self):
        service = FakeLiveService()
        app = self.app(service=service)

        async def wait_forever():
            await asyncio.Event().wait()

        app.run_candle_analysis = wait_forever
        task = asyncio.create_task(app.run())
        while not service.running:
            await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await app.stop()
        self.assertEqual(service.stop_calls, 1)
        self.assertFalse(app.running)

    def test_main_is_thin_and_delegates_lifecycle(self):
        import main

        source = inspect.getsource(main)
        self.assertIn("run_application", source)
        self.assertLessEqual(len(source.splitlines()), 30)
        self.assertNotIn("Market(", source)

    async def test_one_shot_timeout_is_bounded_explicit_and_clean(self):
        service = FakeLiveService(synchronized=False)
        lines = []
        app = self.app(service=service, output=lines.append)
        started = asyncio.get_running_loop().time()
        self.assertEqual(await app.run(), "CANDLE REPORT")
        elapsed = asyncio.get_running_loop().time() - started
        self.assertGreaterEqual(elapsed, .025)
        self.assertLess(elapsed, .2)
        self.assertTrue(any("not ready within" in line for line in lines))
        self.assertEqual(service.stop_calls, 1)
        self.assertIsNone(app.get_health().live_runtime_failure)

    async def test_live_result_arriving_during_grace_is_included(self):
        service = FakeLiveService()
        lines = []
        app = self.app(service=service, output=lines.append, live_first_analysis_timeout=.2)

        async def publish():
            while not service.running:
                await asyncio.sleep(0)
            await asyncio.sleep(.02)
            service.latest["BTCUSDT"] = LatestOrderFlowAnalysis(
                symbol="BTCUSDT", calculated_at=datetime.now(timezone.utc), generation=1,
                analysis={"bias": "BULLISH", "strength": 35.0},
                snapshot_count=2, trade_count=3, window_seconds=60,
            )

        publisher = asyncio.create_task(publish())
        await app.run()
        await publisher
        self.assertTrue(any("BULLISH" in line for line in lines))
        self.assertFalse(any("not ready" in line for line in lines))

    async def test_disabled_one_shot_has_no_grace_delay(self):
        app = self.app(enabled=False, symbols=())
        started = asyncio.get_running_loop().time()
        await app.run()
        self.assertLess(asyncio.get_running_loop().time() - started, .02)

    async def test_long_running_remains_alive_then_stops_without_leaks(self):
        service = FakeLiveService()
        lines = []
        app = self.app(
            service=service, output=lines.append, mode=ApplicationMode.LONG_RUNNING
        )
        before = set(asyncio.all_tasks())
        task = asyncio.create_task(app.run())
        while "CANDLE REPORT" not in lines:
            await asyncio.sleep(0)
        await asyncio.sleep(.03)
        self.assertFalse(task.done())
        app.request_shutdown()
        await asyncio.wait_for(task, .2)
        await asyncio.sleep(0)
        self.assertEqual(service.stop_calls, 1)
        self.assertEqual([item for item in asyncio.all_tasks() - before if not item.done()], [])

    async def test_candle_exception_and_timeout_both_cleanup_live_service(self):
        class BrokenMarket(FakeMarket):
            def load_data(self):
                raise ConnectionError("candles unavailable")

        service = FakeLiveService()
        app = self.app(service=service)
        app.market_factory = BrokenMarket
        with self.assertRaises(ConnectionError):
            await app.run()
        self.assertEqual(service.stop_calls, 1)

        timeout_service = FakeLiveService()
        timeout_app = self.app(
            service=timeout_service, candle_operation_timeout=.01
        )
        timeout_app._run_candle_analysis_sync = lambda: time.sleep(.05)
        with self.assertRaises(asyncio.TimeoutError):
            await timeout_app.run()
        self.assertEqual(timeout_service.stop_calls, 1)

    async def test_sigterm_callback_uses_real_run_application_cleanup(self):
        service = FakeLiveService()
        app = self.app(service=service, mode=ApplicationMode.LONG_RUNNING)
        loop = asyncio.get_running_loop()
        callbacks = {}

        def capture(sig, callback):
            callbacks[sig] = callback

        with patch.object(MarketMindApp, "from_config", return_value=app), \
             patch.object(loop, "add_signal_handler", side_effect=capture), \
             patch.object(loop, "remove_signal_handler", return_value=True):
            task = asyncio.create_task(run_application(settings=SimpleNamespace()))
            while not service.running or not callbacks:
                await asyncio.sleep(0)
            next(iter(callbacks.values()))()
            self.assertIsNone(await asyncio.wait_for(task, .2))
        self.assertEqual(service.stop_calls, 1)

    def test_keyboard_interrupt_is_handled_by_entry_point(self):
        import main

        def interrupt(coroutine):
            coroutine.close()
            raise KeyboardInterrupt

        with patch.object(main.asyncio, "run", side_effect=interrupt):
            main.main([])

    async def test_real_live_service_with_mocked_transport_reaches_summary(self):
        class Provider:
            def __init__(self):
                self.health = ConnectionHealth(
                    symbols={"BTCUSDT": SymbolBookHealth(synchronized=True, generation=1)}
                )
                self.queue = asyncio.Queue()
            async def subscribe_order_book(self, symbol): pass
            async def subscribe_trades(self, symbol): pass
            async def events(self):
                while True:
                    yield await self.queue.get()

        class Transport:
            def __init__(self, provider):
                self.provider = provider
                self.stop_event = asyncio.Event()
            async def start(self):
                self.provider.health.connected = True
                await self.stop_event.wait()
            async def stop(self):
                self.provider.health.connected = False
                self.stop_event.set()

        provider = Provider()
        transport = Transport(provider)
        service = LiveOrderFlowService(
            ("BTCUSDT",), provider=provider, market_data_service=transport,
            analysis_interval=.01, analyzer=lambda *_: {"bias": "NEUTRAL", "strength": 0.0},
        )
        provider.queue.put_nowait(OrderBookSnapshot(
            "BTCUSDT", datetime.now(timezone.utc),
            [OrderBookLevel(100, 1)], [OrderBookLevel(101, 1)], 1,
        ))
        lines = []
        app = self.app(
            service=service, output=lines.append,
            live_ready_timeout=.2, live_first_analysis_timeout=.2,
        )
        before = set(asyncio.all_tasks())
        await app.run()
        await asyncio.sleep(0)
        self.assertTrue(any("NEUTRAL" in line for line in lines))
        self.assertEqual([item for item in asyncio.all_tasks() - before if not item.done()], [])


if __name__ == "__main__":
    unittest.main()
