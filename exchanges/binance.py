import ccxt

exchange = ccxt.binance()


def get_ticker(symbol: str):
    return exchange.fetch_ticker(symbol)