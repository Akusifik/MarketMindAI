import unittest

import numpy as np
import pandas as pd

from analysis.analyzer import analyze_market
from analysis.report import generate_report
from analysis.volume_analysis import analyze_volume_analysis


def market(closes, volumes):
    closes = list(closes)
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-02-01", periods=len(closes), freq="h"),
        "open": np.array(closes, dtype=float),
        "high": np.array(closes, dtype=float) + 1,
        "low": np.array(closes, dtype=float) - 1,
        "close": np.array(closes, dtype=float),
        "volume": volumes,
    })


def structure(swings=None, events=None):
    return {"swings": swings or [], "events": events or []}


def swing(kind, label, index, actionable_index):
    return {
        "type": kind, "label": label, "index": index,
        "actionable_index": actionable_index,
        "actionable_timestamp": pd.Timestamp("2026-02-01") + pd.Timedelta(hours=actionable_index),
    }


class VolumeAnalysisTests(unittest.TestCase):
    def test_normal_volume(self):
        result = analyze_volume_analysis(market(range(100, 121), [100] * 21))
        self.assertEqual(result["volume_state"], "NORMAL")

    def test_extreme_volume_spike(self):
        result = analyze_volume_analysis(market(range(100, 121), [100] * 20 + [500]))
        self.assertEqual(result["volume_state"], "EXTREME_SPIKE")

    def test_price_volume_relations(self):
        base = [100] * 20
        self.assertEqual(analyze_volume_analysis(market(base + [102], [100] * 20 + [200]))["price_volume_relation"], "PRICE_UP_VOLUME_EXPANDING")
        self.assertEqual(analyze_volume_analysis(market(base + [102], [100] * 20 + [50]))["price_volume_relation"], "PRICE_UP_VOLUME_CONTRACTING")
        self.assertEqual(analyze_volume_analysis(market(base + [98], [100] * 20 + [200]))["price_volume_relation"], "PRICE_DOWN_VOLUME_EXPANDING")
        self.assertEqual(analyze_volume_analysis(market(base + [98], [100] * 20 + [50]))["price_volume_relation"], "PRICE_DOWN_VOLUME_CONTRACTING")

    def test_effort_vs_result_interpretations(self):
        base = list(range(100, 120))
        low_result = analyze_volume_analysis(market(base + [119.1], [100] * 20 + [300]))
        strong_result = analyze_volume_analysis(market(base + [122], [100] * 20 + [300]))
        fragile = analyze_volume_analysis(market(base + [122], [100] * 20 + [50]))
        self.assertEqual(low_result["effort_result"]["type"], "HIGH_EFFORT_LOW_RESULT")
        self.assertEqual(strong_result["effort_result"]["type"], "HIGH_EFFORT_STRONG_RESULT")
        self.assertEqual(fragile["effort_result"]["type"], "LARGE_MOVE_WEAK_VOLUME")

    def test_recent_divergences(self):
        bearish = analyze_volume_analysis(
            market([100, 101, 102, 103, 104, 105, 106, 107], [200] * 4 + [50] * 4),
            structure([swing("HIGH", "HIGH", 1, 2), swing("HIGH", "HH", 5, 6)]),
        )
        bullish = analyze_volume_analysis(
            market([107, 106, 105, 104, 103, 102, 101, 100], [200] * 4 + [50] * 4),
            structure([swing("LOW", "LOW", 1, 2), swing("LOW", "LL", 5, 6)]),
        )
        self.assertEqual(bearish["divergences"][0]["direction"], "BEARISH")
        self.assertEqual(bullish["divergences"][0]["direction"], "BULLISH")

    def test_breakout_volume_confirmation_reuses_event_context(self):
        df = market(range(100, 121), [100] * 20 + [300])
        event = {
            "actionable_index": 20, "actionable_timestamp": df["timestamp"].iloc[20],
            "breakout_index": 20, "breakout_timestamp": df["timestamp"].iloc[20],
            "direction": "BULLISH", "type": "BULLISH_BOS",
            "source_swing": {"confirmation_index": 10},
        }
        structure = {"events": [event]}
        strong = analyze_volume_analysis(df, structure)
        weak = analyze_volume_analysis(market(range(100, 121), [100] * 20 + [50]), structure)
        self.assertEqual(strong["breakout_confirmation"]["level"], "STRONG")
        self.assertEqual(weak["breakout_confirmation"]["level"], "WEAK")

    def test_accumulation_distribution_and_neutral_need_repeated_evidence(self):
        accumulation_df = market([100] * 18 + [100, 101, 102], [100] * 18 + [200, 200, 200])
        accumulation_df.loc[20, "high"] = 102.2
        distribution_df = market([102] * 18 + [102, 101, 100], [100] * 18 + [200, 200, 200])
        distribution_df.loc[20, "low"] = 99.8
        accumulation = analyze_volume_analysis(accumulation_df)
        distribution = analyze_volume_analysis(distribution_df)
        neutral = analyze_volume_analysis(market([100, 101], [100, 200]))
        self.assertEqual(accumulation["accumulation_distribution"]["state"], "ACCUMULATION")
        self.assertEqual(distribution["accumulation_distribution"]["state"], "DISTRIBUTION")
        self.assertEqual(neutral["accumulation_distribution"]["state"], "NEUTRAL")

    def test_zero_missing_and_invalid_volume_are_predictable(self):
        zero = analyze_volume_analysis(market([100, 101], [0, 0]))
        self.assertEqual(zero["volume_state"], "NO_VOLUME")
        self.assertEqual(zero["price_volume_relation"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(zero["bias"], "NEUTRAL")
        with self.assertRaises(ValueError):
            analyze_volume_analysis(market([100, 101], [1, 1]).drop(columns="volume"))
        invalid = market([100, 101], [1, np.nan])
        with self.assertRaises(ValueError):
            analyze_volume_analysis(invalid)

    def test_future_volume_does_not_change_historical_baseline_or_divergence(self):
        base = market([100, 101, 102, 103, 104, 105, 106, 107], [200] * 4 + [50] * 4)
        extended = pd.concat([base, market([108], [10000])], ignore_index=True)
        extended["timestamp"] = pd.date_range("2026-02-01", periods=9, freq="h")
        context = structure([swing("HIGH", "HIGH", 1, 2), swing("HIGH", "HH", 5, 6)])
        base_result = analyze_volume_analysis(base, context)
        self.assertEqual(base_result["volume_ratio"], analyze_volume_analysis(extended.iloc[:8])["volume_ratio"])
        self.assertEqual(base_result["divergences"], analyze_volume_analysis(extended.iloc[:8], context)["divergences"])

    def test_noisy_candles_without_confirmed_structure_have_no_divergence(self):
        result = analyze_volume_analysis(market([100, 103, 99, 104, 98, 105, 97, 106], [200] * 4 + [50] * 4))
        self.assertEqual(result["divergences"], [])

    def test_unconfirmed_structural_point_is_not_available_for_divergence(self):
        df = market([107, 106, 105, 104, 103, 102, 101, 100], [200] * 4 + [50] * 4)
        context = structure([swing("LOW", "LOW", 1, 2), swing("LOW", "LL", 5, 8)])
        self.assertEqual(analyze_volume_analysis(df, context)["divergences"], [])

    def test_obv_rules_for_bullish_and_bearish_divergence(self):
        bullish_df = market([100, 99, 101, 98, 97, 96, 98, 99], [100] * 8)
        bullish_df["OBV"] = [0, -100, 0, -100, -100, -50, 0, 100]
        bullish_context = structure([swing("LOW", "LOW", 1, 2), swing("LOW", "LL", 5, 6)])
        bearish_df = market([100, 101, 99, 102, 103, 104, 102, 101], [100] * 8)
        bearish_df["OBV"] = [0, 100, 0, 100, 100, 50, 0, -100]
        bearish_context = structure([swing("HIGH", "HIGH", 1, 2), swing("HIGH", "HH", 5, 6)])
        self.assertEqual(analyze_volume_analysis(bullish_df, bullish_context)["divergences"][0]["direction"], "BULLISH")
        self.assertEqual(analyze_volume_analysis(bearish_df, bearish_context)["divergences"][0]["direction"], "BEARISH")

    def test_confirming_obv_does_not_create_divergence(self):
        df = market([100, 99, 101, 98, 97, 96, 98, 99], [100] * 8)
        df["OBV"] = [0, -50, 0, -100, -150, -200, -100, 0]
        result = analyze_volume_analysis(df, structure([swing("LOW", "LOW", 1, 2), swing("LOW", "LL", 5, 6)]))
        self.assertEqual(result["divergences"], [])

    def test_breakout_uses_breakout_candle_and_rejects_stale_or_invalid_events(self):
        df = market(range(100, 122), [100] * 20 + [300, 50])
        valid_current = {
            "breakout_index": 21, "breakout_timestamp": df["timestamp"].iloc[21],
            "actionable_index": 21, "actionable_timestamp": df["timestamp"].iloc[21],
            "direction": "BULLISH", "source_swing": {"confirmation_index": 10},
        }
        stale = {**valid_current, "breakout_index": 20, "breakout_timestamp": df["timestamp"].iloc[20], "actionable_index": 20, "actionable_timestamp": df["timestamp"].iloc[20]}
        invalid = {"direction": "BULLISH", "actionable_index": 21}
        self.assertEqual(analyze_volume_analysis(df, structure(events=[stale]))["breakout_confirmation"]["level"], "NONE")
        self.assertEqual(analyze_volume_analysis(df, structure(events=[invalid]))["breakout_confirmation"]["level"], "NONE")
        self.assertEqual(analyze_volume_analysis(df, structure(events=[valid_current]))["breakout_confirmation"]["level"], "WEAK")

    def test_existing_rvol_is_supplemental_and_missing_rvol_falls_back(self):
        df = market(range(100, 121), [100] * 20 + [150])
        df["RVOL"] = [np.nan] * 20 + [2.0]
        with_rvol = analyze_volume_analysis(df)
        without_rvol = analyze_volume_analysis(df.drop(columns="RVOL"))
        self.assertEqual(with_rvol["rvol"], 2.0)
        self.assertEqual(with_rvol["volume_state"], "ELEVATED")
        self.assertIsNone(without_rvol["rvol"])

    def test_spike_evidence_is_capped_and_accumulation_needs_multiple_candles(self):
        df = market(list(range(100, 120)) + [122], [100] * 20 + [500])
        event = {"breakout_index": 20, "breakout_timestamp": df["timestamp"].iloc[20], "actionable_index": 20, "actionable_timestamp": df["timestamp"].iloc[20], "direction": "BULLISH", "source_swing": {"confirmation_index": 10}}
        result = analyze_volume_analysis(df, structure(events=[event]))
        self.assertLessEqual(result["strength"], 55)
        self.assertEqual(result["accumulation_distribution"]["state"], "NEUTRAL")


class VolumeAnalysisIntegrationTests(unittest.TestCase):
    def test_analysis_result_and_report_include_volume_analysis(self):
        close = 100 + np.arange(240) * .1
        df = market(close, np.full(240, 100.0))
        analyzed, result = analyze_market(df)
        self.assertIn(result.volume_analysis["bias"], {"BULLISH", "BEARISH", "NEUTRAL"})
        self.assertIn("VOLUME ANALYSIS", generate_report(analyzed, result))


if __name__ == "__main__":
    unittest.main()
