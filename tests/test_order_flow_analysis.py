import unittest
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from analysis.analyzer import analyze_market
from analysis.order_flow_analysis import analyze_order_flow
from orderflow import OrderBookLevel, OrderBookSnapshot, Trade


NOW = datetime(2026, 4, 1, tzinfo=timezone.utc)


def book(bids, asks, offset=0, sequence=None):
    return OrderBookSnapshot("BTCUSDT", NOW + timedelta(seconds=offset), [OrderBookLevel(*item) for item in bids], [OrderBookLevel(*item) for item in asks], offset if sequence is None else sequence)


def flow(side, start_second, prices=(100, 100, 100), quantities=(3, 3, 3)):
    return [Trade("BTCUSDT", NOW + timedelta(seconds=start_second, milliseconds=10 + index), price, quantity, side, str(index)) for index, (price, quantity) in enumerate(zip(prices, quantities))]


def stable_bid_wall(offset):
    return book([(100, 8), (99, 1), (98, 1)], [(101, 1), (102, 1), (103, 1)], offset)


class OrderFlowAnalysisTests(unittest.TestCase):
    def test_book_states_and_top_n_near_mid_imbalance(self):
        balanced = book([(100, 2), (99, 2), (98, 2)], [(101, 2), (102, 2), (103, 2)])
        bid_heavy = book([(100, 9), (99, 4), (98, 1)], [(101, 2), (102, 1), (103, 1)])
        ask_heavy = book([(100, 2), (99, 1), (98, 1)], [(101, 9), (102, 4), (103, 1)])
        self.assertEqual(analyze_order_flow(balanced)["book_state"]["state"], "BALANCED")
        self.assertEqual(analyze_order_flow(bid_heavy)["book_state"]["state"], "BID_HEAVY")
        self.assertEqual(analyze_order_flow(ask_heavy)["book_state"]["state"], "ASK_HEAVY")
        self.assertGreater(analyze_order_flow(bid_heavy, top_n=1)["book_state"]["near_mid_imbalance"], 0)

    def test_one_snapshot_level_is_not_persistent_wall_but_stable_wall_is(self):
        self.assertEqual(analyze_order_flow(stable_bid_wall(0))["liquidity_walls"], [])
        result = analyze_order_flow(stable_bid_wall(2), (), [stable_bid_wall(0), stable_bid_wall(1)])
        wall = result["liquidity_walls"][0]
        self.assertEqual(wall["side"], "BID")
        self.assertEqual(wall["persistence"]["observations"], 3)
        self.assertEqual(wall["persistence"]["persistence_ratio"], 1.0)

    def test_trade_pressure_delta_and_synchronized_price_delta_window(self):
        current = book([(100, 2), (99, 2)], [(101, 2), (102, 2)], 2)
        buying = analyze_order_flow(current, flow("BUY", 1, (100, 101, 102, 103), (3, 3, 3, 3)))
        selling = analyze_order_flow(current, flow("SELL", 1, (103, 102, 101, 100), (3, 3, 3, 3)))
        divergent = analyze_order_flow(current, flow("SELL", 1, (100, 101, 102, 103), (3, 3, 3, 3)))
        low_count = analyze_order_flow(current, flow("BUY", 1, (100, 101), (3, 3)))
        self.assertEqual(buying["trade_flow"]["pressure"], "BUY_PRESSURE")
        self.assertEqual(selling["trade_flow"]["pressure"], "SELL_PRESSURE")
        self.assertEqual(buying["cumulative_delta"]["price_relation"], "CONFIRMS")
        self.assertEqual(divergent["cumulative_delta"]["price_relation"], "DIVERGES")
        self.assertEqual(low_count["cumulative_delta"]["price_relation"], "INSUFFICIENT")

    def test_absorption_requires_meaningful_depth_normalized_flow(self):
        ask0 = book([(100, 1), (99, 1), (98, 1)], [(101, 8), (102, 1), (103, 1)], 0)
        ask1 = book([(100, 1), (99, 1), (98, 1)], [(101, 8), (102, 1), (103, 1)], 1)
        ask2 = book([(100, 1), (99, 1), (98, 1)], [(101, 8), (102, 1), (103, 1)], 2)
        meaningful = analyze_order_flow(ask2, flow("BUY", 1), [ask0, ask1])
        tiny = analyze_order_flow(ask2, flow("BUY", 1, quantities=(.001, .001, .001)), [ask0, ask1])
        self.assertEqual(meaningful["absorption"]["type"], "POSSIBLE_BUY_ABSORPTION")
        self.assertEqual(tiny["absorption"]["type"], "NONE")

    def test_absorption_uses_only_previous_current_snapshot_interval(self):
        ask0 = book([(100, 1), (99, 1), (98, 1)], [(101, 8), (102, 1), (103, 1)], 0)
        ask1 = book([(100, 1), (99, 1), (98, 1)], [(101, 8), (102, 1), (103, 1)], 1)
        ask2 = book([(100, 1), (99, 1), (98, 1)], [(101, 8), (102, 1), (103, 1)], 2)
        old = flow("BUY", 0, quantities=(3, 3, 3))
        current = flow("BUY", 1, quantities=(3, 3, 3))
        self.assertEqual(analyze_order_flow(ask2, old, [ask0, ask1])["absorption"]["type"], "NONE")
        self.assertEqual(analyze_order_flow(ask2, current, [ask0, ask1])["absorption"]["type"], "POSSIBLE_BUY_ABSORPTION")

    def test_sweep_requires_prior_persistent_wall_and_interval_trades(self):
        first, prior = stable_bid_wall(0), stable_bid_wall(1)
        current = book([(98, 1), (97, 1), (96, 1)], [(101, 1), (102, 1), (103, 1)], 2)
        after_wall = flow("SELL", 1, (100, 99, 98), (3, 3, 3))
        before_wall = flow("SELL", 0, (100, 99, 98), (3, 3, 3))
        self.assertTrue(analyze_order_flow(current, after_wall, [first, prior])["sweeps"])
        self.assertEqual(analyze_order_flow(current, before_wall, [first, prior])["sweeps"], [])

    def test_spoofing_requires_persistent_display_and_no_repricing(self):
        first = book([(100, 1), (99, 1), (98, 1)], [(101, 1), (102, 1), (103, 1)], 0)
        appeared = book([(100, 1), (99, 1), (97, 8)], [(101, 1), (102, 1), (103, 1)], 1)
        persisted = book([(100, 1), (99, 1), (97, 8)], [(101, 1), (102, 1), (103, 1)], 2)
        disappeared = book([(100, 1), (99, 1), (98, 1)], [(101, 1), (102, 1), (103, 1)], 3)
        anomalies = analyze_order_flow(disappeared, (), [first, appeared, persisted])["anomalies"]
        self.assertIn("POSSIBLE_SPOOFING", [item["type"] for item in anomalies])
        repriced = book([(110, 1), (109, 1), (107, 1)], [(111, 1), (112, 1), (113, 1)], 3)
        self.assertNotIn("POSSIBLE_SPOOFING", [item["type"] for item in analyze_order_flow(repriced, (), [first, appeared, persisted])["anomalies"]])

    def test_missing_market_reference_fails_closed_for_spoofing(self):
        first = book([(100, 1), (99, 1), (98, 1)], [(101, 1), (102, 1), (103, 1)], 0)
        appeared = book([(100, 1), (99, 1), (97, 8)], [(101, 1), (102, 1), (103, 1)], 1)
        persisted = book([(100, 1), (99, 1), (97, 8)], [(101, 1), (102, 1), (103, 1)], 2)
        one_sided = book([], [(101, 1), (102, 1), (103, 1)], 3)
        anomalies = analyze_order_flow(one_sided, (), [first, appeared, persisted])["anomalies"]
        self.assertEqual(anomalies[0]["type"], "INSUFFICIENT")

    def test_iceberg_requires_repeated_executions_and_replenishment(self):
        first, second, third, fourth = stable_bid_wall(0), stable_bid_wall(1), stable_bid_wall(2), stable_bid_wall(3)
        no_execution = analyze_order_flow(fourth, (), [first, second, third])
        stable_depth_trades = analyze_order_flow(fourth, flow("SELL", 2, (100, 100, 100), (2, 2, 2)), [first, second, third])
        refill_trades = [
            Trade("BTCUSDT", NOW + timedelta(milliseconds=500), 100, 2, "SELL"),
            Trade("BTCUSDT", NOW + timedelta(seconds=1, milliseconds=500), 100, 2, "SELL"),
        ]
        repeated = analyze_order_flow(fourth, refill_trades, [first, second, third])
        one_cycle = analyze_order_flow(fourth, [Trade("BTCUSDT", NOW + timedelta(seconds=1, milliseconds=500), 100, 2, "SELL")], [first, second, third])
        self.assertNotIn("POSSIBLE_ICEBERG", [item["type"] for item in no_execution["anomalies"]])
        self.assertNotIn("POSSIBLE_ICEBERG", [item["type"] for item in stable_depth_trades["anomalies"]])
        self.assertNotIn("POSSIBLE_ICEBERG", [item["type"] for item in one_cycle["anomalies"]])
        self.assertIn("POSSIBLE_ICEBERG", [item["type"] for item in repeated["anomalies"]])

    def test_sequence_ordering_strength_caps_and_candle_pipeline(self):
        first = book([(100, 1), (99, 1)], [(101, 1), (102, 1)], 0, 10)
        current = book([(100, 1), (99, 1)], [(101, 1), (102, 1)], 1, 9)
        with self.assertRaises(ValueError):
            analyze_order_flow(current, (), [first])
        strong = stable_bid_wall(2)
        result = analyze_order_flow(strong, flow("BUY", 1, (100, 101, 102), (10, 10, 10)), [stable_bid_wall(0), stable_bid_wall(1)])
        self.assertLessEqual(result["strength"], 75)
        close = 100 + np.arange(240) * .1
        df = pd.DataFrame({"timestamp": pd.date_range("2026-01-01", periods=240, freq="h"), "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 100})
        _, candle_result = analyze_market(df)
        self.assertIn(candle_result.decision["action"], {"BUY", "SELL", "HOLD"})


if __name__ == "__main__":
    unittest.main()
