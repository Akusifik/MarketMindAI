import asyncio
import unittest
from datetime import datetime, timedelta, timezone

from exchanges.live.base import ConnectionHealth, SymbolBookHealth
from orderflow import OrderBookLevel, OrderBookSnapshot, Trade
from tools.live_order_flow import (
    LiveOrderFlowRunner, format_anomaly, run_live_order_flow, unique_anomalies,
)


NOW = datetime(2026, 7, 31, tzinfo=timezone.utc)


def snapshot(second, sequence=None):
    return OrderBookSnapshot(
        "BTCUSDT", NOW + timedelta(seconds=second),
        [OrderBookLevel(100, 4), OrderBookLevel(99, 1)],
        [OrderBookLevel(101, 2), OrderBookLevel(102, 1)],
        second if sequence is None else sequence,
    )


class MockProvider:
    def __init__(self):
        self.health = ConnectionHealth(symbols={"BTCUSDT": SymbolBookHealth()})
        self.queue = asyncio.Queue()
        self.book_subscriptions = []
        self.trade_subscriptions = []

    async def subscribe_order_book(self, symbol): self.book_subscriptions.append(symbol)
    async def subscribe_trades(self, symbol): self.trade_subscriptions.append(symbol)
    async def events(self):
        while True:
            yield await self.queue.get()


class MockService:
    def __init__(self, provider):
        self.provider = provider
        self.stopped = asyncio.Event()
        self.stop_calls = 0

    async def start(self):
        self.provider.health.connected = True
        await self.stopped.wait()

    async def stop(self):
        self.stop_calls += 1
        self.provider.health.connected = False
        self.stopped.set()


class LiveOrderFlowStateTests(unittest.TestCase):
    def setUp(self):
        self.provider = MockProvider()
        self.calls = []
        self.runner = LiveOrderFlowRunner(
            self.provider, snapshot_history=3, trade_window=10,
            analyzer=lambda book, trades, history: self.calls.append((book, trades, history)) or {"ok": True},
        )

    def trust(self, generation=1):
        self.provider.health.connected = True
        health = self.provider.health.symbols["BTCUSDT"]
        health.synchronized = True
        health.generation = generation

    def test_analysis_waits_for_connected_synchronized_snapshot(self):
        self.runner.record(snapshot(0))
        self.assertIsNone(self.runner.analyze())
        self.trust()
        self.assertIsNone(self.runner.analyze())
        self.runner.record(snapshot(1))
        self.assertEqual(self.runner.analyze(), {"ok": True})
        self.assertEqual(len(self.calls), 1)

    def test_snapshot_history_is_bounded(self):
        self.trust()
        for second in range(5):
            self.runner.record(snapshot(second))
        self.assertEqual([item.sequence for _, item in self.runner.snapshots], [2, 3, 4])

    def test_trade_window_is_time_bounded(self):
        self.trust()
        self.runner.record(snapshot(20))
        for second in (1, 9, 11, 20):
            self.runner.record(Trade("BTCUSDT", NOW + timedelta(seconds=second), 100, 1, "BUY"))
        self.assertEqual([item.timestamp for _, item in self.runner.trades], [NOW + timedelta(seconds=11), NOW + timedelta(seconds=20)])

    def test_generation_change_clears_stale_history(self):
        self.trust(1)
        self.runner.record(snapshot(1))
        self.runner.record(Trade("BTCUSDT", NOW + timedelta(seconds=1), 100, 1, "BUY"))
        self.trust(2)
        self.runner.record(snapshot(2))
        self.assertEqual(len(self.runner.snapshots), 1)
        self.assertEqual(len(self.runner.trades), 0)
        self.runner.analyze()
        self.assertEqual(self.calls[-1][2], [])

    def test_old_generation_entries_never_enter_analysis(self):
        self.trust(2)
        self.runner.record(snapshot(2))
        old_trade = Trade("BTCUSDT", NOW + timedelta(seconds=1), 100, 1, "SELL")
        self.runner.trades.appendleft((1, old_trade))
        self.runner.snapshots.appendleft((1, snapshot(1)))
        self.runner.analyze()
        _, trades, history = self.calls[-1]
        self.assertEqual(trades, [])
        self.assertEqual(history, [])

    def test_unsynchronized_transition_clears_history(self):
        self.trust()
        self.runner.record(snapshot(1))
        self.provider.health.symbols["BTCUSDT"].synchronized = False
        self.assertIsNone(self.runner.analyze())
        self.assertFalse(self.runner.snapshots)


class AnomalyOutputTests(unittest.TestCase):
    def anomaly(self, side, price, **extra):
        return {
            "type": "POSSIBLE_ICEBERG", "side": side, "price": price,
            "strength": 25, "evidence": ["Low-confidence refill heuristic."],
            **extra,
        }

    def result(self, anomalies):
        return {
            "book_state": {"state": "BALANCED", "imbalance": 0.0, "mid_price": 100.5},
            "trade_flow": {"pressure": "BALANCED", "buy_volume": 1, "sell_volume": 1, "delta": 0},
            "liquidity_walls": [], "absorption": {"type": "NONE"}, "sweeps": [],
            "bias": "NEUTRAL", "strength": 0, "anomalies": anomalies,
        }

    def output_for(self, anomalies):
        provider = MockProvider()
        provider.health.connected = True
        provider.health.symbols["BTCUSDT"] = SymbolBookHealth(synchronized=True, generation=1)
        lines = []
        LiveOrderFlowRunner(provider, output=lines.append).print_summary(self.result(anomalies))
        return lines

    def test_two_iceberg_levels_remain_separate(self):
        anomalies = [self.anomaly("ASK", 62980.1), self.anomaly("ASK", 62981.2)]
        self.assertEqual(unique_anomalies(anomalies), anomalies)

    def test_bid_and_ask_candidates_remain_separate(self):
        anomalies = [self.anomaly("BID", 62972.5), self.anomaly("ASK", 62972.5)]
        self.assertEqual(unique_anomalies(anomalies), anomalies)

    def test_exact_duplicate_is_displayed_once(self):
        anomaly = self.anomaly("ASK", 62980.1, refill_cycles=[{"after": NOW}])
        lines = self.output_for([anomaly, dict(anomaly)])
        self.assertEqual(sum("POSSIBLE_ICEBERG" in line for line in lines), 1)

    def test_output_includes_side_price_strength_and_evidence(self):
        line = format_anomaly(self.anomaly("BID", 62972.5))
        self.assertIn("POSSIBLE_ICEBERG | BID @ 62972.5 | strength=25", line)
        self.assertIn("Low-confidence", line)

    def test_missing_optional_metadata_renders_safely(self):
        self.assertEqual(format_anomaly({"type": "POSSIBLE_SPOOFING"}), "POSSIBLE_SPOOFING")
        self.assertEqual(format_anomaly({}), "UNKNOWN")

    def test_no_anomalies_remains_compatible(self):
        lines = self.output_for([])
        self.assertIn("Anomalies:", lines)
        self.assertIn("- NONE", lines)


class LiveOrderFlowAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_analysis_cadence_is_not_per_delta(self):
        from analysis.order_flow_analysis import analyze_order_flow

        provider = MockProvider()
        provider.health.connected = True
        provider.health.symbols["BTCUSDT"] = SymbolBookHealth(synchronized=True, generation=1)
        calls = []
        def counting_analyzer(*args):
            calls.append(args)
            return analyze_order_flow(*args)

        runner = LiveOrderFlowRunner(provider, analysis_interval=.02, analyzer=counting_analyzer, output=lambda *_: None)
        stop = asyncio.Event()
        task = asyncio.create_task(runner.analysis_loop(stop))
        for second in range(1, 11):
            runner.record(snapshot(second))
        await asyncio.sleep(.055)
        stop.set()
        await task
        self.assertGreaterEqual(len(calls), 2)
        self.assertLess(len(calls), 10)

    async def test_clean_shutdown_with_mocked_transport(self):
        provider = MockProvider()
        service = MockService(provider)
        before = set(asyncio.all_tasks())
        runner = await run_live_order_flow(
            symbol="BTCUSDT", duration=.03, analysis_interval=.01,
            trade_window=60, snapshot_history=10, max_trades=100,
            provider=provider, service=service, output=lambda *_: None,
        )
        await asyncio.sleep(0)
        leaked = [task for task in asyncio.all_tasks() - before if not task.done()]
        self.assertEqual(leaked, [])
        self.assertEqual(service.stop_calls, 1)
        self.assertEqual(provider.book_subscriptions, ["BTCUSDT"])
        self.assertEqual(provider.trade_subscriptions, ["BTCUSDT"])
        self.assertEqual(runner.analysis_count, 0)

    async def test_runner_reuses_existing_engine_by_default(self):
        from analysis.order_flow_analysis import analyze_order_flow
        provider = MockProvider()
        runner = LiveOrderFlowRunner(provider)
        self.assertIs(runner.analyzer, analyze_order_flow)


if __name__ == "__main__":
    unittest.main()
