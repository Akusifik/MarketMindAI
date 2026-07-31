import unittest

import numpy as np
import pandas as pd

from analysis.analyzer import analyze_market
from analysis.market_structure import analyze_market_structure
from analysis.report import generate_report


def candles(highs, lows, closes=None):
    closes = closes or [(high + low) / 2 for high, low in zip(highs, lows)]
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=len(highs), freq="h"),
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [100] * len(highs),
    })


UPTREND = (
    [10, 12, 11, 14, 13, 16, 15, 18, 17],
    [8.5, 10, 9, 11, 10, 12, 11, 13, 14],
)
DOWNTREND = (
    [18, 16, 17, 15, 16, 14, 15, 13, 14],
    [16.5, 14, 15, 12, 13, 10, 11, 8, 9],
)
RANGE = (
    [10, 12, 11, 12.1, 11, 12, 11, 12.1, 11],
    [8, 9, 8.5, 9.1, 8.4, 9, 8.6, 9, 8.5],
)


class MarketStructureTests(unittest.TestCase):
    def test_higher_high_higher_low_uptrend(self):
        structure = analyze_market_structure(candles(*UPTREND), swing_window=1)

        self.assertEqual(structure["trend"], "UPTREND")
        self.assertIn("HH", structure["structure_sequence"])
        self.assertIn("HL", structure["structure_sequence"])
        self.assertGreater(structure["strength"], 0)

    def test_lower_high_lower_low_downtrend(self):
        structure = analyze_market_structure(candles(*DOWNTREND), swing_window=1)

        self.assertEqual(structure["trend"], "DOWNTREND")
        self.assertIn("LH", structure["structure_sequence"])
        self.assertIn("LL", structure["structure_sequence"])

    def test_compact_swings_are_a_range(self):
        structure = analyze_market_structure(candles(*RANGE), swing_window=1)

        self.assertEqual(structure["trend"], "RANGE")
        self.assertIsNone(structure["last_event"])

    def test_insufficient_data_is_unclear(self):
        structure = analyze_market_structure(candles([10, 11], [8, 9]))

        self.assertEqual(structure["trend"], "UNCLEAR")
        self.assertEqual(structure["strength"], 0)
        self.assertEqual(structure["swings"], [])

    def test_ambiguous_structure_is_not_forced_directional(self):
        structure = analyze_market_structure(
            candles([10, 12, 11, 13], [8, 9, 7, 9]),
            swing_window=1,
        )

        self.assertEqual(structure["trend"], "UNCLEAR")

    def test_bullish_break_of_structure(self):
        structure = analyze_market_structure(candles(*UPTREND), swing_window=1)

        self.assertEqual(structure["last_event"]["type"], "BULLISH_BOS")

    def test_bearish_break_of_structure(self):
        structure = analyze_market_structure(candles(*DOWNTREND), swing_window=1)

        self.assertEqual(structure["last_event"]["type"], "BEARISH_BOS")

    def test_unconfirmed_final_swing_is_ignored(self):
        structure = analyze_market_structure(
            candles([10, 12, 11, 10], [8, 9, 8.5, 7]),
            swing_window=1,
        )

        self.assertNotIn(3, [swing["index"] for swing in structure["swings"]])

    def test_final_pivot_is_only_visible_after_confirmation(self):
        before = analyze_market_structure(
            candles([10, 11, 12], [8, 9, 10]), swing_window=1,
        )
        after = analyze_market_structure(
            candles([10, 11, 12, 11], [8, 9, 10, 9]), swing_window=1,
        )

        self.assertNotIn(2, [swing["index"] for swing in before["swings"]])
        confirmed = next(swing for swing in after["swings"] if swing["index"] == 2)
        self.assertEqual(confirmed["confirmed_at"], 3)

    def test_bullish_bos_is_unavailable_before_level_confirmation(self):
        structure = analyze_market_structure(
            candles([10, 12, 11], [8, 9, 9], [9, 10, 10]),
            swing_window=1,
        )

        self.assertEqual(structure["events"], [])

    def test_bearish_bos_is_unavailable_before_level_confirmation(self):
        structure = analyze_market_structure(
            candles([10, 9, 9], [8, 6, 7], [9, 7, 8]),
            swing_window=1,
        )

        self.assertEqual(structure["events"], [])

    def test_bullish_bos_uses_breakout_candle_as_actionable_time(self):
        structure = analyze_market_structure(
            candles([10, 12, 11, 13], [8, 9, 9, 10], [9, 10, 10, 12.5]),
            swing_window=1,
        )
        event = structure["last_event"]

        self.assertEqual(event["type"], "BULLISH_BOS")
        self.assertEqual(event["actionable_index"], 3)
        self.assertEqual(event["breakout_index"], 3)
        self.assertEqual(event["source_swing"]["pivot_index"], 1)
        self.assertEqual(event["source_swing"]["confirmation_index"], 2)

    def test_wick_above_level_without_close_has_no_bullish_bos(self):
        structure = analyze_market_structure(
            candles([10, 12, 11, 13], [8, 9, 9, 10], [9, 10, 10, 11.5]),
            swing_window=1,
        )

        self.assertEqual(structure["events"], [])

    def test_close_below_level_creates_bearish_bos(self):
        structure = analyze_market_structure(
            candles([10, 9, 9, 8], [8, 6, 7, 5], [9, 7, 8, 5.5]),
            swing_window=1,
        )

        self.assertEqual(structure["last_event"]["type"], "BEARISH_BOS")
        self.assertEqual(structure["last_event"]["actionable_index"], 3)

    def test_wick_below_level_without_close_has_no_bearish_bos(self):
        structure = analyze_market_structure(
            candles([10, 9, 9, 8], [8, 6, 7, 5], [9, 7, 8, 6.5]),
            swing_window=1,
        )

        self.assertEqual(structure["events"], [])

    def test_repeated_closes_beyond_same_level_create_one_bos(self):
        structure = analyze_market_structure(
            candles(
                [10, 12, 11, 13, 14, 15],
                [8, 9, 9, 10, 11, 12],
                [9, 10, 10, 12.5, 13.5, 14.5],
            ),
            swing_window=1,
        )

        self.assertEqual(
            [event["type"] for event in structure["events"]],
            ["BULLISH_BOS"],
        )

    def test_uptrend_to_downtrend_uses_recent_structure(self):
        highs = UPTREND[0] + [16, 17, 15, 16, 14, 15, 13, 14]
        lows = UPTREND[1] + [12, 13, 10, 11, 8, 9, 6, 7]
        structure = analyze_market_structure(candles(highs, lows), swing_window=1)

        self.assertEqual(structure["trend"], "DOWNTREND")

    def test_downtrend_to_uptrend_uses_recent_structure(self):
        highs = DOWNTREND[0] + [16, 14, 17, 15, 18, 16, 19, 18]
        lows = DOWNTREND[1] + [12, 10, 12, 11, 13, 12, 14, 15]
        structure = analyze_market_structure(candles(highs, lows), swing_window=1)

        self.assertEqual(structure["trend"], "UPTREND")

    def test_mixed_recent_transition_is_unclear(self):
        highs = UPTREND[0] + [16, 17, 14, 15]
        lows = UPTREND[1] + [12, 13, 7, 8]
        structure = analyze_market_structure(candles(highs, lows), swing_window=1)

        self.assertEqual(structure["trend"], "UNCLEAR")

    def test_missing_ohlc_is_rejected(self):
        with self.assertRaises(ValueError):
            analyze_market_structure(pd.DataFrame({"close": [1, 2, 3]}))

    def test_nan_ohlc_is_rejected(self):
        df = candles([10, 12, 11], [8, 9, 9])
        df.loc[1, "high"] = float("nan")

        with self.assertRaises(ValueError):
            analyze_market_structure(df, swing_window=1)

    def test_non_numeric_ohlc_is_rejected(self):
        df = candles([10, 12, 11], [8, 9, 9])
        df["low"] = df["low"].astype(object)
        df.loc[1, "low"] = "invalid"

        with self.assertRaises(ValueError):
            analyze_market_structure(df, swing_window=1)

    def test_unsorted_timestamps_are_rejected(self):
        df = candles([10, 12, 11], [8, 9, 9])
        df.loc[1, "timestamp"] = df.loc[0, "timestamp"] - pd.Timedelta(hours=1)

        with self.assertRaises(ValueError):
            analyze_market_structure(df, swing_window=1)

    def test_invalid_high_low_candle_is_rejected(self):
        df = candles([10, 12, 11], [8, 9, 9])
        df.loc[1, "high"] = 8

        with self.assertRaises(ValueError):
            analyze_market_structure(df, swing_window=1)

    def test_low_volatility_range_is_detected(self):
        structure = analyze_market_structure(candles(*RANGE), swing_window=1)

        self.assertEqual(structure["trend"], "RANGE")

    def test_high_volatility_range_is_detected(self):
        highs = [value * 10 for value in RANGE[0]]
        lows = [value * 10 for value in RANGE[1]]
        structure = analyze_market_structure(candles(highs, lows), swing_window=1)

        self.assertEqual(structure["trend"], "RANGE")

    def test_unconfirmed_volatile_final_candle_does_not_change_range(self):
        baseline = analyze_market_structure(candles(*RANGE), swing_window=1)
        highs = RANGE[0] + [200]
        lows = RANGE[1] + [8]
        closes = [(high + low) / 2 for high, low in zip(highs, lows)]
        extended = analyze_market_structure(candles(highs, lows, closes), swing_window=1)

        self.assertEqual(baseline["trend"], "RANGE")
        self.assertEqual(extended["trend"], "RANGE")


class MarketStructureIntegrationTests(unittest.TestCase):
    @staticmethod
    def _market_data():
        index = np.arange(240)
        close = 100 + (index * 0.05) + (np.sin(index / 4) * 3)
        return pd.DataFrame({
            "timestamp": pd.date_range("2026-01-01", periods=len(index), freq="h"),
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": np.full(len(index), 100.0),
        })

    def test_analyzer_stores_market_structure(self):
        _, result = analyze_market(self._market_data())

        self.assertIn(result.market_structure["trend"], {
            "UPTREND", "DOWNTREND", "RANGE", "UNCLEAR",
        })
        self.assertIn("swings", result.market_structure)
        self.assertIn("strength", result.market_structure)

    def test_report_renders_market_structure(self):
        df, result = analyze_market(self._market_data())

        report = generate_report(df, result)

        self.assertIn("MARKET STRUCTURE", report)
        self.assertIn("Trend:", report)
        self.assertIn("Strength:", report)


if __name__ == "__main__":
    unittest.main()
