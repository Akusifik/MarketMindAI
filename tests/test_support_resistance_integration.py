import unittest

import numpy as np
import pandas as pd

from analysis.analyzer import analyze_market
from analysis.evaluate_market import evaluate_market
from analysis.report import generate_report


def market_data(with_swings):
    index = np.arange(240)
    close = (
        100 + (index * 0.05) + (np.sin(index / 4) * 3)
        if with_swings else 100 + (index * 0.05)
    )
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=len(index), freq="h"),
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": np.full(len(index), 100.0),
    })


class SupportResistanceIntegrationTests(unittest.TestCase):
    def test_zones_are_stored_in_analysis_result(self):
        df, result = analyze_market(market_data(with_swings=True))

        self.assertIsInstance(result.support_resistance_zones, list)
        self.assertTrue(result.support_resistance_zones)
        self.assertIn("type", result.support_resistance_zones[0])
        self.assertIn("strength", result.support_resistance_zones[0])
        self.assertIn("close", df.columns)

    def test_report_renders_detected_zones(self):
        df, result = analyze_market(market_data(with_swings=True))

        report = generate_report(df, result)

        self.assertIn("SUPPORT & RESISTANCE", report)
        self.assertIn("Strength:", report)
        self.assertIn("Touches:", report)
        self.assertIn("Distance:", report)

    def test_report_handles_no_zones(self):
        df, result = analyze_market(market_data(with_swings=False))

        self.assertEqual(result.support_resistance_zones, [])
        self.assertIn(
            "No meaningful support or resistance zones detected.",
            generate_report(df, result),
        )

    def test_existing_evaluation_behavior_is_unchanged(self):
        _, result = analyze_market(market_data(with_swings=True))

        expected_market = evaluate_market(result)
        self.assertEqual(result.score, expected_market["score"])
        self.assertEqual(result.confidence, expected_market["confidence"])
        self.assertIn(result.decision["action"], {"BUY", "SELL", "HOLD"})


if __name__ == "__main__":
    unittest.main()
