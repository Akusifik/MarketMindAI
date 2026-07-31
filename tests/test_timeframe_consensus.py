import unittest
from types import SimpleNamespace

from analysis.timeframe_consensus import analyze_consensus


def make_results(actions, confidence=80):
    return {
        timeframe: SimpleNamespace(
            decision={"action": action},
            confidence=confidence,
        )
        for timeframe, action in actions.items()
    }


class TimeframeConsensusTests(unittest.TestCase):
    def test_all_timeframes_buy(self):
        consensus = analyze_consensus(make_results({
            "1d": "BUY", "4h": "BUY", "1h": "BUY", "15m": "BUY",
        }))

        self.assertEqual(consensus["overall"], "BUY")
        self.assertFalse(consensus["conflict"])
        self.assertFalse(consensus["correction"])
        self.assertEqual(consensus["confidence"], 85)

    def test_all_timeframes_sell(self):
        consensus = analyze_consensus(make_results({
            "1d": "SELL", "4h": "SELL", "1h": "SELL", "15m": "SELL",
        }))

        self.assertEqual(consensus["overall"], "SELL")
        self.assertFalse(consensus["conflict"])
        self.assertFalse(consensus["correction"])

    def test_all_timeframes_hold(self):
        consensus = analyze_consensus(make_results({
            "1d": "HOLD", "4h": "HOLD", "1h": "HOLD", "15m": "HOLD",
        }))

        self.assertEqual(consensus["overall"], "HOLD")
        self.assertFalse(consensus["conflict"])
        self.assertFalse(consensus["correction"])

    def test_higher_buy_lower_sell_is_a_pullback(self):
        consensus = analyze_consensus(make_results({
            "1d": "BUY", "4h": "BUY", "1h": "SELL", "15m": "SELL",
        }))

        self.assertEqual(consensus["overall"], "BUY")
        self.assertEqual(consensus["higher_timeframe_action"], "BUY")
        self.assertEqual(consensus["lower_timeframe_action"], "SELL")
        self.assertTrue(consensus["correction"])
        self.assertFalse(consensus["conflict"])

    def test_higher_sell_lower_buy_is_a_pullback(self):
        consensus = analyze_consensus(make_results({
            "1d": "SELL", "4h": "SELL", "1h": "BUY", "15m": "BUY",
        }))

        self.assertEqual(consensus["overall"], "SELL")
        self.assertTrue(consensus["correction"])
        self.assertFalse(consensus["conflict"])

    def test_mixed_ambiguous_signals_are_a_conflict(self):
        consensus = analyze_consensus(make_results({
            "1d": "BUY", "4h": "SELL", "1h": "SELL", "15m": "BUY",
        }))

        self.assertEqual(consensus["overall"], "HOLD")
        self.assertTrue(consensus["conflict"])
        self.assertFalse(consensus["correction"])

    def test_small_weighted_advantage_remains_hold(self):
        consensus = analyze_consensus(make_results({
            "1d": "BUY", "4h": "SELL", "1h": "HOLD", "15m": "HOLD",
        }))

        self.assertEqual(consensus["weighted_votes"], {
            "BUY": 4, "SELL": 3, "HOLD": 3,
        })
        self.assertEqual(consensus["overall"], "HOLD")


if __name__ == "__main__":
    unittest.main()
