from core.market import Market

TIMEFRAMES = [
    "1d",
    "4h",
    "1h",
    "15m"
]


def analyze_multi_timeframe(symbol):

    results = {}

    for timeframe in TIMEFRAMES:

        market = Market(symbol, timeframe)

        market.load_data()
        market.analyze()

        results[timeframe] = market.result

    return results