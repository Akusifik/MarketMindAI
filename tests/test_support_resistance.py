import unittest

import pandas as pd

from analysis.support_resistance import (
    _cluster_swings,
    _distance_from_price,
    _interaction_groups,
    _interaction_quality,
    _max_zone_width,
    _role_at_index,
    _role_timeline,
    _resolve_overlaps,
    _strength,
    detect_support_resistance,
)


def candles(highs, lows, closes=None, volumes=None, atr=0.4):
    closes = closes or [(high + low) / 2 for high, low in zip(highs, lows)]
    volumes = volumes or [100] * len(highs)
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=len(highs), freq="h"),
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
        "ATR": [atr] * len(highs),
    })


class SupportResistanceTests(unittest.TestCase):
    def test_obvious_support(self):
        df = candles(
            [13, 12, 11, 12, 13, 14, 15],
            [12, 11, 10, 11, 12, 13, 14],
            [12.5, 11.5, 10.5, 11.5, 12.5, 13.5, 14.5],
        )

        zones = detect_support_resistance(df, swing_window=1)

        self.assertTrue(any(zone["type"] == "SUPPORT" for zone in zones))

    def test_obvious_resistance(self):
        df = candles(
            [10, 11, 12, 11, 10, 9, 8],
            [9, 10, 11, 10, 9, 8, 7],
            [9.5, 10.5, 11.5, 10.5, 9.5, 8.5, 7.5],
        )

        zones = detect_support_resistance(df, swing_window=1)

        self.assertTrue(any(zone["type"] == "RESISTANCE" for zone in zones))

    def test_nearby_swings_merge_into_one_zone(self):
        df = candles(
            [12, 11, 10.8, 11, 12, 11, 10.95, 11, 13],
            [11, 10.4, 10, 10.5, 11, 10.5, 10.15, 10.5, 12],
            [11.5, 10.7, 10.4, 10.8, 11.5, 10.8, 10.5, 10.8, 12.5],
            atr=0.5,
        )

        zones = detect_support_resistance(df, swing_window=1)
        nearby = [zone for zone in zones if 9.5 < zone["center"] < 10.5]

        self.assertEqual(len(nearby), 1)
        self.assertIn("2 confirmed swing", nearby[0]["reasons"][0])

    def test_distant_swings_remain_separate(self):
        df = candles(
            [12, 11, 10.8, 11, 16, 15, 15.8, 15, 14],
            [11, 10.4, 10, 10.5, 15, 14.4, 14, 14.5, 13],
            [11.5, 10.7, 10.4, 10.8, 15.5, 14.7, 14.4, 14.8, 13.5],
            atr=0.4,
        )

        zones = detect_support_resistance(df, swing_window=1)

        self.assertTrue(any(zone["center"] < 11 for zone in zones))
        self.assertTrue(any(zone["center"] > 14 for zone in zones))

    def test_one_interaction_does_not_inflate_touch_count(self):
        df = candles(
            [12, 11, 10.8, 10.7, 11, 12, 13],
            [11, 10.3, 10, 10.05, 10.4, 11, 12],
            [11.5, 10.6, 10.4, 10.5, 10.7, 11.5, 12.5],
            atr=0.4,
        )

        zones = detect_support_resistance(df, swing_window=1)
        support = min(zones, key=lambda zone: abs(zone["center"] - 10))

        self.assertEqual(support["touches"], 1)

    def test_resistance_becomes_support_after_confirmed_breakout(self):
        df = candles(
            [10, 11, 12, 11, 13, 14, 15],
            [9, 10, 11, 10, 12, 13, 14],
            [9.5, 10.5, 11.5, 10.5, 12.8, 13.8, 14.8],
            atr=0.4,
        )

        zones = detect_support_resistance(
            df, swing_window=1, confirmation_bars=2,
        )
        former_resistance = min(zones, key=lambda zone: abs(zone["center"] - 12))

        self.assertEqual(former_resistance["type"], "SUPPORT")
        self.assertTrue(former_resistance["role_reversal"])

    def test_insufficient_data_returns_no_zones(self):
        df = candles([10, 11, 10], [9, 10, 9])

        self.assertEqual(detect_support_resistance(df, swing_window=2), [])

    def test_monotonic_data_has_no_meaningful_zones(self):
        df = candles(
            [10, 11, 12, 13, 14, 15],
            [9, 10, 11, 12, 13, 14],
        )

        self.assertEqual(detect_support_resistance(df, swing_window=1), [])

    def test_unconfirmed_final_swing_is_ignored(self):
        df = candles(
            [12, 11, 10],
            [11, 10, 8],
        )

        self.assertEqual(detect_support_resistance(df, swing_window=1), [])

    def test_resistance_breakout_retest_remains_support(self):
        df = candles(
            [10, 11, 12, 11, 13, 14, 13, 14],
            [9, 10, 11, 10, 12, 13, 12, 13],
            [9.5, 10.5, 11.5, 10.5, 12.8, 13.8, 12.4, 13.5],
            atr=0.4,
        )

        zones = detect_support_resistance(df, swing_window=1)
        zone = min(zones, key=lambda item: abs(item["center"] - 12))

        self.assertEqual(zone["type"], "SUPPORT")
        self.assertTrue(zone["role_reversal"])

    def test_support_breakout_retest_remains_resistance(self):
        df = candles(
            [14, 13, 13, 13, 12, 11, 12, 11],
            [11, 10.5, 10, 10.5, 9.2, 8.2, 9.1, 8.3],
            [11.5, 10.7, 10.4, 10.6, 9.3, 8.3, 9.4, 8.5],
            atr=0.4,
        )

        zones = detect_support_resistance(df, swing_window=1)
        zone = min(zones, key=lambda item: abs(item["center"] - 10))

        self.assertEqual(zone["type"], "RESISTANCE")
        self.assertTrue(zone["role_reversal"])

    def test_missing_volume_is_supported(self):
        df = candles(
            [13, 12, 11, 12, 13],
            [12, 11, 10, 11, 12],
            [12.5, 11.5, 10.5, 11.5, 12.5],
        ).drop(columns="volume")

        zones = detect_support_resistance(df, swing_window=1)

        self.assertTrue(zones)
        self.assertTrue(all(0 <= zone["strength"] <= 100 for zone in zones))

    def test_missing_atr_uses_causal_trailing_true_range(self):
        df = candles(
            [11, 12, 10, 12, 100, 101],
            [9, 10, 8, 10, 99, 100],
            [10, 11, 9, 11, 99.5, 100.5],
        ).drop(columns="ATR")

        zones = detect_support_resistance(df, swing_window=1)
        zone = min(zones, key=lambda item: abs(item["center"] - 8))

        # ATR at confirmation index 3 is (2 + 3 + 3) / 3, so tolerance is 2.
        self.assertAlmostEqual(zone["lower_bound"], 7.0)
        self.assertAlmostEqual(zone["upper_bound"], 9.0)

    def test_nan_ohlc_data_is_rejected(self):
        df = candles([12, 11, 10], [11, 10, 9])
        df.loc[1, "high"] = float("nan")

        with self.assertRaises(ValueError):
            detect_support_resistance(df, swing_window=1)

    def test_unsorted_timestamps_are_rejected(self):
        df = candles([12, 11, 10], [11, 10, 9])
        df.loc[1, "timestamp"] = df.loc[0, "timestamp"] - pd.Timedelta(hours=1)

        with self.assertRaises(ValueError):
            detect_support_resistance(df, swing_window=1)

    def test_invalid_candle_is_rejected(self):
        df = candles([12, 11, 10], [11, 10, 9])
        df.loc[1, "high"] = 9

        with self.assertRaises(ValueError):
            detect_support_resistance(df, swing_window=1)

    def test_separated_interactions_are_counted_independently(self):
        df = candles(
            [11, 11, 11, 11, 11, 11, 11, 11, 11],
            [9, 9, 11, 11, 9, 11, 11, 11, 9],
        )

        groups = _interaction_groups(
            df, 9.5, 10.5, start_index=0, seed_indices=[], interaction_gap=1,
        )

        self.assertEqual(groups, [[0, 1], [4], [8]])

    def test_reaction_windows_stop_at_next_interaction(self):
        df = candles(
            [10, 10, 10, 20, 10],
            [9, 9, 9, 9, 9],
            [9.5, 9.5, 9.5, 19, 9.5],
        )

        _, reaction, _ = _interaction_quality(
            df,
            groups=[[0], [3]],
            roles=["SUPPORT", "SUPPORT"],
            center=10,
            reaction_window=5,
        )

        self.assertEqual(reaction, 0)

    def test_strength_after_role_reversal_uses_historical_roles(self):
        df = candles(
            [10, 11, 12, 11, 13, 14, 13, 14],
            [9, 10, 11, 10, 12, 13, 12, 13],
            [9.5, 10.5, 11.5, 10.5, 12.8, 13.8, 12.4, 13.5],
            atr=0.4,
        )
        transitions = _role_timeline(
            df, "RESISTANCE", 11.85, 12.15, start_index=3,
            confirmation_bars=2,
        )
        groups = [[2], [6]]

        self.assertEqual(_role_at_index("RESISTANCE", transitions, 2), "RESISTANCE")
        self.assertEqual(_role_at_index("RESISTANCE", transitions, 6), "SUPPORT")
        self.assertGreater(_strength(
            df, groups, "RESISTANCE", transitions, center=12,
            zone_width=0.3, max_width=1.2,
        ), 0)

    def test_high_atr_swing_cannot_expand_cluster_indefinitely(self):
        swings = [
            {"kind": "LOW", "price": 100, "tolerance": 10},
            {"kind": "LOW", "price": 105, "tolerance": 1},
            {"kind": "LOW", "price": 110, "tolerance": 1},
        ]

        clusters = _cluster_swings(swings)

        self.assertEqual(len(clusters), 2)
        self.assertLessEqual(
            max(item["price"] for item in clusters[0])
            - min(item["price"] for item in clusters[0])
            + sum(item["tolerance"] for item in clusters[0]) / len(clusters[0]),
            _max_zone_width(clusters[0]),
        )

    def test_highs_and_lows_are_clustered_separately(self):
        clusters = _cluster_swings([
            {"kind": "HIGH", "price": 100, "tolerance": 2},
            {"kind": "LOW", "price": 100.5, "tolerance": 2},
        ])

        self.assertEqual(len(clusters), 2)
        self.assertEqual({cluster[0]["kind"] for cluster in clusters}, {"HIGH", "LOW"})

    def test_redundant_overlapping_zones_are_resolved(self):
        zones = [
            {
                "type": "SUPPORT", "source_type": "LOW", "strength": 80,
                "distance_from_price": 1, "lower_bound": 100, "upper_bound": 110,
            },
            {
                "type": "SUPPORT", "source_type": "LOW", "strength": 70,
                "distance_from_price": 2, "lower_bound": 102, "upper_bound": 111,
            },
        ]

        resolved = _resolve_overlaps(zones)

        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["strength"], 80)

    def test_distinct_support_and_resistance_are_not_merged(self):
        zones = [
            {
                "type": "SUPPORT", "source_type": "LOW", "strength": 80,
                "distance_from_price": 1, "lower_bound": 100, "upper_bound": 110,
            },
            {
                "type": "RESISTANCE", "source_type": "HIGH", "strength": 70,
                "distance_from_price": 1, "lower_bound": 102, "upper_bound": 111,
            },
        ]

        self.assertEqual(len(_resolve_overlaps(zones)), 2)

    def test_distance_is_zero_inside_zone_and_nearest_boundary_outside(self):
        self.assertEqual(_distance_from_price(105, 100, 110), (0.0, True))
        below_distance, below_inside = _distance_from_price(90, 100, 110)
        above_distance, above_inside = _distance_from_price(120, 100, 110)
        self.assertAlmostEqual(below_distance, 100 / 9)
        self.assertFalse(below_inside)
        self.assertAlmostEqual(above_distance, 100 / 12)
        self.assertFalse(above_inside)

    def test_broad_zone_receives_strength_penalty(self):
        df = candles(
            [10, 11, 12, 11, 13, 14, 13, 14],
            [9, 10, 11, 10, 12, 13, 12, 13],
            [9.5, 10.5, 11.5, 10.5, 12.8, 13.8, 12.4, 13.5],
            atr=0.4,
        )
        transitions = _role_timeline(
            df, "RESISTANCE", 11.85, 12.15, start_index=3,
            confirmation_bars=2,
        )
        groups = [[2], [6]]
        narrow = _strength(
            df, groups, "RESISTANCE", transitions, 12, 0.3, 1.2,
        )
        broad = _strength(
            df, groups, "RESISTANCE", transitions, 12, 1.2, 1.2,
        )

        self.assertGreater(narrow, broad)
        self.assertGreater(narrow, 50)

    def test_unconfirmed_future_volatility_does_not_expand_existing_zone(self):
        base = candles(
            [11, 12, 10, 12, 11],
            [9, 10, 8, 10, 9],
            [10, 11, 9, 11, 10],
            atr=0.4,
        )
        extended = base.copy()
        extended.loc[len(extended)] = {
            "timestamp": extended["timestamp"].iloc[-1] + pd.Timedelta(hours=1),
            "open": 100, "high": 101, "low": 8, "close": 100,
            "volume": 100, "ATR": 100,
        }

        base_zone = min(
            detect_support_resistance(base, swing_window=1),
            key=lambda zone: abs(zone["center"] - 8),
        )
        extended_zone = min(
            detect_support_resistance(extended, swing_window=1),
            key=lambda zone: abs(zone["center"] - 8),
        )

        self.assertEqual(base_zone["lower_bound"], extended_zone["lower_bound"])
        self.assertEqual(base_zone["upper_bound"], extended_zone["upper_bound"])


if __name__ == "__main__":
    unittest.main()
