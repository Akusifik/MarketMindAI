from core.market import Market
from logs.logger import logger
from config import SYMBOL, TIMEFRAME, CANDLE_LIMIT

logger.info("Запуск MarketMind AI")

market = Market(
    SYMBOL,
    TIMEFRAME,
    CANDLE_LIMIT
)

market.load_data()

market.analyze()

print(market.report())