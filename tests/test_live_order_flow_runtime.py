import asyncio
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone

from exchanges.live.base import ConnectionHealth, SymbolBookHealth
from orderflow import OrderBookLevel, OrderBookSnapshot, Trade
from runtime.live_order_flow import LiveOrderFlowService


NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)


def snapshot(symbol, second, sequence=None):
    return OrderBookSnapshot(
        symbol, NOW + timedelta(seconds=second),
        [OrderBookLevel(100, 4), OrderBookLevel(99, 1)],
        [OrderBookLevel(101, 2), OrderBookLevel(102, 1)],
        second if sequence is None else sequence,
    )


class FakeProvider:
    def __init__(self, symbols):
        self.health = ConnectionHealth(
            symbols={symbol: SymbolBookHealth() for symbol in symbols}
        )
        self.queue = asyncio.Queue()
        self.books = []
        self.trades = []

    async def subscribe_order_book(self, symbol):
        self.books.append(symbol)

    async def subscribe_trades(self, symbol):
        self.trades.append(symbol)

    async def events(self):
        while True:
            yield await self.queue.get()


class FakeMarketDataService:
    def __init__(self, provider):
        self.provider = provider
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self.start_calls = 0
        self.stop_calls = 0

    async def start(self):
        self.start_calls += 1
        self.provider.health.connected = True
        self.started.set()
        await self.stopped.wait()

    async def stop(self):
        self.stop_calls += 1
        self.provider.health.connected = False
        self.stopped.set()


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def make_service(self, symbols=("BTCUSDT",), **options):
        provider = FakeProvider(symbols)
        transport = FakeMarketDataService(provider)
        service = LiveOrderFlowService(
            symbols, provider=provider, market_data_service=transport,
            analysis_interval=options.pop("analysis_interval", .01), **options,
        )
        return service, provider, transport

    @staticmethod
    def trust(provider, symbol, generation=1):
        provider.health.connected = True
        health = provider.health.symbols[symbol]
        health.synchronized = True
        health.generation = generation

    async def test_start_duplicate_stop_repeated_and_no_pending_tasks(self):
        service, provider, transport = self.make_service(("BTCUSDT", "ETHUSDT"))
        before = set(asyncio.all_tasks())
        await asyncio.gather(service.start(), service.start())
        await transport.started.wait()
        self.assertTrue(service.running)
        self.assertEqual(transport.start_calls, 1)
        self.assertEqual(provider.books, ["BTCUSDT", "ETHUSDT"])
        await asyncio.gather(service.stop(), service.stop())
        await asyncio.sleep(0)
        self.assertFalse(service.running)
        self.assertEqual(transport.stop_calls, 1)
        self.assertEqual([task for task in asyncio.all_tasks() - before if not task.done()], [])

    async def test_synchronized_analysis_cadence_and_unsynchronized_suppression(self):
        calls = []
        service, provider, _ = self.make_service(
            analyzer=lambda *args: calls.append(args) or {"valid": len(calls)},
            analysis_interval=.02,
        )
        await service.start()
        provider.health.connected = True
        service.record_event(snapshot("BTCUSDT", 1))
        await asyncio.sleep(.03)
        self.assertIsNone(service.get_latest("BTCUSDT"))
        self.trust(provider, "BTCUSDT")
        for second in range(2, 12):
            service.record_event(snapshot("BTCUSDT", second))
        await asyncio.sleep(.075)
        self.assertGreaterEqual(len(calls), 2)
        self.assertLess(len(calls), 10)
        self.assertLessEqual(service.get_latest("BTCUSDT").analysis["valid"], len(calls))
        await service.stop()

    async def test_bounded_history_expiring_trades_and_health(self):
        service, provider, _ = self.make_service(snapshot_history=3, trade_window=10, max_trades=4)
        self.trust(provider, "BTCUSDT", 4)
        for second in range(5):
            service.record_event(snapshot("BTCUSDT", second))
        for second in (1, 9, 11, 20):
            service.record_event(Trade("BTCUSDT", NOW + timedelta(seconds=second), 100, 1, "BUY"))
        health = service.get_health("BTCUSDT")
        self.assertEqual(health.snapshot_history_size, 3)
        self.assertEqual(health.recent_trade_count, 2)
        self.assertTrue(health.synchronized)
        self.assertEqual(health.generation, 4)
        service.analyze_symbol("BTCUSDT")
        self.assertTrue(service.get_health("BTCUSDT").ready)

    async def test_generation_reset_invalidates_then_fresh_data_analyzes(self):
        service, provider, _ = self.make_service(analyzer=lambda *_: {"ok": True})
        self.trust(provider, "BTCUSDT", 1)
        service.record_event(snapshot("BTCUSDT", 1))
        service.record_event(Trade("BTCUSDT", NOW + timedelta(seconds=1), 100, 1, "BUY"))
        first = service.analyze_symbol("BTCUSDT")
        self.assertEqual(first.generation, 1)
        self.trust(provider, "BTCUSDT", 2)
        self.assertIsNone(service.get_latest("BTCUSDT"))
        health = service.get_health("BTCUSDT")
        self.assertEqual((health.snapshot_history_size, health.recent_trade_count), (0, 0))
        service.record_event(snapshot("BTCUSDT", 2))
        second = service.analyze_symbol("BTCUSDT")
        self.assertEqual(second.generation, 2)

    async def test_multisymbol_generation_isolation(self):
        service, provider, _ = self.make_service(("BTCUSDT", "ETHUSDT"), analyzer=lambda *_: {"ok": True})
        for symbol in service.symbols:
            self.trust(provider, symbol, 1)
            service.record_event(snapshot(symbol, 1))
            service.analyze_symbol(symbol)
        self.trust(provider, "BTCUSDT", 2)
        self.assertIsNone(service.get_latest("BTCUSDT"))
        self.assertIsNotNone(service.get_latest("ETHUSDT"))
        self.assertEqual(service.get_health("ETHUSDT").snapshot_history_size, 1)

    async def test_analysis_exception_preserves_latest_and_service_continues(self):
        outcomes = iter(({"version": 1}, RuntimeError("temporary"), {"version": 2}))

        def analyzer(*_):
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        service, provider, _ = self.make_service(analyzer=analyzer, analysis_interval=.02)
        self.trust(provider, "BTCUSDT")
        service.record_event(snapshot("BTCUSDT", 1))
        first = service.analyze_symbol("BTCUSDT")
        with self.assertRaises(RuntimeError):
            service.analyze_symbol("BTCUSDT")
        self.assertIs(service.get_latest("BTCUSDT"), first)
        self.assertEqual(service.analyze_symbol("BTCUSDT").analysis, {"version": 2})

    async def test_global_health_uses_provider_metadata(self):
        service, provider, _ = self.make_service()
        provider.health.connected = True
        provider.health.reconnect_count = 3
        provider.health.symbols["BTCUSDT"].sequence_gap_count = 2
        health = service.get_health()
        self.assertTrue(health.connected)
        self.assertEqual(health.reconnect_count, 3)
        self.assertEqual(health.symbols["BTCUSDT"].gaps, 2)

    async def wait_thread_event(self, event, timeout=.5):
        async with asyncio.timeout(timeout):
            while not event.is_set():
                await asyncio.sleep(.002)

    async def test_slow_analysis_does_not_block_ingestion(self):
        entered, release = threading.Event(), threading.Event()

        def analyzer(*_):
            entered.set()
            release.wait(.5)
            return {"ok": True}

        service, provider, _ = self.make_service(analyzer=analyzer, analysis_interval=.01)
        self.trust(provider, "BTCUSDT")
        await service.start()
        service.record_event(snapshot("BTCUSDT", 1))
        await self.wait_thread_event(entered)
        await provider.queue.put(snapshot("BTCUSDT", 2))
        await asyncio.sleep(.02)
        self.assertEqual(service.get_health("BTCUSDT").snapshot_history_size, 2)
        release.set()
        await service.stop()

    async def test_slow_btc_does_not_delay_eth(self):
        btc_entered, eth_entered, release = threading.Event(), threading.Event(), threading.Event()

        def analyzer(current, *_):
            if current.symbol == "BTCUSDT":
                btc_entered.set()
                release.wait(.5)
            else:
                eth_entered.set()
            return {"symbol": current.symbol}

        service, provider, _ = self.make_service(
            ("BTCUSDT", "ETHUSDT"), analyzer=analyzer, analysis_interval=.01
        )
        for symbol in service.symbols:
            self.trust(provider, symbol)
            service.record_event(snapshot(symbol, 1))
        await service.start()
        await self.wait_thread_event(btc_entered)
        await self.wait_thread_event(eth_entered)
        async with asyncio.timeout(.5):
            while service.get_latest("ETHUSDT") is None:
                await asyncio.sleep(.002)
        self.assertIsNotNone(service.get_latest("ETHUSDT"))
        release.set()
        await service.stop()

    async def test_same_symbol_never_overlaps_or_builds_backlog(self):
        lock = threading.Lock()
        active = calls = maximum = 0

        def analyzer(*_):
            nonlocal active, calls, maximum
            with lock:
                active += 1
                calls += 1
                maximum = max(maximum, active)
            time.sleep(.04)
            with lock:
                active -= 1
            return {"ok": True}

        service, provider, _ = self.make_service(analyzer=analyzer, analysis_interval=.01)
        self.trust(provider, "BTCUSDT")
        service.record_event(snapshot("BTCUSDT", 1))
        await service.start()
        await asyncio.sleep(.13)
        await service.stop()
        self.assertEqual(maximum, 1)
        self.assertLessEqual(calls, 3)

    async def test_generation_change_during_analysis_discards_result(self):
        entered, release = threading.Event(), threading.Event()
        calls = 0

        def analyzer(*_):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                release.wait(.5)
            return {"cycle": calls}

        service, provider, _ = self.make_service(analyzer=analyzer, analysis_interval=.01)
        self.trust(provider, "BTCUSDT", 1)
        service.record_event(snapshot("BTCUSDT", 1))
        await service.start()
        await self.wait_thread_event(entered)
        self.trust(provider, "BTCUSDT", 2)
        service.record_event(snapshot("BTCUSDT", 2))
        release.set()
        await asyncio.sleep(.02)
        latest = service.get_latest("BTCUSDT")
        self.assertTrue(
            latest is None or (latest.generation == 2 and latest.analysis["cycle"] >= 2)
        )
        await service.stop()

    async def test_sync_loss_during_analysis_discards_result(self):
        entered, release = threading.Event(), threading.Event()

        def analyzer(*_):
            entered.set()
            release.wait(.5)
            return {"stale": True}

        service, provider, _ = self.make_service(analyzer=analyzer, analysis_interval=.01)
        self.trust(provider, "BTCUSDT")
        service.record_event(snapshot("BTCUSDT", 1))
        await service.start()
        await self.wait_thread_event(entered)
        provider.health.symbols["BTCUSDT"].synchronized = False
        release.set()
        await asyncio.sleep(.02)
        self.assertIsNone(service.get_latest("BTCUSDT"))
        await service.stop()

    async def test_stop_during_analysis_prevents_post_stop_publish(self):
        entered, release = threading.Event(), threading.Event()

        def analyzer(*_):
            entered.set()
            release.wait(.5)
            return {"late": True}

        service, provider, _ = self.make_service(analyzer=analyzer, analysis_interval=.01)
        self.trust(provider, "BTCUSDT")
        service.record_event(snapshot("BTCUSDT", 1))
        await service.start()
        await self.wait_thread_event(entered)
        stopping = asyncio.create_task(service.stop())
        await asyncio.sleep(.01)
        self.assertFalse(service.running)
        release.set()
        await stopping
        self.assertIsNone(service.get_latest("BTCUSDT"))

    async def test_provider_task_unexpected_return_marks_runtime_failed(self):
        service, provider, transport = self.make_service()

        async def ends_immediately():
            transport.start_calls += 1
            provider.health.connected = True

        transport.start = ends_immediately
        await service.start()
        async with asyncio.timeout(.5):
            while service.running:
                await asyncio.sleep(.002)
        self.assertIsNotNone(service.get_health().failure)
        await service.stop()

    async def test_consumer_failure_marks_runtime_failed(self):
        service, provider, _ = self.make_service()

        async def broken_events():
            if False:
                yield None
            raise ConnectionError("consumer failed")

        provider.events = broken_events
        await service.start()
        async with asyncio.timeout(.5):
            while service.running:
                await asyncio.sleep(.002)
        self.assertIn("consumer failed", service.get_health().failure)
        await service.stop()

    async def test_analysis_error_isolated_per_symbol(self):
        def analyzer(current, *_):
            if current.symbol == "BTCUSDT":
                raise ValueError("btc temporary")
            return {"symbol": current.symbol}

        service, provider, _ = self.make_service(
            ("BTCUSDT", "ETHUSDT"), analyzer=analyzer, analysis_interval=.01
        )
        for symbol in service.symbols:
            self.trust(provider, symbol)
            service.record_event(snapshot(symbol, 1))
        await service.start()
        async with asyncio.timeout(.5):
            while service.get_latest("ETHUSDT") is None:
                await asyncio.sleep(.002)
        self.assertIn("btc temporary", service.get_health("BTCUSDT").last_analysis_error)
        self.assertTrue(service.running)
        await service.stop()

    async def test_async_temporary_error_preserves_latest_valid_result(self):
        lock = threading.Lock()
        calls = 0

        def analyzer(*_):
            nonlocal calls
            with lock:
                calls += 1
                current_call = calls
            if current_call == 2:
                raise RuntimeError("temporary")
            return {"version": current_call}

        service, provider, _ = self.make_service(analyzer=analyzer, analysis_interval=.015)
        self.trust(provider, "BTCUSDT")
        service.record_event(snapshot("BTCUSDT", 1))
        await service.start()
        async with asyncio.timeout(.5):
            while service.get_latest("BTCUSDT") is None:
                await asyncio.sleep(.002)
        first = service.get_latest("BTCUSDT")
        async with asyncio.timeout(.5):
            while service.get_health("BTCUSDT").last_analysis_error is None:
                await asyncio.sleep(.002)
        self.assertIs(service.get_latest("BTCUSDT"), first)
        self.assertTrue(service.running)
        await service.stop()

    async def test_out_of_order_trade_expiry_uses_timestamps(self):
        service, provider, _ = self.make_service(trade_window=10)
        self.trust(provider, "BTCUSDT")
        base = datetime.now(timezone.utc)
        service.record_event(Trade("BTCUSDT", base, 100, 1, "BUY"))
        service.record_event(Trade("BTCUSDT", base - timedelta(seconds=5), 100, 1, "SELL"))
        service.record_event(Trade("BTCUSDT", base - timedelta(seconds=20), 100, 1, "SELL"))
        timestamps = [trade.timestamp for trade in service._states["BTCUSDT"].trades]
        self.assertEqual(timestamps, [base - timedelta(seconds=5), base])

    async def test_quiet_market_trades_expire_by_wall_clock(self):
        service, provider, _ = self.make_service(trade_window=.03, analysis_interval=.01)
        self.trust(provider, "BTCUSDT")
        service.record_event(Trade("BTCUSDT", datetime.now(timezone.utc), 100, 1, "BUY"))
        await service.start()
        await asyncio.sleep(.06)
        self.assertEqual(len(service._states["BTCUSDT"].trades), 0)
        self.assertEqual(service.get_health("BTCUSDT").recent_trade_count, 0)
        await service.stop()

    async def test_stop_during_transport_backoff_is_clean(self):
        service, _, transport = self.make_service()
        in_backoff = asyncio.Event()

        async def backoff():
            transport.start_calls += 1
            in_backoff.set()
            await transport.stopped.wait()

        transport.start = backoff
        await service.start()
        await in_backoff.wait()
        await asyncio.wait_for(service.stop(), .2)
        self.assertFalse(service.running)


if __name__ == "__main__":
    unittest.main()
