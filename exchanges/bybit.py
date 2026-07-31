import ccxt

exchange = ccxt.bybit()


def get_ticker(symbol: str):
    return exchange.fetch_ticker(symbol)


def get_candles(symbol: str, timeframe: str = "1h", limit: int = 100):
    return exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)