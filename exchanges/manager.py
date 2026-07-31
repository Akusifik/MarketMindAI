from config import EXCHANGE

if EXCHANGE == "bybit":
    from exchanges.bybit import get_ticker, get_candles

elif EXCHANGE == "binance":
    from exchanges.binance import get_ticker, get_candles

else:
    raise ValueError("Биржа не поддерживается")