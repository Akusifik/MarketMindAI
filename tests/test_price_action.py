import unittest

import numpy as np
import pandas as pd

from analysis.analyzer import analyze_market
from analysis.price_action import analyze_price_action, calculate_candle_anatomy
from analysis.report import generate_report


def candles(rows):
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=len(rows), freq="h"),
        "open": [row[0] for row in rows],
        "high": [row[1] for row in rows],
        "low": [row[2] for row in rows],
        "close": [row[3] for row in rows],
        "volume": [100] * len(rows),
    })


SUPPORT_ZONE = {
    "type": "SUPPORT", "lower_bound": 8.5, "upper_bound": 9.2,
    "strength": 80, "role_reversal": False, "activation_index": 0,
}
RESISTANCE_ZONE = {
    "type": "RESISTANCE", "lower_bound": 10.0, "upper_bound": 10.7,
    "strength": 80, "role_reversal": False, "activation_index": 0,
}


class PriceActionTests(unittest.TestCase):
    def test_bullish_engulfing(self):
        result = analyze_price_action(candles([
            (10, 10.2, 8.8, 9),
            (8.9, 10.6, 8.7, 10.5),
        ]))

        self.assertIn("BULLISH_ENGULFING", [p["type"] for p in result["patterns"]])

    def test_bearish_engulfing(self):
        result = analyze_price_action(candles([
            (9, 10.2, 8.8, 10),
            (10.1, 10.3, 8.5, 8.7),
        ]))

        self.assertIn("BEARISH_ENGULFING", [p["type"] for p in result["patterns"]])

    def test_bullish_pin_bar(self):
        result = analyze_price_action(candles([(10, 10.2, 8, 10.1)]))

        self.assertIn("BULLISH_PIN_BAR", [p["type"] for p in result["patterns"]])

    def test_bearish_pin_bar(self):
        result = analyze_price_action(candles([(10, 12, 9.9, 10.1)]))

        self.assertIn("BEARISH_PIN_BAR", [p["type"] for p in result["patterns"]])

    def test_inside_bar(self):
        result = analyze_price_action(candles([
            (10, 12, 8, 11),
            (10, 11, 9, 10.5),
        ]))

        self.assertIn("INSIDE_BAR", [p["type"] for p in result["patterns"]])

    def test_outside_bar(self):
        result = analyze_price_action(candles([
            (10, 11, 9, 10.5),
            (10, 12, 8, 10.5),
        ]))

        self.assertIn("OUTSIDE_BAR", [p["type"] for p in result["patterns"]])

    def test_zero_range_candle_is_safe(self):
        result = analyze_price_action(candles([(10, 10, 10, 10)]))

        self.assertEqual(result["patterns"], [])
        self.assertEqual(calculate_candle_anatomy(candles([(10, 10, 10, 10)]))[0]["body_ratio"], 0)

    def test_bullish_pattern_at_support_has_more_contextual_strength(self):
        df = candles([(10, 10.2, 8.8, 9), (8.9, 10.6, 8.7, 10.5)])
        supported = analyze_price_action(df, [SUPPORT_ZONE])
        away = analyze_price_action(df, [])

        self.assertGreater(
            max(item["strength"] for item in supported["patterns"]),
            max(item["strength"] for item in away["patterns"]),
        )

    def test_bearish_pattern_at_resistance_has_more_contextual_strength(self):
        df = candles([(9, 10.2, 8.8, 10), (10.1, 10.3, 8.5, 8.7)])
        resisted = analyze_price_action(df, [RESISTANCE_ZONE])
        away = analyze_price_action(df, [])

        self.assertGreater(
            max(item["strength"] for item in resisted["patterns"]),
            max(item["strength"] for item in away["patterns"]),
        )

    def test_support_rejection(self):
        result = analyze_price_action(candles([(10, 10.2, 8, 10.1)]), [SUPPORT_ZONE])

        self.assertEqual(result["rejections"][0]["type"], "SUPPORT_REJECTION")

    def test_resistance_rejection(self):
        result = analyze_price_action(candles([(10, 12, 9.9, 10.1)]), [RESISTANCE_ZONE])

        self.assertEqual(result["rejections"][0]["type"], "RESISTANCE_REJECTION")

    def test_close_outside_zone(self):
        zone = {**RESISTANCE_ZONE, "upper_bound": 10, "lower_bound": 9}
        result = analyze_price_action(candles([
            (9.5, 9.9, 9.2, 9.8),
            (9.7, 9.9, 9.4, 9.7),
            (9.9, 10.6, 9.7, 10.4),
        ]), [zone])

        self.assertIn("CLOSE_ABOVE_ZONE", [event["type"] for event in result["zone_events"]])

    def test_role_reversal_retest(self):
        zone = {
            **SUPPORT_ZONE,
            "role_reversal": True,
            "role_transitions": [{
                "from": "RESISTANCE", "to": "SUPPORT",
                "direction": "BULLISH", "breakout_index": 0,
                "confirmed_at": 1, "activation_index": 1,
            }],
        }
        result = analyze_price_action(candles([
            (10, 10.2, 9.5, 10),
            (10, 10.3, 9.5, 10.2),
            (10, 10.2, 8.8, 10.1),
        ]), [zone])

        self.assertIn("RETEST_REJECTION", [event["type"] for event in result["zone_events"]])

    def test_future_candles_do_not_change_closed_candle_anatomy(self):
        base = candles([(10, 10.2, 8.8, 9), (8.9, 10.6, 8.7, 10.5)])
        extended = pd.concat([
            base,
            candles([(10, 20, 5, 15)]).iloc[[0]],
        ], ignore_index=True)
        extended["timestamp"] = pd.date_range("2026-01-01", periods=3, freq="h")

        self.assertEqual(calculate_candle_anatomy(base), calculate_candle_anatomy(extended)[:2])

    def test_invalid_input_is_rejected(self):
        with self.assertRaises(ValueError):
            analyze_price_action(pd.DataFrame({"close": [1, 2]}))

        invalid = candles([(10, 11, 9, 10)])
        invalid.loc[0, "high"] = float("nan")
        with self.assertRaises(ValueError):
            analyze_price_action(invalid)

        non_numeric = candles([(10, 11, 9, 10)])
        non_numeric["low"] = non_numeric["low"].astype(object)
        non_numeric.loc[0, "low"] = "invalid"
        with self.assertRaises(ValueError):
            analyze_price_action(non_numeric)

    def test_no_pattern_case_is_neutral(self):
        result = analyze_price_action(candles([
            (10, 11, 9, 10.5),
            (10.5, 11.2, 10.1, 10.7),
        ]))

        self.assertEqual(result["bias"], "NEUTRAL")
        self.assertEqual(result["patterns"], [])

    def test_role_reversal_touch_without_confirmed_breakout_is_not_retest(self):
        zone = {**SUPPORT_ZONE, "role_reversal": True, "role_transitions": []}
        result = analyze_price_action(candles([(10, 10.2, 8.8, 10.1)]), [zone])

        self.assertNotIn("RETEST", [event["type"] for event in result["zone_events"]])
        self.assertNotIn("RETEST_REJECTION", [event["type"] for event in result["zone_events"]])

    def test_retest_requires_actionable_transition_before_candle(self):
        zone = {
            **SUPPORT_ZONE, "role_reversal": True,
            "role_transitions": [{
                "from": "RESISTANCE", "to": "SUPPORT", "direction": "BULLISH",
                "breakout_index": 0, "confirmed_at": 1, "activation_index": 1,
            }],
        }
        result = analyze_price_action(candles([
            (10, 10.2, 9.5, 10), (10, 10.2, 8.8, 10.1),
        ]), [zone])

        self.assertEqual(result["zone_events"], [])

    def test_newly_activated_zone_cannot_create_historical_breakout(self):
        zone = {**RESISTANCE_ZONE, "lower_bound": 9, "upper_bound": 10, "activation_index": 1}
        result = analyze_price_action(candles([
            (9.5, 9.9, 9.2, 9.8), (9.9, 10.6, 9.7, 10.4),
        ]), [zone])

        self.assertEqual(result["zone_events"], [])

    def test_overlapping_equivalent_zones_do_not_duplicate_rejections(self):
        overlapping = {**SUPPORT_ZONE, "lower_bound": 8.6, "upper_bound": 9.25, "strength": 70}
        result = analyze_price_action(candles([(10, 10.2, 8, 10.1)]), [SUPPORT_ZONE, overlapping])

        self.assertEqual(len(result["rejections"]), 1)

    def test_related_pattern_rejection_and_retest_evidence_is_capped(self):
        zone = {
            **SUPPORT_ZONE, "role_reversal": True,
            "role_transitions": [{
                "from": "RESISTANCE", "to": "SUPPORT", "direction": "BULLISH",
                "breakout_index": 0, "confirmed_at": 1, "activation_index": 1,
            }],
        }
        result = analyze_price_action(candles([
            (10, 10.2, 9.5, 10), (10, 10.2, 9.4, 10),
            (10, 10.2, 8, 10.1),
        ]), [zone])

        self.assertLessEqual(result["strength"], 85)

    def test_distinct_zone_interactions_remain_separate_evidence(self):
        second_support = {**SUPPORT_ZONE, "lower_bound": 10.5, "upper_bound": 10.9, "strength": 75}
        result = analyze_price_action(candles([(11.8, 12, 8, 11.9)]), [SUPPORT_ZONE, second_support])

        self.assertEqual(len(result["rejections"]), 2)

    def test_tiny_bullish_pin_bar_is_rejected_by_causal_volatility_filter(self):
        result = analyze_price_action(candles([
            (100, 101, 99, 100), (100, 100.01, 99.99, 100.009),
        ]))

        self.assertNotIn("BULLISH_PIN_BAR", [item["type"] for item in result["patterns"]])

    def test_tiny_bearish_pin_bar_is_rejected_by_causal_volatility_filter(self):
        result = analyze_price_action(candles([
            (100, 101, 99, 100), (100, 100.01, 99.99, 99.991),
        ]))

        self.assertNotIn("BEARISH_PIN_BAR", [item["type"] for item in result["patterns"]])

    def test_normal_atr_sized_pin_bar_is_accepted(self):
        result = analyze_price_action(candles([
            (10, 10.2, 8.8, 9.5), (10, 10.2, 8, 10.1),
        ]))

        self.assertIn("BULLISH_PIN_BAR", [item["type"] for item in result["patterns"]])

    def test_future_volatility_does_not_change_latest_pin_bar_filter(self):
        base = candles([(10, 10.2, 8.8, 9.5), (10, 10.2, 8, 10.1)])
        extended = pd.concat([base, candles([(10, 30, 5, 20)]).iloc[[0]]], ignore_index=True)
        extended["timestamp"] = pd.date_range("2026-01-01", periods=3, freq="h")

        self.assertIn("BULLISH_PIN_BAR", [item["type"] for item in analyze_price_action(base)["patterns"]])
        self.assertEqual(calculate_candle_anatomy(base), calculate_candle_anatomy(extended)[:2])

    def test_output_explicitly_documents_latest_candle_only_mode(self):
        result = analyze_price_action(candles([
            (10, 10.2, 8, 10.1), (10, 10.5, 9.8, 10),
        ]))

        self.assertIn("latest closed candle only", result["reasons"][0])
        self.assertTrue(all(item["candle_index"] == 1 for item in result["patterns"]))


class PriceActionIntegrationTests(unittest.TestCase):
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

    def test_analyzer_stores_price_action(self):
        _, result = analyze_market(self._market_data())

        self.assertIn(result.price_action["bias"], {"BULLISH", "BEARISH", "NEUTRAL"})
        self.assertIn("patterns", result.price_action)
        self.assertIn("rejections", result.price_action)

    def test_report_renders_price_action(self):
        df, result = analyze_market(self._market_data())

        report = generate_report(df, result)

        self.assertIn("PRICE ACTION", report)
        self.assertIn("Bias:", report)


if __name__ == "__main__":
    unittest.main()
