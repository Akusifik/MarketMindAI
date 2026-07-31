import unittest
from datetime import datetime, timedelta, timezone

from orderflow import (
    OrderBookLevel, OrderBookSnapshot, OrderBookState, Trade,
    calculate_order_book_metrics, calculate_trade_flow, cumulative_delta,
)


NOW = datetime(2026, 3, 1, tzinfo=timezone.utc)


def snapshot(sequence=10):
    return OrderBookSnapshot(
        "BTCUSDT", NOW,
        [OrderBookLevel(100, 3), OrderBookLevel(99, 2), OrderBookLevel(98, 1)],
        [OrderBookLevel(101, 4), OrderBookLevel(102, 2), OrderBookLevel(103, 1)],
        sequence,
    )


class OrderBookTests(unittest.TestCase):
    def test_valid_snapshot_and_basic_metrics(self):
        metrics = calculate_order_book_metrics(snapshot(), top_n=2)
        self.assertEqual(metrics["best_bid"], 100)
        self.assertEqual(metrics["best_ask"], 101)
        self.assertEqual(metrics["spread"], 1)
        self.assertEqual(metrics["mid_price"], 100.5)
        self.assertEqual(metrics["total_bid_depth"], 6)
        self.assertEqual(metrics["total_ask_depth"], 7)
        self.assertEqual(metrics["top_n_bid_depth"], 5)
        self.assertEqual(metrics["top_n_ask_depth"], 6)

    def test_sorting_crossed_and_invalid_levels_are_rejected(self):
        with self.assertRaises(ValueError):
            OrderBookSnapshot("BTCUSDT", NOW, [OrderBookLevel(99, 1), OrderBookLevel(100, 1)], [], 1)
        with self.assertRaises(ValueError):
            OrderBookSnapshot("BTCUSDT", NOW, [], [OrderBookLevel(102, 1), OrderBookLevel(101, 1)], 1)
        with self.assertRaises(ValueError):
            OrderBookSnapshot("BTCUSDT", NOW, [OrderBookLevel(101, 1)], [OrderBookLevel(101, 1)], 1)
        with self.assertRaises(ValueError):
            OrderBookLevel(-1, 1)
        with self.assertRaises(ValueError):
            OrderBookLevel(1, -1)
        with self.assertRaises(ValueError):
            OrderBookSnapshot("BTCUSDT", NOW, [OrderBookLevel(100, 0)], [], 1)

    def test_imbalance_and_zero_depth_are_safe(self):
        balanced = OrderBookSnapshot("BTCUSDT", NOW, [OrderBookLevel(100, 2)], [OrderBookLevel(101, 2)])
        bid_heavy = OrderBookSnapshot("BTCUSDT", NOW, [OrderBookLevel(100, 8)], [OrderBookLevel(101, 2)])
        ask_heavy = OrderBookSnapshot("BTCUSDT", NOW, [OrderBookLevel(100, 2)], [OrderBookLevel(101, 8)])
        empty = OrderBookSnapshot("BTCUSDT", NOW, [], [])
        self.assertEqual(calculate_order_book_metrics(balanced)["imbalance"], 0)
        self.assertGreater(calculate_order_book_metrics(bid_heavy)["imbalance"], 0)
        self.assertLess(calculate_order_book_metrics(ask_heavy)["imbalance"], 0)
        self.assertEqual(calculate_order_book_metrics(empty)["imbalance"], 0)

    def test_incremental_updates_and_sequence_validation(self):
        state = OrderBookState.from_snapshot(snapshot())
        updated = state.apply_updates(
            bids=[OrderBookLevel(100, 5), OrderBookLevel(99, 0)],
            asks=[OrderBookLevel(102, 3)], timestamp=NOW + timedelta(seconds=1), sequence=11,
        )
        self.assertEqual(updated.bids[0].quantity, 5)
        self.assertNotIn(99, state.bids)
        self.assertEqual(updated.asks[1].quantity, 3)
        with self.assertRaises(ValueError):
            state.apply_updates(timestamp=NOW + timedelta(seconds=2), sequence=11)
        with self.assertRaises(ValueError):
            state.apply_updates(timestamp=NOW + timedelta(seconds=2), sequence=9)


class TradeFlowTests(unittest.TestCase):
    def test_trade_validation_and_side_semantics(self):
        buy = Trade("BTCUSDT", NOW, 100, 2, "buy", "a")
        sell = Trade("BTCUSDT", NOW, 100, 1, "SELL", "b")
        unknown = Trade("BTCUSDT", NOW, 100, 1, "maker")
        self.assertEqual(buy.side, "BUY")
        self.assertEqual(sell.side, "SELL")
        self.assertEqual(unknown.side, "UNKNOWN")
        with self.assertRaises(ValueError):
            Trade("BTCUSDT", NOW, 0, 1, "BUY")
        with self.assertRaises(ValueError):
            Trade("BTCUSDT", NOW, 100, 0, "BUY")

    def test_flow_and_cumulative_delta_are_chronological(self):
        later = Trade("BTCUSDT", NOW + timedelta(seconds=2), 102, 1, "SELL", "sell")
        earlier = Trade("BTCUSDT", NOW + timedelta(seconds=1), 101, 3, "BUY", "buy")
        unknown = Trade("BTCUSDT", NOW + timedelta(seconds=3), 103, 2, "UNKNOWN", "unknown")
        flow = calculate_trade_flow([later, earlier, unknown])
        points = cumulative_delta([later, earlier, unknown])
        self.assertEqual(flow["buy_volume"], 3)
        self.assertEqual(flow["sell_volume"], 1)
        self.assertEqual(flow["unknown_volume"], 2)
        self.assertEqual(flow["total_volume"], 6)
        self.assertEqual(flow["delta"], 2)
        self.assertEqual([point.trade_id for point in points], ["buy", "sell", "unknown"])
        self.assertEqual([point.cumulative_delta for point in points], [3, 2, 2])


if __name__ == "__main__":
    unittest.main()
